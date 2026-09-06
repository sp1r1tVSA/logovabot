import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import database
from handlers.topic_management import cmd_bind_group, cb_bind_group


class TestBindGroup(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.init_db()
        self.admin_id = 955001
        self.non_admin_id = 955002
        self.group_chat_id = -100987654321

        uid = uuid.uuid4().hex[:6].upper()
        self.div_code = f"GRP_{uid}"
        self.div_id = database.create_division(name="Групповой Дивизион", code=self.div_code)

    async def asyncTearDown(self):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM divisions WHERE id = ?", (self.div_id,))

    def test_database_set_and_get_division_group(self):
        """Test database functions set_division_group and get_division_by_group."""
        # Initially not bound
        self.assertIsNone(database.get_division_by_group(self.group_chat_id))

        # Bind group
        database.set_division_group(self.div_id, self.group_chat_id)

        # Retrieve by group
        div = database.get_division_by_group(self.group_chat_id)
        self.assertIsNotNone(div)
        self.assertEqual(div["id"], self.div_id)
        self.assertEqual(div["group_chat_id"], self.group_chat_id)

        # Retrieve direct division record
        div_direct = database.get_division(self.div_id)
        self.assertIsNotNone(div_direct)
        self.assertEqual(div_direct["group_chat_id"], self.group_chat_id)

        # Non-existent chat returns None
        self.assertIsNone(database.get_division_by_group(-99999999))

    async def test_cmd_bind_group_permission_and_chat_type(self):
        """Test cmd_bind_group enforces admin-only and group-only checks."""
        # Non-admin check
        update_non_admin = MagicMock()
        update_non_admin.effective_user.id = self.non_admin_id
        update_non_admin.effective_chat.type = "supergroup"
        update_non_admin.effective_message.reply_text = AsyncMock()

        with patch("handlers.topic_management.is_admin", return_value=False):
            await cmd_bind_group(update_non_admin, MagicMock())

        update_non_admin.effective_message.reply_text.assert_called_once()
        self.assertIn("только администраторам", update_non_admin.effective_message.reply_text.call_args[0][0])

        # Private chat check
        update_private = MagicMock()
        update_private.effective_user.id = self.admin_id
        update_private.effective_chat.type = "private"
        update_private.effective_message.reply_text = AsyncMock()

        with patch("handlers.topic_management.is_admin", return_value=True):
            await cmd_bind_group(update_private, MagicMock())

        update_private.effective_message.reply_text.assert_called_once()
        self.assertIn("внутри группы", update_private.effective_message.reply_text.call_args[0][0])

    async def test_cmd_bind_group_renders_divisions_keyboard(self):
        """Test cmd_bind_group in a supergroup shows active divisions inline keyboard."""
        update = MagicMock()
        update.effective_user.id = self.admin_id
        update.effective_chat.type = "supergroup"
        update.effective_chat.id = self.group_chat_id
        update.effective_chat.title = "Тестовая Группа"
        update.effective_message.reply_text = AsyncMock()

        with patch("handlers.topic_management.is_admin", return_value=True):
            await cmd_bind_group(update, MagicMock())

        update.effective_message.reply_text.assert_called_once()
        args, kwargs = update.effective_message.reply_text.call_args
        text = args[0]
        reply_markup = kwargs.get("reply_markup")

        self.assertIn("К какому дивизиону привязать эту группу?", text)
        buttons_cb = [b.callback_data for row in reply_markup.inline_keyboard for b in row]
        self.assertIn(f"bind_group:{self.div_id}", buttons_cb)
        self.assertIn("top_cancel", buttons_cb)

    async def test_cb_bind_group_success(self):
        """Test cb_bind_group binds chat to division and confirms."""
        update = MagicMock()
        update.effective_user.id = self.admin_id
        update.effective_chat.id = self.group_chat_id
        update.effective_chat.title = "Тестовая Группа"

        query = MagicMock()
        query.data = f"bind_group:{self.div_id}"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query

        with patch("handlers.topic_management.is_admin", return_value=True):
            await cb_bind_group(update, MagicMock())

        query.answer.assert_called_once()
        query.edit_message_text.assert_called_once()
        text = query.edit_message_text.call_args[0][0]

        self.assertIn("успешно привязана к дивизиону", text)
        self.assertIn("Групповой Дивизион", text)

        # Verify DB state
        bound_div = database.get_division_by_group(self.group_chat_id)
        self.assertIsNotNone(bound_div)
        self.assertEqual(bound_div["id"], self.div_id)


if __name__ == "__main__":
    unittest.main()
