"""
services/value_engine.py

Logovo.bet — Value Engine 2.0 & Value Radar Scanner.
Strict Invariants:
1. True implied probability is calculated by normalizing for bookmaker overround/margin.
2. Value edge = (Model Probability - True Implied Probability) * 100%.
3. Strictly analytical phrasing: NEVER claims guaranteed profit, 100% win, or sure bets.
4. Division- and season-isolated scanning.
"""

import math
import logging
from typing import Any, Optional
import database
from services.ensemble_engine import EnsemblePredictionEngine

logger = logging.getLogger(__name__)

VALUE_EDGE_THRESHOLD = 3.0  # Minimum 3.0 percentage points edge to be flagged as value
MIN_ODDS_THRESHOLD = 1.10
MAX_ODDS_THRESHOLD = 50.0


class ValueEngine:
    """Calculates true market margin and statistical value edges."""

    @staticmethod
    def calculate_overround(odds_list: list[float]) -> float:
        """
        Calculate total bookmaker overround / margin.
        Overround = sum(1 / odd) - 1.0.
        """
        valid_odds = []
        for o in odds_list:
            try:
                val = float(o)
                if math.isfinite(val) and val > 1.0:
                    valid_odds.append(val)
            except (ValueError, TypeError):
                continue

        if not valid_odds:
            return 0.0
        total_inverse = sum(1.0 / o for o in valid_odds)
        return max(0.0, total_inverse - 1.0)

    @staticmethod
    def calculate_true_implied_probability(odd: float, total_overround: float) -> float:
        """
        Compute overround-adjusted true implied probability.
        P_implied = (1 / odd) / (1.0 + overround)
        """
        try:
            val = float(odd)
            if not math.isfinite(val) or val <= 1.0:
                return 0.0
            raw_implied = 1.0 / val
            normalized = raw_implied / (1.0 + max(0.0, total_overround))
            return round(normalized, 4)
        except (ValueError, TypeError, ZeroDivisionError):
            return 0.0

    @staticmethod
    def analyze_match_value(
        match_id: int,
        prediction_result: Optional[dict[str, Any]] = None
    ) -> list[dict[str, Any]]:
        """
        Evaluate all active market selections for a match against model probabilities.
        """
        pred = prediction_result or EnsemblePredictionEngine.predict_match(match_id)
        confidence = pred["confidence"]

        # Map standard outcome keys to ensemble probabilities
        prob_lookup = {
            "p1": pred["home_probability"],
            "x": pred["draw_probability"],
            "p2": pred["away_probability"],
            "tb25": pred["goals_markets"]["over_2_5"],
            "tm25": pred["goals_markets"]["under_2_5"],
            "tb15": pred["goals_markets"]["over_1_5"],
            "tm15": pred["goals_markets"]["under_1_5"],
            "tb35": pred["goals_markets"]["over_3_5"],
            "tm35": pred["goals_markets"]["under_3_5"],
            "btts_yes": pred["goals_markets"]["btts_yes"],
            "btts_no": pred["goals_markets"]["btts_no"],
        }

        value_items = []
        with database.transaction() as conn:
            cursor = conn.cursor()
            # Fetch all active markets and their selections for this match
            cursor.execute("""
                SELECT m.id as market_id, m.market_key, m.market_name,
                       ms.id as selection_id, ms.selection_key, ms.selection_name, ms.odds_value
                FROM markets m
                JOIN market_selections ms ON m.id = ms.market_id
                WHERE m.match_id = ? AND m.status = 'open' AND ms.status = 'active'
            """, (match_id,))
            rows = [dict(r) for r in cursor.fetchall()]

            # Group by market_id to calculate per-market overround
            markets_map: dict[int, list[dict]] = {}
            for r in rows:
                markets_map.setdefault(r["market_id"], []).append(r)

            for m_id, sels in markets_map.items():
                odds = []
                for s in sels:
                    try:
                        ov = float(s["odds_value"])
                        if math.isfinite(ov) and ov > 1.0:
                            odds.append(ov)
                    except (ValueError, TypeError):
                        pass

                overround = ValueEngine.calculate_overround(odds)

                for s in sels:
                    sel_key = s["selection_key"]
                    try:
                        odd_val = float(s["odds_value"])
                    except (ValueError, TypeError):
                        continue

                    if not math.isfinite(odd_val) or odd_val < MIN_ODDS_THRESHOLD or odd_val > MAX_ODDS_THRESHOLD:
                        continue

                    # Find matching model probability
                    model_p = prob_lookup.get(sel_key)
                    if model_p is None:
                        # Try case-insensitive or mapped key
                        model_p = prob_lookup.get(sel_key.lower())

                    if model_p is not None:
                        true_implied = ValueEngine.calculate_true_implied_probability(odd_val, overround)
                        raw_implied = round(1.0 / odd_val, 4)
                        edge_pp = round((model_p - true_implied) * 100.0, 1)

                        is_value = (edge_pp >= VALUE_EDGE_THRESHOLD) and (confidence >= 0.40)

                        value_items.append({
                            "match_id": match_id,
                            "market_id": m_id,
                            "selection_id": s["selection_id"],
                            "market_key": s["market_key"],
                            "selection_key": sel_key,
                            "selection_name": s["selection_name"],
                            "odds": odd_val,
                            "raw_implied_probability": round(raw_implied * 100, 1),
                            "true_implied_probability": round(true_implied * 100, 1),
                            "model_probability": round(model_p * 100, 1),
                            "edge_percentage_points": edge_pp,
                            "confidence": confidence,
                            "confidence_level": "HIGH" if confidence >= 0.70 else ("MEDIUM" if confidence >= 0.50 else "LOW"),
                            "is_value": is_value,
                            "overround_percent": round(overround * 100, 1),
                            "signal_type": "Potential Value" if is_value else "Market Aligned"
                        })

        # Sort by edge descending
        value_items.sort(key=lambda x: x["edge_percentage_points"], reverse=True)
        return value_items


class ValueRadar:
    """Scans all upcoming and open fixtures across a division to identify high-value opportunities."""

    @staticmethod
    def scan_radar(
        division_id: Optional[int] = None,
        season_id: Optional[int] = None,
        min_edge: float = VALUE_EDGE_THRESHOLD,
        limit: int = 15
    ) -> list[dict[str, Any]]:
        """
        Scan open matches and return top value edges meeting threshold criteria.
        """
        with database.transaction() as conn:
            cursor = conn.cursor()
            base_sql = "SELECT id, player1_team, player2_team, round_number, division_id, season_id FROM matches WHERE status IN ('open', 'scheduled', 'pending')"
            params: list[Any] = []

            if division_id is not None:
                base_sql += " AND division_id = ?"
                params.append(division_id)
            if season_id is not None:
                base_sql += " AND season_id = ?"
                params.append(season_id)

            base_sql += " ORDER BY id DESC LIMIT 40"
            cursor.execute(base_sql, params)
            matches = [dict(r) for r in cursor.fetchall()]

        all_edges = []
        for m in matches:
            try:
                edges = ValueEngine.analyze_match_value(m["id"])
                for e in edges:
                    if e["edge_percentage_points"] >= min_edge:
                        e["team1"] = m["player1_team"]
                        e["team2"] = m["player2_team"]
                        e["round_number"] = m["round_number"]
                        e["division_id"] = m["division_id"]
                        all_edges.append(e)
            except Exception as ex:
                logger.debug(f"Skipping match #{m['id']} in ValueRadar scan: {ex}")

        all_edges.sort(key=lambda x: (x["is_value"], x["edge_percentage_points"]), reverse=True)
        return all_edges[:limit]
