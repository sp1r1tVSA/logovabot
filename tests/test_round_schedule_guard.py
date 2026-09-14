"""Тур без расписания открыть нельзя.

Админ писал «Темшик открыть тур 3», бот отвечал «Тур 3 открыт», а в `matches` для
этого (сезон, дивизион, тур) не было ни одной пары. Получался «фантомный» тур:
участникам предлагают вносить результаты, вносить нечего, но дедлайн уже тикает и
долговой трекер считает просрочку.

Здесь проверяется гейт расписания во всех точках открытия:
1. `update_round_status(is_open=True)` без матчей — отказ, в БД не меняется ничего.
2. `update_round_status(is_open=True)` с матчами — открывает тур.
3. Закрытие тура (`is_open=False`) гейтом не трогается: закрыть можно всегда.
4. `open_rounds_batch` проверяет каждый тур диапазона отдельно: туры с расписанием
   открываются, пустые возвращаются в `skipped` и остаются закрытыми.
5. Текстовая команда «Темшик открыть тур N» показывает админу предупреждение
   вместо рапорта об успехе.
"""
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import database
from handlers.text_commands import handle_temshik_command


def _active_season_id() -> int:
    season = database.get_active_season()
    return season["id"] if season else 1


class RoundScheduleGuardBase(unittest.TestCase):
    """Свой дивизион на каждый прогон: тесты разных файлов идут параллельно."""

    def setUp(self):
        database.init_db()
        self.uid = uuid.uuid4().hex[:6].upper()
        self.season_id = _active_season_id()
        self.div_id = database.create_division(
            name=f"Guard Div {self.uid}", code=f"GRD_{self.uid}"
        )

    def tearDown(self):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM matches WHERE division_id = ?", (self.div_id,))
            c.execute("DELETE FROM rounds WHERE division_id = ?", (self.div_id,))
            c.execute("DELETE FROM divisions WHERE id = ?", (self.div_id,))

    def _add_match(self, round_number: int) -> None:
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO matches (round_number, player1_team, player2_team, status, division_id, season_id) "
                "VALUES (?, ?, ?, 'pending', ?, ?)",
                (
                    round_number,
                    f"Guard Home {self.uid}",
                    f"Guard Away {self.uid}",
                    self.div_id,
                    self.season_id,
                ),
            )

    def _is_open(self, round_number: int):
        info = database.get_round_info(round_number, division_id=self.div_id, season_id=self.season_id)
        return None if info is None else info["is_open"]


class TestUpdateRoundStatusScheduleGuard(RoundScheduleGuardBase):
    def test_open_round_without_matches_is_rejected(self):
        with self.assertRaises(database.RoundScheduleMissingError):
            database.update_round_status(
                round_number=3, is_open=True, division_id=self.div_id, season_id=self.season_id
            )

        # Ни строки тура, ни is_open = 1: отказ не оставляет следов в БД.
        self.assertIsNone(self._is_open(3))

    def test_rejected_open_does_not_flip_an_existing_closed_round(self):
        database.create_round(round_number=3, deadline=None, division_id=self.div_id)
        self.assertEqual(self._is_open(3), 0)

        with self.assertRaises(database.RoundScheduleMissingError):
            database.update_round_status(
                round_number=3, is_open=True, deadline="31.12.2026 23:59",
                division_id=self.div_id, season_id=self.season_id,
            )

        info = database.get_round_info(3, division_id=self.div_id, season_id=self.season_id)
        self.assertEqual(info["is_open"], 0)
        self.assertIsNone(info["deadline"], "Дедлайн отклонённого тура не должен сохраняться")

    def test_open_round_with_matches_succeeds(self):
        self._add_match(3)
        database.update_round_status(
            round_number=3, is_open=True, deadline="31.12.2026 23:59",
            division_id=self.div_id, season_id=self.season_id,
        )

        info = database.get_round_info(3, division_id=self.div_id, season_id=self.season_id)
        self.assertEqual(info["is_open"], 1)
        self.assertEqual(info["deadline"], "31.12.2026 23:59")

    def test_matches_of_another_division_do_not_count(self):
        other_div = database.create_division(
            name=f"Guard Other {self.uid}", code=f"GRDO_{self.uid}"
        )
        try:
            with database.transaction() as conn:
                conn.execute(
                    "INSERT INTO matches (round_number, player1_team, player2_team, status, division_id, season_id) "
                    "VALUES (3, ?, ?, 'pending', ?, ?)",
                    (f"Other Home {self.uid}", f"Other Away {self.uid}", other_div, self.season_id),
                )

            with self.assertRaises(database.RoundScheduleMissingError):
                database.update_round_status(
                    round_number=3, is_open=True, division_id=self.div_id, season_id=self.season_id
                )
        finally:
            with database.transaction() as conn:
                c = conn.cursor()
                c.execute("DELETE FROM matches WHERE division_id = ?", (other_div,))
                c.execute("DELETE FROM rounds WHERE division_id = ?", (other_div,))
                c.execute("DELETE FROM divisions WHERE id = ?", (other_div,))

    def test_closing_a_round_without_matches_is_still_allowed(self):
        """Гейт стоит только на открытии: закрыть пустой тур должно быть можно."""
        database.create_round(round_number=3, deadline=None, division_id=self.div_id)
        database.update_round_status(
            round_number=3, is_open=False, division_id=self.div_id, season_id=self.season_id
        )
        self.assertEqual(self._is_open(3), 0)

    def test_count_round_matches_is_scoped(self):
        self._add_match(3)
        self.assertEqual(database.count_round_matches(3, self.div_id, self.season_id), 1)
        self.assertEqual(database.count_round_matches(4, self.div_id, self.season_id), 0)


