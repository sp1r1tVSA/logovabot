"""
tests/test_fc_card_attributes.py

Unit and integration tests for the EA FC card generator: attribute
calculations and image rendering.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from services.graphics.fc_card_generator import calculate_fut_attributes, generate_ea_fc_card


class TestFcCards(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        database.init_db()

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
        for theme in ["kpl_standard", "kpl_star", "kpl_prime"]:
            buf = generate_ea_fc_card(card_data, theme_name=theme)
            self.assertIsNotNone(buf)
            self.assertGreater(len(buf.getvalue()), 10000)


if __name__ == "__main__":
    unittest.main()
