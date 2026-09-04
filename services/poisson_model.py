"""
services/poisson_model.py

Logovo.bet — Poisson 2.0 Bivariate Goal Expectancy & Distribution Engine.
Strict Invariants:
1. Expected goals (lambda) are strictly bounded [0.20, 4.50] to eliminate mathematical singularities.
2. Complete bivariate grid calculation (0 to 6 goals per team).
3. Evaluates 1X2, Over/Under (1.5, 2.5, 3.5), Both Teams To Score (BTTS), and Correct Scores.
4. All probabilities satisfy 0.0 <= P <= 1.0; 1X2 sums strictly to 1.0.
"""

import math
import logging
from typing import Any

logger = logging.getLogger(__name__)

MAX_SCORE_GRID = 7  # 0 to 6 goals
LAMBDA_MIN = 0.20
LAMBDA_MAX = 4.50


def poisson_pmf(k: int, lamb: float) -> float:
    """Evaluate Poisson probability mass function P(k; lambda)."""
    if lamb <= 0.0:
        return 1.0 if k == 0 else 0.0
    try:
        return (math.exp(-lamb) * (lamb ** k)) / math.factorial(k)
    except OverflowError:
        return 0.0


class PoissonModel:
    """Calculates bivariate Poisson distributions from team attack and defense ratings."""

    @staticmethod
    def calculate_expected_goals(
        attack1: float,
        defense1: float,
        attack2: float,
        defense2: float,
        league_avg_home: float = 1.40,
        league_avg_away: float = 1.10,
        home_advantage_multiplier: float = 1.15
    ) -> tuple[float, float]:
        """
        Derive dynamic lambda (expected goals) for home and away teams.
        Clamped to prevent zero division and mathematical distortion.
        """
        raw_lambda_home = attack1 * defense2 * league_avg_home * home_advantage_multiplier
        raw_lambda_away = attack2 * defense1 * league_avg_away

        lambda_home = max(LAMBDA_MIN, min(LAMBDA_MAX, raw_lambda_home))
        lambda_away = max(LAMBDA_MIN, min(LAMBDA_MAX, raw_lambda_away))

        return round(lambda_home, 3), round(lambda_away, 3)

    @staticmethod
    def calculate_match_probabilities(
        lambda_home: float,
        lambda_away: float
    ) -> dict[str, Any]:
        """
        Simulate bivariate grid across 0-6 goals and produce complete market probabilities.
        """
        lh = max(LAMBDA_MIN, min(LAMBDA_MAX, lambda_home))
        la = max(LAMBDA_MIN, min(LAMBDA_MAX, lambda_away))

        p_home_win = 0.0
        p_draw = 0.0
        p_away_win = 0.0

        p_over_15 = 0.0
        p_over_25 = 0.0
        p_over_35 = 0.0

        correct_scores: dict[str, float] = {}

        for g1 in range(MAX_SCORE_GRID):
            p1 = poisson_pmf(g1, lh)
            for g2 in range(MAX_SCORE_GRID):
                p2 = poisson_pmf(g2, la)
                joint_p = p1 * p2

                if g1 > g2:
                    p_home_win += joint_p
                elif g1 == g2:
                    p_draw += joint_p
                else:
                    p_away_win += joint_p

                total_goals = g1 + g2
                if total_goals > 1:
                    p_over_15 += joint_p
                if total_goals > 2:
                    p_over_25 += joint_p
                if total_goals > 3:
                    p_over_35 += joint_p

                # Record top likely scores
                if g1 <= 4 and g2 <= 4:
                    correct_scores[f"{g1}:{g2}"] = round(joint_p, 4)

        # Normalize 1X2 outcomes to account for truncated tail (>6 goals)
        total_1x2 = p_home_win + p_draw + p_away_win
        p_home_norm = round(p_home_win / total_1x2, 4)
        p_draw_norm = round(p_draw / total_1x2, 4)
        p_away_norm = round(1.0 - p_home_norm - p_draw_norm, 4)

        # Both Teams To Score (analytic formula: 1 - e^-lh)*(1 - e^-la)
        p_btts_yes = round((1.0 - math.exp(-lh)) * (1.0 - math.exp(-la)), 4)
        p_btts_no = round(1.0 - p_btts_yes, 4)

        # Over/Under outcomes
        p_over_15_norm = round(min(0.99, max(0.01, p_over_15)), 4)
        p_under_15_norm = round(1.0 - p_over_15_norm, 4)

        p_over_25_norm = round(min(0.99, max(0.01, p_over_25)), 4)
        p_under_25_norm = round(1.0 - p_over_25_norm, 4)

        p_over_35_norm = round(min(0.99, max(0.01, p_over_35)), 4)
        p_under_35_norm = round(1.0 - p_over_35_norm, 4)

        return {
            "model": "poisson_2",
            "lambda_home": lh,
            "lambda_away": la,
            "home_probability": p_home_norm,
            "draw_probability": p_draw_norm,
            "away_probability": p_away_norm,
            "over_1_5_probability": p_over_15_norm,
            "under_1_5_probability": p_under_15_norm,
            "over_2_5_probability": p_over_25_norm,
            "under_2_5_probability": p_under_25_norm,
            "over_3_5_probability": p_over_35_norm,
            "under_3_5_probability": p_under_35_norm,
            "btts_yes_probability": p_btts_yes,
            "btts_no_probability": p_btts_no,
            "correct_scores": correct_scores
        }
