import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import config
import database
from handlers.admin import (
    admin_squads_status_command,
    admin_squads_view_cb,
    admin_squads_all_cb,
    admin_squads_remind_cb,
    _format_division_squads_status_html,
    _format_all_divisions_squads_summary_html,
)


class TestSquadsStatus(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)
        self.orig_config_path = config.DB_PATH
        self.orig_database_path = database.DB_PATH
        config.DB_PATH = self.temp_db_path
        database.DB_PATH = self.temp_db_path
        database.init_db()

        self.admin_id = 999111
        config.ADMIN_IDS = [self.admin_id]

    def tearDown(self):
        config.DB_PATH = self.orig_config_path
        database.DB_PATH = self.orig_database_path
        try:
            os.remove(self.temp_db_path)
        except Exception:
            pass

    def _build_mock_update(self, user_id: int, message_text: str = None, callback_data: str = None, args: list = None):
        update = MagicMock()
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        update.effective_chat = MagicMock()
        update.effective_chat.id = user_id
        update.effective_chat.type = "private"

        if callback_data:
            query = MagicMock()
            query.from_user = update.effective_user
            query.data = callback_data
            query.answer = AsyncMock()
            query.edit_message_text = AsyncMock()
            query.message = MagicMock()
            query.message.photo = None
            query.message.caption = None
            query.message.document = None
            query.message.is_topic_message = False
            query.message.chat_id = user_id
            update.callback_query = query
            update.message = None
        else:
            update.callback_query = None
            msg = MagicMock()
            msg.text = message_text
            msg.reply_text = AsyncMock()
            msg.message_thread_id = None
            update.message = msg

        context = MagicMock()
        context.args = args or []
        context.bot = MagicMock()
        context.bot.send_message = AsyncMock()
        return update, context

    def test_database_squads_status_calculation(self):
        """Verify get_division_squads_status categorizes ready, partial, empty, and vacant clubs correctly."""
        div_id = database.create_division(name="Дивизион Тест", code="DIV_TEST")

        with database.transaction() as conn:
            # 1. Coach with READY squad (12 players)
            conn.execute("INSERT INTO users (telegram_id, username, team_name, division_id) VALUES (101, 'coach_ready', 'Порту', ?)", (div_id,))
            for i in range(12):
                conn.execute("INSERT INTO squad_players (team_name, player_name) VALUES ('Порту', ?)", (f"Player_P_{i}",))

            # 2. Coach with PARTIAL squad (5 players)
            conn.execute("INSERT INTO users (telegram_id, username, team_name, division_id) VALUES (102, 'coach_partial', 'Аякс', ?)", (div_id,))
            for i in range(5):
                conn.execute("INSERT INTO squad_players (team_name, player_name) VALUES ('Аякс', ?)", (f"Player_A_{i}",))

            # 3. Coach with EMPTY squad (0 players)
            conn.execute("INSERT INTO users (telegram_id, username, team_name, division_id) VALUES (103, 'coach_empty', 'Селтик', ?)", (div_id,))

            # 4. Vacant club (in matches, but no owner in users)
            conn.execute("INSERT INTO matches (tournament_id, round_number, division_id, player1_team, player2_team, status) VALUES (1, 1, ?, 'Брага', 'Порту', 'pending')", (div_id,))

        status = database.get_division_squads_status(div_id)
        self.assertEqual(status["division_id"], div_id)
        self.assertEqual(status["total_clubs"], 4)
        self.assertEqual(status["ready_count"], 1)
        self.assertEqual(status["partial_count"], 1)
        self.assertEqual(status["empty_count"], 1)
        self.assertEqual(status["vacant_count"], 1)

        clubs_by_name = {c["club"]: c for c in status["clubs"]}
        self.assertEqual(clubs_by_name["Порту"]["status"], "ready")
        self.assertEqual(clubs_by_name["Порту"]["player_count"], 12)
        self.assertEqual(clubs_by_name["Порту"]["username"], "coach_ready")

        self.assertEqual(clubs_by_name["Аякс"]["status"], "partial")
        self.assertEqual(clubs_by_name["Аякс"]["player_count"], 5)

        self.assertEqual(clubs_by_name["Селтик"]["status"], "empty")
        self.assertEqual(clubs_by_name["Селтик"]["player_count"], 0)

        self.assertEqual(clubs_by_name["Брага"]["status"], "vacant")
        self.assertIsNone(clubs_by_name["Брага"]["user_id"])

        summary = database.get_all_divisions_squads_summary()
        self.assertGreaterEqual(summary["total_clubs"], 4)

    async def test_access_denied_for_regular_user(self):
        """Regular users must be denied access to /squads_status."""
        regular_user_id = 777000
        update, context = self._build_mock_update(user_id=regular_user_id, message_text="/squads")

        await admin_squads_status_command(update, context)

        update.message.reply_text.assert_called_once()
        reply_text = update.message.reply_text.call_args[0][0]
        self.assertIn("доступна только администраторам", reply_text)

    async def test_admin_squads_command_with_latin_and_cyrillic(self):
        """Test invocation via /squads 1 and /составы 1 by an administrator."""
        div_id = database.create_division(name="Первый Дивизион", code="DIV_1_TEST")
        with database.transaction() as conn:
            conn.execute("INSERT INTO users (telegram_id, username, team_name, division_id) VALUES (201, 'coach_a', 'Лидс', ?)", (div_id,))

        # 1. Via /squads with context.args = [str(div_id)]
        update1, context1 = self._build_mock_update(user_id=self.admin_id, message_text=f"/squads {div_id}", args=[str(div_id)])
        await admin_squads_status_command(update1, context1)
        update1.message.reply_text.assert_called_once()
        text1 = update1.message.reply_text.call_args[0][0]
        self.assertIn("Статус составов — Первый Дивизион", text1)
        self.assertIn("Лидс", text1)

        # 2. Via regex /составы {div_id} (context.args is empty)
        update2, context2 = self._build_mock_update(user_id=self.admin_id, message_text=f"/составы {div_id}", args=[])
        await admin_squads_status_command(update2, context2)
        update2.message.reply_text.assert_called_once()
        text2 = update2.message.reply_text.call_args[0][0]
        self.assertIn("Статус составов — Первый Дивизион", text2)
        self.assertIn("Лидс", text2)

    async def test_admin_squads_selector_without_args(self):
        """Test invocation without args in PM presents division selector."""
        update, context = self._build_mock_update(user_id=self.admin_id, message_text="/squads", args=[])
        await admin_squads_status_command(update, context)
        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        self.assertIn("Выберите дивизион для просмотра отчёта", text)
        markup = update.message.reply_text.call_args[1]["reply_markup"]
        self.assertIsNotNone(markup)

    async def test_admin_squads_view_and_all_callbacks(self):
        """Test callback query navigation between division view and all-divisions summary."""
        div_id = database.create_division(name="Второй Дивизион", code="DIV_2_TEST")

        # 1. View specific division
        update1, context1 = self._build_mock_update(user_id=self.admin_id, callback_data=f"admin_squads_view:{div_id}")
        await admin_squads_view_cb(update1, context1)
        update1.callback_query.edit_message_text.assert_called_once()
        text1 = update1.callback_query.edit_message_text.call_args[0][0]
        self.assertIn("Статус составов — Второй Дивизион", text1)

        # 2. View all divisions summary
        update2, context2 = self._build_mock_update(user_id=self.admin_id, callback_data="admin_squads_all")
        await admin_squads_all_cb(update2, context2)
        update2.callback_query.edit_message_text.assert_called_once()
        text2 = update2.callback_query.edit_message_text.call_args[0][0]
        self.assertIn("Статус составов — Вся лига", text2)

    async def test_admin_squads_remind_callback(self):
        """Test sending reminder DMs to coaches with empty/partial squads."""
        div_id = database.create_division(name="Дивизион Напоминаний", code="DIV_REMIND")
        with database.transaction() as conn:
            conn.execute("INSERT INTO users (telegram_id, username, team_name, division_id) VALUES (555001, 'debtor1', 'Милан', ?)", (div_id,))
            conn.execute("INSERT INTO users (telegram_id, username, team_name, division_id) VALUES (555002, 'debtor2', 'Интер', ?)", (div_id,))
            # Give debtor2 partial squad
            conn.execute("INSERT INTO squad_players (team_name, player_name) VALUES ('Интер', 'Lautaro')")

        update, context = self._build_mock_update(user_id=self.admin_id, callback_data=f"admin_squads_remind:{div_id}")
        await admin_squads_remind_cb(update, context)

        # Both debtor1 and debtor2 should have received a message
        self.assertEqual(context.bot.send_message.call_count, 2)
        sent_uids = [call.kwargs["chat_id"] for call in context.bot.send_message.call_args_list]
        self.assertIn(555001, sent_uids)
        self.assertIn(555002, sent_uids)

        self.assertGreaterEqual(update.callback_query.answer.call_count, 1)
        alert_calls = [c for c in update.callback_query.answer.call_args_list if c.args and "Напоминания отправлены" in c.args[0]]
        self.assertTrue(len(alert_calls) > 0)
        self.assertIn("Напоминания отправлены: 2 из 2", alert_calls[0].args[0])


if __name__ == "__main__":
    unittest.main()
