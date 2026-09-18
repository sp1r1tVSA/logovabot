"""Дивизион-центричная навигация супер-админки (модуль nav-shell).

Главная панель больше не содержит глобальных разделов матчей, составов и
рассылки долгов — единственная точка входа в них это карточка дивизиона.
"""
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import database
from handlers.admin import (
    admin_div_manage_players,
    admin_div_view,
    admin_divs_hub,
    admin_view_player,
    show_super_admin_panel,
)

# Разделы, вход в которые теперь только через карточку дивизиона.
REMOVED_GLOBAL_SECTIONS = (
    "admin_manage_squads",
    "admin_manage_matches_info",
    "admin_broadcast_menu",
)


class TestAdminDivisionNavigation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.init_db()
        uid = uuid.uuid4().hex[:6].upper()
        self.div_id = database.create_division(name=f"NAV Альфа {uid}", code=f"NAVA_{uid}")
        self.super_id = 971001

    def _build_update(self, user_id: int, callback_data: str | None = None):
        update = MagicMock()
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        update.message = None

        query = MagicMock()
        query.from_user.id = user_id
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        return update

    @staticmethod
    def _callbacks(markup) -> list[str]:
        return [btn.callback_data for row in markup.inline_keyboard for btn in row]

    def _patches(self):
        return (
            patch("handlers.base.is_admin", return_value=True),
            patch("handlers.admin.is_admin", return_value=True),
            patch("handlers.admin.is_global_admin", return_value=True),
            patch("handlers.admin.safe_edit_or_reply", new=AsyncMock()),
        )

    # --- главная панель ---

    async def test_super_panel_has_eight_rows(self):
        # Разделы (включая привязку клубов) + режим общения + мастер-выключатель ИИ + «Назад».
        update = self._build_update(self.super_id, "admin_main_menu")
        context = MagicMock()
        p_base, p_adm, p_glob, p_edit = self._patches()
        with p_base, p_adm, p_glob, p_edit as edit_mock:
            await show_super_admin_panel(update, context)

            markup = edit_mock.call_args[1]["reply_markup"]
            self.assertEqual(len(markup.inline_keyboard), 8)
            self.assertIn("admin_bind_hub", self._callbacks(markup))

    async def test_super_panel_drops_global_sections(self):
        update = self._build_update(self.super_id, "admin_main_menu")
        context = MagicMock()
        p_base, p_adm, p_glob, p_edit = self._patches()
        with p_base, p_adm, p_glob, p_edit as edit_mock:
            await show_super_admin_panel(update, context)

            callbacks = self._callbacks(edit_mock.call_args[1]["reply_markup"])
            for removed in REMOVED_GLOBAL_SECTIONS:
                self.assertNotIn(removed, callbacks)
            self.assertIn("admin_divs_hub", callbacks)

    async def test_super_panel_renames_divisions_button(self):
        update = self._build_update(self.super_id, "admin_main_menu")
        context = MagicMock()
        p_base, p_adm, p_glob, p_edit = self._patches()
        with p_base, p_adm, p_glob, p_edit as edit_mock:
            await show_super_admin_panel(update, context)

            markup = edit_mock.call_args[1]["reply_markup"]
            labels = {btn.callback_data: btn.text for row in markup.inline_keyboard for btn in row}
            self.assertEqual(labels["admin_divs_hub"], "🏆 Дивизионы")

    # --- хаб дивизионов ---

    async def test_hub_lists_divisions_with_create_and_back(self):
        update = self._build_update(self.super_id, "admin_divs_hub")
        context = MagicMock()
        p_base, p_adm, p_glob, p_edit = self._patches()
        with p_base, p_adm, p_glob, p_edit:
            await admin_divs_hub(update, context)

            markup = update.callback_query.edit_message_text.call_args[1]["reply_markup"]
            callbacks = self._callbacks(markup)
            self.assertIn(f"admin_div_view_{self.div_id}", callbacks)
            self.assertIn("admin_div_create_start", callbacks)
            self.assertIn("admin_main_menu", callbacks)

    async def test_hub_shows_status_and_player_count(self):
        update = self._build_update(self.super_id, "admin_divs_hub")
        context = MagicMock()
        p_base, p_adm, p_glob, p_edit = self._patches()
        with p_base, p_adm, p_glob, p_edit:
            await admin_divs_hub(update, context)

            markup = update.callback_query.edit_message_text.call_args[1]["reply_markup"]
            label = next(
                btn.text
                for row in markup.inline_keyboard
                for btn in row
                if btn.callback_data == f"admin_div_view_{self.div_id}"
            )
            self.assertTrue(label.startswith(("🟢", "🔴")), label)
            self.assertIn("игр.)", label)

    # --- карточка дивизиона ---

    async def test_card_exposes_functional_sections(self):
        update = self._build_update(self.super_id, f"admin_div_view_{self.div_id}")
        context = MagicMock()
        p_base, p_adm, p_glob, p_edit = self._patches()
        with p_base, p_adm, p_glob, p_edit:
            await admin_div_view(update, context)

            markup = update.callback_query.edit_message_text.call_args[1]["reply_markup"]
            callbacks = self._callbacks(markup)
            self.assertIn(f"admin_div_manage_matches:{self.div_id}", callbacks)
            self.assertIn(f"admin_roster_div:{self.div_id}", callbacks)

    async def test_card_puts_functional_block_above_technical(self):
        update = self._build_update(self.super_id, f"admin_div_view_{self.div_id}")
        context = MagicMock()
        p_base, p_adm, p_glob, p_edit = self._patches()
        with p_base, p_adm, p_glob, p_edit:
            await admin_div_view(update, context)

            markup = update.callback_query.edit_message_text.call_args[1]["reply_markup"]
            callbacks = self._callbacks(markup)
            self.assertLess(
                callbacks.index(f"admin_roster_div:{self.div_id}"),
                callbacks.index(f"admin_div_topics_{self.div_id}"),
            )
            self.assertEqual(callbacks[-1], "admin_divs_hub")

    async def test_card_denied_for_non_global_admin(self):
        update = self._build_update(971002, f"admin_div_view_{self.div_id}")
        context = MagicMock()
        with patch("handlers.base.is_admin", return_value=True), \
                patch("handlers.admin.is_admin", return_value=True), \
                patch("handlers.admin.is_global_admin", return_value=False):
            await admin_div_view(update, context)

            self.assertFalse(update.callback_query.edit_message_text.called)

    async def test_card_participants_button_points_to_division_players(self):
        """Кнопка «👥 Участники» в карточке дивизиона ведёт на admin_div_players:{div_id}:0."""
        update = self._build_update(self.super_id, f"admin_div_view_{self.div_id}")
        context = MagicMock()
        p_base, p_adm, p_glob, p_edit = self._patches()
        with p_base, p_adm, p_glob, p_edit:
            await admin_div_view(update, context)

            markup = update.callback_query.edit_message_text.call_args[1]["reply_markup"]
            callbacks = self._callbacks(markup)
            self.assertIn(f"admin_div_players:{self.div_id}:0", callbacks)

    async def test_admin_div_manage_players_back_button_for_super_admin(self):
        """Из списка участников дивизиона супер-админ возвращается в карточку дивизиона."""
        update = self._build_update(self.super_id, f"admin_div_players:{self.div_id}:0")
        context = MagicMock()
        context.user_data = {}
        p_base, p_adm, p_glob, p_edit = self._patches()
        with p_base, p_adm, p_glob, p_edit:
            await admin_div_manage_players(update, context)

            self.assertEqual(context.user_data.get("admin_player_back_cb"), f"admin_div_players:{self.div_id}:0")
            markup = update.callback_query.edit_message_text.call_args[1]["reply_markup"]
            callbacks = self._callbacks(markup)
            self.assertIn(f"admin_div_view_{self.div_id}", callbacks)

    async def test_admin_view_player_back_button_from_division_list(self):
        """В карточке игрока кнопка «Назад» возвращает в список участников дивизиона."""
        test_player_id = 981123
        with database.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO users (telegram_id, username, team_name, role, division_id) VALUES (?, ?, ?, ?, ?)",
                (test_player_id, "nav_tester", "Nav Club", "player", self.div_id)
            )

        try:
            update = self._build_update(self.super_id, f"admin_view_player_{test_player_id}")
            context = MagicMock()
            context.user_data = {"admin_player_back_cb": f"admin_div_players:{self.div_id}:0"}
            p_base, p_adm, p_glob, p_edit = self._patches()
            with p_base, p_adm, p_glob, p_edit:
                await admin_view_player(update, context, player_id=test_player_id)

                markup = update.callback_query.edit_message_text.call_args[1]["reply_markup"]
                callbacks = self._callbacks(markup)
                self.assertIn(f"admin_div_players:{self.div_id}:0", callbacks)
                labels = [btn.text for row in markup.inline_keyboard for btn in row if btn.callback_data == f"admin_div_players:{self.div_id}:0"]
                self.assertEqual(labels[0], "« К участникам дивизиона")
        finally:
            with database.transaction() as conn:
                conn.execute("DELETE FROM users WHERE telegram_id = ?", (test_player_id,))


if __name__ == "__main__":
    unittest.main()
