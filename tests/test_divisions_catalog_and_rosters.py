import asyncio
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import config
import database
from handlers.cabinet import (
    show_clubs_catalog_divisions,
    show_clubs_catalog_for_division,
)
from handlers.admin import (
    admin_edit_club_select,
    admin_rosters_for_division,
    _build_debts_summary,
    _club_owner_labels,
    _post_or_update_debts_for_division,
)


class TestDivisionsCatalogAndRosters(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.init_db()
        self.admin_id = 999123

        # Create two distinct test divisions
        uid = uuid.uuid4().hex[:6].upper()
        self.div_a_code = f"DIV_A_{uid}"
        self.div_b_code = f"DIV_B_{uid}"
        self.div_a_id = database.create_division(name="Первый Дивизион", code=self.div_a_code)
        self.div_b_id = database.create_division(name="Второй Дивизион", code=self.div_b_code)

        # Create test users in these divisions
        self.user_a1_id = 99901
        self.user_a2_id = 99902
        self.user_b1_id = 99903

        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT OR REPLACE INTO users (telegram_id, username, team_name, role, division_id)
                VALUES (?, 'user_a1', 'Real Madrid', 'player', ?)
            """, (self.user_a1_id, self.div_a_id))
            c.execute("""
                INSERT OR REPLACE INTO users (telegram_id, username, team_name, role, division_id)
                VALUES (?, 'user_a2', 'Barcelona', 'player', ?)
            """, (self.user_a2_id, self.div_a_id))
            c.execute("""
                INSERT OR REPLACE INTO users (telegram_id, username, team_name, role, division_id)
                VALUES (?, 'user_b1', 'Arsenal', 'player', ?)
            """, (self.user_b1_id, self.div_b_id))

    async def asyncTearDown(self):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM matches WHERE division_id IN (?, ?)", (self.div_a_id, self.div_b_id))
            c.execute("DELETE FROM rounds WHERE division_id IN (?, ?)", (self.div_a_id, self.div_b_id))
            c.execute("DELETE FROM division_topics WHERE division_id IN (?, ?)", (self.div_a_id, self.div_b_id))
            c.execute("DELETE FROM users WHERE telegram_id IN (?, ?, ?)", (self.user_a1_id, self.user_a2_id, self.user_b1_id))
            c.execute("DELETE FROM divisions WHERE id IN (?, ?)", (self.div_a_id, self.div_b_id))

    def _build_mock_update(self, callback_data: str):
        update = MagicMock()
        update.effective_user = MagicMock()
        update.effective_user.id = self.admin_id
        update.effective_chat = MagicMock()
        update.effective_chat.id = self.admin_id

        query = MagicMock()
        query.from_user.id = self.admin_id
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = MagicMock()
        query.message.photo = None
        query.message.delete = AsyncMock()
        query.message.is_topic_message = False
        query.message.chat_id = self.admin_id
        query.message.message_thread_id = None

        update.callback_query = query
        update.message = None
        return update

    async def test_get_division_teams_isolation(self):
        """Test database.get_division_teams returns only teams belonging to the specified division."""
        teams_a = database.get_division_teams(self.div_a_id)
        teams_b = database.get_division_teams(self.div_b_id)

        self.assertIn("Real Madrid", teams_a)
        self.assertIn("Barcelona", teams_a)
        self.assertNotIn("Arsenal", teams_a)

        self.assertIn("Arsenal", teams_b)
        self.assertNotIn("Real Madrid", teams_b)
        self.assertNotIn("Barcelona", teams_b)

    async def test_get_clubs_summary_for_division(self):
        """Test database.get_clubs_summary_for_division returns clubs summary only for that division."""
        summary_a = database.get_clubs_summary_for_division(self.div_a_id)
        summary_b = database.get_clubs_summary_for_division(self.div_b_id)

        names_a = [c["team_name"] for c in summary_a]
        names_b = [c["team_name"] for c in summary_b]

        self.assertIn("Real Madrid", names_a)
        self.assertIn("Barcelona", names_a)
        self.assertNotIn("Arsenal", names_a)

        self.assertIn("Arsenal", names_b)
        self.assertNotIn("Real Madrid", names_b)

    async def test_show_clubs_catalog_divisions(self):
        """Test Step 1 of Clubs Catalog: lists active divisions."""
        update = self._build_mock_update(callback_data="cb_clubs_catalog")
        context = MagicMock()
        context.bot.send_message = AsyncMock()

        await show_clubs_catalog_divisions(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        args, kwargs = update.callback_query.edit_message_text.call_args
        text = args[0]
        reply_markup = kwargs.get("reply_markup")

        self.assertIn("КАТАЛОГ КЛУБОВ ПО ДИВИЗИОНАМ", text)
        buttons_cb = [b.callback_data for row in reply_markup.inline_keyboard for b in row]
        self.assertIn(f"clubs_catalog_div:{self.div_a_id}", buttons_cb)
        self.assertIn(f"clubs_catalog_div:{self.div_b_id}", buttons_cb)
        self.assertIn("main_menu", buttons_cb)

    async def test_show_clubs_catalog_for_division(self):
        """Test Step 2 of Clubs Catalog: lists clubs for chosen division with back button."""
        update = self._build_mock_update(callback_data=f"clubs_catalog_div:{self.div_a_id}")
        context = MagicMock()
        context.bot.send_message = AsyncMock()

        await show_clubs_catalog_for_division(update, context)

        update.callback_query.edit_message_text.assert_called_once()
        args, kwargs = update.callback_query.edit_message_text.call_args
        text = args[0]
        reply_markup = kwargs.get("reply_markup")

        self.assertIn("ПЕРВЫЙ ДИВИЗИОН", text.upper())
        buttons_cb = [b.callback_data for row in reply_markup.inline_keyboard for b in row]
        self.assertIn("view_club_Real Madrid", buttons_cb)
        self.assertIn("view_club_Barcelona", buttons_cb)
        self.assertNotIn("view_club_Arsenal", buttons_cb)
        self.assertIn("cb_clubs_catalog", buttons_cb)

    async def test_admin_rosters_for_division(self):
        """Составы дивизиона — единственная точка входа, глобального списка больше нет."""
        update = self._build_mock_update(callback_data=f"admin_roster_div:{self.div_a_id}")
        context = MagicMock()
        context.user_data = {}

        with patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin.is_admin", return_value=True):
            await admin_rosters_for_division(update, context)

        self.assertEqual(context.user_data.get("admin_roster_div_id"), self.div_a_id)

        update.callback_query.edit_message_text.assert_called_once()
        args, kwargs = update.callback_query.edit_message_text.call_args
        text = args[0]
        reply_markup = kwargs.get("reply_markup")

        self.assertIn("Первый Дивизион", text)
        buttons_cb = [b.callback_data for row in reply_markup.inline_keyboard for b in row]
        self.assertIn("admin_squad_view_Real Madrid", buttons_cb)
        self.assertIn("admin_squad_view_Barcelona", buttons_cb)
        self.assertNotIn("admin_squad_view_Arsenal", buttons_cb)
        # Возврат — в карточку своего дивизиона, а не в глобальный экран составов
        self.assertIn(f"admin_div_view_{self.div_a_id}", buttons_cb)
        self.assertNotIn("admin_manage_squads", buttons_cb)
        # Утилита «добавить из матчей» работает в скоупе дивизиона
        self.assertIn(f"admin_squad_add_missing_div:{self.div_a_id}", buttons_cb)
        self.assertNotIn("admin_squad_add_missing_all", buttons_cb)

    async def test_build_debts_summary_with_division_filter(self):
        """Test _build_debts_summary correctly isolates unplayed matches by division."""
        past_dl = "01.01.2025 00:00"
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO rounds (round_number, is_open, deadline, division_id) VALUES (1, 0, ?, ?)",
                (past_dl, self.div_a_id)
            )
            conn.execute(
                "INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, status, division_id) "
                "VALUES (1, ?, ?, 'Real Madrid', 'Barcelona', 'pending', ?)",
                (self.user_a1_id, self.user_a2_id, self.div_a_id)
            )

        summary_a, count_a = await _build_debts_summary(division_id=self.div_a_id, division_name="Первый Дивизион")
        summary_b, count_b = await _build_debts_summary(division_id=self.div_b_id, division_name="Второй Дивизион")

        self.assertIsNotNone(summary_a)
        self.assertIn("ПЕРВЫЙ ДИВИЗИОН", summary_a)
        self.assertIn("Real Madrid", summary_a)
        self.assertIn("Barcelona", summary_a)
        self.assertGreater(count_a, 0)

        # Division B has no unplayed matches
        self.assertIsNone(summary_b)
        self.assertEqual(count_b, 0)

    async def test_post_or_update_debts_for_division(self):
        """Test _post_or_update_debts_for_division posts to the division's bound topic."""
        # Create unplayed match in Division A
        past_dl = "01.01.2025 00:00"
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO rounds (round_number, is_open, deadline, division_id) VALUES (1, 0, ?, ?)",
                (past_dl, self.div_a_id)
            )
            conn.execute(
                "INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, status, division_id) "
                "VALUES (1, ?, ?, 'Real Madrid', 'Barcelona', 'pending', ?)",
                (self.user_a1_id, self.user_a2_id, self.div_a_id)
            )

        # Bind division A to topic 555
        database.set_division_topic(self.div_a_id, "previews", 555)

        context = MagicMock()
        context.bot.send_message = AsyncMock()
        sent_msg = MagicMock()
        sent_msg.message_id = 777
        context.bot.send_message.return_value = sent_msg

        with patch("database.get_group_id", return_value=-100123):
            success, count = await _post_or_update_debts_for_division(
                context, division_id=self.div_a_id, division_name="Первый Дивизион"
            )

        self.assertTrue(success)
        self.assertGreater(count, 0)
        context.bot.send_message.assert_called_once()
        _, call_kwargs = context.bot.send_message.call_args
        self.assertEqual(call_kwargs.get("chat_id"), -100123)
        self.assertEqual(call_kwargs.get("message_thread_id"), 555)
        self.assertIn("ПЕРВЫЙ ДИВИЗИОН", call_kwargs.get("text"))


