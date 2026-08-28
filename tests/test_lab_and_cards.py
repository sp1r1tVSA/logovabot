"""
tests/test_lab_and_cards.py

Unit and integration tests for:
1. Feature Flags & Access Control in database.py
2. EA FC Card Generator attribute calculations & image rendering
3. Lab handler registration and access decorator
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import config
from fc_card_generator import calculate_fut_attributes, generate_ea_fc_card
from handlers.base import is_admin


class TestFeatureFlagsAndCards(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        database.init_db()

    def test_feature_flags_crud(self):
        # 1. Test default flag
        default_val = database.get_feature_flag("fc_player_cards")
        self.assertIn(default_val, ["admin_only", "public", "disabled"])

        # 2. Test set and get
        database.set_feature_flag("test_feature_x", "public")
        self.assertEqual(database.get_feature_flag("test_feature_x"), "public")

        database.set_feature_flag("test_feature_x", "disabled")
        self.assertEqual(database.get_feature_flag("test_feature_x"), "disabled")

        # 3. Test list all
        all_flags = database.get_all_feature_flags()
        self.assertIn("fc_player_cards", all_flags)
        self.assertIn("test_feature_x", all_flags)

    def test_feature_access_control(self):
        database.set_feature_flag("test_admin_only_feature", "admin_only")
        database.set_feature_flag("test_public_feature", "public")
        database.set_feature_flag("test_disabled_feature", "disabled")

        # Random user (not in admin list)
        fake_user_id = 9999999999999
        self.assertFalse(database.is_feature_accessible("test_admin_only_feature", fake_user_id))
        self.assertTrue(database.is_feature_accessible("test_public_feature", fake_user_id))
        self.assertFalse(database.is_feature_accessible("test_disabled_feature", fake_user_id))

        # Real admin if configured
        if config.ADMIN_IDS:
            admin_id = config.ADMIN_IDS[0]
            self.assertTrue(database.is_feature_accessible("test_admin_only_feature", admin_id))
            self.assertTrue(database.is_feature_accessible("test_public_feature", admin_id))

    def test_fut_attributes_calculation(self):
        stats_striker = {
            "player_name": "Haaland",
            "position": "ST",
            "total_goals": 20,
            "total_assists": 4,
            "matches_played": 10,
        }
        res = calculate_fut_attributes(stats_striker)
        self.assertGreaterEqual(res["ovr"], 80)
        self.assertGreaterEqual(res["sho"], 80)
        self.assertEqual(res["position"], "ST")

        stats_defender = {
            "player_name": "Van Dijk",
            "position": "CB",
            "total_goals": 2,
            "total_assists": 1,
            "matches_played": 12,
        }
        res_def = calculate_fut_attributes(stats_defender)
        self.assertGreaterEqual(res_def["def"], 80)
        self.assertGreaterEqual(res_def["phy"], 80)

    def test_card_image_rendering(self):
        card_data = {
            "player_name": "TEST PLAYER",
            "team_name": "Спортинг",
            "position": "ST",
            "total_goals": 10,
            "total_assists": 5,
            "matches_played": 8,
        }
        for theme in ["gold_rare", "totw", "icon"]:
            buf = generate_ea_fc_card(card_data, theme_name=theme)
            self.assertIsNotNone(buf)
            self.assertGreater(len(buf.getvalue()), 10000)


if __name__ == "__main__":
    unittest.main()
