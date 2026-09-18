"""
Unit tests for scripts/clear_squads_and_photos.py
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest


class TestClearSquadsScript(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_league.db")
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE squad_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_name TEXT NOT NULL,
                player_name TEXT NOT NULL,
                position TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE users (
                telegram_id INTEGER PRIMARY KEY,
                team_name TEXT,
                division_id INTEGER,
                squad_photo_id TEXT
            )
        """)
        cursor.execute("INSERT INTO squad_players (team_name, player_name) VALUES ('Real Madrid', 'Vinicius Jr')")
        cursor.execute("INSERT INTO squad_players (team_name, player_name) VALUES ('Barcelona', 'Lamine Yamal')")
        cursor.execute("INSERT INTO users (telegram_id, team_name, division_id, squad_photo_id) VALUES (111, 'Real Madrid', 1, 'photo_111')")
        cursor.execute("INSERT INTO users (telegram_id, team_name, division_id, squad_photo_id) VALUES (222, 'Barcelona', 2, 'photo_222')")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dry_run_does_not_modify_database(self):
        script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "clear_squads_and_photos.py")
        cmd = [sys.executable, script_path, "--db", self.db_path]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self.assertIn("DRY-RUN", res.stdout)
        self.assertIn("Игроков в squad_players к удалению: 2", res.stdout)

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM squad_players")
        self.assertEqual(cur.fetchone()[0], 2)
        cur.execute("SELECT COUNT(*) FROM users WHERE squad_photo_id IS NOT NULL")
        self.assertEqual(cur.fetchone()[0], 2)
        conn.close()

    def test_apply_clears_all(self):
        script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "clear_squads_and_photos.py")
        cmd = [sys.executable, script_path, "--apply", "--no-backup", "--db", self.db_path]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self.assertIn("Удалено футболистов из squad_players: 2", res.stdout)
        self.assertIn("Сброшено фото составов у тренеров: 2", res.stdout)

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM squad_players")
        self.assertEqual(cur.fetchone()[0], 0)
        cur.execute("SELECT COUNT(*) FROM users WHERE squad_photo_id IS NOT NULL")
        self.assertEqual(cur.fetchone()[0], 0)
        conn.close()

    def test_apply_filter_by_division(self):
        script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "clear_squads_and_photos.py")
        cmd = [sys.executable, script_path, "--apply", "--no-backup", "--division", "1", "--db", self.db_path]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self.assertIn("Удалено футболистов из squad_players: 1", res.stdout)

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        # Barcelona still intact
        cur.execute("SELECT player_name FROM squad_players")
        rows = cur.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "Lamine Yamal")
        conn.close()


if __name__ == "__main__":
    unittest.main()
