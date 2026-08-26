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

    def test_club_card_debts_excludes_future_and_open_rounds(self):
        """Test that get_club_card_data does not count future unopened rounds or open tours as debts."""
        config.DEBT_TRACKING_START_DATETIME = "01.01.2026 00:00"
        database.register_user(2001, "racing_mgr", "manager", "Расинг")
        database.register_user(2002, "braga_mgr", "manager", "Брага")
        database.register_user(2003, "porto_mgr", "manager", "Порту")

        now = datetime.datetime.now()
        future_dl = (now + datetime.timedelta(days=3)).strftime("%d.%m.%Y %H:%M")
        past_dl = (now - datetime.timedelta(days=2)).strftime("%d.%m.%Y %H:%M")

        with database.transaction() as conn:
            c = conn.cursor()
            # Rounds 1..24 played, 25-26 open with future deadline, 27-30 future unopened
            c.execute("INSERT INTO rounds (round_number, is_open, deadline) VALUES (25, 1, ?)", (future_dl,))
            c.execute("INSERT INTO rounds (round_number, is_open, deadline) VALUES (26, 1, ?)", (future_dl,))
            c.execute("INSERT INTO rounds (round_number, is_open, deadline) VALUES (27, 0, NULL)")
            c.execute("INSERT INTO rounds (round_number, is_open, deadline) VALUES (28, 0, NULL)")
            c.execute("INSERT INTO rounds (round_number, is_open, deadline) VALUES (29, 0, NULL)")
            c.execute("INSERT INTO rounds (round_number, is_open, deadline) VALUES (30, 0, NULL)")

            # Pending matches for Racing in rounds 25..30
            c.execute("INSERT INTO matches (round_number, player1_team, player2_team, status) VALUES (25, 'Расинг', 'Брага', 'pending')")
            c.execute("INSERT INTO matches (round_number, player1_team, player2_team, status) VALUES (26, 'Порту', 'Расинг', 'pending')")
            c.execute("INSERT INTO matches (round_number, player1_team, player2_team, status) VALUES (27, 'Расинг', 'Брага', 'pending')")
            c.execute("INSERT INTO matches (round_number, player1_team, player2_team, status) VALUES (28, 'Расинг', 'Порту', 'pending')")
            c.execute("INSERT INTO matches (round_number, player1_team, player2_team, status) VALUES (29, 'Брага', 'Расинг', 'pending')")
            c.execute("INSERT INTO matches (round_number, player1_team, player2_team, status) VALUES (30, 'Порту', 'Расинг', 'pending')")

        card = database.get_club_card_data("Расинг")
        # Racing has 6 pending matches, but 0 debts!
        self.assertEqual(card["debts_count"], 0)
        self.assertEqual(len(card["pending_matches"]), 6)
        self.assertTrue(all(not m["is_overdue"] for m in card["pending_matches"]))

        # Now add a past closed round with expired deadline and unplayed match
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO rounds (round_number, is_open, deadline) VALUES (20, 0, ?)", (past_dl,))
            c.execute("INSERT INTO matches (round_number, player1_team, player2_team, status) VALUES (20, 'Расинг', 'Брага', 'pending')")

        card_with_debt = database.get_club_card_data("Расинг")
        self.assertEqual(card_with_debt["debts_count"], 1)

    def test_club_card_avatar_cropping_and_rendering(self):
        """Test generating club card with non-square custom avatar."""
        from PIL import Image
        import club_card_generator

        # Create temporary non-square avatar (200x120)
        tf_av = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        av_path = tf_av.name
        tf_av.close()

        try:
            test_img = Image.new("RGB", (200, 120), color=(100, 150, 200))
            test_img.save(av_path)

            card_data = {
                "team_name": "Расинг",
                "manager": {"username": "ch1lyx", "warn_count": 0, "telegram_id": 99999},
                "league_stats": {
                    "rank": 1, "played": 26, "wins": 20, "draws": 2, "losses": 4,
                    "goals_scored": 90, "goals_conceded": 36, "goal_diff": 54, "points": 62
                },
                "recent_form": ["L", "W", "W", "W", "D"],
                "cup_stats": {
                    "stage": "FINAL", "opponent": "Брага", "club_wins": 3, "opp_wins": 2, "status": "completed"
                },
                "top_scorers": [{"player_name": "Giacomo Raspadori", "goals": 39}],
                "top_assists": [{"player_name": "Noa Lang", "assists": 24}],
                "squad_count": 11,
                "debts_count": 0
            }

            buf = club_card_generator.generate_club_card(card_data, avatar_path=av_path)
            self.assertIsNotNone(buf)
            buf_bytes = buf.getvalue()
            self.assertTrue(buf_bytes.startswith(b'\x89PNG\r\n\x1a\n'))
        finally:
            try:
                os.remove(av_path)
            except Exception:
                pass

    def test_avatar_fetch_and_update_lifecycle(self):
        """Test get_cached_or_fetch_user_avatar lifecycle with fresh downloads and deletion."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from handlers import cabinet

        mock_bot = MagicMock()
        mock_file_obj = AsyncMock()
        async def fake_download(buf):
            buf.write(b"fake_image_bytes_here")
        mock_file_obj.download_to_memory = fake_download
        mock_bot.get_file = AsyncMock(return_value=mock_file_obj)

        photo_item1 = MagicMock()
        photo_item1.file_id = "photo_v1"
        photos_resp1 = MagicMock()
        photos_resp1.total_count = 1
        photos_resp1.photos = [[photo_item1]]
        mock_bot.get_user_profile_photos = AsyncMock(return_value=photos_resp1)

        user_id = 888777
        cabinet._user_avatar_file_ids.clear()

        # 1. Fetch avatar for user_id -> downloads photo_v1
        path1 = asyncio.run(cabinet.get_cached_or_fetch_user_avatar(mock_bot, user_id))
        self.assertIsNotNone(path1)
        self.assertTrue(os.path.exists(path1))
        self.assertEqual(cabinet._user_avatar_file_ids.get(user_id), "photo_v1")

        # 2. Call again with unchanged file_id -> reuses local cache without calling get_file
        mock_bot.get_file.reset_mock()
        path2 = asyncio.run(cabinet.get_cached_or_fetch_user_avatar(mock_bot, user_id))
        self.assertEqual(path1, path2)
        mock_bot.get_file.assert_not_called()

        # 3. User updates avatar in Telegram -> file_id changes to photo_v2
        photo_item2 = MagicMock()
        photo_item2.file_id = "photo_v2"
        photos_resp2 = MagicMock()
        photos_resp2.total_count = 1
        photos_resp2.photos = [[photo_item2]]
        mock_bot.get_user_profile_photos = AsyncMock(return_value=photos_resp2)

        path3 = asyncio.run(cabinet.get_cached_or_fetch_user_avatar(mock_bot, user_id))
        self.assertEqual(cabinet._user_avatar_file_ids.get(user_id), "photo_v2")
        mock_bot.get_file.assert_called_once_with("photo_v2")

        # 4. User removes avatar in Telegram -> total_count = 0
        photos_resp_empty = MagicMock()
        photos_resp_empty.total_count = 0
        photos_resp_empty.photos = []
        mock_bot.get_user_profile_photos = AsyncMock(return_value=photos_resp_empty)

        path4 = asyncio.run(cabinet.get_cached_or_fetch_user_avatar(mock_bot, user_id))
        self.assertIsNone(path4)
        self.assertNotIn(user_id, cabinet._user_avatar_file_ids)
        self.assertFalse(os.path.exists(path1))


if __name__ == "__main__":
    unittest.main()
