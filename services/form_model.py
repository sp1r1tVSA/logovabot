"""
services/form_model.py

Logovo.bet — Form Scoring and Form-Based Predictive Model.
Normalizes team momentum into a [0.0, 1.0] scale and computes form-driven outcome probabilities:
- 0.0: Extreme slump (all losses, heavy conceding)
- 0.5: Average / balanced form
- 1.0: Perfect momentum (all wins, high scoring, clean sheets)
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class FormModel:
    """Computes normalized form scores and form-based match probabilities."""

    @staticmethod
    def calculate_form_score(
        matches: list[dict],
        team_name: str,
        recency_decay: float = 0.85
    ) -> float:
        """
        Calculate composite normalized form score between 0.0 and 1.0.
        Factors:
        - Points percentage (3 pts per win, 1 pt per draw) with exponential decay.
        - Goal differential impact (+/- up to 15% adjustment).
        - Clean sheet bonus.
        """
        if not matches:
            return 0.50  # Default neutral score for unobserved teams

        total_weight = 0.0
        weighted_points = 0.0
        weighted_gd = 0.0
        clean_sheets = 0

        for i, m in enumerate(matches):
            weight = recency_decay ** i
            total_weight += weight

            is_p1 = (m.get("player1_team") or "").lower() == team_name.lower()
            my_score = m.get("player1_score", 0) if is_p1 else m.get("player2_score", 0)
            opp_score = m.get("player2_score", 0) if is_p1 else m.get("player1_score", 0)

            if my_score > opp_score:
                pts = 3.0
            elif my_score == opp_score:
                pts = 1.0
            else:
                pts = 0.0

            weighted_points += (pts * weight)
            weighted_gd += ((my_score - opp_score) * weight)
            if opp_score == 0:
                clean_sheets += 1

        if total_weight <= 0:
            return 0.50

        # Maximum possible points is 3.0 * total_weight
        base_pct = weighted_points / (3.0 * total_weight)

        # GD adjustment: clamp average weighted GD between -2.0 and +2.0
        avg_gd = weighted_gd / total_weight
        gd_adj = max(-0.15, min(0.15, avg_gd * 0.075))

        # Clean sheet bonus (up to +0.05)
        cs_bonus = min(0.05, (clean_sheets / len(matches)) * 0.05)

        raw_score = base_pct + gd_adj + cs_bonus
        return round(max(0.05, min(0.95, raw_score)), 3)

    @staticmethod
    def calculate_match_probabilities(
        team1_form_score: float,
        team2_form_score: float,
        home_advantage: float = 0.05
    ) -> dict[str, Any]:
        """
        Derive 1X2 probabilities purely from relative form scores.
        Sum strictly normalized to 1.0.
        """
        diff = (team1_form_score + home_advantage) - team2_form_score

        # Baseline probabilities: 42% Home, 28% Draw, 30% Away
        p_home = max(0.05, 0.42 + (0.35 * diff))
        p_draw = max(0.12, 0.28 - (0.10 * abs(diff)))
        p_away = max(0.05, 0.30 - (0.35 * diff))

        total = p_home + p_draw + p_away
        p_home_norm = round(p_home / total, 4)
        p_draw_norm = round(p_draw / total, 4)
        p_away_norm = round(1.0 - p_home_norm - p_draw_norm, 4)

        return {
            "model": "form",
            "team1_form": team1_form_score,
            "team2_form": team2_form_score,
            "form_differential": round(diff, 3),
            "home_probability": p_home_norm,
            "draw_probability": p_draw_norm,
            "away_probability": p_away_norm
        }
