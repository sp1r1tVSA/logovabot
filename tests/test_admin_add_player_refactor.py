import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from telegram.ext import ConversationHandler
import handlers.admin as admin_handlers
from handlers.admin import (
    ADMIN_EXPECT_PLAYER_USERNAME,
    ADMIN_EXPECT_PLAYER_DIVISION,
    ADMIN_EXPECT_PLAYER_CLUB,
    ADMIN_EXPECT_MANUAL_CLUB,
    admin_add_player_start,
    admin_add_player_username,
    admin_add_player_div_callback,
    admin_add_player_club_callback,
    admin_add_player_manual_club_callback,
    admin_add_player_manual_club_text,
)


class TestAdminAddPlayerRefactor(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        database.init_db()
        database.ensure_canonical_divisions()

    def test_clubs_not_in_admin_module(self):
        """Verify that hardcoded CLUBS is not imported in handlers.admin."""
        self.assertNotIn("CLUBS", admin_handlers.__dict__, "CLUBS should not be imported in handlers.admin")

    def test_assign_player_to_club_new_user(self):
        """Verify assign_player_to_club creates new user with division_id."""
        username = "new_fifa_player_99"
        club = "Боруссия Д"
        division_id = 3

        # Cleanup if existed
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM users WHERE LOWER(username) = LOWER(?)", (username,))
            c.execute("DELETE FROM users WHERE LOWER(team_name) = LOWER(?)", (club,))

        temp_id, old_username = database.assign_player_to_club(username, club, division_id=division_id)
        self.assertLess(temp_id, 0)
        self.assertIsNone(old_username)

        user = database.get_user(temp_id)
        self.assertIsNotNone(user)
        self.assertEqual(user["username"], username)
        self.assertEqual(user["team_name"], club)
        self.assertEqual(user["division_id"], division_id)
        self.assertEqual(user["role"], "player")

    def test_assign_player_to_club_existing_user(self):
        """Verify assign_player_to_club updates existing user's team, division, and resets warns."""
        test_tg_id = 777123
        username = "existing_fifa_guy"
        
        # Pre-create user in division 1 with warns
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM users WHERE telegram_id = ?", (test_tg_id,))
            c.execute("DELETE FROM users WHERE LOWER(team_name) IN ('интер', 'милан')")
            c.execute(
                "INSERT INTO users (telegram_id, username, team_name, role, warn_count, division_id) "
                "VALUES (?, ?, 'Интер', 'player', 2, 1)",
                (test_tg_id, username)
            )

        # Re-assign to division 2 and new club
        new_club = "Милан"
        assigned_id, old_username = database.assign_player_to_club(username, new_club, division_id=2)
        self.assertEqual(assigned_id, test_tg_id)

        user = database.get_user(test_tg_id)
        self.assertEqual(user["team_name"], new_club)
        self.assertEqual(user["division_id"], 2)
        self.assertEqual(user["warn_count"], 0)

    def test_assign_player_to_club_division_isolation(self):
        """Verify old owner is unlinked ONLY if assigned to the target division."""
        club = "Ливерпуль"
        other_club = "Манчестер Юнайтед"
        div_target = 2
        div_other = 3

        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM users WHERE username IN ('div2_owner', 'div3_owner', 'new_div2_owner')")
            c.execute("DELETE FROM users WHERE LOWER(team_name) IN (LOWER(?), LOWER(?))", (club, other_club))
            # div 2 has Liverpool
            c.execute(
                "INSERT INTO users (telegram_id, username, team_name, role, division_id) "
                "VALUES (88801, 'div2_owner', ?, 'player', ?)",
                (club, div_target)
            )
            # div 3 has Man United
            c.execute(
                "INSERT INTO users (telegram_id, username, team_name, role, division_id) "
                "VALUES (88802, 'div3_owner', ?, 'player', ?)",
                (other_club, div_other)
            )

        # Reassign Liverpool in target division 2
        new_id, old_username = database.assign_player_to_club("new_div2_owner", club, division_id=div_target)
        self.assertEqual(old_username, "div2_owner")

        # div2_owner should be unlinked/deleted
        self.assertIsNone(database.get_user(88801))

        # div3_owner in division 3 MUST NOT be touched
        div3_user = database.get_user(88802)
        self.assertIsNotNone(div3_user)
        self.assertEqual(div3_user["username"], "div3_owner")
        self.assertEqual(div3_user["team_name"], other_club)
        self.assertEqual(div3_user["division_id"], div_other)

        # Verify that querying old owner of Liverpool in division 3 returns None
        with database.transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT telegram_id, username FROM users WHERE LOWER(team_name) = LOWER(?) AND division_id = ?",
                (club, div_other)
            )
            self.assertIsNone(cur.fetchone())

    async def test_fsm_step1_username_to_division_selection(self):
        """Step 1: after entering username, bot asks for division and returns ADMIN_EXPECT_PLAYER_DIVISION."""
        update = MagicMock()
        update.effective_user.id = 12345
        update.callback_query = None
        update.message.text = "@ronaldo_test"
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.user_data = {}

        with patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin.is_admin", return_value=True):
            state = await admin_add_player_username(update, context)

        self.assertEqual(state, ADMIN_EXPECT_PLAYER_DIVISION)
        self.assertEqual(context.user_data.get("admin_add_player_username"), "ronaldo_test")
        update.message.reply_text.assert_called_once()
        text_arg = update.message.reply_text.call_args[0][0]
        self.assertIn("Выберите дивизион", text_arg)

    async def test_fsm_step2_division_callback_shows_clubs_and_manual_btn(self):
        """Step 2: selecting division shows division clubs + manual club button."""
        update = MagicMock()
        update.effective_user.id = 12345
        query = MagicMock()
        query.from_user.id = 12345
        query.data = "admin_add_player_div_1"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        context = MagicMock()
        context.user_data = {"admin_add_player_username": "ronaldo_test"}

        with patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin.is_admin", return_value=True):
            state = await admin_add_player_div_callback(update, context)

        self.assertEqual(state, ADMIN_EXPECT_PLAYER_CLUB)
        self.assertEqual(context.user_data.get("admin_add_player_division_id"), 1)

        query.edit_message_text.assert_called_once()
        reply_markup = query.edit_message_text.call_args[1]["reply_markup"]
        
        # Verify manual button is present in the keyboard
        all_buttons = [btn for row in reply_markup.inline_keyboard for btn in row]
        manual_btn = next((b for b in all_buttons if b.callback_data == "admin_add_player_manual_club"), None)
        self.assertIsNotNone(manual_btn, "Button 'Ввести название вручную' must be present")
        self.assertIn("Ввести название вручную", manual_btn.text)

    async def test_fsm_step3_existing_club_callback(self):
        """Step 3 (existing club): selecting existing club completes flow."""
        update = MagicMock()
        update.effective_user.id = 12345
        query = MagicMock()
        query.from_user.id = 12345
        query.data = "assign_club_Реал Мадрид"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        context = MagicMock()
        context.user_data = {
            "admin_add_player_username": "cr7_test",
            "admin_add_player_division_id": 1,
        }

        with patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin.is_admin", return_value=True):
            state = await admin_add_player_club_callback(update, context)

        self.assertEqual(state, ConversationHandler.END)
        query.edit_message_text.assert_called_once()
        msg_text = query.edit_message_text.call_args[0][0]
        self.assertIn("Игрок успешно добавлен", msg_text)
        self.assertIn("Реал Мадрид", msg_text)

    async def test_fsm_step3_manual_club_entry(self):
        """Step 3 (manual club): pressing manual button then typing club completes flow."""
        # 1. Press manual button
        update_btn = MagicMock()
        update_btn.effective_user.id = 12345
        query = MagicMock()
        query.from_user.id = 12345
        query.data = "admin_add_player_manual_club"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update_btn.callback_query = query
        context = MagicMock()
        context.user_data = {
            "admin_add_player_username": "custom_player",
            "admin_add_player_division_id": 2,
        }

        with patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin.is_admin", return_value=True):
            state = await admin_add_player_manual_club_callback(update_btn, context)

        self.assertEqual(state, ADMIN_EXPECT_MANUAL_CLUB)
        query.edit_message_text.assert_called_once()
        self.assertIn("Введите название клуба вручную", query.edit_message_text.call_args[0][0])

        # 2. Admin sends club name text
        update_msg = MagicMock()
        update_msg.effective_user.id = 12345
        update_msg.callback_query = None
        update_msg.message.text = "ФК Торпедо Кутаиси"
        update_msg.message.reply_text = AsyncMock()

        with patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin.is_admin", return_value=True):
            state_done = await admin_add_player_manual_club_text(update_msg, context)

        self.assertEqual(state_done, ConversationHandler.END)
        update_msg.message.reply_text.assert_called_once()
        reply_text = update_msg.message.reply_text.call_args[0][0]
        self.assertIn("Игрок успешно добавлен", reply_text)
        self.assertIn("ФК Торпедо Кутаиси", reply_text)


if __name__ == "__main__":
    unittest.main()
