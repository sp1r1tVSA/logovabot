"""
tests/test_phase9_risk_engine.py

Phase 9 — Risk Engine & Central Bet Validation Test Suite.
Verifies stake limits, daily limits, payout caps, suspension guards,
stale odds protection, and wallet balance enforcement.
"""

import os
import tempfile
import unittest
import database
from services.risk_engine import RiskEngine
from services.betting_limits import BettingLimitsService


class TestPhase9RiskEngine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        database.DB_PATH = self._tmp.name
        database.init_db()
        database.ensure_canonical_divisions()

        self.user_id = 910001
        self.division_id = 1
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'risk_tester', 'user')", (self.user_id,))
            cursor.execute("INSERT INTO seasons (name, status) VALUES ('Season Risk', 'active')")
            self.season_id = cursor.lastrowid
            cursor.execute("INSERT INTO rounds (division_id, round_number, season_id, is_open) VALUES (?, 1, ?, 1)", (self.division_id, self.season_id))
            cursor.execute("""
                INSERT INTO matches (division_id, season_id, round_number, player1_team, player2_team, status)
                VALUES (?, ?, 1, 'Arsenal', 'Chelsea', 'pending')
            """, (self.division_id, self.season_id))
            self.match_id = cursor.lastrowid
            cursor.execute("""
                INSERT INTO markets (match_id, market_key, market_name, status)
                VALUES (?, '1x2', 'Match Winner', 'open')
            """, (self.match_id,))
            self.market_id = cursor.lastrowid
            cursor.execute("""
                INSERT INTO market_selections (market_id, selection_key, selection_name, odds_value, status, odds_version)
                VALUES (?, 'p1', 'Arsenal Win', 2.00, 'active', 1)
            """, (self.market_id,))
            self.sel_p1_id = cursor.lastrowid

        database.get_or_create_wallet(self.user_id)
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE user_wallets SET balance = 50000 WHERE user_id = ?", (self.user_id,))

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except Exception:
            pass

    def test_01_min_bet_rejected(self):
        """P9-RISK-01: Bet with stake below MIN_BET (5 < 10) rejected."""
        ok, res = database.place_user_bet(
            user_id=self.user_id,
            amount=5,
            selections=[{"match_id": self.match_id, "outcome": "p1", "market_id": self.market_id, "selection_id": self.sel_p1_id}]
        )
        self.assertFalse(ok)
        self.assertIn("10", str(res))

    def test_02_max_bet_limited_or_rejected(self):
        """P9-RISK-02: Bet with stake above MAX_BET (60,000 > 50,000) rejected with MAX_BET_EXCEEDED."""
        ok, res = database.place_user_bet(
            user_id=self.user_id,
            amount=60_000,
            selections=[{"match_id": self.match_id, "outcome": "p1", "market_id": self.market_id, "selection_id": self.sel_p1_id}]
        )
        self.assertFalse(ok)
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("error"), "MAX_BET_EXCEEDED")

    def test_03_max_payout_capped(self):
        """P9-RISK-03: Bet with potential payout > MAX_PAYOUT (500,000) rejected with MAX_PAYOUT_EXCEEDED."""
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE market_selections SET odds_value = 100.0 WHERE id = ?", (self.sel_p1_id,))
        ok, res = database.place_user_bet(
            user_id=self.user_id,
            amount=10_000,  # 10,000 * 100.0 = 1,000,000 > 500,000
            selections=[{"match_id": self.match_id, "outcome": "p1", "market_id": self.market_id, "selection_id": self.sel_p1_id}]
        )
        self.assertFalse(ok)
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("error"), "MAX_PAYOUT_EXCEEDED")

    def test_04_daily_limit_enforced(self):
        """P9-RISK-04: User exceeding daily betting limit is rejected."""
        # Set custom daily limit of 2,000 coins
        BettingLimitsService.set_limit("user", self.user_id, "max_daily_stake", 2000)
        # First bet 1500 succeeds
        ok1, res1 = database.place_user_bet(
            user_id=self.user_id,
            amount=1500,
            selections=[{"match_id": self.match_id, "outcome": "p1", "market_id": self.market_id, "selection_id": self.sel_p1_id}]
        )
        self.assertTrue(ok1)

        # Second bet 1000 exceeds daily limit of 2000 (1500 + 1000 = 2500)
        ok2, res2 = database.place_user_bet(
            user_id=self.user_id,
            amount=1000,
            selections=[{"match_id": self.match_id, "outcome": "p1", "market_id": self.market_id, "selection_id": self.sel_p1_id}]
        )
        self.assertFalse(ok2)
        self.assertIsInstance(res2, dict)
        self.assertEqual(res2.get("error"), "DAILY_LIMIT")

    def test_05_suspended_market_rejected(self):
        """P9-RISK-05: Bet on suspended market is rejected with MARKET_SUSPENDED."""
        database.transition_market_status(self.market_id, "suspended", 999)
        ok, res = database.place_user_bet(
            user_id=self.user_id,
            amount=100,
            selections=[{"match_id": self.match_id, "outcome": "p1", "market_id": self.market_id, "selection_id": self.sel_p1_id}]
        )
        self.assertFalse(ok)
        if isinstance(res, dict):
            self.assertEqual(res.get("error"), "MARKET_SUSPENDED")
        else:
            self.assertIn("приостановлен", str(res))

    def test_06_stale_odds_in_live_match_rejected(self):
        """P9-RISK-06: Bet on live match with odds older than 300s rejected with ODDS_STALE."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE matches SET status = 'live' WHERE id = ?", (self.match_id,))
            cursor.execute("UPDATE market_selections SET updated_at = datetime('now', '-600 seconds') WHERE id = ?", (self.sel_p1_id,))
        
        ok, res = database.place_user_bet(
            user_id=self.user_id,
            amount=100,
            selections=[{"match_id": self.match_id, "outcome": "p1", "market_id": self.market_id, "selection_id": self.sel_p1_id}]
        )
        self.assertFalse(ok)
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("error"), "ODDS_STALE")

    def test_07_insufficient_balance_rejected(self):
        """P9-RISK-07: Bet exceeding available balance rejected without mutating wallet."""
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE user_wallets SET balance = 50 WHERE user_id = ?", (self.user_id,))
        ok, res = database.place_user_bet(
            user_id=self.user_id,
            amount=100,
            selections=[{"match_id": self.match_id, "outcome": "p1", "market_id": self.market_id, "selection_id": self.sel_p1_id}]
        )
        self.assertFalse(ok)
        self.assertIn("Недостаточно монет", str(res))
        self.assertEqual(database.get_user_balance(self.user_id), 50)
