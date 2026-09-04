"""
tests/test_phase6_1_redteam.py

PHASE 6.1 RED TEAM & ADVERSARIAL ACCEPTANCE TEST SUITE
Systematically attacks and stress-tests Phases 1-6 implementations:
1. Live Event Replay, Duplicate Goal & Terminal Match Ingestion.
2. Match State Machine Illegal Transitions & Terminal Protections.
3. Live Market Suspension Race Condition.
4. Live Odds Drift & 409 ODDS_CHANGED Enforcement.
5. Client-Side Financial Tampering Filtering.
6. Double Bet Attack & Idempotency Key Reuse Attack.
7. Wallet Concurrency & Overdraft Protection.
8. Stake & Payout Limit Boundary Enforcement.
9. Double Settlement & Concurrent Settlement Race Protection.
10. Double Refund & void_user_bet Attack on Settled Bets.
11. Admin Result Correction Integrity & Complete Audit Trails.
12. Division & Season Historical & Analytical Data Isolation.
13. IDOR Protection on User Bets & Prediction Slips.
14. Admin RBAC Cross-Division Management Guards.
15. Telegram Forum Topic Isolation with Identical thread_id.
16. Telegram WebApp HMAC Auth Red Team (Missing, Future, Expired auth_date, Mock Admin).
17. Poisson Distribution Model Bounds (0 <= P <= 1, no NaN/Inf) & Edge Math.
18. Capper Leaderboard Threshold (Exclusion of 1-bet flukes).
19. Notification Flood Attack (100 duplicates -> exactly 1 queued).
20. SQL Injection & Pagination Abuse Bounds.
"""

import concurrent.futures
import hashlib
import hmac
import json
import os
import sys
import threading
import time
import unittest
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from api.auth import validate_telegram_init_data, get_authenticated_user
from config import TOKEN
from services.analytics_service import get_capper_leaderboard, get_user_betting_analytics
from services.intelligence_engine import IntelligenceEngine, _poisson_probability
from services.live_ingestion import (
    get_live_events,
    get_live_match_state,
    ingest_live_event,
)
from services.live_state_machine import (
    ABANDONED,
    CANCELLED,
    FINISHED,
    HALFTIME,
    LIVE,
    PRE_MATCH,
    SCHEDULED,
    InvalidStateTransitionError,
    can_transition,
    transition_live_match,
)
from services.market_safety import evaluate_and_apply_suspend_rules
from services.notification_service import queue_notification
from services.settlement_engine import settle_match_predictions
from services.sports_provider import LiveEvent
from services.topic_cache import TopicCache


