"""
tests/test_phase10_seasons.py

Phase 10: Season Lifecycle & Career vs Seasonal Data Isolation Tests.
Strict Invariants:
1. Seasons follow state machine: DRAFT -> ACTIVE -> FINISHED -> ARCHIVED.
2. Season reset isolates seasonal stats while strictly preserving career statistics.
3. Historical season snapshots are immutable.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import database


@pytest.fixture(autouse=True)
def clean_seasons():
    database.init_db()
    with database.transaction() as conn:
        # Clean test seasons and users
        conn.execute("DELETE FROM season_snapshots WHERE season_id >= 90")
        conn.execute("DELETE FROM season_player_stats WHERE season_id >= 90 OR user_id IN (4001, 4002, 4003)")
        conn.execute("DELETE FROM user_bets WHERE user_id IN (4001, 4002, 4003)")
        conn.execute("DELETE FROM user_wallets WHERE user_id IN (4001, 4002, 4003)")
        conn.execute("DELETE FROM user_progression WHERE user_id IN (4001, 4002, 4003)")
        conn.execute("DELETE FROM users WHERE telegram_id IN (4001, 4002, 4003)")
        conn.execute("DELETE FROM seasons WHERE id >= 90")
        conn.execute("UPDATE seasons SET status = 'active' WHERE id = 1")

        # Seed test users and seasons
        conn.execute("INSERT OR IGNORE INTO users (telegram_id, username, role, division_id) VALUES (4001, 'user_4001', 'user', 1)")
        conn.execute("INSERT OR IGNORE INTO users (telegram_id, username, role, division_id) VALUES (4002, 'user_4002', 'user', 1)")
        conn.execute("INSERT OR IGNORE INTO users (telegram_id, username, role, division_id) VALUES (4003, 'user_4003', 'user', 1)")
        conn.execute("INSERT OR IGNORE INTO seasons (id, name, status) VALUES (93, 'Season 93', 'finished')")

    yield

    with database.transaction() as conn:
        conn.execute("DELETE FROM season_snapshots WHERE season_id >= 90")
        conn.execute("DELETE FROM season_player_stats WHERE season_id >= 90 OR user_id IN (4001, 4002, 4003)")
        conn.execute("DELETE FROM user_bets WHERE user_id IN (4001, 4002, 4003)")
        conn.execute("DELETE FROM user_wallets WHERE user_id IN (4001, 4002, 4003)")
        conn.execute("DELETE FROM user_progression WHERE user_id IN (4001, 4002, 4003)")
        conn.execute("DELETE FROM users WHERE telegram_id IN (4001, 4002, 4003)")
        conn.execute("DELETE FROM seasons WHERE id >= 90")
        conn.execute("UPDATE seasons SET status = 'active' WHERE id = 1")


def test_01_season_state_transitions():
    """Verify valid lifecycle: create(draft) -> activate(active) -> finish(finished) -> archive(archived)."""
    with database.transaction() as conn:
        conn.execute("INSERT INTO seasons (id, name, status) VALUES (91, 'Season 91', 'draft')")

    ok, msg = database.activate_season(91)
    assert ok is True
    s = database.get_season(91)
    assert s["status"] == "active"
    assert s["started_at"] is not None

    ok, msg = database.finish_season(91)
    assert ok is True
    s = database.get_season(91)
    assert s["status"] == "finished"
    assert s["finished_at"] is not None

    ok, msg = database.archive_season(91)
    assert ok is True
    s = database.get_season(91)
    assert s["status"] == "archived"


def test_02_cannot_activate_finished_or_archived_season():
    """Verify cannot reactivate an already completed or archived season."""
    with database.transaction() as conn:
        conn.execute("INSERT INTO seasons (id, name, status) VALUES (92, 'Season 92', 'finished')")

    ok, msg = database.activate_season(92)
    assert ok is False
    assert "Нельзя напрямую активировать" in msg


def test_03_career_stats_preserved_across_new_season():
    """Verify career stats persist when transitioning from Season 1 to Season 2."""
    test_uid = 4001
    with database.transaction() as conn:
        conn.execute("DELETE FROM user_bets WHERE user_id = ?", (test_uid,))
        conn.execute("DELETE FROM season_player_stats WHERE user_id = ?", (test_uid,))

        # User places winning bet in Season 1
        conn.execute("""
            INSERT INTO user_bets (id, user_id, bet_type, amount, total_odd, potential_win, actual_payout, status, settled_at)
            VALUES (9401, ?, 'single', 500, 2.0, 1000, 1000, 'won', CURRENT_TIMESTAMP)
        """, (test_uid,))
        conn.execute("""
            INSERT INTO season_player_stats (user_id, season_id, division_id, rating, wins, settled_bets, total_stake, total_payout)
            VALUES (?, 1, 1, 1300.0, 1, 1, 500, 1000)
        """, (test_uid,))

    career_s1 = database.get_player_career_stats(test_uid)
    assert career_s1["career_wins"] == 1

    # Season 2 begins: fresh season_player_stats initialized
    s2_stats = database.get_or_create_season_stats(test_uid, season_id=2, division_id=1)
    assert s2_stats["season_id"] == 2
    assert s2_stats["wins"] == 0
    assert s2_stats["settled_bets"] == 0

    # Career stats still reflect cumulative history!
    career_s2 = database.get_player_career_stats(test_uid)
    assert career_s2["career_wins"] == 1
    assert career_s2["career_bets"] == 1


def test_04_season_specific_stats_isolated():
    """Verify Season 1 player stats and Season 2 player stats are completely distinct rows."""
    test_uid = 4002
    database.update_season_player_stats(test_uid, season_id=1, division_id=1, rating=1450.0, season_points=120.0)
    database.update_season_player_stats(test_uid, season_id=2, division_id=1, rating=1200.0, season_points=0.0)

    s1 = database.get_player_season_stats(test_uid, season_id=1, division_id=1)
    s2 = database.get_player_season_stats(test_uid, season_id=2, division_id=1)

    assert s1["rating"] == 1450.0
    assert s1["season_points"] == 120.0
    assert s2["rating"] == 1200.0
    assert s2["season_points"] == 0.0


def test_05_season_snapshot_creation_and_immutability():
    """Verify historical season snapshots can be created and are uniquely constrained."""
    snap_id = database.create_season_snapshot(
        season_id=93, division_id=1, user_id=4003, final_rank=1, final_rating=1850.0,
        season_points=400.0, wins=10, losses=2, voids=0, settled_bets=12, win_rate=83.3,
        roi=45.5, total_stake=6000, total_payout=8730, best_streak=6, promotion_status="PROMOTED"
    )
    assert snap_id > 0

    snaps = database.get_season_snapshots(season_id=93, division_id=1)
    assert len(snaps) == 1
    assert snaps[0]["final_rank"] == 1
    assert snaps[0]["promotion_status"] == "PROMOTED"

    # Duplicate insertion must be ignored (DO NOTHING)
    dup_id = database.create_season_snapshot(
        season_id=93, division_id=1, user_id=4003, final_rank=2, final_rating=1500.0,
        season_points=100.0, wins=1, losses=5, voids=0, settled_bets=6, win_rate=16.6,
        roi=-50.0, total_stake=1000, total_payout=500, best_streak=1, promotion_status="RELEGATED"
    )
    snaps_after = database.get_season_snapshots(season_id=93, division_id=1)
    assert len(snaps_after) == 1
    assert snaps_after[0]["final_rank"] == 1  # Unchanged!
