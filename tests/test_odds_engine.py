"""
tests/test_odds_engine.py
Unit tests for LOGOVO.BET v2.0 Odds Engine & Market Management.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import services.odds_engine as odds_engine


class TestOddsEngine(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self.match_id = 999101
        self.admin_id = 999199
        with database.transaction() as conn:
            conn.execute("DELETE FROM admin_audit_log WHERE admin_id = ?", (self.admin_id,))
            conn.execute("DELETE FROM odds_history WHERE changed_by = ?", (self.admin_id,))
            conn.execute("DELETE FROM markets WHERE match_id = ?", (self.match_id,))
            conn.execute("DELETE FROM matches WHERE id = ?", (self.match_id,))
            conn.execute("DELETE FROM users WHERE telegram_id = ?", (self.admin_id,))
            
            # Create a test admin user for audit log foreign keys
            conn.execute("""
                INSERT INTO users (telegram_id, username, role)
                VALUES (?, 'test_admin', 'admin')
            """, (self.admin_id,))

            # Create a valid fixture in matches table for foreign key constraint
            conn.execute("""
                INSERT INTO matches (id, tournament_id, round_number, player1_team, player2_team, status)
                VALUES (?, 1, 1, 'Реал Мадрид', 'Барселона', 'scheduled')
            """, (self.match_id,))

    def tearDown(self):
        with database.transaction() as conn:
            conn.execute("DELETE FROM admin_audit_log WHERE admin_id = ?", (self.admin_id,))
            conn.execute("DELETE FROM odds_history WHERE changed_by = ?", (self.admin_id,))
            conn.execute("DELETE FROM markets WHERE match_id = ?", (self.match_id,))
            conn.execute("DELETE FROM matches WHERE id = ?", (self.match_id,))
            conn.execute("DELETE FROM users WHERE telegram_id = ?", (self.admin_id,))

    def test_market_and_selection_lifecycle(self):
        # 1. Create market
        m = odds_engine.get_or_create_market(self.match_id, "1x2", "Исход матча", category="main")
        self.assertEqual(m["market_key"], "1x2")
        self.assertEqual(m["status"], "open")

        # 2. Add selections
        s_p1 = odds_engine.get_or_create_selection(m["id"], "p1", "П1", 2.15)
        s_x = odds_engine.get_or_create_selection(m["id"], "x", "X", 3.20)
        s_p2 = odds_engine.get_or_create_selection(m["id"], "p2", "П2", 2.90)

        self.assertEqual(s_p1["odds_value"], 2.15)
        self.assertEqual(s_p1["odds_version"], 1)
        self.assertEqual(s_p1["status"], "active")

        # 3. Read current odds
        curr_p1 = odds_engine.get_current_odds(m["id"], "p1")
        self.assertEqual(curr_p1, 2.15)

    def test_set_odds_and_history_audit(self):
        m = odds_engine.get_or_create_market(self.match_id, "1x2", "Исход матча")
        odds_engine.get_or_create_selection(m["id"], "p1", "П1", 2.10)

        # Update odds to 2.35 with valid admin_id
        updated = odds_engine.set_odds(m["id"], "p1", 2.35, admin_id=self.admin_id, reason="Market rebalance")
        self.assertEqual(updated["odds_value"], 2.35)
        self.assertEqual(updated["previous_odds"], 2.10)
        self.assertEqual(updated["odds_version"], 2)

        # Check history audit trail
        history = odds_engine.get_odds_history(market_id=m["id"], selection_key="p1")
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0]["old_value"], 2.10)
        self.assertEqual(history[0]["new_value"], 2.35)
        self.assertEqual(history[0]["reason"], "Market rebalance")

    def test_suspend_and_unsuspend_market(self):
        m = odds_engine.get_or_create_market(self.match_id, "btts", "Обе забьют")
        odds_engine.get_or_create_selection(m["id"], "btts_yes", "Да", 1.70)

        # Suspend
        res = odds_engine.suspend_market(m["id"], admin_id=self.admin_id, reason="VAR Check")
        self.assertTrue(res)

        # Validate odds should raise ValueError because suspended
        with self.assertRaises(ValueError) as ctx:
            odds_engine.validate_odds(m["id"], "btts_yes")
        self.assertIn("Рынок недоступен", str(ctx.exception))

        # Unsuspend
        res = odds_engine.unsuspend_market(m["id"], admin_id=self.admin_id)
        self.assertTrue(res)

        # Validate odds should now succeed
        odd = odds_engine.validate_odds(m["id"], "btts_yes")
        self.assertEqual(odd, 1.70)

    def test_lock_and_unlock_selection(self):
        m = odds_engine.get_or_create_market(self.match_id, "total_goals", "Тотал")
        sel = odds_engine.get_or_create_selection(m["id"], "over_2.5", "ТБ 2.5", 1.85)

        # Lock selection
        odds_engine.lock_selection(sel["id"])
        with self.assertRaises(ValueError) as ctx:
            odds_engine.validate_odds(m["id"], "over_2.5")
        self.assertIn("Исход заблокирован", str(ctx.exception))

        # Unlock
        odds_engine.unlock_selection(sel["id"])
        odd = odds_engine.validate_odds(m["id"], "over_2.5")
        self.assertEqual(odd, 1.85)

    def test_odds_drift_validation(self):
        m = odds_engine.get_or_create_market(self.match_id, "1x2", "Исход")
        odds_engine.get_or_create_selection(m["id"], "p1", "П1", 2.50)

        # No expected odd -> returns current
        self.assertEqual(odds_engine.validate_odds(m["id"], "p1"), 2.50)

        # Small drift (within 0.05) -> passes
        self.assertEqual(odds_engine.validate_odds(m["id"], "p1", expected_odd=2.48, max_drift=0.05), 2.50)

        # Large drift -> fails
        with self.assertRaises(ValueError) as ctx:
            odds_engine.validate_odds(m["id"], "p1", expected_odd=2.10, max_drift=0.05)
        self.assertIn("Коэффициент изменился", str(ctx.exception))

    def test_generate_standard_match_markets(self):
        markets = odds_engine.generate_match_markets(self.match_id, "Реал Мадрид", "Барселона")
        self.assertGreaterEqual(len(markets), 7)

        market_keys = [m["market_key"] for m in markets]
        self.assertIn("1x2", market_keys)
        self.assertIn("double_chance", market_keys)
        self.assertIn("total_goals", market_keys)
        self.assertIn("btts", market_keys)
        self.assertIn("individual_total_1", market_keys)
        self.assertIn("individual_total_2", market_keys)
        self.assertIn("handicap", market_keys)

        # Check total goals has all 6 lines (over/under 1.5, 2.5, 3.5)
        tot_m = next(m for m in markets if m["market_key"] == "total_goals")
        self.assertEqual(len(tot_m["selections"]), 6)


if __name__ == "__main__":
    unittest.main()
