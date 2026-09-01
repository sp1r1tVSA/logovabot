"""
tests/test_settlement_engine.py
Comprehensive unit tests for Market Settler & Settlement Engine (v2.0).
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import services.odds_engine as odds_engine
import services.market_settler as market_settler
import services.settlement_engine as settlement_engine


class TestSettlementEngine(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self.user_id = 999301
        self.m1_id = 999201
        self.m2_id = 999202

        with database.transaction() as conn:
            conn.execute("DELETE FROM bet_items WHERE match_id IN (?, ?)", (self.m1_id, self.m2_id))
            conn.execute("DELETE FROM user_bets WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM user_wallets WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM user_progression WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM user_achievements WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM coin_transactions WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM markets WHERE match_id IN (?, ?)", (self.m1_id, self.m2_id))
            conn.execute("DELETE FROM matches WHERE id IN (?, ?)", (self.m1_id, self.m2_id))

            # Create test matches
            conn.execute("""
                INSERT INTO matches (id, tournament_id, round_number, player1_team, player2_team, status)
                VALUES (?, 1, 1, 'Манчестер Сити', 'Ливерпуль', 'scheduled')
            """, (self.m1_id,))
            conn.execute("""
                INSERT INTO matches (id, tournament_id, round_number, player1_team, player2_team, status)
                VALUES (?, 1, 1, 'Арсенал', 'Челси', 'scheduled')
            """, (self.m2_id,))

        database.get_or_create_wallet(self.user_id)

    def tearDown(self):
        with database.transaction() as conn:
            conn.execute("DELETE FROM bet_items WHERE match_id IN (?, ?)", (self.m1_id, self.m2_id))
            conn.execute("DELETE FROM user_bets WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM user_wallets WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM user_progression WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM user_achievements WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM coin_transactions WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM markets WHERE match_id IN (?, ?)", (self.m1_id, self.m2_id))
            conn.execute("DELETE FROM matches WHERE id IN (?, ?)", (self.m1_id, self.m2_id))

    def test_market_settler_pure_rules(self):
        # 1X2
        self.assertEqual(market_settler.evaluate_market_selection("1x2", "p1", 2, 1), "won")
        self.assertEqual(market_settler.evaluate_market_selection("1x2", "x", 2, 2), "won")
        self.assertEqual(market_settler.evaluate_market_selection("1x2", "p2", 0, 3), "won")
        self.assertEqual(market_settler.evaluate_market_selection("1x2", "p1", 1, 2), "lost")

        # Double Chance
        self.assertEqual(market_settler.evaluate_market_selection("double_chance", "1x", 1, 1), "won")
        self.assertEqual(market_settler.evaluate_market_selection("double_chance", "12", 2, 1), "won")
        self.assertEqual(market_settler.evaluate_market_selection("double_chance", "12", 2, 2), "lost")
        self.assertEqual(market_settler.evaluate_market_selection("double_chance", "x2", 0, 1), "won")

        # Totals
        self.assertEqual(market_settler.evaluate_market_selection("total_goals", "over_2.5", 2, 1), "won")
        self.assertEqual(market_settler.evaluate_market_selection("total_goals", "under_2.5", 1, 1), "won")
        self.assertEqual(market_settler.evaluate_market_selection("total_goals", "over_3.5", 2, 1), "lost")

        # BTTS
        self.assertEqual(market_settler.evaluate_market_selection("btts", "btts_yes", 1, 1), "won")
        self.assertEqual(market_settler.evaluate_market_selection("btts", "btts_yes", 2, 0), "lost")
        self.assertEqual(market_settler.evaluate_market_selection("btts", "btts_no", 2, 0), "won")

        # Individual Totals
        self.assertEqual(market_settler.evaluate_market_selection("individual_total_1", "it1_over_1.5", 2, 0), "won")
        self.assertEqual(market_settler.evaluate_market_selection("individual_total_1", "it1_under_1.5", 1, 3), "won")
        self.assertEqual(market_settler.evaluate_market_selection("individual_total_2", "it2_over_1.5", 0, 2), "won")

        # Handicap
        self.assertEqual(market_settler.evaluate_market_selection("handicap", "h1_minus_1.5", 3, 1), "won")
        self.assertEqual(market_settler.evaluate_market_selection("handicap", "h1_minus_1.5", 2, 1), "lost")
        self.assertEqual(market_settler.evaluate_market_selection("handicap", "h2_plus_1.5", 2, 1), "won")

        # Draw No Bet
        self.assertEqual(market_settler.evaluate_market_selection("draw_no_bet", "dnb_1", 1, 1), "voided")
        self.assertEqual(market_settler.evaluate_market_selection("draw_no_bet", "dnb_1", 2, 1), "won")
        self.assertEqual(market_settler.evaluate_market_selection("draw_no_bet", "dnb_1", 0, 1), "lost")

        # Cancelled match -> voided
        self.assertEqual(market_settler.evaluate_market_selection("1x2", "p1", 0, 0, match_status="cancelled"), "voided")

    def test_single_prediction_won_and_idempotent_settlement(self):
        # 1. Place single bet on Match 1 (P1 at 2.50 for 200 coins)
        database.deduct_coins(self.user_id, 200, tx_type="bet_placed")
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO user_bets (user_id, bet_type, amount, total_odd, potential_win, status)
                VALUES (?, 'single', 200, 2.50, 500, 'pending')
            """, (self.user_id,))
            b_id = cursor.lastrowid
            cursor.execute("""
                INSERT INTO bet_items (bet_id, match_id, outcome_type, odd, status)
                VALUES (?, ?, 'p1', 2.50, 'pending')
            """, (b_id, self.m1_id))

        bal_before = database.get_wallet_balance(self.user_id)  # 800

        # 2. Settle Match 1 (Score 3:1 -> P1 wins)
        notifs = settlement_engine.settle_match_predictions(self.m1_id, score1=3, score2=1)
        self.assertEqual(len(notifs), 1)
        self.assertEqual(notifs[0]["status"], "won")
        self.assertEqual(notifs[0]["payout"], 500)

        # 3. Check wallet credited (+500 -> 1300)
        bal_after = database.get_wallet_balance(self.user_id)
        self.assertEqual(bal_after, bal_before + 500)

        # 4. Check user_bets updated
        bet = database.get_user_bets(self.user_id)[0]
        self.assertEqual(bet["status"], "won")
        self.assertEqual(bet["actual_payout"], 500)

        # 5. Settle again -> Idempotent check (no second payout)
        notifs2 = settlement_engine.settle_match_predictions(self.m1_id, score1=3, score2=1)
        self.assertEqual(len(notifs2), 0)
        self.assertEqual(database.get_wallet_balance(self.user_id), bal_after)

    def test_express_prediction_multi_match_progression(self):
        # Place Express on Match 1 (P1 at 2.00) and Match 2 (BTTS Yes at 1.80) -> total 3.60 for 100 coins
        database.deduct_coins(self.user_id, 100, tx_type="bet_placed")
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO user_bets (user_id, bet_type, amount, total_odd, potential_win, status)
                VALUES (?, 'express', 100, 3.60, 360, 'pending')
            """, (self.user_id,))
            b_id = cursor.lastrowid
            cursor.execute("""
                INSERT INTO bet_items (bet_id, match_id, outcome_type, odd, status)
                VALUES (?, ?, 'p1', 2.00, 'pending')
            """, (b_id, self.m1_id))
            cursor.execute("""
                INSERT INTO bet_items (bet_id, match_id, outcome_type, odd, status)
                VALUES (?, ?, 'btts_yes', 1.80, 'pending')
            """, (b_id, self.m2_id))

        bal_start = database.get_wallet_balance(self.user_id)

        # 1. Settle Match 1 (Score 2:0 -> P1 won)
        notifs1 = settlement_engine.settle_match_predictions(self.m1_id, score1=2, score2=0)
        self.assertEqual(len(notifs1), 0)  # Express not fully settled yet

        bet = database.get_user_bets(self.user_id)[0]
        self.assertEqual(bet["status"], "pending")
        self.assertEqual(database.get_wallet_balance(self.user_id), bal_start)

        # 2. Settle Match 2 (Score 1:1 -> BTTS Yes won)
        notifs2 = settlement_engine.settle_match_predictions(self.m2_id, score1=1, score2=1)
        self.assertEqual(len(notifs2), 1)
        self.assertEqual(notifs2[0]["status"], "won")
        self.assertEqual(notifs2[0]["payout"], 360)

        bet_final = database.get_user_bets(self.user_id)[0]
        self.assertEqual(bet_final["status"], "won")
        self.assertEqual(bet_final["actual_payout"], 360)
        self.assertEqual(database.get_wallet_balance(self.user_id), bal_start + 360)

    def test_express_with_voided_match_recalculation(self):
        # Express on Match 1 (P1, 2.00) and Match 2 (Over 2.5, 1.70)
        database.deduct_coins(self.user_id, 100, tx_type="bet_placed")
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO user_bets (user_id, bet_type, amount, total_odd, potential_win, status)
                VALUES (?, 'express', 100, 3.40, 340, 'pending')
            """, (self.user_id,))
            b_id = cursor.lastrowid
            cursor.execute("""
                INSERT INTO bet_items (bet_id, match_id, outcome_type, odd, status)
                VALUES (?, ?, 'p1', 2.00, 'pending')
            """, (b_id, self.m1_id))
            cursor.execute("""
                INSERT INTO bet_items (bet_id, match_id, outcome_type, odd, status)
                VALUES (?, ?, 'tb25', 1.70, 'pending')
            """, (b_id, self.m2_id))

        bal_start = database.get_wallet_balance(self.user_id)

        # Match 1 is cancelled (voided leg, odd effectively 1.00)
        settlement_engine.settle_match_predictions(self.m1_id, score1=0, score2=0, match_status="cancelled")

        # Match 2 finishes 2:1 (Total > 2.5 won, odd 1.70)
        notifs = settlement_engine.settle_match_predictions(self.m2_id, score1=2, score2=1)
        self.assertEqual(len(notifs), 1)
        self.assertEqual(notifs[0]["status"], "won")
        # Payout should be stake * 1.70 = 170 (since voided leg is 1.00)
        self.assertEqual(notifs[0]["payout"], 170)
        self.assertEqual(database.get_wallet_balance(self.user_id), bal_start + 170)

    def test_full_cancelled_match_refund(self):
        # Single bet on Match 1
        database.deduct_coins(self.user_id, 300, tx_type="bet_placed")
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO user_bets (user_id, bet_type, amount, total_odd, potential_win, status)
                VALUES (?, 'single', 300, 2.00, 600, 'pending')
            """, (self.user_id,))
            b_id = cursor.lastrowid
            cursor.execute("""
                INSERT INTO bet_items (bet_id, match_id, outcome_type, odd, status)
                VALUES (?, ?, 'p1', 2.00, 'pending')
            """, (b_id, self.m1_id))

        bal_start = database.get_wallet_balance(self.user_id)

        # Match 1 cancelled -> full refund
        notifs = settlement_engine.settle_match_predictions(self.m1_id, score1=0, score2=0, match_status="cancelled")
        self.assertEqual(len(notifs), 1)
        self.assertEqual(notifs[0]["status"], "refunded")
        self.assertEqual(notifs[0]["payout"], 300)
        self.assertEqual(database.get_wallet_balance(self.user_id), bal_start + 300)


if __name__ == "__main__":
    unittest.main()
