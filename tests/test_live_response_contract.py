"""
tests/test_live_response_contract.py

FIX-08 — контракт ответа GET /api/matches/{id}/live.

Баг: в литерале ответа ключ "status" был объявлен дважды —

    {"status": "ok", ..., "status": m["status"]}

Python оставляет последнее значение, поэтому "status" всегда содержал статус
матча, а не статус API-операции. Клиент (web/js/app.js: `liveRes.status === 'ok'`)
из-за этого никогда не признавал ответ успешным.

Новый контракт:

    "status"       -> статус API-операции ("ok" / "error")
    "match_status" -> статус самого матча (имя уже используется в
                      routes_markets.py, routes_live.py, routes_admin_live.py)

Сценарии:
 01. Успешный ответ: HTTP 200 и status == "ok".
 02. Присутствует match_status и совпадает с реальным статусом матча в БД.
 03. В сериализованном JSON ключ "status" встречается ровно один раз.
 04. Идущий матч: status == "ok", match_status == "live" (+ минута и счёт).
 05. Завершённый матч: status == "ok", match_status == "confirmed".
 06. Consumer-контракт: клиентская проверка `res.status === 'ok'` проходит,
     статус матча доступен отдельно, и app.js по-прежнему гейтит на status.
"""

import json
import os
import re
import tempfile
import unittest

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

import database
from api.server import create_app
from config import TOKEN
from tests.test_phase9_security import generate_valid_init_data


USER_ID = 980001
DIV = 1
ROUND_N = 981

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    """Три матча: несыгранный, идущий (live) и завершённый."""
    ids: dict = {}
    with database.transaction() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'live_contract_user', 'user')",
            (USER_ID,)
        )
        c.execute("INSERT INTO seasons (name, status) VALUES ('Season LiveContract', 'active')")
        ids["season_id"] = c.lastrowid

        c.execute(
            "INSERT INTO matches (division_id, season_id, round_number, player1_team, player2_team, status) "
            "VALUES (?, ?, ?, 'Home', 'Away', 'pending')",
            (DIV, ids["season_id"], ROUND_N)
        )
        ids["pending_id"] = c.lastrowid

        c.execute(
            "INSERT INTO matches (division_id, season_id, round_number, player1_team, player2_team, "
            "player1_score, player2_score, status, live_minute) "
            "VALUES (?, ?, ?, 'Home', 'Away', 1, 0, 'live', 57)",
            (DIV, ids["season_id"], ROUND_N)
        )
        ids["live_id"] = c.lastrowid

        c.execute(
            "INSERT INTO matches (division_id, season_id, round_number, player1_team, player2_team, "
            "player1_score, player2_score, status) "
            "VALUES (?, ?, ?, 'Home', 'Away', 3, 1, 'confirmed')",
            (DIV, ids["season_id"], ROUND_N)
        )
        ids["finished_id"] = c.lastrowid

        c.execute(
            "INSERT INTO match_events (match_id, team_name, player_name, event_type, count) "
            "VALUES (?, 'Home', 'Игрок 1', 'goal', 1)",
            (ids["live_id"],)
        )
    return ids


def _duplicate_keys(body: str) -> list[str]:
    """Возвращает ключи, встречающиеся в одном JSON-объекте более одного раза."""
    found: list[str] = []

    def hook(pairs):
        keys = [k for k, _ in pairs]
        for k in set(keys):
            if keys.count(k) > 1:
                found.append(k)
        return dict(pairs)

    json.loads(body, object_pairs_hook=hook)
    return found


