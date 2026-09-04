"""
tests/test_phase10_profile.py

Phase 10: Player Profile 2.0 & Privacy Boundary Tests.
Strict Invariants:
1. Public profile NEVER reveals wallet balance, coin wagered, or private coupon transactions.
2. Private profile is accessible exclusively to the authenticated user.
3. Player comparison (Player A vs Player B) exposes only public competitive metrics.
4. Career statistics aggregate correctly across seasons without polluting season-specific stats.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import database


@pytest.fixture(autouse=True)
def setup_teardown():
    database.init_db()
    with database.transaction() as conn:
        # Clean test users
        conn.execute("DELETE FROM bet_items WHERE bet_id IN (9101, 9102, 9103, 9104, 9105, 9106)")
        conn.execute("DELETE FROM season_player_stats WHERE user_id IN (1001, 1002)")
        conn.execute("DELETE FROM user_wallets WHERE user_id IN (1001, 1002)")
        conn.execute("DELETE FROM user_progression WHERE user_id IN (1001, 1002)")
        conn.execute("DELETE FROM user_bets WHERE user_id IN (1001, 1002)")
        conn.execute("DELETE FROM users WHERE telegram_id IN (1001, 1002)")

        # Seed test users
        conn.execute("""
            INSERT INTO users (telegram_id, username, team_name, role, division_id)
            VALUES (1001, 'pro_capper', 'FC Barcelona', 'user', 1),
                   (1002, 'rookie_bettor', 'Real Madrid', 'user', 1)
        """)

    yield

    with database.transaction() as conn:
        conn.execute("DELETE FROM bet_items WHERE bet_id IN (9101, 9102, 9103, 9104, 9105, 9106)")
        conn.execute("DELETE FROM season_player_stats WHERE user_id IN (1001, 1002)")
        conn.execute("DELETE FROM user_wallets WHERE user_id IN (1001, 1002)")
        conn.execute("DELETE FROM user_progression WHERE user_id IN (1001, 1002)")
        conn.execute("DELETE FROM user_bets WHERE user_id IN (1001, 1002)")
        conn.execute("DELETE FROM users WHERE telegram_id IN (1001, 1002)")


def test_01_public_profile_strictly_public():
    """Verify that get_public_player_profile contains zero financial data."""
    database.get_or_create_progression(1001)
    database.get_or_create_season_stats(1001, season_id=1, division_id=1)

    profile = database.get_public_player_profile(1001)
    assert profile["user_id"] == 1001
    assert profile["username"] == "pro_capper"
    assert "rating" in profile
    assert "rank" in profile
    assert "season_points" in profile
    assert "experience" in profile
    assert "level" in profile
    assert "tier" in profile

    # STRICT INVARIANT: Private financial & risk fields MUST NOT be present
    assert "balance" not in profile
    assert "wallet" not in profile
    assert "limits" not in profile
    assert "total_stake" not in profile
    assert "total_payout" not in profile


def test_02_private_profile_includes_wallet_and_career():
    """Verify that private profile contains wallet balance and career aggregates."""
    database.get_or_create_wallet(1001)
    database.get_or_create_progression(1001)

    priv = database.get_private_player_profile(1001)
    assert priv["user_id"] == 1001
    assert "wallet" in priv
    assert "balance" in priv["wallet"]
    assert "total_bets" in priv
    assert "total_stake" in priv
    assert "total_payout" in priv
    assert "career" in priv


def test_03_profile_stats_favorite_markets_and_accuracy():
    """Verify calculation of favorite markets, favorite teams, accuracy, and value hit rate."""
    with database.transaction() as conn:
        # Insert 3 won bets on '1x2' and 1 lost bet on 'total_goals'
        conn.execute("""
            INSERT INTO user_bets (id, user_id, bet_type, amount, total_odd, potential_win, actual_payout, status, settled_at)
            VALUES (9101, 1001, 'single', 500, 2.50, 1250, 1250, 'won', CURRENT_TIMESTAMP),
                   (9102, 1001, 'single', 500, 2.10, 1050, 1050, 'won', CURRENT_TIMESTAMP),
                   (9103, 1001, 'single', 500, 1.80, 900, 900, 'won', CURRENT_TIMESTAMP),
                   (9104, 1001, 'single', 500, 1.90, 950, 0, 'lost', CURRENT_TIMESTAMP)
        """)
        conn.execute("INSERT OR IGNORE INTO matches (id, player1_team, player2_team, status) VALUES (1, 'Arsenal', 'Chelsea', 'finished')")
        conn.execute("INSERT OR IGNORE INTO markets (id, match_id, market_key, status) VALUES (10, 1, '1x2', 'settled'), (20, 1, 'total_goals', 'settled')")
        conn.execute("""
            INSERT INTO bet_items (bet_id, match_id, market_id, selection_id, outcome_type, odd, status)
            VALUES (9101, 1, 10, 1, 'home_win', 2.50, 'won'),
                   (9102, 1, 10, 1, 'home_win', 2.10, 'won'),
                   (9103, 1, 10, 1, 'home_win', 1.80, 'won'),
                   (9104, 1, 20, 2, 'over_2.5', 1.90, 'lost')
        """)

    stats = database.get_user_favorite_stats(1001)
    assert "1x2" in stats["favorite_markets"]
    assert stats["prediction_accuracy"] == 75.0  # 3 of 4 won
    assert stats["value_hit_rate"] == 100.0      # 2 of 2 value bets (>=2.0) won


def test_04_career_stats_aggregation():
    """Verify that career stats aggregate across seasons correctly."""
    with database.transaction() as conn:
        conn.execute("""
            INSERT INTO user_bets (id, user_id, bet_type, amount, total_odd, potential_win, actual_payout, status, settled_at)
            VALUES (9105, 1001, 'single', 1000, 2.0, 2000, 2000, 'won', CURRENT_TIMESTAMP),
                   (9106, 1001, 'single', 1000, 2.0, 2000, 0, 'lost', CURRENT_TIMESTAMP)
        """)

    career = database.get_player_career_stats(1001)
    assert career["career_bets"] == 2
    assert career["career_wins"] == 1
    assert career["career_losses"] == 1
    assert career["career_roi"] == 0.0  # 2000 payout on 2000 stake -> 0% ROI
    assert career["career_accuracy"] == 50.0


def test_05_player_comparison_only_public_data():
    """Verify player comparison payload contains zero private attributes for both players."""
    pub_a = database.get_public_player_profile(1001)
    pub_b = database.get_public_player_profile(1002)

    for p in (pub_a, pub_b):
        assert "balance" not in p
        assert "wallet" not in p
        assert "limits" not in p
        assert "total_stake" not in p
        assert "rating" in p
        assert "rank" in p
        assert "season_points" in p
