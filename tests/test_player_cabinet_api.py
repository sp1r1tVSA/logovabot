"""API личного кабинета игрока («Мой Клуб») в Mini App.

Кабинет отдаёт данные клуба строго по telegram_id из валидированного initData,
поэтому проверяются и содержимое ответов, и границы доступа: неавторизованный
запрос, игрок без клуба и попытка постороннего согласовать чужое время матча.
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

# Заведомо недействительный тестовый токен из документации Telegram: настоящий
# TELEGRAM_BOT_TOKEN в тестах не используется и в репозиторий не попадает.
TEST_BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


class TestPlayerCabinetApi(AioHTTPTestCase):
    async def get_application(self):
        database.init_db()
        return create_app()

    async def asyncSetUp(self):
        self._original_token = config.TOKEN
        config.TOKEN = TEST_BOT_TOKEN
        await super().asyncSetUp()

        uid = uuid.uuid4().hex[:6].upper()
        self.uid = uid
        self.division_id = database.create_division(name=f"CAB Дивизион {uid}", code=f"CABD_{uid}")

        # Хозяин кабинета, его соперник и посторонний игрок другого клуба.
        self.owner_id = 96301
        self.rival_id = 96302
        self.stranger_id = 96303
        self.nomad_id = 96304  # зарегистрирован в боте, но без клуба

        self.owner_team = f"CAB Owner {uid}"
        self.rival_team = f"CAB Rival {uid}"
        self.stranger_team = f"CAB Stranger {uid}"

        for tg_id, nick, team in (
            (self.owner_id, f"cab_owner_{uid}", self.owner_team),
            (self.rival_id, f"cab_rival_{uid}", self.rival_team),
            (self.stranger_id, f"cab_stranger_{uid}", self.stranger_team),
        ):
            database.register_user(tg_id, nick, team_name=team)
            database.assign_user_division(tg_id, self.division_id)
        database.register_user(self.nomad_id, f"cab_nomad_{uid}")

        season = database.get_active_season()
        self.season_id = season["id"] if season else 1

        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("UPDATE users SET warn_count = 2 WHERE telegram_id = ?", (self.owner_id,))
            c.execute(
                "INSERT INTO rounds (round_number, is_open, deadline, division_id, season_id) "
                "VALUES (1, 1, ?, ?, ?)",
                ("01.01.2030 20:00", self.division_id, self.season_id),
            )
            # Активный матч: владелец кабинета играет дома против соперника.
            c.execute(
                "INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, "
                "status, division_id, season_id, tournament_type) "
                "VALUES (1, ?, ?, ?, ?, 'pending', ?, ?, 'league')",
                (self.owner_id, self.rival_id, self.owner_team, self.rival_team,
                 self.division_id, self.season_id),
            )
            self.active_match_id = c.lastrowid
            # Активный матч на выезде: владелец кабинета — гость.
            c.execute(
                "INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, "
                "status, division_id, season_id, tournament_type) "
                "VALUES (1, ?, ?, ?, ?, 'pending', ?, ?, 'league')",
                (self.stranger_id, self.owner_id, self.stranger_team, self.owner_team,
                 self.division_id, self.season_id),
            )
            self.away_match_id = c.lastrowid
            # Сыгранный матч для блока истории.
            c.execute(
                "INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, "
                "player1_score, player2_score, status, division_id, season_id, tournament_type, played_at) "
                "VALUES (1, ?, ?, ?, ?, 4, 1, 'confirmed', ?, ?, 'league', CURRENT_TIMESTAMP)",
                (self.owner_id, self.rival_id, self.owner_team, self.rival_team,
                 self.division_id, self.season_id),
            )
            self.finished_match_id = c.lastrowid

        self.striker = f"CAB Striker {uid}"
        self.playmaker = f"CAB Playmaker {uid}"
        database.add_squad(self.owner_team, [self.striker, self.playmaker])
        database.save_match_events(
            self.finished_match_id,
            [
                (self.owner_team, self.striker, "goal", 3),
                (self.owner_team, self.playmaker, "goal", 1),
                (self.owner_team, self.playmaker, "assist", 2),
            ],
            team_name=self.owner_team,
        )

    async def asyncTearDown(self):
        try:
            user_ids = (self.owner_id, self.rival_id, self.stranger_id, self.nomad_id)
            match_ids = (self.active_match_id, self.away_match_id, self.finished_match_id)
            with database.transaction() as conn:
                c = conn.cursor()
                c.execute(
                    f"DELETE FROM match_events WHERE match_id IN ({','.join('?' * len(match_ids))})",
                    match_ids,
                )
                c.execute("DELETE FROM matches WHERE division_id = ?", (self.division_id,))
                c.execute("DELETE FROM rounds WHERE division_id = ?", (self.division_id,))
                c.execute(
                    "DELETE FROM squad_players WHERE team_name IN (?, ?, ?)",
                    (self.owner_team, self.rival_team, self.stranger_team),
                )
                c.execute(
                    f"DELETE FROM users WHERE telegram_id IN ({','.join('?' * len(user_ids))})",
                    user_ids,
                )
                c.execute("DELETE FROM divisions WHERE id = ?", (self.division_id,))
        finally:
            await super().asyncTearDown()
            config.TOKEN = self._original_token

    # ------------------------------------------------------------------ utils
    def _init_data(self, user_id: int, username: str = "cab_tester") -> str:
        """Собрать подписанный initData так же, как это делает Telegram."""
        user_dict = {"id": user_id, "first_name": "Coach", "username": username}
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
        return urllib.parse.urlencode(data)

    def _headers(self, user_id: int) -> dict:
        return {"X-Telegram-Init-Data": self._init_data(user_id)}

    # ------------------------------------------------------------------ tests
    async def test_endpoints_require_authentication(self):
        """1. Без initData кабинет не отдаёт ничего — 401 на всех маршрутах."""
        for method, path in (
            ("GET", "/api/cabinet/overview"),
            ("GET", "/api/cabinet/matches"),
            ("GET", "/api/cabinet/squad"),
            ("POST", "/api/cabinet/match-time"),
        ):
            resp = await self.client.request(method, path)
            self.assertEqual(resp.status, 401, f"{method} {path}")

        # Подделанная подпись тоже отклоняется.
        bad = {"X-Telegram-Init-Data": self._init_data(self.owner_id) + "tampered"}
        resp = await self.client.request("GET", "/api/cabinet/overview", headers=bad)
        self.assertEqual(resp.status, 401)

    async def test_overview_for_user_without_club(self):
        """2. Игрок без клуба получает registered = false и пустой блок клуба."""
        resp = await self.client.request(
            "GET", "/api/cabinet/overview", headers=self._headers(self.nomad_id)
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertFalse(data["registered"])
        self.assertIsNone(data["club"])

        resp = await self.client.request(
            "GET", "/api/cabinet/matches", headers=self._headers(self.nomad_id)
        )
        data = await resp.json()
        self.assertFalse(data["registered"])
        self.assertEqual(data["matches"], [])

        resp = await self.client.request(
            "GET", "/api/cabinet/squad", headers=self._headers(self.nomad_id)
        )
        data = await resp.json()
        self.assertFalse(data["registered"])
        self.assertEqual(data["players"], [])

    async def test_overview_for_registered_player(self):
        """3. Участник клуба видит команду, дивизион, предупреждения и турнирные цифры."""
        resp = await self.client.request(
            "GET", "/api/cabinet/overview", headers=self._headers(self.owner_id)
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()

        self.assertTrue(data["registered"])
        self.assertEqual(data["telegram_id"], self.owner_id)
        self.assertEqual(data["club"]["team_name"], self.owner_team)
        self.assertEqual(data["club"]["division_id"], self.division_id)
        self.assertEqual(data["club"]["division_name"], f"CAB Дивизион {self.uid}")

        self.assertEqual(data["discipline"]["warns"], 2)
        self.assertEqual(data["discipline"]["limit"], config.MAX_WARNS_LIMIT)

        tournament = data["tournament"]
        for key in (
            "position", "total_teams", "points", "played",
            "wins", "draws", "losses", "goals_scored", "goals_conceded", "goal_diff",
        ):
            self.assertIn(key, tournament)
        # Один сыгранный матч 4:1 — победа, три очка.
        self.assertEqual(tournament["played"], 1)
        self.assertEqual(tournament["wins"], 1)
        self.assertEqual(tournament["points"], 3)
        self.assertEqual(tournament["goals_scored"], 4)
        self.assertEqual(tournament["goals_conceded"], 1)
        self.assertEqual(tournament["goal_diff"], 3)

    async def test_matches_home_and_away_detection(self):
        """4. Матчи игрока: сторона поля, соперник и дедлайн тура определяются верно."""
        resp = await self.client.request(
            "GET", "/api/cabinet/matches", headers=self._headers(self.owner_id)
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["registered"])

        by_id = {m["id"]: m for m in data["matches"]}
        self.assertIn(self.active_match_id, by_id)
        self.assertIn(self.away_match_id, by_id)

        home = by_id[self.active_match_id]
        self.assertTrue(home["is_home"])
        self.assertEqual(home["opponent_team"], self.rival_team)
        self.assertEqual(home["opponent_user"], f"cab_rival_{self.uid}")
        self.assertEqual(home["status"], "pending")
        self.assertEqual(home["time_status"], "none")
        self.assertIsNone(home["proposed_time"])
        self.assertFalse(home["proposed_by_me"])
        self.assertEqual(home["deadline"], "01.01.2030 20:00")

        away = by_id[self.away_match_id]
        self.assertFalse(away["is_home"])
        self.assertEqual(away["opponent_team"], self.stranger_team)

        # Сыгранный матч приходит отдельным блоком со счётом со стороны игрока.
        recent = {m["id"]: m for m in data["recent"]}
        self.assertIn(self.finished_match_id, recent)
        finished = recent[self.finished_match_id]
        self.assertEqual(finished["status"], "confirmed")
        self.assertEqual(finished["my_score"], 4)
        self.assertEqual(finished["opp_score"], 1)

    async def test_squad_with_goals_and_assists(self):
        """5. Состав клуба отдаётся с личной статистикой и лидерами клуба."""
        resp = await self.client.request(
            "GET", "/api/cabinet/squad", headers=self._headers(self.owner_id)
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()

        self.assertTrue(data["registered"])
        self.assertEqual(data["team_name"], self.owner_team)

        players = {p["player_name"]: p for p in data["players"]}
        self.assertIn(self.striker, players)
        self.assertIn(self.playmaker, players)
        self.assertEqual(players[self.striker]["goals"], 3)
        self.assertEqual(players[self.striker]["assists"], 0)
        self.assertEqual(players[self.playmaker]["goals"], 1)
        self.assertEqual(players[self.playmaker]["assists"], 2)
        # Карточки в схеме не хранятся — контракт отдаёт нули.
        self.assertEqual(players[self.striker]["yellow_cards"], 0)
        self.assertEqual(players[self.striker]["red_cards"], 0)

        self.assertEqual(data["top_scorer"]["player_name"], self.striker)
        self.assertEqual(data["top_assistant"]["player_name"], self.playmaker)

    async def test_match_time_propose_and_accept_with_access_control(self):
        """6. Время согласуют только участники матча; посторонний получает 403."""
        # Владелец кабинета предлагает время.
        resp = await self.client.request(
            "POST", "/api/cabinet/match-time",
            headers=self._headers(self.owner_id),
            json={"match_id": self.active_match_id, "action": "propose", "proposed_time": "19:30"},
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["time_status"], "proposed")
        self.assertEqual(data["proposed_time"], "19:30")
        self.assertTrue(data["proposed_by_me"])

        # Соперник видит чужое предложение в своём кабинете.
        resp = await self.client.request(
            "GET", "/api/cabinet/matches", headers=self._headers(self.rival_id)
        )
        rival_match = next(
            m for m in (await resp.json())["matches"] if m["id"] == self.active_match_id
        )
        self.assertEqual(rival_match["time_status"], "proposed")
        self.assertEqual(rival_match["proposed_time"], "19:30")
        self.assertFalse(rival_match["proposed_by_me"])

        # Посторонний игрок не может ни предложить, ни подтвердить время чужого матча.
        for payload in (
            {"match_id": self.active_match_id, "action": "propose", "proposed_time": "23:00"},
            {"match_id": self.active_match_id, "action": "accept"},
        ):
            resp = await self.client.request(
                "POST", "/api/cabinet/match-time",
                headers=self._headers(self.stranger_id),
                json=payload,
            )
            self.assertEqual(resp.status, 403)

        # Данные матча не изменились после отказов.
        match = database.get_match(self.active_match_id)
        self.assertEqual(match["proposed_time"], "19:30")
        self.assertEqual(match["proposed_by"], self.owner_id)
        self.assertEqual(match["time_status"], "proposed")

        # Автор предложения не может подтвердить сам себя.
        resp = await self.client.request(
            "POST", "/api/cabinet/match-time",
            headers=self._headers(self.owner_id),
            json={"match_id": self.active_match_id, "action": "accept"},
        )
        self.assertEqual(resp.status, 409)

        # Соперник подтверждает — время согласовано.
        resp = await self.client.request(
            "POST", "/api/cabinet/match-time",
            headers=self._headers(self.rival_id),
            json={"match_id": self.active_match_id, "action": "accept"},
        )
        self.assertEqual(resp.status, 200)
        self.assertEqual((await resp.json())["time_status"], "accepted")
        self.assertEqual(database.get_match(self.active_match_id)["time_status"], "accepted")

    async def test_match_time_rejects_invalid_payload(self):
        """Валидация тела запроса: неизвестное действие, чужой id, пустое время."""
        headers = self._headers(self.owner_id)

        resp = await self.client.request(
            "POST", "/api/cabinet/match-time", headers=headers,
            json={"match_id": self.active_match_id, "action": "cancel"},
        )
        self.assertEqual(resp.status, 400)

        resp = await self.client.request(
            "POST", "/api/cabinet/match-time", headers=headers,
            json={"match_id": self.active_match_id, "action": "propose", "proposed_time": "  "},
        )
        self.assertEqual(resp.status, 400)

        resp = await self.client.request(
            "POST", "/api/cabinet/match-time", headers=headers,
            json={"match_id": 999999999, "action": "propose", "proposed_time": "19:30"},
        )
        self.assertEqual(resp.status, 404)
