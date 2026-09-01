"""
tests/test_gamification.py
Unit tests for LOGOVO.BET v1.1 Progression, Quests, Streaks, Achievements & Duels.
"""

import unittest
import database


class TestGamificationEngine(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self.user_id = 999901
        with database.transaction() as conn:
            conn.execute("DELETE FROM user_progression WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM user_quests WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM user_achievements WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM user_wallets WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM pvp_duels WHERE creator_id = ? OR opponent_id = ?", (self.user_id, self.user_id))

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

    def test_quests_generation_and_claim(self):
        # 1. Generate quests
        quests = database.get_user_quests(self.user_id)
        self.assertGreaterEqual(len(quests), 3)

        # 2. Progress quest
        database.evaluate_quest_progress(self.user_id, "place_bets", 2)
        quests_after = database.get_user_quests(self.user_id)
        place_bet_q = next((q for q in quests_after if q["quest_type"] == "place_bets"), None)
        self.assertIsNotNone(place_bet_q)
        self.assertTrue(place_bet_q["is_completed"])

        # 3. Claim quest
        success, msg, reward = database.claim_quest_reward(self.user_id, place_bet_q["id"])
        self.assertTrue(success)
        self.assertGreater(reward["coins"], 0)
        self.assertGreater(reward["xp"], 0)

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

    def test_pvp_duels_lifecycle(self):
        opp_id = 999902
        with database.transaction() as conn:
            conn.execute("DELETE FROM user_wallets WHERE user_id = ?", (opp_id,))

        database.get_or_create_wallet(self.user_id)
        database.get_or_create_wallet(opp_id)

        # 1. Create Duel
        success, res = database.create_pvp_duel(self.user_id, 200, 1, [1, 2], {"1": "p1", "2": "x"})
        self.assertTrue(success)
        duel_id = res

        # 2. Get duels
        duels = database.get_pvp_duels()
        matching = next((d for d in duels if d["id"] == duel_id), None)
        self.assertIsNotNone(matching)
        self.assertEqual(matching["status"], "open")

        # 3. Accept Duel
        success, msg = database.accept_pvp_duel(duel_id, opp_id, {"1": "p2", "2": "p1"})
        self.assertTrue(success)

        duels_after = database.get_pvp_duels()
        matching_after = next((d for d in duels_after if d["id"] == duel_id), None)
        self.assertEqual(matching_after["status"], "active")
        self.assertEqual(matching_after["opponent_id"], opp_id)


if __name__ == "__main__":
    unittest.main()
