import json
import logging
import os
import re
import sqlite3
import time
import unicodedata
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from datetime import datetime
from itertools import zip_longest
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

try:
    import cv2
    import numpy as np
    import pytesseract
    from pytesseract import Output

    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False

try:
    from selenium import webdriver
    from selenium.common.exceptions import TimeoutException as SeleniumTimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.options import Options as ChromeOptions

    SELENIUM_AVAILABLE = True
except Exception:
    SELENIUM_AVAILABLE = False
    SeleniumTimeoutException = TimeoutError


class LeagueRepositorySQLite:
    def __init__(self, conn, cursor):
        self.conn = conn
        self.cursor = cursor

    def create_tables(self):
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS league_debt_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                round_label TEXT,
                debtor_username TEXT NOT NULL,
                opponent_username TEXT,
                raw_line TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS league_reminder_settings (
                chat_id INTEGER PRIMARY KEY,
                enabled INTEGER DEFAULT 0,
                timezone TEXT DEFAULT 'Europe/Moscow',
                threshold INTEGER DEFAULT 2,
                hourly_enabled INTEGER DEFAULT 0,
                hourly_text TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        try:
            self.cursor.execute(
                "ALTER TABLE league_reminder_settings ADD COLUMN hourly_enabled INTEGER DEFAULT 0"
            )
        except Exception:
            pass

        try:
            self.cursor.execute("ALTER TABLE league_reminder_settings ADD COLUMN hourly_text TEXT")
        except Exception:
            pass

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS league_reminder_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                slot_key TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, slot_key)
            )
            """
        )

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS league_team_map (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                team_name_norm TEXT NOT NULL,
                team_name_raw TEXT NOT NULL,
                telegram_username TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, team_name_norm)
            )
            """
        )

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS league_challenge_sources (
                chat_id INTEGER PRIMARY KEY,
                stage_url TEXT NOT NULL,
                max_round INTEGER NOT NULL,
                enabled INTEGER DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS league_ocr_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                ocr_id INTEGER NOT NULL,
                source_message_id INTEGER,
                author_user_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending_admin_review',
                payload_json TEXT NOT NULL,
                warnings_json TEXT,
                last_editor_user_id INTEGER,
                reviewed_by_user_id INTEGER,
                review_note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, ocr_id)
            )
            """
        )

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS league_team_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                team_name_norm TEXT NOT NULL,
                team_name_raw TEXT NOT NULL,
                player_name_norm TEXT NOT NULL,
                player_name_raw TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, team_name_norm, player_name_norm)
            )
            """
        )

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS league_ocr_applied (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                ocr_id INTEGER NOT NULL,
                match_url TEXT,
                status TEXT NOT NULL,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, ocr_id)
            )
            """
        )

        self.conn.commit()

    def replace_league_debts(self, chat_id: int, entries: List[Dict]):
        self.cursor.execute("DELETE FROM league_debt_entries WHERE chat_id = ?", (chat_id,))
        for e in entries:
            self.cursor.execute(
                """
                INSERT INTO league_debt_entries (chat_id, round_label, debtor_username, opponent_username, raw_line)
                VALUES (?, ?, ?, ?, ?)
                """,
                (chat_id, e.get("round_label"), e.get("debtor_username"), e.get("opponent_username"), e.get("raw_line")),
            )
        self.conn.commit()

    def get_league_debt_summary(self, chat_id: int) -> List[Dict]:
        self.cursor.execute(
            """
            SELECT debtor_username, COUNT(*) AS debts_count
            FROM league_debt_entries
            WHERE chat_id = ?
            GROUP BY debtor_username
            ORDER BY debts_count DESC, debtor_username ASC
            """,
            (chat_id,),
        )
        return [{"debtor_username": r[0], "debts_count": r[1]} for r in self.cursor.fetchall()]

    def get_league_debts_count(self, chat_id: int) -> int:
        self.cursor.execute("SELECT COUNT(*) FROM league_debt_entries WHERE chat_id = ?", (chat_id,))
        row = self.cursor.fetchone()
        return row[0] if row else 0

    def get_league_debts_by_round(self, chat_id: int) -> Dict[str, List[Dict]]:
        self.cursor.execute(
            """
            SELECT round_label, debtor_username, opponent_username, raw_line
            FROM league_debt_entries
            WHERE chat_id = ?
            ORDER BY id ASC
            """,
            (chat_id,),
        )
        result: Dict[str, List[Dict]] = {}
        for row in self.cursor.fetchall():
            round_label = row[0] or "Без тура"
            result.setdefault(round_label, []).append(
                {
                    "debtor_username": row[1],
                    "opponent_username": row[2],
                    "raw_line": row[3],
                }
            )
        return result

    def set_league_reminder_enabled(self, chat_id: int, enabled: bool):
        self.cursor.execute(
            """
            INSERT INTO league_reminder_settings (chat_id, enabled, timezone, threshold, hourly_enabled, hourly_text, updated_at)
            VALUES (?, ?, 'Europe/Moscow', 2, 0, NULL, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET enabled = excluded.enabled, updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, 1 if enabled else 0),
        )
        self.conn.commit()

    def set_league_hourly_reminder(self, chat_id: int, enabled: bool, hourly_text: Optional[str]):
        self.cursor.execute(
            """
            INSERT INTO league_reminder_settings (chat_id, enabled, timezone, threshold, hourly_enabled, hourly_text, updated_at)
            VALUES (?, 0, 'Europe/Moscow', 2, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                hourly_enabled = excluded.hourly_enabled,
                hourly_text = excluded.hourly_text,
                updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, 1 if enabled else 0, hourly_text),
        )
        self.conn.commit()

    def get_league_reminder_settings(self, chat_id: int) -> Dict:
        self.cursor.execute(
            """
            SELECT chat_id, enabled, timezone, threshold, hourly_enabled, hourly_text
            FROM league_reminder_settings
            WHERE chat_id = ?
            """,
            (chat_id,),
        )
        row = self.cursor.fetchone()
        if not row:
            return {
                "chat_id": chat_id,
                "enabled": 0,
                "timezone": "Europe/Moscow",
                "threshold": 2,
                "hourly_enabled": 0,
                "hourly_text": None,
            }
        return {
            "chat_id": row[0],
            "enabled": row[1],
            "timezone": row[2],
            "threshold": row[3],
            "hourly_enabled": row[4],
            "hourly_text": row[5],
        }

    def get_enabled_league_reminder_chats(self) -> List[Dict]:
        self.cursor.execute(
            """
            SELECT chat_id, enabled, timezone, threshold, hourly_enabled, hourly_text
            FROM league_reminder_settings
            WHERE enabled = 1 OR hourly_enabled = 1
            """
        )
        return [
            {
                "chat_id": r[0],
                "enabled": r[1],
                "timezone": r[2],
                "threshold": r[3],
                "hourly_enabled": r[4],
                "hourly_text": r[5],
            }
            for r in self.cursor.fetchall()
        ]

    def try_mark_league_reminder_run(self, chat_id: int, slot_key: str) -> bool:
        self.cursor.execute(
            "INSERT OR IGNORE INTO league_reminder_runs (chat_id, slot_key) VALUES (?, ?)",
            (chat_id, slot_key),
        )
        self.conn.commit()
        return self.cursor.rowcount > 0

    def replace_league_team_map(self, chat_id: int, mappings: List[Dict]):
        self.cursor.execute("DELETE FROM league_team_map WHERE chat_id = ?", (chat_id,))
        for item in mappings:
            self.cursor.execute(
                """
                INSERT INTO league_team_map (chat_id, team_name_norm, team_name_raw, telegram_username)
                VALUES (?, ?, ?, ?)
                """,
                (chat_id, item["team_name_norm"], item["team_name_raw"], item["telegram_username"]),
            )
        self.conn.commit()

    def clear_league_team_map(self, chat_id: int):
        self.cursor.execute("DELETE FROM league_team_map WHERE chat_id = ?", (chat_id,))
        self.conn.commit()

    def get_league_team_map(self, chat_id: int) -> List[Dict]:
        self.cursor.execute(
            """
            SELECT team_name_norm, team_name_raw, telegram_username
            FROM league_team_map
            WHERE chat_id = ?
            ORDER BY team_name_raw ASC
            """,
            (chat_id,),
        )
        return [
            {"team_name_norm": r[0], "team_name_raw": r[1], "telegram_username": r[2]}
            for r in self.cursor.fetchall()
        ]

    def replace_league_team_players(self, chat_id: int, rows: List[Dict]):
        self.cursor.execute("DELETE FROM league_team_players WHERE chat_id = ?", (chat_id,))
        for row in rows:
            self.cursor.execute(
                """
                INSERT INTO league_team_players (
                    chat_id, team_name_norm, team_name_raw, player_name_norm, player_name_raw
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    row["team_name_norm"],
                    row["team_name_raw"],
                    row["player_name_norm"],
                    row["player_name_raw"],
                ),
            )
        self.conn.commit()

    def get_league_team_players(self, chat_id: int, team_name_norm: str) -> List[Dict]:
        self.cursor.execute(
            """
            SELECT player_name_norm, player_name_raw
            FROM league_team_players
            WHERE chat_id = ? AND team_name_norm = ?
            ORDER BY player_name_raw ASC
            """,
            (chat_id, team_name_norm),
        )
        return [{"player_name_norm": r[0], "player_name_raw": r[1]} for r in self.cursor.fetchall()]

    def get_ocr_applied(self, chat_id: int, ocr_id: int) -> Optional[Dict]:
        self.cursor.execute(
            """
            SELECT chat_id, ocr_id, match_url, status, message, created_at, updated_at
            FROM league_ocr_applied
            WHERE chat_id = ? AND ocr_id = ?
            """,
            (chat_id, ocr_id),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        return {
            "chat_id": row[0],
            "ocr_id": row[1],
            "match_url": row[2],
            "status": row[3],
            "message": row[4],
            "created_at": row[5],
            "updated_at": row[6],
        }

    def upsert_ocr_applied(self, chat_id: int, ocr_id: int, match_url: str, status: str, message: str):
        self.cursor.execute(
            """
            INSERT INTO league_ocr_applied (chat_id, ocr_id, match_url, status, message, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id, ocr_id) DO UPDATE SET
                match_url = excluded.match_url,
                status = excluded.status,
                message = excluded.message,
                updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, ocr_id, match_url, status, message),
        )
        self.conn.commit()

    def set_league_challenge_source(self, chat_id: int, stage_url: str, max_round: int):
        self.cursor.execute(
            """
            INSERT INTO league_challenge_sources (chat_id, stage_url, max_round, enabled, updated_at)
            VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                stage_url = excluded.stage_url,
                max_round = excluded.max_round,
                enabled = 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, stage_url, max_round),
        )
        self.conn.commit()

    def get_league_challenge_source(self, chat_id: int) -> Optional[Dict]:
        row = None
        try:
            self.cursor.execute(
                "SELECT chat_id, stage_url, max_round, enabled FROM league_challenge_sources WHERE chat_id = ?",
                (chat_id,),
            )
            row = self.cursor.fetchone()
        except Exception:
            try:
                self.cursor.execute(
                    "SELECT chat_id, url, max_round, enabled FROM league_challenge_sources WHERE chat_id = ?",
                    (chat_id,),
                )
                row = self.cursor.fetchone()
            except Exception:
                return None
        if not row:
            return None
        return {"chat_id": row[0], "stage_url": row[1], "max_round": row[2], "enabled": row[3]}

    def disable_league_challenge_source(self, chat_id: int):
        self.cursor.execute(
            "UPDATE league_challenge_sources SET enabled = 0, updated_at = CURRENT_TIMESTAMP WHERE chat_id = ?",
            (chat_id,),
        )
        self.conn.commit()

    def get_next_ocr_id(self, chat_id: int) -> int:
        self.cursor.execute("SELECT COALESCE(MAX(ocr_id), 0) + 1 FROM league_ocr_drafts WHERE chat_id = ?", (chat_id,))
        row = self.cursor.fetchone()
        return int(row[0] if row else 1)

    def create_ocr_draft(
        self,
        chat_id: int,
        source_message_id: int,
        author_user_id: int,
        payload: Dict,
        warnings: List[str],
    ) -> int:
        ocr_id = self.get_next_ocr_id(chat_id)
        self.cursor.execute(
            """
            INSERT INTO league_ocr_drafts (
                chat_id, ocr_id, source_message_id, author_user_id, status,
                payload_json, warnings_json, last_editor_user_id, updated_at
            )
            VALUES (?, ?, ?, ?, 'pending_admin_review', ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                chat_id,
                ocr_id,
                source_message_id,
                author_user_id,
                json.dumps(payload, ensure_ascii=False),
                json.dumps(warnings, ensure_ascii=False),
                author_user_id,
            ),
        )
        self.conn.commit()
        return ocr_id

    def get_ocr_draft(self, chat_id: int, ocr_id: int) -> Optional[Dict]:
        self.cursor.execute(
            """
            SELECT chat_id, ocr_id, source_message_id, author_user_id, status,
                   payload_json, warnings_json, last_editor_user_id,
                   reviewed_by_user_id, review_note, created_at, updated_at
            FROM league_ocr_drafts
            WHERE chat_id = ? AND ocr_id = ?
            """,
            (chat_id, ocr_id),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        return {
            "chat_id": row[0],
            "ocr_id": row[1],
            "source_message_id": row[2],
            "author_user_id": row[3],
            "status": row[4],
            "payload": json.loads(row[5] or "{}"),
            "warnings": json.loads(row[6] or "[]"),
            "last_editor_user_id": row[7],
            "reviewed_by_user_id": row[8],
            "review_note": row[9],
            "created_at": row[10],
            "updated_at": row[11],
        }

    def update_ocr_draft_payload(self, chat_id: int, ocr_id: int, payload: Dict, warnings: List[str], editor_user_id: int) -> bool:
        self.cursor.execute(
            """
            UPDATE league_ocr_drafts
            SET payload_json = ?,
                warnings_json = ?,
                status = 'pending_admin_review',
                last_editor_user_id = ?,
                reviewed_by_user_id = NULL,
                review_note = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE chat_id = ? AND ocr_id = ?
            """,
            (json.dumps(payload, ensure_ascii=False), json.dumps(warnings, ensure_ascii=False), editor_user_id, chat_id, ocr_id),
        )
        self.conn.commit()
        return self.cursor.rowcount > 0

    def approve_ocr_draft(self, chat_id: int, ocr_id: int, reviewer_user_id: int) -> bool:
        self.cursor.execute(
            """
            UPDATE league_ocr_drafts
            SET status = 'approved',
                reviewed_by_user_id = ?,
                review_note = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE chat_id = ? AND ocr_id = ? AND status = 'pending_admin_review'
            """,
            (reviewer_user_id, chat_id, ocr_id),
        )
        self.conn.commit()
        return self.cursor.rowcount > 0

    def reject_ocr_draft(self, chat_id: int, ocr_id: int, reviewer_user_id: int, note: str) -> bool:
        self.cursor.execute(
            """
            UPDATE league_ocr_drafts
            SET status = 'rejected',
                reviewed_by_user_id = ?,
                review_note = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE chat_id = ? AND ocr_id = ?
            """,
            (reviewer_user_id, note, chat_id, ocr_id),
        )
        self.conn.commit()
        return self.cursor.rowcount > 0


class LeagueRepositoryPostgres(LeagueRepositorySQLite):
    def create_tables(self):
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS league_debt_entries (
                id BIGSERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                round_label TEXT,
                debtor_username TEXT NOT NULL,
                opponent_username TEXT,
                raw_line TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS league_reminder_settings (
                chat_id BIGINT PRIMARY KEY,
                enabled INTEGER DEFAULT 0,
                timezone TEXT DEFAULT 'Europe/Moscow',
                threshold INTEGER DEFAULT 2,
                hourly_enabled INTEGER DEFAULT 0,
                hourly_text TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        self.cursor.execute(
            """
            ALTER TABLE league_reminder_settings
            ADD COLUMN IF NOT EXISTS enabled INTEGER DEFAULT 0
            """
        )
        self.cursor.execute(
            """
            ALTER TABLE league_reminder_settings
            ADD COLUMN IF NOT EXISTS timezone TEXT DEFAULT 'Europe/Moscow'
            """
        )
        self.cursor.execute(
            """
            ALTER TABLE league_reminder_settings
            ADD COLUMN IF NOT EXISTS threshold INTEGER DEFAULT 2
            """
        )
        self.cursor.execute(
            """
            ALTER TABLE league_reminder_settings
            ADD COLUMN IF NOT EXISTS hourly_enabled INTEGER DEFAULT 0
            """
        )
        self.cursor.execute(
            """
            ALTER TABLE league_reminder_settings
            ADD COLUMN IF NOT EXISTS hourly_text TEXT
            """
        )

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS league_reminder_runs (
                id BIGSERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                slot_key TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, slot_key)
            )
            """
        )

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS league_team_map (
                id BIGSERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                team_name_norm TEXT NOT NULL,
                team_name_raw TEXT NOT NULL,
                telegram_username TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, team_name_norm)
            )
            """
        )
        # Backward-compatible migration for pre-existing schemas.
        self.cursor.execute("ALTER TABLE league_team_map ADD COLUMN IF NOT EXISTS chat_id BIGINT")
        self.cursor.execute("ALTER TABLE league_team_map ADD COLUMN IF NOT EXISTS team_name_norm TEXT")
        self.cursor.execute("ALTER TABLE league_team_map ADD COLUMN IF NOT EXISTS team_name_raw TEXT")
        self.cursor.execute("ALTER TABLE league_team_map ADD COLUMN IF NOT EXISTS telegram_username TEXT")
        self.cursor.execute("ALTER TABLE league_team_map ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS league_challenge_sources (
                chat_id BIGINT PRIMARY KEY,
                stage_url TEXT NOT NULL,
                max_round INTEGER NOT NULL,
                enabled INTEGER DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.cursor.execute("ALTER TABLE league_challenge_sources ADD COLUMN IF NOT EXISTS stage_url TEXT")
        self.cursor.execute("ALTER TABLE league_challenge_sources ADD COLUMN IF NOT EXISTS max_round INTEGER")
        self.cursor.execute("ALTER TABLE league_challenge_sources ADD COLUMN IF NOT EXISTS enabled INTEGER DEFAULT 1")
        self.cursor.execute(
            "ALTER TABLE league_challenge_sources ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        )
        self.cursor.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'league_challenge_sources' AND column_name = 'url'
            """
        )
        if self.cursor.fetchone():
            self.cursor.execute(
                """
                UPDATE league_challenge_sources
                SET stage_url = COALESCE(stage_url, url)
                WHERE stage_url IS NULL OR stage_url = ''
                """
            )

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS league_ocr_drafts (
                id BIGSERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                ocr_id INTEGER NOT NULL,
                source_message_id BIGINT,
                author_user_id BIGINT,
                status TEXT NOT NULL DEFAULT 'pending_admin_review',
                payload_json TEXT NOT NULL,
                warnings_json TEXT,
                last_editor_user_id BIGINT,
                reviewed_by_user_id BIGINT,
                review_note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, ocr_id)
            )
            """
        )

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS league_team_players (
                id BIGSERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                team_name_norm TEXT NOT NULL,
                team_name_raw TEXT NOT NULL,
                player_name_norm TEXT NOT NULL,
                player_name_raw TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, team_name_norm, player_name_norm)
            )
            """
        )

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS league_ocr_applied (
                id BIGSERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                ocr_id INTEGER NOT NULL,
                match_url TEXT,
                status TEXT NOT NULL,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, ocr_id)
            )
            """
        )

        self.conn.commit()


class PostgresCursorAdapter:
    def __init__(self, raw_cursor):
        self._cursor = raw_cursor

    @staticmethod
    def _convert_sql(sql: str) -> str:
        return sql.replace("?", "%s")

    def execute(self, sql, params=None):
        converted = self._convert_sql(sql)
        if params is None:
            return self._cursor.execute(converted)
        return self._cursor.execute(converted, params)

    def executemany(self, sql, seq_of_params):
        converted = self._convert_sql(sql)
        return self._cursor.executemany(converted, seq_of_params)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self):
        return self._cursor.rowcount


class LeagueFeature:
    def __init__(self, db, moscow_tz, is_admin_callable, application=None):
        self.db = db
        self.moscow_tz = moscow_tz
        self.league_reminder_times = {"00:00", "04:00", "08:00", "12:00", "16:00", "20:00"}
        self._is_admin = is_admin_callable
        self.application = application
        self.logger = logging.getLogger("league_bot")
        self._ocr_checked = False
        self._ocr_enabled = False

    def _get_ocr_provider(self) -> str:
        provider = (os.getenv("OCR_PROVIDER", "tesseract") or "tesseract").strip().lower()
        return provider

    def _is_allowed_chat(self, update: Update) -> bool:
        chat = update.effective_chat
        user = update.effective_user
        if not chat or not user:
            return False
        if chat.type in ("group", "supergroup"):
            return True
        if chat.type == "private":
            return bool(self._is_admin(user.id))
        return False

    def _guard(self, handler):
        async def guarded(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not self._is_allowed_chat(update):
                return
            await handler(update, context)

        return guarded

    def register_handlers(self, application):
        application.add_handler(CommandHandler("admin", self._guard(self.cmd_admin)))
        application.add_handler(CommandHandler("league_debts_show", self._guard(self.cmd_league_debts_show)))
        application.add_handler(CommandHandler("league_debts_round", self._guard(self.cmd_league_debts_round)))
        application.add_handler(CommandHandler("league_map_bulk", self._guard(self.cmd_league_map_bulk)))
        application.add_handler(CommandHandler("league_map_show", self._guard(self.cmd_league_map_show)))
        application.add_handler(CommandHandler("league_map_clear", self._guard(self.cmd_league_map_clear)))
        application.add_handler(CommandHandler("league_players_seed", self._guard(self.cmd_league_players_seed)))
        application.add_handler(CommandHandler("league_sync_challenge", self._guard(self.cmd_league_sync_challenge)))
        application.add_handler(CommandHandler("league_sync_now", self._guard(self.cmd_league_sync_now)))
        application.add_handler(CommandHandler("league_sync_off", self._guard(self.cmd_league_sync_off)))
        application.add_handler(CommandHandler("league_reminder_on", self._guard(self.cmd_league_reminder_on)))
        application.add_handler(CommandHandler("league_reminder_off", self._guard(self.cmd_league_reminder_off)))
        application.add_handler(CommandHandler("league_reminder_now", self._guard(self.cmd_league_reminder_now)))
        application.add_handler(CommandHandler("league_reminder_hourly_on", self._guard(self.cmd_league_reminder_hourly_on)))
        application.add_handler(CommandHandler("league_reminder_hourly_off", self._guard(self.cmd_league_reminder_hourly_off)))
        application.add_handler(CommandHandler("league_ocr_fix", self._guard(self.cmd_league_ocr_fix)))
        application.add_handler(CommandHandler("league_ocr_show", self._guard(self.cmd_league_ocr_show)))
        application.add_handler(CommandHandler("league_ocr_approve", self._guard(self.cmd_league_ocr_approve)))
        application.add_handler(CommandHandler("league_ocr_reject", self._guard(self.cmd_league_ocr_reject)))
        application.add_handler(CommandHandler("league_apply_result", self._guard(self.cmd_league_apply_result)))
        application.add_handler(MessageHandler(filters.PHOTO, self._guard(self.on_ocr_photo_message)))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._guard(self.on_ocr_fix_text_message)))
        application.add_handler(CallbackQueryHandler(self._guard(self.on_ocr_callback), pattern=r"^ocr:"))

    def setup_jobs(self, application, logger):
        if not application.job_queue:
            logger.warning("JobQueue unavailable. League reminders disabled.")
            return
        application.job_queue.run_repeating(self.league_reminder_scheduler, interval=60, first=10, name="league_reminder_scheduler")

    def normalize_team_name(self, team_name: str) -> str:
        normalized = (team_name or "").strip().lower()
        normalized = normalized.replace("ё", "е").replace("ë", "е")
        normalized = re.sub(r"\s+", " ", normalized)
        normalized = normalized.replace(" - ", "-")
        normalized = normalized.replace("глимпт", "глимт")
        return normalized

    def default_league_players_seed(self) -> Dict[str, List[str]]:
        return {
            "АЕК": ["Eliasson", "Filipe Relvas", "Joao Mario", "Jovic", "Koita", "Marin", "Pilios", "Pineda", "Rota", "Vida"],
            "Аякс": ["Baas", "Berghuis", "Carrizo", "Edvardsen", "Gaaei", "Itakura", "Regeer", "Sutalo", "Weghorst", "Wijndal", "Zinchenko"],
            "Бенфика": ["Antonio Silva", "Aursnes", "Bah", "Dahl", "Dedic", "Lukebakio", "Otamendi", "Rafa", "Rios", "Sudakov"],
            "Бока Хуниорс": ["Ander Herrera", "Ascacibar", "Blanco", "Blondel", "Cavani", "Costa", "Figal", "Merentiel", "Palacios", "Paredes", "Zeballos"],
            "Брага": ["Arrey-mbi", "Gabri Martinez", "Grillitsch", "Joao Moutinho", "Leonardo Lelo", "Niakate", "Ricardo Horta", "Victor Gomez", "Vitor Carvalho", "Zalazar"],
            "Брюгге": [
                "Nordin Jackers",
                "Simon Mignolet",
                "Dani van den Heuvel",
                "Axl De Corte",
                "Joel Ordonez",
                "Brandon Mechele",
                "Jorne Spileers",
                "Vince Osuji",
                "Joaquin Seys",
                "Bjorn Meijer",
                "Kyriani Sabbe",
                "Hugo Siquet",
                "Aleksandar Stankovic",
                "Raphael Onyedika",
                "Lynnt Audoor",
                "Ludovit Reis",
                "Hugo Vetlesen",
                "Alejandro Granados",
                "Felix Lemarechal",
                "Hans Vanaken",
                "Cisse Sandra",
                "Christos Tzolis",
                "Carlos Forbs",
                "Mamadou Diakhon",
                "Shandre Campbell",
                "Nicolo Tresoldi",
                "Romeo Vermant",
                "Gustaf Nilsson",
            ],
            "Буде Глимт": ["Berg", "Bjorkan", "Evjen", "Fet", "Gundersen", "Hauge", "Hogh", "Maatta", "Saltnes", "Slovold"],
            "Копенгаген": ["Achouri", "Aurelio Buta", "Delaney", "Elyounoussi", "Huescas", "Larsson", "Lopez", "Mattsson", "Moukoko", "Zanka"],
            "ПСВ": ["Boadu", "Dest", "Flamingo", "Man", "Mauro Junior", "Perisic", "Schouten", "Van Bommel", "Veerman", "Yarek"],
            "Порту": ["Borja Sainz", "Fofana", "Francisco Moura", "Gabri Vega", "Pepe", "Perez", "Samu", "Sanusi", "Varela", "de Jong"],
            "Расинг": ["Almendra", "Colombo", "Conechny", "Martinez", "Matias Zaracho", "Mura", "Rojas", "Rojo", "Sosa", "Vergara", "Vietto"],
            "Рейнджерс": ["Aarons", "Antman", "Chukwuani", "Cornelius", "Diamonde", "Raskin", "Skov Olsen", "Souttar", "Sterling", "Tavernier"],
            "Ривер Плейт": ["Acuna", "Bustos", "Driussi", "Fernandez", "Galarza", "Galoppo", "Meza", "Montiel", "Portillo", "Quintero"],
            "Селтик": ["Carter-Vickers", "Hatate", "Iheanacho", "Johnston", "Jota", "Maeda", "McGregor", "Nygren", "Oxl.-Chamberlain", "Scales", "Tierney", "Yang Hyun Jun"],
            "Спортинг": ["Doimande", "Eduardo Quaresma", "Geovany Quenda", "Goncalo Inacio", "Hjulmand", "Morita", "Nuno Santos", "Pedro Goncalves", "St. Juste", "Trincao"],
            "Фейеноорд": ["Deijl", "Goncalo Borges", "Hadj-Moussa", "Hwang In Beom", "Kotarski", "Lotomba", "Read", "Smal", "Steijn", "Sterling", "Trauner"],
        }

    def parse_league_map_bulk_text(self, raw_text: str) -> List[Dict]:
        mappings = []
        for line in raw_text.splitlines():
            cleaned = re.sub(r"^\d+\)\s*", "", line.strip())
            if not cleaned:
                continue
            m = re.match(r"^(.+?)\s*[-—]\s*@?([A-Za-z0-9_]+)\s*$", cleaned)
            if not m:
                continue
            team_name = m.group(1).strip()
            username = m.group(2).strip().lstrip("@")
            if team_name and username:
                mappings.append(
                    {
                        "team_name_raw": team_name,
                        "team_name_norm": self.normalize_team_name(team_name),
                        "telegram_username": username,
                    }
                )
        uniq = {}
        for item in mappings:
            uniq[item["team_name_norm"]] = item
        return list(uniq.values())

    def parse_initial_state(self, html_text: str) -> Optional[Dict]:
        marker = "window.__INITIAL_STATE__="
        s = html_text.find(marker)
        if s == -1:
            return None
        e = html_text.find("</script>", s)
        if e == -1:
            return None
        raw = html_text[s + len(marker) : e].strip()
        if raw.endswith(";"):
            raw = raw[:-1]
        try:
            return json.loads(raw)
        except Exception:
            return None

    def fetch_text_url(self, url: str, timeout: int = 30) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; LeagueFeature/1.0)"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")

    def _object_id_add(self, object_id: str, delta: int) -> str:
        return f"{int(object_id, 16) + delta:024x}"

    def _build_debt_line(self, debtor: Dict, opponent: Dict) -> str:
        return f"@{debtor['username']} ({debtor['team_name']}) — @{opponent['username']} ({opponent['team_name']})"

    def sync_challenge_stage_debts(self, chat_id: int, stage_url: str, max_round: int) -> Dict:
        html_text = self.fetch_text_url(stage_url)
        state = self.parse_initial_state(html_text)
        if not state:
            raise ValueError("Не удалось прочитать данные stage (INITIAL_STATE).")

        rooms = state.get("rooms", {})
        stage_room = None
        for room in rooms.values():
            if isinstance(room, dict) and "rounds" in room and "competitors" in room and "groups" in room:
                stage_room = room
                break
        if not stage_room:
            raise ValueError("Не найдена структура stage в данных страницы.")

        rounds_map = stage_room.get("rounds", {})
        competitors_map = stage_room.get("competitors", {})
        team_map_items = self.db.get_league_team_map(chat_id)
        team_to_user = {item["team_name_norm"]: item["telegram_username"] for item in team_map_items}

        debt_entries = []
        unresolved = set()
        unresolved_matches = 0

        sorted_rounds = sorted(rounds_map.values(), key=lambda x: x.get("order", 10**9))
        for round_item in sorted_rounds:
            order = round_item.get("order")
            if not isinstance(order, int) or order > max_round:
                continue

            for series_id in round_item.get("seriesIds", []):
                match_id = self._object_id_add(series_id, 1)
                match_url = re.sub(r"/stage/.*$", f"/match/{match_id}", stage_url)
                try:
                    match_html = self.fetch_text_url(match_url)
                    match_state = self.parse_initial_state(match_html)
                    if not match_state:
                        continue

                    match_rooms = match_state.get("rooms", {})
                    match_room = None
                    for room in match_rooms.values():
                        if isinstance(room, dict) and "homeCompetitorId" in room and "awayCompetitorId" in room:
                            match_room = room
                            break
                    if not match_room:
                        continue

                    round_name = match_room.get("roundName")
                    round_num_match = re.search(r"(\d+)", round_name or "")
                    if not round_num_match:
                        continue
                    round_num = int(round_num_match.group(1))
                    if round_num > max_round:
                        continue

                    if match_room.get("winnerSlot") is not None:
                        continue

                    home_id = match_room.get("homeCompetitorId")
                    away_id = match_room.get("awayCompetitorId")
                    home_team = (
                        (match_room.get("competitors", {}).get(home_id) or competitors_map.get(home_id) or {}).get("name")
                    )
                    away_team = (
                        (match_room.get("competitors", {}).get(away_id) or competitors_map.get(away_id) or {}).get("name")
                    )
                    if not home_team or not away_team:
                        continue

                    home_norm = self.normalize_team_name(home_team)
                    away_norm = self.normalize_team_name(away_team)
                    home_user = team_to_user.get(home_norm)
                    away_user = team_to_user.get(away_norm)

                    unresolved_matches += 1
                    if not home_user:
                        unresolved.add(home_team)
                    if not away_user:
                        unresolved.add(away_team)
                    if not home_user or not away_user:
                        continue

                    round_label = f"{round_num} тур"
                    debt_entries.append(
                        {
                            "round_label": round_label,
                            "debtor_username": home_user,
                            "opponent_username": away_user,
                            "raw_line": self._build_debt_line(
                                {"username": home_user, "team_name": home_team},
                                {"username": away_user, "team_name": away_team},
                            ),
                        }
                    )
                    debt_entries.append(
                        {
                            "round_label": round_label,
                            "debtor_username": away_user,
                            "opponent_username": home_user,
                            "raw_line": self._build_debt_line(
                                {"username": away_user, "team_name": away_team},
                                {"username": home_user, "team_name": home_team},
                            ),
                        }
                    )
                except Exception:
                    continue

        self.db.replace_league_debts(chat_id, debt_entries)
        self.db.set_league_challenge_source(chat_id, stage_url, max_round)
        return {
            "entries_count": len(debt_entries),
            "unresolved_teams": sorted(unresolved),
            "unresolved_matches": unresolved_matches,
            "max_round": max_round,
        }

    def format_league_debts_post(self, chat_id: int) -> str:
        by_round = self.db.get_league_debts_by_round(chat_id)
        if not by_round:
            return "Общие долги:\n\nНет долгов."
        items = []
        for label, entries in by_round.items():
            m = re.search(r"(\d+)", label)
            order = int(m.group(1)) if m else 10**9
            items.append((order, label, entries))
        items.sort(key=lambda x: x[0])
        lines = ["Общие долги:", ""]
        for _, label, entries in items:
            lines.append(f"{label}:")
            seen = set()
            uniq_entries = []
            for it in entries:
                a = (it.get("debtor_username") or "").lower()
                b = (it.get("opponent_username") or "").lower()
                k = tuple(sorted([a, b]))
                if a and b and k in seen:
                    continue
                if a and b:
                    seen.add(k)
                uniq_entries.append(it)
            for it in uniq_entries:
                lines.append(it["raw_line"])
            lines.append("")
        return "\n".join(lines).strip()

    def format_league_debts_round(self, chat_id: int, round_num: int) -> str:
        label = f"{round_num} тур"
        entries = self.db.get_league_debts_by_round(chat_id).get(label, [])
        seen = set()
        uniq_entries = []
        for it in entries:
            a = (it.get("debtor_username") or "").lower()
            b = (it.get("opponent_username") or "").lower()
            k = tuple(sorted([a, b]))
            if a and b and k in seen:
                continue
            if a and b:
                seen.add(k)
            uniq_entries.append(it)
        lines = [f"{label}:"]
        if not uniq_entries:
            lines.append("Нет долгов.")
        else:
            lines.extend([it["raw_line"] for it in uniq_entries])
        return "\n".join(lines)

    def _find_match_candidates_by_teams(self, chat_id: int, home_team: str, away_team: str) -> List[Dict]:
        source = self.db.get_league_challenge_source(chat_id)
        if not source or not source.get("enabled"):
            return []

        stage_url = source.get("stage_url")
        max_round = int(source.get("max_round") or 0)
        if not stage_url:
            return []

        html_text = self.fetch_text_url(stage_url)
        state = self.parse_initial_state(html_text)
        if not state:
            return []

        rooms = state.get("rooms", {})
        stage_room = None
        for room in rooms.values():
            if isinstance(room, dict) and "rounds" in room and "competitors" in room:
                stage_room = room
                break
        if not stage_room:
            return []

        rounds_map = stage_room.get("rounds", {})
        target_set = {self.normalize_team_name(home_team), self.normalize_team_name(away_team)}
        candidates = []

        sorted_rounds = sorted(rounds_map.values(), key=lambda x: x.get("order", 10**9))
        for round_item in sorted_rounds:
            round_num = int(round_item.get("order") or 0)
            if max_round and round_num > max_round:
                continue
            for series_id in round_item.get("seriesIds", []):
                match_id = self._object_id_add(series_id, 1)
                match_url = re.sub(r"/stage/.*$", f"/match/{match_id}", stage_url)
                try:
                    match_html = self.fetch_text_url(match_url)
                    match_state = self.parse_initial_state(match_html)
                    if not match_state:
                        continue
                    match_rooms = match_state.get("rooms", {})
                    match_room = None
                    for room in match_rooms.values():
                        if isinstance(room, dict) and "homeCompetitorId" in room and "awayCompetitorId" in room:
                            match_room = room
                            break
                    if not match_room:
                        continue
                    home_id = match_room.get("homeCompetitorId")
                    away_id = match_room.get("awayCompetitorId")
                    comps = match_room.get("competitors", {})
                    home_name = (comps.get(home_id) or {}).get("name")
                    away_name = (comps.get(away_id) or {}).get("name")
                    if not home_name or not away_name:
                        continue
                    found_set = {self.normalize_team_name(home_name), self.normalize_team_name(away_name)}
                    if found_set != target_set:
                        continue
                    candidates.append(
                        {
                            "round_num": round_num,
                            "match_url": match_url,
                            "home_team": home_name,
                            "away_team": away_name,
                        }
                    )
                except Exception:
                    continue

        return candidates

    def _select_candidate_min_round(self, candidates: List[Dict]) -> Optional[Dict]:
        if not candidates:
            return None
        return sorted(candidates, key=lambda x: (x.get("round_num") or 10**9, x.get("match_url") or ""))[0]

    def _extract_match_teams_from_url(self, match_url: str) -> Optional[Dict]:
        try:
            match_html = self.fetch_text_url(match_url)
            match_state = self.parse_initial_state(match_html)
            if not match_state:
                return None
            match_rooms = match_state.get("rooms", {})
            match_room = None
            for room in match_rooms.values():
                if isinstance(room, dict) and "homeCompetitorId" in room and "awayCompetitorId" in room:
                    match_room = room
                    break
            if not match_room:
                return None
            home_id = match_room.get("homeCompetitorId")
            away_id = match_room.get("awayCompetitorId")
            comps = match_room.get("competitors", {})
            home_name = (comps.get(home_id) or {}).get("name")
            away_name = (comps.get(away_id) or {}).get("name")
            if not home_name or not away_name:
                return None
            round_name = (match_room.get("roundName") or "").strip()
            round_num = None
            round_match = re.search(r"(\d+)", round_name)
            if round_match:
                round_num = int(round_match.group(1))
            return {
                "match_url": match_url,
                "home_team": home_name,
                "away_team": away_name,
                "round_num": round_num,
                "round_name": round_name,
            }
        except Exception:
            return None

    def _map_payload_to_site_sides(self, payload: Dict, site_home: str, site_away: str) -> Dict:
        payload_home_norm = self.normalize_team_name(payload.get("home_team") or "")
        payload_away_norm = self.normalize_team_name(payload.get("away_team") or "")
        site_home_norm = self.normalize_team_name(site_home or "")
        site_away_norm = self.normalize_team_name(site_away or "")

        swapped = payload_home_norm == site_away_norm and payload_away_norm == site_home_norm
        if not swapped:
            return {
                "swapped": False,
                "home_team": payload.get("home_team"),
                "away_team": payload.get("away_team"),
                "score_home": payload.get("score_home"),
                "score_away": payload.get("score_away"),
                "home_goals": list(payload.get("home_goals") or []),
                "away_goals": list(payload.get("away_goals") or []),
                "home_assists": list(payload.get("home_assists") or []),
                "away_assists": list(payload.get("away_assists") or []),
            }

        return {
            "swapped": True,
            "home_team": payload.get("away_team"),
            "away_team": payload.get("home_team"),
            "score_home": payload.get("score_away"),
            "score_away": payload.get("score_home"),
            "home_goals": list(payload.get("away_goals") or []),
            "away_goals": list(payload.get("home_goals") or []),
            "home_assists": list(payload.get("away_assists") or []),
            "away_assists": list(payload.get("home_assists") or []),
        }

    def _selectors_from_env(self, key: str, defaults: List[str]) -> List[str]:
        raw = (os.getenv(key) or "").strip()
        if not raw:
            return list(defaults)
        parts = [part.strip() for part in re.split(r"[\n|]+", raw) if part.strip()]
        return parts or list(defaults)

    def _get_env_loose(self, key: str) -> str:
        value = os.getenv(key)
        if value is None:
            target = (key or "").strip().upper()
            for env_key, env_value in os.environ.items():
                if (env_key or "").strip().upper() == target:
                    value = env_value
                    break
        value = str(value or "").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1].strip()
        return value

    @staticmethod
    def _xpath_literal(value: str) -> str:
        if "'" not in value:
            return f"'{value}'"
        if '"' not in value:
            return f'"{value}"'
        chunks = value.split("'")
        return "concat(" + ", \"'\", ".join([f"'{chunk}'" for chunk in chunks]) + ")"

    @staticmethod
    def _attr_contains_any(element, needles: List[str]) -> bool:
        try:
            values = [
                (element.get_attribute("name") or ""),
                (element.get_attribute("id") or ""),
                (element.get_attribute("placeholder") or ""),
                (element.get_attribute("autocomplete") or ""),
                (element.get_attribute("aria-label") or ""),
            ]
        except Exception:
            return False
        hay = " ".join(values).lower()
        return any(needle in hay for needle in needles)

    def _find_login_email_element(self, driver):
        try:
            inputs = driver.find_elements(By.CSS_SELECTOR, "input")
        except Exception:
            return None

        candidates = []
        for element in inputs:
            try:
                if element.is_enabled():
                    candidates.append(element)
            except Exception:
                continue

        if not candidates:
            candidates = list(inputs)

        for element in candidates:
            try:
                input_type = (element.get_attribute("type") or "").lower().strip()
            except Exception:
                input_type = ""
            if input_type == "email":
                return element
            if self._attr_contains_any(element, ["email", "mail", "login", "user", "username"]):
                return element

        for element in candidates:
            try:
                input_type = (element.get_attribute("type") or "text").lower().strip()
            except Exception:
                input_type = "text"
            if input_type in {"text", "search", "tel"}:
                return element
        return None

    def _find_login_password_element(self, driver):
        try:
            inputs = driver.find_elements(By.CSS_SELECTOR, "input")
        except Exception:
            return None

        candidates = []
        for element in inputs:
            try:
                if element.is_enabled():
                    candidates.append(element)
            except Exception:
                continue

        if not candidates:
            candidates = list(inputs)

        for element in candidates:
            try:
                input_type = (element.get_attribute("type") or "").lower().strip()
            except Exception:
                input_type = ""
            if input_type == "password":
                return element
            if self._attr_contains_any(element, ["pass", "парол"]):
                return element
        return None

    def _set_input_value_js(self, driver, element, value: str) -> bool:
        try:
            driver.execute_script(
                """
                const el = arguments[0];
                const val = arguments[1];
                if (!el) return false;
                el.focus();
                el.value = '';
                el.value = val;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
                """,
                element,
                value,
            )
            return True
        except Exception:
            return False

    def _list_browser_contexts(self, driver):
        contexts = [None]
        try:
            frames = driver.find_elements(By.TAG_NAME, "iframe")
        except Exception:
            frames = []
        contexts.extend(frames)
        return contexts

    def _switch_browser_context(self, driver, frame):
        driver.switch_to.default_content()
        if frame is not None:
            driver.switch_to.frame(frame)

    def _fill_login_field_any_context(self, driver, selectors: List[str], value: str, field_kind: str) -> Optional[str]:
        contexts = self._list_browser_contexts(driver)
        for frame in contexts:
            try:
                self._switch_browser_context(driver, frame)
            except Exception:
                continue

            for selector in selectors:
                try:
                    elements = self._find_elements_in_current_context(driver, selector)
                except Exception:
                    continue
                for element in elements:
                    try:
                        try:
                            visible = element.is_displayed()
                        except Exception:
                            visible = False
                        if visible:
                            element.clear()
                            element.send_keys(value)
                        else:
                            if not self._set_input_value_js(driver, element, value):
                                continue
                        self._switch_browser_context(driver, None)
                        return selector
                    except Exception:
                        continue

            try:
                fallback = (
                    self._find_login_email_element(driver)
                    if field_kind == "email"
                    else self._find_login_password_element(driver)
                )
            except Exception:
                fallback = None

            if fallback is not None:
                try:
                    try:
                        visible = fallback.is_displayed()
                    except Exception:
                        visible = False
                    if visible:
                        fallback.clear()
                        fallback.send_keys(value)
                    else:
                        if not self._set_input_value_js(driver, fallback, value):
                            raise RuntimeError("fallback input fill failed")
                    self._switch_browser_context(driver, None)
                    return f"fallback:{field_kind}"
                except Exception:
                    pass

        try:
            self._switch_browser_context(driver, None)
        except Exception:
            pass
        return None

    def _has_login_email_any_context(self, driver) -> bool:
        contexts = self._list_browser_contexts(driver)
        for frame in contexts:
            try:
                self._switch_browser_context(driver, frame)
            except Exception:
                continue
            try:
                if self._find_login_email_element(driver) is not None:
                    self._switch_browser_context(driver, None)
                    return True
            except Exception:
                continue
        try:
            self._switch_browser_context(driver, None)
        except Exception:
            pass
        return False

    def _search_selector_in_frames(self, driver, selector: str):
        found = []
        try:
            driver.switch_to.default_content()
        except Exception:
            pass

        try:
            found.extend(self._find_elements_in_current_context(driver, selector))
        except Exception:
            pass

        try:
            frames = driver.find_elements(By.TAG_NAME, "iframe")
        except Exception:
            frames = []

        for frame in frames:
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(frame)
                found.extend(self._find_elements_in_current_context(driver, selector))
            except Exception:
                continue
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        return found

    def _find_elements_in_current_context(self, driver, selector: str):
        selector = (selector or "").strip()
        if not selector:
            return []

        if selector.startswith("text="):
            text = selector.split("=", 1)[1].strip()
            if not text:
                return []
            xpath = f"//*[contains(normalize-space(.), {self._xpath_literal(text)})]"
            return driver.find_elements(By.XPATH, xpath)

        has_text_match = re.match(r"^([a-zA-Z0-9_-]+):has-text\((['\"])(.*?)\2\)$", selector)
        if has_text_match:
            tag = has_text_match.group(1)
            text = has_text_match.group(3)
            xpath = f"//{tag}[contains(normalize-space(.), {self._xpath_literal(text)})]"
            return driver.find_elements(By.XPATH, xpath)

        return driver.find_elements(By.CSS_SELECTOR, selector)

    def _find_elements_by_selector(self, driver, selector: str):
        return self._search_selector_in_frames(driver, selector)

    async def _click_first_available(self, driver, selectors: List[str], timeout: int = 3000) -> Optional[str]:
        for selector in selectors:
            try:
                elements = self._find_elements_by_selector(driver, selector)
            except Exception:
                continue
            if not elements:
                continue
            for element in elements:
                try:
                    if not element.is_displayed():
                        continue
                    element.click()
                    return selector
                except Exception:
                    try:
                        driver.execute_script("arguments[0].click();", element)
                        return selector
                    except Exception:
                        continue
        return None

    async def _fill_first_available(self, driver, selectors: List[str], value: str) -> Optional[str]:
        for selector in selectors:
            try:
                elements = self._find_elements_by_selector(driver, selector)
            except Exception:
                continue
            if not elements:
                continue
            for element in elements:
                try:
                    if not element.is_displayed():
                        continue
                    element.clear()
                    element.send_keys(value)
                    return selector
                except Exception:
                    continue
        return None

    async def _pick_first_input_group(self, driver, selectors: List[str]):
        for selector in selectors:
            try:
                elements = self._find_elements_by_selector(driver, selector)
            except Exception:
                continue
            visible = [element for element in elements if element.is_displayed()]
            count = len(visible)
            if count > 0:
                return visible, count, selector
        return None, 0, None

    def _build_side_events(self, payload: Dict, side: str) -> List[Dict]:
        goals = list(payload.get(f"{side}_goals") or [])
        assists = list(payload.get(f"{side}_assists") or [])
        events = []
        for goal, assist in zip_longest(goals, assists, fillvalue=""):
            if not goal and not assist:
                continue
            events.append({"goal": str(goal or ""), "assist": str(assist or "")})
        return events

    async def _fill_side_events(self, driver, payload: Dict, side: str) -> Dict:
        events = self._build_side_events(payload, side)
        if not events:
            return {"ok": True, "events": 0, "filled_goals": 0, "filled_assists": 0}

        goal_defaults = [
            f"input[name*='{side}'][name*='goal']",
            f"input[name*='{side}'][name*='scorer']",
            f"input[name*='{side}'][name*='player']",
            f"input[id*='{side}'][id*='goal']",
        ]
        assist_defaults = [
            f"input[name*='{side}'][name*='assist']",
            f"input[id*='{side}'][id*='assist']",
        ]
        goal_selectors = self._selectors_from_env(f"CHALLENGE_{side.upper()}_GOAL_SELECTORS", goal_defaults)
        assist_selectors = self._selectors_from_env(f"CHALLENGE_{side.upper()}_ASSIST_SELECTORS", assist_defaults)

        goal_group, goal_count, goal_selector = await self._pick_first_input_group(driver, goal_selectors)
        assist_group, assist_count, assist_selector = await self._pick_first_input_group(driver, assist_selectors)

        if goal_count <= 0 and assist_count <= 0:
            return {
                "ok": False,
                "events": len(events),
                "filled_goals": 0,
                "filled_assists": 0,
                "message": f"Не найдены поля событий для стороны {side}",
            }

        filled_goals = 0
        filled_assists = 0
        for i, event in enumerate(events):
            if goal_group is not None and i < goal_count and event.get("goal"):
                try:
                    goal_group[i].clear()
                    goal_group[i].send_keys(event["goal"])
                    filled_goals += 1
                except Exception:
                    pass
            if assist_group is not None and i < assist_count and event.get("assist"):
                try:
                    assist_group[i].clear()
                    assist_group[i].send_keys(event["assist"])
                    filled_assists += 1
                except Exception:
                    pass

        return {
            "ok": True,
            "events": len(events),
            "filled_goals": filled_goals,
            "filled_assists": filled_assists,
            "goal_selector": goal_selector,
            "assist_selector": assist_selector,
        }

    def _json_has_tokens(self, raw_value: str) -> bool:
        try:
            payload = json.loads(raw_value)
        except Exception:
            return False

        if not isinstance(payload, dict):
            return False

        candidates = [
            payload.get("accessToken"),
            payload.get("refreshToken"),
            payload.get("idToken"),
        ]
        manager = payload.get("stsTokenManager") or payload.get("tokenManager")
        if isinstance(manager, dict):
            candidates.extend(
                [
                    manager.get("accessToken"),
                    manager.get("refreshToken"),
                    manager.get("idToken"),
                ]
            )
        return any(isinstance(item, str) and item.strip() for item in candidates)

    async def _has_visible_sign_in(self, driver) -> bool:
        selectors = [
            "a[href='/login']",
            "a:has-text('Sign in')",
            "button:has-text('Sign in')",
            "a:has-text('Log in')",
            "button:has-text('Log in')",
            "a:has-text('Login')",
            "button:has-text('Login')",
            "a:has-text('Войти')",
            "button:has-text('Войти')",
        ]
        for selector in selectors:
            try:
                elements = self._find_elements_by_selector(driver, selector)
            except Exception:
                continue
            for element in elements[:10]:
                try:
                    if element.is_displayed():
                        return True
                except Exception:
                    continue
        return False

    async def _has_auth_storage(self, driver) -> bool:
        try:
            storage = driver.execute_script(
                """
                () => {
                    const out = {};
                    for (let i = 0; i < localStorage.length; i += 1) {
                        const key = localStorage.key(i);
                        if (!key) continue;
                        out[key] = localStorage.getItem(key) || "";
                    }
                    return out;
                }
                """
            )
        except Exception:
            storage = {}

        storage = storage if isinstance(storage, dict) else {}
        for key, value in storage.items():
            key_lower = str(key).lower()
            if str(key).startswith("firebase:authUser:"):
                return True
            if "authuser" in key_lower and isinstance(value, str) and self._json_has_tokens(value):
                return True
            if "token" in key_lower and isinstance(value, str) and len(value.strip()) > 80:
                return True
        return False

    async def _has_auth_cookie(self, driver) -> bool:
        try:
            cookies = driver.get_cookies()
        except Exception:
            return False

        for cookie in cookies:
            name = str(cookie.get("name", "")).lower()
            if any(part in name for part in ("session", "auth", "token")):
                value = str(cookie.get("value", "")).strip()
                if value and value.lower() not in {"true", "false", "1", "0"}:
                    return True
        return False

    async def _is_challenge_session_authorized(self, driver) -> bool:
        current_url = (driver.current_url or "").lower()
        if "challenge.place" in current_url and "/login" in current_url:
            return False
        if await self._has_visible_sign_in(driver):
            return False
        if await self._has_auth_storage(driver):
            return True
        if await self._has_auth_cookie(driver):
            return True
        return True

    async def _attempt_challenge_login_with_credentials(self, driver, match_url: str) -> Dict:
        login_email_raw = self._get_env_loose("CHALLENGE_LOGIN_EMAIL")
        login_password_raw = self._get_env_loose("CHALLENGE_LOGIN_PASSWORD")
        alt_email_raw = self._get_env_loose("CHALLENGE_EMAIL")
        alt_password_raw = self._get_env_loose("CHALLENGE_PASSWORD")

        email = login_email_raw or alt_email_raw
        password = login_password_raw or alt_password_raw
        self.logger.info(
            "Challenge login env presence: CHALLENGE_LOGIN_EMAIL=%s CHALLENGE_LOGIN_PASSWORD=%s CHALLENGE_EMAIL=%s CHALLENGE_PASSWORD=%s",
            bool(login_email_raw),
            bool(login_password_raw),
            bool(alt_email_raw),
            bool(alt_password_raw),
        )
        if not email or not password:
            presence = (
                "["
                f"CHALLENGE_LOGIN_EMAIL={'yes' if bool(login_email_raw) else 'no'}, "
                f"CHALLENGE_LOGIN_PASSWORD={'yes' if bool(login_password_raw) else 'no'}, "
                f"CHALLENGE_EMAIL={'yes' if bool(alt_email_raw) else 'no'}, "
                f"CHALLENGE_PASSWORD={'yes' if bool(alt_password_raw) else 'no'}"
                "]"
            )
            return {
                "ok": False,
                "message": (
                    "Не авторизовано в challenge.place. "
                    "Укажите CHALLENGE_LOGIN_EMAIL и CHALLENGE_LOGIN_PASSWORD "
                    "(или CHALLENGE_EMAIL и CHALLENGE_PASSWORD) и перезапустите сервис. "
                    f"Статус env: {presence}"
                ),
            }

        login_url = self._get_env_loose("CHALLENGE_LOGIN_URL") or "https://challenge.place/login"
        self.logger.info("Trying credential login for challenge.place")

        login_urls = []
        current_url = (driver.current_url or "").strip()
        if current_url.startswith("http"):
            login_urls.append(current_url)
        login_urls.extend(
            [
                login_url,
                "https://challenge.place/login",
                "https://challenge.place/signin",
                "https://challenge.place/auth/login",
            ]
        )
        seen = set()
        normalized_urls = []
        for url in login_urls:
            url = (url or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            normalized_urls.append(url)

        loaded = False
        for url in normalized_urls:
            try:
                driver.get(url)
                time.sleep(1.0)
                loaded = True
                email_found = False
                for _ in range(24):
                    if self._has_login_email_any_context(driver):
                        email_found = True
                        break
                    time.sleep(0.5)
                if email_found:
                    break
            except Exception:
                continue
        if not loaded:
            return {"ok": False, "message": "Не удалось открыть страницу логина."}

        email_selectors = self._selectors_from_env(
            "CHALLENGE_LOGIN_EMAIL_SELECTORS",
            [
                "input[type='email']",
                "input[name*='email']",
                "input[name*='login']",
                "input[name*='user']",
                "input[type='text'][autocomplete='username']",
                "input[autocomplete='email']",
                "input[placeholder*='mail']",
                "input[placeholder*='Email']",
                "input[placeholder*='email']",
            ],
        )
        password_selectors = self._selectors_from_env(
            "CHALLENGE_LOGIN_PASSWORD_SELECTORS",
            [
                "input[type='password']",
                "input[name*='password']",
                "input[autocomplete='current-password']",
                "input[placeholder*='Password']",
                "input[placeholder*='password']",
            ],
        )
        submit_selectors = self._selectors_from_env(
            "CHALLENGE_LOGIN_SUBMIT_SELECTORS",
            [
                "button[type='submit']",
                "button:has-text('Sign in')",
                "button:has-text('Log in')",
                "button:has-text('Login')",
                "button:has-text('Войти')",
                "button:has-text('Continue')",
                "button:has-text('Next')",
                "button:has-text('Продолжить')",
            ],
        )

        filled_email_selector = self._fill_login_field_any_context(driver, email_selectors, email, "email")
        if not filled_email_selector:
            try:
                title = driver.title or ""
            except Exception:
                title = ""
            try:
                current = driver.current_url or ""
            except Exception:
                current = ""
            try:
                input_count = int(driver.execute_script("return document.querySelectorAll('input').length;") or 0)
            except Exception:
                input_count = -1
            return {
                "ok": False,
                "message": (
                    "Не удалось найти поле email на странице логина. "
                    f"url={current}; title={title}; inputs={input_count}"
                ),
            }

        filled_password_selector = self._fill_login_field_any_context(driver, password_selectors, password, "password")

        if not filled_password_selector:
            clicked_continue = await self._click_first_available(driver, submit_selectors, timeout=5000)
            if not clicked_continue:
                try:
                    active = driver.switch_to.active_element
                    active.send_keys("\n")
                except Exception:
                    pass
            time.sleep(2.0)
            filled_password_selector = self._fill_login_field_any_context(driver, password_selectors, password, "password")

        if not filled_password_selector:
            return {
                "ok": False,
                "message": (
                    "Не удалось найти поле пароля на странице логина. "
                    "Возможно, у challenge.place сейчас email-only/кодовый вход (без пароля)."
                ),
            }

        clicked_submit = await self._click_first_available(driver, submit_selectors, timeout=5000)
        if not clicked_submit:
            try:
                active = driver.switch_to.active_element
                active.send_keys("\n")
            except Exception:
                pass

        time.sleep(3.0)
        if match_url:
            try:
                driver.get(match_url)
                time.sleep(1.0)
            except Exception:
                pass

        if not await self._is_challenge_session_authorized(driver):
            return {
                "ok": False,
                "message": "Логин по email/password не прошел. Проверьте креды/2FA/captcha.",
            }
        return {"ok": True, "message": "Логин выполнен."}

    async def _open_match_and_fill_result(self, match_url: str, payload: Dict, dry_run: bool = False) -> Dict:
        if not SELENIUM_AVAILABLE:
            return {"ok": False, "message": "Selenium недоступен в окружении."}

        self.logger.info(
            "Apply result started: dry_run=%s match_url=%s home=%s away=%s",
            dry_run,
            match_url,
            payload.get("home_team"),
            payload.get("away_team"),
        )

        msk_now = datetime.now(ZoneInfo("Europe/Moscow"))
        date_value = msk_now.strftime("%Y-%m-%d")

        options = ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")

        driver = None
        try:
            driver = webdriver.Chrome(options=options)
        except Exception as e:
            self.logger.error("Failed to launch Chromium via Selenium: %s", e)
            return {
                "ok": False,
                "message": "Не удалось запустить Selenium Chromium. Проверьте chromium/chromedriver в контейнере.",
            }

        try:
            driver.set_page_load_timeout(30)
            driver.get(match_url)
            time.sleep(1.0)
            self.logger.info("Match page opened: %s", driver.current_url)

            if not await self._is_challenge_session_authorized(driver):
                self.logger.warning("Challenge session unauthorized for url=%s; trying credential login", match_url)
                login_result = await self._attempt_challenge_login_with_credentials(driver, match_url)
                if not login_result.get("ok"):
                    return {
                        "ok": False,
                        "message": login_result.get("message") or "Не удалось авторизоваться в challenge.place.",
                    }
                if not await self._is_challenge_session_authorized(driver):
                    return {
                        "ok": False,
                        "message": "После логина сессия все еще не авторизована (возможен captcha/2FA).",
                    }

            if dry_run:
                self.logger.info("Dry-run success for match_url=%s", match_url)
                return {"ok": True, "message": f"Dry-run ok: матч открыт, дата к установке {date_value}"}

            edit_selectors = self._selectors_from_env("CHALLENGE_EDIT_SELECTORS", [
                "button:has-text('Edit')",
                "button:has-text('Редактировать')",
                "button:has-text('Set result')",
                "button:has-text('Результат')",
            ])
            await self._click_first_available(driver, edit_selectors)

            date_inputs = self._selectors_from_env("CHALLENGE_DATE_SELECTORS", [
                "input[type='date']",
                "input[name*='date']",
                "input[placeholder*='Date']",
                "input[placeholder*='Дата']",
            ])
            await self._fill_first_available(driver, date_inputs, date_value)

            if payload.get("score_home") is not None and payload.get("score_away") is not None:
                home_score_selectors = self._selectors_from_env(
                    "CHALLENGE_HOME_SCORE_SELECTORS",
                    ["input[name*='home'][name*='score']", "input[placeholder*='Home']"],
                )
                away_score_selectors = self._selectors_from_env(
                    "CHALLENGE_AWAY_SCORE_SELECTORS",
                    ["input[name*='away'][name*='score']", "input[placeholder*='Away']"],
                )
                try:
                    await self._fill_first_available(driver, home_score_selectors, str(payload.get("score_home")))
                    await self._fill_first_available(driver, away_score_selectors, str(payload.get("score_away")))
                except Exception:
                    pass

            home_events_result = await self._fill_side_events(driver, payload, "home")
            away_events_result = await self._fill_side_events(driver, payload, "away")
            if not home_events_result.get("ok") or not away_events_result.get("ok"):
                return {
                    "ok": False,
                    "message": "; ".join(
                        [
                            part
                            for part in [
                                home_events_result.get("message", ""),
                                away_events_result.get("message", ""),
                            ]
                            if part
                        ]
                    ),
                }

            save_buttons = self._selectors_from_env("CHALLENGE_SAVE_SELECTORS", [
                "button:has-text('Save')",
                "button:has-text('Сохранить')",
                "button:has-text('Confirm')",
                "button:has-text('Подтвердить')",
            ])
            clicked_selector = await self._click_first_available(driver, save_buttons)
            saved = bool(clicked_selector)

            time.sleep(1.2)
            success_selectors = self._selectors_from_env("CHALLENGE_SUCCESS_SELECTORS", [
                "text=Saved",
                "text=Сохранено",
                "text=успешно",
                "text=Updated",
            ])
            success_hint = False
            for selector in success_selectors:
                try:
                    if self._find_elements_by_selector(driver, selector):
                        success_hint = True
                        break
                except Exception:
                    continue

            if not saved:
                self.logger.warning("Save button not found for match_url=%s", match_url)
                return {"ok": False, "message": "Не найдено кнопки сохранения результата."}

            self.logger.info(
                "Apply result saved: match_url=%s home_goals=%s home_assists=%s away_goals=%s away_assists=%s success_hint=%s",
                match_url,
                home_events_result.get("filled_goals", 0),
                home_events_result.get("filled_assists", 0),
                away_events_result.get("filled_goals", 0),
                away_events_result.get("filled_assists", 0),
                success_hint,
            )
            return {
                "ok": True,
                "message": (
                    "Результат отправлен на сохранение"
                    f". Home: {home_events_result.get('filled_goals', 0)} гол(ов), {home_events_result.get('filled_assists', 0)} ассист(ов)"
                    f"; Away: {away_events_result.get('filled_goals', 0)} гол(ов), {away_events_result.get('filled_assists', 0)} ассист(ов)"
                    + (". Подтверждение UI найдено." if success_hint else ". Подтверждение UI не найдено.")
                ),
            }
        except SeleniumTimeoutException:
            self.logger.warning("Selenium timeout for match_url=%s", match_url)
            return {"ok": False, "message": "Таймаут при открытии страницы матча."}
        except Exception as e:
            self.logger.exception("Selenium error for match_url=%s", match_url)
            return {"ok": False, "message": f"Ошибка Selenium: {e}"}
        finally:
            try:
                if driver is not None:
                    driver.quit()
            except Exception:
                pass

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        if context.error is None:
            self.logger.error("Unhandled update error without exception object")
            return
        self._rollback_db_safely()
        self.logger.error(
            "Unhandled update error",
            exc_info=(type(context.error), context.error, context.error.__traceback__),
        )

    def _rollback_db_safely(self):
        try:
            rollback = getattr(self.db.conn, "rollback", None)
            if callable(rollback):
                rollback()
        except Exception:
            pass

    def build_league_summary_text(self, chat_id: int, threshold: int = 2) -> str:
        summary = self.db.get_league_debt_summary(chat_id)
        total = self.db.get_league_debts_count(chat_id)
        if not summary:
            return "Долги лиги не загружены."
        lines = [f"📋 Долги лиги (всего матчей-долгов: {total})", ""]
        for row in summary:
            marker = " ⚠️" if row["debts_count"] >= threshold else ""
            lines.append(f"@{row['debtor_username']} — {row['debts_count']}{marker}")
        lines.append("")
        lines.append(f"Порог для напоминания: >= {threshold}")
        return "\n".join(lines)

    async def send_league_reminder_message(self, chat_id: int, threshold: int = 2, bot=None, custom_text: Optional[str] = None) -> bool:
        summary = self.db.get_league_debt_summary(chat_id)
        debtors = [r for r in summary if r["debts_count"] >= threshold]
        if not debtors:
            return False
        mentions = " ".join([f"@{r['debtor_username']}" for r in debtors])
        lines = [
            "🔔 Напоминание по долгам в лиге",
            mentions,
            "",
            custom_text or f"У вас {threshold} и более долгов. Пожалуйста, сыграйте долги сегодня.",
            "",
            "Текущие долги:",
        ]
        lines.extend([f"- @{r['debtor_username']}: {r['debts_count']}" for r in debtors])
        target_bot = bot or (self.application.bot if self.application else None)
        if target_bot is None:
            return False
        try:
            await target_bot.send_message(chat_id=chat_id, text="\n".join(lines))
            return True
        except Exception:
            return False

    async def league_reminder_scheduler(self, context: ContextTypes.DEFAULT_TYPE):
        now_msk = datetime.now(self.moscow_tz)
        time_key = now_msk.strftime("%H:%M")
        is_daily_slot = time_key in self.league_reminder_times
        is_hourly_slot = now_msk.minute == 0
        if not is_daily_slot and not is_hourly_slot:
            return
        try:
            configs = self.db.get_enabled_league_reminder_chats()
        except Exception:
            self._rollback_db_safely()
            self.logger.exception("Failed to fetch reminder configs")
            return
        for cfg in configs:
            chat_id = cfg["chat_id"]
            threshold = cfg.get("threshold", 2)
            if is_daily_slot and bool(cfg.get("enabled")):
                key = f"daily:{now_msk.strftime('%Y-%m-%d %H:%M')}"
                if self.db.try_mark_league_reminder_run(chat_id, key):
                    await self.send_league_reminder_message(chat_id=chat_id, threshold=threshold, bot=context.bot)
            if is_hourly_slot and bool(cfg.get("hourly_enabled")):
                key = f"hourly:{now_msk.strftime('%Y-%m-%d %H:00')}"
                if self.db.try_mark_league_reminder_run(chat_id, key):
                    await self.send_league_reminder_message(
                        chat_id=chat_id,
                        threshold=threshold,
                        bot=context.bot,
                        custom_text=cfg.get("hourly_text") or "Напоминание: сыграйте долги в лиге.",
                    )

    def _is_ocr_ready(self) -> bool:
        if not OCR_AVAILABLE:
            return False
        provider = self._get_ocr_provider()
        if provider == "ocrspace":
            return bool((os.getenv("OCRSPACE_API_KEY", "") or "").strip())
        if not self._ocr_checked:
            custom_cmd = os.getenv("TESSERACT_CMD", "").strip()
            if custom_cmd:
                pytesseract.pytesseract.tesseract_cmd = custom_cmd
            try:
                pytesseract.get_tesseract_version()
                self._ocr_enabled = True
            except Exception:
                self._ocr_enabled = False
            self._ocr_checked = True
        return self._ocr_enabled

    def _ocr_timeout_seconds(self) -> int:
        raw = (os.getenv("OCR_TIMEOUT_SEC", "20") or "20").strip()
        try:
            value = int(raw)
            return max(5, min(value, 120))
        except Exception:
            return 20

    def _ocrspace_extract_lines(self, image_bytes: bytes) -> List[Dict]:
        api_key = (os.getenv("OCRSPACE_API_KEY", "") or "").strip()
        if not api_key:
            raise RuntimeError("OCRSPACE_API_KEY не задан")

        encoded = base64.b64encode(image_bytes).decode("ascii")
        timeout_sec = self._ocr_timeout_seconds()

        def parse_ocrspace_payload(data: Dict) -> List[Dict]:
            parsed = data.get("ParsedResults") or []
            if not parsed:
                return []

            out = []
            for pr in parsed:
                overlay = ((pr.get("TextOverlay") or {}).get("Lines") or [])
                for line in overlay:
                    words = line.get("Words") or []
                    text = " ".join([str(w.get("WordText") or "").strip() for w in words]).strip()
                    if not text:
                        continue
                    if words:
                        x1 = min(int(w.get("Left", 0)) for w in words)
                        y1 = min(int(w.get("Top", line.get("MinTop", 0))) for w in words)
                        x2 = max(int(w.get("Left", 0)) + int(w.get("Width", 0)) for w in words)
                        y2 = max(int(w.get("Top", line.get("MinTop", 0))) + int(w.get("Height", line.get("MaxHeight", 0))) for w in words)
                    else:
                        x1 = int(line.get("MinLeft", 0))
                        y1 = int(line.get("MinTop", 0))
                        x2 = x1 + int(line.get("MaxWidth", 0))
                        y2 = y1 + int(line.get("MaxHeight", 0))
                    out.append({"text": text, "x1": x1, "y1": y1, "x2": x2, "y2": y2})

            if out:
                return out

            text_fallback = "\n".join([(pr.get("ParsedText") or "") for pr in parsed]).strip()
            return [{"text": ln.strip(), "x1": 0, "y1": 0, "x2": 1, "y2": 1} for ln in text_fallback.splitlines() if ln.strip()]

        # OCR.space accepts a single language code, not comma-separated list.
        for language in ("eng", "rus"):
            payload = {
                "apikey": api_key,
                "language": language,
                "isOverlayRequired": "true",
                "OCREngine": "2",
                "base64Image": f"data:image/jpeg;base64,{encoded}",
            }
            body = urllib.parse.urlencode(payload).encode("utf-8")
            req = urllib.request.Request(
                "https://api.ocr.space/parse/image",
                data=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "LeagueBot-OCR/1.0",
                },
            )

            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw)

            if data.get("IsErroredOnProcessing"):
                err_text = " ".join((data.get("ErrorMessage") or []))
                # Continue on language-specific error and try next language.
                if "Value for parameter 'language' is invalid" in err_text:
                    continue
                err = err_text or data.get("ErrorDetails") or "OCRSpace error"
                raise RuntimeError(str(err))

            lines = parse_ocrspace_payload(data)
            if lines:
                return lines

        return []

    def _build_ocr_keyboard(self, chat_id: int, ocr_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Показать", callback_data=f"ocr:show:{chat_id}:{ocr_id}"),
                    InlineKeyboardButton("Исправить", callback_data=f"ocr:fix:{chat_id}:{ocr_id}"),
                ],
                [
                    InlineKeyboardButton("Подтвердить", callback_data=f"ocr:approve:{chat_id}:{ocr_id}"),
                    InlineKeyboardButton("Отклонить", callback_data=f"ocr:reject:{chat_id}:{ocr_id}"),
                ],
            ]
        )

    def _extract_ocr_id_from_message_text(self, text: str) -> Optional[int]:
        m = re.search(r"#(\d+)", text or "")
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    def _resolve_draft_id(self, update: Update, args: Optional[List[str]] = None) -> Optional[int]:
        if args:
            try:
                return int(args[0])
            except Exception:
                return None
        msg = update.effective_message
        if msg and msg.reply_to_message:
            return self._extract_ocr_id_from_message_text(msg.reply_to_message.text or msg.reply_to_message.caption or "")
        return None

    def _match_team_name(self, chat_id: int, raw_name: str) -> Dict:
        team_map = self.db.get_league_team_map(chat_id)
        if not team_map:
            return {"matched": raw_name.strip(), "confidence": 0.0, "exact": False}
        norm = self.normalize_team_name(raw_name)
        exact = next((item for item in team_map if item["team_name_norm"] == norm), None)
        if exact:
            return {"matched": exact["team_name_raw"], "confidence": 1.0, "exact": True}
        best_item = None
        best_score = 0.0
        for item in team_map:
            score = SequenceMatcher(None, norm, item["team_name_norm"]).ratio()
            if score > best_score:
                best_item = item
                best_score = score
        if best_item and best_score >= 0.72:
            return {"matched": best_item["team_name_raw"], "confidence": round(best_score, 2), "exact": False}
        return {"matched": raw_name.strip(), "confidence": round(best_score, 2), "exact": False}

    def _parse_caption_match_and_assists(self, caption: str, chat_id: int) -> Dict:
        lines = [x.strip() for x in (caption or "").splitlines() if x.strip()]
        warnings = []
        team_map_items = self.db.get_league_team_map(chat_id)
        has_team_map = bool(team_map_items)
        home_raw = ""
        away_raw = ""
        match_line_index = -1
        for idx, line in enumerate(lines):
            m = re.match(r"^(.+?)\s*(?:-|—|vs|VS|v\.?s\.?)\s*(.+)$", line)
            if m:
                home_raw = m.group(1).strip()
                away_raw = m.group(2).strip()
                match_line_index = idx
                break

        # Heuristic fallback for captions like "Аякс Спортинг" (without separators).
        if (not home_raw or not away_raw) and lines:
            candidate = lines[0]
            if not re.match(r"^(ассисты|голы|сч[её]т)\b", candidate, flags=re.IGNORECASE):
                words = [w for w in re.split(r"\s+", candidate.strip()) if w]
                if len(words) >= 2:
                    mid = max(1, len(words) // 2)
                    home_raw = " ".join(words[:mid]).strip()
                    away_raw = " ".join(words[mid:]).strip()
                    match_line_index = 0

        if (not home_raw or not away_raw) and has_team_map:
            inferred = self._infer_match_from_caption_line(lines, team_map_items)
            if inferred:
                home_raw = inferred["home"]
                away_raw = inferred["away"]
                match_line_index = inferred["line_index"]
        if not home_raw or not away_raw:
            warnings.append("В подписи не найден формат матча 'Команда1 - Команда2'.")
        home_match = self._match_team_name(chat_id, home_raw or "Хозяева")
        away_match = self._match_team_name(chat_id, away_raw or "Гости")
        if has_team_map and home_raw and home_match["confidence"] < 0.8 and self.normalize_team_name(home_raw) != self.normalize_team_name(home_match["matched"]):
            warnings.append(f"Команда хозяев неуверенно сопоставлена: {home_raw or 'не указана'} -> {home_match['matched']}")
        if has_team_map and away_raw and away_match["confidence"] < 0.8 and self.normalize_team_name(away_raw) != self.normalize_team_name(away_match["matched"]):
            warnings.append(f"Команда гостей неуверенно сопоставлена: {away_raw or 'не указана'} -> {away_match['matched']}")

        assists_raw: Dict[str, List[str]] = {"home": [], "away": [], "any": []}
        current_bucket = None
        start_index = match_line_index + 1 if match_line_index >= 0 else 0
        for line in lines[start_index:]:
            generic_header = re.match(r"^ассисты\b\s*:?\s*(.*)$", line, flags=re.IGNORECASE)
            if generic_header:
                current_bucket = "any"
                inline_raw = generic_header.group(1).strip()
                if inline_raw:
                    for token in [x.strip() for x in re.split(r"[;,]", inline_raw) if x.strip()]:
                        assists_raw["any"].extend(self._expand_person_token(token))
                continue
            header = re.match(r"^ассисты\s+(.+?)\s*:\s*$", line, flags=re.IGNORECASE)
            if header:
                team_label = header.group(1).strip()
                home_score = SequenceMatcher(None, self.normalize_team_name(team_label), self.normalize_team_name(home_raw)).ratio()
                away_score = SequenceMatcher(None, self.normalize_team_name(team_label), self.normalize_team_name(away_raw)).ratio()
                current_bucket = "home" if home_score >= away_score else "away"
                continue
            if current_bucket:
                for token in [x.strip() for x in re.split(r"[;,]", line) if x.strip()]:
                    assists_raw[current_bucket].extend(self._expand_person_token(token))
                continue

            # Fallback: if users send plain player names without "Ассисты" label.
            if re.match(r"^(голы|сч[её]т)\b", line, flags=re.IGNORECASE):
                continue
            if re.search(r"\d\s*[-:]\s*\d", line):
                continue
            for token in [x.strip("-• ") for x in re.split(r"[;,]", line) if x.strip("-• ")]:
                assists_raw["any"].extend(self._expand_person_token(token))

        return {
            "chat_id": chat_id,
            "home_team": home_match["matched"],
            "away_team": away_match["matched"],
            "home_team_raw": home_raw,
            "away_team_raw": away_raw,
            "home_assists": assists_raw["home"],
            "away_assists": assists_raw["away"],
            "assists_any": assists_raw["any"],
            "warnings": warnings,
        }

    def _infer_match_from_caption_line(self, lines: List[str], team_map_items: List[Dict]) -> Optional[Dict]:
        for idx, line in enumerate(lines):
            if re.match(r"^(ассисты|голы|сч[её]т)\b", line, flags=re.IGNORECASE):
                continue
            normalized_line = self.normalize_team_name(line)
            if not normalized_line:
                continue

            words = [w for w in normalized_line.split(" ") if w]
            if len(words) < 2:
                continue

            best = None
            best_score = 0.0
            for split in range(1, len(words)):
                left = " ".join(words[:split]).strip()
                right = " ".join(words[split:]).strip()
                if not left or not right:
                    continue

                left_match = self._match_team_name_from_items(left, team_map_items)
                right_match = self._match_team_name_from_items(right, team_map_items)
                if not left_match or not right_match:
                    continue
                if left_match["team_name_norm"] == right_match["team_name_norm"]:
                    continue

                score = left_match["score"] + right_match["score"]
                if score > best_score:
                    best_score = score
                    best = {
                        "line_index": idx,
                        "home": left_match["team_name_raw"],
                        "away": right_match["team_name_raw"],
                    }

            if best and best_score >= 1.55:
                return best
        return None

    def _match_team_name_from_items(self, raw_name: str, team_map_items: List[Dict]) -> Optional[Dict]:
        normalized = self.normalize_team_name(raw_name)
        if not normalized:
            return None

        for item in team_map_items:
            if item["team_name_norm"] == normalized:
                return {"team_name_norm": item["team_name_norm"], "team_name_raw": item["team_name_raw"], "score": 1.0}

        best_item = None
        best_score = 0.0
        for item in team_map_items:
            score = SequenceMatcher(None, normalized, item["team_name_norm"]).ratio()
            if score > best_score:
                best_item = item
                best_score = score
        if best_item and best_score >= 0.72:
            return {
                "team_name_norm": best_item["team_name_norm"],
                "team_name_raw": best_item["team_name_raw"],
                "score": best_score,
            }
        return None

    def _normalize_player_name(self, name: str) -> str:
        value = (name or "").strip().lower()
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        value = value.replace("ё", "е")
        value = re.sub(r"[^a-zа-я0-9\s\-]", " ", value, flags=re.IGNORECASE)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def _clean_person_label(self, text: str) -> str:
        value = (text or "").strip()
        value = re.sub(r"^(ассист(?:ы)?|assist(?:s)?)\b\s*:?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s+", " ", value).strip(" ,;:-")
        return value

    def _expand_person_token(self, text: str) -> List[str]:
        value = self._clean_person_label(text)
        if not value:
            return []

        m = re.match(r"^(.*?)\s*[\(\[]\s*(\d{1,2})\s*[\)\]]\s*$", value)
        if not m:
            m = re.match(r"^(.*?)\s*[xх\*]\s*(\d{1,2})\s*$", value, flags=re.IGNORECASE)

        if m:
            name = self._clean_person_label(m.group(1))
            if not name:
                return []
            count = max(1, min(int(m.group(2)), 20))
            return [name for _ in range(count)]

        return [value]

    def _transliterate_ru_to_en(self, text: str) -> str:
        mapping = {
            "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
            "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
            "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
            "ю": "yu", "я": "ya",
        }
        out = []
        for ch in (text or ""):
            out.append(mapping.get(ch, ch))
        return "".join(out)

    def _transliterate_en_to_ru(self, text: str) -> str:
        value = (text or "").lower()
        # Order matters for multi-char combinations.
        pairs = [
            ("sch", "щ"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("kh", "х"),
            ("yu", "ю"), ("ya", "я"), ("yo", "е"), ("ts", "ц"), ("th", "т"),
            ("ph", "ф"), ("qu", "к"), ("ck", "к"),
        ]
        for src, dst in pairs:
            value = value.replace(src, dst)

        single = {
            "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х",
            "i": "и", "j": "й", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п",
            "q": "к", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в", "w": "в", "x": "кс",
            "y": "и", "z": "з",
        }
        out = []
        for ch in value:
            out.append(single.get(ch, ch))
        return "".join(out)

    def _tokenize_player_name(self, name: str) -> List[str]:
        norm = self._normalize_player_name(name)
        return [t for t in re.split(r"[\s\-]+", norm) if t]

    def _simplify_name_for_match(self, name: str) -> str:
        value = self._transliterate_ru_to_en(self._normalize_player_name(name))
        value = value.replace("ph", "f").replace("ck", "k").replace("qu", "k")
        value = value.replace("zh", "j").replace("kh", "h").replace("ch", "c").replace("sh", "s")
        value = value.replace("ts", "s")
        value = re.sub(r"[^a-z0-9]", "", value)
        value = re.sub(r"(.)\1+", r"\1", value)
        return value

    def _score_name_match(self, source: str, target: str) -> float:
        a = self._normalize_player_name(source)
        b = self._normalize_player_name(target)
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0

        base_score = SequenceMatcher(None, a, b).ratio()

        a_lat = self._transliterate_ru_to_en(a)
        b_lat = self._transliterate_ru_to_en(b)
        translit_score = SequenceMatcher(None, a_lat, b_lat).ratio()

        a_ru = self._transliterate_en_to_ru(a)
        b_ru = self._transliterate_en_to_ru(b)
        translit_ru_score = SequenceMatcher(None, a_ru, b_ru).ratio()

        simple_a = self._simplify_name_for_match(source)
        simple_b = self._simplify_name_for_match(target)
        simple_score = SequenceMatcher(None, simple_a, simple_b).ratio() if simple_a and simple_b else 0.0

        cons_a = re.sub(r"[aeiouy]", "", simple_a)
        cons_b = re.sub(r"[aeiouy]", "", simple_b)
        consonant_score = SequenceMatcher(None, cons_a, cons_b).ratio() if cons_a and cons_b else 0.0

        contain_score = 0.0
        if simple_a and simple_b and (simple_a in simple_b or simple_b in simple_a):
            contain_score = 0.93

        token_score = 0.0
        tokens_a = self._tokenize_player_name(source)
        tokens_b = self._tokenize_player_name(target)
        if tokens_a and tokens_b:
            for ta in tokens_a:
                for tb in tokens_b:
                    if ta == tb:
                        token_score = max(token_score, 0.98)
                    elif len(ta) >= 3 and len(tb) >= 3 and (ta.startswith(tb) or tb.startswith(ta)):
                        token_score = max(token_score, 0.9)
                    else:
                        token_score = max(token_score, SequenceMatcher(None, ta, tb).ratio() * 0.9)
                    ta_lat = self._transliterate_ru_to_en(ta)
                    tb_lat = self._transliterate_ru_to_en(tb)
                    token_score = max(token_score, SequenceMatcher(None, ta_lat, tb_lat).ratio() * 0.92)
                    ta_ru = self._transliterate_en_to_ru(ta)
                    tb_ru = self._transliterate_en_to_ru(tb)
                    token_score = max(token_score, SequenceMatcher(None, ta_ru, tb_ru).ratio() * 0.9)

        return max(base_score, translit_score, translit_ru_score, token_score, simple_score, consonant_score * 0.95, contain_score)

    def _resolve_assists_by_goals(
        self,
        chat_id: int,
        home_team: str,
        away_team: str,
        assists_any: List[str],
        home_goals: List[str],
        away_goals: List[str],
    ) -> Dict:
        home_team_norm = self.normalize_team_name(home_team)
        away_team_norm = self.normalize_team_name(away_team)
        if isinstance(chat_id, int):
            home_players = self.db.get_league_team_players(chat_id, home_team_norm)
            away_players = self.db.get_league_team_players(chat_id, away_team_norm)
        else:
            home_players = []
            away_players = []
        home_pool = [x["player_name_raw"] for x in home_players]
        away_pool = [x["player_name_raw"] for x in away_players]

        home = []
        away = []
        unknown = []
        warnings = []
        for assist in assists_any:
            assist = self._clean_person_label(assist)
            if not assist:
                continue
            best_home_goal = max([self._score_name_match(assist, x) for x in home_goals], default=0.0)
            best_away_goal = max([self._score_name_match(assist, x) for x in away_goals], default=0.0)
            best_home_player = max([self._score_name_match(assist, x) for x in home_pool], default=0.0)
            best_away_player = max([self._score_name_match(assist, x) for x in away_pool], default=0.0)
            best_home = max(best_home_goal, best_home_player)
            best_away = max(best_away_goal, best_away_player)

            if best_home < 0.65 and best_away < 0.65:
                unknown.append(assist)
                warnings.append(f"Ассист '{assist}' не удалось сопоставить с командой автоматически.")
                continue
            if abs(best_home - best_away) < 0.06:
                unknown.append(assist)
                warnings.append(f"Ассист '{assist}' неоднозначен (оба варианта похожи).")
                continue
            if best_home > best_away:
                home.append(assist)
            else:
                away.append(assist)
        return {"home": home, "away": away, "unknown": unknown, "warnings": warnings}

    def _resolve_goals_by_players(self, chat_id: int, home_team: str, away_team: str, goals_info: Dict) -> Dict:
        home_team_norm = self.normalize_team_name(home_team)
        away_team_norm = self.normalize_team_name(away_team)
        home_players = self.db.get_league_team_players(chat_id, home_team_norm) if isinstance(chat_id, int) else []
        away_players = self.db.get_league_team_players(chat_id, away_team_norm) if isinstance(chat_id, int) else []
        home_pool = [x["player_name_raw"] for x in home_players]
        away_pool = [x["player_name_raw"] for x in away_players]

        # If no rosters are loaded for both teams, keep OCR color result untouched.
        if not home_pool and not away_pool:
            return {
                "home_goals": list(goals_info.get("home_goals", [])),
                "away_goals": list(goals_info.get("away_goals", [])),
                "unknown_goals": list(goals_info.get("unknown_goals", [])),
            }

        home_goals = list(goals_info.get("home_goals", []))
        away_goals = list(goals_info.get("away_goals", []))
        still_unknown = []

        for raw_name in goals_info.get("unknown_goals", []):
            name = self._clean_person_label(raw_name)
            if not name:
                continue
            best_home = max([self._score_name_match(name, p) for p in home_pool], default=0.0)
            best_away = max([self._score_name_match(name, p) for p in away_pool], default=0.0)

            # Both rosters exist: compare both sides.
            if home_pool and away_pool:
                if best_home >= 0.65 and best_home > best_away + 0.05:
                    home_goals.append(name)
                    continue
                if best_away >= 0.65 and best_away > best_home + 0.05:
                    away_goals.append(name)
                    continue
                still_unknown.append(name)
                continue

            # Only home roster exists: map only confident home matches, keep others unknown.
            if home_pool and not away_pool:
                if best_home >= 0.65:
                    home_goals.append(name)
                else:
                    still_unknown.append(name)
                continue

            # Only away roster exists: map only confident away matches, keep others unknown.
            if away_pool and not home_pool:
                if best_away >= 0.65:
                    away_goals.append(name)
                else:
                    still_unknown.append(name)
                continue
            still_unknown.append(name)

        return {"home_goals": home_goals, "away_goals": away_goals, "unknown_goals": still_unknown}

    def _classify_goal_color(self, image_bgr, box: Dict[str, int]) -> str:
        try:
            x1 = max(int(box.get("x1", 0)), 0)
            x2 = max(int(box.get("x2", 0)), x1 + 1)
            y1 = max(int(box.get("y1", 0)), 0)
            y2 = max(int(box.get("y2", 0)), y1 + 1)
            line_w = max(x2 - x1, 1)
            line_h = max(y2 - y1, 1)
            cx1 = x1 + int(line_w * 0.18)
            cx2 = x1 + int(line_w * 0.48)
            cy = y1 + line_h // 2
            cy1 = max(cy - 14, 0)
            cy2 = min(cy + 14, image_bgr.shape[0])
            cx1 = max(cx1, 0)
            cx2 = min(max(cx2, cx1 + 1), image_bgr.shape[1])
            patch = image_bgr[cy1:cy2, cx1:cx2]
            if patch.size == 0:
                sx1 = max(x1 - 70, 0)
                sx2 = max(x1 - 5, sx1 + 1)
                sy1 = max(y1 - 10, 0)
                sy2 = min(y2 + 10, image_bgr.shape[0])
                patch = image_bgr[sy1:sy2, sx1:sx2]
            if patch.size == 0:
                return "unknown"
            hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
            blue_mask = cv2.inRange(hsv, (90, 70, 70), (140, 255, 255))
            green_mask = cv2.inRange(hsv, (35, 60, 60), (90, 255, 255))
            blue_count = int(cv2.countNonZero(blue_mask))
            green_count = int(cv2.countNonZero(green_mask))
            if blue_count < 20 and green_count < 20:
                return "unknown"
            if green_count > blue_count * 1.25:
                return "home"
            if blue_count > green_count * 1.25:
                return "away"
            return "unknown"
        except Exception:
            return "unknown"

    def _ocr_extract_lines(self, image_bgr, psm: int = 6) -> List[Dict]:
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        config = f"--oem 3 --psm {psm}"
        data = pytesseract.image_to_data(rgb, output_type=Output.DICT, config=config, lang="eng+rus")
        rows = len(data.get("text", []))
        grouped: Dict[tuple, Dict] = {}
        for i in range(rows):
            text = str(data["text"][i] or "").strip()
            if not text:
                continue
            try:
                conf = float(data["conf"][i])
            except Exception:
                conf = -1.0
            if conf < 20:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            left = int(data["left"][i])
            top = int(data["top"][i])
            width = int(data["width"][i])
            height = int(data["height"][i])
            item = grouped.setdefault(
                key,
                {"parts": [], "x1": left, "y1": top, "x2": left + width, "y2": top + height},
            )
            item["parts"].append(text)
            item["x1"] = min(item["x1"], left)
            item["y1"] = min(item["y1"], top)
            item["x2"] = max(item["x2"], left + width)
            item["y2"] = max(item["y2"], top + height)

        lines = []
        for item in grouped.values():
            text = " ".join(item["parts"]).strip()
            if not text:
                continue
            lines.append({
                "text": text,
                "x1": item["x1"],
                "y1": item["y1"],
                "x2": item["x2"],
                "y2": item["y2"],
            })
        return lines

    def _extract_score(self, ocr_texts: List[str]) -> Optional[Dict]:
        def _valid_score(home: int, away: int) -> bool:
            return 0 <= home <= 20 and 0 <= away <= 20

        for text in ocr_texts:
            cleaned = (text or "").replace("—", "-").replace("–", "-")
            m = re.search(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b", cleaned)
            if m:
                home, away = int(m.group(1)), int(m.group(2))
                if _valid_score(home, away):
                    return {"home": home, "away": away}

        for text in ocr_texts:
            cleaned = (text or "").replace("—", ":").replace("–", ":")
            m = re.search(r"\b(\d{1,2})\s*:\s*(\d{1,2})\b", cleaned)
            if m:
                home, away = int(m.group(1)), int(m.group(2))
                if _valid_score(home, away):
                    return {"home": home, "away": away}
        return None

    def _extract_score_from_line_items(self, line_items: List[Dict], img_width: int, img_height: int) -> Optional[Dict]:
        top_limit = int(img_height * 0.28)
        center_left = int(img_width * 0.30)
        center_right = int(img_width * 0.70)

        candidates = []
        for item in line_items:
            y1 = int(item.get("y1", 0))
            y2 = int(item.get("y2", 0))
            x1 = int(item.get("x1", 0))
            x2 = int(item.get("x2", 0))
            if y1 > top_limit and y2 > top_limit:
                continue
            if x2 < center_left or x1 > center_right:
                continue
            candidates.append(str(item.get("text", "")))

        # Prefer top-center candidates, then fallback to all lines.
        score = self._extract_score(candidates)
        if score:
            return score
        return self._extract_score([str(x.get("text", "")) for x in line_items])

    def _extract_score_from_image(self, image_bgr) -> Optional[Dict]:
        def parse_score(raw_text: str) -> Optional[Dict]:
            cleaned = (raw_text or "").replace("—", "-").replace("–", "-")
            cleaned = re.sub(r"[^0-9:\-\s]", " ", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if not cleaned:
                return None

            # Remove clock-like fragments (e.g. 90:00) before score matching.
            no_clock = re.sub(r"\b\d{1,2}\s*:\s*\d{2}\b", " ", cleaned)
            no_clock = re.sub(r"\s+", " ", no_clock).strip()

            m = re.search(r"\b(\d{1,2})\s*[-:]\s*(\d{1,2})\b", cleaned)
            if m:
                home, away = int(m.group(1)), int(m.group(2))
                if 0 <= home <= 20 and 0 <= away <= 20:
                    return {"home": home, "away": away}

            m = re.search(r"\b(\d{1,2})\s*[-:]\s*(\d{1,2})\b", no_clock)
            if m:
                home, away = int(m.group(1)), int(m.group(2))
                if 0 <= home <= 20 and 0 <= away <= 20:
                    return {"home": home, "away": away}

            # Pattern like "2 0 90:00" or "2 0".
            m = re.search(r"\b(\d{1,2})\s+(\d{1,2})\b", no_clock)
            if m:
                home, away = int(m.group(1)), int(m.group(2))
                if 0 <= home <= 20 and 0 <= away <= 20:
                    return {"home": home, "away": away}

            # Compact fallback like "20" for 2:0 (only for one-digit scores).
            m = re.search(r"\b([0-9])([0-9])\b", no_clock)
            if m:
                return {"home": int(m.group(1)), "away": int(m.group(2))}

            m = re.search(r"\b(\d{1,2})\s+(\d{1,2})\b", cleaned)
            if m:
                home, away = int(m.group(1)), int(m.group(2))
                if 0 <= home <= 20 and 0 <= away <= 20:
                    return {"home": home, "away": away}
            return None

        h, w = image_bgr.shape[:2]
        rois = [
            image_bgr[max(int(h * 0.03), 0) : min(int(h * 0.20), h), max(int(w * 0.38), 0) : min(int(w * 0.62), w)],
            image_bgr[max(int(h * 0.02), 0) : min(int(h * 0.14), h), max(int(w * 0.34), 0) : min(int(w * 0.66), w)],
            image_bgr[max(int(h * 0.00), 0) : min(int(h * 0.18), h), max(int(w * 0.30), 0) : min(int(w * 0.70), w)],
        ]

        for roi in rois:
            if roi.size == 0:
                continue
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            enlarged = cv2.resize(gray, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
            _, binary = cv2.threshold(enlarged, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            inv = cv2.bitwise_not(binary)

            for img in (binary, inv, enlarged):
                for psm in (7, 6, 11):
                    text = pytesseract.image_to_string(
                        img,
                        config=f"--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789-:",
                        lang="eng",
                    )
                    parsed = parse_score(text)
                    if parsed:
                        return parsed
        return None

    def _extract_goal_scorers(self, image_bgr, line_items: List[Dict], offset_x: int = 0, offset_y: int = 0) -> Dict:
        home_goals = []
        away_goals = []
        unknown_goals = []
        seen = set()
        for item in line_items:
            raw = str(item.get("text") or "").strip()
            if not raw:
                continue
            if not re.search(r"гол|goal", raw, flags=re.IGNORECASE):
                continue
            name = re.sub(r"(?i)гол|goal", "", raw)
            name = re.sub(r"\b\d{1,2}\s*\(?\d*\)?\b", "", name)
            name = re.sub(r"[^A-Za-zА-Яа-яЁё\-\s]", " ", name)
            name = re.sub(r"\s+", " ", name).strip()
            if not name:
                continue
            y1_raw = int(item.get("y1", 0))
            key = f"{name.lower()}::{y1_raw // 12}"
            if key in seen:
                continue
            seen.add(key)
            side = self._classify_goal_color(
                image_bgr,
                {
                    "x1": int(item.get("x1", 0)) + offset_x,
                    "x2": int(item.get("x2", 0)) + offset_x,
                    "y1": int(item.get("y1", 0)) + offset_y,
                    "y2": int(item.get("y2", 0)) + offset_y,
                },
            )
            if side == "home":
                home_goals.append(name)
            elif side == "away":
                away_goals.append(name)
            else:
                unknown_goals.append(name)
        return {"home_goals": home_goals, "away_goals": away_goals, "unknown_goals": unknown_goals}

    def _build_ocr_payload(self, caption_info: Dict, score: Optional[Dict], goals_info: Dict) -> Dict:
        resolved = self._resolve_assists_by_goals(
            caption_info.get("chat_id"),
            caption_info.get("home_team", ""),
            caption_info.get("away_team", ""),
            caption_info.get("assists_any", []),
            goals_info.get("home_goals", []),
            goals_info.get("away_goals", []),
        )
        return {
            "home_team": caption_info["home_team"],
            "away_team": caption_info["away_team"],
            "score_home": (score or {}).get("home"),
            "score_away": (score or {}).get("away"),
            "home_goals": goals_info.get("home_goals", []),
            "away_goals": goals_info.get("away_goals", []),
            "unknown_goals": goals_info.get("unknown_goals", []),
            "home_assists": caption_info.get("home_assists", []) + resolved.get("home", []),
            "away_assists": caption_info.get("away_assists", []) + resolved.get("away", []),
            "unknown_assists": resolved.get("unknown", []),
            "assist_warnings": resolved.get("warnings", []),
        }

    def _format_ocr_draft_text(self, draft: Dict) -> str:
        payload = draft.get("payload", {})
        warnings = draft.get("warnings", [])
        score_home = payload.get("score_home")
        score_away = payload.get("score_away")
        home_goals_list = payload.get("home_goals", []) or []
        away_goals_list = payload.get("away_goals", []) or []
        unknown_goals_list = payload.get("unknown_goals", []) or []
        home_assists_list = payload.get("home_assists", []) or []
        away_assists_list = payload.get("away_assists", []) or []
        unknown_assists_list = payload.get("unknown_assists", []) or []
        lines = [
            f"🧾 OCR-черновик #{draft['ocr_id']}",
            f"Статус: {draft.get('status')}",
            f"Матч: {payload.get('home_team', '—')} - {payload.get('away_team', '—')}",
        ]

        if score_home is not None and score_away is not None:
            lines.append(f"Счет: {score_home}:{score_away}")

        if home_goals_list:
            lines.append("Голы хоз.: " + ", ".join(home_goals_list))
        if away_goals_list:
            lines.append("Голы гост.: " + ", ".join(away_goals_list))
        if unknown_goals_list:
            lines.append("Неразнесенные голы: " + ", ".join(unknown_goals_list))

        if home_assists_list or away_assists_list or unknown_assists_list:
            lines.append("Ассисты хоз.: " + (", ".join(home_assists_list) if home_assists_list else "нет"))
            lines.append("Ассисты гост.: " + (", ".join(away_assists_list) if away_assists_list else "нет"))
        if unknown_assists_list:
            lines.append("Неразнесенные ассисты: " + ", ".join(unknown_assists_list))

        if warnings:
            lines.append("⚠️ Нужна проверка:")
            lines.extend([f"- {w}" for w in warnings])
        lines.append("Исправление: исправь [id] или /league_ocr_fix [id]")
        lines.append("Подтверждает только админ.")
        return "\n".join(lines)

    def _parse_fix_payload(self, raw_text: str, chat_id: int) -> Dict:
        text = (raw_text or "").strip()
        text = re.sub(r"^/league_ocr_fix\b[^\n]*", "", text, count=1).strip()
        text = re.sub(r"^исправ[ьт]?\s*\d*\s*", "", text, count=1, flags=re.IGNORECASE).strip()
        lines = [x.strip() for x in text.splitlines()]
        non_empty = [x for x in lines if x]
        if not non_empty:
            raise ValueError("Пустой шаблон исправления.")

        m = re.match(r"^(.+?)\s*(?:-|—|vs|VS|v\.?s\.?)\s*(.+)$", non_empty[0])
        if not m:
            raise ValueError("Не найдена строка матча 'Команда1 - Команда2'.")
        home_team = m.group(1).strip()
        away_team = m.group(2).strip()
        score_home = None
        score_away = None

        sections: Dict[str, List[str]] = {}
        assists_any: List[str] = []
        current = None
        for line in non_empty[1:]:
            score_match = re.match(r"^сч[её]т\s*[:\-]?\s*(\d{1,2})\s*[-:]\s*(\d{1,2})\s*$", line, flags=re.IGNORECASE)
            if score_match:
                score_home = int(score_match.group(1))
                score_away = int(score_match.group(2))
                continue
            generic_assists = re.match(r"^ассисты\b\s*:?\s*(.*)$", line, flags=re.IGNORECASE)
            if generic_assists:
                current = "assists_any"
                inline_raw = generic_assists.group(1).strip()
                if inline_raw:
                    for token in [x.strip() for x in re.split(r"[;,]", inline_raw) if x.strip()]:
                        assists_any.extend(self._expand_person_token(token))
                continue
            h = re.match(r"^(Голы|Ассисты)\s+(.+?)\s*:\s*$", line, flags=re.IGNORECASE)
            if h:
                kind = h.group(1).lower()
                team = h.group(2).strip()
                side = "home" if SequenceMatcher(None, self.normalize_team_name(team), self.normalize_team_name(home_team)).ratio() >= SequenceMatcher(None, self.normalize_team_name(team), self.normalize_team_name(away_team)).ratio() else "away"
                current = f"{kind}_{side}"
                sections.setdefault(current, [])
                continue
            if current is None:
                # Fallback: plain lines without header are treated as assists list.
                for token in [x.strip("-• ") for x in re.split(r"[;,]", line) if x.strip("-• ")]:
                    assists_any.extend(self._expand_person_token(token))
                continue
            chunks: List[str] = []
            for token in [x.strip() for x in re.split(r"[;,]", line) if x.strip()]:
                chunks.extend(self._expand_person_token(token))
            if current == "assists_any":
                assists_any.extend(chunks)
            else:
                sections[current].extend(chunks)

        home_goals = sections.get("голы_home", [])
        away_goals = sections.get("голы_away", [])
        resolved = self._resolve_assists_by_goals(
            chat_id,
            home_team,
            away_team,
            assists_any,
            home_goals,
            away_goals,
        )
        warnings = list(resolved.get("warnings", []))

        return {
            "home_team": home_team,
            "away_team": away_team,
            "score_home": score_home,
            "score_away": score_away,
            "home_goals": home_goals,
            "away_goals": away_goals,
            "unknown_goals": [],
            "home_assists": sections.get("ассисты_home", []) + resolved.get("home", []),
            "away_assists": sections.get("ассисты_away", []) + resolved.get("away", []),
            "unknown_assists": resolved.get("unknown", []),
            "_warnings": warnings,
        }

    async def on_ocr_photo_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if not message or not chat or not user or not message.photo:
            return
        if not self._is_ocr_ready():
            await message.reply_text("❌ OCR недоступен: установите Tesseract OCR в системе и пакет pytesseract.")
            return

        caption_info = self._parse_caption_match_and_assists(message.caption or "", chat.id)
        warnings = list(caption_info.get("warnings", []))

        photo = message.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)
        image_bytes = await tg_file.download_as_bytearray()
        image_np = np.frombuffer(bytes(image_bytes), dtype=np.uint8)
        image_bgr = cv2.imdecode(image_np, cv2.IMREAD_COLOR)
        if image_bgr is None:
            await message.reply_text("❌ Не удалось прочитать изображение.")
            return

        provider = self._get_ocr_provider()
        if provider == "ocrspace":
            try:
                line_items = self._ocrspace_extract_lines(bytes(image_bytes))
            except Exception as e:
                await message.reply_text(f"❌ OCR API временно недоступен: {e}")
                return
            img_h, img_w = image_bgr.shape[:2]
            score = self._extract_score_from_line_items(line_items, img_w, img_h)
            goals_info = self._extract_goal_scorers(image_bgr, line_items, offset_x=0, offset_y=0)
        else:
            height, width = image_bgr.shape[:2]
            score_roi = image_bgr[0 : max(int(height * 0.26), 1), max(int(width * 0.2), 0) : min(int(width * 0.8), width)]
            events_x1 = max(int(width * 0.48), 0)
            events_y1 = max(int(height * 0.18), 0)
            events_roi = image_bgr[events_y1 : min(int(height * 0.92), height), events_x1:width]

            score_lines = self._ocr_extract_lines(score_roi if score_roi.size else image_bgr, psm=6)
            event_lines = self._ocr_extract_lines(events_roi if events_roi.size else image_bgr, psm=6)

            score = self._extract_score_from_image(image_bgr)
            if not score:
                score = self._extract_score([str(x.get("text", "")) for x in score_lines])
            if not score:
                score = self._extract_score([str(x.get("text", "")) for x in event_lines])
            goals_info = self._extract_goal_scorers(image_bgr, event_lines, offset_x=events_x1, offset_y=events_y1)

        goals_info = self._resolve_goals_by_players(
            chat.id,
            caption_info.get("home_team", ""),
            caption_info.get("away_team", ""),
            goals_info,
        )

        if not score:
            if not goals_info.get("unknown_goals") and (goals_info.get("home_goals") or goals_info.get("away_goals")):
                score = {
                    "home": len(goals_info.get("home_goals", [])),
                    "away": len(goals_info.get("away_goals", [])),
                }
                warnings.append("Счет восстановлен по количеству распознанных голов.")
            else:
                warnings.append("Счет не распознан автоматически.")
        if goals_info.get("unknown_goals"):
            warnings.append("Есть нераспределенные голы. Нужна команда /league_ocr_fix.")

        payload = self._build_ocr_payload(caption_info, score, goals_info)
        warnings.extend(payload.get("assist_warnings", []))
        ocr_id = self.db.create_ocr_draft(chat.id, message.message_id, user.id, payload, warnings)
        draft = self.db.get_ocr_draft(chat.id, ocr_id)
        if not draft:
            await message.reply_text("❌ Ошибка сохранения OCR-черновика.")
            return

        await message.reply_text(
            self._format_ocr_draft_text(draft),
            reply_markup=self._build_ocr_keyboard(chat.id, ocr_id),
        )

    async def on_ocr_fix_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        if not message:
            return
        text = (message.text or "").strip()
        if not re.match(r"^исправ[ьт]?\b", text, flags=re.IGNORECASE):
            return
        await self._apply_ocr_fix(update, context, from_text=True)

    async def _apply_ocr_fix(self, update: Update, context: ContextTypes.DEFAULT_TYPE, from_text: bool = False):
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        if not message or not user or not chat:
            return
        draft_id = self._resolve_draft_id(update, context.args if not from_text else None)
        if draft_id is None:
            if from_text:
                m = re.match(r"^исправ[ьт]?\s*(\d+)?", message.text or "", flags=re.IGNORECASE)
                draft_id = int(m.group(1)) if m and m.group(1) else self._resolve_draft_id(update, None)
            if draft_id is None:
                await message.reply_text("❌ Не указан ID черновика. Используйте: исправь [id] или reply на сообщение черновика.")
                return

        draft = self.db.get_ocr_draft(chat.id, draft_id)
        if not draft:
            await message.reply_text(f"❌ Черновик #{draft_id} не найден.")
            return
        if draft.get("status") == "approved":
            await message.reply_text(f"❌ Черновик #{draft_id} уже подтвержден и недоступен для правок.")
            return

        try:
            fixed_payload = self._parse_fix_payload(message.text or "", chat.id)
        except ValueError as e:
            await message.reply_text(f"❌ Ошибка формата: {e}")
            return

        existing_payload = draft.get("payload", {})
        if fixed_payload.get("score_home") is None:
            fixed_payload["score_home"] = existing_payload.get("score_home")
        if fixed_payload.get("score_away") is None:
            fixed_payload["score_away"] = existing_payload.get("score_away")
        warnings = fixed_payload.pop("_warnings", [])
        updated = self.db.update_ocr_draft_payload(chat.id, draft_id, fixed_payload, warnings, user.id)
        if not updated:
            await message.reply_text("❌ Не удалось сохранить правку.")
            return
        updated_draft = self.db.get_ocr_draft(chat.id, draft_id)
        if not updated_draft:
            await message.reply_text("❌ Не удалось получить обновленный черновик.")
            return
        await message.reply_text(
            f"✅ Правка для черновика #{draft_id} сохранена. Ожидает подтверждения админа."
        )
        await message.reply_text(
            self._format_ocr_draft_text(updated_draft),
            reply_markup=self._build_ocr_keyboard(chat.id, draft_id),
        )

    async def on_ocr_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user = update.effective_user
        chat = update.effective_chat
        if not query or not user or not chat:
            return
        data = query.data or ""
        parts = data.split(":")
        if len(parts) != 4:
            await query.answer("Некорректная кнопка", show_alert=True)
            return
        _, action, chat_id_raw, draft_id_raw = parts
        try:
            chat_id = int(chat_id_raw)
            draft_id = int(draft_id_raw)
        except Exception:
            await query.answer("Некорректный ID", show_alert=True)
            return
        if chat.id != chat_id:
            await query.answer("Эта кнопка из другого чата", show_alert=True)
            return
        draft = self.db.get_ocr_draft(chat.id, draft_id)
        if not draft:
            await query.answer("Черновик не найден", show_alert=True)
            return

        if action == "show":
            if not self._is_admin(user.id):
                await query.answer("Только админ", show_alert=True)
                return
            await query.answer("Показываю")
            await query.message.reply_text(
                self._format_ocr_draft_text(draft),
                reply_markup=self._build_ocr_keyboard(chat.id, draft_id),
            )
            return

        if action == "fix":
            await query.answer("Отправьте исправление")
            await query.message.reply_text(
                "Шаблон исправления:\n"
                f"исправь {draft_id}\n"
                "Команда1 - Команда2\n"
                "Счет: 2-1\n"
                "Голы Команда1: Игрок1, Игрок2\n"
                "Голы Команда2: Игрок3\n"
                "Ассисты: Игрок1, Игрок3"
            )
            return

        if not self._is_admin(user.id):
            await query.answer("Только админ", show_alert=True)
            return

        if action == "approve":
            ok = self.db.approve_ocr_draft(chat.id, draft_id, user.id)
            await query.answer("Подтверждено" if ok else "Не удалось подтвердить", show_alert=not ok)
            refreshed = self.db.get_ocr_draft(chat.id, draft_id)
            if refreshed:
                await query.message.reply_text(self._format_ocr_draft_text(refreshed))
            return

        if action == "reject":
            ok = self.db.reject_ocr_draft(chat.id, draft_id, user.id, "Отклонено администратором")
            await query.answer("Отклонено" if ok else "Не удалось отклонить", show_alert=not ok)
            refreshed = self.db.get_ocr_draft(chat.id, draft_id)
            if refreshed:
                await query.message.reply_text(self._format_ocr_draft_text(refreshed))
            return

    # --- Commands (copy these into your bot class if needed) ---
    async def cmd_league_debts_show(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        await update.message.reply_text(self.build_league_summary_text(update.effective_chat.id))

    async def cmd_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            return
        await update.message.reply_text(
            "\n".join(
                [
                    "Доступные команды:",
                    "/admin - список доступных команд",
                    "/league_debts_show - показать сводку долгов по игрокам",
                    "/league_debts_round [N] - показать долги за конкретный тур",
                    "/league_map_bulk [список] - массово задать привязки Команда - @username",
                    "/league_map_show - показать текущие привязки команд",
                    "/league_map_clear - очистить все привязки команд",
                    "/league_players_seed - загрузить базу футболистов для этой лиги",
                    "/league_sync_challenge [url] [N] - синк долгов из challenge.place до тура N",
                    "/league_sync_now [N] - повторить синк из сохраненного источника",
                    "/league_sync_off - отключить сохраненный источник синка",
                    "/league_reminder_on - включить напоминания каждые 4 часа (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 МСК)",
                    "/league_reminder_off - выключить ежедневные напоминания",
                    "/league_reminder_now - отправить напоминание сразу",
                    "/league_reminder_hourly_on [текст] - включить ежечасные напоминания в :00",
                    "/league_reminder_hourly_off - выключить ежечасные напоминания",
                    "/league_ocr_fix [id] - исправить OCR-черновик (доступно игрокам)",
                    "/league_ocr_show [id] - показать OCR-черновик",
                    "/league_ocr_approve [id] - подтвердить OCR-черновик",
                    "/league_ocr_reject [id] [причина] - отклонить OCR-черновик",
                    "/league_apply_result [id] [match_url] [--dry-run] [--force] - внести подтвержденный результат на сайт",
                ]
            )
        )

    async def cmd_league_debts_round(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        if not context.args:
            await update.message.reply_text("Использование: /league_debts_round [номер тура]")
            return
        try:
            round_num = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Номер тура должен быть числом.")
            return
        await update.message.reply_text(self.format_league_debts_round(update.effective_chat.id, round_num))

    async def cmd_league_map_bulk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        message_text = (update.effective_message.text or "").strip()
        if "\n" in message_text:
            payload = message_text.split("\n", 1)[1].strip()
        else:
            payload = " ".join(context.args).strip()
        if not payload:
            await update.message.reply_text("Использование: /league_map_bulk [список]")
            return
        mappings = self.parse_league_map_bulk_text(payload)
        if not mappings:
            await update.message.reply_text("Не удалось распознать ни одной строки.")
            return
        self.db.replace_league_team_map(update.effective_chat.id, mappings)
        await update.message.reply_text(f"✅ Обновил привязки клубов: {len(mappings)}")

    async def cmd_league_map_show(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        items = self.db.get_league_team_map(update.effective_chat.id)
        if not items:
            await update.message.reply_text("Привязки клубов пустые.")
            return
        lines = ["📌 Привязки клубов:", ""]
        lines.extend([f"{i}) {x['team_name_raw']} - @{x['telegram_username']}" for i, x in enumerate(items, 1)])
        await update.message.reply_text("\n".join(lines))

    async def cmd_league_map_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        self.db.clear_league_team_map(update.effective_chat.id)
        await update.message.reply_text("✅ Привязки клубов очищены.")

    async def cmd_league_players_seed(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return

        chat_id = update.effective_chat.id
        team_map = {x["team_name_norm"]: x["team_name_raw"] for x in self.db.get_league_team_map(chat_id)}
        seed = self.default_league_players_seed()

        rows = []
        teams_loaded = 0
        players_loaded = 0
        for team_raw, players in seed.items():
            team_norm = self.normalize_team_name(team_raw)
            mapped_raw = team_map.get(team_norm, team_raw)
            if not players:
                continue
            teams_loaded += 1
            for player in players:
                player_raw = (player or "").strip()
                if not player_raw:
                    continue
                rows.append(
                    {
                        "team_name_norm": team_norm,
                        "team_name_raw": mapped_raw,
                        "player_name_norm": self._normalize_player_name(player_raw),
                        "player_name_raw": player_raw,
                    }
                )
                players_loaded += 1

        self.db.replace_league_team_players(chat_id, rows)
        await update.message.reply_text(
            f"✅ База футболистов загружена для этой лиги. Команд: {teams_loaded}, игроков: {players_loaded}."
        )

    async def cmd_league_sync_challenge(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        if len(context.args) < 2:
            await update.message.reply_text("Использование: /league_sync_challenge <stage_url> <max_round>")
            return
        stage_url = context.args[0].strip()
        try:
            max_round = int(context.args[1])
        except ValueError:
            await update.message.reply_text("max_round должен быть числом.")
            return
        chat_id = update.effective_chat.id
        try:
            result = self.sync_challenge_stage_debts(chat_id, stage_url, max_round)
            await update.message.reply_text(
                f"✅ Синк выполнен до {max_round} тура включительно.\n"
                "Старые долги очищены, записаны актуальные данные после синка.\n"
                f"Записей долгов: {result['entries_count']}\n"
                f"Неразобранных матчей: {result['unresolved_matches']}"
            )
            if result["unresolved_teams"]:
                unresolved_text = "\n".join([f"- {team}" for team in result["unresolved_teams"]])
                await update.message.reply_text("⚠️ Команды без привязки к @username:\n" + unresolved_text)
            await update.message.reply_text(self.format_league_debts_post(chat_id))
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка синка: {e}")

    async def cmd_league_sync_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        chat_id = update.effective_chat.id
        source = self.db.get_league_challenge_source(chat_id)
        if not source or not source.get("enabled"):
            await update.message.reply_text("Источник не настроен. Используйте /league_sync_challenge <stage_url> <max_round>.")
            return
        max_round = source.get("max_round", 0)
        if context.args:
            try:
                max_round = int(context.args[0])
            except ValueError:
                await update.message.reply_text("max_round должен быть числом.")
                return
        try:
            result = self.sync_challenge_stage_debts(chat_id, source["stage_url"], max_round)
            await update.message.reply_text(
                f"✅ Повторный синк выполнен до {max_round} тура включительно.\n"
                "Старые долги очищены, записаны актуальные данные после синка.\n"
                f"Записей долгов: {result['entries_count']}"
            )
            await update.message.reply_text(self.format_league_debts_post(chat_id))
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка синка: {e}")

    async def cmd_league_sync_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        self.db.disable_league_challenge_source(update.effective_chat.id)
        await update.message.reply_text("✅ Источник синка Challenge отключен.")

    async def cmd_league_ocr_fix(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._apply_ocr_fix(update, context, from_text=False)

    async def cmd_league_ocr_show(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        draft_id = self._resolve_draft_id(update, context.args)
        if draft_id is None:
            await update.message.reply_text("Использование: /league_ocr_show [id] или reply на сообщение черновика.")
            return
        draft = self.db.get_ocr_draft(update.effective_chat.id, draft_id)
        if not draft:
            await update.message.reply_text(f"❌ Черновик #{draft_id} не найден.")
            return
        await update.message.reply_text(
            self._format_ocr_draft_text(draft),
            reply_markup=self._build_ocr_keyboard(update.effective_chat.id, draft_id),
        )

    async def cmd_league_ocr_approve(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        draft_id = self._resolve_draft_id(update, context.args)
        if draft_id is None:
            await update.message.reply_text("Использование: /league_ocr_approve [id] или reply на сообщение черновика.")
            return
        ok = self.db.approve_ocr_draft(update.effective_chat.id, draft_id, update.effective_user.id)
        if not ok:
            await update.message.reply_text(f"❌ Не удалось подтвердить черновик #{draft_id} (возможно уже подтвержден/отклонен).")
            return
        draft = self.db.get_ocr_draft(update.effective_chat.id, draft_id)
        await update.message.reply_text(f"✅ Черновик #{draft_id} подтвержден админом.")
        if draft:
            await update.message.reply_text(self._format_ocr_draft_text(draft))

    async def cmd_league_ocr_reject(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        draft_id = self._resolve_draft_id(update, context.args)
        if draft_id is None:
            await update.message.reply_text("Использование: /league_ocr_reject [id] [причина] или reply на сообщение черновика.")
            return
        reason = " ".join(context.args[1:]).strip() if len(context.args) > 1 else "Отклонено администратором"
        ok = self.db.reject_ocr_draft(update.effective_chat.id, draft_id, update.effective_user.id, reason)
        if not ok:
            await update.message.reply_text(f"❌ Не удалось отклонить черновик #{draft_id}.")
            return
        draft = self.db.get_ocr_draft(update.effective_chat.id, draft_id)
        await update.message.reply_text(f"🛑 Черновик #{draft_id} отклонен. Причина: {reason}")
        if draft:
            await update.message.reply_text(self._format_ocr_draft_text(draft))

    async def cmd_league_apply_result(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return

        args = list(context.args or [])
        dry_run = "--dry-run" in args
        force = "--force" in args
        clean_args = [x for x in args if x not in {"--dry-run", "--force"}]
        draft_id = self._resolve_draft_id(update, clean_args)
        if draft_id is None:
            await update.message.reply_text("Использование: /league_apply_result [id] [match_url] [--dry-run] [--force]")
            return

        manual_match_url = ""
        for token in clean_args[1:]:
            if re.match(r"^https?://", token or "", flags=re.IGNORECASE):
                manual_match_url = token.strip()
                break

        self.logger.info(
            "Command /league_apply_result: chat_id=%s user_id=%s draft_id=%s dry_run=%s force=%s manual_url=%s",
            update.effective_chat.id if update.effective_chat else None,
            update.effective_user.id if update.effective_user else None,
            draft_id,
            dry_run,
            force,
            bool(manual_match_url),
        )

        chat_id = update.effective_chat.id
        draft = self.db.get_ocr_draft(chat_id, draft_id)
        if not draft:
            await update.message.reply_text(f"❌ Черновик #{draft_id} не найден.")
            return
        if draft.get("status") != "approved":
            await update.message.reply_text(f"❌ Черновик #{draft_id} не подтвержден админом.")
            return

        already = self.db.get_ocr_applied(chat_id, draft_id)
        if already and already.get("status") == "success" and not force and not dry_run:
            await update.message.reply_text(
                f"⚠️ Черновик #{draft_id} уже был применен. Используйте --force для повторного применения."
            )
            return

        payload = draft.get("payload", {})
        home_team = payload.get("home_team")
        away_team = payload.get("away_team")
        if not home_team or not away_team:
            await update.message.reply_text("❌ В черновике отсутствуют команды.")
            return

        await update.message.reply_text("⏳ Ищу матч по командам...")
        selected = None
        if manual_match_url:
            selected = self._extract_match_teams_from_url(manual_match_url)
            if not selected:
                self.db.upsert_ocr_applied(chat_id, draft_id, manual_match_url, "failed", "Не удалось прочитать данные матча по ссылке")
                await update.message.reply_text("❌ Не удалось прочитать команды матча по переданной ссылке.")
                return
        else:
            candidates = self._find_match_candidates_by_teams(chat_id, home_team, away_team)
            selected = self._select_candidate_min_round(candidates)
        if not selected:
            self.db.upsert_ocr_applied(chat_id, draft_id, "", "failed", "Матч не найден по паре команд")
            self.logger.warning(
                "Match not found for draft_id=%s home=%s away=%s",
                draft_id,
                home_team,
                away_team,
            )
            await update.message.reply_text(
                "❌ Не удалось найти матч по паре команд в источнике лиги. "
                "Можно указать ссылку вручную: /league_apply_result [id] [match_url]"
            )
            return

        mapped = self._map_payload_to_site_sides(payload, selected.get("home_team", ""), selected.get("away_team", ""))
        round_info = selected.get("round_name") or (f"тур {selected.get('round_num')}" if selected.get("round_num") else "тур не определен")
        await update.message.reply_text(
            f"🔎 Выбран матч: {round_info}\n{selected.get('match_url')}\n"
            + ("↔️ Стороны переставлены под сайт." if mapped.get("swapped") else "✅ Стороны совпали.")
        )

        result = await self._open_match_and_fill_result(selected.get("match_url", ""), mapped, dry_run=dry_run)
        status = "success" if result.get("ok") else "failed"
        status = "dry_run" if dry_run and result.get("ok") else status
        self.db.upsert_ocr_applied(chat_id, draft_id, selected.get("match_url", ""), status, result.get("message", ""))
        self.logger.info(
            "Apply result finished: draft_id=%s status=%s ok=%s message=%s",
            draft_id,
            status,
            result.get("ok"),
            result.get("message", ""),
        )

        if result.get("ok"):
            await update.message.reply_text(
                f"✅ {'Dry-run завершен' if dry_run else 'Результат отправлен на сайт'} для черновика #{draft_id}.\n"
                f"{result.get('message', '')}"
            )
        else:
            await update.message.reply_text(f"❌ Не удалось применить черновик #{draft_id}: {result.get('message', 'неизвестная ошибка')}")

    async def cmd_league_reminder_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        self.db.set_league_reminder_enabled(update.effective_chat.id, True)
        await update.message.reply_text(
            "✅ Авто-напоминания включены: каждые 4 часа (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 Europe/Moscow)."
        )

    async def cmd_league_reminder_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        self.db.set_league_reminder_enabled(update.effective_chat.id, False)
        await update.message.reply_text("✅ Авто-напоминания выключены.")

    async def cmd_league_reminder_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        settings = self.db.get_league_reminder_settings(update.effective_chat.id)
        threshold = settings.get("threshold", 2)
        sent = await self.send_league_reminder_message(update.effective_chat.id, threshold=threshold, bot=context.bot)
        await update.message.reply_text(
            "✅ Напоминание отправлено." if sent else f"Нет игроков с долгами >= {threshold}."
        )

    async def cmd_league_reminder_hourly_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        text = " ".join(context.args).strip() or "Напоминание: сыграйте долги в лиге."
        self.db.set_league_hourly_reminder(update.effective_chat.id, True, text)
        await update.message.reply_text(
            "✅ Ежечасное напоминание включено.\nОтправка: каждый час в :00 (Europe/Moscow).\n"
            f"Текст: {text}"
        )

    async def cmd_league_reminder_hourly_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        self.db.set_league_hourly_reminder(update.effective_chat.id, False, None)
        await update.message.reply_text("✅ Ежечасное напоминание выключено.")


def _parse_admin_ids(raw: str) -> set[str]:
    return {value.strip() for value in str(raw or "").split(",") if value.strip()}


def _clean_env_value(raw: str | None) -> str:
    value = str(raw or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1].strip()
    value = re.sub(r"\s+", "", value)
    return value


def run_bot():
    load_dotenv()
    logging.basicConfig(
        level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    token = _clean_env_value(os.getenv("BOT_TOKEN")) or _clean_env_value(
        os.getenv("TELEGRAM_BOT_TOKEN")
    )
    if not token:
        raise RuntimeError("Set BOT_TOKEN or TELEGRAM_BOT_TOKEN")

    admin_ids = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))
    database_url = (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or "").strip()
    if database_url:
        try:
            import psycopg

            connection = psycopg.connect(database_url)
            raw_cursor = connection.cursor()
            cursor = PostgresCursorAdapter(raw_cursor)
            repo = LeagueRepositoryPostgres(connection, cursor)
            print("Using PostgreSQL storage")
        except Exception as e:
            print(f"PostgreSQL unavailable, fallback to SQLite: {e}")
            db_path = os.getenv("LEAGUE_SQLITE_PATH", "league.db")
            connection = sqlite3.connect(db_path, check_same_thread=False)
            cursor = connection.cursor()
            repo = LeagueRepositorySQLite(connection, cursor)
    else:
        db_path = os.getenv("LEAGUE_SQLITE_PATH", "league.db")
        connection = sqlite3.connect(db_path, check_same_thread=False)
        cursor = connection.cursor()
        repo = LeagueRepositorySQLite(connection, cursor)

    repo.create_tables()

    application = ApplicationBuilder().token(token).build()
    feature = LeagueFeature(
        db=repo,
        moscow_tz=ZoneInfo("Europe/Moscow"),
        is_admin_callable=lambda user_id: str(user_id) in admin_ids,
        application=application,
    )
    feature.register_handlers(application)
    application.add_error_handler(feature.on_error)
    feature.setup_jobs(application, logging.getLogger("league_bot"))

    print(f"League bot started via league_module.py (admins: {len(admin_ids)})")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run_bot()