class TestSeededDivisionRoster(unittest.IsolatedAsyncioTestCase):
    """Сид-состав дивизиона виден до того, как в нём кто-то зарегистрировался.

    Иначе получается замкнутый круг: клуб появляется в списке, только когда его
    уже кто-то занял, а занять его админу не из чего — пустой сезон невозможно
    расписать по тренерам.
    """

    async def asyncSetUp(self):
        database.init_db()
        database.ensure_canonical_divisions()
        self.admin_id = 999124
        self.coach_id = 99911
        div_five = database.get_division_by_code("DIV_5")
        self.div_five_id = div_five["id"]

        with database.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO users (telegram_id, username, team_name, role, division_id) "
                "VALUES (?, 'coach_no_club', NULL, 'player', ?)",
                (self.coach_id, self.div_five_id)
            )

    async def asyncTearDown(self):
        with database.transaction() as conn:
            conn.execute("DELETE FROM users WHERE telegram_id = ?", (self.coach_id,))

    async def test_division_teams_come_from_the_seeded_roster(self):
        teams = database.get_division_teams(self.div_five_id)

        for club in config.DIVISION_CLUBS["DIV_5"]:
            self.assertIn(club, teams)

    async def test_seeded_roster_is_scoped_to_its_own_division(self):
        teams = database.get_division_teams(self.div_five_id)

        for club in config.DIVISION_CLUBS["DIV_1"]:
            self.assertNotIn(club, teams)

    async def test_admin_club_picker_offers_the_seeded_roster(self):
        """«Изменить клуб» для тренера без клуба предлагает все 16 клубов его дивизиона."""
        update = MagicMock()
        update.effective_user = MagicMock()
        update.effective_user.id = self.admin_id
        query = MagicMock()
        query.from_user.id = self.admin_id
        query.data = f"admin_edit_club_select_{self.coach_id}"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        context = MagicMock()
        context.user_data = {}

        with patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin.is_admin", return_value=True):
            await admin_edit_club_select(update, context)

        query.edit_message_text.assert_called_once()
        _, kwargs = query.edit_message_text.call_args
        labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]

        offered = context.user_data[f"admin_edit_clubs_{self.coach_id}"]
        for club in config.DIVISION_CLUBS["DIV_5"]:
            self.assertIn(club, offered)
        # Клубов никто не занял — ни один не должен быть помечен красным.
        self.assertTrue(any("Реал Мадрид (свободен)" in label for label in labels))
        self.assertFalse(any(label.startswith("🔴") for label in labels))

    async def test_club_owner_without_username_still_marks_the_club_occupied(self):
        """Владелец без @username не превращает свой клуб в «свободен».

        set_player_club снимает прежнего владельца молча, поэтому неверная
        подпись стоила бы тренеру клуба.
        """
        with database.transaction() as conn:
            conn.execute(
                "UPDATE users SET username = NULL, team_name = 'Челси' WHERE telegram_id = ?",
                (self.coach_id,)
            )

        labels = _club_owner_labels(
            [dict(u) for u in database.list_users()],
            division_id=self.div_five_id,
        )

        self.assertEqual(labels.get("челси"), f"ID {self.coach_id}")


if __name__ == "__main__":
    unittest.main()
