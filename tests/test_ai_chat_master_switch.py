"""Мастер-выключатель ИИ «Темшик» в супер-админ-панели.

Флаг `ai_chat_enabled` в `system_config` глушит генеративный диалог целиком:
Gemini не вызывается, в чат ничего не уходит. Турнирные текстовые команды
(«Темшик таблица») тумблером не затрагиваются — они обрабатываются выше.

Флаг глобальный и живёт в общей league.db, а файлы тестов идут параллельно
(`--dist loadfile`), поэтому выключенное состояние здесь моделируется подменой
конфига, а не реальной записью "0": иначе соседний файл, дергающий handle_ai_chat,
падал бы от чужого состояния. Реальный round-trip проверяется на значении "1",
которое совпадает с дефолтом и никому не мешает.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import database
from handlers.admin import admin_toggle_ai_chat, show_super_admin_panel
from handlers.chat import handle_ai_chat


class FakeConfigStore:
    """Подмена system_config в памяти: get_config/set_config поверх dict."""

    def __init__(self, initial: dict[str, str] | None = None):
        self.values = dict(initial or {})

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def patches(self):
        return (
            patch("database.get_config", side_effect=self.get),
            patch("database.set_config", side_effect=self.set),
        )


def _build_callback_update(user_id: int):
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.message = None

    query = MagicMock()
    query.from_user.id = user_id
    query.data = "admin_toggle_ai_chat"
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    update.callback_query = query
    return update


class TestAiChatFlagStorage(unittest.IsolatedAsyncioTestCase):
    """Чтение/запись флага на уровне database."""

    async def test_default_state_is_enabled(self):
        """Записи нет — ИИ считается включённым."""
        store = FakeConfigStore()
        p_get, p_set = store.patches()
        with p_get, p_set:
            self.assertTrue(database.is_ai_chat_enabled())

    async def test_zero_disables_and_one_enables(self):
        store = FakeConfigStore({"ai_chat_enabled": "0"})
        p_get, p_set = store.patches()
        with p_get, p_set:
            self.assertFalse(database.is_ai_chat_enabled())
            database.set_ai_chat_enabled(True)
            self.assertEqual(store.values["ai_chat_enabled"], "1")
            self.assertTrue(database.is_ai_chat_enabled())
            database.set_ai_chat_enabled(False)
            self.assertEqual(store.values["ai_chat_enabled"], "0")

    async def test_real_db_roundtrip_on_enabled_value(self):
        """Значение реально доезжает до system_config (пишем дефолтное «включено»)."""
        database.init_db()
        database.set_ai_chat_enabled(True)
        self.assertEqual(database.get_config("ai_chat_enabled"), "1")
        self.assertTrue(database.is_ai_chat_enabled())


class TestSuperAdminPanelButton(unittest.IsolatedAsyncioTestCase):
    """Кнопка состояния в супер-админ-панели."""

    @staticmethod
    def _buttons(markup):
        return [btn for row in markup.inline_keyboard for btn in row]

    async def _render_panel(self, ai_enabled: bool):
        update = _build_callback_update(970101)
        ctx = MagicMock()
        sender = AsyncMock()
        store = FakeConfigStore({} if ai_enabled else {"ai_chat_enabled": "0"})
        p_get, p_set = store.patches()

        with p_get, p_set, \
             patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin.is_admin", return_value=True), \
             patch("handlers.admin.is_global_admin", return_value=True), \
             patch("handlers.admin.safe_edit_or_reply", new=sender):
            await show_super_admin_panel(update, ctx)

        self.assertTrue(sender.called, "Панель не была отрисована")
        return sender.call_args.kwargs["reply_markup"]

    async def test_button_shows_on_state(self):
        markup = await self._render_panel(ai_enabled=True)
        labels = {b.callback_data: b.text for b in self._buttons(markup)}
        self.assertEqual(labels.get("admin_toggle_ai_chat"), "🤖 ИИ Темшик: 🟢 ВКЛ")

    async def test_button_shows_off_state(self):
        markup = await self._render_panel(ai_enabled=False)
        labels = {b.callback_data: b.text for b in self._buttons(markup)}
        self.assertEqual(labels.get("admin_toggle_ai_chat"), "🤖 ИИ Темшик: 🔴 ВЫКЛ")

    async def test_chat_mode_button_survives(self):
        """Существующий переключатель стиля общения не потерян."""
        markup = await self._render_panel(ai_enabled=True)
        callbacks = [b.callback_data for b in self._buttons(markup)]
        self.assertIn("admin_toggle_chat_mode", callbacks)


class TestAdminToggleAiChat(unittest.IsolatedAsyncioTestCase):
    """Хендлер admin_toggle_ai_chat: переключение, уведомление, права."""

    async def _toggle(self, store: FakeConfigStore, *, is_admin=True, is_global=True, user_id=970102):
        update = _build_callback_update(user_id)
        ctx = MagicMock()
        p_get, p_set = store.patches()

        with p_get, p_set, \
             patch("handlers.base.is_admin", return_value=is_admin), \
             patch("handlers.admin.is_admin", return_value=is_admin), \
             patch("handlers.admin.is_global_admin", return_value=is_global):
            await admin_toggle_ai_chat(update, ctx)

        return update.callback_query

    async def test_toggle_off_persists_zero(self):
        store = FakeConfigStore()
        query = await self._toggle(store)
        self.assertEqual(store.values["ai_chat_enabled"], "0")
        query.answer.assert_any_call("ИИ Темшик выключен 🔴")

    async def test_toggle_on_persists_one(self):
        store = FakeConfigStore({"ai_chat_enabled": "0"})
        query = await self._toggle(store)
        self.assertEqual(store.values["ai_chat_enabled"], "1")
        query.answer.assert_any_call("ИИ Темшик включён 🟢")

    async def test_toast_is_the_only_callback_answer(self):
        """
        Хендлер намеренно без @admin_only: Telegram принимает ответ на callback
        один раз, поэтому пустого предответа быть не должно — иначе тост не долетит.
        """
        store = FakeConfigStore()
        query = await self._toggle(store)
        self.assertEqual(query.answer.await_count, 1)
        self.assertEqual(query.answer.await_args.args, ("ИИ Темшик выключен 🔴",))

    async def test_keyboard_refreshed_in_place(self):
        """Клавиатура правится на месте — без нового сообщения в чат админа."""
        store = FakeConfigStore()
        query = await self._toggle(store)
        query.edit_message_reply_markup.assert_awaited_once()
        markup = query.edit_message_reply_markup.call_args.kwargs["reply_markup"]
        labels = {b.callback_data: b.text for row in markup.inline_keyboard for b in row}
        self.assertEqual(labels["admin_toggle_ai_chat"], "🤖 ИИ Темшик: 🔴 ВЫКЛ")
        self.assertFalse(query.message.reply_text.called)

    async def test_non_admin_denied(self):
        """Обычный пользователь получает отказ и ничего не пишет в БД."""
        store = FakeConfigStore()
        query = await self._toggle(store, is_admin=False, is_global=False, user_id=970199)
        self.assertEqual(store.values, {})
        query.answer.assert_awaited_once_with("⛔ Раздел доступен только супер-админу", show_alert=True)
        self.assertFalse(query.edit_message_reply_markup.called)

    async def test_division_admin_denied(self):
        """Админ дивизиона — не супер-админ: тумблер ему недоступен."""
        store = FakeConfigStore()
        query = await self._toggle(store, is_admin=True, is_global=False, user_id=970198)
        self.assertEqual(store.values, {})
        query.answer.assert_awaited_once_with("⛔ Раздел доступен только супер-админу", show_alert=True)
        self.assertFalse(query.edit_message_reply_markup.called)


class TestHandleAiChatGate(unittest.IsolatedAsyncioTestCase):
    """Перехват в handlers/chat.py."""

    @staticmethod
    def _build_update():
        update = MagicMock()
        update.message.text = "Темшик а кто фаворит"
        update.message.voice = None
        update.message.reply_to_message = None
        update.message.message_thread_id = None
        update.message.reply_text = AsyncMock()
        update.effective_message = update.message
        update.effective_user.id = 970103
        update.effective_user.username = "switch_tester"
        update.effective_chat.id = 970103
        update.effective_chat.type = "private"
        return update

    async def _run(self, enabled: bool, temshik_command_handled: bool = False):
        update = self._build_update()
        ctx = MagicMock()
        ctx.bot.id = 999
        ctx.bot.send_chat_action = AsyncMock()
        cmd = AsyncMock(return_value=temshik_command_handled)

        with patch("database.is_ai_chat_enabled", return_value=enabled), \
             patch("handlers.chat.handle_temshik_command", new=cmd), \
             patch("handlers.chat.ai_chat.generate_chat_reply", return_value="ok") as gen:
            await handle_ai_chat(update, ctx)

        return update, cmd, gen

    async def test_disabled_skips_gemini_silently(self):
        update, _, gen = await self._run(enabled=False)
        self.assertFalse(gen.called, "Gemini дернули при выключенном ИИ")
        self.assertFalse(update.message.reply_text.called, "Бот ответил при выключенном ИИ")
        self.assertFalse(update.effective_chat.send_message.called)

    async def test_disabled_keeps_text_commands_working(self):
        """Турнирные команды обрабатываются до тумблера и остаются живыми."""
        _, cmd, gen = await self._run(enabled=False, temshik_command_handled=True)
        cmd.assert_awaited_once()
        self.assertFalse(gen.called)

    async def test_enabled_reaches_generator(self):
        database.init_db()
        _, _, gen = await self._run(enabled=True)
        self.assertTrue(gen.called, "При включённом ИИ генератор не был вызван")


if __name__ == "__main__":
    unittest.main()
