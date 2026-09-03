"""
tests/test_phase5_advanced_betting.py

Phase 5 Acceptance Test Suite — Advanced Betting Engine & Production Hardening.
25 targeted tests covering:
  - Market lifecycle state machine (6 allowed + 2 forbidden)
  - ODDS_CHANGED detection
  - MAX_BET / MAX_PAYOUT limits
  - IDEMPOTENCY_KEY_REUSED vs valid idempotency
  - Race conditions (concurrent bets, overdraft prevention)
  - Correct Score settlement
  - Handicap settlement
  - Audit log creation
  - Admin void bet (with refund)
  - Bet History 2.0 filters
  - Enriched bet details with odds_at_placement
"""

import os
import sys
import threading
import unittest
import tempfile

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from services.market_settler import evaluate_market_selection


# ─── Test Base ─────────────────────────────────────────────────────────────────

class Phase5TestBase(unittest.TestCase):
    """Base class: fresh in-memory DB for each test."""

    def setUp(self):
        # Use a fresh temp DB file for each test (full isolation)
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        database.DB_PATH = self._tmp.name
        database.init_db()

        # Create canonical divisions (1..5)
        database.ensure_canonical_divisions()

        # Seed test users
        self.user_a_id = 881001
        self.user_b_id = 881002
        self.admin_id = 999999  # any actor_id for audit logs

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'user_a', 'user')",
                (self.user_a_id,)
            )
            cursor.execute(
                "INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'user_b', 'user')",
                (self.user_b_id,)
            )

        database.get_or_create_wallet(self.user_a_id)
        database.get_or_create_wallet(self.user_b_id)

        # Give each user 10,000 coins
        with database.transaction() as conn:
            conn.cursor().execute(
                "UPDATE user_wallets SET balance = 10000 WHERE user_id IN (?, ?)",
                (self.user_a_id, self.user_b_id)
            )

        # Create test season, round, match, market, selections
        # Note: seasons has columns (id, name, status, created_at, started_at, finished_at, created_by)
        # Note: rounds has columns (id, season_id, division_id, round_number, is_open, deadline)
        self.division_id = 1
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO seasons (name, status) VALUES ('Test Season 1', 'active')"
            )
            self.season_id = cursor.lastrowid

            cursor.execute(
                """INSERT INTO rounds (division_id, round_number, season_id, is_open)
                   VALUES (?, 1, ?, 1)""",
                (self.division_id, self.season_id)
            )

            cursor.execute(
                """INSERT INTO matches (division_id, season_id, round_number,
                            player1_team, player2_team, status)
                   VALUES (?, ?, 1, 'TeamA', 'TeamB', 'pending')""",
                (self.division_id, self.season_id)
            )
            self.match_id = cursor.lastrowid

            cursor.execute(
                """INSERT INTO markets (match_id, market_key, market_name, status)
                   VALUES (?, '1x2', 'Match Result', 'open')""",
                (self.match_id,)
            )
            self.market_id = cursor.lastrowid

            cursor.execute(
                """INSERT INTO market_selections
                   (market_id, selection_key, selection_name, odds_value, status, odds_version)
                   VALUES (?, 'p1', 'TeamA Win', 2.00, 'active', 1)""",
                (self.market_id,)
            )
            self.sel_p1_id = cursor.lastrowid

            cursor.execute(
                """INSERT INTO market_selections
                   (market_id, selection_key, selection_name, odds_value, status, odds_version)
                   VALUES (?, 'p2', 'TeamB Win', 3.50, 'active', 1)""",
                (self.market_id,)
            )
            self.sel_p2_id = cursor.lastrowid

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except Exception:
            pass

    def _place_bet(self, user_id=None, amount=100, outcome="p1", odd=2.00,
                   match_id=None, market_id=None, sel_id=None, idempotency_key=None):
        user_id = user_id or self.user_a_id
        match_id = match_id or self.match_id
        market_id = market_id or self.market_id
        sel_id = sel_id or self.sel_p1_id
        return database.place_user_bet(
            user_id=user_id,
            amount=amount,
            selections=[{
                "match_id": match_id,
                "outcome": outcome,
                "odd": odd,
                "market_id": market_id,
                "selection_id": sel_id
            }],
            idempotency_key=idempotency_key
        )


# ─── Group 1: Market Lifecycle State Machine ──────────────────────────────────

