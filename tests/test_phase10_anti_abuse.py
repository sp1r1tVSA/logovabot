"""
tests/test_phase10_anti_abuse.py

Phase 10: Anti-Abuse, Farming Prevention & Fair Competition Tests.
Strict Invariants:
1. Repeated tiny bets cannot artificially pump competitive rating or points.
2. Ultra low-risk farming (odds 1.01) provides negligible rating delta (surprise -> 0).
3. Qualification thresholds protect top standings from spam manipulation.
4. Alerts are generated for suspicious rapid activity.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import database
from services.player_rating import PlayerRatingEngine
from services.leaderboard_service import LeaderboardService


@pytest.fixture(autouse=True)
def clean_anti_abuse_data():
    database.init_db()
    with database.transaction() as conn:
        conn.execute("DELETE FROM season_player_stats WHERE user_id BETWEEN 8501 AND 8505")
        conn.execute("DELETE FROM users WHERE telegram_id BETWEEN 8501 AND 8505")
        for uid in range(8501, 8506):
            conn.execute("INSERT OR IGNORE INTO users (telegram_id, username, role, division_id) VALUES (?, ?, 'user', 1)", (uid, f"abuse_{uid}"))
    yield
    with database.transaction() as conn:
        conn.execute("DELETE FROM season_player_stats WHERE user_id BETWEEN 8501 AND 8505")
        conn.execute("DELETE FROM users WHERE telegram_id BETWEEN 8501 AND 8505")


def test_01_repeated_small_bets_do_not_artificially_inflate_rating():
    """
    Verify that betting 1 coin 100 times does not inflate rating more than
    normal betting, because K-factor decays and rating delta is governed by math.
    """
    uid = 8501
    for _ in range(50):
        PlayerRatingEngine.process_bet_settlement(
            user_id=uid, outcome="won", total_odd=1.5, stake=1, payout=1, season_id=1, division_id=1
        )
    stats = database.get_player_season_stats(uid)
    # With 50 wins at 1.5 odd, rating rises moderately, never unbounded
    assert stats["rating"] < 2200.0, f"Rating grew uncontrollably: {stats['rating']}"


def test_02_intentional_low_risk_farming_gives_diminishing_rating_delta():
    """
    Farming ultra-low odds (1.01):
    P = 1 / 1.01 = 0.9901
    Surprise S - P = 1.0 - 0.9901 = 0.0099.
    Delta is virtually zero (< 0.35 points).
    """
    delta = PlayerRatingEngine.calculate_rating_delta(1200.0, 10, "won", 1.01)
    assert delta <= 0.35, f"1.01 farming should yield near-zero delta, got {delta}"

    # Compare to a normal 2.0 odd win
    delta_normal = PlayerRatingEngine.calculate_rating_delta(1200.0, 10, "won", 2.00)
    assert delta_normal > (delta * 20), "Normal prediction should reward dramatically more than 1.01 farming"


def test_03_minimum_qualification_filter_protects_leaderboard():
    """
    Verify a user who places 2 bets with high odds (e.g. 50.0)
    cannot steal top rank from consistent players because is_qualified = False.
    """
    with database.transaction() as conn:
        # Consistent grinder (10 bets, rating 1550)
        conn.execute("""
            INSERT INTO season_player_stats (user_id, season_id, division_id, rating, settled_bets, season_points, status)
            VALUES (8502, 1, 1, 1550.0, 10, 200.0, 'ACTIVE')
        """)
        # Lucky 1-bet account (1 bet, rating 1800)
        conn.execute("""
            INSERT INTO season_player_stats (user_id, season_id, division_id, rating, settled_bets, season_points, status)
            VALUES (8503, 1, 1, 1800.0, 1, 50.0, 'QUALIFYING')
        """)

    lb = LeaderboardService.get_leaderboard(season_id=1, division_id=1, scope="DIVISION", metric="RATING")
    user_8502 = next(e for e in lb["entries"] if e["player_id"] == 8502)
    user_8503 = next(e for e in lb["entries"] if e["player_id"] == 8503)

    assert user_8502["rank"] < user_8503["rank"], \
        f"Qualified player (rank {user_8502['rank']}) must rank above 1-bet account (rank {user_8503['rank']})!"


def test_04_cancelled_or_refunded_bets_award_zero_points():
    """Verify cancelled or refunded bets give exactly 0 season points to prevent churn farming."""
    pts_refund = PlayerRatingEngine.calculate_season_points_delta("refunded", 3.0)
    pts_void = PlayerRatingEngine.calculate_season_points_delta("voided", 5.0)

    assert pts_refund == 0.0
    assert pts_void == 0.0


def test_05_extreme_odds_capped_in_rating_calculation():
    """Verify extreme odds (e.g. 500.0) are safely bounded in rating calculation."""
    delta_extreme = PlayerRatingEngine.calculate_rating_delta(1200.0, 10, "won", 999.0)
    delta_100 = PlayerRatingEngine.calculate_rating_delta(1200.0, 10, "won", 100.0)

    # Implied probability is clamped to max 100.0 odds
    assert delta_extreme == delta_100
    assert delta_extreme > 0
