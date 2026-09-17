"""В дивизионе одновременно живут максимум 2 тура с неистекшим дедлайном.

Лимит нужен конвейеру линии Logovo.bet «два через два»: на два текущих тура
ставки закрыты, на два следующих выставляется ранняя линия. Третий открытый тур
ломает эту раскладку, поэтому открытие блокируется на уровне `database.py`.

Слот считается строго по дедлайну и освобождается сам: админ старые туры руками
не закрывает (`is_open` остаётся 1), но как только `deadline <= now()`, тур
перестаёт быть активным и пара следующих туров открывается штатно.

Здесь проверяется:
1. Два тура с будущим дедлайном открываются без препятствий.
2. Третий тур до истечения дедлайна — `MaxActiveRoundsExceededError`, в БД ничего.
3. После истечения дедлайна пары следующие два тура открываются без ручного
   перевода `is_open = 0`.
4. Кнопка «📦 Открыть туры» сразу спрашивает дедлайн: диапазон подбирается сам.
"""
import datetime
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import config
import database


def _active_season_id() -> int:
    season = database.get_active_season()
    return season["id"] if season else 1


def _offset_deadline(**delta) -> str:
    """Дедлайн относительно «сейчас» в формате, который пишет админ."""
    return (datetime.datetime.now() + datetime.timedelta(**delta)).strftime("%d.%m.%Y %H:%M")


class MaxActiveRoundsBase(unittest.TestCase):
    """Свой дивизион на каждый прогон: тесты разных файлов идут параллельно."""

    def setUp(self):
        database.init_db()
        self.uid = uuid.uuid4().hex[:6].upper()
        self.season_id = _active_season_id()
        self.div_id = database.create_division(
            name=f"Limit Div {self.uid}", code=f"LMT_{self.uid}"
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
                    f"Limit Home {self.uid}",
                    f"Limit Away {self.uid}",
                    self.div_id,
                    self.season_id,
                ),
            )

    def _schedule(self, *round_numbers: int) -> None:
        for r_num in round_numbers:
            self._add_match(r_num)

    def _round(self, round_number: int):
        return database.get_round_info(round_number, division_id=self.div_id, season_id=self.season_id)

    def _open(self, round_number: int, deadline: str) -> None:
        database.update_round_status(
            round_number=round_number, is_open=True, deadline=deadline,
            division_id=self.div_id, season_id=self.season_id,
        )

    def _active_numbers(self) -> list[int]:
        return [
            r["round_number"]
            for r in database.get_active_open_rounds(self.div_id, self.season_id)
        ]


class TestActiveRoundsLimit(MaxActiveRoundsBase):
    def test_open_two_rounds_with_future_deadline_succeeds(self):
        self._schedule(1, 2)
        deadline = _offset_deadline(days=3)

        self._open(1, deadline)
        self._open(2, deadline)

        self.assertEqual(self._round(1)["is_open"], 1)
        self.assertEqual(self._round(2)["is_open"], 1)
        self.assertEqual(self._active_numbers(), [1, 2])

    def test_opening_third_round_before_deadline_is_rejected(self):
        self._schedule(1, 2, 3)
        deadline = _offset_deadline(days=3)
        self._open(1, deadline)
        self._open(2, deadline)

        with self.assertRaises(database.MaxActiveRoundsExceededError) as ctx:
            self._open(3, deadline)

        self.assertEqual(ctx.exception.active_rounds, [1, 2])
        self.assertEqual(ctx.exception.limit, config.MAX_OPEN_ROUNDS_PER_DIVISION)
        # Отказ ничего не меняет: тур 3 остаётся закрытым и без дедлайна. Строка
        # у него к этому моменту уже есть — её завела ранняя линия Logovo.bet,
        # которую открытие туров 1–2 автоматически двинуло на 3–4.
        self.assertEqual(self._round(3)["is_open"], 0)
        self.assertIsNone(self._round(3)["deadline"])
        self.assertEqual(self._active_numbers(), [1, 2])

    def test_opening_rounds_after_deadline_passed_succeeds_without_manual_close(self):
        self._schedule(1, 2, 3, 4)
        past = _offset_deadline(hours=-1)
        self._open(1, past)
        self._open(2, past)

        # Туры остаются открытыми в БД — админ их не закрывал.
        self.assertEqual(self._round(1)["is_open"], 1)
        self.assertEqual(self._round(2)["is_open"], 1)
        # Но слотов уже не занимают: дедлайн истёк.
        self.assertEqual(self._active_numbers(), [])

        future = _offset_deadline(days=3)
        self._open(3, future)
        self._open(4, future)

        self.assertEqual(self._round(3)["is_open"], 1)
        self.assertEqual(self._round(4)["is_open"], 1)
        self.assertEqual(self._active_numbers(), [3, 4])

    def test_changing_the_deadline_of_an_active_round_is_allowed(self):
        """Уже активный тур свой же слот повторно не занимает — иначе его нельзя продлить."""
        self._schedule(1, 2)
        self._open(1, _offset_deadline(days=3))
        self._open(2, _offset_deadline(days=3))

        extended = _offset_deadline(days=5)
        self._open(2, extended)

        self.assertEqual(self._round(2)["deadline"], extended)
        self.assertEqual(self._active_numbers(), [1, 2])

    def test_limit_is_isolated_per_division(self):
        other_div = database.create_division(
            name=f"Limit Other {self.uid}", code=f"LMTO_{self.uid}"
        )
        try:
            self._schedule(1, 2)
            deadline = _offset_deadline(days=3)
            self._open(1, deadline)
            self._open(2, deadline)

            with database.transaction() as conn:
                conn.execute(
                    "INSERT INTO matches (round_number, player1_team, player2_team, status, division_id, season_id) "
                    "VALUES (1, ?, ?, 'pending', ?, ?)",
                    (f"Other H {self.uid}", f"Other A {self.uid}", other_div, self.season_id),
                )

            # Забитые слоты соседнего дивизиона на этот дивизион не влияют.
            database.update_round_status(
                round_number=1, is_open=True, deadline=deadline,
                division_id=other_div, season_id=self.season_id,
            )
            self.assertEqual(
                database.get_round_info(1, division_id=other_div, season_id=self.season_id)["is_open"], 1
            )
        finally:
            with database.transaction() as conn:
                c = conn.cursor()
                c.execute("DELETE FROM matches WHERE division_id = ?", (other_div,))
                c.execute("DELETE FROM rounds WHERE division_id = ?", (other_div,))
                c.execute("DELETE FROM divisions WHERE id = ?", (other_div,))


