"""
tests/test_risk_engine_fail_closed.py

FIX-04 — Risk Engine must FAIL CLOSED.

Инвариант: риск-контроль — обязательная проверка, а не «необязательная подсистема».

    RiskEngine ALLOW      -> ставка продолжает обработку
    RiskEngine REJECT     -> ставка отклонена (обычный бизнес-отказ)
    RiskEngine EXCEPTION  -> ставка НЕМЕДЛЕННО отклонена:
                             монеты не списаны, купон не создан,
                             bet_items не созданы, coin_transactions не созданы,
                             idempotency-запись не создана,
                             пользователь получает безопасный отказ без stack trace,
                             в лог уходит запись уровня ERROR.

Сценарии:
 1. Нормальный ALLOW -> ставка создана.
 2. Нормальный REJECT -> ставки нет, баланс не тронут.
 3. RuntimeError внутри RiskEngine -> отказ, нет ставки, нет списания, ERROR в логе.
 4. KeyError внутри RiskEngine -> отказ, нет ставки, нет списания.
 5. Ошибка БД внутри RiskEngine -> отказ, нет ставки, нет списания.
 6. Экспресс (A -> ok, B -> exception) -> ничего не создано частично.
 7/8. REST-путь POST /api/predictions: контрольная ставка проходит,
      при исключении RiskEngine — безопасная HTTP-ошибка без stack trace.
 9. Telegram-путь (database.place_user_bet напрямую) -> безопасный отказ.
10. Совместимость с FIX-01: закрытая линия отклоняет ставку независимо от RiskEngine.
11. Совместимость с FIX-03: RiskEngine не может «одолжить» тур чужого сезона/дивизиона.
12. Прямой детектор fail-open: bet_count и balance не меняются при исключении.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

import database
from api.server import create_app
from config import TOKEN
from services.risk_engine import RiskDecision, RiskEngine
from tests.test_phase9_security import generate_valid_init_data


USER_ID = 940001
DIV = 1
ROUND_A = 941
ROUND_B = 942


# --------------------------------------------------------------------------
# Fixture helpers (общие для unittest- и aiohttp-классов)
# --------------------------------------------------------------------------

def _open_temp_db() -> str:
    """Создать изолированную временную БД и переключить на неё database.DB_PATH."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    database.DB_PATH = tmp.name
    database.init_db()
    database.ensure_canonical_divisions()
    return tmp.name


def _seed() -> dict:
    """
    Два матча в одном сезоне и дивизионе, но в РАЗНЫХ турах (для экспресса),
    у каждого свой открытый рынок. Линия открыта: is_open = 0 AND bets_open = 1.
    """
    ids: dict = {}
    with database.transaction() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'failclosed_user', 'user')",
            (USER_ID,)
        )
        c.execute("INSERT INTO seasons (name, status) VALUES ('Season FailClosed', 'active')")
        ids["season_id"] = c.lastrowid

        for label, rnd in (("a", ROUND_A), ("b", ROUND_B)):
            c.execute(
                "INSERT INTO rounds (division_id, round_number, season_id, is_open, bets_open) "
                "VALUES (?, ?, ?, 0, 1)",
                (DIV, rnd, ids["season_id"])
            )
            c.execute(
                "INSERT INTO matches (division_id, season_id, round_number, player1_team, player2_team, status) "
                "VALUES (?, ?, ?, ?, ?, 'pending')",
                (DIV, ids["season_id"], rnd, f"Home {label.upper()}", f"Away {label.upper()}")
            )
            ids[f"match_{label}"] = c.lastrowid
            c.execute(
                "INSERT INTO markets (match_id, market_key, market_name, status) "
                "VALUES (?, '1x2', 'Match Winner', 'open')",
                (ids[f"match_{label}"],)
            )
            ids[f"market_{label}"] = c.lastrowid
            c.execute(
                "INSERT INTO market_selections (market_id, selection_key, selection_name, odds_value, status, odds_version) "
                "VALUES (?, 'p1', 'Home Win', 2.00, 'active', 1)",
                (ids[f"market_{label}"],)
            )
            ids[f"sel_{label}"] = c.lastrowid

    database.get_or_create_wallet(USER_ID)
    with database.transaction() as conn:
        conn.cursor().execute("UPDATE user_wallets SET balance = 50000 WHERE user_id = ?", (USER_ID,))
    return ids


