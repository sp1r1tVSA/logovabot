"""
tests/test_phase10_leaderboard.py

Phase 10: Fair Leaderboard Engine, Scopes, Pagination & Caching Tests.
Strict Invariants:
1. Primary metric is 'rating'. Unqualified players (< min_bets) are ranked below qualified.
2. Division leaderboard strictly filters by division_id.
3. Pagination limit is enforced between 1 and 50.
4. Authenticated user pin is computed accurately.
5. In-memory cache is invalidated upon settlement.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import database
from services.leaderboard_service import LeaderboardService, invalidate_leaderboard_cache


@pytest.fixture(autouse=True)
def clean_leaderboard_users():
    database.init_db()
    invalidate_leaderboard_cache()
    with database.transaction() as conn:
        conn.execute("DELETE FROM season_player_stats WHERE user_id BETWEEN 3001 AND 3010")
        conn.execute("DELETE FROM users WHERE telegram_id BETWEEN 3001 AND 3010")

        # Seed 5 players in Division 1, 2 in Division 2
        for uid, div, rating, settled, pts in [
            (3001, 1, 1600.0, 10, 250.0),
            (3002, 1, 1500.0, 8, 200.0),
            (3003, 1, 1400.0, 6, 150.0),
            (3004, 1, 1900.0, 1, 50.0),   # High rating but unqualified (1 bet < 5)
            (3005, 1, 1200.0, 5, 100.0),
            (3006, 2, 1700.0, 15, 300.0), # Division 2 player
            (3007, 2, 1300.0, 10, 110.0), # Division 2 player
        ]:
            conn.execute("INSERT OR REPLACE INTO users (telegram_id, username, division_id) VALUES (?, ?, ?)",
                         (uid, f"user_{uid}", div))
            conn.execute("""
                INSERT OR REPLACE INTO season_player_stats (user_id, season_id, division_id, rating, settled_bets, season_points, status)
                VALUES (?, 1, ?, ?, ?, ?, ?)
            """, (uid, div, rating, settled, pts, "ACTIVE" if settled >= 5 else "QUALIFYING"))

    yield

    with database.transaction() as conn:
        conn.execute("DELETE FROM season_player_stats WHERE user_id BETWEEN 3001 AND 3010")
        conn.execute("DELETE FROM users WHERE telegram_id BETWEEN 3001 AND 3010")
    invalidate_leaderboard_cache()


def test_01_global_leaderboard_ordering_by_rating():
    """Verify that qualified players are ranked ahead by rating in global leaderboard."""
    lb = LeaderboardService.get_leaderboard(season_id=1, scope="GLOBAL", metric="RATING")
    assert lb["total_players"] >= 7

    # First entry should be highest qualified player across all divisions (3006 with rating 1700)
    top_entry = next(e for e in lb["entries"] if e["player_id"] in (3001, 3002, 3003, 3004, 3005, 3006, 3007))
    assert top_entry["player_id"] == 3006
    assert top_entry["rating"] == 1700.0
    assert top_entry["is_qualified"] is True


def test_02_division_leaderboard_isolation():
    """Verify Division 1 leaderboard strictly excludes Division 2 players."""
    lb = LeaderboardService.get_leaderboard(season_id=1, division_id=1, scope="DIVISION", metric="RATING")
    for entry in lb["entries"]:
        assert entry["division_id"] == 1, f"Division 1 leaderboard contains alien division player: {entry}"
        assert entry["player_id"] not in (3006, 3007), "Division 2 players must not appear in Division 1!"


def test_03_fair_leaderboard_not_enough_data_flagging():
    """
    Verify that user 3004 (rating 1900 but only 1 bet < 5) is marked NOT_ENOUGH_DATA
    and placed below qualified players.
    """
    lb = LeaderboardService.get_leaderboard(season_id=1, division_id=1, scope="DIVISION", metric="RATING")
    unqual_entry = next(e for e in lb["entries"] if e["player_id"] == 3004)
    assert unqual_entry["is_qualified"] is False
    assert unqual_entry["status"] == "NOT_ENOUGH_DATA"

    # In Division 1, 3001 (1600), 3002 (1500), 3003 (1400), 3005 (1200) are qualified.
    # 3004 must be ranked after the qualified players.
    qual_ranks = [e["rank"] for e in lb["entries"] if e["is_qualified"] and e["player_id"] in (3001, 3002, 3003, 3005)]
    assert all(r < unqual_entry["rank"] for r in qual_ranks), "All qualified players must rank above NOT_ENOUGH_DATA"


def test_04_pagination_clamping_limits():
    """Verify pagination limit is strictly clamped between 1 and 50."""
    lb_huge = LeaderboardService.get_leaderboard(season_id=1, scope="GLOBAL", limit=99999)
    assert lb_huge["limit"] == 50, f"Limit must be capped at 50, got {lb_huge['limit']}"

    lb_neg = LeaderboardService.get_leaderboard(season_id=1, scope="GLOBAL", limit=-10)
    assert lb_neg["limit"] == 1, f"Negative limit must be clamped to 1, got {lb_neg['limit']}"


def test_05_user_pin_standing_computed():
    """Verify user_pin correctly identifies player rank even when outside current page."""
    lb_page2 = LeaderboardService.get_leaderboard(season_id=1, division_id=1, scope="DIVISION", page=2, limit=2, user_id=3001)
    pin = lb_page2["user_pin"]
    assert pin is not None
    assert pin["entry"]["player_id"] == 3001
    assert pin["rank"] == 1
    assert pin["on_current_page"] is False  # 3001 is on page 1, not page 2


def test_06_cache_invalidation_upon_settlement():
    """Verify that updating database and calling invalidate_leaderboard_cache refreshes cached data."""
    # First query warms cache
    lb1 = LeaderboardService.get_leaderboard(season_id=1, division_id=1, scope="DIVISION", metric="RATING")
    old_top = lb1["entries"][0]["rating"]

    # Directly alter player rating in DB
    with database.transaction() as conn:
        conn.execute("UPDATE season_player_stats SET rating = 2500.0 WHERE user_id = 3001 AND season_id = 1")

    # Before invalidation, cache might still return old
    invalidate_leaderboard_cache(season_id=1, division_id=1)

    # After invalidation, fresh data is loaded
    lb2 = LeaderboardService.get_leaderboard(season_id=1, division_id=1, scope="DIVISION", metric="RATING")
    new_top = lb2["entries"][0]["rating"]
    assert new_top == 2500.0
