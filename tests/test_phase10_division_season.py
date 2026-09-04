"""
tests/test_phase10_division_season.py

Phase 10: Multi-Division (5 Divisions) & Multi-Season (Seasons 1-3) Isolation Tests.
Strict Invariants:
1. 5 Divisions (DIV_1 to DIV_5) maintain completely isolated standings and stats.
2. Season 1 wagers and points NEVER bleed into Season 2 or Season 3.
3. Division-specific rules do not interfere across divisions.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import database
from services.season_progression import SeasonProgressionEngine
from services.leaderboard_service import LeaderboardService


@pytest.fixture(autouse=True)
def clean_multi_div_data():
    database.init_db()
    with database.transaction() as conn:
        conn.execute("DELETE FROM season_snapshots WHERE season_id IN (98, 99)")
        conn.execute("DELETE FROM season_player_stats WHERE season_id IN (98, 99)")
        conn.execute("DELETE FROM season_rules_config WHERE season_id IN (98, 99)")
        conn.execute("DELETE FROM seasons WHERE id IN (98, 99)")
        conn.execute("DELETE FROM users WHERE telegram_id BETWEEN 9600 AND 9640")
        for uid in range(9600, 9640):
            conn.execute("INSERT OR IGNORE INTO users (telegram_id, username, role, division_id) VALUES (?, ?, 'user', 1)", (uid, f"u{uid}"))
        conn.execute("INSERT OR IGNORE INTO seasons (id, name, status) VALUES (98, 'Multi-Div Season 98', 'active')")
        conn.execute("INSERT OR IGNORE INTO seasons (id, name, status) VALUES (99, 'Multi-Div Season 99', 'active')")

    yield

    with database.transaction() as conn:
        conn.execute("DELETE FROM season_snapshots WHERE season_id IN (98, 99)")
        conn.execute("DELETE FROM season_player_stats WHERE season_id IN (98, 99)")
        conn.execute("DELETE FROM season_rules_config WHERE season_id IN (98, 99)")
        conn.execute("DELETE FROM seasons WHERE id IN (98, 99)")
        conn.execute("DELETE FROM users WHERE telegram_id BETWEEN 9600 AND 9640")


def test_01_all_five_divisions_have_isolated_standings():
    """Verify each of the 5 divisions computes its standings independently."""

    # Seed 1 player in each of the 5 divisions
    for d in range(1, 6):
        uid = 9600 + d
        database.update_season_player_stats(
            user_id=uid, season_id=98, division_id=d,
            rating=1500.0 + (d * 10), settled_bets=10, season_points=100.0 * d
        )

    for d in range(1, 6):
        standings = SeasonProgressionEngine.get_division_standings(season_id=98, division_id=d)
        assert len(standings) == 1
        assert standings[0]["division_id"] == d
        assert standings[0]["user_id"] == 9600 + d


def test_02_division_1_points_do_not_bleed_into_division_2():
    """Verify points earned in Division 1 do not inflate Division 2 standings."""
    uid = 9610
    # Player plays in Division 1
    database.update_season_player_stats(
        user_id=uid, season_id=98, division_id=1, rating=1600.0, season_points=500.0, settled_bets=10
    )

    # Inquire Division 2 standings
    d2_standings = SeasonProgressionEngine.get_division_standings(season_id=98, division_id=2)
    assert not any(p["user_id"] == uid for p in d2_standings), "Division 1 user must not appear in Division 2!"


def test_03_season_1_historical_rankings_isolated_from_season_2():
    """Verify a user ranked #1 in Season 1 starts unranked or fresh in Season 2."""
    uid = 9620
    # Season 1
    database.update_season_player_stats(
        user_id=uid, season_id=98, division_id=1, rating=1900.0, season_points=800.0, settled_bets=20
    )
    # Season 2
    database.update_season_player_stats(
        user_id=uid, season_id=99, division_id=1, rating=1200.0, season_points=0.0, settled_bets=0
    )

    s1_lb = LeaderboardService.get_leaderboard(season_id=98, division_id=1, scope="DIVISION", metric="RATING")
    s2_lb = LeaderboardService.get_leaderboard(season_id=99, division_id=1, scope="DIVISION", metric="RATING")

    s1_entry = next(e for e in s1_lb["entries"] if e["player_id"] == uid)
    s2_entry = next(e for e in s2_lb["entries"] if e["player_id"] == uid)

    assert s1_entry["rating"] == 1900.0
    assert s1_entry["season_points"] == 800.0
    assert s1_entry["is_qualified"] is True

    assert s2_entry["rating"] == 1200.0
    assert s2_entry["season_points"] == 0.0
    assert s2_entry["is_qualified"] is False  # 0 bets in Season 2!


def test_04_snapshot_division_queries():
    """Verify get_season_snapshots returns division-specific snapshots or all divisions."""
    database.create_season_snapshot(
        season_id=98, division_id=1, user_id=9631, final_rank=1, final_rating=1800.0,
        season_points=300.0, wins=5, losses=1, voids=0, settled_bets=6, win_rate=83.3,
        roi=20.0, total_stake=600, total_payout=720, best_streak=4, promotion_status="PROMOTED"
    )
    database.create_season_snapshot(
        season_id=98, division_id=2, user_id=9632, final_rank=1, final_rating=1750.0,
        season_points=280.0, wins=5, losses=1, voids=0, settled_bets=6, win_rate=83.3,
        roi=15.0, total_stake=600, total_payout=690, best_streak=3, promotion_status="PROMOTED"
    )

    div1_snaps = database.get_season_snapshots(season_id=98, division_id=1)
    div2_snaps = database.get_season_snapshots(season_id=98, division_id=2)
    all_snaps = database.get_season_snapshots(season_id=98)

    assert len(div1_snaps) == 1
    assert div1_snaps[0]["user_id"] == 9631

    assert len(div2_snaps) == 1
    assert div2_snaps[0]["user_id"] == 9632

    assert len(all_snaps) == 2


def test_05_division_rules_isolation():
    """Verify rules set for Division 1 in Season 98 do not overwrite Division 1 in Season 99."""
    database.set_season_rules(season_id=98, division_id=1, promotion_slots=4, relegation_slots=2)
    database.set_season_rules(season_id=99, division_id=1, promotion_slots=1, relegation_slots=5)

    r98 = database.get_season_rules(season_id=98, division_id=1)
    r99 = database.get_season_rules(season_id=99, division_id=1)

    assert r98["promotion_slots"] == 4
    assert r98["relegation_slots"] == 2

    assert r99["promotion_slots"] == 1
    assert r99["relegation_slots"] == 5
