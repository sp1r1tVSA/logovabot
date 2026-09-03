"""
tests/test_phase2_architecture.py

Comprehensive Phase 2 test suite verifying:
1. Double Round Robin Invariants (16 teams, 30 rounds, 240 matches, 8/round, 30/team, 2/pair, 2 legs).
2. RoundRobinValidator enforcement and violation detection.
3. Season entity lifecycle (draft -> active -> finished -> archived).
4. Strict Division and Season data isolation.
5. Historical data preservation across seasons.
6. Match schedule regeneration protection.
7. REST API season and division isolation.
"""

import os
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import sqlite3
import datetime

import database
from services.tournament_validator import RoundRobinValidator
from handlers.admin import generate_round_robin_fixtures, admin_generate_matches_execute


class TestPhase2Architecture(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db = "test_phase2.db"
        database.DB_PATH = cls.test_db

    def setUp(self):
        if os.path.exists(self.test_db):
            try:
                os.remove(self.test_db)
            except OSError:
                pass
        database.init_db()

        # Seed test divisions and system admin
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO divisions (id, name, code) VALUES (1, 'Высшая Лига', 'div_a')")
            c.execute("INSERT OR IGNORE INTO divisions (id, name, code) VALUES (2, 'Первая Лига', 'div_b')")
            c.execute("INSERT OR IGNORE INTO users (telegram_id, username) VALUES (999, 'admin')")

    def tearDown(self):
        if os.path.exists(self.test_db):
            try:
                os.remove(self.test_db)
            except OSError:
                pass

    def test_01_double_round_robin_30_rounds_mathematics(self):
        """Verify strict 30-round double round robin fixture mathematics for 16 teams."""
        teams = list(range(101, 117))  # 16 teams
        fixtures = generate_round_robin_fixtures(teams)

        # 1. Total matches must be 240 (16 * 15)
        self.assertEqual(len(fixtures), 240)

        # 2. Total rounds must be exactly 30
        rounds = set(f[0] for f in fixtures)
        self.assertEqual(len(rounds), 30)
        self.assertEqual(min(rounds), 1)
        self.assertEqual(max(rounds), 30)

        # 3. Exactly 8 matches per round
        for r in range(1, 31):
            round_matches = [f for f in fixtures if f[0] == r]
            self.assertEqual(len(round_matches), 8, f"Round {r} does not have 8 matches")

            # In each round, all 16 teams must participate exactly once
            teams_in_round = []
            for _, p1, p2 in round_matches:
                self.assertNotEqual(p1, p2, "Self-match detected")
                teams_in_round.extend([p1, p2])
            self.assertEqual(len(set(teams_in_round)), 16, f"Round {r} has duplicate or missing teams")

        # 4. Each team plays exactly 30 matches
        team_counts = {t: 0 for t in teams}
        for _, p1, p2 in fixtures:
            team_counts[p1] += 1
            team_counts[p2] += 1
        for t, count in team_counts.items():
            self.assertEqual(count, 30, f"Team {t} played {count} matches instead of 30")

        # 5. Exactly 2 meetings per pair, with swapped home/away in leg 1 vs leg 2
        leg1 = [f for f in fixtures if 1 <= f[0] <= 15]
        leg2 = [f for f in fixtures if 16 <= f[0] <= 30]
        self.assertEqual(len(leg1), 120)
        self.assertEqual(len(leg2), 120)

        leg1_pairs = set((p1, p2) for _, p1, p2 in leg1)
        leg2_pairs = set((p1, p2) for _, p1, p2 in leg2)

        # Leg 2 must mirror Leg 1 with swapped home/away
        mirrored_leg1 = set((p2, p1) for p1, p2 in leg1_pairs)
        self.assertEqual(leg2_pairs, mirrored_leg1)

        # Validator check
        is_valid, errors = RoundRobinValidator.validate_fixtures(
            fixtures, expected_teams=16, expected_rounds=30, expected_matches=240
        )
        self.assertTrue(is_valid, f"Validator failed: {errors}")
        self.assertEqual(len(errors), 0)

    def test_02_tournament_validator_rejection(self):
        """Verify that RoundRobinValidator strictly rejects invalid schedules."""
        teams = list(range(101, 117))
        fixtures = generate_round_robin_fixtures(teams)

        # Reject truncated 15 rounds (single round robin)
        leg1_only = [f for f in fixtures if f[0] <= 15]
        is_valid, errors = RoundRobinValidator.validate_fixtures(
            leg1_only, expected_teams=16, expected_rounds=30, expected_matches=240
        )
        self.assertFalse(is_valid)
        self.assertTrue(any("240" in e or "30" in e for e in errors))

        # Reject self-match
        bad_fixtures = fixtures.copy()
        bad_fixtures[0] = (1, 101, 101)
        is_valid, errors = RoundRobinValidator.validate_fixtures(bad_fixtures, expected_teams=16, expected_rounds=30, expected_matches=240)
        self.assertFalse(is_valid)
        self.assertTrue(any("самой собой" in e for e in errors))

    def test_03_season_lifecycle(self):
        """Verify season lifecycle state transitions: DRAFT -> ACTIVE -> FINISHED -> ARCHIVED."""
        # 1. Create a draft season
        s_id = database.create_season(name="Зимний Кубок 2026", created_by=999)
        self.assertIsNotNone(s_id)
        season = database.get_season(s_id)
        self.assertEqual(season["status"], "draft")
        self.assertEqual(season["name"], "Зимний Кубок 2026")

        # 2. Activate season
        ok = database.activate_season(s_id)
        self.assertTrue(ok)
        active = database.get_active_season()
        self.assertEqual(active["id"], s_id)
        self.assertEqual(active["status"], "active")

        # 3. Finish season
        ok = database.finish_season(s_id)
        self.assertTrue(ok)
        season = database.get_season(s_id)
        self.assertEqual(season["status"], "finished")
        self.assertIsNotNone(season["finished_at"])

        # 4. Archive season
        ok = database.archive_season(s_id)
        self.assertTrue(ok)
        season = database.get_season(s_id)
        self.assertEqual(season["status"], "archived")

    def test_04_season_and_division_data_isolation(self):
        """Verify strict isolation between seasons and divisions in standings and match queries."""
        s1_id = 1  # Default active
        s2_id = database.create_season(name="Сезон 2", created_by=999)

        # Register users for Division 1 and 2
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO users (telegram_id, username, team_name, division_id) VALUES (201, 'player1', 'Реал', 1)")
            c.execute("INSERT INTO users (telegram_id, username, team_name, division_id) VALUES (202, 'player2', 'Барселона', 1)")
            c.execute("INSERT INTO users (telegram_id, username, team_name, division_id) VALUES (203, 'player3', 'Бавария', 2)")
            c.execute("INSERT INTO users (telegram_id, username, team_name, division_id) VALUES (204, 'player4', 'Арсенал', 2)")

        # Record match in Season 1, Division 1
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, 
                                     player1_score, player2_score, status, division_id, season_id)
                VALUES (1, 201, 202, 'Реал', 'Барселона', 3, 1, 'confirmed', 1, ?)
            """, (s1_id,))

        # Record match in Season 2, Division 1
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, 
                                     player1_score, player2_score, status, division_id, season_id)
                VALUES (1, 201, 202, 'Реал', 'Барселона', 0, 5, 'confirmed', 1, ?)
            """, (s2_id,))

        # Standings for Season 1, Division 1: Реал won 3-1 -> 3 pts
        s1_standings = database.get_standings(division_id=1, season_id=s1_id)
        real_s1 = next(t for t in s1_standings if t["team_name"] == "Реал")
        self.assertEqual(real_s1["points"], 3)
        self.assertEqual(real_s1["goals_scored"], 3)
        self.assertEqual(real_s1["goals_conceded"], 1)

        # Standings for Season 2, Division 1: Барселона won 5-0 -> 3 pts
        s2_standings = database.get_standings(division_id=1, season_id=s2_id)
        real_s2 = next(t for t in s2_standings if t["team_name"] == "Реал")
        self.assertEqual(real_s2["points"], 0)
        self.assertEqual(real_s2["goals_scored"], 0)
        self.assertEqual(real_s2["goals_conceded"], 5)

        # Standings for Division 2: should be empty for both teams
        div2_standings = database.get_standings(division_id=2, season_id=s1_id)
        div2_teams = [t["team_name"] for t in div2_standings]
        self.assertNotIn("Реал", div2_teams)
        self.assertNotIn("Барселона", div2_teams)

    def test_05_historical_preservation_across_seasons(self):
        """Verify that activating a new season preserves previous season fixtures and results."""
        s1_id = 1

        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO users (telegram_id, username, team_name, division_id) VALUES (201, 'p1', 'T1', 1)")
            c.execute("INSERT OR IGNORE INTO users (telegram_id, username, team_name, division_id) VALUES (202, 'p2', 'T2', 1)")
            c.execute("INSERT OR IGNORE INTO users (telegram_id, username, team_name, division_id) VALUES (203, 'p3', 'T3', 1)")
            c.execute("INSERT OR IGNORE INTO users (telegram_id, username, team_name, division_id) VALUES (204, 'p4', 'T4', 1)")

        fixtures_s1 = [(1, 201, 202), (1, 203, 204)]
        database.batch_insert_matches(fixtures_s1, division_id=1, season_id=s1_id)

        # Verify season 1 matches exist
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM matches WHERE season_id = ?", (s1_id,))
            self.assertEqual(c.fetchone()[0], 2)

        # Create and activate Season 2
        s2_id = database.create_season(name="Сезон 2", created_by=999)
        database.activate_season(s2_id)

        # Clear and generate matches for Season 2
        fixtures_s2 = [(1, 201, 203), (1, 202, 204), (2, 201, 204)]
        database.clear_matches_by_division(division_id=1, season_id=s2_id)
        database.batch_insert_matches(fixtures_s2, division_id=1, season_id=s2_id)

        # Verify Season 1 matches were NOT deleted
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM matches WHERE season_id = ?", (s1_id,))
            self.assertEqual(c.fetchone()[0], 2)

            c.execute("SELECT COUNT(*) FROM matches WHERE season_id = ?", (s2_id,))
            self.assertEqual(c.fetchone()[0], 3)

    async def test_06_regeneration_protection_with_played_matches(self):
        """Verify that admin cannot regenerate schedule once matches are confirmed."""
        s_id = 1
        div_id = 1

        # Seed played match with valid user FKs
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO users (telegram_id, username, team_name, division_id) VALUES (10, 'p10', 'T10', 1)")
            c.execute("INSERT OR IGNORE INTO users (telegram_id, username, team_name, division_id) VALUES (20, 'p20', 'T20', 1)")
            c.execute("""
                INSERT INTO matches (round_number, player1_id, player2_id, player1_score, player2_score, status, division_id, season_id)
                VALUES (1, 10, 20, 2, 1, 'confirmed', ?, ?)
            """, (div_id, s_id))

        has_played = database.division_has_played_matches(div_id, season_id=s_id)
        self.assertTrue(has_played)

        # Mock admin calling generation execute
        update = MagicMock()
        context = MagicMock()
        query = MagicMock()
        admin_id = 999
        query.from_user.id = admin_id
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.data = f"admin_gen_exec_{div_id}"
        update.callback_query = query
        update.effective_user = MagicMock(id=admin_id)

        with patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin.is_admin", return_value=True):
            await admin_generate_matches_execute(update, context)

            # Generation must be blocked
            self.assertTrue(query.edit_message_text.called)
            args, kwargs = query.edit_message_text.call_args
            self.assertIn("Генерация заблокирована", args[0])

    def test_07_api_seasons_and_isolation(self):
        """Verify season listings and database helper functions for API consumption."""
        s1 = database.get_season(1)
        self.assertIsNotNone(s1)
        self.assertEqual(s1["name"], "Сезон 2026")

        all_seasons = database.list_seasons()
        self.assertGreaterEqual(len(all_seasons), 1)
        self.assertEqual(all_seasons[0]["id"], 1)


