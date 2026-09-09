"""
tests/test_miniapp_access_call_sites.py

FIX-07 — пропущенные call sites проверки feature access в Mini App API.

FIX-06 сделал саму check_user_access() fail-closed, но нашёл два места, где её
просто не вызывали: успешной HMAC-аутентификации хватало, чтобы получить данные,
закрытые политикой betting_market.

  1. api/routes_markets.py -> handle_get_odds_history()  (GET /api/markets/{id}/odds-history)
  2. api/routes_matches.py -> защищённые Mini App endpoints (/api/matches*, /api/recommendations)

Инвариант: аутентификация != авторизация. После get_authenticated_user()
защищённый endpoint обязан вызвать check_user_access() ДО любой бизнес-операции.

    disabled                  -> 403 access_restricted
    admin_only + пользователь -> 403 access_restricted
    admin_only + админ        -> обычный успешный ответ
    public                    -> обычный успешный ответ
    сбой проверки             -> 403, бизнес-операция не вызвана

Сценарии:
 01. odds-history: disabled -> 403 access_restricted.
 02. odds-history: admin_only + обычный пользователь -> 403.
 03. odds-history: public -> история возвращается.
 04. odds-history: сбой проверки -> 403 и odds_engine.get_odds_history() НЕ вызван.
 05. routes_matches: каждый защищённый endpoint при disabled -> 403.
 06. routes_matches: каждый защищённый endpoint при public -> прежнее успешное поведение.
 07. routes_matches: admin_only -> обычный пользователь 403, глобальный админ проходит.
 08. Ответы не содержат stack trace, внутренних ошибок, initData и токена.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

import config
import database
import services.odds_engine as odds_engine
from api.server import create_app
from config import TOKEN
from tests.test_phase9_security import generate_valid_init_data


USER_ID = 970001          # обычный пользователь, гарантированно не админ
ADMIN_ID = config.ADMIN_IDS[0] if config.ADMIN_IDS else 970002
DIV = 1
ROUND_N = 971


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
    """Один сыгранный и один предстоящий матч + рынок с линией и историей коэффициентов."""
    ids: dict = {}
    with database.transaction() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'callsite_user', 'user')",
            (USER_ID,)
        )
        c.execute("INSERT INTO seasons (name, status) VALUES ('Season CallSites', 'active')")
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
        # История встреч, чтобы stats/h2h/insights имели реальные данные.
        c.execute(
            "INSERT INTO matches (division_id, season_id, round_number, player1_team, player2_team, "
            "player1_score, player2_score, status) VALUES (?, ?, ?, 'Home', 'Away', 3, 1, 'confirmed')",
            (DIV, ids["season_id"], ROUND_N - 1)
        )
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
    return ids


def _assert_no_internals_leaked(testcase, body: str) -> None:
    for needle in (
        "Traceback",
        "RuntimeError",
        "OperationalError",
        "sqlite3",
        "simulated feature access failure",
        "simulated db failure",
        "initData",
        "init_data",
        "hash=",
        os.sep + "api" + os.sep + "auth.py",
        os.sep + "api" + os.sep + "routes_markets.py",
        os.sep + "api" + os.sep + "routes_matches.py",
    ):
        testcase.assertNotIn(
            needle, body,
            f"HTTP-ответ не должен раскрывать внутренние детали: {needle!r}"
        )
    testcase.assertNotIn(TOKEN, body, "Токен бота не должен попадать в ответ")


def _flag_reader_that_explodes(exc: Exception):
    """Ломает чтение ТОЛЬКО betting_market; остальные флаги читаются штатно."""
    real = database.get_feature_flag

    def _side_effect(key, default="admin_only"):
        if key == "betting_market":
            raise exc
        return real(key, default)

    return _side_effect


class TestMiniAppAccessCallSites(AioHTTPTestCase):
    """Реальный HTTP-путь Mini App: аутентифицированный, но неавторизованный пользователь."""

    async def get_application(self) -> web.Application:
        return create_app()

    def setUp(self) -> None:
        self._orig_db_path = database.DB_PATH
        self._tmp_path = _open_temp_db()
        self.ids = _seed()
        super().setUp()
        self.headers = {
            "X-Telegram-Init-Data": generate_valid_init_data(
                {"id": USER_ID, "username": "callsite_user"}, TOKEN
            )
        }
        self.admin_headers = {
            "X-Telegram-Init-Data": generate_valid_init_data(
                {"id": ADMIN_ID, "username": "callsite_admin"}, TOKEN
            )
        }

    def tearDown(self) -> None:
        super().tearDown()
        _drop_temp_db(self._tmp_path, self._orig_db_path)

    # Защищённые Mini App endpoints routes_matches.py.
    # /api/matches/hot сюда сознательно не входит: он не аутентифицирован вовсе
    # и по исходной архитектуре является публичным витринным endpoint'ом.
    def _protected_match_endpoints(self) -> list[str]:
        m = self.ids["match_id"]
        return [
            "/api/matches",
            f"/api/matches/{m}",
            f"/api/matches/{m}/stats",
            f"/api/matches/{m}/h2h",
            f"/api/matches/{m}/insights",
            f"/api/matches/{m}/live",
            "/api/recommendations",
        ]

    def _odds_history_url(self) -> str:
        return f"/api/markets/{self.ids['market_id']}/odds-history?selection_key=p1"

    def _assert_allowed(self, url: str, status: int, body: str) -> None:
        """Разрешённый доступ = прежнее успешное поведение endpoint'а."""
        self.assertEqual(status, 200, f"{url}: {body}")
        data = json.loads(body)
        self.assertEqual(data.get("status"), "ok", url)
        if url.endswith("/live"):
            # FIX-08: статус матча живёт в match_status и не затирает "status".
            self.assertEqual(data.get("match_id"), self.ids["match_id"], url)
            self.assertIn("match_status", data, url)

    async def _assert_restricted(self, url: str, headers: dict) -> str:
        resp = await self.client.get(url, headers=headers)
        body = await resp.text()
        self.assertEqual(resp.status, 403, f"{url}: ожидается 403, тело: {body}")
        data = json.loads(body)
        self.assertEqual(data.get("status"), "error", url)
        self.assertEqual(data.get("error"), "access_restricted", url)
        return body

    # --- 01. odds-history: disabled -----------------------------------------
    @unittest_run_loop
    async def test_01_odds_history_disabled_rejects(self):
        database.set_feature_flag("betting_market", "disabled")
        await self._assert_restricted(self._odds_history_url(), self.headers)

    # --- 02. odds-history: admin_only + обычный пользователь ------------------
    @unittest_run_loop
    async def test_02_odds_history_admin_only_rejects_regular_user(self):
        database.set_feature_flag("betting_market", "admin_only")
        await self._assert_restricted(self._odds_history_url(), self.headers)

    # --- 03. odds-history: public --------------------------------------------
    @unittest_run_loop
    async def test_03_odds_history_public_returns_history(self):
        """Контроль: при разрешённом доступе структура ответа прежняя."""
        database.set_feature_flag("betting_market", "public")

        resp = await self.client.get(self._odds_history_url(), headers=self.headers)
        body = await resp.text()

        self.assertEqual(resp.status, 200, body)
        data = json.loads(body)
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("market_id"), self.ids["market_id"])
        self.assertEqual(data.get("selection_key"), "p1")
        self.assertIn("history", data)
        self.assertIsInstance(data["history"], list)

    # --- 04. odds-history: сбой проверки -> бизнес-операция не выполняется ----
    @unittest_run_loop
    async def test_04_odds_history_access_failure_runs_no_business_operation(self):
        """
        Детектор порядка: check_user_access() -> DENIED -> 403,
        а НЕ get_odds_history() -> потом проверка.
        """
        database.set_feature_flag("betting_market", "public")

        with patch.object(
            database, "get_feature_flag",
            side_effect=_flag_reader_that_explodes(sqlite3.OperationalError("simulated db failure"))
        ), patch.object(odds_engine, "get_odds_history", side_effect=AssertionError(
            "get_odds_history не должен вызываться при запрещённом доступе"
        )) as spy:
            body = await self._assert_restricted(self._odds_history_url(), self.headers)

        spy.assert_not_called()
        _assert_no_internals_leaked(self, body)

    # --- 05. routes_matches: disabled ----------------------------------------
    @unittest_run_loop
    async def test_05_protected_match_endpoints_disabled_reject(self):
        database.set_feature_flag("betting_market", "disabled")
        for url in self._protected_match_endpoints():
            with self.subTest(endpoint=url):
                await self._assert_restricted(url, self.headers)

    # --- 06. routes_matches: public ------------------------------------------
    @unittest_run_loop
    async def test_06_protected_match_endpoints_public_keep_working(self):
        """Контроль: разрешённый доступ не сломан — прежние 200 и status=ok."""
        database.set_feature_flag("betting_market", "public")
        for url in self._protected_match_endpoints():
            with self.subTest(endpoint=url):
                resp = await self.client.get(url, headers=self.headers)
                self._assert_allowed(url, resp.status, await resp.text())

    # --- 07. routes_matches: admin_only --------------------------------------
    @unittest_run_loop
    async def test_07_protected_match_endpoints_admin_only(self):
        """Обычный пользователь получает 403, существующая RBAC админа не тронута."""
        database.set_feature_flag("betting_market", "admin_only")
        for url in self._protected_match_endpoints():
            with self.subTest(endpoint=url, role="user"):
                await self._assert_restricted(url, self.headers)
            with self.subTest(endpoint=url, role="admin"):
                resp = await self.client.get(url, headers=self.admin_headers)
                self._assert_allowed(url, resp.status, await resp.text())

    # --- 08. безопасность ответов ---------------------------------------------
    @unittest_run_loop
    async def test_08_denied_responses_leak_nothing(self):
        database.set_feature_flag("betting_market", "disabled")
        for url in [self._odds_history_url()] + self._protected_match_endpoints():
            with self.subTest(endpoint=url):
                resp = await self.client.get(url, headers=self.headers)
                body = await resp.text()
                self.assertEqual(resp.status, 403, url)
                _assert_no_internals_leaked(self, body)

        # Тот же контракт при внутреннем сбое проверки: 403, а не 500 со стеком.
        database.set_feature_flag("betting_market", "public")
        with patch.object(
            database, "get_feature_flag",
            side_effect=_flag_reader_that_explodes(RuntimeError("simulated feature access failure"))
        ):
            for url in [self._odds_history_url()] + self._protected_match_endpoints():
                with self.subTest(endpoint=url, mode="access_check_failure"):
                    resp = await self.client.get(url, headers=self.headers)
                    body = await resp.text()
                    self.assertEqual(resp.status, 403, f"{url}: {body}")
                    _assert_no_internals_leaked(self, body)


if __name__ == "__main__":
    unittest.main()
