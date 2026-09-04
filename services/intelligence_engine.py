"""
services/intelligence_engine.py

Logovo.bet — Sports Intelligence & Predictive Analytics Engine.
Strict Invariants:
1. Analytical layer only — NEVER places bets or modifies balances.
2. Calculates Form, H2H, Scoring Trends, Implied Probability, Model Probability, and Edge.
3. Verifiable facts only — NO HALLUCINATIONS.
4. AI/model predictions are analytical indicators, NOT guarantees.
"""

import math
import logging
from typing import Any, Optional
import database

logger = logging.getLogger(__name__)


def _poisson_probability(k: int, lamb: float) -> float:
    """Calculate Poisson probability P(k; lambda)."""
    if lamb <= 0:
        return 1.0 if k == 0 else 0.0
    return (math.exp(-lamb) * (lamb ** k)) / math.factorial(k)


class IntelligenceEngine:
    """Read-only sports intelligence and analytics service."""

    @staticmethod
    def get_match_intelligence(match_id: int) -> dict[str, Any]:
        """
        Generate full intelligence report for a match:
        - Team Form (last 5 games)
        - Head-to-Head (H2H) record
        - Scoring trends (Over 2.5, BTTS, clean sheets)
        - Implied vs Model Probabilities & Value Edge
        - Verifiable insights list
        """
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
            m = cursor.fetchone()
            if not m:
                raise ValueError(f"Match #{match_id} does not exist.")

            t1 = m["player1_team"] or ""
            t2 = m["player2_team"] or ""
            div_id = m["division_id"] if "division_id" in m.keys() and m["division_id"] else 1
            season_id = m["season_id"] if "season_id" in m.keys() and m["season_id"] else 1

            # 1. Fetch recent matches for both teams (strictly within same season and division)
            t1_recent = IntelligenceEngine._get_recent_matches(cursor, t1, div_id, season_id, limit=5)
            t2_recent = IntelligenceEngine._get_recent_matches(cursor, t2, div_id, season_id, limit=5)

            # 2. Fetch H2H matches between these two clubs
            h2h_matches = IntelligenceEngine._get_h2h_matches(cursor, t1, t2, div_id, season_id, limit=10)

            # 3. Fetch current market odds for 1X2 and Over 2.5
            odds_map = IntelligenceEngine._get_match_odds(cursor, match_id)

        # Compute Form
        t1_form = IntelligenceEngine._analyze_team_form(t1, t1_recent)
        t2_form = IntelligenceEngine._analyze_team_form(t2, t2_recent)

        # Compute H2H Summary
        h2h_summary = IntelligenceEngine._analyze_h2h(t1, t2, h2h_matches)

        # Compute Probabilities & Edge
        value_analysis = IntelligenceEngine._calculate_probabilities_and_edge(
            t1_form, t2_form, odds_map
        )

        # Generate Data-Driven Insights
        insights = IntelligenceEngine._generate_insights(t1, t2, t1_form, t2_form, h2h_summary, value_analysis)

        return {
            "status": "ok",
            "match_id": match_id,
            "division_id": div_id,
            "season_id": season_id,
            "team1": t1,
            "team2": t2,
            "form": {
                "team1": t1_form,
                "team2": t2_form
            },
            "h2h": h2h_summary,
            "value_analysis": value_analysis,
            "insights": insights,
            "disclaimer": "Аналитические расчеты и вероятности носят сугубо информационный характер и не являются гарантией исхода."
        }

    @staticmethod
    def get_match_prediction(match_id: int) -> dict[str, Any]:
        """Generate ensemble prediction for match_id."""
        from services.ensemble_engine import EnsemblePredictionEngine
        return EnsemblePredictionEngine.predict_match(match_id, save_to_db=True)

    @staticmethod
    def get_match_preview(match_id: int) -> dict[str, Any]:
        """
        Generate comprehensive AI Match Preview:
        - Ensemble probabilities (Home, Draw, Away, Totals, BTTS)
        - Elo comparison
        - Form scores
        - Value Radar picks
        - Key Factors ('Why?')
        """
        from services.ensemble_engine import EnsemblePredictionEngine
        from services.value_engine import ValueEngine

        pred = EnsemblePredictionEngine.predict_match(match_id, save_to_db=True)
        value_items = ValueEngine.analyze_match_value(match_id, prediction_result=pred)

        return {
            "status": "ok",
            "match_id": match_id,
            "division_id": pred["division_id"],
            "season_id": pred["season_id"],
            "team1": pred["team1"],
            "team2": pred["team2"],
            "probabilities": {
                "home": pred["home_probability"],
                "draw": pred["draw_probability"],
                "away": pred["away_probability"]
            },
            "goals": pred["goals_markets"],
            "confidence": pred["confidence"],
            "elo": pred["sub_models"]["elo"],
            "form": pred["sub_models"]["form"],
            "key_factors": pred["key_factors"],
            "value_radar": [v for v in value_items if v.get("is_value")][:3],
            "disclaimer": "Прогноз AI — аналитическая оценка, а не гарантия результата."
        }

    @staticmethod
    def _get_recent_matches(cursor, team_name: str, division_id: int, season_id: int, limit: int = 5, exclude_match_id: int | None = None) -> list[dict]:
        extra_sql = " AND id != ?" if exclude_match_id is not None else ""
        params = [team_name, team_name, division_id, season_id]
        if exclude_match_id is not None:
            params.append(exclude_match_id)
        params.append(limit)

        cursor.execute(f"""
            SELECT id, round_number, player1_team, player2_team, player1_score, player2_score, status, played_at
            FROM matches
            WHERE (LOWER(player1_team) = LOWER(?) OR LOWER(player2_team) = LOWER(?))
              AND division_id = ? AND season_id = ?
              AND status IN ('confirmed', 'completed', 'finished')
              AND player1_score IS NOT NULL AND player2_score IS NOT NULL
              {extra_sql}
            ORDER BY id DESC
            LIMIT ?
        """, params)
        return [dict(r) for r in cursor.fetchall()]

    @staticmethod
    def _get_h2h_matches(cursor, t1: str, t2: str, division_id: int, season_id: int, limit: int = 10, exclude_match_id: int | None = None) -> list[dict]:
        extra_sql = " AND id != ?" if exclude_match_id is not None else ""
        params = [t1, t2, t2, t1, division_id, season_id]
        if exclude_match_id is not None:
            params.append(exclude_match_id)
        params.append(limit)

        cursor.execute(f"""
            SELECT id, round_number, player1_team, player2_team, player1_score, player2_score, status, played_at
            FROM matches
            WHERE ((LOWER(player1_team) = LOWER(?) AND LOWER(player2_team) = LOWER(?))
                OR (LOWER(player1_team) = LOWER(?) AND LOWER(player2_team) = LOWER(?)))
              AND division_id = ? AND season_id = ?
              AND status IN ('confirmed', 'completed', 'finished')
              AND player1_score IS NOT NULL AND player2_score IS NOT NULL
              {extra_sql}
            ORDER BY id DESC
            LIMIT ?
        """, params)
        return [dict(r) for r in cursor.fetchall()]

    @staticmethod
    def _get_match_odds(cursor, match_id: int) -> dict[str, float]:
        odds: dict[str, float] = {}
        cursor.execute("""
            SELECT ms.selection_key, ms.odds_value
            FROM market_selections ms
            JOIN markets m ON ms.market_id = m.id
            WHERE m.match_id = ? AND ms.status = 'active'
        """, (match_id,))
        for r in cursor.fetchall():
            odds[r["selection_key"]] = float(r["odds_value"])
        return odds

    @staticmethod
    def _analyze_team_form(team_name: str, recent_matches: list[dict]) -> dict[str, Any]:
        wins, draws, losses = 0, 0, 0
        goals_for, goals_against = 0, 0
        form_seq: list[str] = []
        clean_sheets = 0
        btts_count = 0
        over25_count = 0

        for m in recent_matches:
            is_p1 = (m["player1_team"] or "").lower() == team_name.lower()
            my_score = m["player1_score"] if is_p1 else m["player2_score"]
            opp_score = m["player2_score"] if is_p1 else m["player1_score"]

            goals_for += my_score
            goals_against += opp_score

            if my_score > opp_score:
                wins += 1
                form_seq.append("W")
            elif my_score == opp_score:
                draws += 1
                form_seq.append("D")
            else:
                losses += 1
                form_seq.append("L")

            if opp_score == 0:
                clean_sheets += 1
            if my_score > 0 and opp_score > 0:
                btts_count += 1
            if (my_score + opp_score) > 2.5:
                over25_count += 1

        total_games = max(1, len(recent_matches))
        return {
            "matches_played": len(recent_matches),
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "goals_for": goals_for,
            "goals_against": goals_against,
            "avg_scored": round(goals_for / total_games, 2),
            "avg_conceded": round(goals_against / total_games, 2),
            "clean_sheets": clean_sheets,
            "btts_rate": round(btts_count / total_games, 2),
            "over_25_rate": round(over25_count / total_games, 2),
            "form_string": "".join(form_seq) if form_seq else "N/A",
            "momentum_score": round((wins * 3 + draws * 1) / (total_games * 3) * 100, 1) if recent_matches else 50.0
        }

    @staticmethod
    def _analyze_h2h(t1: str, t2: str, h2h_matches: list[dict]) -> dict[str, Any]:
        t1_wins, draws, t2_wins = 0, 0, 0
        total_goals = 0

        for m in h2h_matches:
            s1 = m["player1_score"]
            s2 = m["player2_score"]
            total_goals += (s1 + s2)
            is_t1_p1 = (m["player1_team"] or "").lower() == t1.lower()
            my_s = s1 if is_t1_p1 else s2
            opp_s = s2 if is_t1_p1 else s1

            if my_s > opp_s:
                t1_wins += 1
            elif my_s == opp_s:
                draws += 1
            else:
                t2_wins += 1

        total_h2h = max(1, len(h2h_matches))
        return {
            "total_meetings": len(h2h_matches),
            "team1_wins": t1_wins,
            "draws": draws,
            "team2_wins": t2_wins,
            "avg_goals": round(total_goals / total_h2h, 2) if h2h_matches else 0.0
        }

    @staticmethod
    def _calculate_probabilities_and_edge(
        t1_form: dict[str, Any],
        t2_form: dict[str, Any],
        odds_map: dict[str, float]
    ) -> list[dict[str, Any]]:
        """
        Calculates implied probability from market odds, model probability from form,
        and the resulting edge (model_prob - implied_prob).
        """
        # Base goal expectancies (lambda) using averages, clamped reasonably
        exp_goals_t1 = max(0.5, (t1_form.get("avg_scored", 1.2) + t2_form.get("avg_conceded", 1.2)) / 2.0)
        exp_goals_t2 = max(0.5, (t2_form.get("avg_scored", 1.0) + t1_form.get("avg_conceded", 1.0)) / 2.0)

        # Bivariate Poisson matrix simulation up to 6 goals
        p_home_win = 0.0
        p_draw = 0.0
        p_away_win = 0.0
        p_over25 = 0.0
        p_btts = 0.0

        for g1 in range(7):
            p1 = _poisson_probability(g1, exp_goals_t1)
            for g2 in range(7):
                p2 = _poisson_probability(g2, exp_goals_t2)
                p_joint = p1 * p2
                if g1 > g2:
                    p_home_win += p_joint
                elif g1 == g2:
                    p_draw += p_joint
                else:
                    p_away_win += p_joint

                if (g1 + g2) > 2:
                    p_over25 += p_joint
                if g1 > 0 and g2 > 0:
                    p_btts += p_joint

        # Map to standard outcomes
        model_probs = {
            "p1": round(p_home_win * 100, 2),
            "x": round(p_draw * 100, 2),
            "p2": round(p_away_win * 100, 2),
            "tb25": round(p_over25 * 100, 2),
            "over_2.5": round(p_over25 * 100, 2),
            "btts_yes": round(p_btts * 100, 2),
        }

        results = []
        sample_size = t1_form.get("matches_played", 0) + t2_form.get("matches_played", 0)
        confidence = "HIGH" if sample_size >= 8 else ("MEDIUM" if sample_size >= 4 else "LOW")

        for key, odd in odds_map.items():
            if odd <= 1.0:
                continue
            implied_prob = round((1.0 / odd) * 100, 2)
            model_prob = model_probs.get(key)
            if model_prob is not None:
                edge = round(model_prob - implied_prob, 2)
                results.append({
                    "selection": key,
                    "odds": odd,
                    "implied_probability": implied_prob,
                    "model_probability": model_prob,
                    "edge": edge,
                    "confidence": confidence,
                    "is_value": edge > 3.0  # At least 3 percentage points edge
                })

        return results

    @staticmethod
    def _generate_insights(
        t1: str,
        t2: str,
        t1_form: dict[str, Any],
        t2_form: dict[str, Any],
        h2h: dict[str, Any],
        value_analysis: list[dict[str, Any]]
    ) -> list[str]:
        """Generate 3 to 5 verifiable, data-backed insight statements."""
        insights: list[str] = []

        # 1. Scoring streak / average
        if t1_form["avg_scored"] >= 1.8:
            insights.append(f"⚽ {t1} в отличной форме в атаке: в среднем {t1_form['avg_scored']} гола за матч.")
        elif t1_form["clean_sheets"] >= 2:
            insights.append(f"🛡 {t1} надежен в обороне: {t1_form['clean_sheets']} сухих матча в последних играх.")

        # 2. Over 2.5 trend
        if t1_form["over_25_rate"] >= 0.6 and t2_form["over_25_rate"] >= 0.6:
            insights.append("🔥 Обе команды играют результативно: Тотал Больше 2.5 пробивался более чем в 60% недавних матчей.")

        # 3. H2H dominance
        if h2h["total_meetings"] >= 3:
            if h2h["team1_wins"] > h2h["team2_wins"]:
                insights.append(f"📊 В личных встречах перевес у {t1}: {h2h['team1_wins']} побед против {h2h['team2_wins']}.")
            elif h2h["team2_wins"] > h2h["team1_wins"]:
                insights.append(f"📊 В личных встречах лидирует {t2}: {h2h['team2_wins']} побед против {h2h['team1_wins']}.")
            else:
                insights.append(f"🤝 Равенство в очных дуэлях: {h2h['team1_wins']} побед у каждой стороны.")

        # 4. Value edge note
        value_picks = [v for v in value_analysis if v.get("is_value")]
        if value_picks:
            best_pick = max(value_picks, key=lambda x: x["edge"])
            insights.append(f"💡 Статистический перевес модели на исходе '{best_pick['selection']}': перевес +{best_pick['edge']}% к рыночной котировке.")

        if not insights:
            insights.append(f"📈 {t1} и {t2} демонстрируют сбалансированную форму на старте сезона.")

        return insights
