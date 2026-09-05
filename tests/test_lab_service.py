"""
tests/test_lab_service.py

Comprehensive test suite for Logovo Lab (🧪 ЛАБОРАТОРИЯ).
Verifies:
1. Synthetic season generation (16 teams, 30 rounds, 240 matches, markets, odds).
2. Hard synthetic isolation (production DB untouched).
3. Predefined quick scenarios & match preparation (NO automated betting).
4. Match lifecycle transitions & live event ingestion.
5. Detection of real manual bets placed via database.place_user_bet.
6. Match result setting & delegation to official settlement engine.
7. Mathematical financial reconciliation (Expected == Actual, mismatch detection).
8. Dynamic teams standings calculation.
9. Safe Reset (deletes only synthetic data, restores 100,000 balance).
"""

import unittest
import database
from services import lab_service


class TestLabService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()

    def setUp(self):
        self.test_user_id = 999999999
        lab_service.set_active_test_user_id(self.test_user_id)
        # Ensure clean state before each test
        lab_service.reset_test_lab(self.test_user_id)

    def tearDown(self):
        lab_service.reset_test_lab(self.test_user_id)

    def test_01_create_synthetic_season(self):
        """Verify season generator creates 16 teams, 30 rounds, 240 matches, markets and odds."""
        res = lab_service.create_test_season(
            season_name="LOGOVO TEST SEASON 2026",
            division_name="LOGOVO TEST LEAGUE",
            teams_count=16,
            rounds_count=30,
            seed=20260905,
        )
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["teams_count"], 16)
        self.assertEqual(res["rounds_count"], 30)
        self.assertEqual(res["matches_count"], 240)
        self.assertEqual(res["test_user_balance"], 100000)

        # Check DB state
        with database.transaction() as conn:
            cursor = conn.cursor()

            # Verify matches
            cursor.execute("SELECT COUNT(*) as cnt FROM matches WHERE division_id = ?", (res["division_id"],))
            self.assertEqual(cursor.fetchone()["cnt"], 240)

            # Verify rounds
            cursor.execute("SELECT COUNT(*) as cnt FROM rounds WHERE division_id = ?", (res["division_id"],))
            self.assertEqual(cursor.fetchone()["cnt"], 30)

            # Verify round 1 is open by default
            cursor.execute("SELECT is_open FROM rounds WHERE division_id = ? AND round_number = 1", (res["division_id"],))
            self.assertEqual(cursor.fetchone()["is_open"], 1)

            # Verify markets for first match
            cursor.execute("SELECT id FROM matches WHERE division_id = ? ORDER BY id ASC LIMIT 1", (res["division_id"],))
            m1_id = cursor.fetchone()["id"]

            cursor.execute("SELECT market_key FROM markets WHERE match_id = ?", (m1_id,))
            m_keys = [r["market_key"] for r in cursor.fetchall()]
            self.assertIn("1x2", m_keys)
            self.assertIn("total_goals", m_keys)
            self.assertIn("btts", m_keys)

            # Verify odds
            cursor.execute("""
                SELECT ms.selection_key, ms.odds_value
                FROM market_selections ms
                JOIN markets mkt ON ms.market_id = mkt.id
                WHERE mkt.match_id = ?
            """, (m1_id,))
            odds_dict = {r["selection_key"]: float(r["odds_value"]) for r in cursor.fetchall()}
            self.assertGreater(odds_dict.get("p1", 0), 1.0)
            self.assertGreater(odds_dict.get("x", 0), 1.0)
            self.assertGreater(odds_dict.get("p2", 0), 1.0)

    def test_02_synthetic_isolation_production_untouched(self):
        """Verify operations on test season do NOT affect production divisions (1..5)."""
        # Count production division 1 matches before
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM matches WHERE division_id = 1")
            prod_cnt_before = cursor.fetchone()["cnt"]

        # Create and reset test season
        lab_service.create_test_season()
        lab_service.reset_test_lab()

        # Count production division 1 matches after
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM matches WHERE division_id = 1")
            prod_cnt_after = cursor.fetchone()["cnt"]

        self.assertEqual(prod_cnt_before, prod_cnt_after)

    def test_03_match_preparation_and_quick_scenarios(self):
        """Verify scenario preparation opens round, match, sets expected score and DOES NOT place bet."""
        lab_service.create_test_season()

        # Apply Home Win scenario (North Wolves vs Red Falcons, 1.80, 2:0)
        res = lab_service.apply_scenario("home_win")
        self.assertEqual(res["status"], "ok")
        prep = res["preparation"]
        self.assertEqual(prep["match_status"], "open")
        self.assertEqual(prep["expected_score"], "2:0")

        # Check in DB
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, stadium FROM matches WHERE id = ?", (prep["match_id"],))
            m = cursor.fetchone()
            self.assertEqual(m["status"], "open")
            self.assertIn("Expected:2:0", m["stadium"])

            # Verify NO bet was placed automatically!
            cursor.execute("SELECT COUNT(*) as cnt FROM user_bets WHERE user_id = ?", (self.test_user_id,))
            self.assertEqual(cursor.fetchone()["cnt"], 0, "Lab must NOT place automated bets!")

    def test_04_status_transitions_and_live_events(self):
        """Verify match transitions and live event ingestion via real services."""
        lab_service.create_test_season()
        matches = lab_service.get_lab_matches(round_number=1)
        m_id = matches[0]["id"]

        # 1. Transition to OPEN
        trans1 = lab_service.transition_match_lifecycle(m_id, "open")
        self.assertEqual(trans1["new_status"], "open")

        # 2. Transition to LIVE
        trans2 = lab_service.transition_match_lifecycle(m_id, "live")
        self.assertEqual(trans2["new_status"], "live")

        # 3. Ingest Live Goal Home
        ev1 = lab_service.send_live_event_action(m_id, action="goal", side="home", minute=23)
        self.assertEqual(ev1["status"], "ok")
        self.assertEqual(ev1["current_score"], "1:0")

        # 4. Ingest Live Yellow Card
        ev2 = lab_service.send_live_event_action(m_id, action="yellow_card", side="away", minute=35)
        self.assertEqual(ev2["status"], "ok")

        # 5. Ingest Live Halftime
        ev3 = lab_service.send_live_event_action(m_id, action="halftime")
        self.assertEqual(ev3["status"], "ok")
        self.assertEqual(ev3["match_status"], "halftime")

    def test_05_manual_bet_detection_and_settlement_workflow(self):
        """
        Complete manual test cycle:
        1. Lab prepares match.
        2. Tester manually places bet via database.place_user_bet.
        3. Lab detects manual bet and updates Step Tracker.
        4. Match finishes 2:0 and is settled via official settlement engine.
        5. Payout is credited to wallet.
        6. Financial reconciliation formula matches exactly!
        """
        lab_service.create_test_season()
        sc_res = lab_service.apply_scenario("home_win")
        match_id = sc_res["preparation"]["match_id"]

        # Verify initial balance is 100,000
        self.assertEqual(database.get_wallet_balance(self.test_user_id), 100000)

        # Tester places MANUAL bet (1,000 coins on P1 @ 1.80)
        success, bet_res = database.place_user_bet(
            user_id=self.test_user_id,
            amount=1000,
            selections=[{"match_id": match_id, "outcome": "p1", "odd": 1.80}],
        )
        self.assertTrue(success, f"Manual bet failed: {bet_res}")
        bet_id = bet_res

        # Balance after bet: 100,000 - 1,000 = 99,000
        self.assertEqual(database.get_wallet_balance(self.test_user_id), 99000)

        # Lab detects manual bet
        detected = lab_service.check_manual_bet(match_id, self.test_user_id)
        self.assertTrue(detected["detected"])
        self.assertEqual(detected["bet_id"], bet_id)
        self.assertEqual(detected["stake"], 1000)
        self.assertEqual(detected["selection"], "p1")
        self.assertEqual(detected["status"], "pending")

        # Step tracker reflects detected bet
        tracker = lab_service.get_step_tracker_status(self.test_user_id)
        self.assertTrue(tracker["steps"][2]["completed"])  # Step 3: Bet placed is completed!

        # Now tester finishes and settles match with 2:0 (P1 wins!)
        settle_res = lab_service.set_match_result_and_settle(match_id, score1=2, score2=0, confirm_and_settle=True)
        self.assertEqual(settle_res["status"], "ok")
        self.assertGreater(settle_res["payouts_count"], 0)

        # Check bet status updated to 'won' and payout = 1,000 * 1.80 = 1,800
        bets = lab_service.get_test_player_bets(self.test_user_id)
        self.assertEqual(bets[0]["status"], "won")
        self.assertEqual(bets[0]["actual_payout"], 1800)

        # Wallet balance: 99,000 + 1,800 = 100,800
        self.assertEqual(database.get_wallet_balance(self.test_user_id), 100800)

        # Financial Reconciliation check
        recon = lab_service.get_financial_reconciliation(self.test_user_id)
        self.assertEqual(recon["status"], "ok")
        self.assertEqual(recon["badge"], "🟢 BALANCE OK")
        self.assertEqual(recon["initial_balance"], 100000)
        self.assertEqual(recon["total_stakes"], 1000)
        self.assertEqual(recon["total_payouts"], 1800)
        self.assertEqual(recon["actual_balance"], 100800)
        self.assertEqual(recon["expected_balance"], 100800)
        self.assertEqual(recon["difference"], 0)

    def test_06_financial_mismatch_detection(self):
        """Verify that any tampering or inconsistency triggers 🔴 FINANCIAL MISMATCH."""
        lab_service.create_test_season()

        # Inject an unauthorized direct balance modification outside of betting engine
        with database.transaction() as conn:
            conn.execute("UPDATE user_wallets SET balance = balance + 555 WHERE user_id = ?", (self.test_user_id,))

        recon = lab_service.get_financial_reconciliation(self.test_user_id)
        self.assertEqual(recon["status"], "mismatch")
        self.assertEqual(recon["badge"], "🔴 FINANCIAL MISMATCH")
        self.assertEqual(recon["difference"], 555)

    def test_07_teams_standings(self):
        """Verify dynamic calculation of standings table for synthetic teams."""
        lab_service.create_test_season()
        matches = lab_service.get_lab_matches(round_number=1)
        m1 = matches[0]

        # Settle North Wolves 2:0 Red Falcons
        lab_service.set_match_result_and_settle(m1["id"], score1=2, score2=0, confirm_and_settle=True)

        standings = lab_service.get_teams_standings()
        self.assertEqual(len(standings), 16)

        # Leader should be North Wolves with 3 points, 1 win, +2 GD
        leader = standings[0]
        self.assertEqual(leader["team"], "North Wolves")
        self.assertEqual(leader["played"], 1)
        self.assertEqual(leader["wins"], 1)
        self.assertEqual(leader["points"], 3)
        self.assertEqual(leader["gf"], 2)
        self.assertEqual(leader["ga"], 0)
        self.assertEqual(leader["gd"], 2)

    def test_08_reset_test_lab(self):
        """Verify complete reset cleans all synthetic data and restores balance."""
        lab_service.create_test_season()
        matches = lab_service.get_lab_matches(round_number=1)

        # Place bet and settle
        database.place_user_bet(
            user_id=self.test_user_id,
            amount=500,
            selections=[{"match_id": matches[0]["id"], "outcome": "p1", "odd": 1.80}],
        )
        lab_service.set_match_result_and_settle(matches[0]["id"], score1=2, score2=0, confirm_and_settle=True)

        # Reset
        reset_res = lab_service.reset_test_lab(self.test_user_id)
        self.assertEqual(reset_res["status"], "ok")
        self.assertEqual(reset_res["test_user_balance"], 100000)
        self.assertEqual(database.get_wallet_balance(self.test_user_id), 100000)

        # Verify matches in test division are 0
        div = lab_service.get_test_division()
        self.assertIsNone(div)

        # Verify test user bets are 0
        bets = lab_service.get_test_player_bets(self.test_user_id)
        self.assertEqual(len(bets), 0)


if __name__ == "__main__":
    unittest.main()
