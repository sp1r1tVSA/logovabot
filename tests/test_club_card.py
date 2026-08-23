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

    def test_club_card_image_generator(self):
        """Test that club_card_generator.generate_club_card produces valid PNG bytes without error."""
        import club_card_generator
        card_data = {
            "team_name": "Фейеноорд",
            "manager": {"username": "georgiy", "warn_count": 0, "telegram_id": 12345},
            "league_stats": {
                "rank": 1, "played": 22, "wins": 18, "draws": 3, "losses": 1,
                "goals_scored": 58, "goals_conceded": 24, "goal_diff": 34, "points": 57
            },
            "recent_form": ["W", "W", "D", "W", "W"],
            "cup_stats": {
                "stage": "1/4", "opponent": "Бенфика", "club_wins": 2, "opp_wins": 1, "status": "active"
            },
            "top_scorers": [{"player_name": "Serhou Guirassy", "goals": 18}, {"player_name": "Sem Steijn", "goals": 14}],
            "top_assists": [{"player_name": "Raheem Sterling", "assists": 12}, {"player_name": "Jordan Lotomba", "assists": 8}],
            "squad_count": 18,
            "debts_count": 0
        }
        buf = club_card_generator.generate_club_card(card_data)
        self.assertIsNotNone(buf)
        buf_bytes = buf.getvalue()
        self.assertGreater(len(buf_bytes), 1000)
        # PNG signature check
        self.assertTrue(buf_bytes.startswith(b'\x89PNG\r\n\x1a\n'))

    def test_club_schedule_and_results_image_generator(self):
        """Test database.get_club_schedule_and_results and club_schedule_generator."""
        import club_schedule_generator
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO matches (round_number, player1_team, player2_team, player1_score, player2_score, status, tournament_type) "
                "VALUES (22, 'Фейеноорд', 'Бенфика', 5, 4, 'confirmed', 'league')"
            )
            m_id = c.lastrowid
            c.execute("INSERT INTO match_events (match_id, player_name, team_name, event_type, count) VALUES (?, 'Guirassy', 'Фейеноорд', 'goal', 2)", (m_id,))
            c.execute("INSERT INTO match_events (match_id, player_name, team_name, event_type, count) VALUES (?, 'Steijn', 'Фейеноорд', 'goal', 2)", (m_id,))

        sched_data = database.get_club_schedule_and_results("Фейеноорд")
        self.assertEqual(sched_data["played_count"], 1)
        self.assertEqual(len(sched_data["matches"]), 1)
        self.assertEqual(sched_data["matches"][0]["outcome"], "W")
        self.assertIn("Guirassy (2)", sched_data["matches"][0]["scorers"])

        buf = club_schedule_generator.generate_club_schedule(sched_data)
        self.assertIsNotNone(buf)
        buf_bytes = buf.getvalue()
        self.assertGreater(len(buf_bytes), 1000)
        self.assertTrue(buf_bytes.startswith(b'\x89PNG\r\n\x1a\n'))

    def test_racing_logo_lookup(self):
        """Test that get_team_logo_filename finds racing.png for 'Расинг' case-insensitively."""
        from table_generator import get_team_logo_filename
        import club_card_generator
        logo = get_team_logo_filename("Расинг")
        self.assertEqual(logo, "racing.png")
        logo_lower = get_team_logo_filename("расинг")
        self.assertEqual(logo_lower, "racing.png")

        # Test generating card for Racing
        card_data = {
            "team_name": "Расинг",
            "manager": {"username": "ch1lyx", "warn_count": 1, "telegram_id": 99999},
            "league_stats": {
                "rank": 1, "played": 23, "wins": 18, "draws": 1, "losses": 4,
                "goals_scored": 80, "goals_conceded": 32, "goal_diff": 48, "points": 55
            },
            "recent_form": ["L", "W", "L", "W", "W"],
            "cup_stats": {
                "stage": "FINAL", "opponent": "Брага", "club_wins": 3, "opp_wins": 2, "status": "completed"
            },
            "top_scorers": [{"player_name": "Giacomo Raspadori", "goals": 36}],
            "top_assists": [{"player_name": "Jamie Bynoe-Gittens", "assists": 23}],
            "squad_count": 11,
            "debts_count": 7
        }
        buf = club_card_generator.generate_club_card(card_data)
        self.assertIsNotNone(buf)
        self.assertTrue(buf.getvalue().startswith(b'\x89PNG\r\n\x1a\n'))

    def test_club_schedule_cup_stage_aggregation(self):
        """Test that multiple games in a cup series are aggregated into 1 row per stage."""
        import club_schedule_generator
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO cup_series (stage, series_num, team1_name, team2_name, team1_wins, team2_wins, winner_name, status) "
                "VALUES ('final', 1, 'Расинг', 'Брага', 3, 2, 'Расинг', 'completed')"
            )
            s_id = c.lastrowid
            # 5 games in the series
            for g_num, (h_s, a_s) in enumerate([(0, 1), (2, 1), (1, 3), (2, 1), (4, 3)], 1):
                c.execute(
                    "INSERT INTO matches (round_number, player1_team, player2_team, player1_score, player2_score, status, tournament_type, cup_stage, cup_series_id, game_num_in_series) "
                    "VALUES (-1, 'Расинг', 'Брага', ?, ?, 'confirmed', 'cup', 'final', ?, ?)",
                    (h_s, a_s, s_id, g_num)
                )
                m_id = c.lastrowid
                c.execute("INSERT INTO match_events (match_id, player_name, team_name, event_type, count) VALUES (?, 'Giacomo Raspadori', 'Расинг', 'goal', 1)", (m_id,))

        sched_data = database.get_club_schedule_and_results("Расинг")
        # Should aggregate all 5 final games into 1 row for КУБОК • ФИНАЛ
        final_rows = [m for m in sched_data["matches"] if m.get("tour_title") == "КУБОК • ФИНАЛ"]
        self.assertEqual(len(final_rows), 1)
        final_row = final_rows[0]
        self.assertEqual(final_row["home_score"], 3)
        self.assertEqual(final_row["away_score"], 2)
        self.assertEqual(final_row["outcome"], "W")
        self.assertIn("Матчи: 0:1, 2:1, 1:3, 2:1, 4:3", final_row["subline"])

    def test_check_group_card_access(self):
        """Test that check_group_card_access allows in private and admins, but denies non-admins in groups."""
        from handlers.cabinet import check_group_card_access
        from unittest.mock import MagicMock

        # 1. Private chat -> always allowed
        update_private = MagicMock()
        update_private.effective_chat.type = "private"
        update_private.effective_user.id = 999999
        self.assertTrue(check_group_card_access(update_private))

        # 2. Group chat + Non-admin -> denied
        update_group_user = MagicMock()
        update_group_user.effective_chat.type = "supergroup"
        update_group_user.effective_user.id = 999999
        self.assertFalse(check_group_card_access(update_group_user))

        # 3. Group chat + Admin -> allowed
        update_group_admin = MagicMock()
        update_group_admin.effective_chat.type = "supergroup"
        # Admin ID from config.ADMIN_IDS
        admin_id = config.ADMIN_IDS[0] if config.ADMIN_IDS else 123456
        if not config.ADMIN_IDS:
            config.ADMIN_IDS = [admin_id]
        update_group_admin.effective_user.id = admin_id
        self.assertTrue(check_group_card_access(update_group_admin))


if __name__ == "__main__":
    unittest.main()
