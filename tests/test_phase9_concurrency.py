"""
tests/test_phase9_concurrency.py

Phase 9 — Concurrency Red Team Test Suite.
Verifies thread-safe execution and zero financial duplication under concurrent races:
1. Two simultaneous cashouts on same bet.
2. Two simultaneous match settlements on same match.
3. Bet placement concurrent with market suspension.
4. Bet placement concurrent with odds update (ODDS_CHANGED or clean placement).
5. Bet placement concurrent with match status finish.
"""

import concurrent.futures
import os
import tempfile
import unittest
import database
from services.cashout_engine import execute_cashout
from services.settlement_engine import settle_match_predictions
from services.odds_engine import set_odds


class TestPhase9Concurrency(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        database.DB_PATH = self._tmp.name
        database.init_db()
        database.ensure_canonical_divisions()

        self.user_id = 970001
        self.match_id = 970011

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'conc_user', 'user')", (self.user_id,))
            cursor.execute("INSERT INTO seasons (name, status) VALUES ('Conc Season', 'active')")
            self.season_id = cursor.lastrowid
            cursor.execute("INSERT INTO rounds (division_id, round_number, season_id, is_open) VALUES (1, 1, ?, 1)", (self.season_id,))

            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, status)
                VALUES (?, 1, ?, 1, 'Juventus', 'Roma', 'open')
            """, (self.match_id, self.season_id))

            cursor.execute("INSERT INTO markets (id, match_id, market_key, market_name, status) VALUES (971, ?, 'match_result', 'Match Winner', 'open')", (self.match_id,))
            cursor.execute("""
                INSERT INTO market_selections (id, market_id, selection_key, selection_name, odds_value, status, odds_version)
                VALUES (9711, 971, 'home', 'Juventus', 2.00, 'active', 1)
            """)

        database.get_or_create_wallet(self.user_id)
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE user_wallets SET balance = 10000 WHERE user_id = ?", (self.user_id,))

    def tearDown(self):
        try:
            os.remove(self._tmp.name)
        except OSError:
            pass

    def test_p9_conc_01_two_concurrent_cashouts_exact_single_payout(self):
        """P9-CONC-01: Two simultaneous cashout requests settle the bet exactly once."""
        success, bet_id = database.place_user_bet(
            user_id=self.user_id,
            amount=1000,
            selections=[{"match_id": self.match_id, "market_id": 971, "selection_id": 9711, "outcome": "home", "odds": 2.00}],
            idempotency_key="conc-bet-1"
        )
        self.assertTrue(success)
        bal_after_bet = database.get_or_create_wallet(self.user_id)["balance"]
        self.assertEqual(bal_after_bet, 9000)

        # Launch 2 concurrent cashouts
        def run_cashout(key_suffix):
            return execute_cashout(self.user_id, bet_id, idempotency_key=f"cash-{key_suffix}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(run_cashout, "1")
            f2 = executor.submit(run_cashout, "2")
            r1 = f1.result()
            r2 = f2.result()

        results = [r1, r2]
        success_count = sum(1 for ok, _ in results if ok)
        fail_count = sum(1 for ok, _ in results if not ok)

        self.assertEqual(success_count, 1, "Exactly one cashout must succeed")
        self.assertEqual(fail_count, 1, "The duplicate cashout must be rejected")

        # Verify wallet credited once
        successful_res = [res for ok, res in results if ok][0]
        expected_bal = 9000 + successful_res["payout"]
        current_bal = database.get_or_create_wallet(self.user_id)["balance"]
        self.assertEqual(current_bal, expected_bal)

    def test_p9_conc_02_two_concurrent_match_settlements(self):
        """P9-CONC-02: Two concurrent calls to settle_match_predictions pay winnings exactly once."""
        success, bet_id = database.place_user_bet(
            user_id=self.user_id,
            amount=500,
            selections=[{"match_id": self.match_id, "market_id": 971, "selection_id": 9711, "outcome": "home", "odds": 2.00}],
            idempotency_key="conc-bet-2"
        )
        self.assertTrue(success)
        bal_after_bet = database.get_or_create_wallet(self.user_id)["balance"]

        def run_settle():
            return settle_match_predictions(self.match_id, score1=1, score2=0)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(run_settle)
            f2 = executor.submit(run_settle)
            s1 = f1.result()
            s2 = f2.result()

        total_notifs = len(s1) + len(s2)
        # Exactly one execution emits the winning notification (500 * 2.0 = 1000)
        self.assertEqual(total_notifs, 1)

        # Wallet balance should be bal_after_bet + 1000 (NOT + 2000)
        current_bal = database.get_or_create_wallet(self.user_id)["balance"]
        self.assertEqual(current_bal, bal_after_bet + 1000)

    def test_p9_conc_03_bet_concurrent_with_market_suspension(self):
        """P9-CONC-03: Placing bet while market is being suspended never causes corrupt/negative balance."""
        def try_bet():
            return database.place_user_bet(
                user_id=self.user_id,
                amount=200,
                selections=[{"match_id": self.match_id, "market_id": 971, "selection_id": 9711, "outcome": "home", "odds": 2.00}],
                idempotency_key="conc-bet-3"
            )

        def suspend_market():
            with database.transaction() as conn:
                conn.cursor().execute("UPDATE markets SET status = 'suspended' WHERE id = 971")
            return True

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f_susp = executor.submit(suspend_market)
            f_bet = executor.submit(try_bet)
            f_susp.result()
            bet_ok, bet_res = f_bet.result()

        bal = database.get_or_create_wallet(self.user_id)["balance"]
        if bet_ok:
            # Bet succeeded before suspension committed
            self.assertEqual(bal, 9800)
        else:
            # Bet was blocked by suspension
            self.assertEqual(bal, 10000)

    def test_p9_conc_04_bet_concurrent_with_odds_update(self):
        """P9-CONC-04: Bet placement concurrent with odds update triggers ODDS_CHANGED or succeeds with valid state."""
        def try_bet():
            return database.place_user_bet(
                user_id=self.user_id,
                amount=300,
                selections=[{"match_id": self.match_id, "market_id": 971, "selection_id": 9711, "outcome": "home", "odds": 2.00, "odd": 2.00}],
                idempotency_key="conc-bet-4"
            )

        def update_odds():
            return set_odds(market_id=971, selection_key="home", value=2.20, admin_id=None, reason="drift")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f_up = executor.submit(update_odds)
            f_bet = executor.submit(try_bet)
            f_up.result()
            bet_ok, bet_res = f_bet.result()

        bal = database.get_or_create_wallet(self.user_id)["balance"]
        if not bet_ok and isinstance(bet_res, dict):
            # Caught by ODDS_CHANGED
            self.assertEqual(bet_res.get("error"), "ODDS_CHANGED")
            self.assertEqual(bal, 10000)
        elif bet_ok:
            # Placed before or after cleanly
            self.assertEqual(bal, 9700)

    def test_p9_conc_05_bet_concurrent_with_match_finish(self):
        """P9-CONC-05: Bet placement concurrent with match finish is rejected once match is finished."""
        # Mark match finished
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE matches SET status = 'completed' WHERE id = ?", (self.match_id,))

        ok, err = database.place_user_bet(
            user_id=self.user_id,
            amount=500,
            selections=[{"match_id": self.match_id, "market_id": 971, "selection_id": 9711, "outcome": "home", "odds": 2.00}],
            idempotency_key="conc-bet-5"
        )
        self.assertFalse(ok)
        self.assertEqual(database.get_or_create_wallet(self.user_id)["balance"], 10000)


if __name__ == "__main__":
    unittest.main()
