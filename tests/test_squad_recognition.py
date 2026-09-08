import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import database
from services.ai.squad_recognizer import MAX_SQUAD_PLAYERS, _parse_players


class TestSquadOcrParsing(unittest.TestCase):
    def test_cleans_ratings_positions_and_badges(self):
        players = _parse_players({"players": [
            {"name": "108 Viktor Gyökeres", "position": "ST"},
            {"name": "ЦАП Francisco Trincão 113", "position": None},
            {"name": "Pedro Gonçalves ⚽", "position": "cm"},
        ]})
        self.assertEqual(
            [(p["player_name"], p["position"]) for p in players],
            [("Viktor Gyökeres", "ST"), ("Francisco Trincão", None), ("Pedro Gonçalves", "cm")],
        )

    def test_drops_duplicates_and_nameless_rows(self):
        players = _parse_players({"players": [
            {"name": "Lamine Yamal"},
            {"name": "lamine yamal", "position": "RW"},
            {"name": "   "},
            {"name": "99"},
            {"name": "x" * 80},
        ]})
        self.assertEqual([p["player_name"] for p in players], ["Lamine Yamal"])

    def test_accepts_plain_strings_and_caps_length(self):
        names = [f"Player{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(MAX_SQUAD_PLAYERS + 10)]
        players = _parse_players({"players": names})
        self.assertEqual(len(players), MAX_SQUAD_PLAYERS)
        self.assertIsNone(players[0]["position"])

    def test_malformed_payload_yields_empty_list(self):
        self.assertEqual(_parse_players({}), [])
        self.assertEqual(_parse_players({"players": "Gyökeres"}), [])


class TestReplaceSquad(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orig_db_path = database.DB_PATH
        cls._tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = config.DB_PATH = os.path.join(cls._tmp.name, "league.db")
        database.init_db()

    @classmethod
    def tearDownClass(cls):
        database.close_thread_connection()
        database.DB_PATH = config.DB_PATH = cls.orig_db_path
        cls._tmp.cleanup()

    def setUp(self):
        database.clear_squad("Sporting")

    def test_replace_swaps_the_whole_roster(self):
        database.add_squad("Sporting", ["Old One", "Old Two"])
        deleted, added = database.replace_squad("Sporting", [
            {"player_name": "Viktor Gyökeres", "position": "ST"},
            {"player_name": "Francisco Trincão", "position": None},
        ])
        self.assertEqual(deleted, 2)
        self.assertEqual(added, 2)
        self.assertEqual(database.get_squad("Sporting"), ["Viktor Gyökeres", "Francisco Trincão"])

    def test_add_keeps_existing_players(self):
        database.add_squad("Sporting", ["Old One"])
        database.add_squad("Sporting", [{"player_name": "Viktor Gyökeres", "position": "ST"}])
        self.assertEqual(sorted(database.get_squad("Sporting")), ["Old One", "Viktor Gyökeres"])


if __name__ == "__main__":
    unittest.main()
