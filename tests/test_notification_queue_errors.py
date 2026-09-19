"""The notification queue keeps transient failures pending and drops permanent ones."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest, Forbidden, RetryAfter, TimedOut

import database
from services.background_sync import process_notification_queue_job

REAL, PRE_REGISTERED = 7301, -7301


@pytest.fixture(autouse=True)
def seeded(monkeypatch):
    monkeypatch.setattr("config.SMART_NOTIFICATIONS_ENABLED", True, raising=False)
    with database.transaction() as conn:
        conn.execute("DELETE FROM notification_events")
        for uid in (REAL, PRE_REGISTERED):
            conn.execute("INSERT OR IGNORE INTO users (telegram_id, username) VALUES (?, ?)", (uid, f"u{uid}"))
            conn.execute(
                "INSERT INTO notification_events (user_id, event_type, source_event_id, title, status) "
                "VALUES (?, 'BET_SETTLED', ?, 'Ставка рассчитана', 'pending')",
                (uid, f"bet_{uid}"),
            )


def _run(side_effect=None):
    ctx = MagicMock()
    ctx.bot.send_message = AsyncMock(side_effect=side_effect)
    asyncio.run(process_notification_queue_job(ctx))
    return ctx.bot.send_message


def _status(uid):
    with database.transaction() as conn:
        return conn.execute("SELECT status FROM notification_events WHERE user_id = ?", (uid,)).fetchone()[0]


def test_pre_registered_id_is_dropped_without_an_api_call():
    send = _run()
    assert [c.kwargs["chat_id"] for c in send.call_args_list] == [REAL]
    assert _status(PRE_REGISTERED) == "failed"


@pytest.mark.parametrize("error", [TimedOut(), RetryAfter(5)])
def test_transient_errors_keep_the_notice_pending(error):
    _run(error)
    assert _status(REAL) == "pending"


@pytest.mark.parametrize("error", [BadRequest("Chat not found"), Forbidden("bot was blocked by the user")])
def test_permanent_errors_mark_the_notice_failed(error):
    _run(error)
    assert _status(REAL) == "failed"