class TestMarketLifecycle(Phase5TestBase):

    def test_01_open_to_suspended_allowed(self):
        """Market lifecycle: OPEN → SUSPENDED allowed."""
        result = database.transition_market_status(self.market_id, "suspended", self.admin_id)
        self.assertEqual(result["new_status"], "suspended")

    def test_02_suspended_to_open_allowed(self):
        """Market lifecycle: SUSPENDED → OPEN allowed."""
        database.transition_market_status(self.market_id, "suspended", self.admin_id)
        result = database.transition_market_status(self.market_id, "open", self.admin_id)
        self.assertEqual(result["new_status"], "open")

    def test_03_open_to_closed_allowed(self):
        """Market lifecycle: OPEN → CLOSED allowed."""
        result = database.transition_market_status(self.market_id, "closed", self.admin_id)
        self.assertEqual(result["new_status"], "closed")

    def test_04_suspended_to_closed_allowed(self):
        """Market lifecycle: SUSPENDED → CLOSED allowed."""
        database.transition_market_status(self.market_id, "suspended", self.admin_id)
        result = database.transition_market_status(self.market_id, "closed", self.admin_id)
        self.assertEqual(result["new_status"], "closed")

    def test_05_closed_to_settled_allowed(self):
        """Market lifecycle: CLOSED → SETTLED allowed."""
        database.transition_market_status(self.market_id, "closed", self.admin_id)
        result = database.transition_market_status(self.market_id, "settled", self.admin_id)
        self.assertEqual(result["new_status"], "settled")

    def test_06_closed_to_voided_allowed(self):
        """Market lifecycle: CLOSED → VOIDED allowed."""
        database.transition_market_status(self.market_id, "closed", self.admin_id)
        result = database.transition_market_status(self.market_id, "voided", self.admin_id)
        self.assertEqual(result["new_status"], "voided")

    def test_07_settled_to_open_forbidden(self):
        """Market lifecycle: SETTLED → OPEN FORBIDDEN."""
        database.transition_market_status(self.market_id, "closed", self.admin_id)
        database.transition_market_status(self.market_id, "settled", self.admin_id)
        with self.assertRaises(ValueError) as ctx:
            database.transition_market_status(self.market_id, "open", self.admin_id)
        self.assertIn("Forbidden", str(ctx.exception))

    def test_08_voided_to_open_forbidden(self):
        """Market lifecycle: VOIDED → OPEN FORBIDDEN."""
        database.transition_market_status(self.market_id, "closed", self.admin_id)
        database.transition_market_status(self.market_id, "voided", self.admin_id)
        with self.assertRaises(ValueError) as ctx:
            database.transition_market_status(self.market_id, "open", self.admin_id)
        self.assertIn("Forbidden", str(ctx.exception))


# ─── Group 2: Odds Changed Detection ─────────────────────────────────────────

class TestOddsChanged(Phase5TestBase):

    def test_09_odds_changed_detection(self):
        """ODDS_CHANGED when client odd differs from current server odd."""
        # Server odd is 2.00 for p1, but client sends 1.50
        ok, result = self._place_bet(odd=1.50)
        self.assertFalse(ok)
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("error"), "ODDS_CHANGED")
        self.assertEqual(result.get("old_odd"), 1.50)
        self.assertEqual(result.get("new_odd"), 2.00)

    def test_09b_correct_odds_accepted(self):
        """Bet with correct server odd is accepted."""
        ok, bet_id = self._place_bet(odd=2.00)
        self.assertTrue(ok)
        self.assertIsInstance(bet_id, int)


# ─── Group 3: Bet Limits ─────────────────────────────────────────────────────

class TestBetLimits(Phase5TestBase):

    def test_10_max_bet_rejected(self):
        """MAX_BET rejection (amount > 50000)."""
        with database.transaction() as conn:
            conn.cursor().execute(
                "UPDATE user_wallets SET balance = 1000000 WHERE user_id = ?",
                (self.user_a_id,)
            )
        ok, result = self._place_bet(amount=50001)
        self.assertFalse(ok)
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("error"), "MAX_BET_EXCEEDED")

    def test_11_max_payout_rejected(self):
        """MAX_PAYOUT rejection (potential_win > 500000)."""
        with database.transaction() as conn:
            conn.cursor().execute(
                "UPDATE user_wallets SET balance = 1000000 WHERE user_id = ?",
                (self.user_a_id,)
            )
            conn.cursor().execute(
                "UPDATE market_selections SET odds_value = 100.00 WHERE id = ?",
                (self.sel_p1_id,)
            )
        ok, result = self._place_bet(amount=10000, odd=100.00)
        self.assertFalse(ok)
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("error"), "MAX_PAYOUT_EXCEEDED")


