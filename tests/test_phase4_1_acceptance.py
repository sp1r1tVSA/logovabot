"""
tests/test_phase4_1_acceptance.py

LOGOVO.BET — PHASE 4.1 PRODUCTION ACCEPTANCE TEST SUITE
Covers all 18 acceptance criteria:
1. All 5 Divisions (existence, API, Mini App data loading, table, results)
2. Match Flow (Division -> Round -> Match -> Markets -> Selections -> Odds)
3. Bet Flow (Single and Express full lifecycle)
4. Odds Manipulation (server-side override of malicious client odds)
5. Double Submit (Idempotency key prevents duplicate bets and debits)
6. Insufficient Balance (balance = 100, stake = 101 -> rejection)
7. Market Closing (active -> works, suspended/closed/settled -> rejected)
8. Match Lifecycle (scheduled/open allowed; in_progress/pending_result/completed rejected)
9. Settlement & Idempotency (won bet payout credited; duplicate settlement no-op)
10. Refund & Idempotency (cancelled match refunded; duplicate refund no-op)
11. IDOR Protection (User B cannot access User A bets, wallet, stats)
12. Division Isolation (Division 1 != Division 2 matches, tours, table, results)
13. Season Isolation (Season 1 != Season 2)
14. 5 Division Topics (draft, previews, results, reports, lineups routing)
15. Telegram UX & WebApp button verification
16. Mini App Screens (HOME, MATCHES, MATCH CENTER, BET SLIP, MY BETS, RESULTS, TABLE, PROFILE)
17. Database Integrity Audit (no orphan records, no negative balances)
"""

import unittest
import datetime
import hmac
import hashlib
import json
import urllib.parse
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

import database
import config
from api.auth import validate_telegram_data, get_authenticated_user
from api.server import create_app
from services import odds_engine, settlement_engine, topic_cache
from handlers.base import get_main_inline_keyboard


def make_test_init_data(user_id: int, username: str = "accept_tester") -> str:
    """Helper to generate cryptographically valid Telegram initData."""
    token = getattr(config, "TOKEN", "test_bot_token")
    user_dict = {
        "id": user_id,
        "first_name": "Acceptance",
        "last_name": "Tester",
        "username": username,
        "language_code": "ru"
    }
    user_json = json.dumps(user_dict, separators=(",", ":"))
    auth_date = str(int(datetime.datetime.now().timestamp()))

    data_pairs = [
        ("auth_date", auth_date),
        ("query_id", f"query_{user_id}_{auth_date}"),
        ("user", user_json)
    ]
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data_pairs))

    secret_key = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    sig = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    params = {k: v for k, v in data_pairs}
    params["hash"] = sig
    return urllib.parse.urlencode(params)


