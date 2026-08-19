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

        config.DB_PATH = self.temp_db_path
        database.DB_PATH = self.temp_db_path
        database.init_db()

    def tearDown(self):
        """Restore original paths and cleanup temp db."""
        config.DB_PATH = self.orig_config_path
        database.DB_PATH = self.orig_database_path
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

    def test_get_detailed_overdue_matches_and_is_overdue(self):
        """Test overdue matches detection and calculation."""
        past_dl = (datetime.datetime.now() - datetime.timedelta(hours=26)).strftime("%d.%m.%Y %H:%M")
        future_dl = (datetime.datetime.now() + datetime.timedelta(hours=24)).strftime("%d.%m.%Y %H:%M")

        with database.transaction() as conn:
            conn.execute("INSERT INTO users (telegram_id, username, team_name, warn_count) VALUES (111, 'u1', 'Real Madrid', 1)")
            conn.execute("INSERT INTO users (telegram_id, username, team_name, warn_count) VALUES (222, 'u2', 'Barcelona', 0)")

            conn.execute("INSERT INTO rounds (round_number, is_open, deadline) VALUES (1, 1, ?)", (past_dl,))
            conn.execute("INSERT INTO rounds (round_number, is_open, deadline) VALUES (2, 1, ?)", (future_dl,))

            conn.execute("""
                INSERT INTO matches (id, round_number, player1_id, player2_id, player1_team, player2_team, status)
                VALUES (10, 1, 111, 222, 'Real Madrid', 'Barcelona', 'pending')
            """)
            conn.execute("""
                INSERT INTO matches (id, round_number, player1_id, player2_id, player1_team, player2_team, status)
                VALUES (20, 2, 111, 222, 'Real Madrid', 'Barcelona', 'pending')
            """)

        self.assertTrue(database.is_match_overdue(10))
        self.assertFalse(database.is_match_overdue(20))

        overdue = database.get_detailed_overdue_matches()
        self.assertEqual(len(overdue), 1)
        m = overdue[0]
        self.assertEqual(m["id"], 10)
        self.assertEqual(m["round_number"], 1)
        self.assertGreaterEqual(m["hours_overdue"], 25.0)
        self.assertEqual(m["p1_warns"], 1)
        self.assertEqual(m["p2_warns"], 0)

        # User 111 and 222 have 1 debt remaining
        self.assertEqual(database.count_user_remaining_debts(111), 1)
        self.assertEqual(database.count_user_remaining_debts(222), 1)

    def test_ban_and_remove_from_league(self):
        """Test auto-kick function sets team_name to NULL and logs AUTO_KICK."""
        user_id = 999888
        with database.transaction() as conn:
            conn.execute("INSERT INTO users (telegram_id, username, team_name, warn_count) VALUES (?, 'kicked_user', 'Chelsea', 4)", (user_id,))

        team = database.ban_and_remove_from_league(user_id)
        self.assertEqual(team, "Chelsea")

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT team_name FROM users WHERE telegram_id = ?", (user_id,))
            row = cursor.fetchone()
            self.assertIsNone(row["team_name"])

            cursor.execute("SELECT type FROM user_warns WHERE user_id = ?", (user_id,))
            warn_row = cursor.fetchone()
            self.assertEqual(warn_row["type"], "AUTO_KICK")


if __name__ == "__main__":
    unittest.main()
