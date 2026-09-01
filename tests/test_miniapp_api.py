import unittest
import hmac
import hashlib
import urllib.parse
import json
import time
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
import database
import config
from api.server import create_app
from api.auth import validate_telegram_init_data


class TestMiniAppApi(AioHTTPTestCase):
    async def get_application(self):
        database.init_db()
        return create_app()

    def _generate_mock_init_data(self, user_id=12345678, username="test_bettor"):
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

    def test_init_data_validation(self):
        token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        user_dict = {"id": 998877, "first_name": "Test", "username": "tester"}
        data = {
            "auth_date": str(int(time.time())),
            "user": json.dumps(user_dict, separators=(",", ":"))
        }
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret_key = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
        hash_val = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        data["hash"] = hash_val
        valid_query = urllib.parse.urlencode(data)

        # 1. Valid validation
        user = validate_telegram_init_data(valid_query, bot_token=token)
        self.assertIsNotNone(user)
        self.assertEqual(user["id"], 998877)

        # 2. Tampered hash
        tampered_query = valid_query + "bad"
        self.assertIsNone(validate_telegram_init_data(tampered_query, bot_token=token))

    @unittest_run_loop
    async def test_bootstrap_endpoint_unauthorized(self):
        resp = await self.client.request("GET", "/api/bootstrap")
        self.assertEqual(resp.status, 401)

    @unittest_run_loop
    async def test_bootstrap_endpoint_authorized(self):
        init_data = self._generate_mock_init_data(user_id=11223344)
        headers = {"X-Telegram-Init-Data": init_data}
        resp = await self.client.request("GET", "/api/bootstrap", headers=headers)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["user"]["user_id"], 11223344)
        self.assertIn("balance", data["user"])

    @unittest_run_loop
    async def test_leaderboard_endpoint(self):
        resp = await self.client.request("GET", "/api/leaderboard")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("leaders", data)


if __name__ == "__main__":
    unittest.main()
