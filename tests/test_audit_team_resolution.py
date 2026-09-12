"""Скрипт аудита обязан быть безвредным для боевой базы.

`scripts/audit_team_resolution.py` задуман для запуска на VPS, по живым данным
тренеров. Единственная его гарантия — он ничего не пишет; проверяем эту гарантию
здесь, а не доверием к глазам ревьюера.

База поднимается своя, временная: настоящая league.db скрипту для проверки не нужна,
а трогать общий файл из параллельного прогона не стоит.
"""
import os
import sqlite3
import tempfile
import unittest
import uuid

import club_registry
import config
from scripts.audit_team_resolution import (
    check_normalization_duplicates,
    check_resolution_collisions,
    check_unregistered,
    emit_config,
    fetch_roster,
    open_read_only,
)


class _TempRosterDB(unittest.TestCase):
    """Общая временная база с одной таблицей users."""

    clubs: tuple[tuple[int, str, int], ...] = ()

    def setUp(self):
        self.path = os.path.join(tempfile.gettempdir(), f"audit_{uuid.uuid4().hex}.db")
        conn = sqlite3.connect(self.path)
        conn.execute(
            "CREATE TABLE users (telegram_id INTEGER PRIMARY KEY, username TEXT, "
            "team_name TEXT, division_id INTEGER)"
        )
        conn.executemany(
            "INSERT INTO users (telegram_id, username, team_name, division_id) VALUES (?, ?, ?, ?)",
            [(tg, f"u{tg}", name, div) for tg, name, div in self.clubs],
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        os.remove(self.path)


class TestAuditIsReadOnly(_TempRosterDB):
    clubs = ((1, "Расинг", 1),)

    def test_every_write_is_denied_by_the_connection(self):
        """Не «мы не пишем», а «драйвер не даст записать»."""
        conn = open_read_only(self.path)
        try:
            for sql in (
                "INSERT INTO users (telegram_id, team_name) VALUES (2, 'X')",
                "UPDATE users SET team_name = 'X' WHERE telegram_id = 1",
                "DELETE FROM users WHERE telegram_id = 1",
                "DROP TABLE users",
                "CREATE TABLE t (a INTEGER)",
            ):
                with self.subTest(sql=sql.split()[0]):
                    with self.assertRaises(sqlite3.DatabaseError):
                        conn.execute(sql)
        finally:
            conn.close()

        # И база действительно не изменилась.
        check = sqlite3.connect(self.path)
        try:
            self.assertEqual(check.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
            self.assertEqual(
                check.execute("SELECT team_name FROM users").fetchone()[0], "Расинг"
            )
        finally:
            check.close()

    def test_reads_still_work(self):
        conn = open_read_only(self.path)
        try:
            self.assertEqual(len(fetch_roster(conn)), 1)
        finally:
            conn.close()

    def test_missing_database_is_reported_not_created(self):
        ghost = os.path.join(tempfile.gettempdir(), f"audit_ghost_{uuid.uuid4().hex}.db")
        with self.assertRaises(SystemExit):
            open_read_only(ghost)
        self.assertFalse(os.path.exists(ghost), "Скрипт создал базу вместо отказа")


class TestAuditFindsCollisions(_TempRosterDB):
    # «расинг клаб» — алиас «Расинга»: разные строки, один канон, одна строка таблицы.
    clubs = (
        (1, "Расинг", 1),
        (2, "расинг клаб", 1),
        (3, "Реал Мадрид", 1),
        (4, "реал мадрид  ", 1),
    )

    def setUp(self):
        super().setUp()
        # Реестр задаём явно: иначе тест поедет, как только в T8 приедут настоящие
        # 80 клубов и «Расинг» из заглушки КПЛ оттуда исчезнет.
        config.CLUB_REGISTRY = ["Расинг"]
        club_registry.reload_registry()

    def tearDown(self):
        config.CLUB_REGISTRY = []
        club_registry.reload_registry()
        super().tearDown()

    def test_alias_collision_is_reported(self):
        conn = open_read_only(self.path)
        try:
            roster = fetch_roster(conn)
        finally:
            conn.close()

        finding = check_resolution_collisions(roster)
        self.assertIsNotNone(finding, "Коллизия канонов не найдена")
        self.assertEqual(finding.code, "COLLISION")
        self.assertEqual(len(finding.lines), 1)
        self.assertIn("расинг клаб", finding.lines[0])

    def test_names_differing_only_by_normalization_are_reported(self):
        conn = open_read_only(self.path)
        try:
            roster = fetch_roster(conn)
        finally:
            conn.close()

        finding = check_normalization_duplicates(roster)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.code, "NORMALIZE_DUP")
        self.assertIn("Реал Мадрид", finding.lines[0])

    def test_clubs_outside_registry_are_listed(self):
        conn = open_read_only(self.path)
        try:
            roster = fetch_roster(conn)
        finally:
            conn.close()

        finding = check_unregistered(roster)
        self.assertIsNotNone(finding)
        self.assertIn("  Реал Мадрид", finding.lines)

    def test_emit_config_collapses_duplicates_and_sorts(self):
        conn = open_read_only(self.path)
        try:
            block = emit_config(fetch_roster(conn))
        finally:
            conn.close()

        self.assertIn("CLUB_REGISTRY: list[str] = [", block)
        self.assertEqual(block.count('"Реал Мадрид"'), 1, "Дубль по нормализации не схлопнут")
        names = [line.strip().strip('",') for line in block.splitlines() if line.startswith("    ")]
        self.assertEqual(names, sorted(names))


class TestAuditOnEmptyRoster(_TempRosterDB):
    clubs = ()

    def test_empty_database_gives_no_findings(self):
        """Локальный прогон на пустой базе обязан быть чистым — иначе exit 1 ничего не значит."""
        conn = open_read_only(self.path)
        try:
            roster = fetch_roster(conn)
        finally:
            conn.close()

        self.assertEqual(roster, [])
        self.assertIsNone(check_resolution_collisions(roster))
        self.assertIsNone(check_normalization_duplicates(roster))
        self.assertIsNone(check_unregistered(roster))


if __name__ == "__main__":
    unittest.main()
