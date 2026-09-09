"""
tests/test_feature_access_fail_closed.py

AUTH-01: проверка доступа Mini App должна быть FAIL-CLOSED.

Инвариант: у обязательной проверки доступа ровно два допустимых исхода.

    lockdown выключен       -> ALLOW
    lockdown + обычный user -> REJECT
    lockdown + глобальный админ -> ALLOW
    невалидный user_id      -> REJECT
    sqlite3.OperationalError-> REJECT
    любое исключение        -> REJECT

Никогда: EXCEPTION -> ALLOW.

Сценарии:
 1. Обычный доступ разрешён.
 2. Lockdown: обычный пользователь отклонён.
 3. Lockdown: глобальный админ проходит.
 4. Невалидный user_id отклонён без исключений.
 5. sqlite3.OperationalError внутри проверки -> отказ.
 6. RuntimeError внутри проверки -> отказ.
 7. Внутренняя ошибка пишется в лог уровня ERROR с traceback и без секретов.
 8. REST: при сбое проверки защищённый endpoint не выполняет бизнес-операцию.
 9. REST: ответ без stack trace и текста внутреннего исключения.
10. REST: контрольный успешный путь.
11. REST: lockdown отклоняет обычного пользователя (403 от middleware).
"""

import json
import logging
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

import config
import database
from api import auth as api_auth
from api.auth import check_user_access
from api.server import create_app
from config import TOKEN
from tests.test_phase9_security import generate_valid_init_data


USER_ID = 960001          # обычный пользователь, гарантированно не админ
ADMIN_ID = config.ADMIN_IDS[0] if config.ADMIN_IDS else 960002
DIV = 1
ROUND_N = 961


# --------------------------------------------------------------------------
# Fixture helpers
# --------------------------------------------------------------------------

def _open_temp_db() -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    database.close_thread_connection()
    database.DB_PATH = tmp.name
    database.init_db()
    database.ensure_canonical_divisions()
    return tmp.name


def _drop_temp_db(path: str, original_path: str) -> None:
    database.close_thread_connection()
    database.DB_PATH = original_path
    try:
        os.unlink(path)
    except OSError:
        pass


def _seed() -> dict:
    """Один матч с открытым рынком и линией (is_open = 0 AND bets_open = 1) + кошелёк."""
    ids: dict = {}
    with database.transaction() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'featacc_user', 'user')",
            (USER_ID,)
        )
        c.execute("INSERT INTO seasons (name, status) VALUES ('Season FeatureAccess', 'active')")
        ids["season_id"] = c.lastrowid
        c.execute(
            "INSERT INTO rounds (division_id, round_number, season_id, is_open, bets_open) "
            "VALUES (?, ?, ?, 0, 1)",
            (DIV, ROUND_N, ids["season_id"])
        )
        c.execute(
            "INSERT INTO matches (division_id, season_id, round_number, player1_team, player2_team, status) "
            "VALUES (?, ?, ?, 'Home', 'Away', 'pending')",
            (DIV, ids["season_id"], ROUND_N)
        )
        ids["match_id"] = c.lastrowid
        c.execute(
            "INSERT INTO markets (match_id, market_key, market_name, status) "
            "VALUES (?, '1x2', 'Match Winner', 'open')",
            (ids["match_id"],)
        )
        ids["market_id"] = c.lastrowid
        c.execute(
            "INSERT INTO market_selections (market_id, selection_key, selection_name, odds_value, status, odds_version) "
            "VALUES (?, 'p1', 'Home', 2.00, 'active', 1)",
            (ids["market_id"],)
        )
        ids["selection_id"] = c.lastrowid

    database.get_or_create_wallet(USER_ID)
    with database.transaction() as conn:
        conn.cursor().execute("UPDATE user_wallets SET balance = 50000 WHERE user_id = ?", (USER_ID,))
    return ids


def _bet_count() -> int:
    with database.transaction() as conn:
        return conn.cursor().execute("SELECT COUNT(*) FROM user_bets").fetchone()[0]


def _balance() -> int:
    with database.transaction() as conn:
        row = conn.cursor().execute(
            "SELECT balance FROM user_wallets WHERE user_id = ?", (USER_ID,)
        ).fetchone()
        return row[0] if row else 0


def _assert_no_internals_leaked(testcase, body: str) -> None:
    for needle in (
        "Traceback",
        "RuntimeError",
        "OperationalError",
        "sqlite3",
        "simulated access check failure",
        "simulated db failure",
        os.sep + "api" + os.sep + "auth.py",
    ):
        testcase.assertNotIn(
            needle, body,
            f"HTTP-ответ не должен раскрывать внутренние детали: {needle!r}"
        )


# --------------------------------------------------------------------------
# Уровень функции проверки доступа
# --------------------------------------------------------------------------

