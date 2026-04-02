import json
import logging
import os
import re
import sqlite3
import urllib.request
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes


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
        self.cursor.execute(
            "SELECT chat_id, stage_url, max_round, enabled FROM league_challenge_sources WHERE chat_id = ?",
            (chat_id,),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        return {"chat_id": row[0], "stage_url": row[1], "max_round": row[2], "enabled": row[3]}

    def disable_league_challenge_source(self, chat_id: int):
        self.cursor.execute(
            "UPDATE league_challenge_sources SET enabled = 0, updated_at = CURRENT_TIMESTAMP WHERE chat_id = ?",
            (chat_id,),
        )
        self.conn.commit()


class LeagueRepositoryPostgres(LeagueRepositorySQLite):
    # Inherit command-side logic; override SQL placeholders where needed in your project.
    pass


class LeagueFeature:
    def __init__(self, db, moscow_tz, is_admin_callable, application=None):
        self.db = db
        self.moscow_tz = moscow_tz
        self.league_reminder_times = {"09:00", "15:00", "20:00"}
        self._is_admin = is_admin_callable
        self.application = application

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
        application.add_handler(CommandHandler("league_debts_show", self._guard(self.cmd_league_debts_show)))
        application.add_handler(CommandHandler("league_debts_round", self._guard(self.cmd_league_debts_round)))
        application.add_handler(CommandHandler("league_map_bulk", self._guard(self.cmd_league_map_bulk)))
        application.add_handler(CommandHandler("league_map_show", self._guard(self.cmd_league_map_show)))
        application.add_handler(CommandHandler("league_map_clear", self._guard(self.cmd_league_map_clear)))
        application.add_handler(CommandHandler("league_sync_challenge", self._guard(self.cmd_league_sync_challenge)))
        application.add_handler(CommandHandler("league_sync_now", self._guard(self.cmd_league_sync_now)))
        application.add_handler(CommandHandler("league_sync_off", self._guard(self.cmd_league_sync_off)))
        application.add_handler(CommandHandler("league_reminder_on", self._guard(self.cmd_league_reminder_on)))
        application.add_handler(CommandHandler("league_reminder_off", self._guard(self.cmd_league_reminder_off)))
        application.add_handler(CommandHandler("league_reminder_now", self._guard(self.cmd_league_reminder_now)))
        application.add_handler(CommandHandler("league_reminder_hourly_on", self._guard(self.cmd_league_reminder_hourly_on)))
        application.add_handler(CommandHandler("league_reminder_hourly_off", self._guard(self.cmd_league_reminder_hourly_off)))

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

    def build_league_summary_text(self, chat_id: int, threshold: int = 2) -> str:
        summary = self.db.get_league_debt_summary(chat_id)
        total = self.db.get_league_debts_count(chat_id)
        if not summary:
            return "Долги лиги не загружены."
        lines = [f"📋 Долги лиги (всего матчей-долгов: {total})", ""]
        for row in summary:
            marker = " ⚠️" if row["debts_count"] > threshold else ""
            lines.append(f"@{row['debtor_username']} — {row['debts_count']}{marker}")
        lines.append("")
        lines.append(f"Порог для напоминания: > {threshold}")
        return "\n".join(lines)

    async def send_league_reminder_message(self, chat_id: int, threshold: int = 2, bot=None, custom_text: Optional[str] = None) -> bool:
        summary = self.db.get_league_debt_summary(chat_id)
        debtors = [r for r in summary if r["debts_count"] > threshold]
        if not debtors:
            return False
        mentions = " ".join([f"@{r['debtor_username']}" for r in debtors])
        lines = [
            "🔔 Напоминание по долгам в лиге",
            mentions,
            "",
            custom_text or "У вас больше 2 долгов. Пожалуйста, сыграйте долги сегодня.",
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
        for cfg in self.db.get_enabled_league_reminder_chats():
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

    # --- Commands (copy these into your bot class if needed) ---
    async def cmd_league_debts_show(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        await update.message.reply_text(self.build_league_summary_text(update.effective_chat.id))

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

    async def cmd_league_reminder_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Команда доступна только админам.")
            return
        self.db.set_league_reminder_enabled(update.effective_chat.id, True)
        await update.message.reply_text("✅ Авто-напоминания включены: 09:00, 15:00, 20:00 (Europe/Moscow).")

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
        sent = await self.send_league_reminder_message(update.effective_chat.id, threshold=settings.get("threshold", 2), bot=context.bot)
        await update.message.reply_text("✅ Напоминание отправлено." if sent else "Нет игроков с долгами > 2.")

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

    token = _clean_env_value(os.getenv("BOT_TOKEN")) or _clean_env_value(
        os.getenv("TELEGRAM_BOT_TOKEN")
    )
    if not token:
        raise RuntimeError("Set BOT_TOKEN or TELEGRAM_BOT_TOKEN")

    admin_ids = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))
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
    feature.setup_jobs(application, logging.getLogger("league_bot"))

    print(f"League bot started via league_module.py (admins: {len(admin_ids)})")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run_bot()