def _drop_temp_db(path: str, original_path: str) -> None:
    database.close_thread_connection()
    database.DB_PATH = original_path
    try:
        os.unlink(path)
    except OSError:
        pass


def _count(sql: str, params: tuple = ()) -> int:
    with database.transaction() as conn:
        return conn.cursor().execute(sql, params).fetchone()[0]


def _bet_count() -> int:
    return _count("SELECT COUNT(*) FROM user_bets WHERE user_id = ?", (USER_ID,))


def _bet_item_count() -> int:
    return _count(
        "SELECT COUNT(*) FROM bet_items WHERE bet_id IN (SELECT id FROM user_bets WHERE user_id = ?)",
        (USER_ID,)
    )


def _coin_tx_count() -> int:
    return _count("SELECT COUNT(*) FROM coin_transactions WHERE user_id = ?", (USER_ID,))


def _balance() -> int:
    return database.get_user_balance(USER_ID)


def _financial_state() -> tuple[int, int, int, int]:
    """Снимок всего, что не должно измениться при внутренней ошибке риск-движка."""
    return _bet_count(), _bet_item_count(), _coin_tx_count(), _balance()


def _allow_decision(*args, **kwargs) -> RiskDecision:
    """Максимально разрешающее решение риск-движка (для проверки FIX-01/FIX-03)."""
    return RiskDecision(decision="ALLOW", allowed=True, reason=None, details={})


def _assert_no_internals_leaked(testcase: unittest.TestCase, payload: str) -> None:
    for token in ("Traceback", "RuntimeError", "KeyError", "OperationalError", "simulated risk failure"):
        testcase.assertNotIn(
            token, payload,
            f"Внутренние детали исключения не должны попадать пользователю: {payload}"
        )


# --------------------------------------------------------------------------
# Основной набор
# --------------------------------------------------------------------------

