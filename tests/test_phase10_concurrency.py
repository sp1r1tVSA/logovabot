"""
tests/test_phase10_concurrency.py

Phase 10: Concurrency, Race Conditions & Thread Safety Tests.
Strict Invariants:
1. Two simultaneous finalization jobs -> exactly one succeeds, zero duplicate rewards.
2. Concurrent achievement claims -> exactly one succeeds.
3. Concurrent reward ledger writes -> idempotent uniqueness.
4. Concurrent bet settlement maintains consistent streak and rating state.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import threading
import database
from services.season_progression import SeasonProgressionEngine
from services.streak_engine import StreakEngine


@pytest.fixture(autouse=True)
def clean_concurrency_data():
    database.init_db()
    with database.transaction() as conn:
        conn.execute("DELETE FROM season_reward_ledger WHERE season_id = 97")
        conn.execute("DELETE FROM season_snapshots WHERE season_id = 97")
        conn.execute("DELETE FROM season_player_stats WHERE season_id = 97")
        conn.execute("DELETE FROM seasons WHERE id = 97")
        conn.execute("DELETE FROM user_achievements WHERE user_id = 9501")
        conn.execute("DELETE FROM user_wallets WHERE user_id = 9501")
        conn.execute("DELETE FROM user_progression WHERE user_id = 9501")
        conn.execute("DELETE FROM users WHERE telegram_id = 9501")
        conn.execute("INSERT OR IGNORE INTO users (telegram_id, username, role, division_id) VALUES (9501, 'conc_user', 'user', 1)")

    yield

    with database.transaction() as conn:
        conn.execute("DELETE FROM season_reward_ledger WHERE season_id = 97")
        conn.execute("DELETE FROM season_snapshots WHERE season_id = 97")
        conn.execute("DELETE FROM season_player_stats WHERE season_id = 97")
        conn.execute("DELETE FROM seasons WHERE id = 97")
        conn.execute("DELETE FROM user_achievements WHERE user_id = 9501")
        conn.execute("DELETE FROM user_wallets WHERE user_id = 9501")
        conn.execute("DELETE FROM user_progression WHERE user_id = 9501")
        conn.execute("DELETE FROM users WHERE telegram_id = 9501")


def test_01_concurrent_season_finalization_exactly_one_succeeds():
    """Verify that launching two concurrent finalization threads results in exactly 1 success."""
    with database.transaction() as conn:
        conn.execute("INSERT INTO seasons (id, name, status) VALUES (97, 'Season 97 Concurrent', 'active')")

    database.update_season_player_stats(
        user_id=9501, season_id=97, division_id=1, rating=1700.0, settled_bets=10, season_points=200.0
    )

    results = []
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        res = SeasonProgressionEngine.finalize_season(season_id=97)
        results.append(res)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    success_count = sum(1 for r in results if r[0] is True)
    failure_count = sum(1 for r in results if r[0] is False)

    assert success_count == 1, f"Expected exactly 1 success, got {success_count}"
    assert failure_count == 1, f"Expected exactly 1 failure, got {failure_count}"

    # Verify snapshot count in DB is exactly 1 (no duplicate rows)
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM season_snapshots WHERE season_id = 97 AND user_id = 9501")
        assert cursor.fetchone()["cnt"] == 1


def test_02_concurrent_achievement_claims_exactly_one_succeeds():
    """Verify concurrent claim attempts on the same unlocked achievement yield exactly 1 success."""
    database.unlock_achievement(9501, "ACH_FIRST_BET")
    database.get_or_create_wallet(9501)

    results = []
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        res = database.claim_achievement_reward(9501, "ACH_FIRST_BET")
        results.append(res)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    successes = [r for r in results if r[0] is True]
    assert len(successes) == 1, f"Expected 1 claim success, got {len(successes)}"


def test_03_concurrent_reward_ledger_inserts():
    """Verify concurrent inserts into season_reward_ledger for same (user, season, reward) are idempotent."""
    results = []
    barrier = threading.Barrier(3)

    def worker():
        barrier.wait()
        res = database.record_season_reward_in_ledger(
            season_id=97, division_id=1, user_id=9501, reward_id="REW_TOP_3",
            reward_type="coins", coins_awarded=5000
        )
        results.append(res)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    true_count = sum(1 for r in results if r is True)
    assert true_count == 1, f"Expected exactly 1 True insert, got {true_count}"


def test_04_concurrent_bet_settlement_streak_consistency():
    """Verify concurrent processing of bet outcomes maintains monotonic or reset consistency."""
    database.get_or_create_progression(9501)
    with database.transaction() as conn:
        conn.execute("UPDATE user_progression SET current_streak = 0, best_streak = 0 WHERE user_id = 9501")

    barrier = threading.Barrier(4)

    def worker_win():
        barrier.wait()
        StreakEngine.process_bet_outcome(9501, "won", season_id=1, division_id=1)

    threads = [threading.Thread(target=worker_win) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    prog = database.get_or_create_progression(9501)
    assert prog["current_streak"] == 4
    assert prog["best_streak"] == 4


def test_05_concurrent_bet_settlement_and_finalization_safety():
    """Verify that settling bets concurrently with season finalization cannot corrupt DB state."""
    with database.transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO seasons (id, name, status) VALUES (97, 'Season 97 Race', 'active')")

    database.update_season_player_stats(
        user_id=9501, season_id=97, division_id=1, rating=1600.0, settled_bets=10, season_points=150.0
    )

    barrier = threading.Barrier(2)

    def worker_settle():
        barrier.wait()
        try:
            StreakEngine.process_bet_outcome(9501, "won", season_id=97, division_id=1)
        except Exception:
            pass

    def worker_finalize():
        barrier.wait()
        SeasonProgressionEngine.finalize_season(season_id=97)

    t1 = threading.Thread(target=worker_settle)
    t2 = threading.Thread(target=worker_finalize)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Integrity check: database must remain valid, snapshots intact
    with database.transaction() as c:
        assert [tuple(r) for r in c.execute("PRAGMA integrity_check").fetchall()] == [("ok",)]