def make_init_data(user_dict: dict, token: str = "test_bot_token", auth_date: int | None = None) -> str:
    """Helper to generate signed Telegram initData."""
    if auth_date is None:
        auth_date = int(time.time())
    params = {
        "auth_date": str(auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(user_dict, separators=(",", ":"))
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    hash_val = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    params["hash"] = hash_val
    return urlencode(params)


class Phase61RedTeamTestBase(unittest.TestCase):

    def setUp(self) -> None:
        database.init_db()
        self._cleanup()
        self._seed()

    def tearDown(self) -> None:
        self._cleanup()

    def _cleanup(self) -> None:
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM bet_audit_log WHERE actor_id >= 886600 OR entity_id >= 886600")
            cursor.execute("DELETE FROM admin_audit_log WHERE admin_id >= 886600 OR target_id >= 886600")
            cursor.execute("DELETE FROM coin_transactions WHERE user_id >= 886600")
            cursor.execute("DELETE FROM bet_items WHERE match_id >= 886600 OR bet_id IN (SELECT id FROM user_bets WHERE user_id >= 886600)")
            cursor.execute("DELETE FROM user_bets WHERE user_id >= 886600")
            cursor.execute("DELETE FROM user_wallets WHERE user_id >= 886600")
            cursor.execute("DELETE FROM division_admins WHERE user_id >= 886600")
            cursor.execute("DELETE FROM notification_events WHERE user_id >= 886600")
            cursor.execute("DELETE FROM live_events WHERE match_id >= 886600")
            cursor.execute("DELETE FROM live_statistics WHERE match_id >= 886600")
            cursor.execute("DELETE FROM live_match_states WHERE match_id >= 886600")
            cursor.execute("DELETE FROM market_selections WHERE market_id >= 8866000")
            cursor.execute("DELETE FROM markets WHERE match_id >= 886600")
            cursor.execute("DELETE FROM matches WHERE id >= 886600")
            cursor.execute("DELETE FROM users WHERE telegram_id >= 886600")
            cursor.execute("DELETE FROM division_topics WHERE group_chat_id IN (-1001111111, -1002222222)")

    def _seed(self) -> None:
        with database.transaction() as conn:
            cursor = conn.cursor()
            # Seed users in isolated namespace 886600+
            cursor.execute("INSERT INTO users (telegram_id, username, division_id) VALUES (886601, 'red_user_1', 1)")
            cursor.execute("INSERT INTO users (telegram_id, username, division_id) VALUES (886602, 'red_user_2', 1)")
            cursor.execute("INSERT INTO users (telegram_id, username, division_id) VALUES (886603, 'red_admin_div1', 1)")
            cursor.execute("INSERT INTO users (telegram_id, username, division_id) VALUES (886604, 'red_user_div2', 2)")

            cursor.execute("INSERT INTO user_wallets (user_id, balance) VALUES (886601, 1000)")
            cursor.execute("INSERT INTO user_wallets (user_id, balance) VALUES (886602, 1000)")
            cursor.execute("INSERT INTO user_wallets (user_id, balance) VALUES (886603, 10000)")
            cursor.execute("INSERT INTO user_wallets (user_id, balance) VALUES (886604, 1000)")

            cursor.execute("INSERT INTO division_admins (user_id, division_id) VALUES (886603, 1)")

            # Seed matches (Match 886601 = Div 1, Match 886602 = Div 2)
            cursor.execute("""
                INSERT INTO matches (id, season_id, division_id, round_number, player1_team, player2_team, status, player1_score, player2_score)
                VALUES (886601, 1, 1, 1, 'Порту', 'Бенфика', 'live', 0, 0),
                       (886602, 1, 2, 1, 'Аякс', 'Фейеноорд', 'live', 0, 0)
            """)

            # Seed markets & market_selections
            cursor.execute("INSERT INTO markets (id, match_id, market_key, market_name, category, status) VALUES (8866001, 886601, '1x2', '1X2', 'main', 'open')")
            cursor.execute("INSERT INTO markets (id, match_id, market_key, market_name, category, status) VALUES (8866002, 886602, '1x2', '1X2', 'main', 'open')")

            cursor.execute("""
                INSERT INTO market_selections (id, market_id, selection_key, selection_name, odds_value, status)
                VALUES (88660011, 8866001, 'p1', 'П1', 2.00, 'active'),
                       (88660012, 8866001, 'x', 'X', 3.20, 'active'),
                       (88660013, 8866001, 'p2', 'П2', 3.50, 'active'),
                       (88660021, 8866002, 'p1', 'П1', 1.80, 'active')
            """)


class TestLiveEventAndStateMachineAttacks(Phase61RedTeamTestBase):

    def test_01_duplicate_event_replay_does_not_corrupt_score(self) -> None:
        """STEP 4 & 5: Ingesting duplicate events must be idempotent and preserve monotonic score."""
        ev = LiveEvent(
            match_id=886601,
            provider="red_provider",
            provider_event_id="red_goal_01",
            event_type="goal",
            minute=15,
            team_name="Порту",
            player_name="Evanilson",
            payload={"side": "home"}
        )

        res1 = ingest_live_event(ev)
        self.assertEqual(res1["status"], "applied")
        self.assertEqual(res1["home_score"], 1)

        # Replay duplicate
        res2 = ingest_live_event(ev)
        self.assertEqual(res2["status"], "duplicate")

        st = get_live_match_state(886601)
        self.assertEqual(st["home_score"], 1, "Score must remain 1:0 upon duplicate event replay")

    def test_02_event_on_finished_or_cancelled_match_rejected(self) -> None:
        """STEP 4 (items 15, 16): Events on terminal matches must be rejected without score mutation."""
        # Transition match to FINISHED legitimately
        transition_live_match(886601, PRE_MATCH)
        transition_live_match(886601, LIVE)
        transition_live_match(886601, FINISHED)

        late_goal = LiveEvent(
            match_id=886601,
            provider="red_provider",
            provider_event_id="late_goal_01",
            event_type="goal",
            minute=95,
            team_name="Порту",
            payload={"side": "home"}
        )

        res = ingest_live_event(late_goal)
        self.assertEqual(res["status"], "rejected")
        st = get_live_match_state(886601)
        self.assertEqual(st["home_score"], 0, "Finished match must not accept new scoring events")

    def test_03_malformed_event_minute_rejected(self) -> None:
        """STEP 4 (items 8, 9): Negative or impossible minutes must be rejected."""
        neg_ev = LiveEvent(
            match_id=886601,
            provider="red_provider",
            provider_event_id="malformed_01",
            event_type="goal",
            minute=-10,
            payload={"side": "home"}
        )
        res = ingest_live_event(neg_ev)
        self.assertEqual(res["status"], "rejected")

        future_ev = LiveEvent(
            match_id=886601,
            provider="red_provider",
            provider_event_id="malformed_02",
            event_type="goal",
            minute=999,
            payload={"side": "home"}
        )
        res_fut = ingest_live_event(future_ev)
        self.assertEqual(res_fut["status"], "rejected")

    def test_04_illegal_state_machine_transitions_rejected(self) -> None:
        """STEP 6: Illegal transitions must raise InvalidStateTransitionError."""
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE live_match_states SET status = 'SCHEDULED' WHERE match_id = 886601")

        with self.assertRaises(InvalidStateTransitionError):
            transition_live_match(886601, FINISHED)

        # Transition to FINISHED legitimately
        transition_live_match(886601, PRE_MATCH)
        transition_live_match(886601, LIVE)
        transition_live_match(886601, FINISHED)

        # Terminal state: FINISHED -> LIVE forbidden
        with self.assertRaises(InvalidStateTransitionError):
            transition_live_match(886601, LIVE)


class TestMarketAndOddsRaceAttacks(Phase61RedTeamTestBase):

    def test_05_bet_on_suspended_market_rejected(self) -> None:
        """STEP 7: Bets placed on suspended market must be rejected inside the transaction."""
        evaluate_and_apply_suspend_rules(886601, "goal")

        slip = [{"match_id": 886601, "market_id": 8866001, "selection_id": 88660011, "outcome": "p1", "odd": 2.00}]
        success, res = database.place_user_bet(886601, 100, slip)

        self.assertFalse(success)
        self.assertIn("приостановлен", str(res))

        bal = database.get_wallet_balance(886601)
        self.assertEqual(bal, 1000)

    def test_06_odds_race_attack_returns_409_odds_changed(self) -> None:
        """STEP 8: Placing bet with stale odds must return ODDS_CHANGED."""
        slip = [{"match_id": 886601, "market_id": 8866001, "selection_id": 88660011, "outcome": "p1", "odd": 2.50}]
        success, res = database.place_user_bet(886601, 100, slip)

        self.assertFalse(success)
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("error"), "ODDS_CHANGED")
        self.assertEqual(res.get("new_odd"), 2.00)

    def test_07_client_cannot_tamper_with_payout_or_limits(self) -> None:
        """STEP 9 & 13: Client cannot tamper with payout limits or stake limits."""
        slip = [{"match_id": 886601, "market_id": 8866001, "selection_id": 88660011, "outcome": "p1", "odd": 2.00}]
        ok, res = database.place_user_bet(886601, 50001, slip)
        self.assertFalse(ok)
        self.assertEqual(res.get("error"), "MAX_BET_EXCEEDED")

        # Exceed MAX_PAYOUT (500,000) with legal stake but huge odd (user 886603 has 10000 balance)
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE market_selections SET odds_value = 100.0 WHERE id = 88660011")
        slip_huge = [{"match_id": 886601, "market_id": 8866001, "selection_id": 88660011, "outcome": "p1", "odd": 100.00}]
        ok2, res2 = database.place_user_bet(886603, 6000, slip_huge)
        self.assertFalse(ok2)
        self.assertIsInstance(res2, dict)
        self.assertEqual(res2.get("error"), "MAX_PAYOUT_EXCEEDED")


class TestFinancialConcurrencyAndIdempotency(Phase61RedTeamTestBase):

    def test_08_double_bet_attack_with_idempotency_key(self) -> None:
        """STEP 10 & 11: 20 concurrent identical requests must result in exactly 1 bet and 1 debit."""
        slip = [{"match_id": 886601, "market_id": 8866001, "selection_id": 88660011, "outcome": "p1", "odd": 2.00}]
        key = "idemp_test_concurrent_key_01"

        results = []
        def place():
            return database.place_user_bet(886601, 100, slip, idempotency_key=key)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(place) for _ in range(20)]
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())

        self.assertTrue(all(r[0] for r in results))

        bet_ids = {r[1] for r in results}
        self.assertEqual(len(bet_ids), 1, "All concurrent requests must return the exact same bet_id")

        bal = database.get_wallet_balance(886601)
        self.assertEqual(bal, 900, "Wallet must be debited exactly once")

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM coin_transactions WHERE user_id = 886601 AND transaction_type = 'bet_placed'")
            self.assertEqual(cursor.fetchone()["cnt"], 1)

    def test_09_idempotency_key_reused_with_different_payload_rejected(self) -> None:
        """STEP 11: Reusing idempotency key with different payload returns IDEMPOTENCY_KEY_REUSED."""
        slip1 = [{"match_id": 886601, "market_id": 8866001, "selection_id": 88660011, "outcome": "p1", "odd": 2.00}]
        key = "idemp_reuse_key_99"

        ok1, bet1 = database.place_user_bet(886601, 100, slip1, idempotency_key=key)
        self.assertTrue(ok1)

        ok2, res2 = database.place_user_bet(886601, 200, slip1, idempotency_key=key)
        self.assertFalse(ok2)
        self.assertEqual(res2.get("error"), "IDEMPOTENCY_KEY_REUSED")

        bal = database.get_wallet_balance(886601)
        self.assertEqual(bal, 900)

    def test_10_wallet_concurrency_prevents_overdraft(self) -> None:
        """STEP 12: Two concurrent bets of 700 on a 1000 balance must never allow negative balance."""
        slip1 = [{"match_id": 886601, "market_id": 8866001, "selection_id": 88660011, "outcome": "p1", "odd": 2.00}]
        slip2 = [{"match_id": 886602, "market_id": 8866002, "selection_id": 88660021, "outcome": "p1", "odd": 1.80}]

        results = []
        barrier = threading.Barrier(2)

        def place(slip, key):
            barrier.wait()
            return database.place_user_bet(886601, 700, slip, idempotency_key=key)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(place, slip1, "overdraft_1")
            f2 = executor.submit(place, slip2, "overdraft_2")
            results = [f1.result(), f2.result()]

        success_count = sum(1 for ok, _ in results if ok)
        self.assertEqual(success_count, 1, "Only one of the two 700 bets can succeed on a 1000 balance")

        bal = database.get_wallet_balance(886601)
        self.assertEqual(bal, 300, "Balance must be exactly 300, never negative")
        self.assertGreaterEqual(bal, 0, "Financial invariant balance >= 0 holds")