class TestRiskEngineFailClosed(unittest.TestCase):

    def setUp(self):
        self._orig_db_path = database.DB_PATH
        self._tmp_path = _open_temp_db()
        self.ids = _seed()
        self.sel_a = {
            "match_id": self.ids["match_a"],
            "market_id": self.ids["market_a"],
            "selection_id": self.ids["sel_a"],
            "outcome": "p1",
        }
        self.sel_b = {
            "match_id": self.ids["match_b"],
            "market_id": self.ids["market_b"],
            "selection_id": self.ids["sel_b"],
            "outcome": "p1",
        }

    def tearDown(self):
        _drop_temp_db(self._tmp_path, self._orig_db_path)

    # --- 1. ALLOW ---------------------------------------------------------
    def test_01_risk_allow_places_bet(self):
        """RiskEngine ALLOW -> ставка проходит весь путь и создаётся."""
        before = _financial_state()

        ok, res = database.place_user_bet(user_id=USER_ID, amount=100, selections=[self.sel_a])

        self.assertTrue(ok, f"Штатная ставка должна приниматься: {res}")
        self.assertEqual(_bet_count(), before[0] + 1)
        self.assertEqual(_bet_item_count(), before[1] + 1)
        self.assertEqual(_balance(), before[3] - 100)

    # --- 2. REJECT --------------------------------------------------------
    def test_02_risk_reject_creates_no_bet(self):
        """RiskEngine REJECT -> обычный бизнес-отказ, ставки нет, деньги на месте."""
        before = _financial_state()
        reject = RiskDecision(
            decision="REJECT", allowed=False, reason="RISK_LIMIT",
            message="Слишком много ставок за короткий промежуток времени."
        )

        with patch.object(RiskEngine, "evaluate_bet", return_value=reject):
            ok, res = database.place_user_bet(user_id=USER_ID, amount=100, selections=[self.sel_a])

        self.assertFalse(ok)
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("error"), "RISK_LIMIT")
        self.assertEqual(_financial_state(), before, "Бизнес-отказ не должен ничего менять")

    # --- 3. RuntimeError --------------------------------------------------
    def test_03_runtime_error_rejects_and_logs_error(self):
        """Неожиданный RuntimeError -> отказ, нет списания, ERROR в логе, без stack trace для пользователя."""
        before = _financial_state()

        with patch.object(RiskEngine, "evaluate_bet", side_effect=RuntimeError("simulated risk failure")):
            with self.assertLogs("database", level="ERROR") as logs:
                ok, res = database.place_user_bet(
                    user_id=USER_ID, amount=100, selections=[self.sel_a],
                    idempotency_key="fc04-runtime-error"
                )

        self.assertFalse(ok, "Исключение риск-движка обязано отклонять ставку")
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("error"), "RISK_CHECK_UNAVAILABLE")
        _assert_no_internals_leaked(self, json.dumps(res, ensure_ascii=False))

        self.assertEqual(_financial_state(), before, "Ни ставки, ни списания, ни транзакции быть не должно")

        # Ошибка риск-движка должна быть видна в логах на уровне ERROR со stack trace.
        risk_records = [r for r in logs.records if "RISK_CHECK_UNAVAILABLE" in r.getMessage()]
        self.assertTrue(risk_records, f"Ожидалась ERROR-запись о сбое RiskEngine: {logs.output}")
        record = risk_records[0]
        self.assertIsNotNone(record.exc_info, "logger.exception должен приложить исходное исключение")
        msg = record.getMessage()
        self.assertIn(f"user_id={USER_ID}", msg)
        self.assertIn(str(self.ids["match_a"]), msg)
        self.assertIn(f"round_number={ROUND_A}", msg)
        self.assertIn(f"division_id={DIV}", msg)
        self.assertIn(f"season_id={self.ids['season_id']}", msg)

        # Idempotency-ключ не должен «залипнуть» в успешном состоянии.
        self.assertEqual(
            _count("SELECT COUNT(*) FROM user_bets WHERE idempotency_key = ?", ("fc04-runtime-error",)),
            0,
            "Провалившаяся проверка не должна оставлять idempotency-запись"
        )

    # --- 4. KeyError ------------------------------------------------------
    def test_04_key_error_rejects_without_side_effects(self):
        """KeyError (битые внутренние данные) -> отказ без списания."""
        before = _financial_state()

        with patch.object(RiskEngine, "evaluate_bet", side_effect=KeyError("division_id")):
            ok, res = database.place_user_bet(user_id=USER_ID, amount=100, selections=[self.sel_a])

        self.assertFalse(ok)
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("error"), "RISK_CHECK_UNAVAILABLE")
        _assert_no_internals_leaked(self, json.dumps(res, ensure_ascii=False))
        self.assertEqual(_financial_state(), before)

    # --- 5. Database error -------------------------------------------------
    def test_05_database_error_rejects_without_side_effects(self):
        """Ошибка БД внутри риск-проверки -> отказ без списания."""
        before = _financial_state()

        with patch.object(
            RiskEngine, "evaluate_bet",
            side_effect=sqlite3.OperationalError("no such table: risk_limits_config")
        ):
            ok, res = database.place_user_bet(user_id=USER_ID, amount=100, selections=[self.sel_a])

        self.assertFalse(ok)
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("error"), "RISK_CHECK_UNAVAILABLE")
        _assert_no_internals_leaked(self, json.dumps(res, ensure_ascii=False))
        self.assertEqual(_financial_state(), before)

    # --- 6. Express coupon -------------------------------------------------
    def test_06_express_partial_failure_places_nothing(self):
        """
        Экспресс из двух исходов: первый проходит проверку, второй ломает риск-движок
        изнутри цикла. Купон не должен разместиться ни частично, ни полностью.
        """
        before = _financial_state()
        real_gate = database.evaluate_round_betting_gate

        def _gate(cursor, round_number, division_id=None, season_id=None):
            if round_number == ROUND_B:
                raise RuntimeError("simulated risk failure")
            return real_gate(cursor, round_number, division_id, season_id)

        with patch.object(database, "evaluate_round_betting_gate", side_effect=_gate):
            ok, res = database.place_user_bet(
                user_id=USER_ID, amount=100, selections=[self.sel_a, self.sel_b]
            )

        self.assertFalse(ok, "Экспресс с упавшей проверкой обязан быть отклонён целиком")
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("error"), "RISK_CHECK_UNAVAILABLE")
        self.assertEqual(
            _financial_state(), before,
            "Ни одна нога экспресса не должна быть размещена, списаний быть не должно"
        )

    # --- 9. Telegram path --------------------------------------------------
    def test_09_telegram_path_gets_safe_rejection(self):
        """
        Telegram вызывает database.place_user_bet напрямую (handlers/betting.py:404).
        Пользователь должен увидеть обычный текст отказа, а не внутреннюю ошибку.
        """
        before = _financial_state()
        slip = [self.sel_a]

        with patch.object(RiskEngine, "evaluate_bet", side_effect=RuntimeError("simulated risk failure")):
            success, res = database.place_user_bet(USER_ID, 100, slip)

        self.assertFalse(success)
        self.assertEqual(_financial_state(), before)

        # Ровно та ветка формирования сообщения, что в handlers/betting.py.
        err_msg = str(res)
        if isinstance(res, dict):
            err_msg = res.get("message", res.get("error", ""))
        self.assertTrue(err_msg, "Пользователю должно уйти непустое сообщение об отказе")
        _assert_no_internals_leaked(self, err_msg)

    # --- 10. FIX-01 compatibility -----------------------------------------
    def test_10_fix01_line_gate_survives_permissive_risk_engine(self):
        """
        FIX-01 не должен зависеть от риск-движка: правило is_open = 0 AND bets_open = 1
        применяется в place_user_bet и при полностью разрешающем RiskEngine.
        """
        before = _financial_state()

        # is_open = 0, bets_open = 0 -> отказ
        database.set_round_bets_open(ROUND_A, False, division_id=DIV, season_id=self.ids["season_id"])
        with patch.object(RiskEngine, "evaluate_bet", side_effect=_allow_decision):
            ok_closed, res_closed = database.place_user_bet(user_id=USER_ID, amount=100, selections=[self.sel_a])
        self.assertFalse(ok_closed, f"Закрытая линия обязана отклонять ставку: {res_closed}")

        # is_open = 1, bets_open = 1 -> отказ (тур уже играется)
        with database.transaction() as conn:
            conn.cursor().execute(
                "UPDATE rounds SET is_open = 1, bets_open = 1 "
                "WHERE round_number = ? AND division_id = ? AND season_id = ?",
                (ROUND_A, DIV, self.ids["season_id"])
            )
        with patch.object(RiskEngine, "evaluate_bet", side_effect=_allow_decision):
            ok_started, res_started = database.place_user_bet(user_id=USER_ID, amount=100, selections=[self.sel_a])
        self.assertFalse(ok_started, f"Открытый для игры тур прогнозы не принимает: {res_started}")

        # И исключение риск-движка на закрытой линии тоже не открывает дорогу.
        with patch.object(RiskEngine, "evaluate_bet", side_effect=RuntimeError("simulated risk failure")):
            ok_broken, _ = database.place_user_bet(user_id=USER_ID, amount=100, selections=[self.sel_a])
        self.assertFalse(ok_broken)

        self.assertEqual(_financial_state(), before, "Ни один из трёх сценариев не создаёт ставку")

    # --- 11. FIX-03 compatibility -----------------------------------------
    def test_11_fix03_scope_isolation_survives_permissive_risk_engine(self):
        """
        FIX-03: тур другого сезона/дивизиона с тем же номером не может разрешить ставку,
        даже если риск-движок полностью разрешающий.
        """
        before = _financial_state()

        # Своя линия закрыта, а у чужого сезона и чужого дивизиона тур с тем же
        # номером открыт для ставок.
        database.set_round_bets_open(ROUND_A, False, division_id=DIV, season_id=self.ids["season_id"])
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO seasons (name, status) VALUES ('Season FailClosed Next', 'active')")
            other_season = c.lastrowid
            c.execute(
                "INSERT INTO rounds (division_id, round_number, season_id, is_open, bets_open) "
                "VALUES (?, ?, ?, 0, 1)",
                (DIV, ROUND_A, other_season)
            )
            c.execute(
                "INSERT INTO rounds (division_id, round_number, season_id, is_open, bets_open) "
                "VALUES (?, ?, ?, 0, 1)",
                (DIV + 1, ROUND_A, self.ids["season_id"])
            )

        with patch.object(RiskEngine, "evaluate_bet", side_effect=_allow_decision):
            ok, res = database.place_user_bet(user_id=USER_ID, amount=100, selections=[self.sel_a])

        self.assertFalse(
            ok,
            f"Открытый тур чужого сезона/дивизиона не должен разрешать ставку: {res}"
        )
        self.assertEqual(_financial_state(), before)

    # --- 12. Direct fail-open detector -------------------------------------
    def test_12_fail_open_detector(self):
        """
        Прямой детектор fail-open: реальный продовый путь размещения ставки,
        RiskEngine брошен в исключение. На старой fail-open реализации этот тест падает.
        """
        bets_before = _bet_count()
        balance_before = _balance()

        with patch.object(RiskEngine, "evaluate_bet", side_effect=RuntimeError("simulated risk failure")):
            ok, res = database.place_user_bet(
                user_id=USER_ID, amount=250, selections=[self.sel_a],
                idempotency_key="fc04-fail-open-detector"
            )

        self.assertFalse(ok, "Ставка обязана быть отклонена при сбое риск-контроля")
        self.assertEqual(_bet_count(), bets_before, "Ставка не должна быть создана")
        self.assertEqual(_balance(), balance_before, "Баланс не должен измениться")
        self.assertEqual(
            _count("SELECT COUNT(*) FROM user_bets WHERE idempotency_key = ?", ("fc04-fail-open-detector",)),
            0,
            "Не должно быть ложной успешной idempotency-записи"
        )

        # После восстановления риск-движка тот же ключ по-прежнему пригоден:
        # неуспешная попытка не «сожгла» идемпотентность.
        ok_retry, res_retry = database.place_user_bet(
            user_id=USER_ID, amount=250, selections=[self.sel_a],
            idempotency_key="fc04-fail-open-detector"
        )
        self.assertTrue(ok_retry, f"Повтор после восстановления риск-движка должен пройти: {res_retry}")
        self.assertEqual(_bet_count(), bets_before + 1)
        self.assertEqual(_balance(), balance_before - 250)


