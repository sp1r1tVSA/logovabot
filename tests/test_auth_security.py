"""
tests/test_auth_security.py

Безопасность Telegram-авторизации Mini App.

Инварианты:
1. Подпись initData проверяется HMAC-SHA256 по токену бота; подделка отклоняется.
2. auth_date обязателен, не из будущего и не старше 24 часов (защита от replay).
3. Личность берётся только из валидированного initData — user_id из query или
   тела запроса не даёт доступа.
4. Fail-closed: без валидного initData сервер отдаёт 401 и не трогает бизнес-логику.
5. Dev-заглушка mock_admin_ мертва, пока ALLOW_DEV_AUTH_BYPASS не выставлен явно.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import hashlib
import hmac
import json
import time
import urllib.parse

from aiohttp.test_utils import AioHTTPTestCase

import config
import database
from api.auth import get_authenticated_user, validate_telegram_init_data
from api.server import create_app

TEST_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def build_init_data(user_id: int = 55501, auth_date: int | None = None,
                    token: str = TEST_TOKEN, include_hash: bool = True,
                    include_auth_date: bool = True) -> str:
    """Собрать корректно подписанную строку initData (или намеренно битую)."""
    user_str = json.dumps(
        {"id": user_id, "first_name": "Tester", "username": "sec_tester"},
        separators=(",", ":")
    )
    data = {"user": user_str}
    if include_auth_date:
        data["auth_date"] = str(auth_date if auth_date is not None else int(time.time()))

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if include_hash:
        data["hash"] = calc_hash
    return urllib.parse.urlencode(data)


class TestInitDataValidation(AioHTTPTestCase):
    """Криптографическая часть: подпись и свежесть."""

    async def get_application(self):
        database.init_db()
        return create_app()

    def test_valid_signature_accepted(self):
        user = validate_telegram_init_data(build_init_data(user_id=777001), bot_token=TEST_TOKEN)
        self.assertIsNotNone(user)
        self.assertEqual(user["id"], 777001)

    def test_forged_hash_rejected(self):
        init_data = build_init_data()
        forged = init_data.replace("hash=", "hash=0")
        self.assertIsNone(validate_telegram_init_data(forged, bot_token=TEST_TOKEN))

    def test_signature_from_wrong_token_rejected(self):
        """initData, подписанная чужим ботом, не должна открывать наш API."""
        foreign = build_init_data(token="999999:WRONG-TOKEN-aaaaaaaaaaaaaaaaaaaaaa")
        self.assertIsNone(validate_telegram_init_data(foreign, bot_token=TEST_TOKEN))

    def test_tampered_user_id_rejected(self):
        """Подмена user_id внутри подписанных данных ломает hash."""
        init_data = build_init_data(user_id=55501)
        tampered = init_data.replace("55501", "55502")
        self.assertIsNone(validate_telegram_init_data(tampered, bot_token=TEST_TOKEN))

    def test_missing_hash_rejected(self):
        no_hash = build_init_data(include_hash=False)
        self.assertIsNone(validate_telegram_init_data(no_hash, bot_token=TEST_TOKEN))

    def test_missing_auth_date_rejected(self):
        no_date = build_init_data(include_auth_date=False)
        self.assertIsNone(validate_telegram_init_data(no_date, bot_token=TEST_TOKEN))

    def test_expired_auth_date_rejected(self):
        """Replay: перехваченная вчерашняя initData не переиспользуется."""
        stale = build_init_data(auth_date=int(time.time()) - 86_401)
        self.assertIsNone(validate_telegram_init_data(stale, bot_token=TEST_TOKEN))

    def test_future_auth_date_rejected(self):
        future = build_init_data(auth_date=int(time.time()) + 4000)
        self.assertIsNone(validate_telegram_init_data(future, bot_token=TEST_TOKEN))

    def test_fresh_auth_date_within_window_accepted(self):
        recent = build_init_data(auth_date=int(time.time()) - 3600)
        self.assertIsNotNone(validate_telegram_init_data(recent, bot_token=TEST_TOKEN))

    def test_empty_and_garbage_input_rejected(self):
        for payload in ("", "   ", "not-even-query-string", "hash=abc", "user=%7B%7D&hash=x"):
            self.assertIsNone(validate_telegram_init_data(payload, bot_token=TEST_TOKEN))


class TestDevBypassIsolation(AioHTTPTestCase):
    """Тестовая заглушка не должна быть доступна в проде."""

    async def get_application(self):
        database.init_db()
        return create_app()

    def setUp(self):
        super().setUp()
        self._orig_flag = os.environ.get("ALLOW_DEV_AUTH_BYPASS")
        os.environ.pop("ALLOW_DEV_AUTH_BYPASS", None)

    def tearDown(self):
        if self._orig_flag is None:
            os.environ.pop("ALLOW_DEV_AUTH_BYPASS", None)
        else:
            os.environ["ALLOW_DEV_AUTH_BYPASS"] = self._orig_flag
        super().tearDown()

    def test_mock_admin_rejected_without_flag(self):
        admin_id = config.ADMIN_IDS[0] if config.ADMIN_IDS else 1
        self.assertIsNone(get_authenticated_user(f"mock_admin_{admin_id}"))

    def test_mock_admin_rejected_for_non_admin_even_with_flag(self):
        """Даже с включённым флагом заглушка не производит обычных пользователей."""
        os.environ["ALLOW_DEV_AUTH_BYPASS"] = "true"
        self.assertIsNone(get_authenticated_user("mock_admin_424242"))

    def test_garbage_mock_payload_rejected_with_flag(self):
        os.environ["ALLOW_DEV_AUTH_BYPASS"] = "true"
        self.assertIsNone(get_authenticated_user("mock_admin_not_a_number"))


class TestApiFailClosed(AioHTTPTestCase):
    """HTTP-контракт: без подписи — 401, без утечки бизнес-логики."""

    async def get_application(self):
        database.init_db()
        return create_app()

    def setUp(self):
        super().setUp()
        self._orig_token = config.TOKEN
        config.TOKEN = TEST_TOKEN

    def tearDown(self):
        config.TOKEN = self._orig_token
        super().tearDown()

    async def test_missing_init_data_returns_401(self):
        resp = await self.client.request("GET", "/api/wallet")
        self.assertEqual(resp.status, 401)
        body = await resp.json()
        self.assertEqual(body.get("error"), "unauthorized")

    async def test_forged_init_data_returns_401(self):
        forged = build_init_data().replace("hash=", "hash=deadbeef")
        resp = await self.client.request(
            "GET", "/api/wallet", headers={"X-Telegram-Init-Data": forged}
        )
        self.assertEqual(resp.status, 401)

    async def test_user_id_in_query_does_not_authenticate(self):
        """Spoofing: user_id в query-параметрах не заменяет подпись."""
        resp = await self.client.request("GET", "/api/wallet?user_id=1")
        self.assertEqual(resp.status, 401)

    async def test_user_id_in_body_does_not_authenticate(self):
        """Spoofing: user_id в теле POST не заменяет подпись, ставка не ставится."""
        resp = await self.client.request(
            "POST", "/api/predictions",
            json={"user_id": 1, "amount": 1000, "selections": [{"match_id": 1, "outcome": "p1"}]}
        )
        self.assertEqual(resp.status, 401)

    async def test_expired_init_data_returns_401_on_http(self):
        stale = build_init_data(auth_date=int(time.time()) - 90_000)
        resp = await self.client.request(
            "GET", "/api/wallet", headers={"X-Telegram-Init-Data": stale}
        )
        self.assertEqual(resp.status, 401)

    async def test_valid_init_data_passes_authentication(self):
        """Валидная подпись проходит аутентификацию (403 lockdown допустим, 401 — нет)."""
        resp = await self.client.request(
            "GET", "/api/wallet",
            headers={"X-Telegram-Init-Data": build_init_data(user_id=778101)}
        )
        self.assertNotEqual(resp.status, 401)
