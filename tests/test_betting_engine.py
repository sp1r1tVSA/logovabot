import unittest
import database
import services.betting_engine as betting_engine


class TestBettingEngine(unittest.TestCase):
    def setUp(self):
        database.init_db()

    def test_calculate_odds_bounds_and_margin(self):
        odds = betting_engine.calculate_match_odds("Спортинг", "Бенфика")
        self.assertIn("odd_p1", odds)
        self.assertIn("odd_x", odds)
        self.assertIn("odd_p2", odds)
        self.assertIn("odd_tb25", odds)
        self.assertIn("odd_tm25", odds)
        self.assertIn("odd_btts_yes", odds)
        self.assertIn("odd_btts_no", odds)

        # Realistic decimal bounds
        self.assertGreaterEqual(odds["odd_p1"], 1.10)
        self.assertGreaterEqual(odds["odd_x"], 2.10)
        self.assertGreaterEqual(odds["odd_p2"], 1.10)
        self.assertLessEqual(odds["odd_p1"], 12.0)
        self.assertLessEqual(odds["odd_p2"], 12.0)

    def test_wallet_initialization_and_balance(self):
        test_user = 999111222
        # Clean previous if any
        with database.transaction() as conn:
            conn.execute("DELETE FROM user_wallets WHERE user_id = ?", (test_user,))
            conn.execute("DELETE FROM coin_transactions WHERE user_id = ?", (test_user,))

        wallet = database.get_or_create_wallet(test_user)
        self.assertEqual(wallet["balance"], 1000)
        self.assertEqual(database.get_wallet_balance(test_user), 1000)

        # Add coins
        new_bal = database.add_coins(test_user, 500, "test_add")
        self.assertEqual(new_bal, 1500)
        self.assertEqual(database.get_wallet_balance(test_user), 1500)

        # Deduct coins
        ok = database.deduct_coins(test_user, 300, "test_sub")
        self.assertTrue(ok)
        self.assertEqual(database.get_wallet_balance(test_user), 1200)

        # Over-deduct should fail
        fail_ok = database.deduct_coins(test_user, 5000, "overdraft")
        self.assertFalse(fail_ok)
        self.assertEqual(database.get_wallet_balance(test_user), 1200)

    def test_daily_bonus_and_cooldown(self):
        test_user = 999333444
        with database.transaction() as conn:
            conn.execute("DELETE FROM user_wallets WHERE user_id = ?", (test_user,))

        # 1. First claim succeeds
        ok, bal, msg = database.claim_daily_bonus(test_user, 250)
        self.assertTrue(ok)
        self.assertEqual(bal, 1250) # 1000 welcome + 250 bonus

        # 2. Immediate second claim fails due to 24h cooldown
        ok2, rem_h, msg2 = database.claim_daily_bonus(test_user, 250)
        self.assertFalse(ok2)
        self.assertGreaterEqual(rem_h, 23)

    def test_place_single_and_express_bet(self):
        test_user = 999555666
        with database.transaction() as conn:
            conn.execute("DELETE FROM user_wallets WHERE user_id = ?", (test_user,))
            conn.execute("DELETE FROM user_bets WHERE user_id = ?", (test_user,))
            conn.execute("DELETE FROM matches WHERE id IN (8881, 8882)")
            conn.execute("DELETE FROM bet_markets WHERE match_id IN (8881, 8882)")
            conn.execute(
                "INSERT OR REPLACE INTO rounds (round_number, is_open, deadline) VALUES (1, 1, '2099-01-01 23:59')"
            )
            conn.execute(
                "INSERT INTO matches (id, tournament_id, round_number, status) VALUES (8881, 1, 1, 'pending'), (8882, 1, 1, 'pending')"
            )

        database.save_bet_market(8881, 1, "Порту", "Брага", 1.80, 3.40, 3.20, 1.75, 1.95, 1.70, 2.05)
        database.save_bet_market(8882, 1, "Аякс", "ПСВ", 2.10, 3.30, 2.80, 1.60, 2.20, 1.65, 2.10)

        # 1. Single Bet (100 coins on P1 with odd 1.80)
        ok_single, bet_id_1 = database.place_user_bet(
            test_user, 100, [{"match_id": 8881, "outcome": "p1", "odd": 1.80}]
        )
        self.assertTrue(ok_single)
        self.assertEqual(database.get_wallet_balance(test_user), 900)

        # 2. Express Bet (200 coins on P1 1.80 * TB25 1.60 = 2.88)
        ok_express, bet_id_2 = database.place_user_bet(
            test_user, 200, [
                {"match_id": 8881, "outcome": "p1", "odd": 1.80},
                {"match_id": 8882, "outcome": "tb25", "odd": 1.60}
            ]
        )
        self.assertTrue(ok_express)
        self.assertEqual(database.get_wallet_balance(test_user), 700)

        # 3. Settle Match 8881 (Score: 2-1 -> P1 won)
        payouts_1 = database.settle_match_bets(8881, 2, 1)
        # Single bet wins: 100 * 1.80 = 180 coins
        self.assertEqual(len(payouts_1), 1)
        self.assertEqual(payouts_1[0]["payout"], 180)
        self.assertEqual(database.get_wallet_balance(test_user), 880)

        # 4. Settle Match 8882 (Score: 3-1 -> TB25 won)
        payouts_2 = database.settle_match_bets(8882, 3, 1)
        # Express bet wins: 200 * 2.88 = 576 coins (+ optional 500 level up reward if XP milestone reached)
        self.assertEqual(len(payouts_2), 1)
        self.assertEqual(payouts_2[0]["payout"], 576)
        self.assertIn(database.get_wallet_balance(test_user), [880 + 576, 880 + 576 + 500])

        # Verify bet history
        history = database.get_user_bets(test_user)
        self.assertEqual(len(history), 2)
        self.assertTrue(all(b["status"] == "won" for b in history))

        # Cleanup
        with database.transaction() as conn:
            conn.execute("DELETE FROM matches WHERE id IN (8881, 8882)")
            conn.execute("DELETE FROM bet_markets WHERE match_id IN (8881, 8882)")

    def test_betting_access_control(self):
        from handlers.betting import _check_betting_access
        import config

        admin_id = config.ADMIN_IDS[0] if config.ADMIN_IDS else 12345
        regular_user = 777888999

        # Set flag to admin_only (Lab mode)
        database.set_feature_flag("betting_market", "admin_only")
        self.assertTrue(_check_betting_access(admin_id))
        self.assertFalse(_check_betting_access(regular_user))

        # Set flag to public
        database.set_feature_flag("betting_market", "public")
        self.assertTrue(_check_betting_access(regular_user))

        # Set back to admin_only for safe lab isolation
        database.set_feature_flag("betting_market", "admin_only")


if __name__ == "__main__":
    unittest.main()
