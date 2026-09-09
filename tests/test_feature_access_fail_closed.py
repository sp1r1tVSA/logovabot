"""
tests/test_feature_access_fail_closed.py

FIX-06 — AUTH-01: проверка доступа Mini App к feature-флагу должна быть FAIL-CLOSED.

Инвариант: у обязательной проверки feature access ровно два допустимых исхода.

    public                  -> ALLOW
    admin_only + user       -> REJECT (существующая RBAC-семантика)
    admin_only + admin      -> ALLOW
    disabled                -> REJECT
    строки флага нет        -> существующий безопасный default проекта
    sqlite3.OperationalError-> REJECT
    любое исключение        -> REJECT

Никогда: EXCEPTION -> ALLOW.

Сценарии:
 1. public -> доступ разрешён.
 2. disabled -> доступ запрещён.
 3. admin_only + обычный пользователь -> отказ.
 4. admin_only + глобальный админ -> доступ.
 5. Строки флага нет -> штатный fallback get_feature_flag (default 'public').
 6. sqlite3.OperationalError при чтении флага -> отказ.
 7. RuntimeError при проверке -> отказ.
 8. REST: при сбое проверки защищённый endpoint не выполняет бизнес-операцию.
 9. REST: ответ без stack trace и текста внутреннего исключения.
10. Внутренняя ошибка пишется в лог уровня ERROR с feature key и traceback.
11/12/13. betting_market public / admin_only / disabled на реальном REST-пути.
14. Сбой чтения betting_market НЕ откатывается на permissive default 'public'.
15. Совместимость с FIX-05: admin_only переживает init_db() и виден проверке доступа.
16. Прямой детектор fail-open: сломанная проверка -> REJECT, а не ALLOW.
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


def _flag_reader_that_explodes(exc: Exception):
    """
    Подменяет database.get_feature_flag так, что ломается ТОЛЬКО чтение
    betting_market; остальные флаги читаются штатно.
    """
    real = database.get_feature_flag

    def _side_effect(key, default="admin_only"):
        if key == "betting_market":
            raise exc
        return real(key, default)

    return _side_effect


def _assert_no_internals_leaked(testcase, body: str) -> None:
    for needle in (
        "Traceback",
        "RuntimeError",
        "OperationalError",
        "sqlite3",
        "simulated feature access failure",
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

    # --- 1. public ---------------------------------------------------------
    def test_01_public_allows_access(self):
        database.set_feature_flag("betting_market", "public")
        self.assertTrue(check_user_access(USER_ID))

    # --- 2. disabled -------------------------------------------------------
    def test_02_disabled_rejects_access(self):
        database.set_feature_flag("betting_market", "disabled")
        self.assertFalse(check_user_access(USER_ID))

    # --- 3. admin_only + обычный пользователь -------------------------------
    def test_03_admin_only_rejects_regular_user(self):
        database.set_feature_flag("betting_market", "admin_only")
        self.assertFalse(check_user_access(USER_ID))

    # --- 4. admin_only + админ ---------------------------------------------
    def test_04_admin_only_allows_admin(self):
        database.set_feature_flag("betting_market", "admin_only")
        self.assertTrue(
            check_user_access(ADMIN_ID),
            "Существующая RBAC-семантика: глобальный админ проходит при admin_only"
        )

    # --- 5. отсутствующий флаг ---------------------------------------------
    def test_05_missing_flag_uses_existing_default(self):
        """Строки нет — это НЕ ошибка: работает штатный default проекта ('public')."""
        with database.transaction() as conn:
            conn.cursor().execute("DELETE FROM feature_flags WHERE feature_key = 'betting_market'")
        with database.transaction() as conn:
            row = conn.cursor().execute(
                "SELECT status FROM feature_flags WHERE feature_key = 'betting_market'"
            ).fetchone()
        self.assertIsNone(row, "Строка флага должна отсутствовать")

        self.assertEqual(database.get_feature_flag("betting_market", default="public"), "public")
        self.assertTrue(check_user_access(USER_ID), "Существующий fallback-контракт не должен меняться")

    # --- 6. ошибка БД -------------------------------------------------------
    def test_06_database_error_rejects_access(self):
        database.set_feature_flag("betting_market", "public")
        with patch.object(
            database, "get_feature_flag",
            side_effect=_flag_reader_that_explodes(sqlite3.OperationalError("simulated db failure"))
        ):
            self.assertFalse(
                check_user_access(USER_ID),
                "Ошибка БД при чтении флага не может разрешать доступ"
            )

    # --- 7. неожиданное исключение ------------------------------------------
    def test_07_unexpected_exception_rejects_access(self):
        database.set_feature_flag("betting_market", "public")
        with patch.object(
            database, "get_feature_flag",
            side_effect=_flag_reader_that_explodes(RuntimeError("simulated feature access failure"))
        ):
            self.assertFalse(check_user_access(USER_ID))

    # --- 10. ERROR-лог ------------------------------------------------------
    def test_10_internal_error_is_logged_at_error_level(self):
        database.set_feature_flag("betting_market", "public")
        with patch.object(
            database, "get_feature_flag",
            side_effect=_flag_reader_that_explodes(RuntimeError("simulated feature access failure"))
        ):
            with self.assertLogs("api.auth", level="ERROR") as captured:
                allowed = check_user_access(USER_ID)

        self.assertFalse(allowed)
        record = captured.records[-1]
        self.assertEqual(record.levelno, logging.ERROR)
        self.assertIsNotNone(record.exc_info, "Ожидается logger.exception с traceback на сервере")
        msg = record.getMessage()
        self.assertIn("betting_market", msg)
        self.assertIn(str(USER_ID), msg)
        # Секреты в лог не попадают.
        self.assertNotIn(TOKEN, msg)
        for forbidden in ("initData", "init_data", "hash="):
            self.assertNotIn(forbidden, msg)

    # --- 14. отсутствие permissive fallback ---------------------------------
    def test_14_flag_read_failure_does_not_fall_back_to_public(self):
        """
        Контроль + сбой: тот же пользователь при работающей проверке проходит,
        а при сломанном чтении betting_market — нет. Значит отказ вызван именно
        fail-closed, а не посторонней причиной, и никакого отката на 'public' нет.
        """
        database.set_feature_flag("betting_market", "public")
        self.assertTrue(check_user_access(USER_ID), "Контроль: при исправной проверке доступ есть")

        with patch.object(
            database, "get_feature_flag",
            side_effect=_flag_reader_that_explodes(sqlite3.OperationalError("simulated db failure"))
        ):
            self.assertFalse(check_user_access(USER_ID))

    # --- 15. совместимость с FIX-05 -----------------------------------------
    def test_15_fix05_persistence_compatibility(self):
        """admin_only переживает init_db(), и проверка доступа видит именно его."""
        database.set_feature_flag("betting_market", "admin_only")
        database.init_db()

        self.assertEqual(database.get_feature_flag("betting_market"), "admin_only")
        self.assertFalse(check_user_access(USER_ID))
        self.assertTrue(check_user_access(ADMIN_ID))

    # --- 16. прямой детектор fail-open --------------------------------------
    def test_16_fail_open_detector(self):
        """
        Ловит старую реализацию `except Exception: return True`.
        Проверка ломается на каждом из внутренних шагов по очереди — доступа быть не должно.
        """
        database.set_feature_flag("betting_market", "public")

        breakages = (
            ("get_feature_flag", database, "get_feature_flag",
             _flag_reader_that_explodes(RuntimeError("simulated feature access failure"))),
            ("is_logovo_access_allowed", api_auth, "is_logovo_access_allowed",
             RuntimeError("simulated feature access failure")),
            ("is_admin", api_auth, "is_admin",
             sqlite3.OperationalError("simulated db failure")),
        )
        for label, target, attr, effect in breakages:
            with self.subTest(broken=label):
                with patch.object(target, attr, side_effect=effect):
                    self.assertFalse(
                        check_user_access(USER_ID),
                        f"Сбой в {label} не может превращаться в разрешение доступа"
                    )

        # Невалидный user_id по-прежнему отклоняется без всяких исключений.
        self.assertFalse(check_user_access(0))
        self.assertFalse(check_user_access(-5))


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

    # --- 11. public на REST-пути --------------------------------------------
    @unittest_run_loop
    async def test_11_rest_public_allows_business_operation(self):
        """Контроль: при betting_market = public защищённый endpoint работает."""
        database.set_feature_flag("betting_market", "public")
        before_bets, before_balance = _bet_count(), _balance()

        resp = await self.client.post("/api/predictions", json=self._payload("fa06-ok"), headers=self.headers)

        self.assertEqual(resp.status, 200, await resp.text())
        self.assertEqual(_bet_count(), before_bets + 1)
        self.assertEqual(_balance(), before_balance - 100)

    # --- 12. admin_only на REST-пути -----------------------------------------
    @unittest_run_loop
    async def test_12_rest_admin_only_rejects_regular_user(self):
        database.set_feature_flag("betting_market", "admin_only")
        before_bets, before_balance = _bet_count(), _balance()

        resp = await self.client.post("/api/predictions", json=self._payload("fa06-adm"), headers=self.headers)

        self.assertEqual(resp.status, 403)
        data = await resp.json()
        self.assertEqual(data.get("error"), "access_restricted")
        self.assertEqual((_bet_count(), _balance()), (before_bets, before_balance))

    # --- 13. disabled на REST-пути -------------------------------------------
    @unittest_run_loop
    async def test_13_rest_disabled_rejects(self):
        database.set_feature_flag("betting_market", "disabled")
        before_bets, before_balance = _bet_count(), _balance()

        resp = await self.client.post("/api/predictions", json=self._payload("fa06-dis"), headers=self.headers)

        self.assertEqual(resp.status, 403)
        data = await resp.json()
        self.assertEqual(data.get("error"), "access_restricted")
        self.assertEqual((_bet_count(), _balance()), (before_bets, before_balance))

    # --- 8. бизнес-операция не выполняется при сбое проверки ------------------
    @unittest_run_loop
    async def test_08_rest_feature_error_runs_no_business_operation(self):
        """Сбой проверки доступа -> ставка не создана, баланс не тронут."""
        database.set_feature_flag("betting_market", "public")
        before_bets, before_balance = _bet_count(), _balance()

        with patch.object(
            database, "get_feature_flag",
            side_effect=_flag_reader_that_explodes(sqlite3.OperationalError("simulated db failure"))
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
    async def test_09_rest_feature_error_response_is_safe(self):
        """Ответ соответствует существующему контракту 403 access_restricted, без внутренностей."""
        database.set_feature_flag("betting_market", "public")

        with patch.object(
            database, "get_feature_flag",
            side_effect=_flag_reader_that_explodes(RuntimeError("simulated feature access failure"))
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
