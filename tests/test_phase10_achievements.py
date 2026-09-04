"""
tests/test_phase10_achievements.py

Phase 10: Non-Duplicable Achievements & Catalog Tests.
Strict Invariants:
1. Achievements can be unlocked at most once per user.
2. Claiming achievement rewards cannot be executed twice.
3. Catalog contains all Phase 10 volume, skill, and parlay achievements.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import database


@pytest.fixture(autouse=True)
def clean_ach_data():
    database.init_db()
    with database.transaction() as conn:
        conn.execute("DELETE FROM user_achievements WHERE user_id = 7001")
        conn.execute("DELETE FROM coin_transactions WHERE user_id = 7001")
        conn.execute("DELETE FROM user_wallets WHERE user_id = 7001")
        conn.execute("DELETE FROM user_progression WHERE user_id = 7001")
        conn.execute("DELETE FROM users WHERE telegram_id = 7001")
        conn.execute("INSERT OR IGNORE INTO users (telegram_id, username, role, division_id) VALUES (7001, 'user_7001', 'user', 1)")
    yield
    with database.transaction() as conn:
        conn.execute("DELETE FROM user_achievements WHERE user_id = 7001")
        conn.execute("DELETE FROM coin_transactions WHERE user_id = 7001")
        conn.execute("DELETE FROM user_wallets WHERE user_id = 7001")
        conn.execute("DELETE FROM user_progression WHERE user_id = 7001")
        conn.execute("DELETE FROM users WHERE telegram_id = 7001")


def test_01_unlock_achievement_idempotency():
    """Verify an achievement can be unlocked only once."""
    first = database.unlock_achievement(7001, "ACH_FIRST_BET")
    assert first is True

    # Second unlock attempt must return False
    second = database.unlock_achievement(7001, "ACH_FIRST_BET")
    assert second is False


def test_02_claim_achievement_reward_cannot_claim_twice():
    """Verify claiming an achievement credits coins and XP, but second claim is blocked."""
    database.unlock_achievement(7001, "ACH_FIRST_BET")
    database.get_or_create_wallet(7001)

    ok, msg, payload = database.claim_achievement_reward(7001, "ACH_FIRST_BET")
    assert ok is True
    assert payload["coins"] > 0
    assert payload["xp"] > 0

    # Second claim must be rejected
    ok2, msg2, payload2 = database.claim_achievement_reward(7001, "ACH_FIRST_BET")
    assert ok2 is False
    assert "уже получена" in msg2


def test_03_volume_achievements_evaluation():
    """Verify volume milestones trigger automatically upon reaching count thresholds."""
    database.get_or_create_wallet(7001)
    with database.transaction() as conn:
        conn.execute("UPDATE user_wallets SET bets_count = 10 WHERE user_id = 7001")

    database.evaluate_betting_achievements(7001)
    achs = database.get_user_achievements(7001)
    unlocked = {a["id"] for a in achs if a["is_unlocked"]}

    assert "ACH_FIRST_BET" in unlocked
    assert "ACH_TOTAL_10_BETS" in unlocked


def test_04_express_odd_achievements_evaluation():
    """Verify high-odds express trigger corresponding achievement."""
    payload = {"bet_type": "express", "total_odd": 15.5}
    database.evaluate_betting_achievements(7001, payload)

    achs = database.get_user_achievements(7001)
    unlocked = {a["id"] for a in achs if a["is_unlocked"]}

    assert "ACH_EXPRESS_3" in unlocked
    assert "ACH_EXPRESS_ODD_5" in unlocked
    assert "ACH_EXPRESS_ODD_15" in unlocked


def test_05_underdog_single_achievement():
    """Verify winning an underdog single (odd >= 3.5) unlocks ACH_UNDERDOG."""
    payload = {"bet_type": "single", "total_odd": 3.80}
    database.evaluate_betting_achievements(7001, payload)

    achs = database.get_user_achievements(7001)
    unlocked = {a["id"] for a in achs if a["is_unlocked"]}
    assert "ACH_UNDERDOG" in unlocked
