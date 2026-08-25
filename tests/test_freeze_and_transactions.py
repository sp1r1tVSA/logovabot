import os
import sys
import tempfile
import datetime
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
import database


class TestReentrantTransactions(unittest.TestCase):
    def setUp(self):
        self.tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db_path = self.tf.name
        self.tf.close()
        self.orig_config_path = config.DB_PATH
        self.orig_database_path = database.DB_PATH
        config.DB_PATH = self.temp_db_path
        database.DB_PATH = self.temp_db_path
        database.init_db()

    def tearDown(self):
        config.DB_PATH = self.orig_config_path
        database.DB_PATH = self.orig_database_path
        try:
            os.remove(self.temp_db_path)
        except Exception:
            pass

    def test_nested_transaction_shares_connection(self):
        """Nested transaction() must return the SAME connection object."""
        with database.transaction() as outer:
            with database.transaction() as inner:
                self.assertIs(outer, inner)

    def test_nested_transaction_rolls_back_everything(self):
        """If an inner step raises after outer writes, NOTHING is committed."""
        raised = False
        try:
            with database.transaction() as conn:
                conn.execute(
                    "INSERT INTO users (telegram_id, username, team_name) VALUES (555, 'atomic', 'Аякс')"
                )
                # NOTE: no try/except here — the exception must escape the
                # outer scope so its rollback actually runs.
                with database.transaction() as conn2:
                    conn2.execute("UPDATE users SET username='changed' WHERE telegram_id=555")
                    raise RuntimeError("boom")
        except RuntimeError:
            raised = True

        self.assertTrue(raised)
        row = database.get_user(555)
        self.assertIsNone(row)

    def test_nested_transaction_commits_at_outermost(self):
        """All nested writes commit together when the outer scope succeeds."""
        with database.transaction() as conn:
            conn.execute("INSERT INTO users (telegram_id, username) VALUES (777, 'outer_ok')")
            with database.transaction() as c2:
                c2.execute("INSERT INTO users (telegram_id, username) VALUES (888, 'inner_ok')")

        self.assertIsNotNone(database.get_user(777))
        self.assertIsNotNone(database.get_user(888))


class TestFreezeAccounting(unittest.TestCase):
    def setUp(self):
        self.tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db_path = self.tf.name
        self.tf.close()

        self.orig_config_path = config.DB_PATH
        self.orig_database_path = database.DB_PATH
        self.orig_start_dt = config.DEBT_TRACKING_START_DATETIME

        config.DB_PATH = self.temp_db_path
        database.DB_PATH = self.temp_db_path
        # Tracking started long ago so it never gates overdue detection here
        config.DEBT_TRACKING_START_DATETIME = (
            datetime.datetime.now() - datetime.timedelta(days=30)
        ).strftime("%d.%m.%Y %H:%M")
        database.init_db()

        past_dl = (datetime.datetime.now() - datetime.timedelta(hours=100)).strftime("%d.%m.%Y %H:%M")
        with database.transaction() as conn:
            conn.execute("INSERT INTO users (telegram_id, username, team_name, warn_count) VALUES (11, 'p1', 'ПСВ', 0)")
            conn.execute("INSERT INTO users (telegram_id, username, team_name, warn_count) VALUES (22, 'p2', 'Аякс', 0)")
            conn.execute("INSERT INTO rounds (round_number, is_open, deadline) VALUES (5, 0, ?)", (past_dl,))
            conn.execute(
                "INSERT INTO matches (id, round_number, player1_team, player2_team, status) "
                "VALUES (7001, 5, 'ПСВ', 'Аякс', 'pending')"
            )

    def tearDown(self):
        config.DB_PATH = self.orig_config_path
        database.DB_PATH = self.orig_database_path
        config.DEBT_TRACKING_START_DATETIME = self.orig_start_dt
        try:
            os.remove(self.temp_db_path)
        except Exception:
            pass

    def _hours_for_match(self, match_id: int):
        for m in database.get_detailed_overdue_matches():
            if m["id"] == match_id:
                return m["hours_overdue"]
        return None

    def test_unfrozen_match_accrues_normally(self):
        hours = self._hours_for_match(7001)
        self.assertIsNotNone(hours)
        # Deadline was 100h ago; allow small scheduler drift
        self.assertGreater(hours, 90.0)

    def test_banked_freeze_time_reduces_hours_overdue(self):
        # Simulate a long completed freeze: 80 of 100 overdue hours were frozen
        with database.transaction() as conn:
            conn.execute(
                "UPDATE matches SET is_extended = 0, frozen_at = NULL, frozen_seconds = ? WHERE id = 7001",
                (int(80 * 3600),)
            )
        hours = self._hours_for_match(7001)
        self.assertLess(hours, 25.0)   # 100h wall-clock minus ~80h frozen ≈ ~20h
        self.assertGreaterEqual(hours, 10.0)

    def test_ongoing_freeze_excluded_from_hours(self):
        # Freeze started 60h ago and is still active (is_extended=1)
        f_at = (datetime.datetime.now() - datetime.timedelta(hours=60)).strftime("%Y-%m-%d %H:%M:%S")
        with database.transaction() as conn:
            conn.execute(
                "UPDATE matches SET is_extended = 1, frozen_at = ?, frozen_seconds = 0 WHERE id = 7001",
                (f_at,)
            )
        hours = self._hours_for_match(7001)
        # Only ~40h of real overdue time remain visible
        self.assertLess(hours, 45.0)

    def test_set_and_unset_freeze_via_api(self):
        database.set_match_extended(7001, 1)
        m = database.get_match(7001)
        self.assertEqual(m["is_extended"], 1)
        self.assertIsNotNone(m["frozen_at"])

        # Simulate the freeze having started 2 hours ago
        f_at = (datetime.datetime.now() - datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        with database.transaction() as conn:
            conn.execute("UPDATE matches SET frozen_at = ? WHERE id = 7001", (f_at,))

        database.set_match_extended(7001, 0)
        m = database.get_match(7001)
        self.assertEqual(m["is_extended"], 0)
        self.assertIsNone(m["frozen_at"])
        # ~2h banked into frozen_seconds
        self.assertGreaterEqual(m["frozen_seconds"], 7000)

    def test_reset_clears_freeze_state(self):
        with database.transaction() as conn:
            conn.execute(
                "UPDATE matches SET status='confirmed', player1_score=3, player2_score=1,"
                " is_extended=1, frozen_at='2026-01-01 00:00:00', frozen_seconds=99999 WHERE id = 7001"
            )
        database.reset_match(7001)
        m = database.get_match(7001)
        self.assertEqual(m["status"], "pending")
        self.assertEqual(m["is_extended"], 0)
        self.assertIsNone(m["frozen_at"])
        self.assertEqual(m["frozen_seconds"] or 0, 0)


if __name__ == "__main__":
    unittest.main()
