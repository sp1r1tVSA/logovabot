"""
tests/test_lab_api.py

Integration tests for 🧪 ЛАБОРАТОРИЯ (Logovo Lab) REST API & UI endpoints.
Verifies all /api/lab/* routes:
- /api/lab/status
- /api/lab/season/create
- /api/lab/season/reset
- /api/lab/teams
- /api/lab/matches & /api/lab/matches/{id}
- /api/lab/matches/{id}/prepare
- /api/lab/matches/{id}/status
- /api/lab/matches/{id}/live-event
- /api/lab/matches/{id}/result
- /api/lab/scenarios & /api/lab/scenarios/{id}/apply
- /api/lab/step-tracker
- /api/lab/bets
- /api/lab/financial
- /api/lab/season/control & /api/lab/season/rounds/{round}/action
- /api/lab/settings/user
- /lab (HTML dashboard)
"""

import unittest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

import database
from api.server import create_app
from services import lab_service


class TestLabApi(AioHTTPTestCase):
    async def get_application(self):
        database.init_db()
        return create_app()

    def setUp(self):
        super().setUp()
        self.test_user_id = 999999999
        lab_service.set_active_test_user_id(self.test_user_id)
        lab_service.reset_test_lab(self.test_user_id)

    def tearDown(self):
        lab_service.reset_test_lab(self.test_user_id)
        super().tearDown()

    @unittest_run_loop
    async def test_01_lab_page_and_status(self):
        """Verify GET /lab serves HTML and /api/lab/status returns dashboard state."""
        # 1. UI route
        resp = await self.client.request("GET", "/lab")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("LOGOVO LAB", text)
        self.assertIn("Sandbox", text)

        # 2. Status API
        resp = await self.client.request("GET", "/api/lab/status")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["test_user"]["id"], self.test_user_id)

    @unittest_run_loop
    async def test_02_season_create_teams_matches(self):
        """Verify season creation and listing teams/matches via API."""
        # Create season
        resp = await self.client.request(
            "POST",
            "/api/lab/season/create",
            json={
                "season_name": "LOGOVO TEST SEASON 2026",
                "division_name": "LOGOVO TEST LEAGUE",
                "teams_count": 16,
                "rounds_count": 30,
                "seed": 20260905,
            },
        )
        self.assertEqual(resp.status, 200)
        c_data = await resp.json()
        self.assertEqual(c_data["status"], "ok")
        self.assertEqual(c_data["teams_count"], 16)
        self.assertEqual(c_data["rounds_count"], 30)
        self.assertEqual(c_data["matches_count"], 240)

        # Get teams
        resp = await self.client.request("GET", "/api/lab/teams")
        self.assertEqual(resp.status, 200)
        t_data = await resp.json()
        self.assertEqual(len(t_data["standings"]), 16)

        # Get matches
        resp = await self.client.request("GET", "/api/lab/matches?limit=10")
        self.assertEqual(resp.status, 200)
        m_data = await resp.json()
        self.assertEqual(m_data["count"], 10)

        # Get match detail
        match_id = m_data["matches"][0]["id"]
        resp = await self.client.request("GET", f"/api/lab/matches/{match_id}")
        self.assertEqual(resp.status, 200)
        md_data = await resp.json()
        self.assertEqual(md_data["match"]["id"], match_id)
        self.assertIn("markets", md_data["match"])

    @unittest_run_loop
    async def test_03_scenarios_and_prepare_match(self):
        """Verify scenarios listing, one-click apply, and match preparation API."""
        lab_service.create_test_season()

        # List scenarios
        resp = await self.client.request("GET", "/api/lab/scenarios")
        self.assertEqual(resp.status, 200)
        sc_list = await resp.json()
        self.assertGreaterEqual(len(sc_list["scenarios"]), 8)

        # Apply HOME WIN scenario
        resp = await self.client.request("POST", "/api/lab/scenarios/home_win/apply")
        self.assertEqual(resp.status, 200)
        app_res = await resp.json()
        self.assertEqual(app_res["status"], "ok")
        match_id = app_res["preparation"]["match_id"]

        # Prepare match manually with custom odds
        resp = await self.client.request(
            "POST",
            f"/api/lab/matches/{match_id}/prepare",
            json={"custom_odds": 1.95, "custom_score": [3, 0]},
        )
        self.assertEqual(resp.status, 200)
        prep_res = await resp.json()
        self.assertEqual(prep_res["status"], "ok")
        self.assertEqual(prep_res["expected_score"], "3:0")

    @unittest_run_loop
    async def test_04_live_controls_and_result_settle(self):
        """Verify lifecycle transition, live events, match score and settlement."""
        lab_service.create_test_season()
        m_list = lab_service.get_lab_matches(limit=1)
        m_id = m_list[0]["id"]

        # Transition to LIVE
        resp = await self.client.request(
            "POST",
            f"/api/lab/matches/{m_id}/status",
            json={"status": "live"},
        )
        self.assertEqual(resp.status, 200)
        st_data = await resp.json()
        self.assertEqual(st_data["new_status"], "live")

        # Ingest goal
        resp = await self.client.request(
            "POST",
            f"/api/lab/matches/{m_id}/live-event",
            json={"action": "goal", "side": "home", "minute": 18},
        )
        self.assertEqual(resp.status, 200)
        ev_data = await resp.json()
        self.assertEqual(ev_data["current_score"], "1:0")

        # Set result and settle
        resp = await self.client.request(
            "POST",
            f"/api/lab/matches/{m_id}/result",
            json={"score1": 2, "score2": 0, "settle": True},
        )
        self.assertEqual(resp.status, 200)
        res_data = await resp.json()
        self.assertEqual(res_data["score"], "2:0")
        self.assertTrue(res_data["settled"])

    @unittest_run_loop
    async def test_05_financial_and_step_tracker(self):
        """Verify step tracker and financial reconciliation API."""
        lab_service.create_test_season()

        # Step tracker
        resp = await self.client.request("GET", "/api/lab/step-tracker")
        self.assertEqual(resp.status, 200)
        track_data = await resp.json()
        self.assertEqual(track_data["status"], "ok")
        self.assertTrue(track_data["steps"][0]["completed"])

        # Financial reconciliation
        resp = await self.client.request("GET", "/api/lab/financial")
        self.assertEqual(resp.status, 200)
        fin_data = await resp.json()
        self.assertEqual(fin_data["status"], "ok")
        self.assertEqual(fin_data["badge"], "🟢 BALANCE OK")
        self.assertEqual(fin_data["actual_balance"], 100000)

        # Settings switch user
        resp = await self.client.request(
            "POST",
            "/api/lab/settings/user",
            json={"user_id": 999999998, "balance": 50000},
        )
        self.assertEqual(resp.status, 200)
        set_data = await resp.json()
        self.assertEqual(set_data["test_user_id"], 999999998)
        self.assertEqual(set_data["balance"], 50000)

    @unittest_run_loop
    async def test_06_season_control_and_reset(self):
        """Verify season rounds management and full reset via API."""
        lab_service.create_test_season()

        # Season control
        resp = await self.client.request("GET", "/api/lab/season/control")
        self.assertEqual(resp.status, 200)
        ctrl_data = await resp.json()
        self.assertEqual(ctrl_data["total_rounds"], 30)

        # Round action open
        resp = await self.client.request(
            "POST",
            "/api/lab/season/rounds/5/action",
            json={"action": "open"},
        )
        self.assertEqual(resp.status, 200)

        # Reset season
        resp = await self.client.request("POST", "/api/lab/season/reset")
        self.assertEqual(resp.status, 200)
        reset_data = await resp.json()
        self.assertEqual(reset_data["status"], "ok")
        self.assertEqual(reset_data["test_user_balance"], 100000)


if __name__ == "__main__":
    unittest.main()
