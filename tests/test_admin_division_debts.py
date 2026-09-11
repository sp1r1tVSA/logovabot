"""Рассылка задолженностей в скоупе дивизиона (модуль div-debts).

Глобальной рассылки больше нет. Из карточки дивизиона открывается развилка
`admin_div_debts_menu`, а её действия — ЛС должникам и сводка в топик — видят
только участников и матчи своего дивизиона.
"""
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import database
from handlers.admin import admin_div_debts_menu, admin_div_debts_dm


class TestAdminDivisionDebts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.init_db()
        uid = uuid.uuid4().hex[:6].upper()
        self.uid = uid
        self.div_a_id = database.create_division(name=f"DBT Альфа {uid}", code=f"DBTA_{uid}")
        self.div_b_id = database.create_division(name=f"DBT Бета {uid}", code=f"DBTB_{uid}")
        self.admin_id = 973001

        self.user_a1 = 97301
        self.user_a2 = 97302
        self.user_b1 = 97311
        self.team_a1 = f"DBT Alpha One {uid}"
        self.team_a2 = f"DBT Alpha Two {uid}"
        self.team_b1 = f"DBT Beta One {uid}"
        self.team_b2 = f"DBT Beta Two {uid}"

        database.register_user(self.user_a1, f"dbt_a1_{uid}", team_name=self.team_a1)
        database.register_user(self.user_a2, f"dbt_a2_{uid}", team_name=self.team_a2)
        database.register_user(self.user_b1, f"dbt_b1_{uid}", team_name=self.team_b1)
        database.assign_user_division(self.user_a1, self.div_a_id)
        database.assign_user_division(self.user_a2, self.div_a_id)
        database.assign_user_division(self.user_b1, self.div_b_id)

        with database.transaction() as conn:
            c = conn.cursor()
            # Дедлайн в прошлом → матчи тура считаются просроченными.
            for div_id in (self.div_a_id, self.div_b_id):
                c.execute(
                    "INSERT INTO rounds (round_number, is_open, deadline, division_id) VALUES (1, 1, ?, ?)",
                    ("01.01.2025 00:00", div_id),
                )
            c.execute(
                "INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, status, division_id) "
                "VALUES (1, ?, ?, ?, ?, 'pending', ?)",
                (self.user_a1, self.user_a2, self.team_a1, self.team_a2, self.div_a_id),
            )
            c.execute(
                "INSERT INTO matches (round_number, player1_id, player1_team, player2_team, status, division_id) "
                "VALUES (1, ?, ?, ?, 'pending', ?)",
                (self.user_b1, self.team_b1, self.team_b2, self.div_b_id),
            )

    async def asyncTearDown(self):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM matches WHERE division_id IN (?, ?)", (self.div_a_id, self.div_b_id))
            c.execute("DELETE FROM rounds WHERE division_id IN (?, ?)", (self.div_a_id, self.div_b_id))
            c.execute(
                "DELETE FROM users WHERE telegram_id IN (?, ?, ?)",
                (self.user_a1, self.user_a2, self.user_b1),
            )
            c.execute("DELETE FROM divisions WHERE id IN (?, ?)", (self.div_a_id, self.div_b_id))

    def _build_update(self, callback_data: str, user_id: int | None = None):
        uid = user_id if user_id is not None else self.admin_id
        update = MagicMock()
        update.effective_user = MagicMock()
        update.effective_user.id = uid
        update.message = None

        query = MagicMock()
        query.from_user.id = uid
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        return update

    @staticmethod
    def _callbacks(markup) -> list[str]:
        return [btn.callback_data for row in markup.inline_keyboard for btn in row]

    def _patches(self, is_global: bool = True):
        return (
            patch("handlers.base.is_admin", return_value=True),
            patch("handlers.admin.is_admin", return_value=True),
            patch("handlers.admin.is_global_admin", return_value=is_global),
            patch("handlers.admin.safe_edit_or_reply", new=AsyncMock()),
        )

    # --- развилка рассылки ---

    async def test_menu_offers_both_scoped_actions(self):
        update = self._build_update(f"admin_div_debts_menu:{self.div_a_id}")
        context = MagicMock()
        p_base, p_adm, p_glob, p_edit = self._patches()
        with p_base, p_adm, p_glob, p_edit as edit_mock:
            await admin_div_debts_menu(update, context)

            text = edit_mock.call_args[0][2]
            callbacks = self._callbacks(edit_mock.call_args[1]["reply_markup"])

        self.assertIn("DBT Альфа", text)
        self.assertEqual(
            callbacks,
            [
                f"admin_div_debts_dm:{self.div_a_id}",
                f"admin_div_broadcast_debts:{self.div_a_id}",
                f"admin_div_view_{self.div_a_id}",
            ],
        )

    async def test_menu_back_returns_division_admin_to_own_panel(self):
        """Карточка `admin_div_view` — экран супер-админа, дивадмина она отошьёт."""
        update = self._build_update(f"admin_div_debts_menu:{self.div_a_id}")
        context = MagicMock()
        p_base, p_adm, p_glob, p_edit = self._patches(is_global=False)
        with p_base, p_adm, p_glob, p_edit as edit_mock, \
                patch("handlers.admin.database.get_admin_divisions", return_value=[{"id": self.div_a_id}]):
            await admin_div_debts_menu(update, context)

            callbacks = self._callbacks(edit_mock.call_args[1]["reply_markup"])

        self.assertEqual(callbacks[-1], f"admin_div_panel:{self.div_a_id}")

    async def test_menu_rejects_foreign_division(self):
        update = self._build_update(f"admin_div_debts_menu:{self.div_b_id}")
        context = MagicMock()
        p_base, p_adm, p_glob, p_edit = self._patches(is_global=False)
        with p_base, p_adm, p_glob, p_edit as edit_mock, \
                patch("handlers.admin.database.get_admin_divisions", return_value=[{"id": self.div_a_id}]):
            await admin_div_debts_menu(update, context)

            self.assertFalse(edit_mock.called)

    # --- ЛС должникам ---

    async def test_dm_reaches_only_own_division_members(self):
        update = self._build_update(f"admin_div_debts_dm:{self.div_a_id}")
        context = MagicMock()
        p_base, p_adm, p_glob, p_edit = self._patches()
        sender = AsyncMock(return_value=True)
        with p_base, p_adm, p_glob, p_edit as edit_mock, \
                patch("handlers.admin.safe_send_notification", new=sender):
            await admin_div_debts_dm(update, context)

            text = edit_mock.call_args[0][2]
            callbacks = self._callbacks(edit_mock.call_args[1]["reply_markup"])

        recipients = [call.args[1] for call in sender.await_args_list]
        self.assertEqual(set(recipients), {self.user_a1, self.user_a2})
        self.assertNotIn(self.user_b1, recipients)
        # Соперник в тексте письма — из своего же дивизиона
        first_message = sender.await_args_list[0].args[2]
        self.assertIn("Тур 1", first_message)
        self.assertIn("Рассылка выполнена", text)
        self.assertEqual(callbacks[-1], f"admin_div_debts_menu:{self.div_a_id}")

    async def test_dm_counts_failures_without_stopping(self):
        """Заблокировавший бота игрок не должен обрывать рассылку остальным."""
        update = self._build_update(f"admin_div_debts_dm:{self.div_a_id}")
        context = MagicMock()
        p_base, p_adm, p_glob, p_edit = self._patches()

        async def fake_send(bot, chat_id, message, markup=None):
            return chat_id != self.user_a1

        sender = AsyncMock(side_effect=fake_send)
        with p_base, p_adm, p_glob, p_edit as edit_mock, \
                patch("handlers.admin.safe_send_notification", new=sender):
            await admin_div_debts_dm(update, context)

            text = edit_mock.call_args[0][2]

        recipients = [call.args[1] for call in sender.await_args_list]
        self.assertEqual(set(recipients), {self.user_a1, self.user_a2})
        self.assertIn("Должников: <b>2</b>", text)
        self.assertIn("Доставлено: <b>1</b>", text)
        self.assertIn("Не доставлено: <b>1</b>", text)

    async def test_dm_rejects_foreign_division(self):
        update = self._build_update(f"admin_div_debts_dm:{self.div_b_id}")
        context = MagicMock()
        p_base, p_adm, p_glob, p_edit = self._patches(is_global=False)
        sender = AsyncMock(return_value=True)
        with p_base, p_adm, p_glob, p_edit as edit_mock, \
                patch("handlers.admin.safe_send_notification", new=sender), \
                patch("handlers.admin.database.get_admin_divisions", return_value=[{"id": self.div_a_id}]):
            await admin_div_debts_dm(update, context)

            self.assertFalse(edit_mock.called)
        self.assertFalse(sender.called)


if __name__ == "__main__":
    unittest.main()
