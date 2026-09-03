import asyncio
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import database
from handlers.admin import (
    admin_divs_hub,
    admin_div_view,
    admin_div_toggle,
    admin_div_topics_menu,
    admin_div_create_start,
    admin_div_create_receive,
    admin_div_rename_start,
    admin_div_rename_receive,
    admin_div_settopic_prompt,
    admin_div_settopic_receive,
    admin_set_div_topic_cmd,
    ADMIN_EXPECT_DIV_NAME,
    ADMIN_EXPECT_DIV_RENAME,
    ADMIN_EXPECT_DIV_TOPIC_ID,
)


class TestDivisionAdminManagement(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.init_db()
        uid = uuid.uuid4().hex[:6].upper()
        self.div_code = f"ADM_{uid}"
        self.div_id = database.create_division(name="Тестовый Дивизион", code=self.div_code)
        self.admin_id = 940001

    def _build_mock_update(self, callback_data: str = None, message_text: str = None, thread_id: int = None):
        update = MagicMock()
        update.effective_user = MagicMock()
        update.effective_user.id = self.admin_id

        if callback_data:
            query = MagicMock()
            query.from_user.id = self.admin_id
            query.data = callback_data
            query.answer = AsyncMock()
            query.edit_message_text = AsyncMock()
            update.callback_query = query
        else:
            update.callback_query = None

        if message_text is not None:
            msg = MagicMock()
            msg.text = message_text
            msg.message_thread_id = thread_id
            msg.reply_text = AsyncMock()
            update.message = msg
        else:
            update.message = None

        return update

    async def test_admin_divs_hub_renders_divisions(self):
        """Test that the divisions hub lists active divisions and provides create button."""
        update = self._build_mock_update(callback_data="admin_divs_hub")
        context = MagicMock()

        with patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin.is_admin", return_value=True):
            await admin_divs_hub(update, context)

            self.assertTrue(update.callback_query.edit_message_text.called)
            args, kwargs = update.callback_query.edit_message_text.call_args
            self.assertIn("Управление дивизионами лиги", args[0])

            # Check keyboard contains create button
            markup = kwargs.get("reply_markup")
            all_callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
            self.assertIn("admin_div_create_start", all_callbacks)
            self.assertIn(f"admin_div_view_{self.div_id}", all_callbacks)

    async def test_admin_div_view_and_toggle_active(self):
        """Test viewing division card and toggling its active status."""
        update = self._build_mock_update(callback_data=f"admin_div_view_{self.div_id}")
        context = MagicMock()

        with patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin.is_admin", return_value=True):
            # 1. View division
            await admin_div_view(update, context, div_id=self.div_id)
            args, kwargs = update.callback_query.edit_message_text.call_args
            self.assertIn("Тестовый Дивизион", args[0])
            self.assertIn("🟢 Активен", args[0])

            # 2. Toggle active (to 0)
            update.callback_query.data = f"admin_div_toggle_{self.div_id}"
            await admin_div_toggle(update, context)
            div = database.get_division(self.div_id)
            self.assertEqual(div["is_active"], 0)

            # 3. Toggle back to active (to 1)
            await admin_div_toggle(update, context)
            div = database.get_division(self.div_id)
            self.assertEqual(div["is_active"], 1)

    async def test_admin_div_rename_flow(self):
        """Test renaming division via interactive conversation."""
        context = MagicMock()
        context.user_data = {}

        with patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin.is_admin", return_value=True):
            # Start rename
            update_btn = self._build_mock_update(callback_data=f"admin_div_rename_{self.div_id}")
            state = await admin_div_rename_start(update_btn, context)
            self.assertEqual(state, ADMIN_EXPECT_DIV_RENAME)
            self.assertEqual(context.user_data.get("rename_div_id"), self.div_id)

            # Send new name
            update_msg = self._build_mock_update(message_text="Супер Лига")
            next_state = await admin_div_rename_receive(update_msg, context)
            self.assertEqual(next_state, -1)  # ConversationHandler.END is -1

            div = database.get_division(self.div_id)
            self.assertEqual(div["name"], "Супер Лига")

    async def test_admin_div_settopic_and_command(self):
        """Test setting division forum topics via callback flow and direct command."""
        context = MagicMock()
        context.user_data = {}

        with patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin.is_admin", return_value=True):
            # 1. Start topic binding for drafts
            update_btn = self._build_mock_update(callback_data=f"admin_div_settopic_{self.div_id}_drafts")
            state = await admin_div_settopic_prompt(update_btn, context)
            self.assertEqual(state, ADMIN_EXPECT_DIV_TOPIC_ID)

            # 2. Provide numeric thread_id 8888
            update_msg = self._build_mock_update(message_text="8888")
            await admin_div_settopic_receive(update_msg, context)
            tid = database.get_division_topic(self.div_id, "drafts")
            self.assertEqual(tid, 8888)

            # 3. Clear topic by sending 0
            context.user_data = {"div_topic_div_id": self.div_id, "div_topic_type": "drafts"}
            update_clear = self._build_mock_update(message_text="0")
            await admin_div_settopic_receive(update_clear, context)
            tid = database.get_division_topic(self.div_id, "drafts")
            self.assertIsNone(tid)

            # 4. Use command /set_div_topic inside a topic
            cmd_update = self._build_mock_update(message_text="/set_div_topic", thread_id=9999)
            cmd_context = MagicMock()
            cmd_context.args = [str(self.div_id), "results"]
            await admin_set_div_topic_cmd(cmd_update, cmd_context)

            res_tid = database.get_division_topic(self.div_id, "results")
            self.assertEqual(res_tid, 9999)

    async def test_admin_div_create_flow(self):
        """Test creating a new division via interactive conversation."""
        context = MagicMock()
        context.user_data = {}

        with patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin.is_admin", return_value=True):
            update_btn = self._build_mock_update(callback_data="admin_div_create_start")
            state = await admin_div_create_start(update_btn, context)
            self.assertEqual(state, ADMIN_EXPECT_DIV_NAME)

            unique_title = f"Золотая Лига {uuid.uuid4().hex[:4]}"
            update_msg = self._build_mock_update(message_text=unique_title)
            next_state = await admin_div_create_receive(update_msg, context)
            self.assertEqual(next_state, -1)

            # Check division created in DB
            divisions = database.get_divisions()
            created = [d for d in divisions if d["name"] == unique_title]
            self.assertEqual(len(created), 1)
            self.assertTrue(len(created[0]["code"]) >= 3)


if __name__ == "__main__":
    unittest.main()