class TestOpenRoundsBatchScheduleGuard(RoundScheduleGuardBase):
    def test_batch_opens_only_rounds_with_a_schedule(self):
        self._add_match(1)
        self._add_match(3)

        report = database.open_rounds_batch(
            start_round=1, end_round=3, deadline="31.12.2026 23:59",
            division_id=self.div_id, season_id=self.season_id,
        )

        self.assertEqual(report["opened"], [1, 3])
        self.assertEqual(report["skipped"], [2])
        self.assertEqual(self._is_open(1), 1)
        self.assertEqual(self._is_open(3), 1)
        # Пустой тур не открыт и строки не получил.
        self.assertIsNone(self._is_open(2))

    def test_batch_without_any_schedule_changes_nothing(self):
        report = database.open_rounds_batch(
            start_round=1, end_round=3, deadline="31.12.2026 23:59",
            division_id=self.div_id, season_id=self.season_id,
        )

        self.assertEqual(report["opened"], [])
        self.assertEqual(report["skipped"], [1, 2, 3])
        for r in (1, 2, 3):
            self.assertIsNone(self._is_open(r))

    def test_batch_keeps_an_empty_round_closed_even_if_its_row_exists(self):
        database.create_round(round_number=2, deadline=None, division_id=self.div_id)
        self._add_match(1)

        report = database.open_rounds_batch(
            start_round=1, end_round=2, deadline="31.12.2026 23:59",
            division_id=self.div_id, season_id=self.season_id,
        )

        self.assertEqual(report["opened"], [1])
        self.assertEqual(report["skipped"], [2])
        self.assertEqual(self._is_open(2), 0)


