import json
import logging
import os
import re
import sqlite3
import unicodedata
import urllib.request
from datetime import datetime, time as time_module
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

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
                threshold INTEGER DEFAULT 2,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        try:
            self.cursor.execute("ALTER TABLE league_reminder_settings ADD COLUMN threshold INTEGER DEFAULT 2")
        except Exception:
            pass

        for col in ("timezone", "hourly_enabled", "hourly_text"):
            try:
                self.cursor.execute(f"ALTER TABLE league_reminder_settings DROP COLUMN IF EXISTS {col}")
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


        # Remove stage-2 / OCR artifacts from DB schema.
        self.cursor.execute("DROP TABLE IF EXISTS league_challenge_sources")
        self.cursor.execute("DROP TABLE IF EXISTS league_ocr_drafts")
        self.cursor.execute("DROP TABLE IF EXISTS league_ocr_applied")

        self.conn.commit()

    def replace_league_debts(self, chat_id: int, entries: List[Dict]):
        if not entries:
            return
        self.cursor.execute("BEGIN IMMEDIATE")
        try:
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
        except Exception:
            self.conn.rollback()
            raise

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
            INSERT INTO league_reminder_settings (chat_id, enabled, threshold, updated_at)
            VALUES (?, ?, 2, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET enabled = excluded.enabled, updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, 1 if enabled else 0),
        )
        self.conn.commit()


    def get_league_reminder_settings(self, chat_id: int) -> Dict:
        self.cursor.execute(
            """
            SELECT chat_id, enabled, threshold
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
                "threshold": 2,
            }
        return {
            "chat_id": row[0],
            "enabled": row[1],
            "threshold": row[2],
        }

    def get_enabled_league_reminder_chats(self) -> List[Dict]:
        self.cursor.execute(
            """
            SELECT chat_id, enabled, threshold
            FROM league_reminder_settings
            WHERE enabled = 1
            """
        )
        return [
            {
                "chat_id": r[0],
                "enabled": r[1],
                "threshold": r[2],
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
        # Backward-compatible migration for legacy schemas (old table without chat_id).
        self.cursor.execute("ALTER TABLE league_debt_entries ADD COLUMN IF NOT EXISTS chat_id BIGINT")
        self.cursor.execute("ALTER TABLE league_debt_entries ADD COLUMN IF NOT EXISTS round_label TEXT")
        self.cursor.execute("ALTER TABLE league_debt_entries ADD COLUMN IF NOT EXISTS debtor_username TEXT")
        self.cursor.execute("ALTER TABLE league_debt_entries ADD COLUMN IF NOT EXISTS opponent_username TEXT")
        self.cursor.execute("ALTER TABLE league_debt_entries ADD COLUMN IF NOT EXISTS raw_line TEXT")
        self.cursor.execute(
            "ALTER TABLE league_debt_entries ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        )

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS league_reminder_settings (
                chat_id BIGINT PRIMARY KEY,
                enabled INTEGER DEFAULT 0,
                threshold INTEGER DEFAULT 2,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        self.cursor.execute(
            "ALTER TABLE league_reminder_settings ADD COLUMN IF NOT EXISTS enabled INTEGER DEFAULT 0"
        )
        self.cursor.execute(
            "ALTER TABLE league_reminder_settings ADD COLUMN IF NOT EXISTS threshold INTEGER DEFAULT 2"
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

        # Remove stage-2 / OCR artifacts from DB schema.
        self.cursor.execute("DROP TABLE IF EXISTS league_challenge_sources")
        self.cursor.execute("DROP TABLE IF EXISTS league_ocr_drafts")
        self.cursor.execute("DROP TABLE IF EXISTS league_ocr_applied")

        self.conn.commit()

    def _column_meta(self, table_name: str, column_name: str) -> Dict:
        try:
            self.cursor.execute(
                """
                SELECT data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = ?
                  AND column_name = ?
                """,
                (table_name, column_name),
            )
            row = self.cursor.fetchone()
            if not row:
                return {"exists": False, "data_type": "", "not_null": False}
            return {
                "exists": True,
                "data_type": (row[0] or "").lower(),
                "not_null": str(row[1] or "").upper() == "NO",
            }
        except Exception:
            return {"exists": False, "data_type": "", "not_null": False}

    def _table_columns_meta(self, table_name: str) -> Dict[str, Dict]:
        try:
            self.cursor.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = ?
                """,
                (table_name,),
            )
            out: Dict[str, Dict] = {}
            for row in self.cursor.fetchall():
                out[str(row[0])] = {
                    "data_type": (row[1] or "").lower(),
                    "not_null": str(row[2] or "").upper() == "NO",
                }
            return out
        except Exception:
            return {}

    def replace_league_debts(self, chat_id: int, entries: List[Dict]):
        try:
            self.cursor.execute("DELETE FROM league_debt_entries WHERE chat_id = ?", (chat_id,))

            round_no_meta = self._column_meta("league_debt_entries", "round_no")
            need_round_no = bool(round_no_meta.get("exists") and round_no_meta.get("not_null"))

            pair_key_meta = self._column_meta("league_debt_entries", "pair_key")
            need_pair_key = bool(pair_key_meta.get("exists") and pair_key_meta.get("not_null"))

            team_a_norm_meta = self._column_meta("league_debt_entries", "team_a_norm")
            team_b_norm_meta = self._column_meta("league_debt_entries", "team_b_norm")
            team_a_raw_meta = self._column_meta("league_debt_entries", "team_a_raw")
            team_b_raw_meta = self._column_meta("league_debt_entries", "team_b_raw")

            need_team_a_norm = bool(team_a_norm_meta.get("exists") and team_a_norm_meta.get("not_null"))
            need_team_b_norm = bool(team_b_norm_meta.get("exists") and team_b_norm_meta.get("not_null"))
            need_team_a_raw = bool(team_a_raw_meta.get("exists") and team_a_raw_meta.get("not_null"))
            need_team_b_raw = bool(team_b_raw_meta.get("exists") and team_b_raw_meta.get("not_null"))

            all_meta = self._table_columns_meta("league_debt_entries")

            def _norm_team(value: str) -> str:
                normalized = (value or "").strip().lower()
                normalized = normalized.replace("ё", "е").replace("ë", "е")
                normalized = re.sub(r"\s+", " ", normalized)
                return normalized

            def _extract_teams_from_raw(raw_line: str) -> tuple[str, str]:
                text = str(raw_line or "")
                teams = re.findall(r"\(([^()]+)\)", text)
                if len(teams) >= 2:
                    return teams[0].strip(), teams[1].strip()
                return "", ""

            for e in entries:
                round_label = e.get("round_label")
                debtor = e.get("debtor_username")
                opponent = e.get("opponent_username")
                raw_line = e.get("raw_line")

                columns = ["chat_id", "round_label", "debtor_username", "opponent_username", "raw_line"]
                values = [chat_id, round_label, debtor, opponent, raw_line]

                if need_round_no:
                    round_no = 0
                    m = re.search(r"(\d+)", str(round_label or ""))
                    if m:
                        try:
                            round_no = int(m.group(1))
                        except Exception:
                            round_no = 0
                    columns.append("round_no")
                    values.append(round_no)

                if need_pair_key:
                    left = str(debtor or "").strip().lower().lstrip("@")
                    right = str(opponent or "").strip().lower().lstrip("@")
                    if left and right:
                        # Directed debt: same undirected pair appears twice (A owes B, B owes A).
                        # Legacy UNIQUE(round_no, pair_key) requires distinct keys per row.
                        pair_key = f"{left}>>{right}"
                    else:
                        pair_key = (left or right or str(raw_line or "").strip().lower() or "unknown")
                    columns.append("pair_key")
                    values.append(pair_key)

                team_a_raw, team_b_raw = _extract_teams_from_raw(raw_line)
                if need_team_a_raw:
                    columns.append("team_a_raw")
                    values.append(team_a_raw or str(debtor or ""))
                if need_team_b_raw:
                    columns.append("team_b_raw")
                    values.append(team_b_raw or str(opponent or ""))
                if need_team_a_norm:
                    columns.append("team_a_norm")
                    values.append(_norm_team(team_a_raw or str(debtor or "")) or "unknown")
                if need_team_b_norm:
                    columns.append("team_b_norm")
                    values.append(_norm_team(team_b_raw or str(opponent or "")) or "unknown")

                # Catch-all for unknown legacy NOT NULL columns.
                existing = set(columns)
                for col_name, meta in all_meta.items():
                    if not meta.get("not_null"):
                        continue
                    if col_name in existing:
                        continue
                    if col_name in {"id", "created_at", "updated_at"}:
                        continue
                    data_type = str(meta.get("data_type") or "")
                    columns.append(col_name)
                    if "bool" in data_type:
                        values.append(False)
                    elif any(token in data_type for token in ["int", "numeric", "double", "real"]):
                        values.append(0)
                    else:
                        values.append("")

                placeholders = ", ".join(["?"] * len(values))
                cols_sql = ", ".join(columns)
                self.cursor.execute("SAVEPOINT league_debt_row_sp")
                try:
                    self.cursor.execute(
                        f"INSERT INTO league_debt_entries ({cols_sql}) VALUES ({placeholders})",
                        tuple(values),
                    )
                    self.cursor.execute("RELEASE SAVEPOINT league_debt_row_sp")
                except Exception:
                    self.cursor.execute("ROLLBACK TO SAVEPOINT league_debt_row_sp")
                    self.cursor.execute("RELEASE SAVEPOINT league_debt_row_sp")

                    # Legacy fallback: some schemas enforce UNIQUE(round_no, pair_key)
                    # without chat_id; update existing row instead of failing sync.
                    if need_round_no and need_pair_key and "round_no" in columns and "pair_key" in columns:
                        row_data = dict(zip(columns, values))
                        assignments = []
                        params: List = []
                        for col in columns:
                            if col in {"round_no", "pair_key"}:
                                continue
                            assignments.append(f"{col} = ?")
                            params.append(row_data[col])
                        if assignments:
                            where_extra = ""
                            where_params: List = [row_data["round_no"], row_data["pair_key"]]
                            if "chat_id" in all_meta:
                                where_extra = " AND chat_id = ?"
                                where_params.append(row_data["chat_id"])
                            params.extend(where_params)
                            self.cursor.execute(
                                f"UPDATE league_debt_entries SET {', '.join(assignments)} WHERE round_no = ? AND pair_key = ?{where_extra}",
                                tuple(params),
                            )
                            if self.cursor.rowcount > 0:
                                continue
                    raise
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _reminder_enabled_literal(self, column_name: str, enabled: bool) -> str:
        try:
            self.cursor.execute(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_name = 'league_reminder_settings'
                  AND column_name = ?
                  AND table_schema = current_schema()
                """,
                (column_name,),
            )
            row = self.cursor.fetchone()
            data_type = (row[0] if row else "").lower()
            if data_type == "boolean":
                return "TRUE" if enabled else "FALSE"
        except Exception:
            pass
        return "1" if enabled else "0"

    def set_league_reminder_enabled(self, chat_id: int, enabled: bool):
        enabled_literal = self._reminder_enabled_literal("enabled", enabled)
        self.cursor.execute(
            f"""
            UPDATE league_reminder_settings
            SET enabled = {enabled_literal},
                updated_at = CURRENT_TIMESTAMP
            WHERE chat_id = ?
            """,
            (chat_id,),
        )
        if self.cursor.rowcount <= 0:
            self.cursor.execute(
                f"""
                INSERT INTO league_reminder_settings
                    (chat_id, enabled, threshold, updated_at)
                VALUES
                    (?, {enabled_literal}, 2, CURRENT_TIMESTAMP)
                """,
                (chat_id,),
            )
        self.conn.commit()

    def get_enabled_league_reminder_chats(self) -> List[Dict]:
        self.cursor.execute(
            """
            SELECT chat_id, enabled, threshold
            FROM league_reminder_settings
            WHERE LOWER(COALESCE(enabled::text, '')) IN ('1', 't', 'true')
            """
        )
        return [
            {
                "chat_id": r[0],
                "enabled": r[1],
                "threshold": r[2],
            }
            for r in self.cursor.fetchall()
        ]

    def _sync_table_id_sequence(self, table_name: str):
        allowed = {
            "league_team_map",
            "league_team_players",
            "league_debt_entries",
        }
        if table_name not in allowed:
            return
        try:
            self.cursor.execute(
                f"""
                SELECT setval(
                    pg_get_serial_sequence('{table_name}', 'id'),
                    COALESCE((SELECT MAX(id) FROM {table_name}), 1),
                    COALESCE((SELECT MAX(id) FROM {table_name}), 0) > 0
                )
                """,
            )
        except Exception:
            # best-effort only; rollback if probe failed to avoid aborted tx state
            try:
                self.conn.rollback()
            except Exception:
                pass

    def replace_league_team_map(self, chat_id: int, mappings: List[Dict]):
        try:
            self.cursor.execute("DELETE FROM league_team_map WHERE chat_id = ?", (chat_id,))
            self._sync_table_id_sequence("league_team_map")
            for item in mappings:
                team_name_norm = item["team_name_norm"]
                team_name_raw = item["team_name_raw"]
                telegram_username = item["telegram_username"]

                # Legacy-safe write path:
                # some old schemas have unique/PK on team_name_norm only.
                self.cursor.execute("SAVEPOINT league_map_row_sp")
                try:
                    self.cursor.execute(
                        """
                        INSERT INTO league_team_map (chat_id, team_name_norm, team_name_raw, telegram_username)
                        VALUES (?, ?, ?, ?)
                        """,
                        (chat_id, team_name_norm, team_name_raw, telegram_username),
                    )
                    self.cursor.execute("RELEASE SAVEPOINT league_map_row_sp")
                except Exception:
                    self.cursor.execute("ROLLBACK TO SAVEPOINT league_map_row_sp")
                    self.cursor.execute("RELEASE SAVEPOINT league_map_row_sp")
                    # Fallback for unique(team_name_norm) legacy schemas.
                    self.cursor.execute(
                        """
                        UPDATE league_team_map
                        SET chat_id = ?, team_name_raw = ?, telegram_username = ?
                        WHERE team_name_norm = ?
                        """,
                        (chat_id, team_name_raw, telegram_username, team_name_norm),
                    )
                    if self.cursor.rowcount <= 0:
                        raise
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


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
        self._is_admin = is_admin_callable
        self.application = application
        self.logger = logging.getLogger("league_bot")

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

    async def _on_text_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (update.message.text or "").strip()
        if not text:
            return

        first_line = text.split("\n", 1)[0].strip()
        parts = first_line.split()
        if not parts:
            return
        cmd = parts[0].strip()

        if cmd == "+":
            if len(parts) < 2:
                return
            subcmd = parts[1].strip().lower()
            if subcmd == "долги":
                rest = first_line[len(parts[0]) + len(parts[1]) + 2:].strip()
                body = rest
                if not rest and len(text.split("\n", 1)) > 1:
                    body = text.split("\n", 1)[1].strip()
                await self._handle_debts_command(update, body)
                return
            if subcmd == "команды":
                rest = first_line[len(parts[0]) + len(parts[1]) + 2:].strip()
                body = rest
                if not rest and len(text.split("\n", 1)) > 1:
                    body = "\n".join(text.split("\n", 1)[1:]).strip()
                await self._handle_commands_command(update, body)
                return

    async def _handle_debts_command(self, update: Update, body: str):
        url = None
        max_round = None
        for line in body.split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if parts[0].startswith("http://") or parts[0].startswith("https://"):
                if url is None:
                    url = parts[0]
                tokens = parts[1:]
            else:
                tokens = parts
            for token in tokens:
                try:
                    n = int(token)
                    if n > 0:
                        max_round = n
                except ValueError:
                    pass
        if not url:
            await update.message.reply_text("Не указан URL. Пример: + долги https://challenge.place/stage/... 5")
            return
        if not max_round:
            await update.message.reply_text("Не указан номер тура. Пример: + долги https://challenge.place/stage/... 5")
            return
        chat_id = update.effective_chat.id
        try:
            result = self.sync_challenge_stage_debts(chat_id, url, max_round)
            await update.message.reply_text(
                self._format_challenge_sync_user_message(
                    result,
                    f"✅ Синк выполнен до {max_round} тура.",
                )
            )
            if result["unresolved_teams"]:
                unresolved_text = "\n".join([f"- {team}" for team in result["unresolved_teams"]])
                await update.message.reply_text("⚠️ Команды без привязки к @username:\n" + unresolved_text)
            await update.message.reply_text(self.format_league_debts_post(chat_id))
            self.db.set_league_reminder_enabled(chat_id, True)
        except Exception as e:
            self.logger.exception("handle_debts_command failed")
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def _handle_commands_command(self, update: Update, body: str):
        if not body:
            await update.message.reply_text("Использование: + команды\nКоманда1 - @username1\nКоманда2 - @username2\n...")
            return
        mappings = self.parse_league_map_bulk_text(body)
        if not mappings:
            await update.message.reply_text("Не удалось распознать команды. Формат: Команда - @username")
            return
        self.db.replace_league_team_map(update.effective_chat.id, mappings)
        await update.message.reply_text(f"✅ Обновил привязки: {len(mappings)} команд.")

    def register_handlers(self, application):
        application.add_handler(CommandHandler("admin", self._guard(self.cmd_admin)))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._guard(self._on_text_command)))

    def setup_jobs(self, application, logger):
        if not application.job_queue:
            logger.warning("JobQueue unavailable. League reminders disabled.")
            return
        for hour in [8, 12, 18]:
            application.job_queue.run_daily(
                self._daily_reminder,
                time=time_module.time(hour=hour, minute=0),
                name=f"daily_reminder_{hour}"
            )
            logger.info("Scheduled daily reminder at %s:00 Moscow", hour)

    def normalize_team_name(self, team_name: str) -> str:
        normalized = (team_name or "").strip().lower()
        normalized = normalized.replace("ё", "е").replace("ë", "е")
        normalized = re.sub(r"\s+", " ", normalized)
        normalized = normalized.replace(" - ", "-")
        normalized = normalized.replace("глимпт", "глимт")
        return normalized

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
        stage_url = (stage_url or "").strip()
        if max_round <= 0:
            raise ValueError("max_round должен быть положительным числом.")
        self.logger.info(
            "Challenge sync debts [start]: chat_id=%s max_round=%s stage_url=%s",
            chat_id,
            max_round,
            stage_url,
        )
        fetch_errors = 0
        matches_considered = 0
        matches_finished = 0
        matches_unmapped = 0
        debt_entries = []
        unresolved = set()

        try:
            html_text = self.fetch_text_url(stage_url)
            state = self.parse_initial_state(html_text)
            if not state:
                raise ValueError("Не удалось прочитать данные stage (INITIAL_STATE). URL=%s" % stage_url)

            rooms = state.get("rooms", {})
            stage_room = None
            for room in rooms.values():
                if isinstance(room, dict) and "rounds" in room and "competitors" in room and "groups" in room:
                    stage_room = room
                    break
            if not stage_room:
                raise ValueError("Не найдена структура stage в данных страницы. URL=%s" % stage_url)

            rounds_map = stage_room.get("rounds", {})
            competitors_map = stage_room.get("competitors", {})
            team_map_items = self.db.get_league_team_map(chat_id)
            team_to_user = {item["team_name_norm"]: item["telegram_username"] for item in team_map_items}

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
                            fetch_errors += 1
                            self.logger.warning(
                                "Challenge sync: no INITIAL_STATE on match page chat_id=%s round_order=%s match_url=%s",
                                chat_id,
                                order,
                                match_url,
                            )
                            continue

                        match_rooms = match_state.get("rooms", {})
                        match_room = None
                        for room in match_rooms.values():
                            if isinstance(room, dict) and "homeCompetitorId" in room and "awayCompetitorId" in room:
                                match_room = room
                                break
                        if not match_room:
                            fetch_errors += 1
                            self.logger.warning(
                                "Challenge sync: match room structure missing chat_id=%s round_order=%s match_url=%s",
                                chat_id,
                                order,
                                match_url,
                            )
                            continue

                        round_name = match_room.get("roundName")
                        round_num_match = re.search(r"(\d+)", str(round_name or ""))
                        if round_num_match:
                            round_num = int(round_num_match.group(1))
                        else:
                            round_num = order
                        if round_num > max_round:
                            continue

                        if match_room.get("winnerSlot") is not None:
                            matches_finished += 1
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
                            self.logger.warning(
                                "Challenge sync: missing team name chat_id=%s round=%s match_url=%s home_id=%s away_id=%s",
                                chat_id,
                                round_num,
                                match_url,
                                home_id,
                                away_id,
                            )
                            continue

                        matches_considered += 1
                        home_norm = self.normalize_team_name(home_team)
                        away_norm = self.normalize_team_name(away_team)
                        home_user = team_to_user.get(home_norm)
                        away_user = team_to_user.get(away_norm)
                        if not home_user:
                            unresolved.add(home_team)
                        if not away_user:
                            unresolved.add(away_team)
                        if not home_user or not away_user:
                            matches_unmapped += 1
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
                    except Exception as exc:
                        fetch_errors += 1
                        self.logger.warning(
                            "Challenge sync: match processing failed chat_id=%s round_order=%s match_url=%s err=%s",
                            chat_id,
                            order,
                            match_url,
                            exc,
                            exc_info=self.logger.isEnabledFor(logging.DEBUG),
                        )

            self.db.replace_league_debts(chat_id, debt_entries)
        except Exception:
            self.logger.exception(
                "Challenge sync debts [fail]: chat_id=%s max_round=%s stage_url=%s",
                chat_id,
                max_round,
                stage_url,
            )
            raise

        result = {
            "entries_count": len(debt_entries),
            "unresolved_teams": sorted(unresolved),
            "unresolved_matches": matches_unmapped,
            "matches_considered": matches_considered,
            "matches_finished_skipped": matches_finished,
            "fetch_errors": fetch_errors,
            "max_round": max_round,
        }
        self.logger.info(
            "Challenge sync debts [done]: chat_id=%s entries=%s unmapped_matches=%s teams_without_map=%s fetch_errors=%s",
            chat_id,
            result["entries_count"],
            matches_unmapped,
            len(result["unresolved_teams"]),
            fetch_errors,
        )
        return result

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

    def _format_challenge_sync_user_message(self, result: Dict, headline: str) -> str:
        return (f"{headline} | Долгов: {result.get('entries_count', 0)}, "
                f"обработано: {result.get('matches_considered', 0)}, "
                f"без привязки: {result.get('unresolved_matches', 0)}, "
                f"ошибок: {result.get('fetch_errors', 0)}")

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

    async def send_league_reminder_message(self, chat_id: int, threshold: int = 2, bot=None) -> bool:
        summary = self.db.get_league_debt_summary(chat_id)
        debtors = [r for r in summary if r["debts_count"] >= threshold]
        if not debtors:
            self.logger.info("send_league_reminder_message: no debtors for chat=%s", chat_id)
            return False
        mentions = " ".join([f"@{r['debtor_username']}" for r in debtors])
        lines = [
            "🔔 Напоминание по долгам в лиге",
            mentions,
            "",
            f"У вас {threshold} и более долгов. Пожалуйста, сыграйте долги.",
            "",
            "Текущие долги:",
        ]
        lines.extend([f"- @{r['debtor_username']}: {r['debts_count']}" for r in debtors])
        target_bot = bot or (self.application.bot if self.application else None)
        if target_bot is None:
            self.logger.error("send_league_reminder_message: no bot instance for chat=%s", chat_id)
            return False
        try:
            await target_bot.send_message(chat_id=chat_id, text="\n".join(lines))
            self.logger.info("send_league_reminder_message: sent to chat=%s debtors=%s", chat_id, len(debtors))
            return True
        except Exception as exc:
            self.logger.error("send_league_reminder_message: failed to send to chat=%s: %s", chat_id, exc)
            return False

    async def _daily_reminder(self, context: ContextTypes.DEFAULT_TYPE):
        self.logger.info("Daily reminder triggered")
        try:
            configs = self.db.get_enabled_league_reminder_chats()
        except Exception:
            self.logger.exception("Daily reminder: failed to fetch configs")
            return
        for cfg in configs:
            if not bool(cfg.get("enabled")):
                continue
            chat_id = cfg["chat_id"]
            threshold = cfg.get("threshold", 2)
            await self.send_league_reminder_message(
                chat_id=chat_id, threshold=threshold, bot=context.bot
            )

    async def cmd_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            return
        await update.message.reply_text(
            "\n".join(
                [
                    "Доступные команды:",
                    "/admin - список команд",
                    "+ долги <url> <тур> - загрузить долги из challenge.place (автонапоминания в 08, 12, 18 МСК)",
                    "+ команды\nКоманда - @username\n... - задать привязки команд",
                ]
            )
        )

def _parse_admin_ids(raw: str) -> set[str]:
    return {value.strip() for value in str(raw or "").split(",") if value.strip()}


def _clean_env_value(raw: str | None) -> str:
    value = str(raw or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1].strip()
    value = re.sub(r"\s+", "", value)
    return value


def _load_dotenv_resilient() -> None:
    # Some Windows editors save .env as cp1251/cp866; try common encodings.
    encodings = ("utf-8", "utf-8-sig", "cp1251", "cp866")
    last_error: Exception | None = None
    for encoding in encodings:
        try:
            load_dotenv(encoding=encoding)
            return
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    load_dotenv()


def run_bot():
    _load_dotenv_resilient()
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