# ─── Group 4: Idempotency 2.0 ────────────────────────────────────────────────

class TestIdempotency(Phase5TestBase):

    def test_12_idempotency_key_reused_different_payload(self):
        """IDEMPOTENCY_KEY_REUSED: same key + different payload → error."""
        key = "idem-test-reuse-001"
        ok1, bet_id1 = self._place_bet(amount=100, idempotency_key=key)
        self.assertTrue(ok1, f"First bet failed: {bet_id1}")

        # Same key but different amount → IDEMPOTENCY_KEY_REUSED
        ok2, result = self._place_bet(amount=200, idempotency_key=key)
        self.assertFalse(ok2)
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("error"), "IDEMPOTENCY_KEY_REUSED")

    def test_13_idempotency_same_payload_returns_same_bet(self):
        """Idempotency OK: same key + same payload → same bet ID, no double debit."""
        key = "idem-test-same-001"
        ok1, bet_id1 = self._place_bet(amount=100, idempotency_key=key)
        self.assertTrue(ok1, f"First bet failed: {bet_id1}")
        balance_after_first = database.get_wallet_balance(self.user_a_id)

        ok2, bet_id2 = self._place_bet(amount=100, idempotency_key=key)
        self.assertTrue(ok2, f"Idempotent request failed: {bet_id2}")
        self.assertEqual(bet_id1, bet_id2, "Must return same bet ID on duplicate")

        balance_after_second = database.get_wallet_balance(self.user_a_id)
        self.assertEqual(balance_after_first, balance_after_second,
                         "Balance must NOT change on duplicate idempotent request")


# ─── Group 5: Race Conditions ─────────────────────────────────────────────────