class TestTemshikOpenRoundWarnsAboutMissingSchedule(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.init_db()
        self.uid = uuid.uuid4().hex[:6].upper()
        self.season_id = _active_season_id()
        self.div_id = database.create_division(
            name=f"Guard CMD {self.uid}", code=f"GCMD_{self.uid}"
        )
        self.admin_id = 97811
        database.register_user(self.admin_id, f"guard_adm_{self.uid}", team_name=f"Guard Adm {self.uid}")
        database.assign_user_division(self.admin_id, self.div_id)

    async def asyncTearDown(self):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM matches WHERE division_id = ?", (self.div_id,))
            c.execute("DELETE FROM rounds WHERE division_id = ?", (self.div_id,))
            c.execute("DELETE FROM users WHERE telegram_id = ?", (self.admin_id,))
            c.execute("DELETE FROM divisions WHERE id = ?", (self.div_id,))

    def _build_update(self, text: str):
        update = MagicMock()
        update.message.text = text
        update.message.message_thread_id = None
        update.message.reply_text = AsyncMock()
        update.effective_message = update.message
        update.effective_user.id = self.admin_id
        update.effective_user.username = "guard_adm"
        update.effective_chat.id = self.admin_id
        update.effective_chat.type = "private"
        return update

    async def test_open_round_without_schedule_reports_the_problem(self):
        update = self._build_update("Темшик открыть тур 3")
        with patch("handlers.text_commands.is_admin", return_value=True):
            handled = await handle_temshik_command(update, MagicMock())

        self.assertTrue(handled)
        text = update.message.reply_text.call_args[0][0]
        self.assertIn("Нельзя открыть Тур 3", text)
        self.assertIn("расписание ещё не сгенерировано", text)
        self.assertNotIn("успешно открыт", text)
        self.assertIsNone(
            database.get_round_info(3, division_id=self.div_id, season_id=self.season_id),
            "Отклонённое открытие не должно создавать строку тура",
        )

    async def test_open_round_with_schedule_still_works(self):
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO matches (round_number, player1_team, player2_team, status, division_id, season_id) "
                "VALUES (3, ?, ?, 'pending', ?, ?)",
                (f"Guard H {self.uid}", f"Guard A {self.uid}", self.div_id, self.season_id),
            )

        update = self._build_update("Темшик открыть тур 3")
        with patch("handlers.text_commands.is_admin", return_value=True):
            handled = await handle_temshik_command(update, MagicMock())

        self.assertTrue(handled)
        self.assertIn("успешно открыт", update.message.reply_text.call_args[0][0])
        info = database.get_round_info(3, division_id=self.div_id, season_id=self.season_id)
        self.assertEqual(info["is_open"], 1)

    async def test_deadline_command_also_refuses_an_empty_round(self):
        update = self._build_update("Темшик дедлайн 3 18.08 23:59")
        with patch("handlers.text_commands.is_admin", return_value=True):
            handled = await handle_temshik_command(update, MagicMock())

        self.assertTrue(handled)
        text = update.message.reply_text.call_args[0][0]
        self.assertIn("Нельзя открыть Тур 3", text)
        self.assertIsNone(
            database.get_round_info(3, division_id=self.div_id, season_id=self.season_id)
        )


class TestAdminBatchOpenHandlerGuard(unittest.IsolatedAsyncioTestCase):
    """Админ-панель: пакетное открытие не рапортует об успехе для пустых туров."""

    async def asyncSetUp(self):
        database.init_db()
        self.uid = uuid.uuid4().hex[:6].upper()
        self.season_id = _active_season_id()
        self.div_id = database.create_division(
            name=f"Guard Batch {self.uid}", code=f"GBT_{self.uid}"
        )
        self.admin_id = 97812

    async def asyncTearDown(self):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM matches WHERE division_id = ?", (self.div_id,))
            c.execute("DELETE FROM rounds WHERE division_id = ?", (self.div_id,))
            c.execute("DELETE FROM divisions WHERE id = ?", (self.div_id,))

    def _add_match(self, round_number: int) -> None:
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO matches (round_number, player1_team, player2_team, status, division_id, season_id) "
                "VALUES (?, ?, ?, 'pending', ?, ?)",
                (round_number, f"Batch H {self.uid}", f"Batch A {self.uid}", self.div_id, self.season_id),
            )

    async def _run_batch(self, start_r: int, end_r: int):
        from handlers import admin as admin_handlers

        update = MagicMock()
        update.effective_user.id = self.admin_id
        update.message.text = "31.12.2026 23:59"
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.user_data = {"batch_start": start_r, "batch_end": end_r, "batch_div_id": self.div_id}

        with patch("handlers.admin.is_admin", return_value=True), \
             patch("handlers.admin._announce_rounds_opened", new=AsyncMock(return_value=True)), \
             patch("handlers.admin.notify_players_rounds_opened", new=AsyncMock()):
            await admin_handlers.admin_open_batch_deadline(update, context)

        return update.message.reply_text.call_args[0][0]

    async def test_batch_without_any_schedule_reports_failure(self):
        text = await self._run_batch(1, 3)

        self.assertIn("Нельзя открыть туры 1, 2, 3", text)
        self.assertNotIn("успешно", text)
        for r in (1, 2, 3):
            self.assertIsNone(
                database.get_round_info(r, division_id=self.div_id, season_id=self.season_id)
            )

    async def test_batch_warns_about_skipped_rounds(self):
        self._add_match(1)
        text = await self._run_batch(1, 2)

        self.assertIn("Открыты туры: 1", text)
        self.assertIn("Пропущены туры без расписания: 2", text)
        self.assertEqual(
            database.get_round_info(1, division_id=self.div_id, season_id=self.season_id)["is_open"], 1
        )
        self.assertIsNone(
            database.get_round_info(2, division_id=self.div_id, season_id=self.season_id)
        )


if __name__ == "__main__":
    unittest.main()
