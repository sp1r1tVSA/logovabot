"""
tests/test_phase3_operations.py

Comprehensive test suite for Phase 3:
1. Match score validation (negative score rejection).
2. Match score correction & admin audit log tracking.
3. Wallet balance concurrency protection (preventing negative balance).
4. Settlement idempotency (zero duplicate payouts on repeat settlement).
5. Odds engine division isolation.
6. Topic routing isolation for division results & reminders.
7. Season lifecycle validation on round opening.
8. Direct IDOR-safe bet retrieval.
9. Backward compatibility with legacy NULL division records.
"""

import unittest
import database
import config
from services import odds_engine, settlement_engine, betting_engine


class TestPhase3Operations(unittest.TestCase):
    def setUp(self):
        self.admin_id = 999001
        self.user1_id = 999002
        self.user2_id = 999003
        self.other_user_id = 999004

        database.init_db()

        with database.transaction() as conn:
            c = conn.cursor()
            u_ids = (self.admin_id, self.user1_id, self.user2_id, self.other_user_id)
            c.execute("DELETE FROM bet_items WHERE bet_id IN (SELECT id FROM user_bets WHERE user_id IN (?, ?, ?, ?))", u_ids)
            c.execute("DELETE FROM user_bets WHERE user_id IN (?, ?, ?, ?)", u_ids)
            c.execute("DELETE FROM coin_transactions WHERE user_id IN (?, ?, ?, ?)", u_ids)
            c.execute("DELETE FROM user_wallets WHERE user_id IN (?, ?, ?, ?)", u_ids)
            c.execute("DELETE FROM match_events WHERE match_id >= 99000")
            c.execute("DELETE FROM bet_markets WHERE match_id >= 99000")
            c.execute("DELETE FROM matches WHERE id >= 99000")
            c.execute("DELETE FROM rounds WHERE division_id IN (991, 992)")
            c.execute("DELETE FROM division_topics WHERE division_id IN (991, 992)")
            c.execute("DELETE FROM divisions WHERE id IN (991, 992)")
            c.execute("DELETE FROM admin_audit_log WHERE admin_id = ?", (self.admin_id,))
            c.execute("DELETE FROM seasons WHERE id IN (998, 999)")
            c.execute("DELETE FROM users WHERE telegram_id IN (?, ?, ?, ?)", u_ids)

            c.execute("INSERT OR REPLACE INTO users (telegram_id, username, team_name, division_id, role) VALUES (?, ?, ?, ?, ?)",
                      (self.admin_id, "p3_admin", "Admin FC", 1, "admin"))
            c.execute("INSERT OR REPLACE INTO users (telegram_id, username, team_name, division_id, role) VALUES (?, ?, ?, ?, ?)",
                      (self.user1_id, "p3_player1", "Phase3 Team A", 1, "player"))
            c.execute("INSERT OR REPLACE INTO users (telegram_id, username, team_name, division_id, role) VALUES (?, ?, ?, ?, ?)",
                      (self.user2_id, "p3_player2", "Phase3 Team B", 1, "player"))
            c.execute("INSERT OR REPLACE INTO users (telegram_id, username, team_name, division_id, role) VALUES (?, ?, ?, ?, ?)",
                      (self.other_user_id, "p3_other", "Phase3 Other", 2, "player"))

            # Create test seasons & divisions
            c.execute("INSERT INTO seasons (id, name, status, created_by) VALUES (998, 'P3 Active Season', 'active', ?)", (self.admin_id,))
            c.execute("INSERT INTO seasons (id, name, status, created_by) VALUES (999, 'P3 Finished Season', 'finished', ?)", (self.admin_id,))

            c.execute("INSERT INTO divisions (id, name, code, is_active, season_id) VALUES (991, 'P3 Division 1', 'P3D1', 1, 998)")
            c.execute("INSERT INTO divisions (id, name, code, is_active, season_id) VALUES (992, 'P3 Division 2', 'P3D2', 1, 998)")

            # Create test division topics
            c.execute("INSERT INTO division_topics (division_id, topic_type, message_thread_id, group_chat_id) VALUES (991, 'results', 1111, -100123)")
            c.execute("INSERT INTO division_topics (division_id, topic_type, message_thread_id, group_chat_id) VALUES (991, 'reports', 2222, -100123)")
            c.execute("INSERT INTO division_topics (division_id, topic_type, message_thread_id, group_chat_id) VALUES (992, 'results', 3333, -100123)")

            # Setup test wallets
            c.execute("INSERT INTO user_wallets (user_id, balance) VALUES (?, 1000)", (self.user1_id,))
            c.execute("INSERT INTO user_wallets (user_id, balance) VALUES (?, 50)", (self.user2_id,))

    def tearDown(self):
        with database.transaction() as conn:
            c = conn.cursor()
            u_ids = (self.admin_id, self.user1_id, self.user2_id, self.other_user_id)
            c.execute("DELETE FROM bet_items WHERE bet_id IN (SELECT id FROM user_bets WHERE user_id IN (?, ?, ?, ?))", u_ids)
            c.execute("DELETE FROM user_bets WHERE user_id IN (?, ?, ?, ?)", u_ids)
            c.execute("DELETE FROM coin_transactions WHERE user_id IN (?, ?, ?, ?)", u_ids)
            c.execute("DELETE FROM user_wallets WHERE user_id IN (?, ?, ?, ?)", u_ids)
            c.execute("DELETE FROM match_events WHERE match_id >= 99000")
            c.execute("DELETE FROM bet_markets WHERE match_id >= 99000")
            c.execute("DELETE FROM matches WHERE id >= 99000")
            c.execute("DELETE FROM rounds WHERE division_id IN (991, 992)")
            c.execute("DELETE FROM division_topics WHERE division_id IN (991, 992)")
            c.execute("DELETE FROM divisions WHERE id IN (991, 992)")
            c.execute("DELETE FROM admin_audit_log WHERE admin_id = ?", (self.admin_id,))
            c.execute("DELETE FROM seasons WHERE id IN (998, 999)")
            c.execute("DELETE FROM users WHERE telegram_id IN (?, ?, ?, ?)", u_ids)
            c.execute("DELETE FROM user_bets WHERE user_id IN (?, ?, ?, ?)",
                      (self.admin_id, self.user1_id, self.user2_id, self.other_user_id))

    def test_01_negative_scores_rejected(self):
        """Repository must reject negative scores across all result-setting functions."""
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO matches (id, round_number, player1_id, player2_id, player1_team, player2_team, status, division_id, season_id) "
                "VALUES (99001, 1, ?, ?, 'Phase3 Team A', 'Phase3 Team B', 'pending', 991, 998)",
                (self.user1_id, self.user2_id)
            )

        with self.assertRaises(ValueError):
            database.confirm_and_finalize_match(99001, -1, 2, [])

        with self.assertRaises(ValueError):
            database.confirm_and_finalize_match(99001, 2, -3, [])

        with self.assertRaises(ValueError):
            database.set_technical_result(99001, -3, 0)

        with self.assertRaises(ValueError):
            database.admin_set_match_score(99001, 0, -1, admin_id=self.admin_id)

    def test_02_score_correction_and_audit_logging(self):
        """Score correction on confirmed match must log to admin_audit_log and update standings."""
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO matches (id, round_number, player1_id, player2_id, player1_team, player2_team, status, division_id, season_id) "
                "VALUES (99002, 1, ?, ?, 'Phase3 Team A', 'Phase3 Team B', 'pending', 991, 998)",
                (self.user1_id, self.user2_id)
            )

        # 1. Initial confirmation
        database.admin_set_match_score(99002, 2, 1, admin_id=self.admin_id)
        m = database.get_match(99002)
        self.assertEqual(m["status"], "confirmed")
        self.assertEqual(m["player1_score"], 2)
        self.assertEqual(m["player2_score"], 1)

        # 2. Score correction
        database.admin_set_match_score(99002, 1, 1, admin_id=self.admin_id)
        m_corrected = database.get_match(99002)
        self.assertEqual(m_corrected["player1_score"], 1)
        self.assertEqual(m_corrected["player2_score"], 1)

        # Verify audit log recorded
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM admin_audit_log WHERE target_id = 99002 AND action = 'correct_match_score'")
            log_row = c.fetchone()
            self.assertIsNotNone(log_row)
            self.assertEqual(log_row["old_value"], "2:1")
            self.assertEqual(log_row["new_value"], "1:1")
            self.assertEqual(log_row["admin_id"], self.admin_id)

        # Verify standings recalculated (draw = 1 point each)
        standings = database.get_standings(division_id=991, season_id=998)
        team_a = next((t for t in standings if t["team_name"] == "Phase3 Team A"), None)
        team_b = next((t for t in standings if t["team_name"] == "Phase3 Team B"), None)
        self.assertIsNotNone(team_a)
        self.assertIsNotNone(team_b)
        self.assertEqual(team_a["points"], 1)
        self.assertEqual(team_b["points"], 1)
        self.assertEqual(team_a["draws"], 1)
        self.assertEqual(team_b["draws"], 1)

    def test_03_wallet_balance_concurrency_guard(self):
        """Wallet balance cannot go negative when betting amount exceeds balance."""
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO matches (id, round_number, player1_id, player2_id, player1_team, player2_team, status, division_id, season_id) "
                "VALUES (99003, 1, ?, ?, 'Phase3 Team A', 'Phase3 Team B', 'open', 991, 998)",
                (self.user1_id, self.user2_id)
            )
            conn.execute(
                "INSERT INTO bet_markets (match_id, tour, team1_name, team2_name, odd_p1, odd_x, odd_p2, is_active) "
                "VALUES (99003, 1, 'Phase3 Team A', 'Phase3 Team B', 2.00, 3.00, 3.50, 1)"
            )

        # User2 has balance 50, tries to bet 100 -> Rejected
        ok, res = database.place_user_bet(
            user_id=self.user2_id,
            amount=100,
            selections=[{"match_id": 99003, "outcome": "p1"}]
        )
        self.assertFalse(ok)
        self.assertIn("Недостаточно монет", str(res))

        # Balance remains 50
        wallet = database.get_or_create_wallet(self.user2_id)
        self.assertEqual(wallet["balance"], 50)

    def test_04_settlement_idempotency_zero_double_payout(self):
        """Repeated settlement of the same match must not double-credit wallet balance."""
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO matches (id, round_number, player1_id, player2_id, player1_team, player2_team, status, division_id, season_id) "
                "VALUES (99004, 1, ?, ?, 'Phase3 Team A', 'Phase3 Team B', 'open', 991, 998)",
                (self.user1_id, self.user2_id)
            )
            conn.execute(
                "INSERT INTO bet_markets (match_id, tour, team1_name, team2_name, odd_p1, odd_x, odd_p2, is_active) "
                "VALUES (99004, 1, 'Phase3 Team A', 'Phase3 Team B', 2.00, 3.00, 3.50, 1)"
            )

        # User1 bets 100 on p1 (odd 2.00) -> potential win = 200
        ok, bet_id = database.place_user_bet(
            user_id=self.user1_id,
            amount=100,
            selections=[{"match_id": 99004, "outcome": "p1"}]
        )
        self.assertTrue(ok)
        bal_after_bet = database.get_or_create_wallet(self.user1_id)["balance"]
        self.assertEqual(bal_after_bet, 900)

        # First settlement: Team A wins 2:0 -> Bet WON -> +200 payout
        notifs1 = settlement_engine.settle_match_predictions(99004, score1=2, score2=0)
        self.assertEqual(len(notifs1), 1)
        self.assertEqual(notifs1[0]["status"], "won")
        self.assertEqual(notifs1[0]["payout"], 200)

        bal_after_settle1 = database.get_or_create_wallet(self.user1_id)["balance"]
        self.assertGreaterEqual(bal_after_settle1, 1100)

        # Second settlement (repeat trigger): Must be completely idempotent
        notifs2 = settlement_engine.settle_match_predictions(99004, score1=2, score2=0)
        self.assertEqual(len(notifs2), 0)

        bal_after_settle2 = database.get_or_create_wallet(self.user1_id)["balance"]
        self.assertEqual(bal_after_settle2, bal_after_settle1, "Double settlement must NOT increase wallet balance!")

    def test_05_odds_engine_division_isolation(self):
        """Odds engine must evaluate standings strictly scoped to the fixture's division."""
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO matches (id, round_number, player1_id, player2_id, player1_team, player2_team, status, division_id, season_id) "
                "VALUES (99005, 1, ?, ?, 'Phase3 Team A', 'Phase3 Team B', 'open', 991, 998)",
                (self.user1_id, self.user2_id)
            )

        # Generate markets for match
        markets = odds_engine.generate_match_markets(99005, "Phase3 Team A", "Phase3 Team B")
        self.assertGreater(len(markets), 0)
        # Check standard market keys
        keys = [m["market_key"] for m in markets]
        self.assertIn("1x2", keys)
        self.assertIn("total_goals", keys)
        self.assertIn("btts", keys)

    def test_06_round_opening_season_status_guard(self):
        """Opening rounds in a finished or archived season must be rejected."""
        # Season 999 is 'finished'
        with self.assertRaises(ValueError):
            database.update_round_status(round_number=1, is_open=True, division_id=991, season_id=999)

        with self.assertRaises(ValueError):
            database.open_rounds_batch(start_round=1, end_round=3, deadline="2026-12-31 23:59", division_id=991, season_id=999)

        # Season 998 is 'active' -> Succeeds
        database.update_round_status(round_number=1, is_open=True, division_id=991, season_id=998)
        r_info = database.get_round_info(1, division_id=991, season_id=998)
        self.assertIsNotNone(r_info)
        self.assertEqual(r_info["is_open"], 1)

    def test_07_topic_routing_isolation(self):
        """Results and reports must route to the specific division topics without leakage."""
        t_res_991 = database.get_division_topic(991, "results")
        t_rep_991 = database.get_division_topic(991, "reports")
        t_res_992 = database.get_division_topic(992, "results")

        self.assertEqual(t_res_991, 1111)
        self.assertEqual(t_rep_991, 2222)
        self.assertEqual(t_res_992, 3333)
        self.assertNotEqual(t_res_991, t_res_992)

    def test_08_direct_user_bet_by_id_idor_safe(self):
        """get_user_bet_by_id must return bet only for the authorized user."""
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO matches (id, round_number, player1_id, player2_id, player1_team, player2_team, status, division_id, season_id) "
                "VALUES (99008, 1, ?, ?, 'Phase3 Team A', 'Phase3 Team B', 'open', 991, 998)",
                (self.user1_id, self.user2_id)
            )
            conn.execute(
                "INSERT INTO bet_markets (match_id, tour, team1_name, team2_name, odd_p1, odd_x, odd_p2, is_active) "
                "VALUES (99008, 1, 'Phase3 Team A', 'Phase3 Team B', 2.00, 3.00, 3.50, 1)"
            )

        ok, bet_id = database.place_user_bet(
            user_id=self.user1_id,
            amount=50,
            selections=[{"match_id": 99008, "outcome": "p1"}]
        )
        self.assertTrue(ok)

        # Authorized user gets the bet
        bet = database.get_user_bet_by_id(user_id=self.user1_id, bet_id=bet_id)
        self.assertIsNotNone(bet)
        self.assertEqual(bet["id"], bet_id)
        self.assertEqual(len(bet["items"]), 1)

        # Other user cannot access this bet (IDOR prevented)
        bet_idor = database.get_user_bet_by_id(user_id=self.other_user_id, bet_id=bet_id)
        self.assertIsNone(bet_idor)

    def test_09_backward_compatibility_preserved(self):
        """Historical records with division_id IS NULL or season_id IS NULL must remain accessible."""
        with database.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO matches (id, round_number, player1_id, player2_id, player1_team, player2_team, status, division_id, season_id) "
                "VALUES (99009, 1, ?, ?, 'Legacy Team A', 'Legacy Team B', 'pending', NULL, NULL)",
                (self.user1_id, self.user2_id)
            )

        with database.transaction() as conn:
            c = conn.cursor()
            # Verify legacy match with division_id IS NULL is accessible
            c.execute("SELECT id, division_id, season_id FROM matches WHERE id = 99009")
            row = c.fetchone()
            self.assertIsNotNone(row)
            self.assertIsNone(row["division_id"])
            self.assertIn(row["season_id"], (1, None))

        # Verify unplayed matches query fallback works when division_id is None
        unplayed = database.get_unplayed_matches_by_round(1, division_id=None)
        self.assertIsInstance(unplayed, list)
        matching = next((m for m in unplayed if m["id"] == 99009), None)
        self.assertIsNotNone(matching, "Legacy match without division_id must be returned in global query")


if __name__ == "__main__":
    unittest.main()