class TestLiveResponseStatusContract(AioHTTPTestCase):

    async def get_application(self) -> web.Application:
        return create_app()

    def setUp(self) -> None:
        self._orig_db_path = database.DB_PATH
        self._tmp_path = _open_temp_db()
        self.ids = _seed()
        super().setUp()
        self.headers = {
            "X-Telegram-Init-Data": generate_valid_init_data(
                {"id": USER_ID, "username": "live_contract_user"}, TOKEN
            )
        }

    def tearDown(self) -> None:
        super().tearDown()
        _drop_temp_db(self._tmp_path, self._orig_db_path)

    async def _live(self, match_id: int) -> tuple[int, str, dict]:
        resp = await self.client.get(f"/api/matches/{match_id}/live", headers=self.headers)
        body = await resp.text()
        return resp.status, body, json.loads(body)

    def _db_status(self, match_id: int) -> str:
        with database.transaction() as conn:
            return conn.cursor().execute(
                "SELECT status FROM matches WHERE id = ?", (match_id,)
            ).fetchone()[0]

    # --- 01. status == "ok" ---------------------------------------------------
    @unittest_run_loop
    async def test_01_successful_response_status_is_ok(self):
        for label, key in (("pending", "pending_id"), ("live", "live_id"), ("finished", "finished_id")):
            with self.subTest(match=label):
                http_status, body, data = await self._live(self.ids[key])
                self.assertEqual(http_status, 200, body)
                self.assertEqual(
                    data["status"], "ok",
                    "\"status\" обозначает результат API-операции, а не статус матча"
                )

    # --- 02. match_status ------------------------------------------------------
    @unittest_run_loop
    async def test_02_match_status_matches_database(self):
        for label, key in (("pending", "pending_id"), ("live", "live_id"), ("finished", "finished_id")):
            with self.subTest(match=label):
                _, body, data = await self._live(self.ids[key])
                self.assertIn("match_status", data, body)
                self.assertEqual(data["match_status"], self._db_status(self.ids[key]))
                self.assertEqual(data["match_id"], self.ids[key])

    # --- 03. ключ "status" ровно один раз --------------------------------------
    @unittest_run_loop
    async def test_03_status_key_appears_exactly_once(self):
        for label, key in (("pending", "pending_id"), ("live", "live_id"), ("finished", "finished_id")):
            with self.subTest(match=label):
                _, body, _ = await self._live(self.ids[key])
                self.assertEqual(
                    _duplicate_keys(body), [],
                    f"В JSON не должно быть повторяющихся ключей, тело: {body}"
                )
                self.assertEqual(
                    len(re.findall(r'(?<!_)"status"\s*:', body)), 1,
                    f"Ключ \"status\" должен встречаться ровно один раз, тело: {body}"
                )

    # --- 04. идущий матч --------------------------------------------------------
    @unittest_run_loop
    async def test_04_live_match_contract(self):
        http_status, body, data = await self._live(self.ids["live_id"])

        self.assertEqual(http_status, 200, body)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["match_status"], "live")
        self.assertEqual(data["live_minute"], 57)
        self.assertEqual((data["score1"], data["score2"]), (1, 0))
        self.assertEqual(len(data["events"]), 1)
        self.assertEqual(data["events"][0]["event_type"], "goal")

    # --- 05. завершённый матч ----------------------------------------------------
    @unittest_run_loop
    async def test_05_finished_match_contract(self):
        http_status, body, data = await self._live(self.ids["finished_id"])

        self.assertEqual(http_status, 200, body)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["match_status"], "confirmed")
        self.assertEqual((data["score1"], data["score2"]), (3, 1))

    # --- 06. consumer-контракт ----------------------------------------------------
    @unittest_run_loop
    async def test_06_consumer_contract_is_preserved(self):
        """
        Клиент (web/js/app.js) принимает ответ только при `status === 'ok'`.
        До исправления там лежал статус матча, и live-блок всегда отбрасывался.
        """
        _, _, data = await self._live(self.ids["live_id"])

        accepted = data if data.get("status") == "ok" else None       # логика app.js
        self.assertIsNotNone(accepted, "Клиент должен принять успешный ответ")
        self.assertEqual(accepted["match_status"], "live", "Статус матча доступен отдельным полем")

        app_js = open(os.path.join(REPO_ROOT, "web", "js", "app.js"), encoding="utf-8").read()
        self.assertIn(
            "liveRes.status === 'ok'", app_js,
            "Существующий клиентский контракт (status = успешность API) должен сохраниться"
        )
        ui_js = open(os.path.join(REPO_ROOT, "web", "js", "ui.js"), encoding="utf-8").read()
        self.assertIn(
            "live?.match_status", ui_js,
            "Статус матча в Match Center должен читаться из match_status, а не из status"
        )

        # Ошибочные ответы этого endpoint'а по-прежнему используют "status": "error".
        resp = await self.client.get("/api/matches/99999999/live", headers=self.headers)
        self.assertEqual(resp.status, 404)
        self.assertEqual((await resp.json())["status"], "error")


if __name__ == "__main__":
    unittest.main()
