"""
services/player_rating.py

Competitive Player Rating & Season Points Engine for Logovo.bet.
Strict Invariants:
1. Team Elo (services/elo_engine.py) != Player Rating.
   Football team Elo predicts match outcomes.
   Player Rating evaluates the prediction skill of users.
2. Non-Stake Bias: Rating and Season Points DO NOT scale with coin stake amount.
   A 10,000-coin bet has the exact same skill weight as a 10-coin bet.
   Rich players cannot buy competitive rating.
3. Minimum Sample Requirement: Players with < min_bets (default 5) are in 'QUALIFYING'
   status and display 'NOT_ENOUGH_DATA' on competitive leaderboards.
4. Deterministic updates on bet settlement.
"""

import math
import logging
from typing import Optional
import database

logger = logging.getLogger(__name__)

DEFAULT_PLAYER_RATING = 1200.0
MIN_QUALIFYING_BETS = 5
MAX_RATING = 3000.0
MIN_RATING = 500.0

# Status tiers based on rating and qualification
TIER_ROOKIE = "ROOKIE"
TIER_RISING = "RISING"
TIER_PRO = "PRO"
TIER_ELITE = "ELITE"
TIER_MASTER = "MASTER"


class PlayerRatingEngine:
    """Authoritative evaluator of competitive player ratings and season points."""

    @staticmethod
    def get_tier(rating: float, settled_bets: int, min_bets: int = MIN_QUALIFYING_BETS) -> str:
        """Return player status tier."""
        if settled_bets < min_bets:
            return TIER_ROOKIE
        if rating >= 2100.0:
            return TIER_MASTER
        elif rating >= 1800.0:
            return TIER_ELITE
        elif rating >= 1500.0:
            return TIER_PRO
        elif rating >= 1300.0:
            return TIER_RISING
        return TIER_ROOKIE

    @staticmethod
    def calculate_rating_delta(
        current_rating: float,
        settled_bets_count: int,
        outcome: str,
        total_odd: float,
        is_value: bool = False
    ) -> float:
        """
        Calculate mathematical rating change.
        Formula:
            Delta = K * (S - P) * W_value
            where:
            S = 1.0 (won), 0.0 (lost)
            P = 1.0 / max(1.01, total_odd) (market implied probability)
            K = dynamic sensitivity factor (higher for newer players)
            W_value = optional bonus weight for identifying value
        """
        if outcome in ("refunded", "voided", "cancelled"):
            return 0.0

        s = 1.0 if outcome == "won" else 0.0
        p = 1.0 / max(1.01, min(100.0, float(total_odd)))

        # Dynamic K-factor based on sample size
        if settled_bets_count < 10:
            k = 32.0
        elif settled_bets_count < 30:
            k = 24.0
        elif settled_bets_count < 100:
            k = 16.0
        else:
            k = 10.0

        surprise = s - p
        delta = k * surprise

        if is_value and s == 1.0:
            delta *= 1.15

        return round(delta, 2)

    @staticmethod
    def calculate_season_points_delta(outcome: str, total_odd: float, is_value: bool = False) -> float:
        """
        Calculate competitive season points change.
        Won bet awards points proportional to odds difficulty: base 10 + (odds * 5).
        Lost bet awards small participation points (1.0).
        Refunded bet gives 0.0 points.
        Crucially: Stake size is NEVER factored into points.
        """
        if outcome == "won":
            pts = 10.0 + (min(10.0, float(total_odd)) * 5.0)
            if is_value:
                pts += 15.0
            return round(pts, 1)
        elif outcome == "lost":
            return 1.0
        return 0.0

    @classmethod
    def process_bet_settlement(
        cls,
        user_id: int,
        outcome: str,
        total_odd: float,
        stake: int,
        payout: int,
        season_id: Optional[int] = None,
        division_id: Optional[int] = None,
        is_value: bool = False
    ) -> dict:
        """
        Update player season stats and competitive rating upon bet settlement.
        Returns dict with updated metrics and tier.
        """
        # 1. Fetch current season stats
        stats = database.get_or_create_season_stats(user_id, season_id, division_id)
        s_id = stats["season_id"]
        d_id = stats["division_id"]

        cur_rating = float(stats["rating"])
        cur_points = float(stats["season_points"])
        cur_settled = stats["settled_bets"]
        cur_wins = stats["wins"]
        cur_losses = stats["losses"]
        cur_voids = stats["voids"]
        cur_stake = stats["total_stake"] + stake
        cur_payout = stats["total_payout"] + (payout if outcome == "won" else (stake if outcome == "refunded" else 0))
        cur_value_hits = stats["value_bets_hit"] + (1 if is_value and outcome == "won" else 0)

        # 2. Compute deltas
        if outcome == "won":
            cur_settled += 1
            cur_wins += 1
            r_delta = cls.calculate_rating_delta(cur_rating, cur_settled, "won", total_odd, is_value)
            pts_delta = cls.calculate_season_points_delta("won", total_odd, is_value)
        elif outcome == "lost":
            cur_settled += 1
            cur_losses += 1
            r_delta = cls.calculate_rating_delta(cur_rating, cur_settled, "lost", total_odd, is_value)
            pts_delta = cls.calculate_season_points_delta("lost", total_odd, is_value)
        else:
            cur_voids += 1
            r_delta = 0.0
            pts_delta = 0.0

        new_rating = max(MIN_RATING, min(MAX_RATING, cur_rating + r_delta))
        new_points = max(0.0, cur_points + pts_delta)

        # 3. Calculate Win Rate & ROI
        win_rate = round((cur_wins / max(1, cur_settled)) * 100, 1) if cur_settled > 0 else 0.0
        roi = round(((cur_payout - cur_stake) / max(1, cur_stake)) * 100, 1) if cur_stake > 0 else 0.0

        # 4. Status determination
        rules = database.get_season_rules(s_id, d_id)
        min_b = rules.get("min_bets_qualification", MIN_QUALIFYING_BETS)
        new_status = "ACTIVE" if cur_settled >= min_b else "QUALIFYING"

        tier = cls.get_tier(new_rating, cur_settled, min_b)

        # 5. Persist updates
        database.update_season_player_stats(
            user_id=user_id,
            season_id=s_id,
            division_id=d_id,
            rating=new_rating,
            season_points=new_points,
            settled_bets=cur_settled,
            wins=cur_wins,
            losses=cur_losses,
            voids=cur_voids,
            win_rate=win_rate,
            roi=roi,
            total_stake=cur_stake,
            total_payout=cur_payout,
            value_bets_hit=cur_value_hits,
            status=new_status
        )

        return {
            "user_id": user_id,
            "season_id": s_id,
            "division_id": d_id,
            "rating": new_rating,
            "rating_delta": r_delta,
            "season_points": new_points,
            "points_delta": pts_delta,
            "settled_bets": cur_settled,
            "win_rate": win_rate,
            "roi": roi,
            "status": new_status,
            "tier": tier
        }