class TestOpenRoundsBatchLimit(MaxActiveRoundsBase):
    def test_batch_opens_a_pair_and_then_refuses_the_next_one(self):
        self._schedule(1, 2, 3, 4)
        deadline = _offset_deadline(days=3)

        report = database.open_rounds_batch(
            start_round=1, end_round=2, deadline=deadline,
            division_id=self.div_id, season_id=self.season_id,
        )
        self.assertEqual(report["opened"], [1, 2])

        with self.assertRaises(database.MaxActiveRoundsExceededError):
            database.open_rounds_batch(
                start_round=3, end_round=4, deadline=deadline,
                division_id=self.div_id, season_id=self.season_id,
            )

        # Превышение не открывает даже часть диапазона.
        self.assertEqual(self._round(3)["is_open"], 0)
        self.assertEqual(self._round(4)["is_open"], 0)
        self.assertEqual(self._active_numbers(), [1, 2])

    def test_batch_after_deadline_opens_the_next_pair(self):
        self._schedule(1, 2, 3, 4)
        database.open_rounds_batch(
            start_round=1, end_round=2, deadline=_offset_deadline(hours=-1),
            division_id=self.div_id, season_id=self.season_id,
        )

        report = database.open_rounds_batch(
            start_round=3, end_round=4, deadline=_offset_deadline(days=3),
            division_id=self.div_id, season_id=self.season_id,
        )

        self.assertEqual(report["opened"], [3, 4])
        self.assertEqual(self._active_numbers(), [3, 4])


class TestGetNextRoundsToOpen(MaxActiveRoundsBase):
    def test_first_call_offers_rounds_one_and_two(self):
        self._schedule(1, 2, 3)
        self.assertEqual(
            database.get_next_rounds_to_open(self.div_id, self.season_id), [1, 2]
        )

    def test_next_pair_follows_the_highest_opened_round(self):
        self._schedule(1, 2, 3, 4)
        deadline = _offset_deadline(hours=-1)
        self._open(1, deadline)
        self._open(2, deadline)

        self.assertEqual(
            database.get_next_rounds_to_open(self.div_id, self.season_id), [3, 4]
        )

    def test_rounds_without_a_schedule_are_not_offered(self):
        self._schedule(1)
        self.assertEqual(
            database.get_next_rounds_to_open(self.div_id, self.season_id), [1]
        )


