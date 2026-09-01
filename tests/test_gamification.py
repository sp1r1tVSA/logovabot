"""
tests/test_gamification.py
Unit tests for LOGOVO.BET Progression, Streaks & Achievements (Secondary gamification).
"""

import unittest
import database


class TestGamificationEngine(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self.user_id = 999901
        with database.transaction() as conn:
            conn.execute("DELETE FROM user_progression WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM user_achievements WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM user_wallets WHERE user_id = ?", (self.user_id,))

    def test_progression_and_level_up(self):
        # 1. Initial progression
        p = database.get_or_create_progression(self.user_id)
        self.assertEqual(p["level"], 1)
        self.assertEqual(p["current_xp"], 0)

        # 2. Add XP (e.g. 500 XP -> should level up)
        res = database.add_user_xp(self.user_id, 500)
        self.assertTrue(res["leveled_up"])
        self.assertGreater(res["level"], 1)
        self.assertGreater(res["reward_coins"], 0)

        # 3. Check wallet got level up coins
        w = database.get_or_create_wallet(self.user_id)
        self.assertGreaterEqual(w["balance"], 1000 + res["reward_coins"])

    def test_login_streak(self):
        streak_info = database.check_and_update_login_streak(self.user_id)
        self.assertGreaterEqual(streak_info["streak"], 1)
        self.assertGreaterEqual(streak_info["streak_shield_count"], 1)

    def test_achievements_unlock_and_claim(self):
        # 1. Unlock achievement
        database.unlock_achievement(self.user_id, "ACH_FIRST_BET")
        achievements = database.get_user_achievements(self.user_id)
        first_bet_ach = next((a for a in achievements if a["id"] == "ACH_FIRST_BET"), None)
        self.assertIsNotNone(first_bet_ach)
        self.assertEqual(first_bet_ach["is_unlocked"], 1)
        self.assertEqual(first_bet_ach["is_claimed"], 0)

        # 2. Claim achievement
        success, msg, reward = database.claim_achievement_reward(self.user_id, "ACH_FIRST_BET")
        self.assertTrue(success)
        self.assertGreater(reward["coins"], 0)


if __name__ == "__main__":
    unittest.main()