from aiohttp.test_utils import AioHTTPTestCase
import json
import time
import hmac
import hashlib
import urllib.parse
import config
from api.server import create_app


class TestPhase2ApiIntegration(AioHTTPTestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db = "test_phase2_api.db"
        database.DB_PATH = cls.test_db

    async def get_application(self):
        if os.path.exists(self.test_db):
            try:
                os.remove(self.test_db)
            except OSError:
                pass
        database.init_db()
        return create_app()

    def tearDown(self):
        super().tearDown()
        if os.path.exists(self.test_db):
            try:
                os.remove(self.test_db)
            except OSError:
                pass

    def _generate_mock_init_data(self, user_id=12345678, username="test_user"):
        token = config.TOKEN or "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        user_dict = {
            "id": user_id,
            "first_name": "Tester",
            "username": username
        }
        data = {
            "auth_date": str(int(time.time())),
            "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
            "user": json.dumps(user_dict, separators=(",", ":"))
        }
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret_key = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
        hash_val = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        data["hash"] = hash_val
        return urllib.parse.urlencode(data)

    async def test_api_seasons_endpoints(self):
        """Test GET /api/seasons and GET /api/seasons/{id}."""
        headers = {"X-Telegram-Init-Data": self._generate_mock_init_data()}

        # 1. GET /api/seasons
        resp = await self.client.request("GET", "/api/seasons", headers=headers)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("seasons", data)
        self.assertGreaterEqual(len(data["seasons"]), 1)
        self.assertEqual(data["seasons"][0]["name"], "Сезон 2026")

        # 2. GET /api/seasons/1
        resp2 = await self.client.request("GET", "/api/seasons/1", headers=headers)
        self.assertEqual(resp2.status, 200)
        data2 = await resp2.json()
        self.assertEqual(data2["status"], "ok")
        self.assertEqual(data2["season"]["id"], 1)
        self.assertEqual(data2["season"]["status"], "active")

        # 3. GET /api/seasons/999 (not found)
        resp3 = await self.client.request("GET", "/api/seasons/999", headers=headers)
        self.assertEqual(resp3.status, 404)

    async def test_api_tournament_standings_and_results_scoped(self):
        """Test standings and results endpoint division & season query params."""
        headers = {"X-Telegram-Init-Data": self._generate_mock_init_data()}

        # Standings with division_id and season_id
        resp = await self.client.request("GET", "/api/tournaments/1/standings?division_id=1&season_id=1", headers=headers)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("standings", data)

        # Results with division_id and season_id
        resp2 = await self.client.request("GET", "/api/tournaments/1/results?division_id=1&season_id=1", headers=headers)
        self.assertEqual(resp2.status, 200)
        data2 = await resp2.json()
        self.assertEqual(data2["status"], "ok")
        self.assertIn("results", data2)

        # Matches with division_id and season_id
        resp3 = await self.client.request("GET", "/api/matches?division_id=1&season_id=1", headers=headers)
        self.assertEqual(resp3.status, 200)
        data3 = await resp3.json()
        self.assertEqual(data3["status"], "ok")
        self.assertIn("matches", data3)


if __name__ == "__main__":
    unittest.main()