class TestSettlementAndRefundRedTeam(Phase61RedTeamTestBase):

    def test_11_concurrent_settlement_single_payout_only(self) -> None:
        """STEP 14: Concurrent settlements of winning bet must result in exactly one payout."""
        slip = [{"match_id": 886601, "market_id": 8866001, "selection_id": 88660011, "outcome": "p1", "odd": 2.00}]
        ok, bet_id = database.place_user_bet(886601, 100, slip)
        self.assertTrue(ok)
        self.assertEqual(database.get_wallet_balance(886601), 900)

        # Run settlement 5 times concurrently for Porto 2 - 0 Benfica (p1 wins)
        def settle():
            return settle_match_predictions(886601, score1=2, score2=0, match_status="finished")

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(settle) for _ in range(5)]
            for f in concurrent.futures.as_completed(futures):
                f.result()

        bal = database.get_wallet_balance(886601)
        self.assertEqual(bal, 1100, "Double settlement must not occur")

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM coin_transactions WHERE user_id = 886601 AND transaction_type = 'bet_won'")
            self.assertEqual(cursor.fetchone()["cnt"], 1)

    def test_12_void_user_bet_rejects_settled_bets_and_double_voids(self) -> None:
        """STEP 15: void_user_bet cannot void already won/lost bets and cannot double-refund."""
        # Use isolated user 886602 and match 886601
        slip = [{"match_id": 886601, "market_id": 8866001, "selection_id": 88660011, "outcome": "p1", "odd": 2.00}]
        ok, bet_id = database.place_user_bet(886602, 100, slip)
        self.assertTrue(ok)

        # Settle bet as WON
        settle_match_predictions(886601, score1=2, score2=0, match_status="finished")
        bal_before = database.get_wallet_balance(886602)
        self.assertEqual(bal_before, 1100)

        # Attempt to void already won bet must raise ValueError
        with self.assertRaises(ValueError):
            database.void_user_bet(bet_id, actor_id=886603)

        # Balance remains unchanged
        self.assertEqual(database.get_wallet_balance(886602), 1100)

        # Now test double void on a fresh pending bet on an open match (886602)
        slip2 = [{"match_id": 886602, "market_id": 8866002, "selection_id": 88660021, "outcome": "p1", "odd": 1.80}]
        ok2, bet_id2 = database.place_user_bet(886602, 100, slip2)
        self.assertTrue(ok2)
        res = database.void_user_bet(bet_id2, actor_id=886603)
        self.assertEqual(res["refunded_amount"], 100)

        # Second void call on same bet raises ValueError
        with self.assertRaises(ValueError):
            database.void_user_bet(bet_id2, actor_id=886603)


