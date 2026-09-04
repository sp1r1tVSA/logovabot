"""
services/feature_engine.py

Logovo.bet — Advanced Sports Data & Feature Engineering Service.
Calculates pre-match features for predictive models:
1. Team Form (last 5, last 10) with exponential recency weighting.
2. Home-specific and Away-specific performance splits.
3. Attack Strength and Defense Weakness relative to league averages.
4. Clean sheets, scoring frequency, conceding frequency, Over 2.5 and BTTS rates.
5. Head-to-Head (H2H) history between teams (strictly excluding current match).
6. xG extraction (if available from live data; explicitly false if unavailable).
7. Anti-Data-Leakage temporal guards: strictly queries matches completed before target match.
"""

import logging
from typing import Any, Optional
import database

logger = logging.getLogger(__name__)

# Centralized, testable recency decay configuration
DEFAULT_RECENCY_DECAY = 0.85
MIN_LEAGUE_AVG_GOALS = 1.20


class FeatureEngine:
    """Calculates standardized, leakage-free feature vectors for football matches."""

    @staticmethod
    def extract_match_features(
        match_id: int,
        decay: float = DEFAULT_RECENCY_DECAY,
        as_of_match_id: Optional[int] = None
    ) -> dict[str, Any]:
        """
        Extract comprehensive feature set for match_id.
        as_of_match_id: if set, limits historical query strictly to matches with id < as_of_match_id.
        Defaults to match_id itself to prevent data leakage from current or future fixtures.
        """
        ref_match_id = as_of_match_id if as_of_match_id is not None else match_id

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

            # 1. League-wide goal averages for attack/defense normalization
            league_avg_home, league_avg_away = FeatureEngine._get_league_averages(
                cursor, div_id, season_id, before_match_id=ref_match_id
            )

            # 2. Team 1 historical matches (overall, home split)
            t1_recent_all = FeatureEngine._get_team_matches(
                cursor, t1, div_id, season_id, limit=10, before_match_id=ref_match_id
            )
            t1_recent_home = FeatureEngine._get_team_matches(
                cursor, t1, div_id, season_id, limit=10, home_only=True, before_match_id=ref_match_id
            )

            # 3. Team 2 historical matches (overall, away split)
            t2_recent_all = FeatureEngine._get_team_matches(
                cursor, t2, div_id, season_id, limit=10, before_match_id=ref_match_id
            )
            t2_recent_away = FeatureEngine._get_team_matches(
                cursor, t2, div_id, season_id, limit=10, away_only=True, before_match_id=ref_match_id
            )

            # 4. Head-to-head matches between t1 and t2 (strictly excluding match_id)
            h2h_matches = FeatureEngine._get_h2h_matches(
                cursor, t1, t2, div_id, season_id, limit=10, before_match_id=ref_match_id
            )

            # 5. Extract xG metrics if present
            t1_xg_info = FeatureEngine._get_team_xg_stats(cursor, t1, div_id, season_id, before_match_id=ref_match_id)
            t2_xg_info = FeatureEngine._get_team_xg_stats(cursor, t2, div_id, season_id, before_match_id=ref_match_id)

        # Compute metric aggregates
        t1_stats = FeatureEngine._compute_team_metrics(t1, t1_recent_all, decay)
        t1_home_stats = FeatureEngine._compute_team_metrics(t1, t1_recent_home, decay)
        t2_stats = FeatureEngine._compute_team_metrics(t2, t2_recent_all, decay)
        t2_away_stats = FeatureEngine._compute_team_metrics(t2, t2_recent_away, decay)
        h2h_stats = FeatureEngine._compute_h2h_metrics(t1, t2, h2h_matches)

        # Attack and Defense strengths relative to league base
        t1_attack_strength = round(t1_stats["avg_scored"] / league_avg_home, 3) if league_avg_home > 0 else 1.0
        t1_defense_weakness = round(t1_stats["avg_conceded"] / league_avg_away, 3) if league_avg_away > 0 else 1.0
        t2_attack_strength = round(t2_stats["avg_scored"] / league_avg_away, 3) if league_avg_away > 0 else 1.0
        t2_defense_weakness = round(t2_stats["avg_conceded"] / league_avg_home, 3) if league_avg_home > 0 else 1.0

        xg_available = bool(t1_xg_info.get("has_data") and t2_xg_info.get("has_data"))

        return {
            "match_id": match_id,
            "division_id": div_id,
            "season_id": season_id,
            "team1": t1,
            "team2": t2,
            "league_averages": {
                "avg_home_goals": league_avg_home,
                "avg_away_goals": league_avg_away,
                "avg_total_goals": round(league_avg_home + league_avg_away, 2)
            },
            "team1_features": {
                "overall": t1_stats,
                "home_split": t1_home_stats,
                "attack_strength": max(0.2, min(3.0, t1_attack_strength)),
                "defense_weakness": max(0.2, min(3.0, t1_defense_weakness)),
                "xg": t1_xg_info
            },
            "team2_features": {
                "overall": t2_stats,
                "away_split": t2_away_stats,
                "attack_strength": max(0.2, min(3.0, t2_attack_strength)),
                "defense_weakness": max(0.2, min(3.0, t2_defense_weakness)),
                "xg": t2_xg_info
            },
            "h2h_features": h2h_stats,
            "xg_available": xg_available,
            "sample_size": t1_stats["matches_played"] + t2_stats["matches_played"]
        }

    @staticmethod
    def _get_league_averages(cursor, division_id: int, season_id: int, before_match_id: int) -> tuple[float, float]:
        """Compute league average home goals and away goals before ref_match_id."""
        cursor.execute("""
            SELECT AVG(player1_score) as avg_home, AVG(player2_score) as avg_away
            FROM matches
            WHERE division_id = ? AND season_id = ?
              AND id < ?
              AND status IN ('confirmed', 'completed', 'finished')
              AND player1_score IS NOT NULL AND player2_score IS NOT NULL
        """, (division_id, season_id, before_match_id))
        row = cursor.fetchone()
        if row and row["avg_home"] is not None and row["avg_away"] is not None:
            return round(float(row["avg_home"]), 2), round(float(row["avg_away"]), 2)
        return MIN_LEAGUE_AVG_GOALS, MIN_LEAGUE_AVG_GOALS

    @staticmethod
    def _get_team_matches(
        cursor,
        team_name: str,
        division_id: int,
        season_id: int,
        limit: int = 10,
        home_only: bool = False,
        away_only: bool = False,
        before_match_id: int = 999999999
    ) -> list[dict]:
        """Fetch completed historical matches for a team strictly before before_match_id."""
        if home_only:
            where_clause = "LOWER(player1_team) = LOWER(?)"
            params: list[Any] = [team_name]
        elif away_only:
            where_clause = "LOWER(player2_team) = LOWER(?)"
            params: list[Any] = [team_name]
        else:
            where_clause = "(LOWER(player1_team) = LOWER(?) OR LOWER(player2_team) = LOWER(?))"
            params = [team_name, team_name]

        sql = f"""
            SELECT id, round_number, player1_team, player2_team, player1_score, player2_score, status, played_at
            FROM matches
            WHERE {where_clause}
              AND division_id = ? AND season_id = ?
              AND id < ?
              AND status IN ('confirmed', 'completed', 'finished')
              AND player1_score IS NOT NULL AND player2_score IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
        """
        params.extend([division_id, season_id, before_match_id, limit])
        cursor.execute(sql, params)
        return [dict(r) for r in cursor.fetchall()]

    @staticmethod
    def _get_h2h_matches(
        cursor,
        t1: str,
        t2: str,
        division_id: int,
        season_id: int,
        limit: int = 10,
        before_match_id: int = 999999999
    ) -> list[dict]:
        """Fetch historical H2H matches between t1 and t2, strictly before before_match_id."""
        cursor.execute("""
            SELECT id, round_number, player1_team, player2_team, player1_score, player2_score, status, played_at
            FROM matches
            WHERE ((LOWER(player1_team) = LOWER(?) AND LOWER(player2_team) = LOWER(?))
                OR (LOWER(player1_team) = LOWER(?) AND LOWER(player2_team) = LOWER(?)))
              AND division_id = ? AND season_id = ?
              AND id < ?
              AND status IN ('confirmed', 'completed', 'finished')
              AND player1_score IS NOT NULL AND player2_score IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
        """, (t1, t2, t2, t1, division_id, season_id, before_match_id, limit))
        return [dict(r) for r in cursor.fetchall()]

    @staticmethod
    def _compute_team_metrics(team_name: str, matches: list[dict], decay: float) -> dict[str, Any]:
        """Compute rolling and recency-weighted metrics for a match sample."""
        if not matches:
            return {
                "matches_played": 0,
                "wins": 0,
                "draws": 0,
                "losses": 0,
                "win_rate": 0.0,
                "draw_rate": 0.0,
                "loss_rate": 0.0,
                "goals_for": 0,
                "goals_against": 0,
                "avg_scored": 1.20,
                "avg_conceded": 1.20,
                "weighted_avg_scored": 1.20,
                "weighted_avg_conceded": 1.20,
                "clean_sheets": 0,
                "clean_sheet_rate": 0.0,
                "scoring_frequency": 0.0,
                "conceding_frequency": 0.0,
                "btts_rate": 0.0,
                "over_25_rate": 0.0,
                "form_string": "N/A"
            }

        wins, draws, losses = 0, 0, 0
        goals_for, goals_against = 0, 0
        clean_sheets = 0
        scored_matches = 0
        conceded_matches = 0
        btts_count = 0
        over25_count = 0
        form_seq = []

        total_weight = 0.0
        weighted_scored = 0.0
        weighted_conceded = 0.0

        for i, m in enumerate(matches):
            weight = decay ** i
            total_weight += weight

            is_p1 = (m["player1_team"] or "").lower() == team_name.lower()
            my_score = m["player1_score"] if is_p1 else m["player2_score"]
            opp_score = m["player2_score"] if is_p1 else m["player1_score"]

            goals_for += my_score
            goals_against += opp_score
            weighted_scored += (my_score * weight)
            weighted_conceded += (opp_score * weight)

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
            else:
                conceded_matches += 1

            if my_score > 0:
                scored_matches += 1

            if my_score > 0 and opp_score > 0:
                btts_count += 1
            if (my_score + opp_score) > 2.5:
                over25_count += 1

        n = len(matches)
        return {
            "matches_played": n,
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "win_rate": round(wins / n, 3),
            "draw_rate": round(draws / n, 3),
            "loss_rate": round(losses / n, 3),
            "goals_for": goals_for,
            "goals_against": goals_against,
            "avg_scored": round(goals_for / n, 2),
            "avg_conceded": round(goals_against / n, 2),
            "weighted_avg_scored": round(weighted_scored / total_weight, 2) if total_weight > 0 else 1.20,
            "weighted_avg_conceded": round(weighted_conceded / total_weight, 2) if total_weight > 0 else 1.20,
            "clean_sheets": clean_sheets,
            "clean_sheet_rate": round(clean_sheets / n, 2),
            "scoring_frequency": round(scored_matches / n, 2),
            "conceding_frequency": round(conceded_matches / n, 2),
            "btts_rate": round(btts_count / n, 2),
            "over_25_rate": round(over25_count / n, 2),
            "form_string": "".join(form_seq)
        }

    @staticmethod
    def _compute_h2h_metrics(t1: str, t2: str, matches: list[dict]) -> dict[str, Any]:
        """Analyze head-to-head records between two clubs."""
        if not matches:
            return {
                "total_meetings": 0,
                "team1_wins": 0,
                "draws": 0,
                "team2_wins": 0,
                "avg_goals": 0.0,
                "has_h2h_data": False
            }

        t1_wins, draws, t2_wins = 0, 0, 0
        total_goals = 0

        for m in matches:
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

        n = len(matches)
        return {
            "total_meetings": n,
            "team1_wins": t1_wins,
            "draws": draws,
            "team2_wins": t2_wins,
            "avg_goals": round(total_goals / n, 2),
            "has_h2h_data": True
        }

    @staticmethod
    def _get_team_xg_stats(cursor, team_name: str, division_id: int, season_id: int, before_match_id: int) -> dict[str, Any]:
        """
        Extract xG metrics from live_statistics if available.
        Strict rule: NO FAKE xG. If not present in DB, returns has_data = False.
        """
        cursor.execute("""
            SELECT AVG(ls.xg_home) as xg_as_home, AVG(ls.xg_away) as xg_as_away
            FROM live_statistics ls
            JOIN matches m ON ls.match_id = m.id
            WHERE (LOWER(m.player1_team) = LOWER(?) OR LOWER(m.player2_team) = LOWER(?))
              AND m.division_id = ? AND m.season_id = ?
              AND m.id < ?
              AND (ls.xg_home IS NOT NULL OR ls.xg_away IS NOT NULL)
        """, (team_name, team_name, division_id, season_id, before_match_id))
        row = cursor.fetchone()

        if row and (row["xg_as_home"] is not None or row["xg_as_away"] is not None):
            h_xg = float(row["xg_as_home"]) if row["xg_as_home"] is not None else 0.0
            a_xg = float(row["xg_as_away"]) if row["xg_as_away"] is not None else 0.0
            return {
                "has_data": True,
                "avg_xg": round((h_xg + a_xg) / 2.0, 2),
                "is_synthetic": False
            }

        return {
            "has_data": False,
            "avg_xg": None,
            "is_synthetic": False
        }