class TestRaceConditions(Phase5TestBase):

    def test_14_concurrent_bets_same_key_single_debit(self):
        """Race condition: 5 concurrent bets with same key → only 1 unique bet ID."""
        key = "race-idem-001"
        results = []

        def place():
            try:
                ok, r = self._place_bet(amount=100, idempotency_key=key)
                results.append((ok, r))
            except Exception as e:
                results.append((False, str(e)))

        threads = [threading.Thread(target=place) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        bet_ids = {r for ok, r in results if ok and isinstance(r, int)}
        self.assertLessEqual(len(bet_ids), 1, f"Only 1 unique bet ID allowed, got: {bet_ids}")

        # Balance should only be debited once max
        balance = database.get_wallet_balance(self.user_a_id)
        self.assertGreaterEqual(balance, 9900, f"Balance should be at least 9900, got {balance}")

    def test_15_concurrent_overdraft_prevention(self):
        """Race condition: balance=100, two concurrent 80-coin bets → max 1 accepted."""
        with database.transaction() as conn:
            conn.cursor().execute(
                "UPDATE user_wallets SET balance = 100 WHERE user_id = ?",
                (self.user_a_id,)
            )

        results = []

        def place_unique(i):
            try:
                ok, r = self._place_bet(amount=80, idempotency_key=f"overdraft-test-{i}")
                results.append((ok, r))
            except Exception as e:
                results.append((False, str(e)))

        threads = [threading.Thread(target=place_unique, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [(ok, r) for ok, r in results if ok]
        self.assertLessEqual(len(successes), 1,
                             f"Max 1 of 2 concurrent 80-coin bets can succeed. Got: {results}")
        balance = database.get_wallet_balance(self.user_a_id)
        self.assertGreaterEqual(balance, 0, "Balance must never go negative")


# ─── Group 6: Settlement (pure logic, no DB) ──────────────────────────────────

class TestSettlement(unittest.TestCase):
    """Pure market_settler tests — no DB required."""

    def test_16_correct_score_won(self):
        """Correct Score: 2:1 match + cs_2_1 selection → WON."""
        result = evaluate_market_selection(
            market_key="correct_score",
            selection_key="cs_2_1",
            score1=2, score2=1, match_status="finished"
        )
        self.assertEqual(result, "won")

    def test_17_correct_score_lost(self):
        """Correct Score: 2:1 match + cs_1_0 selection → LOST."""
        result = evaluate_market_selection(
            market_key="correct_score",
            selection_key="cs_1_0",
            score1=2, score2=1, match_status="finished"
        )
        self.assertEqual(result, "lost")

    def test_18_handicap_minus_1_5_team1_win(self):
        """Handicap: h1_minus_1.5, score 3:0 → WON (3.0 - 1.5 = 1.5 > 0)."""
        result = evaluate_market_selection(
            market_key="handicap",
            selection_key="h1_minus_1.5",
            score1=3, score2=0, match_status="finished"
        )
        self.assertEqual(result, "won")

    def test_19_handicap_minus_1_5_team1_lost(self):
        """Handicap: h1_minus_1.5, score 1:0 → LOST (1.0 - 1.5 = -0.5 < 0)."""
        result = evaluate_market_selection(
            market_key="handicap",
            selection_key="h1_minus_1.5",
            score1=1, score2=0, match_status="finished"
        )
        self.assertEqual(result, "lost")


# ─── Group 7: Audit Log ──────────────────────────────────────────────────────

class TestAuditLog(Phase5TestBase):

    def test_20_audit_log_on_market_transition(self):
        """Audit log entry created on market transition."""
        database.transition_market_status(self.market_id, "suspended", self.admin_id)
        logs = database.get_betting_audit_log(entity_type="market", limit=10)
        self.assertTrue(
            any(l["entity_id"] == self.market_id and l["action"] == "market_suspended"
                for l in logs),
            f"Expected 'market_suspended' entry. Got: {[l['action'] for l in logs]}"
        )

    def test_21_audit_log_on_odds_change(self):
        """Audit log entry created on odds change."""
        database.update_selection_odds(self.sel_p1_id, 1.75, self.admin_id)
        logs = database.get_betting_audit_log(entity_type="selection", limit=10)
        self.assertTrue(
            any(l["entity_id"] == self.sel_p1_id and l["action"] == "odds_changed"
                for l in logs),
            f"Expected 'odds_changed' entry. Got: {[l['action'] for l in logs]}"
        )


# ─── Group 8: Admin Void Bet ─────────────────────────────────────────────────

class TestAdminVoidBet(Phase5TestBase):

    def test_22_admin_can_void_bet_with_refund(self):
        """Admin can void a bet and the stake is refunded."""
        ok, bet_id = self._place_bet(amount=500)
        self.assertTrue(ok, f"Bet placement failed: {bet_id}")
        bal_after_bet = database.get_wallet_balance(self.user_a_id)

        result = database.void_user_bet(bet_id, self.admin_id)
        self.assertEqual(result["refunded_amount"], 500)
        self.assertEqual(result["bet_id"], bet_id)

        bal_after_void = database.get_wallet_balance(self.user_a_id)
        self.assertEqual(bal_after_void, bal_after_bet + 500,
                         "Balance must be fully refunded after void")

    def test_23_cannot_void_already_voided_bet(self):
        """Voiding an already refunded bet raises ValueError."""
        ok, bet_id = self._place_bet(amount=100)
        self.assertTrue(ok)
        database.void_user_bet(bet_id, self.admin_id)
        with self.assertRaises(ValueError):
            database.void_user_bet(bet_id, self.admin_id)


# ─── Group 9: Bet History 2.0 ────────────────────────────────────────────────

class TestBetHistory(Phase5TestBase):

    def test_24_bet_history_refunded_filter(self):
        """Bet History: filter by 'refunded' returns only refunded bets."""
        ok, bet_id = self._place_bet(amount=100)
        self.assertTrue(ok, f"Bet placement failed: {bet_id}")
        database.void_user_bet(bet_id, self.admin_id)

        bets = database.get_user_bets(self.user_a_id, status="refunded")
        refunded_ids = {b["id"] for b in bets}
        self.assertIn(bet_id, refunded_ids, "Voided bet should appear in refunded filter")

    def test_25_bet_detail_has_enriched_items_with_odds_at_placement(self):
        """Bet detail response includes enriched items with odds_at_placement."""
        ok, bet_id = self._place_bet(amount=100, odd=2.00)
        self.assertTrue(ok, f"Bet placement failed: {bet_id}")

        bets = database.get_user_bets(self.user_a_id, status="pending")
        target = next((b for b in bets if b["id"] == bet_id), None)
        self.assertIsNotNone(target, "Bet not found in history")

        items = target.get("items", [])
        self.assertTrue(len(items) > 0, "Bet must have items")

        item = items[0]
        self.assertIn("odds_at_placement", item, "Item must include odds_at_placement")
        self.assertAlmostEqual(float(item["odds_at_placement"]), 2.00, places=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
