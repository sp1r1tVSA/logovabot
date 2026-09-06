import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import io
import os
import tempfile
import shutil
import database
from services.graphics.top_stats_generator import generate_top_stats_image
from services.graphics.table_generator import generate_league_table_image
from handlers.base import (
    show_division_table,
    show_division_scorers,
    show_division_assists,
)


class TestDivisionGraphicsAndHandlers(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_div_graphics.db")
        self.orig_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        database.init_db()

        # Register players
        self.p1_id = 1001
        self.p2_id = 1002
        self.p3_id = 1003
        self.p4_id = 1004

        database.register_user(self.p1_id, "user_1", team_name="Arsenal")
        database.register_user(self.p2_id, "user_2", team_name="Chelsea")
        database.register_user(self.p3_id, "user_3", team_name="Liverpool")
        database.register_user(self.p4_id, "user_4", team_name="Barcelona")

        # Create seasons and divisions
        self.s1_id = 1
        self.s2_id = database.create_season(name="Season 2", created_by=self.p1_id)

        self.div1_id = database.create_division(name="Первый дивизион", code="DIV_1_TEST")
        self.div2_id = database.create_division(name="Второй дивизион", code="DIV_2_TEST")

        database.assign_user_division(self.p1_id, self.div1_id)
        database.assign_user_division(self.p2_id, self.div1_id)
        database.assign_user_division(self.p3_id, self.div2_id)
        database.assign_user_division(self.p4_id, self.div2_id)

    def tearDown(self):
        database.DB_PATH = self.orig_db_path
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_database_season_and_division_filtering(self):
        """Verify get_teams_recent_form, get_top_scorers, get_top_assists filter strictly by season_id & division_id."""
        with database.transaction() as conn:
            cur = conn.cursor()
            # Match 1: Season 1, Div 1. Arsenal (3) vs Chelsea (1)
            cur.execute("""
                INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, player1_score, player2_score, status, division_id, season_id)
                VALUES (1, ?, ?, 'Arsenal', 'Chelsea', 3, 1, 'confirmed', ?, ?)
            """, (self.p1_id, self.p2_id, self.div1_id, self.s1_id))
            m1_id = cur.lastrowid

            # Match 2: Season 2, Div 1. Arsenal (0) vs Chelsea (2)
            cur.execute("""
                INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, player1_score, player2_score, status, division_id, season_id)
                VALUES (1, ?, ?, 'Arsenal', 'Chelsea', 0, 2, 'confirmed', ?, ?)
            """, (self.p1_id, self.p2_id, self.div1_id, self.s2_id))
            m2_id = cur.lastrowid

            # Match 3: Season 1, Div 2. Liverpool (2) vs Barcelona (2)
            cur.execute("""
                INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, player1_score, player2_score, status, division_id, season_id)
                VALUES (1, ?, ?, 'Liverpool', 'Barcelona', 2, 2, 'confirmed', ?, ?)
            """, (self.p3_id, self.p4_id, self.div2_id, self.s1_id))
            m3_id = cur.lastrowid

            # Events for m1 (Season 1, Div 1)
            cur.execute("INSERT INTO match_events (match_id, team_name, player_name, event_type, count) VALUES (?, 'Arsenal', 'Striker_S1_D1', 'goal', 3)", (m1_id,))
            cur.execute("INSERT INTO match_events (match_id, team_name, player_name, event_type, count) VALUES (?, 'Arsenal', 'Passer_S1_D1', 'assist', 2)", (m1_id,))

            # Events for m2 (Season 2, Div 1)
            cur.execute("INSERT INTO match_events (match_id, team_name, player_name, event_type, count) VALUES (?, 'Chelsea', 'Striker_S2_D1', 'goal', 2)", (m2_id,))
            cur.execute("INSERT INTO match_events (match_id, team_name, player_name, event_type, count) VALUES (?, 'Chelsea', 'Passer_S2_D1', 'assist', 1)", (m2_id,))

            # Events for m3 (Season 1, Div 2)
            cur.execute("INSERT INTO match_events (match_id, team_name, player_name, event_type, count) VALUES (?, 'Liverpool', 'Striker_S1_D2', 'goal', 2)", (m3_id,))
            cur.execute("INSERT INTO match_events (match_id, team_name, player_name, event_type, count) VALUES (?, 'Liverpool', 'Passer_S1_D2', 'assist', 2)", (m3_id,))

        # 1. Form Map Season 1 vs Season 2 in Div 1
        form_s1_d1 = database.get_teams_recent_form(limit=5, division_id=self.div1_id, season_id=self.s1_id)
        self.assertEqual(form_s1_d1.get("arsenal"), ["W"])
        self.assertEqual(form_s1_d1.get("chelsea"), ["L"])

        form_s2_d1 = database.get_teams_recent_form(limit=5, division_id=self.div1_id, season_id=self.s2_id)
        self.assertEqual(form_s2_d1.get("arsenal"), ["L"])
        self.assertEqual(form_s2_d1.get("chelsea"), ["W"])

        # 2. Top Scorers Season 1 Div 1
        scorers_s1_d1 = database.get_top_scorers(limit=10, division_id=self.div1_id, season_id=self.s1_id)
        p_names = [s["player_name"] for s in scorers_s1_d1]
        self.assertIn("Striker_S1_D1", p_names)
        self.assertNotIn("Striker_S2_D1", p_names)
        self.assertNotIn("Striker_S1_D2", p_names)

        # 3. Top Scorers Season 2 Div 1
        scorers_s2_d1 = database.get_top_scorers(limit=10, division_id=self.div1_id, season_id=self.s2_id)
        p_names_s2 = [s["player_name"] for s in scorers_s2_d1]
        self.assertIn("Striker_S2_D1", p_names_s2)
        self.assertNotIn("Striker_S1_D1", p_names_s2)

        # 4. Top Assists Season 1 Div 2
        assists_s1_d2 = database.get_top_assists(limit=10, division_id=self.div2_id, season_id=self.s1_id)
        passers_d2 = [a["player_name"] for a in assists_s1_d2]
        self.assertIn("Passer_S1_D2", passers_d2)
        self.assertNotIn("Passer_S1_D1", passers_d2)
        self.assertNotIn("Passer_S2_D1", passers_d2)

    def test_generate_top_stats_image_with_season_and_division(self):
        """Verify generate_top_stats_image accepts season_id and produces a valid PNG buffer."""
        buf = generate_top_stats_image(mode="goals", limit=10, division_id=self.div1_id, season_id=self.s1_id)
        self.assertIsInstance(buf, io.BytesIO)
        self.assertGreater(len(buf.getvalue()), 1000)

        buf_assists = generate_top_stats_image(mode="assists", limit=10, division_id=self.div1_id, season_id=self.s1_id)
        self.assertIsInstance(buf_assists, io.BytesIO)
        self.assertGreater(len(buf_assists.getvalue()), 1000)

    async def test_show_division_table_handler(self):
        """Verify show_division_table deletes old message and sends photo with division back button."""
        update = MagicMock()
        query = MagicMock()
        query.data = f"division_table:{self.s1_id}:{self.div1_id}"
        query.answer = AsyncMock()
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.is_topic_message = False
        query.message.delete = AsyncMock()
        update.callback_query = query
        update.effective_chat.id = 12345

        context = MagicMock()
        context.matches = None
        context.bot.send_photo = AsyncMock()

        await show_division_table(update, context)

        query.answer.assert_awaited_once()
        query.message.delete.assert_awaited_once()
        context.bot.send_photo.assert_awaited_once()

        kwargs = context.bot.send_photo.call_args.kwargs
        self.assertEqual(kwargs["chat_id"], 12345)
        self.assertIn("Турнирная таблица", kwargs["caption"])
        self.assertIn("Первый дивизион", kwargs["caption"])

        # Check back button callback data
        markup = kwargs["reply_markup"]
        back_btn = markup.inline_keyboard[0][0]
        self.assertEqual(back_btn.text, "« Назад к меню дивизиона")
        self.assertEqual(back_btn.callback_data, f"division_view:{self.s1_id}:{self.div1_id}")

    async def test_show_division_scorers_handler(self):
        """Verify show_division_scorers deletes old message and sends photo with division back button."""
        update = MagicMock()
        query = MagicMock()
        query.data = f"division_scorers:{self.s1_id}:{self.div1_id}"
        query.answer = AsyncMock()
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.is_topic_message = False
        query.message.delete = AsyncMock()
        update.callback_query = query
        update.effective_chat.id = 12345

        context = MagicMock()
        context.matches = None
        context.bot.send_photo = AsyncMock()

        await show_division_scorers(update, context)

        query.answer.assert_awaited_once()
        query.message.delete.assert_awaited_once()
        context.bot.send_photo.assert_awaited_once()

        kwargs = context.bot.send_photo.call_args.kwargs
        self.assertEqual(kwargs["chat_id"], 12345)
        self.assertIn("Топ бомбардиров", kwargs["caption"])
        markup = kwargs["reply_markup"]
        back_btn = markup.inline_keyboard[0][0]
        self.assertEqual(back_btn.callback_data, f"division_view:{self.s1_id}:{self.div1_id}")

    async def test_show_division_assists_handler(self):
        """Verify show_division_assists deletes old message and sends photo with division back button."""
        update = MagicMock()
        query = MagicMock()
        query.data = f"division_assists:{self.s1_id}:{self.div1_id}"
        query.answer = AsyncMock()
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.is_topic_message = False
        query.message.delete = AsyncMock()
        update.callback_query = query
        update.effective_chat.id = 12345

        context = MagicMock()
        context.matches = None
        context.bot.send_photo = AsyncMock()

        await show_division_assists(update, context)

        query.answer.assert_awaited_once()
        query.message.delete.assert_awaited_once()
        context.bot.send_photo.assert_awaited_once()

        kwargs = context.bot.send_photo.call_args.kwargs
        self.assertEqual(kwargs["chat_id"], 12345)
        self.assertIn("Топ ассистентов", kwargs["caption"])
        markup = kwargs["reply_markup"]
        back_btn = markup.inline_keyboard[0][0]
        self.assertEqual(back_btn.callback_data, f"division_view:{self.s1_id}:{self.div1_id}")

    def test_router_registration(self):
        """Verify register_all_handlers registers the division table, scorers, and assists handlers."""
        from handlers import register_all_handlers
        from telegram.ext import CallbackQueryHandler
        mock_app = MagicMock()
        registered_handlers = []
        mock_app.add_handler.side_effect = lambda h, *args, **kwargs: registered_handlers.append(h)

        register_all_handlers(mock_app)

        patterns = {}
        for h in registered_handlers:
            if isinstance(h, CallbackQueryHandler) and hasattr(h, "pattern") and h.pattern:
                patterns[h.pattern.pattern] = h.callback

        self.assertIn(r"^division_table:(\d+):(\d+)$", patterns)
        self.assertIn(r"^division_scorers:(\d+):(\d+)$", patterns)
        self.assertIn(r"^division_assists:(\d+):(\d+)$", patterns)
        self.assertEqual(patterns[r"^division_table:(\d+):(\d+)$"], show_division_table)
        self.assertEqual(patterns[r"^division_scorers:(\d+):(\d+)$"], show_division_scorers)
        self.assertEqual(patterns[r"^division_assists:(\d+):(\d+)$"], show_division_assists)


if __name__ == "__main__":
    unittest.main()
