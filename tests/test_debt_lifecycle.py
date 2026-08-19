import os
import sys
import tempfile
import sqlite3
import datetime
import unittest

# Ensure logovobot root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
import database


class TestDebtLifecycle(unittest.TestCase):
    def setUp(self):
        """Create a temporary isolated SQLite database for each test."""
        self.tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db_path = self.tf.name
        self.tf.close()

        self.orig_config_path = config.DB_PATH
        self.orig_database_path = database.DB_PATH
        self.orig_start_dt = config.DEBT_TRACKING_START_DATETIME

        config.DB_PATH = self.temp_db_path
        database.DB_PATH = self.temp_db_path
        config.DEBT_TRACKING_START_DATETIME = "20.08.2026 12:00"
        database.init_db()

    def tearDown(self):
        """Restore original paths and cleanup temp db."""
        config.DB_PATH = self.orig_config_path
        database.DB_PATH = self.orig_database_path
        config.DEBT_TRACKING_START_DATETIME = self.orig_start_dt
        try:
            os.remove(self.temp_db_path)
        except Exception:
            pass

    def test_debt_reminders_table_and_stages(self):
        """Test recording and checking debt lifecycle stages."""
        match_id = 999
        with database.transaction() as conn:
            conn.execute("INSERT INTO matches (id, round_number, status) VALUES (?, 1, 'pending')", (match_id,))

        self.assertFalse(database.has_debt_stage(match_id, "deadline_passed"))
        self.assertFalse(database.has_debt_stage(match_id, "warn_24h"))

        database.record_debt_stage(match_id, "deadline_passed")
        self.assertTrue(database.has_debt_stage(match_id, "deadline_passed"))
        self.assertFalse(database.has_debt_stage(match_id, "warn_24h"))

        database.record_debt_stage(match_id, "warn_24h")
        self.assertTrue(database.has_debt_stage(match_id, "warn_24h"))

    def test_debt_12h_cycle_reminders(self):
        """Test 12h cycle reminder timestamp tracking."""
        match_id = 101
        with database.transaction() as conn:
            conn.execute("INSERT INTO matches (id, round_number, status) VALUES (?, 1, 'pending')", (match_id,))

        self.assertIsNone(database.get_last_debt_12h_reminder(match_id))

        database.record_debt_12h_reminder(match_id)
        last_dt = database.get_last_debt_12h_reminder(match_id)
        self.assertIsNotNone(last_dt)
        self.assertIsInstance(last_dt, datetime.datetime)

    def test_apply_debt_played_reward(self):
        """Test reward for clearing debt matches (-1 warn, 0 stays 0)."""
        user_id = 777123

        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO users (telegram_id, username, team_name, warn_count) VALUES (?, 'tester', 'Arsenal', 2)",
                (user_id,)
            )

        # 1. First played debt match: 2 -> 1
        new_cnt, unwarned = database.apply_debt_played_reward(user_id, round_number=5)
        self.assertEqual(new_cnt, 1)
        self.assertTrue(unwarned)
        self.assertEqual(database.get_user_warn_count(user_id), 1)

        # 2. Second played debt match: 1 -> 0
        new_cnt, unwarned = database.apply_debt_played_reward(user_id, round_number=6)
        self.assertEqual(new_cnt, 0)
        self.assertTrue(unwarned)
        self.assertEqual(database.get_user_warn_count(user_id), 0)

        # 3. Third played debt match when already 0: 0 -> 0
        new_cnt, unwarned = database.apply_debt_played_reward(user_id, round_number=7)
        self.assertEqual(new_cnt, 0)
        self.assertFalse(unwarned)
        self.assertEqual(database.get_user_warn_count(user_id), 0)

    def test_start_datetime_clamping(self):
        """Test that past deadlines are clamped to DEBT_TRACKING_START_DATETIME (21.08.2026 12:00)."""
        # When start datetime is in the future relative to now:
        config.DEBT_TRACKING_START_DATETIME = (datetime.datetime.now() + datetime.timedelta(days=2)).strftime("%d.%m.%Y %H:%M")
        
        past_dl = (datetime.datetime.now() - datetime.timedelta(hours=50)).strftime("%d.%m.%Y %H:%M")

        with database.transaction() as conn:
            conn.execute("INSERT INTO users (telegram_id, username, team_name, warn_count) VALUES (111, 'u1', 'Real Madrid', 0)")
            conn.execute("INSERT INTO users (telegram_id, username, team_name, warn_count) VALUES (222, 'u2', 'Barcelona', 0)")
            conn.execute("INSERT INTO rounds (round_number, is_open, deadline) VALUES (1, 1, ?)", (past_dl,))
            conn.execute("INSERT INTO matches (id, round_number, player1_id, player2_id, player1_team, player2_team, status) VALUES (10, 1, 111, 222, 'Real Madrid', 'Barcelona', 'pending')")

        # Since start datetime is in the future, is_match_overdue should be False
        self.assertFalse(database.is_match_overdue(10))

        # Overdue matches should report 0.0 hours_overdue
        overdue = database.get_detailed_overdue_matches()
        self.assertEqual(len(overdue), 1)
        self.assertEqual(overdue[0]["hours_overdue"], 0.0)

    def test_recent_warn_rate_limit(self):
        """Test has_user_been_warned_recently helper."""
        user_id = 555666
        with database.transaction() as conn:
            conn.execute("INSERT INTO users (telegram_id, username, team_name, warn_count) VALUES (?, 'warned_u', 'Porto', 1)", (user_id,))
            conn.execute("INSERT INTO user_warns (user_id, admin_id, reason, type, created_at) VALUES (?, NULL, 'Test warn', 'WARN_ADD', CURRENT_TIMESTAMP)", (user_id,))

        self.assertTrue(database.has_user_been_warned_recently(user_id, hours=20.0))
        self.assertFalse(database.has_user_been_warned_recently(999999, hours=20.0))

    def test_admin_reset_and_restore(self):
        """Test global reset and restore user team."""
        with database.transaction() as conn:
            conn.execute("INSERT INTO users (telegram_id, username, team_name, warn_count) VALUES (10, 'u1', NULL, 4)")
            conn.execute("INSERT INTO users (telegram_id, username, team_name, warn_count) VALUES (20, 'u2', 'Ajax', 3)")
            conn.execute("INSERT INTO matches (id, round_number, status) VALUES (1, 1, 'pending')")
            conn.execute("INSERT INTO debt_reminders (match_id, stage) VALUES (1, 'warn_24h')")

        affected = database.admin_reset_all_warns_and_debts()
        self.assertEqual(affected, 2)
        self.assertEqual(database.get_user_warn_count(10), 0)
        self.assertEqual(database.get_user_warn_count(20), 0)
        self.assertFalse(database.has_debt_stage(1, "warn_24h"))

        # Restore user 10 club
        database.restore_user_team(10, "ПСВ")
        user10 = database.get_user(10)
        self.assertEqual(user10["team_name"], "ПСВ")
        self.assertEqual(user10["warn_count"], 0)


if __name__ == "__main__":
    unittest.main()
