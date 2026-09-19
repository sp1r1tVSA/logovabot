"""safe_send_notification must skip pre-registered coaches (negative temp ids)."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from handlers.cabinet import safe_send_notification


@pytest.mark.parametrize("chat_id", [-57, 0, None])
def test_skips_pre_registered_ids(chat_id):
    bot = AsyncMock()
    assert asyncio.run(safe_send_notification(bot, chat_id, "hi")) is False
    bot.send_message.assert_not_called()


def test_sends_to_real_user():
    bot = AsyncMock()
    assert asyncio.run(safe_send_notification(bot, 12345, "hi")) is True
    bot.send_message.assert_awaited_once()