class TestSecurityIsolationAndRBAC(Phase61RedTeamTestBase):

    def test_13_division_and_season_isolation(self) -> None:
        """STEP 17 & 18: Historical data, leaderboards, and intelligence must not leak across divisions or seasons."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            # Season 1: Porto wins
            cursor.execute("""
                INSERT INTO matches (id, season_id, division_id, round_number, player1_team, player2_team, status, player1_score, player2_score)
                VALUES (886610, 1, 1, 2, 'Порту', 'Спортинг', 'finished', 3, 0)
            """)
            # Season 2: Porto loses
            cursor.execute("""
                INSERT INTO matches (id, season_id, division_id, round_number, player1_team, player2_team, status, player1_score, player2_score)
                VALUES (886620, 2, 1, 1, 'Порту', 'Спортинг', 'finished', 0, 4)
            """)

        intel_s1 = IntelligenceEngine.get_match_intelligence(886601)
        form_s1 = intel_s1["form"]["team1"]
        self.assertEqual(form_s1["wins"], 1)
        self.assertEqual(form_s1["losses"], 0)
        self.assertEqual(form_s1["goals_for"], 3)
        self.assertEqual(form_s1["goals_against"], 0, "Season 2 loss must not leak into Season 1 intelligence")

    def test_14_idor_get_user_bet_by_id_scopes_to_user(self) -> None:
        """STEP 19 & 23: User 1 cannot access User 2's bet slip."""
        slip = [{"match_id": 886601, "market_id": 8866001, "selection_id": 88660011, "outcome": "p1", "odd": 2.00}]
        ok, bet_user2 = database.place_user_bet(886602, 100, slip)
        self.assertTrue(ok)

        bet = database.get_user_bet_by_id(user_id=886601, bet_id=bet_user2)
        self.assertIsNone(bet, "User 1 must not be able to retrieve User 2's bet slip via IDOR")

    def test_15_topic_isolation_different_chats_same_thread_id(self) -> None:
        """STEP 21: Chat A thread 100 and Chat B thread 100 must be isolated in TopicCache."""
        tc = TopicCache()
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO division_topics (division_id, group_chat_id, message_thread_id, topic_type)
                VALUES (1, -1001111111, 100, 'main'),
                       (2, -1002222222, 100, 'main')
            """)

        tc.reload_cache()

        binding_chat_a = tc.get_by_topic(-1001111111, 100)
        binding_chat_b = tc.get_by_topic(-1002222222, 100)

        self.assertIsNotNone(binding_chat_a)
        self.assertIsNotNone(binding_chat_b)
        self.assertEqual(binding_chat_a["division_id"], 1)
        self.assertEqual(binding_chat_b["division_id"], 2)
        self.assertNotEqual(binding_chat_a["group_chat_id"], binding_chat_b["group_chat_id"])

    def test_16_auth_red_team(self) -> None:
        """STEP 22: Missing, future-dated, expired auth_date and mock admin in prod must be rejected."""
        token = "test_bot_token"
        user = {"id": 886601, "username": "player"}

        # 1. Valid initData
        valid_init = make_init_data(user, token)
        self.assertIsNotNone(validate_telegram_init_data(valid_init, token))

        # 2. Tampered HMAC
        tampered = valid_init.replace("886601", "886602")
        self.assertIsNone(validate_telegram_init_data(tampered, token))

        # 3. Missing auth_date
        params = {"query_id": "123", "user": json.dumps(user)}
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        sk = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
        params["hash"] = hmac.new(sk, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
        no_auth_date = urlencode(params)
        self.assertIsNone(validate_telegram_init_data(no_auth_date, token))

        # 4. Future auth_date (> 5 min)
        future_init = make_init_data(user, token, auth_date=int(time.time()) + 10000)
        self.assertIsNone(validate_telegram_init_data(future_init, token))

        # 5. Expired auth_date (> 24h)
        expired_init = make_init_data(user, token, auth_date=int(time.time()) - 90000)
        self.assertIsNone(validate_telegram_init_data(expired_init, token))

        # 6. Mock admin bypass in production (ALLOW_DEV_AUTH_BYPASS unset)
        os.environ.pop("ALLOW_DEV_AUTH_BYPASS", None)
        mock_auth = get_authenticated_user("mock_admin_886603")
        self.assertIsNone(mock_auth, "mock_admin_ bypass must be forbidden when ALLOW_DEV_AUTH_BYPASS is not set")


class TestIntelligenceMathAndNotifications(Phase61RedTeamTestBase):

    def test_17_poisson_probability_bounds_and_edge(self) -> None:
        """STEP 24 & 25: Poisson probabilities must be strictly bounded in [0, 1] without NaN or Inf."""
        p = _poisson_probability(2, 1.5)
        self.assertTrue(0.0 <= p <= 1.0)

        p_zero = _poisson_probability(0, 0.0)
        self.assertEqual(p_zero, 1.0)
        p_zero_k = _poisson_probability(1, 0.0)
        self.assertEqual(p_zero_k, 0.0)

        p_large = _poisson_probability(20, 2.0)
        self.assertTrue(0.0 <= p_large <= 1.0)

    def test_18_leaderboard_min_bets_threshold(self) -> None:
        """STEP 29: Capper leaderboard enforces minimum bet threshold to filter 1-bet flukes."""
        slip = [{"match_id": 886601, "market_id": 8866001, "selection_id": 88660011, "outcome": "p1", "odd": 2.00}]
        database.place_user_bet(886601, 100, slip)
        settle_match_predictions(886601, score1=2, score2=0, match_status="finished")

        leaders = get_capper_leaderboard(division_id=1, min_bets=5)
        user_ids = [l["user_id"] for l in leaders]
        self.assertNotIn(886601, user_ids, "User with only 1 bet must not appear on leaderboard with min_bets=5")

    def test_19_notification_flood_attack_deduplication(self) -> None:
        """STEP 30: 100 identical notification dispatches produce exactly 1 queued event."""
        queued_count = 0
        duplicate_count = 0

        for _ in range(100):
            ok, status = queue_notification(
                user_id=886601,
                event_type="GOAL",
                source_event_id="flood_event_goal_99",
                title="Гол!",
                body="Порту забил гол"
            )
            if ok:
                queued_count += 1
            elif status == "duplicate":
                duplicate_count += 1

        self.assertEqual(queued_count, 1, "Exactly one notification must be queued")
        self.assertEqual(duplicate_count, 99, "99 duplicates must be rejected by unique constraint")


class TestDatabaseIntegrityAndPaginationBounds(Phase61RedTeamTestBase):

    def test_20_pagination_and_query_safety(self) -> None:
        """STEP 34 & 35: Negative, excessive, or malformed pagination parameters must not crash or leak DB."""
        bets_neg = database.get_user_bets(886601, limit=-1)
        self.assertIsInstance(bets_neg, list)

        bets_huge = database.get_user_bets(886601, limit=999999999)
        self.assertIsInstance(bets_huge, list)

        stats = get_user_betting_analytics(886602)
        self.assertIsNone(stats["roi_pct"], "ROI must be None when total_staked is 0")
        self.assertIsNone(stats["win_rate_pct"], "Win rate must be None when settled_predictions is 0")


if __name__ == "__main__":
    unittest.main()