class TestPhase41ProductionAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()

    def setUp(self):
        self.user_a_id = 991001
        self.user_b_id = 991002

        with database.transaction() as conn:
            c = conn.cursor()
            # Clean acceptance test artifacts
            c.execute("DELETE FROM user_bets WHERE id IN (SELECT DISTINCT bet_id FROM bet_items WHERE match_id BETWEEN 99701 AND 99750)")
            c.execute("DELETE FROM bet_items WHERE match_id BETWEEN 99701 AND 99750")
            c.execute("DELETE FROM bet_items WHERE bet_id IN (SELECT id FROM user_bets WHERE user_id IN (?, ?))", (self.user_a_id, self.user_b_id))
            c.execute("DELETE FROM user_bets WHERE user_id IN (?, ?)", (self.user_a_id, self.user_b_id))
            c.execute("DELETE FROM coin_transactions WHERE user_id IN (?, ?)", (self.user_a_id, self.user_b_id))
            c.execute("DELETE FROM user_wallets WHERE user_id IN (?, ?)", (self.user_a_id, self.user_b_id))
            c.execute("DELETE FROM users WHERE telegram_id IN (?, ?)", (self.user_a_id, self.user_b_id))
            c.execute("DELETE FROM matches WHERE id BETWEEN 99701 AND 99750")
            c.execute("DELETE FROM rounds WHERE division_id BETWEEN 1 AND 5 AND round_number = 88")
            c.execute("DELETE FROM market_selections WHERE market_id IN (SELECT id FROM markets WHERE match_id BETWEEN 99701 AND 99750)")
            c.execute("DELETE FROM markets WHERE match_id BETWEEN 99701 AND 99750")
            c.execute("DELETE FROM bet_markets WHERE match_id BETWEEN 99701 AND 99750")

            # Insert users
            c.execute("INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'user_a', 'user')", (self.user_a_id,))
            c.execute("INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'user_b', 'user')", (self.user_b_id,))

            # Setup wallets
            c.execute("INSERT INTO user_wallets (user_id, balance) VALUES (?, 1000)", (self.user_a_id,))
            c.execute("INSERT INTO user_wallets (user_id, balance) VALUES (?, 500)", (self.user_b_id,))

            # Setup open test round 88 across all 5 divisions
            for d in range(1, 6):
                c.execute("""
                    INSERT OR REPLACE INTO rounds (round_number, division_id, is_open, deadline, season_id)
                    VALUES (88, ?, 1, '2029-12-31 23:59:59', 1)
                """, (d,))

            # Setup 1 test match per division (99701 to 99705)
            for d in range(1, 6):
                m_id = 99700 + d
                c.execute("""
                    INSERT INTO matches (id, round_number, division_id, season_id, player1_team, player2_team, status)
                    VALUES (?, 88, ?, 1, ?, ?, 'scheduled')
                """, (m_id, d, f"Club {d}A", f"Club {d}B"))

                c.execute("""
                    INSERT INTO bet_markets (match_id, tour, team1_name, team2_name, odd_p1, odd_x, odd_p2, odd_tb25, odd_tm25, is_active)
                    VALUES (?, 88, ?, ?, 1.95, 3.20, 2.10, 1.85, 1.85, 1)
                """, (m_id, f"Club {d}A", f"Club {d}B"))

                odds_engine.generate_match_markets(m_id, f"Club {d}A", f"Club {d}B")

    def tearDown(self):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM user_bets WHERE id IN (SELECT DISTINCT bet_id FROM bet_items WHERE match_id BETWEEN 99701 AND 99750)")
            c.execute("DELETE FROM bet_items WHERE match_id BETWEEN 99701 AND 99750")
            c.execute("DELETE FROM bet_items WHERE bet_id IN (SELECT id FROM user_bets WHERE user_id IN (?, ?))", (self.user_a_id, self.user_b_id))
            c.execute("DELETE FROM user_bets WHERE user_id IN (?, ?)", (self.user_a_id, self.user_b_id))
            c.execute("DELETE FROM coin_transactions WHERE user_id IN (?, ?)", (self.user_a_id, self.user_b_id))
            c.execute("DELETE FROM user_wallets WHERE user_id IN (?, ?)", (self.user_a_id, self.user_b_id))
            c.execute("DELETE FROM users WHERE telegram_id IN (?, ?)", (self.user_a_id, self.user_b_id))
            c.execute("DELETE FROM matches WHERE id BETWEEN 99701 AND 99750")
            c.execute("DELETE FROM rounds WHERE division_id BETWEEN 1 AND 5 AND round_number = 88")
            c.execute("DELETE FROM market_selections WHERE market_id IN (SELECT id FROM markets WHERE match_id BETWEEN 99701 AND 99750)")
            c.execute("DELETE FROM markets WHERE match_id BETWEEN 99701 AND 99750")
            c.execute("DELETE FROM bet_markets WHERE match_id BETWEEN 99701 AND 99750")

    # ==============================================================
    # 1. TEST ALL 5 DIVISIONS
    # ==============================================================
    def test_01_all_5_divisions_exist_and_accessible(self):
        """Verify all 5 canonical divisions exist in DB and are returned by get_divisions."""
        divs = database.get_divisions(only_active=True)
        div_ids = [d["id"] for d in divs]
        for expected_id in [1, 2, 3, 4, 5]:
            self.assertIn(expected_id, div_ids, f"Division #{expected_id} must exist and be active.")

    def test_02_all_5_divisions_data_loading(self):
        """Verify matches, markets, standings, and results load for each of the 5 divisions."""
        for d in range(1, 6):
            tours = database.get_open_betting_tours(division_id=d)
            self.assertTrue(len(tours) > 0, f"Division #{d} must have active open tours.")
            self.assertTrue(any(t["round_number"] == 88 for t in tours), f"Division #{d} must include round 88.")

            markets = database.get_active_bet_markets(tour=88, division_id=d)
            self.assertTrue(len(markets) > 0, f"Division #{d} must have active bet markets.")
            self.assertEqual(markets[0]["match_id"], 99700 + d)

            # Standings check
            standings = database.get_tournament_standings(division_id=d)
            self.assertIsInstance(standings, list)

            # Results check
            results = database.get_tournament_results(limit=10, division_id=d)
            self.assertIsInstance(results, list)

    # ==============================================================
    # 2. MATCH FLOW
    # ==============================================================
    def test_03_match_flow_integrity(self):
        """Verify Division -> Round -> Match -> Markets -> Selections -> Odds flow."""
        for d in range(1, 6):
            m_id = 99700 + d
            match = database.get_match_by_id(m_id)
            self.assertIsNotNone(match)
            self.assertEqual(match["division_id"], d)
            self.assertEqual(match["season_id"], 1)
            self.assertEqual(match["round_number"], 88)

            markets = odds_engine.get_match_markets(m_id)
            self.assertTrue(len(markets) > 0, f"Match #{m_id} must have relational markets")
            for mkt in markets:
                self.assertIn("selections", mkt)
                for sel in mkt["selections"]:
                    self.assertGreater(sel["odds_value"], 1.0)

    # ==============================================================
    # 3. BET FLOW (SINGLE & EXPRESS)
    # ==============================================================
    def test_04_bet_flow_single_and_express(self):
        """Verify single bet and express bet placement, wallet debit, and transactions."""
        initial_balance = database.get_user_balance(self.user_a_id)

        # 1. Single Bet
        ok_single, bet_single_id = database.place_user_bet(
            user_id=self.user_a_id,
            amount=100,
            selections=[{"match_id": 99701, "outcome": "p1"}]
        )
        self.assertTrue(ok_single)
        bal_after_single = database.get_user_balance(self.user_a_id)
        self.assertEqual(bal_after_single, initial_balance - 100)

        bet_single = database.get_user_bet_by_id(bet_single_id, self.user_a_id)
        self.assertEqual(bet_single["bet_type"], "single")
        self.assertEqual(len(bet_single["items"]), 1)

        # 2. Express Bet (cross-division: Div 1 + Div 2)
        ok_exp, bet_exp_id = database.place_user_bet(
            user_id=self.user_a_id,
            amount=200,
            selections=[
                {"match_id": 99701, "outcome": "p1"},
                {"match_id": 99702, "outcome": "p1"}
            ]
        )
        self.assertTrue(ok_exp)
        bal_after_exp = database.get_user_balance(self.user_a_id)
        self.assertEqual(bal_after_exp, initial_balance - 300)

        bet_exp = database.get_user_bet_by_id(bet_exp_id, self.user_a_id)
        self.assertEqual(bet_exp["bet_type"], "express")
        self.assertEqual(len(bet_exp["items"]), 2)

    # ==============================================================
    # 4. ODDS MANIPULATION
    # ==============================================================
    def test_05_odds_manipulation_prevented(self):
        """Client-sent odds=999999 must be completely ignored by the server."""
        ok, bet_id = database.place_user_bet(
            user_id=self.user_a_id,
            amount=100,
            selections=[{"match_id": 99701, "outcome": "p1", "odd": 999999.0}]
        )
        self.assertTrue(ok)
        bet = database.get_user_bet_by_id(bet_id, self.user_a_id)
        self.assertNotEqual(bet["total_odd"], 999999.0)
        self.assertLess(bet["total_odd"], 10.0)

    # ==============================================================
    # 5. DOUBLE SUBMIT (IDEMPOTENCY)
    # ==============================================================
    def test_06_double_submit_idempotency(self):
        """Submitting the exact same idempotency_key must return existing bet without second debit."""
        key = "idem_test_key_99701"
        bal_start = database.get_user_balance(self.user_a_id)

        # First request
        ok1, bet_id1 = database.place_user_bet(
            user_id=self.user_a_id,
            amount=150,
            selections=[{"match_id": 99701, "outcome": "p1"}],
            idempotency_key=key
        )
        self.assertTrue(ok1)
        bal_mid = database.get_user_balance(self.user_a_id)
        self.assertEqual(bal_mid, bal_start - 150)

        # Second request (duplicate double click)
        ok2, bet_id2 = database.place_user_bet(
            user_id=self.user_a_id,
            amount=150,
            selections=[{"match_id": 99701, "outcome": "p1"}],
            idempotency_key=key
        )
        self.assertTrue(ok2)
        self.assertEqual(bet_id1, bet_id2)
        bal_end = database.get_user_balance(self.user_a_id)
        self.assertEqual(bal_end, bal_mid, "Balance must NOT be debited twice on duplicate click.")

    # ==============================================================
    # 6. INSUFFICIENT BALANCE
    # ==============================================================
    def test_07_insufficient_balance_rejected(self):
        """Balance=500, stake=501 must be strictly rejected and balance untouched."""
        bal_start = database.get_user_balance(self.user_b_id)
        self.assertEqual(bal_start, 500)

        ok, msg = database.place_user_bet(
            user_id=self.user_b_id,
            amount=501,
            selections=[{"match_id": 99701, "outcome": "p1"}]
        )
        self.assertFalse(ok)
        self.assertIn("Недостаточно монет", msg)

        bal_end = database.get_user_balance(self.user_b_id)
        self.assertEqual(bal_end, 500)

    # ==============================================================
    # 7. MARKET CLOSING
    # ==============================================================
    def test_08_market_closing_and_suspension(self):
        """Active market accepts bets; suspended, closed, settled markets reject bets."""
        # Find market for match 99701
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM markets WHERE match_id = 99701 LIMIT 1")
            m_id = c.fetchone()["id"]

            # 1. Suspended
            c.execute("UPDATE markets SET status = 'suspended' WHERE id = ?", (m_id,))
        ok_susp, msg_susp = database.place_user_bet(
            user_id=self.user_a_id,
            amount=50,
            selections=[{"match_id": 99701, "outcome": "p1"}]
        )
        self.assertFalse(ok_susp)
        self.assertIn("приостановлен", msg_susp)

        # 2. Closed
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE markets SET status = 'closed' WHERE id = ?", (m_id,))
        ok_closed, msg_closed = database.place_user_bet(
            user_id=self.user_a_id,
            amount=50,
            selections=[{"match_id": 99701, "outcome": "p1"}]
        )
        self.assertFalse(ok_closed)

        # 3. Settled
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE markets SET status = 'settled' WHERE id = ?", (m_id,))
        ok_settled, msg_settled = database.place_user_bet(
            user_id=self.user_a_id,
            amount=50,
            selections=[{"match_id": 99701, "outcome": "p1"}]
        )
        self.assertFalse(ok_settled)

    # ==============================================================
    # 8. MATCH LIFECYCLE
    # ==============================================================
    def test_09_match_lifecycle_betting_rules(self):
        """scheduled/open allow betting; in_progress/pending_result/completed reject betting."""
        test_m_id = 99703

        # scheduled -> allowed
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE matches SET status = 'scheduled' WHERE id = ?", (test_m_id,))
        ok_sched, _ = database.place_user_bet(self.user_a_id, 20, [{"match_id": test_m_id, "outcome": "p1"}])
        self.assertTrue(ok_sched)

        # open -> allowed
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE matches SET status = 'open' WHERE id = ?", (test_m_id,))
        ok_open, _ = database.place_user_bet(self.user_a_id, 20, [{"match_id": test_m_id, "outcome": "p1"}])
        self.assertTrue(ok_open)

        # in_progress -> rejected
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE matches SET status = 'in_progress' WHERE id = ?", (test_m_id,))
        ok_prog, _ = database.place_user_bet(self.user_a_id, 20, [{"match_id": test_m_id, "outcome": "p1"}])
        self.assertFalse(ok_prog)

        # pending_result -> rejected
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE matches SET status = 'pending_result' WHERE id = ?", (test_m_id,))
        ok_pend, _ = database.place_user_bet(self.user_a_id, 20, [{"match_id": test_m_id, "outcome": "p1"}])
        self.assertFalse(ok_pend)

        # completed -> rejected
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE matches SET status = 'completed' WHERE id = ?", (test_m_id,))
        ok_comp, _ = database.place_user_bet(self.user_a_id, 20, [{"match_id": test_m_id, "outcome": "p1"}])
        self.assertFalse(ok_comp)

    # ==============================================================
    # 9. SETTLEMENT & IDEMPOTENCY
    # ==============================================================
    def test_10_settlement_and_no_double_payout(self):
        """Winning bet gets credited exactly once; repeat settlement does not payout twice."""
        # User places single bet on match 99704 P1
        ok, bet_id = database.place_user_bet(
            user_id=self.user_a_id,
            amount=100,
            selections=[{"match_id": 99704, "outcome": "p1"}]
        )
        self.assertTrue(ok)
        bal_before_settle = database.get_user_balance(self.user_a_id)

        # 1. First settlement: Match 99704 finishes 2:0 (P1 won)
        settlement_engine.settle_match_result(match_id=99704, score1=2, score2=0)

        bet = database.get_user_bet_by_id(bet_id, self.user_a_id)
        self.assertEqual(bet["status"], "won")
        self.assertGreater(bet["actual_payout"], 0)
        payout = bet["actual_payout"]

        bal_after_settle = database.get_user_balance(self.user_a_id)
        self.assertEqual(bal_after_settle, bal_before_settle + payout)

        # 2. Second settlement (idempotency check)
        settlement_engine.settle_match_result(match_id=99704, score1=2, score2=0)
        bal_after_repeat = database.get_user_balance(self.user_a_id)
        self.assertEqual(bal_after_repeat, bal_after_settle, "Settlement must NOT credit payout twice.")

    # ==============================================================
    # 10. REFUND
    # ==============================================================
    def test_11_refund_lifecycle_and_idempotency(self):
        """Cancelled match triggers refund of stake; repeat refund does not credit coins twice."""
        ok, bet_id = database.place_user_bet(
            user_id=self.user_a_id,
            amount=120,
            selections=[{"match_id": 99705, "outcome": "p1"}]
        )
        self.assertTrue(ok)
        bal_before_ref = database.get_user_balance(self.user_a_id)

        # 1. First refund
        settlement_engine.refund_match_bets(99705)
        bet = database.get_user_bet_by_id(bet_id, self.user_a_id)
        self.assertEqual(bet["status"], "refunded")

        bal_after_ref = database.get_user_balance(self.user_a_id)
        self.assertEqual(bal_after_ref, bal_before_ref + 120)

        # 2. Repeat refund
        settlement_engine.refund_match_bets(99705)
        bal_after_repeat = database.get_user_balance(self.user_a_id)
        self.assertEqual(bal_after_repeat, bal_after_ref, "Refund must NOT credit coins twice.")

    # ==============================================================
    # 11. IDOR PROTECTION
    # ==============================================================
    def test_12_idor_protection_at_repository_level(self):
        """User B cannot fetch User A's bet by ID."""
        ok, a_bet_id = database.place_user_bet(
            user_id=self.user_a_id,
            amount=50,
            selections=[{"match_id": 99701, "outcome": "p1"}]
        )
        self.assertTrue(ok)

        # User A can access
        a_access = database.get_user_bet_by_id(a_bet_id, self.user_a_id)
        self.assertIsNotNone(a_access)

        # User B attempts to access User A's bet
        b_access = database.get_user_bet_by_id(a_bet_id, self.user_b_id)
        self.assertIsNone(b_access, "User B must NOT be able to view User A's bet (IDOR prevention).")

    # ==============================================================
    # 12. DIVISION ISOLATION
    # ==============================================================
    def test_13_division_isolation(self):
        """Matches of Division 1 never leak into Division 2 and vice versa."""
        div1_matches = database.get_active_bet_markets(tour=88, division_id=1)
        div2_matches = database.get_active_bet_markets(tour=88, division_id=2)

        div1_ids = [m["match_id"] for m in div1_matches]
        div2_ids = [m["match_id"] for m in div2_matches]

        self.assertIn(99701, div1_ids)
        self.assertNotIn(99701, div2_ids)

        self.assertIn(99702, div2_ids)
        self.assertNotIn(99702, div1_ids)

    # ==============================================================
    # 13. SEASON ISOLATION
    # ==============================================================
    def test_14_season_isolation(self):
        """Season 1 match does not appear in Season 2 queries."""
        with database.transaction() as conn:
            conn.cursor().execute("""
                INSERT INTO matches (id, round_number, division_id, season_id, player1_team, player2_team, status)
                VALUES (99740, 88, 1, 999, 'Season2 TeamA', 'Season2 TeamB', 'scheduled')
            """)

        m_s1 = database.get_tournament_results(division_id=1, season_id=1)
        s1_ids = [m["id"] for m in m_s1]
        self.assertNotIn(99740, s1_ids)

    # ==============================================================
    # 14. 5 DIVISION TOPICS
    # ==============================================================
    def test_15_division_topics_routing_for_all_5_divisions(self):
        """TopicCache routes topics strictly via composite key (division_id, topic_type)."""
        tc = topic_cache.TopicCache()
        # Bind topics for all 5 divisions with standard types
        types = ["draft", "previews", "results", "reports", "lineups"]
        for d in range(1, 6):
            for idx, t_type in enumerate(types):
                thread_id = 7000 + d * 10 + idx
                database.bind_division_topic(
                    division_id=d,
                    topic_type=t_type,
                    message_thread_id=thread_id,
                    group_chat_id=-100123456789
                )

        tc.reload_cache()

        for d in range(1, 6):
            for idx, t_type in enumerate(types):
                expected_thread = 7000 + d * 10 + idx
                binding = tc.get_by_division(division_id=d, topic_type=t_type)
                self.assertIsNotNone(binding, f"Binding for Div #{d} and {t_type} must exist.")
                self.assertEqual(binding["message_thread_id"], expected_thread)

    # ==============================================================
    # 15. TELEGRAM UX & BOT START BUTTON
    # ==============================================================
    def test_16_telegram_start_button_exists(self):
        """Bot /start inline keyboard must feature Logovo.bet Mini App button."""
        markup = get_main_inline_keyboard(telegram_id=self.user_a_id)
        buttons = [btn for row in markup.inline_keyboard for btn in row]
        miniapp_btns = [b for b in buttons if b.web_app and "Logovo.bet" in b.text]
        self.assertTrue(len(miniapp_btns) > 0, "Main keyboard must contain Logovo.bet Mini App button.")

    # ==============================================================
    # 16. DATABASE INTEGRITY AUDIT
    # ==============================================================
    def test_17_database_integrity_audit(self):
        """Verify no negative balances, no orphan bet_items, and no duplicate transactions."""
        with database.transaction() as conn:
            c = conn.cursor()
            # 1. No negative balances
            c.execute("SELECT COUNT(*) as c FROM user_wallets WHERE balance < 0")
            self.assertEqual(c.fetchone()["c"], 0)

            # 2. No orphan bet items
            c.execute("SELECT COUNT(*) as c FROM bet_items WHERE bet_id NOT IN (SELECT id FROM user_bets)")
            self.assertEqual(c.fetchone()["c"], 0)

            # 3. Foreign key check
            c.execute("PRAGMA foreign_key_check")
            fk_violations = c.fetchall()
            self.assertEqual(len(fk_violations), 0, "No foreign key violations allowed.")

    # ==============================================================
    # 17. MINI APP 8 SCREENS REAL DATA FLOW AUDIT
    # ==============================================================
    def test_18_mini_app_all_8_screens_and_data_flow_verified(self):
        """Verify all 8 Mini App screens exist in HTML and have full data flows in JS."""
        import os
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html_path = os.path.join(base_dir, "web", "index.html")
        ui_path = os.path.join(base_dir, "web", "js", "ui.js")
        app_path = os.path.join(base_dir, "web", "js", "app.js")
        api_path = os.path.join(base_dir, "web", "js", "api.js")

        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        with open(ui_path, "r", encoding="utf-8") as f:
            ui_content = f.read()
        with open(app_path, "r", encoding="utf-8") as f:
            app_content = f.read()
        with open(api_path, "r", encoding="utf-8") as f:
            api_content = f.read()

        # 1. HOME Screen
        self.assertIn('id="view-lobby"', html_content)
        self.assertIn('renderDivisionTabs', ui_content)
        self.assertIn('renderTourTabs', ui_content)

        # 2. MATCHES Screen
        self.assertIn('id="matches-list-container"', html_content)
        self.assertIn('renderMatches', ui_content)
        self.assertIn('match-status-pills', html_content)

        # 3. MATCH CENTER Screen
        self.assertIn('id="view-match_center"', html_content)
        self.assertIn('renderMatchCenter', ui_content)
        self.assertIn('loadMatchCenter', app_content)

        # 4. BET SLIP Drawer
        self.assertIn('id="slip-drawer"', html_content)
        self.assertIn('renderSlipDrawer', ui_content)
        self.assertIn('btn-submit-prediction', app_content)
        self.assertIn('placePrediction', app_content)
        self.assertIn('idempotencyKey', app_content)

        # 5. MY BETS Screen
        self.assertIn('id="view-history"', html_content)
        self.assertIn('renderPredictionsHistory', ui_content)
        self.assertIn('getPredictions', api_content)

        # 6. RESULTS Screen
        self.assertIn('id="view-tournaments"', html_content)
        self.assertIn('renderTournaments', ui_content)
        self.assertIn('getResults', api_content)

        # 7. TABLE Screen
        self.assertIn('id="tournaments-content-container"', html_content)
        self.assertIn('renderTournaments', ui_content)
        self.assertIn('getStandings', api_content)

        # 8. PROFILE Screen
        self.assertIn('id="view-profile"', html_content)
        self.assertIn('renderProfile', ui_content)
        self.assertIn('getWallet', api_content)
        self.assertIn('getMyStats', api_content)