class TestAdminBatchButtonFlow(unittest.IsolatedAsyncioTestCase):
    """Кнопка «📦 Открыть туры» сама выбирает пару и сразу просит дедлайн."""

    async def asyncSetUp(self):
        database.init_db()
        self.uid = uuid.uuid4().hex[:6].upper()
        self.season_id = _active_season_id()
        self.div_id = database.create_division(
            name=f"Limit Btn {self.uid}", code=f"LBTN_{self.uid}"
        )
        self.admin_id = 97833

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
                (round_number, f"Btn H {self.uid}", f"Btn A {self.uid}", self.div_id, self.season_id),
            )

    async def _press_open_rounds(self):
        from handlers import admin as admin_handlers

        query = MagicMock()
        query.data = f"admin_batch_open_div:{self.div_id}"
        query.from_user.id = self.admin_id
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        update.effective_user.id = self.admin_id

        context = MagicMock()
        context.user_data = {}

        # `admin_only` резолвит права через handlers.base — патчим обе точки.
        with patch("handlers.admin.is_admin", return_value=True), \
             patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin._ensure_division_access", new=AsyncMock(return_value=True)):
            state = await admin_handlers.admin_open_batch_prompt(update, context)

        return state, context.user_data, query.edit_message_text.call_args[0][0]

    async def test_admin_batch_button_flow_prompts_deadline_directly(self):
        from handlers.admin import ADMIN_WAITING_FOR_BATCH_DEADLINE

        self._add_match(1)
        self._add_match(2)

        state, user_data, text = await self._press_open_rounds()

        self.assertEqual(state, ADMIN_WAITING_FOR_BATCH_DEADLINE)
        # Диапазон подобран сам — шага ручного ввода больше нет.
        self.assertEqual(user_data["batch_start"], 1)
        self.assertEqual(user_data["batch_end"], 2)
        self.assertEqual(user_data["batch_div_id"], self.div_id)
        self.assertIn("Открытие туров 1 и 2", text)
        self.assertIn("ДД.ММ.ГГГГ ЧЧ:ММ", text)

    async def test_admin_batch_button_refuses_when_the_limit_is_taken(self):
        from telegram.ext import ConversationHandler

        self._add_match(1)
        self._add_match(2)
        deadline = _offset_deadline(days=3)
        database.open_rounds_batch(
            start_round=1, end_round=2, deadline=deadline,
            division_id=self.div_id, season_id=self.season_id,
        )

        state, user_data, text = await self._press_open_rounds()

        self.assertEqual(state, ConversationHandler.END)
        self.assertNotIn("batch_start", user_data)
        self.assertIn("уже открыты туры 1 и 2", text)
        self.assertIn(deadline, text)

    async def test_admin_batch_button_offers_the_next_pair_after_the_deadline(self):
        from handlers.admin import ADMIN_WAITING_FOR_BATCH_DEADLINE

        for r_num in (1, 2, 3, 4):
            self._add_match(r_num)
        database.open_rounds_batch(
            start_round=1, end_round=2, deadline=_offset_deadline(hours=-1),
            division_id=self.div_id, season_id=self.season_id,
        )

        state, user_data, text = await self._press_open_rounds()

        self.assertEqual(state, ADMIN_WAITING_FOR_BATCH_DEADLINE)
        self.assertEqual(user_data["batch_start"], 3)
        self.assertEqual(user_data["batch_end"], 4)
        self.assertIn("Открытие туров 3 и 4", text)


class TestTemshikCommandsReportTheLimit(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.init_db()
        self.uid = uuid.uuid4().hex[:6].upper()
        self.season_id = _active_season_id()
        self.div_id = database.create_division(
            name=f"Limit CMD {self.uid}", code=f"LCMD_{self.uid}"
        )
        self.admin_id = 97834
        database.register_user(self.admin_id, f"limit_adm_{self.uid}", team_name=f"Limit Adm {self.uid}")
        database.assign_user_division(self.admin_id, self.div_id)

        for r_num in (1, 2, 3):
            with database.transaction() as conn:
                conn.execute(
                    "INSERT INTO matches (round_number, player1_team, player2_team, status, division_id, season_id) "
                    "VALUES (?, ?, ?, 'pending', ?, ?)",
                    (r_num, f"Cmd H {self.uid}", f"Cmd A {self.uid}", self.div_id, self.season_id),
                )

        self.deadline = _offset_deadline(days=3)
        database.open_rounds_batch(
            start_round=1, end_round=2, deadline=self.deadline,
            division_id=self.div_id, season_id=self.season_id,
        )

    async def asyncTearDown(self):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM matches WHERE division_id = ?", (self.div_id,))
            c.execute("DELETE FROM rounds WHERE division_id = ?", (self.div_id,))
            c.execute("DELETE FROM users WHERE telegram_id = ?", (self.admin_id,))
            c.execute("DELETE FROM divisions WHERE id = ?", (self.div_id,))

    async def _run(self, text: str) -> str:
        from handlers.text_commands import handle_temshik_command

        update = MagicMock()
        update.message.text = text
        update.message.message_thread_id = None
        update.message.reply_text = AsyncMock()
        update.effective_message = update.message
        update.effective_user.id = self.admin_id
        update.effective_user.username = "limit_adm"
        update.effective_chat.id = self.admin_id
        update.effective_chat.type = "private"

        with patch("handlers.text_commands.is_admin", return_value=True):
            handled = await handle_temshik_command(update, MagicMock())

        self.assertTrue(handled)
        return update.message.reply_text.call_args[0][0]

    async def test_open_round_command_reports_the_limit(self):
        text = await self._run("Темшик открыть тур 3")

        self.assertIn("Лимит туров!", text)
        self.assertIn("уже открыты туры 1 и 2", text)
        self.assertNotIn("успешно открыт", text)
        # Строка тура уже есть — её завела ранняя линия; важно, что он закрыт.
        self.assertEqual(
            database.get_round_info(3, division_id=self.div_id, season_id=self.season_id)["is_open"], 0
        )

    async def test_deadline_command_reports_the_limit(self):
        text = await self._run(f"Темшик дедлайн 3 {_offset_deadline(days=4)}")

        self.assertIn("Лимит туров!", text)
        # Строка тура уже есть — её завела ранняя линия; важно, что он закрыт.
        self.assertEqual(
            database.get_round_info(3, division_id=self.div_id, season_id=self.season_id)["is_open"], 0
        )


if __name__ == "__main__":
    unittest.main()