class TestFeatureAccessFailClosed(unittest.TestCase):

    def setUp(self) -> None:
        self._orig_db_path = database.DB_PATH
        self._tmp_path = _open_temp_db()
        _seed()

    def tearDown(self) -> None:
        _drop_temp_db(self._tmp_path, self._orig_db_path)

    # --- 1. штатный доступ ---------------------------------------------------
    def test_01_regular_user_is_allowed(self):
        with patch.object(config, "is_global_lockdown_enabled", return_value=False):
            self.assertTrue(check_user_access(USER_ID))

    # --- 2. lockdown отклоняет обычного пользователя -------------------------
    def test_02_lockdown_rejects_regular_user(self):
        with patch.object(config, "is_global_lockdown_enabled", return_value=True):
            self.assertFalse(check_user_access(USER_ID))

    # --- 3. lockdown пропускает глобального админа ---------------------------
    def test_03_lockdown_allows_global_admin(self):
        with patch.object(config, "is_global_lockdown_enabled", return_value=True):
            self.assertTrue(
                check_user_access(ADMIN_ID),
                "Существующая RBAC-семантика: глобальный админ проходит при lockdown"
            )

    # --- 4. невалидный user_id -----------------------------------------------
    def test_04_invalid_user_id_is_rejected(self):
        self.assertFalse(check_user_access(0))
        self.assertFalse(check_user_access(-5))
        self.assertFalse(check_user_access(None))

    # --- 5. ошибка БД --------------------------------------------------------
    def test_05_database_error_rejects_access(self):
        with patch.object(
            api_auth, "is_logovo_access_allowed",
            side_effect=sqlite3.OperationalError("simulated db failure")
        ):
            self.assertFalse(
                check_user_access(USER_ID),
                "Ошибка БД внутри проверки не может разрешать доступ"
            )

    # --- 6. неожиданное исключение -------------------------------------------
    def test_06_unexpected_exception_rejects_access(self):
        with patch.object(
            api_auth, "is_logovo_access_allowed",
            side_effect=RuntimeError("simulated access check failure")
        ):
            self.assertFalse(check_user_access(USER_ID))

    # --- 7. ERROR-лог ---------------------------------------------------------
    def test_07_internal_error_is_logged_at_error_level(self):
        with patch.object(
            api_auth, "is_logovo_access_allowed",
            side_effect=RuntimeError("simulated access check failure")
        ):
            with self.assertLogs("api.auth", level="ERROR") as captured:
                allowed = check_user_access(USER_ID)

        self.assertFalse(allowed)
        record = captured.records[-1]
        self.assertEqual(record.levelno, logging.ERROR)
        self.assertIsNotNone(record.exc_info, "Ожидается logger.exception с traceback на сервере")
        msg = record.getMessage()
        self.assertIn(str(USER_ID), msg)
        # Секреты в лог не попадают.
        self.assertNotIn(TOKEN, msg)
        for forbidden in ("initData", "init_data", "hash="):
            self.assertNotIn(forbidden, msg)


# --------------------------------------------------------------------------
# Реальный Mini App REST-путь
# --------------------------------------------------------------------------

class TestFeatureAccessFailClosedRestPath(AioHTTPTestCase):

    async def get_application(self) -> web.Application:
        return create_app()

    def setUp(self) -> None:
        self._orig_db_path = database.DB_PATH
        self._tmp_path = _open_temp_db()
        self.ids = _seed()
        super().setUp()
        self.headers = {
            "X-Telegram-Init-Data": generate_valid_init_data(
                {"id": USER_ID, "username": "featacc_user"}, TOKEN
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
                "match_id": self.ids["match_id"],
                "market_id": self.ids["market_id"],
                "selection_id": self.ids["selection_id"],
                "outcome": "p1",
            }]
        }

    # --- 10. контрольный успешный путь ---------------------------------------
    @unittest_run_loop
    async def test_10_rest_allows_business_operation(self):
        before_bets, before_balance = _bet_count(), _balance()

        resp = await self.client.post("/api/predictions", json=self._payload("fa06-ok"), headers=self.headers)

        self.assertEqual(resp.status, 200, await resp.text())
        self.assertEqual(_bet_count(), before_bets + 1)
        self.assertEqual(_balance(), before_balance - 100)

    # --- 11. lockdown на REST-пути --------------------------------------------
    @unittest_run_loop
    async def test_11_rest_lockdown_rejects_regular_user(self):
        """На HTTP-пути lockdown перехватывает middleware раньше route'а — свой 403."""
        before_bets, before_balance = _bet_count(), _balance()

        with patch.object(config, "is_global_lockdown_enabled", return_value=True):
            resp = await self.client.post(
                "/api/predictions", json=self._payload("fa06-lock"), headers=self.headers
            )

        self.assertEqual(resp.status, 403)
        data = await resp.json()
        self.assertEqual(data.get("error"), "LOGOVO_LOCKDOWN")
        self.assertEqual((_bet_count(), _balance()), (before_bets, before_balance))

    # --- 8. бизнес-операция не выполняется при сбое проверки ------------------
    @unittest_run_loop
    async def test_08_rest_access_error_runs_no_business_operation(self):
        """Сбой проверки доступа -> ставка не создана, баланс не тронут."""
        before_bets, before_balance = _bet_count(), _balance()

        with patch.object(
            api_auth, "is_logovo_access_allowed",
            side_effect=sqlite3.OperationalError("simulated db failure")
        ), patch.object(database, "place_user_bet", side_effect=AssertionError(
            "place_user_bet не должен вызываться после сбоя проверки доступа"
        )):
            resp = await self.client.post(
                "/api/predictions", json=self._payload("fa06-fail-db"), headers=self.headers
            )

        self.assertEqual(resp.status, 403)
        self.assertEqual((_bet_count(), _balance()), (before_bets, before_balance))

    # --- 9. безопасный HTTP-ответ ---------------------------------------------
    @unittest_run_loop
    async def test_09_rest_access_error_response_is_safe(self):
        """Ответ соответствует существующему контракту 403 access_restricted, без внутренностей."""
        with patch.object(
            api_auth, "is_logovo_access_allowed",
            side_effect=RuntimeError("simulated access check failure")
        ):
            resp = await self.client.post(
                "/api/predictions", json=self._payload("fa06-fail-rt"), headers=self.headers
            )

        self.assertEqual(resp.status, 403, "Ожидается существующий 403, а не 500")
        body = await resp.text()
        data = json.loads(body)
        self.assertEqual(data.get("status"), "error")
        self.assertEqual(data.get("error"), "access_restricted")
        _assert_no_internals_leaked(self, body)


if __name__ == "__main__":
    unittest.main()
