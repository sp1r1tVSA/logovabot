"""
tests/test_phase10_rewards.py

Phase 10: Season Reward Ledger & Financial Wallet Safety Tests.
Strict Invariants:
1. Reward Ledger is uniquely constrained by (user_id, season_id, reward_id).
2. Financial rewards route strictly through Wallet + coin_transactions (transaction_type='season_reward').
3. No direct balance mutations from gamification or progression services.
4. Season finalization is idempotent: second run returns False and awards zero duplicate coins.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import database
from services.season_progression import SeasonProgressionEngine


@pytest.fixture(autouse=True)
def clean_reward_data():
    database.init_db()
    with database.transaction() as conn:
        conn.execute("DELETE FROM season_reward_ledger WHERE user_id = 6001 OR season_id = 96")
        conn.execute("DELETE FROM season_snapshots WHERE season_id = 96")
        conn.execute("DELETE FROM season_player_stats WHERE user_id = 6001 OR season_id = 96")
        conn.execute("DELETE FROM seasons WHERE id = 96")
        conn.execute("DELETE FROM coin_transactions WHERE user_id = 6001")
        conn.execute("DELETE FROM user_wallets WHERE user_id = 6001")
        conn.execute("DELETE FROM users WHERE telegram_id = 6001")
        conn.execute("INSERT OR IGNORE INTO users (telegram_id, username, role, division_id) VALUES (6001, 'user_6001', 'user', 1)")
    yield
    with database.transaction() as conn:
        conn.execute("DELETE FROM season_reward_ledger WHERE user_id = 6001 OR season_id = 96")
        conn.execute("DELETE FROM season_snapshots WHERE season_id = 96")
        conn.execute("DELETE FROM season_player_stats WHERE user_id = 6001 OR season_id = 96")
        conn.execute("DELETE FROM seasons WHERE id = 96")
        conn.execute("DELETE FROM coin_transactions WHERE user_id = 6001")
        conn.execute("DELETE FROM user_wallets WHERE user_id = 6001")
        conn.execute("DELETE FROM users WHERE telegram_id = 6001")


def test_01_season_reward_ledger_idempotency():
    """Verify recording a reward in the ledger is strictly idempotent."""
    first = database.record_season_reward_in_ledger(
        season_id=96, division_id=1, user_id=6001, reward_id="REW_CHAMPION",
        reward_type="coins", coins_awarded=10000
    )
    assert first is True

    # Second attempt must return False
    second = database.record_season_reward_in_ledger(
        season_id=96, division_id=1, user_id=6001, reward_id="REW_CHAMPION",
        reward_type="coins", coins_awarded=10000
    )
    assert second is False


def test_02_wallet_integration_via_coin_transactions():
    """Verify season rewards are credited via coin_transactions with transaction_type='season_reward'."""
    database.get_or_create_wallet(6001)
    w_before = database.get_or_create_wallet(6001)["balance"]

    database.add_coins(6001, 5000, tx_type="season_reward", ref_id="season_96_REW_TOP_3")
    w_after = database.get_or_create_wallet(6001)["balance"]

    assert w_after == w_before + 5000

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM coin_transactions
            WHERE user_id = 6001 AND transaction_type = 'season_reward'
        """)
        tx = cursor.fetchone()
        assert tx is not None
        assert tx["amount"] == 5000
        assert tx["reference_id"] == "season_96_REW_TOP_3"


def test_03_season_finalization_awards_rewards_once():
    """Verify finalize_season credits rewards and creates snapshots."""
    with database.transaction() as conn:
        conn.execute("INSERT INTO seasons (id, name, status) VALUES (96, 'Season 96 Active', 'active')")

    database.get_or_create_wallet(6001)
    w_initial = database.get_or_create_wallet(6001)["balance"]

    # Seed 6001 as rank 1 in division 1
    database.update_season_player_stats(
        user_id=6001, season_id=96, division_id=1,
        rating=1800.0, settled_bets=10, season_points=300.0, status="ACTIVE"
    )

    success, msg, res = SeasonProgressionEngine.finalize_season(season_id=96)
    assert success is True
    assert res["snapshots_created"] >= 1
    assert res["rewards_distributed"] >= 1

    w_final = database.get_or_create_wallet(6001)["balance"]
    assert w_final > w_initial, "Champion must have received coin reward!"

    # Second finalization attempt must be rejected
    success2, msg2, res2 = SeasonProgressionEngine.finalize_season(season_id=96)
    assert success2 is False
    assert "не активен" in msg2 or "уже зафиксирован" in msg2

    w_after_second = database.get_or_create_wallet(6001)["balance"]
    assert w_after_second == w_final, "Balance must NOT increase on duplicate finalization!"


def test_04_season_reward_catalog_listing():
    """Verify catalog contains standard rewards (champion, top 3, top 10, promotion)."""
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM season_rewards_catalog")
        rewards = {r["id"]: dict(r) for r in cursor.fetchall()}

    assert "REW_CHAMPION" in rewards
    assert "REW_TOP_3" in rewards
    assert "REW_TOP_10" in rewards
    assert "REW_PROMOTION" in rewards
    assert rewards["REW_CHAMPION"]["reward_type"] == "coins"


def test_05_reward_ledger_filtering_by_season():
    """Verify get_user_season_rewards correctly filters by season_id."""
    database.record_season_reward_in_ledger(season_id=1, division_id=1, user_id=6001, reward_id="R1", reward_type="xp", xp_awarded=100)
    database.record_season_reward_in_ledger(season_id=2, division_id=1, user_id=6001, reward_id="R2", reward_type="xp", xp_awarded=200)

    s1_rewards = database.get_user_season_rewards(6001, season_id=1)
    s2_rewards = database.get_user_season_rewards(6001, season_id=2)

    assert len(s1_rewards) == 1
    assert s1_rewards[0]["reward_id"] == "R1"

    assert len(s2_rewards) == 1
    assert s2_rewards[0]["reward_id"] == "R2"
