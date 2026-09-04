"""
services/background_sync.py

Phase 6 Background Jobs for Live Sync, Intelligence, and Notification Delivery.
Uses telegram.ext.JobQueue compatible coroutines:
1. sync_live_provider_job: Safe, idempotent sync with external sports provider.
2. sync_intelligence_cache_job: Pre-computes hot scores and intelligence snapshots.
3. process_notification_queue_job: Dispatches pending notification_events via Telegram bot.
"""

from __future__ import annotations

import logging
from typing import Any

from telegram.error import TelegramError

import database
from services.intelligence_engine import get_match_intelligence
from services.notification_service import mark_notification_sent
from services.sports_provider import get_sports_data_provider

logger = logging.getLogger(__name__)


async def sync_live_provider_job(context: Any) -> None:
    """
    Periodic job to sync live matches from external sports data provider.
    Runs every 30-60 seconds.
    If provider is Null or unconfigured, records status and exits cleanly (ZERO fake data).
    """
    try:
        provider = get_sports_data_provider()
        sync_status = provider.get_sync_status()

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO provider_sync_state (provider, last_sync_at, status, last_error)
                VALUES (?, CURRENT_TIMESTAMP, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    last_sync_at = CURRENT_TIMESTAMP,
                    status = excluded.status,
                    last_error = excluded.last_error
            """, (provider.provider_name, sync_status.get("status", "unknown"), sync_status.get("last_error")))

        if not provider.is_connected:
            logger.debug("Live sync skipped: provider '%s' is not connected.", provider.provider_name)
            return

        # Connected provider ingestion
        live_matches = await provider.get_live_matches()
        logger.info("Live provider synced %d active matches.", len(live_matches))
    except Exception as e:
        logger.error("Error in sync_live_provider_job: %s", e, exc_info=True)


async def sync_intelligence_cache_job(context: Any) -> None:
    """
    Periodic job to refresh intelligence calculations for open and live matches.
    Runs every 5 minutes.
    """
    try:
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id FROM matches
                WHERE status IN ('open', 'live')
                ORDER BY id DESC
                LIMIT 20
            """)
            match_ids = [r["id"] for r in cursor.fetchall()]

        for mid in match_ids:
            try:
                # Precompute intelligence
                get_match_intelligence(mid)
            except Exception as e:
                logger.debug("Failed precomputing intelligence for match %s: %s", mid, e)
    except Exception as e:
        logger.error("Error in sync_intelligence_cache_job: %s", e)


async def process_notification_queue_job(context: Any) -> None:
    """
    Dispatches pending notifications to users via Telegram bot.
    Runs every 10-15 seconds.
    """
    if not hasattr(context, "bot") or context.bot is None:
        return

    try:
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, user_id, event_type, title, body, link
                FROM notification_events
                WHERE status = 'pending'
                ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'normal' THEN 3 ELSE 4 END, id ASC
                LIMIT 25
            """)
            pending = [dict(r) for r in cursor.fetchall()]

        for item in pending:
            ev_id = item["id"]
            uid = item["user_id"]
            text = f"<b>{item['title']}</b>\n{item.get('body') or ''}"
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=text,
                    parse_mode="HTML"
                )
                mark_notification_sent(ev_id)
            except TelegramError as te:
                logger.warning("Failed to send notification %s to user %s: %s", ev_id, uid, te)
                # Mark failed or leave pending with attempt limit
                with database.transaction() as conn:
                    conn.cursor().execute("UPDATE notification_events SET status = 'failed' WHERE id = ?", (ev_id,))
            except Exception as e:
                logger.warning("Unexpected error sending notification %s: %s", ev_id, e)
    except Exception as e:
        logger.error("Error in process_notification_queue_job: %s", e)
