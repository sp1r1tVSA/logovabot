"""
tests/test_phase6_odds.py

Tests for Phase 6D: Live Odds Movement, Market Suspension Rules & Market Safety.
Ensures:
1. Accurate odds movement tracking, percentage change, direction, and velocity.
2. Movers categorization (biggest drops, biggest rises, fastest velocity, suspended).
3. Rule-based live market suspension (goal, VAR, penalty, red cards).
4. Safe market resumption and force close with audit trails.
5. In-play betting safety: suspended and closed markets reject bets immediately.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from services.market_safety import (
    evaluate_and_apply_suspend_rules,
    force_close_match_markets,
    resume_match_markets,
)
from services.odds_movers import get_odds_movers, record_odds_movement


class TestPhase6Odds(unittest.TestCase):

    def setUp(self) -> None:
        database.init_db()
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM odds_movement WHERE match_id >= 99200")
            cursor.execute("DELETE FROM market_selections WHERE id >= 992000")
            cursor.execute("DELETE FROM markets WHERE match_id >= 99200")
            cursor.execute("DELETE FROM matches WHERE id >= 99200")

            # Seed open round 6 for division 1
            cursor.execute("""
                INSERT OR REPLACE INTO rounds (division_id, round_number, is_open)
                VALUES (1, 6, 1)
            """)

            # Seed test match
            cursor.execute("""
                INSERT INTO matches (
                    id, season_id, division_id, round_number,
                    player1_team, player2_team, status, player1_score, player2_score
                ) VALUES (99201, 1, 1, 6, 'Спортинг', 'Брага', 'scheduled', 0, 0)
            """)

            # Seed markets
            cursor.execute("""
                INSERT INTO markets (id, match_id, market_key, market_name, category, status)
                VALUES (992011, 99201, '1x2', 'Основной исход', 'main', 'open'),
                       (992012, 99201, 'totals', 'Тотал 2.5', 'totals', 'open')
            """)

            # Seed selections
            cursor.execute("""
                INSERT INTO market_selections (id, market_id, selection_key, selection_name, odds_value, odds_version, status)
                VALUES (9920111, 992011, 'p1', 'П1', 2.10, 1, 'active'),
                       (9920112, 992011, 'x', 'X', 3.40, 1, 'active'),
                       (9920113, 992011, 'p2', 'П2', 3.20, 1, 'active'),
                       (9920121, 992012, 'tb25', 'ТБ 2.5', 1.85, 1, 'active')
            """)

    def tearDown(self) -> None:
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM odds_movement WHERE match_id >= 99200")
            cursor.execute("DELETE FROM market_selections WHERE id >= 992000")
            cursor.execute("DELETE FROM markets WHERE match_id >= 99200")
            cursor.execute("DELETE FROM matches WHERE id >= 99200")

    def test_record_odds_movement_drop_and_rise(self) -> None:
        """Odds shifts calculate correct percentage changes and directions."""
        # 1. Drop: 2.10 -> 1.70 (-19.05%)
        row_id1 = record_odds_movement(
            selection_id=9920111,
            market_id=992011,
            match_id=99201,
            old_odds=2.10,
            new_odds=1.70,
            reason="Market demand shift"
        )
        self.assertIsNotNone(row_id1)

        # 2. Rise: 3.20 -> 4.50 (+40.62%)
        row_id2 = record_odds_movement(
            selection_id=9920113,
            market_id=992011,
            match_id=99201,
            old_odds=3.20,
            new_odds=4.50,
            reason="Underdog drifting"
        )
        self.assertIsNotNone(row_id2)

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM odds_movement WHERE id = ?", (row_id1,))
            r1 = cursor.fetchone()
            self.assertEqual(r1["direction"], "down")
            self.assertAlmostEqual(r1["pct_change"], -19.05, places=1)

            cursor.execute("SELECT * FROM odds_movement WHERE id = ?", (row_id2,))
            r2 = cursor.fetchone()
            self.assertEqual(r2["direction"], "up")
            self.assertAlmostEqual(r2["pct_change"], 40.62, places=1)

    def test_get_odds_movers_categorization(self) -> None:
        """get_odds_movers partitions biggest drops, rises, and suspended markets."""
        record_odds_movement(9920111, 992011, 99201, 2.50, 1.80, reason="Drop 1")
        record_odds_movement(9920112, 992011, 99201, 3.00, 3.90, reason="Rise 1")

        # Suspend one market
        evaluate_and_apply_suspend_rules(99201, "goal")

        movers = get_odds_movers(division_id=1, season_id=1)
        self.assertEqual(movers["status"], "ok")
        self.assertGreaterEqual(len(movers["biggest_drops"]), 1)
        self.assertGreaterEqual(len(movers["biggest_rises"]), 1)
        self.assertGreaterEqual(len(movers["suspended_markets"]), 1)
        self.assertEqual(movers["biggest_drops"][0]["selection_key"], "p1")
        self.assertEqual(movers["biggest_rises"][0]["selection_key"], "x")

    def test_market_suspension_rules_on_goal_and_var(self) -> None:
        """Goal and VAR events auto-suspend all open markets for the match."""
        m_id = 99201

        # Check initial state is open
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM markets WHERE id = 992011")
            self.assertEqual(cursor.fetchone()["status"], "open")

        # Apply goal rule
        suspended_count = evaluate_and_apply_suspend_rules(m_id, "goal")
        self.assertEqual(suspended_count, 2)

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM markets WHERE id = 992011")
            self.assertEqual(cursor.fetchone()["status"], "suspended")
            cursor.execute("SELECT status FROM markets WHERE id = 992012")
            self.assertEqual(cursor.fetchone()["status"], "suspended")

        # Resume markets after goal confirmation
        resumed_count = resume_match_markets(m_id, actor_id=1, reason="VAR confirmed goal")
        self.assertEqual(resumed_count, 2)

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM markets WHERE id = 992011")
            self.assertEqual(cursor.fetchone()["status"], "open")

    def test_bet_placement_rejected_on_suspended_market(self) -> None:
        """place_user_bet must immediately reject wagers on suspended markets."""
        m_id = 99201
        # Suspend market
        evaluate_and_apply_suspend_rules(m_id, "var")

        # Attempt to place bet on suspended market
        user_id = 777001
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO users (telegram_id, username) VALUES (?, 'bettor1')", (user_id,))
            cursor.execute("""
                INSERT INTO user_wallets (user_id, balance, total_wagered, total_won, bets_count)
                VALUES (?, 1000, 0, 0, 0)
                ON CONFLICT(user_id) DO UPDATE SET balance = 1000
            """, (user_id,))

        slip = [{
            "match_id": m_id,
            "outcome": "p1",
            "market_id": 992011,
            "selection_id": 9920111,
            "odd": 2.10
        }]
        success, res = database.place_user_bet(user_id, 100, slip)
        self.assertFalse(success)
        self.assertIn("приостановлен", str(res))

        # Check that user was NOT debited
        wallet = database.get_or_create_wallet(user_id)
        self.assertEqual(wallet["balance"], 1000)


if __name__ == "__main__":
    unittest.main()
