"""«Темшик таблица» и generate_league_table_image обязаны быть скоуплены по дивизиону.

До правки текстовая команда звала generate_league_table_image() вообще без аргументов,
а генератор в этом случае тянул database.get_standings() без division_id — то есть
legacy-ветку, которая перебирает 16 имён КПЛ. Любой тренер любого дивизиона получал
одну и ту же таблицу эпохи единой лиги вместо своей.
"""
import io
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import database
from handlers.text_commands import handle_temshik_command
from services.graphics.table_generator import generate_league_table_image


class TestTemshikTableIsDivisionScoped(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.init_db()
        uid = uuid.uuid4().hex[:6].upper()
        self.uid = uid

        self.div_a_id = database.create_division(name=f"TBL Альфа {uid}", code=f"TBLA_{uid}")
        self.div_b_id = database.create_division(name=f"TBL Бета {uid}", code=f"TBLB_{uid}")

        self.user_a, self.user_b, self.orphan = 97601, 97611, 97699
        self.team_a = f"TBL Alpha {uid}"
        self.team_b = f"TBL Beta {uid}"

        for tg_id, nick, team, div in (
            (self.user_a, f"tbl_a_{uid}", self.team_a, self.div_a_id),
            (self.user_b, f"tbl_b_{uid}", self.team_b, self.div_b_id),
        ):
            database.register_user(tg_id, nick, team_name=team)
            database.assign_user_division(tg_id, div)

        database.register_user(self.orphan, f"tbl_orphan_{uid}", team_name=f"TBL Orphan {uid}")
        database.assign_user_division(self.orphan, None)

    async def asyncTearDown(self):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute(
                "DELETE FROM users WHERE telegram_id IN (?, ?, ?)",
                (self.user_a, self.user_b, self.orphan),
            )
            c.execute("DELETE FROM divisions WHERE id IN (?, ?)", (self.div_a_id, self.div_b_id))

    def _build_update(self, user_id: int):
        """Личка с ботом: дивизион резолвится из users.division_id."""
        update = MagicMock()
        update.message.text = "Темшик таблица"
        update.message.message_thread_id = None
        update.message.reply_text = AsyncMock()
        update.message.reply_photo = AsyncMock()
        update.effective_message = update.message
        update.effective_user.id = user_id
        update.effective_user.username = "tester"
        update.effective_chat.id = user_id
        update.effective_chat.type = "private"
        return update

    async def _run(self, user_id: int):
        """Прогнать команду с замоканным генератором и вернуть (update, mock генератора)."""
        update = self._build_update(user_id)
        gen = MagicMock(return_value=io.BytesIO(b"png"))
        with patch("handlers.text_commands.generate_league_table_image", gen):
            handled = await handle_temshik_command(update, MagicMock())
        self.assertTrue(handled, "команда «таблица» не была распознана")
        return update, gen

    async def test_table_is_rendered_for_the_speakers_division(self):
        """Тренеру дивизиона А рисуем таблицу дивизиона А, а не кросс-дивизионную."""
        update, gen = await self._run(self.user_a)

        self.assertTrue(gen.called, "генератор таблицы не был вызван")
        args, kwargs = gen.call_args
        passed = kwargs.get("division_id", args[3] if len(args) > 3 else None)
        self.assertEqual(passed, self.div_a_id)

    async def test_other_division_gets_its_own_table(self):
        """Зеркально: тренер дивизиона Б получает свой division_id."""
        _, gen = await self._run(self.user_b)

        args, kwargs = gen.call_args
        passed = kwargs.get("division_id", args[3] if len(args) > 3 else None)
        self.assertEqual(passed, self.div_b_id)

    async def test_caption_and_refresh_button_name_the_division(self):
        """Подпись и кнопка «Обновить» должны указывать на тот же дивизион."""
        update, _ = await self._run(self.user_a)

        self.assertTrue(update.message.reply_photo.called)
        caption = update.message.reply_photo.call_args.kwargs["caption"]
        self.assertIn(f"TBL Альфа {self.uid}", caption)

        markup = update.message.reply_photo.call_args.kwargs["reply_markup"]
        self.assertEqual(
            markup.inline_keyboard[0][0].callback_data,
            f"refresh_div_table_{self.div_a_id}",
        )

    async def test_user_without_division_gets_a_hint_not_a_cross_division_table(self):
        """Без дивизиона лучше честно переспросить, чем показать таблицу КПЛ."""
        update, gen = await self._run(self.orphan)

        self.assertFalse(gen.called, "без дивизиона таблицу рисовать нельзя")
        self.assertTrue(update.message.reply_text.called)
        text = update.message.reply_text.call_args[0][0]
        self.assertIn("дивизион", text.lower())


class TestGeneratorPullsDataForTheSameDivision(unittest.TestCase):
    """Генератор не должен смешивать цвета одного дивизиона с данными всех сразу."""

    def test_division_id_is_forwarded_into_both_db_calls(self):
        with patch("services.graphics.table_generator.database.get_standings", return_value=[]) as st, \
             patch("services.graphics.table_generator.database.get_teams_recent_form", return_value={}) as form:
            generate_league_table_image(None, None, "TBL Альфа", 4242)

        self.assertEqual(st.call_args.kwargs.get("division_id"), 4242)
        self.assertEqual(form.call_args.kwargs.get("division_id"), 4242)

    def test_explicitly_passed_data_is_not_refetched(self):
        """Если данные передали — генератор в базу не ходит вовсе."""
        rows = [{
            "team_name": "TBL Alpha", "played": 1, "wins": 1, "draws": 0, "losses": 0,
            "goals_for": 3, "goals_against": 1, "goal_diff": 2, "points": 3,
        }]
        with patch("services.graphics.table_generator.database.get_standings") as st, \
             patch("services.graphics.table_generator.database.get_teams_recent_form") as form:
            generate_league_table_image(rows, {}, "TBL Альфа", 4242)

        st.assert_not_called()
        form.assert_not_called()


if __name__ == "__main__":
    unittest.main()
