"""
services/elo_engine.py

Logovo.bet — Dedicated Football Elo Rating Engine.
Strict Invariants:
1. Pure calculation during prediction: ZERO mutation of team ratings during inference.
2. Ratings are division- and season-scoped.
3. Outcome probabilities (Home, Draw, Away) are mathematically bounded and sum to 1.0.
4. Rating updates occur only after confirmed post-match results.
"""

import math
import logging
from typing import Any, Optional
import database

logger = logging.getLogger(__name__)

DEFAULT_ELO_RATING = 1500.0
DEFAULT_K_FACTOR = 24.0
DEFAULT_HOME_ADVANTAGE = 65.0
BASE_DRAW_PROBABILITY = 0.28
DRAW_SIGMA = 250.0


class EloEngine:
    """Read-only during prediction; updates ratings strictly after verified match completion."""

    @staticmethod
    def get_rating(team_name: str, division_id: int = 1, season_id: int = 1) -> float:
        """Fetch current Elo rating for team from SQLite repository."""
        return database.get_team_elo(team_name, division_id=division_id, season_id=season_id)

    @staticmethod
    def calculate_match_probabilities(
        team1: str,
        team2: str,
        division_id: int = 1,
        season_id: int = 1,
        home_advantage: float = DEFAULT_HOME_ADVANTAGE,
        custom_r1: Optional[float] = None,
        custom_r2: Optional[float] = None
    ) -> dict[str, Any]:
        """
        Calculate 1X2 probabilities based on Elo rating differential.
        Zero mutation: does NOT alter database records.
        """
        r1 = custom_r1 if custom_r1 is not None else EloEngine.get_rating(team1, division_id, season_id)
        r2 = custom_r2 if custom_r2 is not None else EloEngine.get_rating(team2, division_id, season_id)

        # Home team gets home_advantage adjustment
        effective_r1 = r1 + home_advantage
        rating_diff = effective_r1 - r2

        # Expected score (win + 0.5 * draw)
        expected_home = 1.0 / (1.0 + 10.0 ** (-rating_diff / 400.0))

        # Model draw probability using Gaussian decay around rating parity
        # High rating difference decreases draw probability
        p_draw = BASE_DRAW_PROBABILITY * math.exp(- (rating_diff ** 2) / (2.0 * (DRAW_SIGMA ** 2)))
        p_draw = max(0.08, min(0.38, p_draw))

        # Allocate win and loss probabilities based on expected score and draw probability
        p_home_raw = expected_home - (0.5 * p_draw)
        p_away_raw = (1.0 - expected_home) - (0.5 * p_draw)

        # Clamp and normalize
        p_home = max(0.02, p_home_raw)
        p_away = max(0.02, p_away_raw)
        total_p = p_home + p_draw + p_away

        p_home_norm = round(p_home / total_p, 4)
        p_draw_norm = round(p_draw / total_p, 4)
        p_away_norm = round(1.0 - p_home_norm - p_draw_norm, 4)

        return {
            "model": "elo",
            "team1": team1,
            "team2": team2,
            "rating_team1": round(r1, 1),
            "rating_team2": round(r2, 1),
            "effective_rating_team1": round(effective_r1, 1),
            "rating_diff": round(rating_diff, 1),
            "home_probability": p_home_norm,
            "draw_probability": p_draw_norm,
            "away_probability": p_away_norm,
            "expected_score_home": round(expected_home, 3)
        }

    @staticmethod
    def calculate_new_ratings(
        r1: float,
        r2: float,
        score1: int,
        score2: int,
        k_factor: float = DEFAULT_K_FACTOR,
        home_advantage: float = DEFAULT_HOME_ADVANTAGE
    ) -> tuple[float, float]:
        """
        Compute new Elo ratings given match score without mutating database.
        Used by backtesters and post-match updaters.
        """
        effective_r1 = r1 + home_advantage
        rating_diff = effective_r1 - r2
        expected_home = 1.0 / (1.0 + 10.0 ** (-rating_diff / 400.0))
        expected_away = 1.0 - expected_home

        if score1 > score2:
            actual_home, actual_away = 1.0, 0.0
        elif score1 == score2:
            actual_home, actual_away = 0.5, 0.5
        else:
            actual_home, actual_away = 0.0, 1.0

        # Goal difference multiplier (margin of victory scaling)
        goal_diff = abs(score1 - score2)
        if goal_diff <= 1:
            mov_mult = 1.0
        elif goal_diff == 2:
            mov_mult = 1.35
        else:
            mov_mult = (1.5 + (goal_diff - 3) / 8.0)

        delta1 = k_factor * mov_mult * (actual_home - expected_home)
        delta2 = k_factor * mov_mult * (actual_away - expected_away)

        new_r1 = max(800.0, min(2400.0, r1 + delta1))
        new_r2 = max(800.0, min(2400.0, r2 + delta2))

        return round(new_r1, 2), round(new_r2, 2)

    @staticmethod
    def update_ratings_post_match(
        match_id: int,
        team1: str,
        team2: str,
        score1: int,
        score2: int,
        division_id: int = 1,
        season_id: int = 1,
        k_factor: float = DEFAULT_K_FACTOR
    ) -> tuple[float, float]:
        """Update persistent database ratings for both teams following confirmed match score."""
        current_r1 = database.get_team_elo(team1, division_id, season_id)
        current_r2 = database.get_team_elo(team2, division_id, season_id)

        new_r1, new_r2 = EloEngine.calculate_new_ratings(
            current_r1, current_r2, score1, score2, k_factor=k_factor
        )

        database.update_team_elo(team1, division_id, season_id, new_r1)
        database.update_team_elo(team2, division_id, season_id, new_r2)

        logger.info(
            f"Updated Elo for Match #{match_id} ({team1} vs {team2}): "
            f"{team1} {current_r1} -> {new_r1}, {team2} {current_r2} -> {new_r2}"
        )
        return new_r1, new_r2