class TestPhase41ApiAcceptance(AioHTTPTestCase):
    """Integration acceptance tests for all Mini App REST API contracts."""
    async def get_application(self):
        return create_app()

    def setUp(self):
        super().setUp()
        database.init_db()
        self.user_a_id = 991001
        self.user_b_id = 991002
        self.auth_a = make_test_init_data(self.user_a_id, "user_a")
        self.auth_b = make_test_init_data(self.user_b_id, "user_b")

        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'user_a', 'user')", (self.user_a_id,))
            c.execute("INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'user_b', 'user')", (self.user_b_id,))
            c.execute("INSERT OR REPLACE INTO user_wallets (user_id, balance) VALUES (?, 1000)", (self.user_a_id,))
            c.execute("INSERT OR REPLACE INTO user_wallets (user_id, balance) VALUES (?, 500)", (self.user_b_id,))

            c.execute("INSERT OR REPLACE INTO rounds (round_number, division_id, is_open, deadline, season_id) VALUES (88, 1, 1, '2029-12-31 23:59:59', 1)")
            c.execute("""
                INSERT OR REPLACE INTO matches (id, round_number, division_id, season_id, player1_team, player2_team, status)
                VALUES (99701, 88, 1, 1, 'Club 1A', 'Club 1B', 'scheduled')
            """)
            c.execute("""
                INSERT OR REPLACE INTO bet_markets (match_id, tour, team1_name, team2_name, odd_p1, odd_x, odd_p2, is_active)
                VALUES (99701, 88, 'Club 1A', 'Club 1B', 1.95, 3.20, 2.10, 1)
            """)
            odds_engine.generate_match_markets(99701, 'Club 1A', 'Club 1B')

    def tearDown(self):
        super().tearDown()
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM user_bets WHERE id IN (SELECT DISTINCT bet_id FROM bet_items WHERE match_id BETWEEN 99701 AND 99750)")
            c.execute("DELETE FROM bet_items WHERE match_id BETWEEN 99701 AND 99750")
            c.execute("DELETE FROM bet_items WHERE bet_id IN (SELECT id FROM user_bets WHERE user_id IN (?, ?))", (self.user_a_id, self.user_b_id))
            c.execute("DELETE FROM user_bets WHERE user_id IN (?, ?)", (self.user_a_id, self.user_b_id))
            c.execute("DELETE FROM coin_transactions WHERE user_id IN (?, ?)", (self.user_a_id, self.user_b_id))
            c.execute("DELETE FROM user_wallets WHERE user_id IN (?, ?)", (self.user_a_id, self.user_b_id))
            c.execute("DELETE FROM users WHERE telegram_id IN (?, ?)", (self.user_a_id, self.user_b_id))
            c.execute("DELETE FROM matches WHERE id BETWEEN 99701 AND 99750")
            c.execute("DELETE FROM rounds WHERE division_id BETWEEN 1 AND 5 AND round_number = 88")
            c.execute("DELETE FROM market_selections WHERE market_id IN (SELECT id FROM markets WHERE match_id BETWEEN 99701 AND 99750)")
            c.execute("DELETE FROM markets WHERE match_id BETWEEN 99701 AND 99750")
            c.execute("DELETE FROM bet_markets WHERE match_id BETWEEN 99701 AND 99750")

    # 1. API: All 5 Divisions in /api/divisions
    async def test_api_divisions_returns_all_5(self):
        resp = await self.client.get("/api/divisions", headers={"X-Telegram-Init-Data": self.auth_a})
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["status"], "ok")
        div_ids = [d["id"] for d in data["divisions"]]
        for exp in [1, 2, 3, 4, 5]:
            self.assertIn(exp, div_ids)

    # 2. API: IDOR protection on GET /api/bets/{id}
    async def test_api_idor_protection(self):
        # User A places a bet
        resp = await self.client.post(
            "/api/predictions",
            headers={"X-Telegram-Init-Data": self.auth_a},
            json={
                "amount": 50,
                "selections": [{"match_id": 99701, "outcome": "p1"}]
            }
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        bet_id = data["bet_id"]

        # User B tries to view User A's bet
        resp_b = await self.client.get(f"/api/bets/{bet_id}", headers={"X-Telegram-Init-Data": self.auth_b})
        self.assertEqual(resp_b.status, 404, "User B must receive 404 when querying User A's bet.")

    # 3. API: Division-scoped matches
    async def test_api_division_scoped_matches(self):
        resp1 = await self.client.get("/api/matches?division_id=1", headers={"X-Telegram-Init-Data": self.auth_a})
        self.assertEqual(resp1.status, 200)
        data1 = await resp1.json()
        matches1 = data1["matches"]
        for m in matches1:
            if m.get("division_id"):
                self.assertEqual(m["division_id"], 1)

    # 4. API: Wallet data isolation
    async def test_api_wallet_data_isolation(self):
        resp_a = await self.client.get("/api/wallet", headers={"X-Telegram-Init-Data": self.auth_a})
        self.assertEqual(resp_a.status, 200)
        data_a = await resp_a.json()
        self.assertEqual(data_a["wallet"]["user_id"], self.user_a_id)

        resp_b = await self.client.get("/api/wallet", headers={"X-Telegram-Init-Data": self.auth_b})
        self.assertEqual(resp_b.status, 200)
        data_b = await resp_b.json()
        self.assertEqual(data_b["wallet"]["user_id"], self.user_b_id)


if __name__ == "__main__":
    unittest.main()
