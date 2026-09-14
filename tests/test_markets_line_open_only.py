"""Линия лобби отдаёт только матчи, открытые для ставок.

Табы туров в Mini App убраны, матчи показываются единым сквозным списком, поэтому
`/api/markets/tours` не должен подмешивать в ответ архив: сыгранные матчи с
заглушечными коэффициентами 1.0 раньше приезжали вместе с открытой линией.
"""
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aiohttp.test_utils import AioHTTPTestCase

import config
import database
from api.server import create_app

# Заведомо недействительный тестовый токен из документации Telegram.
TEST_BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

TOUR = 77


class TestMarketsLineOpenOnly(AioHTTPTestCase):
    async def get_application(self):
        database.init_db()
        return create_app()

    async def asyncSetUp(self):
        self._original_token = config.TOKEN
        config.TOKEN = TEST_BOT_TOKEN
        await super().asyncSetUp()

        uid = uuid.uuid4().hex[:6].upper()
        self.division_id = database.create_division(name=f"LINE Дивизион {uid}", code=f"LINED_{uid}")

        self.user_ids = (97401, 97402, 97403, 97404)
        teams = [f"LINE Team {i} {uid}" for i in range(4)]
        self.home_open, self.away_open, self.home_done, self.away_done = teams

        for tg_id, team in zip(self.user_ids, teams):
            database.register_user(tg_id, f"line_user_{tg_id}_{uid}", team_name=team)
            database.assign_user_division(tg_id, self.division_id)
        self.viewer_id = self.user_ids[0]

        season = database.get_active_season()
        self.season_id = season["id"] if season else 1

        with database.transaction() as conn:
            c = conn.cursor()
            # Тур открыт для ставок, дедлайн далеко впереди.
            c.execute(
                "INSERT INTO rounds (round_number, is_open, bets_open, deadline, division_id, season_id) "
                "VALUES (?, 1, 1, ?, ?, ?)",
                (TOUR, "01.01.2030 20:00", self.division_id, self.season_id),
            )
            # Несыгранный матч — должен попасть в линию.
            c.execute(
                "INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, "
                "status, division_id, season_id, tournament_type) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, 'league')",
                (TOUR, self.user_ids[0], self.user_ids[1], self.home_open, self.away_open,
                 self.division_id, self.season_id),
            )
            self.open_match_id = c.lastrowid
            # Сыгранный матч того же тура — в линию попадать не должен.
            c.execute(
                "INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, "
                "player1_score, player2_score, status, division_id, season_id, tournament_type, played_at) "
                "VALUES (?, ?, ?, ?, ?, 3, 1, 'confirmed', ?, ?, 'league', CURRENT_TIMESTAMP)",
                (TOUR, self.user_ids[2], self.user_ids[3], self.home_done, self.away_done,
                 self.division_id, self.season_id),
            )
            self.finished_match_id = c.lastrowid

            for m_id, t1, t2 in (
                (self.open_match_id, self.home_open, self.away_open),
                (self.finished_match_id, self.home_done, self.away_done),
            ):
                c.execute(
                    "INSERT OR REPLACE INTO bet_markets (match_id, tour, team1_name, team2_name, "
                    "odd_p1, odd_x, odd_p2, is_active) VALUES (?, ?, ?, ?, 1.85, 3.30, 2.40, 1)",
                    (m_id, TOUR, t1, t2),
                )

    async def asyncTearDown(self):
        try:
            with database.transaction() as conn:
                c = conn.cursor()
                c.execute(
                    "DELETE FROM bet_markets WHERE match_id IN "
                    "(SELECT id FROM matches WHERE division_id = ?)",
                    (self.division_id,),
                )
                c.execute("DELETE FROM matches WHERE division_id = ?", (self.division_id,))
                c.execute("DELETE FROM rounds WHERE division_id = ?", (self.division_id,))
                c.execute(
                    f"DELETE FROM users WHERE telegram_id IN ({','.join('?' * len(self.user_ids))})",
                    self.user_ids,
                )
                c.execute("DELETE FROM divisions WHERE id = ?", (self.division_id,))
        finally:
            await super().asyncTearDown()
            config.TOKEN = self._original_token

    def _headers(self, user_id: int) -> dict:
        user_dict = {"id": user_id, "first_name": "Coach", "username": f"line_{user_id}"}
        data = {
            "auth_date": str(int(time.time())),
            "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
            "user": json.dumps(user_dict, separators=(",", ":")),
        }
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret_key = hmac.new(b"WebAppData", TEST_BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
        data["hash"] = hmac.new(
            secret_key, data_check_string.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return {"X-Telegram-Init-Data": urllib.parse.urlencode(data)}

    async def _fetch_line(self) -> list[dict]:
        resp = await self.client.request(
            "GET",
            f"/api/markets/tours?division_id={self.division_id}",
            headers=self._headers(self.viewer_id),
        )
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["status"], "ok")
        return [m for t in body["tours"] for m in t["matches"]]

    async def test_open_match_is_in_line_with_real_odds(self):
        """1. Несыгранный матч приезжает с флагом линии и живыми коэффициентами."""
        matches = await self._fetch_line()
        open_match = next((m for m in matches if m["match_id"] == self.open_match_id), None)
        self.assertIsNotNone(open_match, "Открытый матч должен быть в линии.")
        self.assertTrue(open_match["is_line"])
        self.assertEqual(open_match["tour"], TOUR)
        self.assertGreater(open_match["odds"]["p1"], 1.0)
        self.assertGreater(open_match["odds"]["x"], 1.0)
        self.assertGreater(open_match["odds"]["p2"], 1.0)

    async def test_finished_match_is_not_mixed_into_line(self):
        """2. Сыгранный матч в линию не подмешивается — его место в «Турнирах»."""
        matches = await self._fetch_line()
        self.assertNotIn(self.finished_match_id, [m["match_id"] for m in matches])
        for m in matches:
            self.assertNotIn(m["status"], ("confirmed", "completed", "finished", "cancelled"))
