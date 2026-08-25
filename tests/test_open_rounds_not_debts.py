import os
import sys
import tempfile
import datetime
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
import database


class TestOpenRoundsNotDebts(unittest.TestCase):
    """Regression: open rounds with a future deadline must NOT count as debts."""

    def setUp(self):
        self.tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db_path = self.tf.name
        self.tf.close()

        self.orig_config_path = config.DB_PATH
        self.orig_database_path = database.DB_PATH
        self.orig_start_dt = config.DEBT_TRACKING_START_DATETIME

        config.DB_PATH = self.temp_db_path
        database.DB_PATH = self.temp_db_path
        config.DEBT_TRACKING_START_DATETIME = "01.01.2026 00:00"
        database.init_db()

        now = datetime.datetime.now()
        future_dl = (now + datetime.timedelta(days=2)).strftime("%d.%m.%Y %H:%M")
        past_dl = (now - datetime.timedelta(days=3)).strftime("%d.%m.%Y %H:%M")

        with database.transaction() as conn:
            conn.execute("INSERT INTO users (telegram_id, username, team_name, warn_count) VALUES (1, 'u1', 'ПСВ', 0)")
            conn.execute("INSERT INTO users (telegram_id, username, team_name, warn_count) VALUES (2, 'u2', 'Аякс', 0)")
            conn.execute("INSERT INTO users (telegram_id, username, team_name, warn_count) VALUES (3, 'u3', 'Порту', 0)")

            # Two simultaneously open rounds with FUTURE deadlines (like tours 25-26)
            conn.execute("INSERT INTO rounds (round_number, is_open, deadline) VALUES (25, 1, ?)", (future_dl,))
            conn.execute("INSERT INTO rounds (round_number, is_open, deadline) VALUES (26, 1, ?)", (future_dl,))
            # One closed round with expired deadline — legit debt
            conn.execute("INSERT INTO rounds (round_number, is_open, deadline) VALUES (24, 0, ?)", (past_dl,))

            conn.execute(
                "INSERT INTO matches (id, round_number, player1_team, player2_team, status) "
                "VALUES (9001, 25, 'ПСВ', 'Аякс', 'pending')"
            )
            conn.execute(
                "INSERT INTO matches (id, round_number, player1_team, player2_team, status) "
                "VALUES (9002, 26, 'Порту', 'ПСВ', 'pending')"
            )
            conn.execute(
                "INSERT INTO matches (id, round_number, player1_team, player2_team, status) "
                "VALUES (9003, 24, 'Аякс', 'Порту', 'pending')"
            )

    def tearDown(self):
        config.DB_PATH = self.orig_config_path
        database.DB_PATH = self.orig_database_path
        config.DEBT_TRACKING_START_DATETIME = self.orig_start_dt
        try:
            os.remove(self.temp_db_path)
        except Exception:
            pass

    def test_debts_summary_excludes_open_rounds_with_future_deadline(self):
        debts = database.get_all_unplayed_league_matches()
        ids = {m["id"] for m in debts}
        self.assertNotIn(9001, ids)
        self.assertNotIn(9002, ids)
        self.assertIn(9003, ids)

    def test_overdue_tracker_excludes_open_rounds_with_future_deadline(self):
        overdue = database.get_detailed_overdue_matches()
        ids = {m["id"] for m in overdue}
        self.assertNotIn(9001, ids)
        self.assertNotIn(9002, ids)
        self.assertIn(9003, ids)

    def test_open_round_match_not_overdue(self):
        self.assertFalse(database.is_match_overdue(9001))
        self.assertFalse(database.is_match_overdue(9002))
        self.assertTrue(database.is_match_overdue(9003))


if __name__ == "__main__":
    unittest.main()
