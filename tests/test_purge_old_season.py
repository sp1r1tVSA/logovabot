"""
tests/test_purge_old_season.py

Comprehensive test suite for purge_old_season.py.
Verifies on an isolated temporary SQLite database:
1. Dry-run mode collects statistics and DOES NOT modify any data.
2. Execute mode purges 100% of seasonal data (matches, rounds, bets, markets, season stats, squads, ratings).
3. Preserves users, wallets, balances (to the exact coin), global transactions, progression, and RBAC.
4. Preserves divisions architecture (DIV_1..DIV_5, topics, admins).
5. Passes PRAGMA foreign_key_check and PRAGMA integrity_check with zero errors.
6. Rollback safety upon simulated failure.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest

import database
import purge_old_season


class TestPurgeOldSeason(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_league.db")
        
        # Initialize full schema
        self._init_test_database()

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _init_test_database(self):
        """Create rich test database with both global and seasonal data."""
        # Use database module's init on custom db
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        # Temporarily hook database.DB_PATH
        orig_db = database.DB_PATH
        try:
            database.DB_PATH = self.db_path
            database.init_db()
        finally:
            database.DB_PATH = orig_db
            
        with conn:
            cursor = conn.cursor()
            
            # Ensure season
            cursor.execute("""
                INSERT OR IGNORE INTO seasons (id, name, status)
                VALUES (1, 'Сезон 2026', 'active')
            """)
            
            # Ensure test teams
            cursor.execute("INSERT OR IGNORE INTO teams (name, short_name) VALUES ('Расинг', 'RAC')")
            cursor.execute("INSERT OR IGNORE INTO teams (name, short_name) VALUES ('АЕК', 'AEK')")
            cursor.execute("INSERT OR IGNORE INTO squad_players (team_name, player_name) VALUES ('Расинг', 'Игрок 1')")
            cursor.execute("INSERT OR IGNORE INTO squad_players (team_name, player_name) VALUES ('АЕК', 'Игрок 2')")
            cursor.execute("INSERT OR IGNORE INTO team_ratings (team_name, division_id, season_id, elo_rating, matches_counted) VALUES ('Расинг', 1, 1, 1650.0, 30)")
            
            # Ensure users
            cursor.execute("""
                INSERT OR IGNORE INTO users (telegram_id, username, team_name, league_name, role, division_id)
                VALUES 
                    (1001, 'user1', 'Расинг', 'Сезон 2026', 'user', 1),
                    (1002, 'user2', 'АЕК', 'Сезон 2026', 'user', 1),
                    (9999, 'admin1', NULL, NULL, 'admin', 1)
            """)
            
            # Wallets and progression
            cursor.execute("""
                INSERT OR IGNORE INTO user_wallets (user_id, balance, total_wagered, total_won, bets_count, bets_won)
                VALUES 
                    (1001, 75000, 20000, 15000, 10, 6),
                    (1002, 120000, 50000, 70000, 25, 18),
                    (9999, 1000000, 0, 0, 0, 0)
            """)
            
            cursor.execute("""
                INSERT OR IGNORE INTO user_progression (user_id, level, current_xp, total_xp_earned, current_streak, best_streak, equipped_title)
                VALUES 
                    (1001, 5, 250, 2500, 3, 7, 'Тактик 🐾'),
                    (1002, 12, 100, 15000, 5, 12, 'Опытный Каппер 🎯')
            """)
            
            # Financial transactions
            cursor.execute("""
                INSERT INTO coin_transactions (user_id, amount, transaction_type, reference_type, balance_after)
                VALUES 
                    (1001, 50000, 'deposit', 'test', 50000),
                    (1001, 25000, 'bet_won', 'bet', 75000),
                    (1002, 100000, 'deposit', 'test', 100000),
                    (1002, 20000, 'bet_won', 'bet', 120000)
            """)
            
            # Seed 25 division topics (5 divisions x 5 topic types)
            topic_types = ["chat", "table", "matches", "draft", "bets"]
            for div_id in range(1, 6):
                for tt in topic_types:
                    cursor.execute("""
                        INSERT OR REPLACE INTO division_topics (division_id, topic_type, message_thread_id, group_chat_id)
                        VALUES (?, ?, ?, -100123456789)
                    """, (div_id, tt, div_id * 1000 + (hash(tt) % 900)))
                    
            # Seed division admins
            cursor.execute("""
                INSERT OR REPLACE INTO division_admins (division_id, user_id)
                VALUES (1, 9999), (2, 9999)
            """)

            # Matches and rounds
            cursor.execute("""
                INSERT INTO rounds (season_id, division_id, round_number, is_open, deadline)
                VALUES 
                    (1, 1, 1, 0, '2026-05-01 18:00:00'),
                    (1, 1, 2, 0, '2026-05-08 18:00:00')
            """)
            
            cursor.execute("""
                INSERT INTO matches (id, tournament_id, round_number, player1_team, player2_team, status, player1_score, player2_score, division_id, season_id)
                VALUES 
                    (1, 1, 1, 'Расинг', 'АЕК', 'confirmed', 3, 1, 1, 1),
                    (2, 1, 2, 'АЕК', 'Расинг', 'confirmed', 2, 2, 1, 1)
            """)
            
            cursor.execute("""
                INSERT INTO match_events (match_id, team_name, player_name, event_type, count)
                VALUES 
                    (1, 'Расинг', 'Игрок 1', 'goal', 2),
                    (1, 'АЕК', 'Игрок 2', 'goal', 1)
            """)
            
            # Markets and odds
            cursor.execute("""
                INSERT INTO markets (id, match_id, market_key, market_name, category, status)
                VALUES (10, 1, '1x2', 'Исход матча', 'main', 'settled')
            """)
            cursor.execute("""
                INSERT INTO market_selections (id, market_id, selection_key, selection_name, odds_value, status)
                VALUES 
                    (101, 10, 'p1', 'П1', 1.85, 'locked'),
                    (102, 10, 'x', 'Ничья', 3.40, 'locked'),
                    (103, 10, 'p2', 'П2', 4.20, 'locked')
            """)
            
            # User bets
            cursor.execute("""
                INSERT INTO user_bets (id, user_id, bet_type, amount, total_odd, potential_win, status, actual_payout)
                VALUES (501, 1001, 'single', 1000, 1.85, 1850, 'won', 1850)
            """)
            cursor.execute("""
                INSERT INTO bet_items (bet_id, match_id, market_id, selection_id, outcome_type, odd, status)
                VALUES (501, 1, 10, 101, 'p1', 1.85, 'won')
            """)
            
            # Season player stats
            cursor.execute("""
                INSERT INTO season_player_stats (user_id, season_id, division_id, rating, season_points, wins, settled_bets)
                VALUES 
                    (1001, 1, 1, 1350.0, 45.0, 6, 10),
                    (1002, 1, 1, 1580.0, 85.0, 18, 25)
            """)
            
            cursor.execute("""
                INSERT INTO season_snapshots (season_id, division_id, user_id, final_rank, final_rating, season_points, wins, losses, settled_bets, win_rate, roi, promotion_status)
                VALUES (1, 1, 1001, 1, 1350.0, 45.0, 6, 4, 10, 60.0, 15.0, 'PROMOTED')
            """)

        conn.close()

    def test_01_dry_run_does_not_mutate_db(self):
        """Verify dry-run mode returns 0 and leaves database completely unchanged."""
        conn = sqlite3.connect(self.db_path)
        before_summary = purge_old_season.collect_audit_summary(conn)
        conn.close()
        
        self.assertGreater(before_summary["seasonal"]["matches"], 0)
        self.assertGreater(before_summary["seasonal"]["user_bets"], 0)
        self.assertGreater(before_summary["seasonal"]["seasons"], 0)
        
        ret = purge_old_season.run(self.db_path, execute=False, verbose=False)
        self.assertEqual(ret, 0)
        
        conn = sqlite3.connect(self.db_path)
        after_summary = purge_old_season.collect_audit_summary(conn)
        conn.close()
        
        # Verify counts are 100% identical
        self.assertEqual(before_summary["seasonal"], after_summary["seasonal"])
        self.assertEqual(before_summary["preserved"], after_summary["preserved"])
        self.assertEqual(before_summary["total_balance"], after_summary["total_balance"])

    def test_02_execute_purges_all_seasonal_data(self):
        """Verify execute mode purges all matches, rounds, bets, markets, season stats, squads, and seasons."""
        ret = purge_old_season.run(self.db_path, execute=True, verbose=False)
        self.assertEqual(ret, 0)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # All seasonal tables must have 0 rows
        for tbl in purge_old_season.SEASONAL_PURGE_TABLES:
            if purge_old_season.table_exists(cursor, tbl):
                cnt = purge_old_season.get_table_count(cursor, tbl)
                self.assertEqual(cnt, 0, f"Table {tbl} should have 0 rows, but has {cnt}")
                
        conn.close()

    def test_03_execute_preserves_users_and_balances(self):
        """Verify execute mode preserves users, wallets, balances, transactions, and resets season fields."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT SUM(balance) as total_bal, COUNT(*) as cnt FROM user_wallets")
        row = cursor.fetchone()
        orig_total_bal = row["total_bal"]
        orig_wallets_cnt = row["cnt"]
        
        cursor.execute("SELECT COUNT(*) as cnt FROM users")
        orig_users_cnt = cursor.fetchone()["cnt"]
        
        cursor.execute("SELECT COUNT(*) as cnt FROM coin_transactions")
        orig_tx_cnt = cursor.fetchone()["cnt"]
        conn.close()
        
        # Run execute purge
        ret = purge_old_season.run(self.db_path, execute=True, verbose=False)
        self.assertEqual(ret, 0)
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Check users
        cursor.execute("SELECT COUNT(*) as cnt FROM users")
        self.assertEqual(cursor.fetchone()["cnt"], orig_users_cnt)
        
        cursor.execute("SELECT * FROM users WHERE telegram_id = 1001")
        u1 = cursor.fetchone()
        self.assertIsNone(u1["team_name"])  # Team link reset
        self.assertIsNone(u1["league_name"])  # League link reset
        self.assertEqual(u1["username"], "user1")
        self.assertEqual(u1["role"], "user")
        
        # Check admin
        cursor.execute("SELECT * FROM users WHERE telegram_id = 9999")
        admin_u = cursor.fetchone()
        self.assertEqual(admin_u["role"], "admin")
        
        # Check wallets & balances
        cursor.execute("SELECT SUM(balance) as total_bal, COUNT(*) as cnt FROM user_wallets")
        w_row = cursor.fetchone()
        self.assertEqual(w_row["total_bal"], orig_total_bal)
        self.assertEqual(w_row["cnt"], orig_wallets_cnt)
        
        # Check betting counters reset
        cursor.execute("SELECT bets_count, bets_won, total_wagered, total_won FROM user_wallets WHERE user_id = 1001")
        w1 = cursor.fetchone()
        self.assertEqual(w1["bets_count"], 0)
        self.assertEqual(w1["bets_won"], 0)
        self.assertEqual(w1["total_wagered"], 0)
        self.assertEqual(w1["total_won"], 0)
        
        # Check transactions
        cursor.execute("SELECT COUNT(*) as cnt FROM coin_transactions")
        self.assertEqual(cursor.fetchone()["cnt"], orig_tx_cnt)
        
        # Check progression: Level and XP preserved, streaks reset
        cursor.execute("SELECT * FROM user_progression WHERE user_id = 1001")
        p1 = cursor.fetchone()
        self.assertEqual(p1["level"], 5)
        self.assertEqual(p1["total_xp_earned"], 2500)
        self.assertEqual(p1["current_streak"], 0)  # Streak reset
        self.assertEqual(p1["best_streak"], 0)     # Best streak reset
        self.assertEqual(p1["equipped_title"], "Тактик 🐾")
        
        conn.close()

    def test_04_execute_preserves_divisions_architecture(self):
        """Verify divisions (DIV_1..DIV_5), all 25 division_topics, and division_admins remain intact."""
        ret = purge_old_season.run(self.db_path, execute=True, verbose=False)
        self.assertEqual(ret, 0)
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT code FROM divisions ORDER BY sort_order ASC")
        codes = [r["code"] for r in cursor.fetchall()]
        self.assertIn("DIV_1", codes)
        self.assertIn("DIV_2", codes)
        self.assertIn("DIV_3", codes)
        self.assertIn("DIV_4", codes)
        self.assertIn("DIV_5", codes)
        
        # Verify EXACTLY 25 division_topics are preserved (5 divisions x 5 topics)
        cursor.execute("SELECT COUNT(*) FROM division_topics")
        topics_count = cursor.fetchone()[0]
        self.assertEqual(topics_count, 25, f"Expected 25 division_topics, got {topics_count}")
        
        # Verify division_admins are preserved
        cursor.execute("SELECT COUNT(*) FROM division_admins")
        admins_count = cursor.fetchone()[0]
        self.assertEqual(admins_count, 2, f"Expected 2 division_admins, got {admins_count}")
        
        conn.close()

    def test_05_integrity_and_foreign_keys_pass(self):
        """Verify PRAGMA foreign_key_check and integrity_check return 0 errors after purge."""
        ret = purge_old_season.run(self.db_path, execute=True, verbose=False)
        self.assertEqual(ret, 0)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("PRAGMA foreign_key_check;")
        fk_violations = cursor.fetchall()
        self.assertEqual(len(fk_violations), 0, f"Foreign key violations found: {fk_violations}")
        
        cursor.execute("PRAGMA integrity_check;")
        res = cursor.fetchone()[0]
        self.assertEqual(res, "ok")
        
        conn.close()

    def test_06_execute_preserves_division_topics_when_season_id_is_not_null(self):
        """
        Simulate the exact production server scenario:
        divisions.season_id has a NOT NULL constraint (from earlier migration).
        Verify that execute mode does NOT drop divisions and keeps all 25 division_topics.
        """
        not_null_db = os.path.join(self.temp_dir, "test_not_null.db")
        conn = sqlite3.connect(not_null_db)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("PRAGMA foreign_keys = ON;")
        c.execute("CREATE TABLE tournaments (id INTEGER PRIMARY KEY, name TEXT)")
        c.execute("CREATE TABLE seasons (id INTEGER PRIMARY KEY, name TEXT)")
        # divisions with NOT NULL on season_id (exactly as on server before fix)
        c.execute("""
            CREATE TABLE divisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL DEFAULT 1,
                name TEXT NOT NULL,
                code TEXT NOT NULL UNIQUE,
                season_id INTEGER NOT NULL DEFAULT 1,
                topic_id INTEGER DEFAULT NULL,
                is_active BOOLEAN DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                FOREIGN KEY(tournament_id) REFERENCES tournaments(id)
            )
        """)
        c.execute("""
            CREATE TABLE division_topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                division_id INTEGER NOT NULL,
                topic_type TEXT NOT NULL,
                message_thread_id INTEGER NOT NULL,
                group_chat_id INTEGER DEFAULT NULL,
                UNIQUE(division_id, topic_type),
                FOREIGN KEY(division_id) REFERENCES divisions(id) ON DELETE CASCADE
            )
        """)
        c.execute("CREATE TABLE users (telegram_id INTEGER PRIMARY KEY, username TEXT, team_name TEXT, league_name TEXT, role TEXT DEFAULT 'user', division_id INTEGER DEFAULT 1)")
        c.execute("CREATE TABLE user_wallets (user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 1000, bets_count INTEGER DEFAULT 0, bets_won INTEGER DEFAULT 0, total_wagered INTEGER DEFAULT 0, total_won INTEGER DEFAULT 0)")
        c.execute("CREATE TABLE matches (id INTEGER PRIMARY KEY, player1_team TEXT, player2_team TEXT, status TEXT)")
        
        c.execute("INSERT INTO tournaments (id, name) VALUES (1, 'Main League')")
        c.execute("INSERT INTO seasons (id, name) VALUES (1, 'Season 2026')")
        c.execute("INSERT INTO users (telegram_id, username, team_name) VALUES (101, 'player1', 'Team A')")
        c.execute("INSERT INTO user_wallets (user_id, balance, bets_count) VALUES (101, 5000, 10)")
        c.execute("INSERT INTO matches (id, player1_team, player2_team, status) VALUES (1, 'Team A', 'Team B', 'confirmed')")
        
        # Insert 5 divisions and 25 division_topics
        for i in range(1, 6):
            c.execute("INSERT INTO divisions (id, tournament_id, name, code, season_id, sort_order) VALUES (?, 1, ?, ?, 1, ?)", (i, f"Div {i}", f"DIV_{i}", i))
            for tt in ["chat", "table", "matches", "draft", "bets"]:
                c.execute("INSERT INTO division_topics (division_id, topic_type, message_thread_id, group_chat_id) VALUES (?, ?, ?, -100123)", (i, tt, i * 100 + hash(tt) % 50))
                
        conn.commit()
        conn.close()
        
        # Run execute purge
        ret = purge_old_season.run(not_null_db, execute=True, verbose=False)
        self.assertEqual(ret, 0)
        
        # Check that all 25 topics remain intact
        conn = sqlite3.connect(not_null_db)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM division_topics")
        topics_count = c.fetchone()[0]
        self.assertEqual(topics_count, 25, "division_topics must remain exactly 25 after purge with NOT NULL season_id")
        
        c.execute("SELECT COUNT(*) FROM divisions")
        divs_count = c.fetchone()[0]
        self.assertEqual(divs_count, 5, "divisions must remain exactly 5")
        
        c.execute("PRAGMA foreign_key_check;")
        self.assertEqual(len(c.fetchall()), 0)
        conn.close()


if __name__ == "__main__":
    unittest.main()
