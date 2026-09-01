"""
tests/test_schema_migration.py
Unit tests for LOGOVO.BET v2.0 Database Schema:
- Relational markets & selections
- Odds history & audits
- Favorites & Notifications
- Saved coupons
- Extended matches & user_bets columns
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database


class TestSchemaMigration(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self.user_id = 999905

    def test_new_tables_exist(self):
        expected_tables = [
            "teams",
            "tournaments",
            "markets",
            "market_selections",
            "odds_history",
            "favorites",
            "notifications",
            "user_notification_settings",
            "admin_audit_log",
            "saved_coupons"
        ]
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cursor.fetchall()]
            for tbl in expected_tables:
                self.assertIn(tbl, tables, f"Table '{tbl}' should exist in DB schema.")

    def test_matches_new_columns(self):
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(matches)")
            cols = [r[1] for r in cursor.fetchall()]
            for col in ["ht_score1", "ht_score2", "match_date", "match_time", "stadium", "referee", "live_minute"]:
                self.assertIn(col, cols, f"Column '{col}' should exist in matches table.")

    def test_user_bets_new_columns(self):
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(user_bets)")
            cols = [r[1] for r in cursor.fetchall()]
            for col in ["system_config", "actual_payout", "idempotency_key", "cashout_at"]:
                self.assertIn(col, cols, f"Column '{col}' should exist in user_bets table.")

    def test_bet_items_new_columns(self):
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(bet_items)")
            cols = [r[1] for r in cursor.fetchall()]
            for col in ["market_id", "selection_id", "odds_at_placement"]:
                self.assertIn(col, cols, f"Column '{col}' should exist in bet_items table.")

    def test_coin_transactions_new_columns(self):
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(coin_transactions)")
            cols = [r[1] for r in cursor.fetchall()]
            for col in ["reference_type", "balance_after"]:
                self.assertIn(col, cols, f"Column '{col}' should exist in coin_transactions table.")

    def test_user_wallets_daily_limit_column(self):
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(user_wallets)")
            cols = [r[1] for r in cursor.fetchall()]
            self.assertIn("daily_limit", cols, "Column 'daily_limit' should exist in user_wallets table.")


if __name__ == "__main__":
    unittest.main()
