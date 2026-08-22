import os
import sys
import tempfile
import sqlite3
import datetime
import unittest

# Ensure logovobot root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
import database


class TestClubCard(unittest.TestCase):
    def setUp(self):
        """Create a temporary isolated SQLite database for each test."""
        self.tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db_path = self.tf.name
        self.tf.close()

        self.orig_config_path = config.DB_PATH
        self.orig_database_path = database.DB_PATH

        config.DB_PATH = self.temp_db_path
        database.DB_PATH = self.temp_db_path
        database.init_db()

    def tearDown(self):
        """Restore original paths and cleanup temp db."""
        config.DB_PATH = self.orig_config_path
        database.DB_PATH = self.orig_database_path
        try:
            os.remove(self.temp_db_path)
        except Exception:
            pass

    def test_club_card_manager_and_stats(self):
        """Test that club card returns manager, standings, form, and retains history across manager changes."""
        # 1. Register manager for Porto
        database.register_user(1001, "porto_boss", "manager", "Порту")
        database.register_user(1002, "benfica_boss", "manager", "Бенфика")

        # 2. Add squad for Porto
        database.save_squad_players("Порту", ["Francisco Moura", "David Neres", "Galeno"])

        # 3. Create and confirm matches for Porto
        # Match 1: Porto 3 : 1 Benfica
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO matches (round_number, player1_team, player2_team, player1_score, player2_score, status, tournament_type) "
                "VALUES (1, 'Порту', 'Бенфика', 3, 1, 'confirmed', 'league')"
            )
            m1_id = c.lastrowid
            # Events for Match 1
            c.execute("INSERT INTO match_events (match_id, player_name, team_name, event_type, count) VALUES (?, 'David Neres', 'Порту', 'goal', 2)", (m1_id,))
            c.execute("INSERT INTO match_events (match_id, player_name, team_name, event_type, count) VALUES (?, 'Francisco Moura', 'Порту', 'goal', 1)", (m1_id,))
            c.execute("INSERT INTO match_events (match_id, player_name, team_name, event_type, count) VALUES (?, 'Galeno', 'Порту', 'assist', 2)", (m1_id,))

        # Fetch club card
        card = database.get_club_card_data("Порту")
        self.assertEqual(card["team_name"], "Порту")
        self.assertIsNotNone(card["manager"])
        self.assertEqual(card["manager"]["username"], "porto_boss")
        self.assertEqual(card["league_stats"]["played"], 1)
        self.assertEqual(card["league_stats"]["wins"], 1)
        self.assertEqual(card["league_stats"]["points"], 3)
        self.assertEqual(card["league_stats"]["goals_scored"], 3)
        self.assertEqual(card["recent_form"], ["W"])

        # Check top scorers
        self.assertEqual(len(card["top_scorers"]), 2)
        self.assertEqual(card["top_scorers"][0]["player_name"], "David Neres")
        self.assertEqual(card["top_scorers"][0]["goals"], 2)

        # Check top assists
        self.assertEqual(len(card["top_assists"]), 1)
        self.assertEqual(card["top_assists"][0]["player_name"], "Galeno")
        self.assertEqual(card["top_assists"][0]["assists"], 2)

        # Check squad stats
        squad_stats = database.get_club_squad_stats("Порту")
        self.assertEqual(len(squad_stats), 3)
        neres = next(p for p in squad_stats if p["player_name"] == "David Neres")
        self.assertEqual(neres["goals"], 2)

        # 4. Change manager: Replace porto_boss with new_boss
        with database.transaction() as conn:
            conn.execute("UPDATE users SET team_name = NULL WHERE telegram_id = 1001")
            database.register_user(1003, "new_porto_boss", "manager", "Порту")

        # Club card should still have all 3 goals, 1 win, and squad intact, but with new manager!
        card_after = database.get_club_card_data("Порту")
        self.assertEqual(card_after["manager"]["username"], "new_porto_boss")
        self.assertEqual(card_after["league_stats"]["points"], 3)
        self.assertEqual(card_after["top_scorers"][0]["player_name"], "David Neres")

    def test_club_match_history_and_summary(self):
        """Test get_club_match_history and get_all_clubs_summary."""
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO matches (round_number, player1_team, player2_team, player1_score, player2_score, status, tournament_type) "
                "VALUES (1, 'Аякс', 'ПСВ', 2, 2, 'confirmed', 'league')"
            )
            m_id = c.lastrowid
            c.execute("INSERT INTO match_events (match_id, player_name, team_name, event_type, count) VALUES (?, 'Brobbey', 'Аякс', 'goal', 2)", (m_id,))

        history = database.get_club_match_history("Аякс")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["opponent_team"], "ПСВ")
        self.assertEqual(history[0]["outcome"], "D")
        self.assertIn("Brobbey (2)", history[0]["scorers"])

        summary = database.get_all_clubs_summary()
        self.assertGreaterEqual(len(summary), 15)
        ajax = next(c for c in summary if c["team_name"] == "Аякс")
        self.assertEqual(ajax["points"], 1)
        self.assertEqual(ajax["draws"], 1)


if __name__ == "__main__":
    unittest.main()