# --------------------------------------------------------------------------
# REST-путь Mini App
# --------------------------------------------------------------------------

class TestRiskEngineFailClosedRestPath(AioHTTPTestCase):

    async def get_application(self) -> web.Application:
        return create_app()

    def setUp(self) -> None:
        self._orig_db_path = database.DB_PATH
        self._tmp_path = _open_temp_db()
        self.ids = _seed()
        super().setUp()
        self.headers = {
            "X-Telegram-Init-Data": generate_valid_init_data(
                {"id": USER_ID, "username": "failclosed_user"}, TOKEN
            )
        }

    def tearDown(self) -> None:
        super().tearDown()
        _drop_temp_db(self._tmp_path, self._orig_db_path)

    def _payload(self, key: str) -> dict:
        return {
            "amount": 100,
            "idempotency_key": key,
            "selections": [{
                "match_id": self.ids["match_a"],
                "market_id": self.ids["market_a"],
                "selection_id": self.ids["sel_a"],
                "outcome": "p1",
            }]
        }

    @unittest_run_loop
    async def test_07_rest_control_bet_is_accepted(self):
        """Контроль (не даёт тесту 08 быть «зелёным» по посторонней причине): штатная ставка проходит."""
        before = _financial_state()
        resp = await self.client.post("/api/predictions", json=self._payload("fc04-rest-ok"), headers=self.headers)

        self.assertEqual(resp.status, 200, await resp.text())
        data = await resp.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(_bet_count(), before[0] + 1)
        self.assertEqual(_balance(), before[3] - 100)

    @unittest_run_loop
    async def test_08_rest_risk_exception_is_safe_rejection(self):
        """POST /api/predictions при сбое RiskEngine: бизнес-ошибка без stack trace, ставки нет."""
        before = _financial_state()

        with patch.object(RiskEngine, "evaluate_bet", side_effect=RuntimeError("simulated risk failure")):
            resp = await self.client.post(
                "/api/predictions", json=self._payload("fc04-rest-fail"), headers=self.headers
            )

        self.assertEqual(resp.status, 400, "Ожидается бизнес-ответ по существующему контракту, а не 500")
        body = await resp.text()
        data = json.loads(body)
        self.assertEqual(data.get("status"), "error")
        _assert_no_internals_leaked(self, body)

        self.assertEqual(_financial_state(), before, "REST-путь не должен создавать ставку при сбое риск-контроля")


if __name__ == "__main__":
    unittest.main()
