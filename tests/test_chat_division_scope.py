"""Изоляция контекста Темшика по дивизиону.

Промты переписаны под дивизионную структуру, поэтому контекст обязан быть скоуплен:
в него попадают таблица, форма, расписание, бомбардиры и составы ТОЛЬКО того дивизиона,
в котором идёт разговор. Данные соседнего дивизиона не должны утекать ни при каких условиях.
"""
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import database
from handlers.chat import handle_ai_chat


class TestChatDivisionScope(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.init_db()
        uid = uuid.uuid4().hex[:6].upper()
        self.uid = uid

        self.div_a_id = database.create_division(name=f"CHT Альфа {uid}", code=f"CHTA_{uid}")
        self.div_b_id = database.create_division(name=f"CHT Бета {uid}", code=f"CHTB_{uid}")

        self.user_a1, self.user_a2, self.user_b1, self.user_b2 = 97401, 97402, 97411, 97412
        self.team_a1 = f"CHT Alpha One {uid}"
        self.team_a2 = f"CHT Alpha Two {uid}"
        self.team_b1 = f"CHT Beta One {uid}"
        self.team_b2 = f"CHT Beta Two {uid}"

        for tg_id, nick, team, div in (
            (self.user_a1, f"cht_a1_{uid}", self.team_a1, self.div_a_id),
            (self.user_a2, f"cht_a2_{uid}", self.team_a2, self.div_a_id),
            (self.user_b1, f"cht_b1_{uid}", self.team_b1, self.div_b_id),
            (self.user_b2, f"cht_b2_{uid}", self.team_b2, self.div_b_id),
        ):
            database.register_user(tg_id, nick, team_name=team)
            database.assign_user_division(tg_id, div)

        season = database.get_active_season()
        self.season_id = season["id"] if season else 1

        with database.transaction() as conn:
            c = conn.cursor()
            for div_id in (self.div_a_id, self.div_b_id):
                c.execute(
                    "INSERT INTO rounds (round_number, is_open, deadline, division_id) VALUES (1, 1, ?, ?)",
                    ("01.01.2030 00:00", div_id),
                )
            # Сыгранный матч в каждом дивизионе.
            c.execute(
                "INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, "
                "player1_score, player2_score, status, division_id, season_id, tournament_type) "
                "VALUES (1, ?, ?, ?, ?, 3, 1, 'confirmed', ?, ?, 'league')",
                (self.user_a1, self.user_a2, self.team_a1, self.team_a2, self.div_a_id, self.season_id),
            )
            c.execute(
                "INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, "
                "player1_score, player2_score, status, division_id, season_id, tournament_type) "
                "VALUES (1, ?, ?, ?, ?, 5, 0, 'confirmed', ?, ?, 'league')",
                (self.user_b1, self.user_b2, self.team_b1, self.team_b2, self.div_b_id, self.season_id),
            )

    async def asyncTearDown(self):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM matches WHERE division_id IN (?, ?)", (self.div_a_id, self.div_b_id))
            c.execute("DELETE FROM rounds WHERE division_id IN (?, ?)", (self.div_a_id, self.div_b_id))
            c.execute(
                "DELETE FROM users WHERE telegram_id IN (?, ?, ?, ?)",
                (self.user_a1, self.user_a2, self.user_b1, self.user_b2),
            )
            c.execute("DELETE FROM divisions WHERE id IN (?, ?)", (self.div_a_id, self.div_b_id))

    def _build_update(self, user_id: int):
        """Личка с ботом: дивизион резолвится из users.division_id."""
        update = MagicMock()
        update.message.text = "Темшик какие у меня шансы"
        update.message.voice = None
        update.message.reply_to_message = None
        update.message.message_thread_id = None
        update.message.reply_text = AsyncMock()
        update.effective_message = update.message
        update.effective_user.id = user_id
        update.effective_user.username = "tester"
        update.effective_chat.id = user_id
        update.effective_chat.type = "private"
        return update

    async def _capture_context(self, user_id: int) -> str:
        """Прогнать хендлер и вернуть context_data, ушедший в модель."""
        update = self._build_update(user_id)
        ctx = MagicMock()
        ctx.bot.id = 999
        ctx.bot.send_chat_action = AsyncMock()

        with patch("handlers.chat.handle_temshik_command", new=AsyncMock(return_value=False)), \
             patch("handlers.chat.ai_chat.generate_chat_reply", return_value="ok") as gen:
            await handle_ai_chat(update, ctx)

        self.assertTrue(gen.called, "generate_chat_reply не был вызван")
        return gen.call_args[0][3]

    async def test_context_contains_only_own_division(self):
        """Тренер дивизиона А не видит клубов дивизиона Б."""
        ctx_a = await self._capture_context(self.user_a1)

        self.assertIn(self.team_a1, ctx_a)
        self.assertIn(self.team_a2, ctx_a)
        self.assertNotIn(self.team_b1, ctx_a)
        self.assertNotIn(self.team_b2, ctx_a)

    async def test_context_is_symmetric_for_other_division(self):
        """Зеркальная проверка: тренер дивизиона Б не видит клубов дивизиона А."""
        ctx_b = await self._capture_context(self.user_b1)

        self.assertIn(self.team_b1, ctx_b)
        self.assertIn(self.team_b2, ctx_b)
        self.assertNotIn(self.team_a1, ctx_b)
        self.assertNotIn(self.team_a2, ctx_b)

    async def test_context_names_division_and_promotion_rules(self):
        """В контекст попадают имя дивизиона и правила повышения/вылета."""
        ctx_a = await self._capture_context(self.user_a1)

        self.assertIn(f"CHT Альфа {self.uid}", ctx_a)
        self.assertIn("СТРУКТУРА ТУРНИРА", ctx_a)
        self.assertTrue(
            "повышени" in ctx_a.lower() or "вылет" in ctx_a.lower(),
            "В контексте нет упоминания повышения/вылета",
        )

    async def test_archive_is_marked_as_history(self):
        """Старая единая лига подаётся как архив, а не как текущая таблица."""
        ctx_a = await self._capture_context(self.user_a1)

        self.assertIn("АРХИВ", ctx_a)
        self.assertIn("ДО ДИВИЗИОНОВ", ctx_a.upper())

    async def test_user_without_division_gets_no_tournament_data(self):
        """Без дивизиона турнирных данных не отдаём вовсе — вместо каши из всех дивизионов."""
        orphan_id = 97499
        database.register_user(orphan_id, f"cht_orphan_{self.uid}", team_name=f"CHT Orphan {self.uid}")
        database.assign_user_division(orphan_id, None)
        try:
            ctx = await self._capture_context(orphan_id)

            self.assertIn("НЕ приписан", ctx)
            for team in (self.team_a1, self.team_a2, self.team_b1, self.team_b2):
                self.assertNotIn(team, ctx)
        finally:
            with database.transaction() as conn:
                conn.cursor().execute("DELETE FROM users WHERE telegram_id = ?", (orphan_id,))

    async def test_recent_matches_are_division_scoped(self):
        """Счёт чужого дивизиона не утекает в блок последних матчей."""
        ctx_a = await self._capture_context(self.user_a1)

        self.assertIn("3 : 1", ctx_a)
        self.assertNotIn("5 : 0", ctx_a)

    async def test_structure_reports_division_size(self):
        """Модель должна знать точный размер дивизиона, а не гадать про «16 клубов»."""
        ctx_a = await self._capture_context(self.user_a1)

        self.assertIn("Клубов в этом дивизионе: 2", ctx_a)

    async def test_new_era_club_names_are_not_swapped_for_kpl_names(self):
        """Ростер вырос за 16 клубов: имя новичка не должно подменяться каноническим клубом КПЛ.

        resolve_team_name фуззи-матчит против config.KPL_TEAMS, поэтому клуб из
        новых дивизионов рискует приехать в контекст под чужим названием.
        """
        import config

        ctx_a = await self._capture_context(self.user_a1)

        for canon in config.KPL_TEAMS:
            self.assertNotIn(
                f"• {canon} —",
                ctx_a,
                f"Клуб дивизиона подменён каноническим именем КПЛ: {canon}",
            )


if __name__ == "__main__":
    unittest.main()
