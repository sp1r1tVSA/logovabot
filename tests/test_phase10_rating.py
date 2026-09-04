"""
tests/test_phase10_rating.py

Phase 10: Player Competitive Rating Formula & Non-Stake Bias Tests.
Strict Invariants:
1. Team Elo != Player Rating.
2. Non-Stake Bias: Rating and Points NEVER scale with coin bet size.
3. Minimum sample requirement: Players with < min_bets remain 'QUALIFYING'.
4. Voids/refunds have zero rating delta.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import database
from services.player_rating import PlayerRatingEngine, TIER_ROOKIE, TIER_RISING, TIER_PRO, TIER_ELITE, TIER_MASTER


@pytest.fixture(autouse=True)
def clean_users():
    database.init_db()
    with database.transaction() as conn:
        conn.execute("DELETE FROM season_player_stats WHERE user_id IN (2001, 2002)")
    yield
    with database.transaction() as conn:
        conn.execute("DELETE FROM season_player_stats WHERE user_id IN (2001, 2002)")


def test_01_rating_formula_increases_on_win_and_decreases_on_loss():
    """Verify that winning a bet increases rating and losing decreases rating according to odds difficulty."""
    # Won bet with odd 2.0 (P = 0.5)
    delta_win = PlayerRatingEngine.calculate_rating_delta(1200.0, 1, "won", 2.0)
    assert delta_win > 0, f"Winning should increase rating, got {delta_win}"

    # Lost bet with odd 2.0
    delta_loss = PlayerRatingEngine.calculate_rating_delta(1200.0, 1, "lost", 2.0)
    assert delta_loss < 0, f"Losing should decrease rating, got {delta_loss}"

    # High odds underdog win should give bigger boost than low odds favorite win
    delta_dog = PlayerRatingEngine.calculate_rating_delta(1200.0, 1, "won", 4.0)
    delta_fav = PlayerRatingEngine.calculate_rating_delta(1200.0, 1, "won", 1.25)
    assert delta_dog > delta_fav, f"Underdog win delta ({delta_dog}) must exceed favorite win delta ({delta_fav})"


def test_02_rating_non_stake_bias_rich_vs_regular():
    """
    CRITICAL: Verify that a 10,000-coin bet produces the EXACT SAME rating and season points delta
    as a 10-coin bet for the same odds and outcome. Rich players cannot buy rank.
    """
    res_regular = PlayerRatingEngine.process_bet_settlement(
        user_id=2001, outcome="won", total_odd=2.50, stake=10, payout=25, season_id=1, division_id=1
    )
    res_whale = PlayerRatingEngine.process_bet_settlement(
        user_id=2002, outcome="won", total_odd=2.50, stake=10000, payout=25000, season_id=1, division_id=1
    )

    assert res_regular["rating"] == res_whale["rating"], \
        f"Rating must be independent of stake! Regular: {res_regular['rating']}, Whale: {res_whale['rating']}"
    assert res_regular["season_points"] == res_whale["season_points"], \
        f"Season points must be independent of stake! Regular: {res_regular['season_points']}, Whale: {res_whale['season_points']}"


def test_03_minimum_sample_requirement_not_enough_data():
    """Verify that players with < 5 settled bets have status 'QUALIFYING'."""
    res = PlayerRatingEngine.process_bet_settlement(
        user_id=2001, outcome="won", total_odd=2.0, stake=100, payout=200, season_id=1, division_id=1
    )
    assert res["settled_bets"] == 1
    assert res["status"] == "QUALIFYING"
    assert res["tier"] == TIER_ROOKIE


def test_04_status_qualifying_to_active_transition():
    """Verify transition from QUALIFYING to ACTIVE when reaching minimum settled bets."""
    for i in range(4):
        PlayerRatingEngine.process_bet_settlement(
            user_id=2001, outcome="won", total_odd=2.0, stake=100, payout=200, season_id=1, division_id=1
        )
    # 4 bets -> QUALIFYING
    s = database.get_player_season_stats(2001)
    assert s["status"] == "QUALIFYING"

    # 5th bet -> ACTIVE
    res = PlayerRatingEngine.process_bet_settlement(
        user_id=2001, outcome="won", total_odd=2.0, stake=100, payout=200, season_id=1, division_id=1
    )
    assert res["settled_bets"] == 5
    assert res["status"] == "ACTIVE"


def test_05_void_refund_preserves_rating():
    """Verify refunded or voided bet produces zero rating and zero point changes."""
    s_before = database.get_player_season_stats(2001)
    res = PlayerRatingEngine.process_bet_settlement(
        user_id=2001, outcome="refunded", total_odd=2.0, stake=500, payout=500, season_id=1, division_id=1
    )
    assert res["rating_delta"] == 0.0
    assert res["points_delta"] == 0.0
    assert res["rating"] == s_before["rating"]


def test_06_player_tier_resolution():
    """Verify status tiers based on rating and settled bets."""
    assert PlayerRatingEngine.get_tier(1250.0, 10) == TIER_ROOKIE
    assert PlayerRatingEngine.get_tier(1350.0, 10) == TIER_RISING
    assert PlayerRatingEngine.get_tier(1600.0, 10) == TIER_PRO
    assert PlayerRatingEngine.get_tier(1900.0, 10) == TIER_ELITE
    assert PlayerRatingEngine.get_tier(2200.0, 10) == TIER_MASTER
    # Even if 2200 rating, if settled bets < 5 -> stays Rookie (Qualifying)
    assert PlayerRatingEngine.get_tier(2200.0, 2) == TIER_ROOKIE
