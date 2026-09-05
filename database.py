import logging
import sqlite3
import datetime
import re
import difflib
import threading
from typing import Generator
from contextlib import contextmanager
from config import DB_PATH

logger = logging.getLogger(__name__)

def get_connection() -> sqlite3.Connection:
    """Establish and return a new SQLite database connection."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    except sqlite3.Error as e:
        logger.exception(f"Failed to connect to database at {DB_PATH}")
        raise


_tx_local = threading.local()


@contextmanager
def transaction() -> Generator[sqlite3.Connection, None, None]:
    """Provide a transactional scope around database operations.

    Re-entrant: a nested transaction() call on the same thread joins the
    outer transaction (returns the same connection) instead of opening an
    independent one. Commit happens only when the OUTERMOST scope exits;
    a rollback rolls back the entire multi-step operation. This makes
    composite actions (e.g. confirm match + update cup series) atomic.
    """
    stack = getattr(_tx_local, "stack", None)
    if stack:
        # Nested scope: reuse the outer connection, never commit/close here.
        yield stack[-1]
        return

    conn = get_connection()
    _tx_local.stack = [conn]
    try:
        yield conn
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        logger.exception("Transaction rolled back due to error")
        raise
    finally:
        _tx_local.stack = []
        conn.close()

def init_db() -> None:
    """Initialize the database tables."""
    logger.info("Initializing database tables...")
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                description TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS seasons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'active', 'finished', 'archived')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                created_by INTEGER
            )
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO seasons (id, name, status, created_at, started_at)
            VALUES (1, 'Сезон 2026', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                team_name TEXT,
                league_name TEXT,
                role TEXT NOT NULL DEFAULT 'user',
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER,
                round_number INTEGER,
                player1_id INTEGER,
                player2_id INTEGER,
                player1_score INTEGER,
                player2_score INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                played_at TIMESTAMP,
                FOREIGN KEY(player1_id) REFERENCES users(telegram_id),
                FOREIGN KEY(player2_id) REFERENCES users(telegram_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS squad_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_name TEXT NOT NULL,
                player_name TEXT NOT NULL,
                position TEXT,
                UNIQUE(team_name, player_name)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS match_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL,
                team_name TEXT NOT NULL,
                player_name TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK(event_type IN ('goal', 'assist')),
                count INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                season_id INTEGER NOT NULL DEFAULT 1,
                division_id INTEGER NOT NULL DEFAULT 1,
                round_number INTEGER NOT NULL,
                is_open BOOLEAN DEFAULT 0,
                deadline TEXT,
                UNIQUE(season_id, division_id, round_number)
            )
        """)

        # Migration: check if rounds still uses legacy schema or lacks season_id
        try:
            cursor.execute("PRAGMA table_info(rounds)")
            r_cols = cursor.fetchall()
            r_col_names = [c["name"] if isinstance(c, sqlite3.Row) else c[1] for c in r_cols]
            r_pk_cols = [c["name"] if isinstance(c, sqlite3.Row) else c[1] for c in r_cols if (c["pk"] if isinstance(c, sqlite3.Row) else c[5]) > 0]
            div_col = next((c for c in r_cols if (c["name"] if isinstance(c, sqlite3.Row) else c[1]) == "division_id"), None)
            div_dflt = (div_col["dflt_value"] if isinstance(div_col, sqlite3.Row) else div_col[4]) if div_col else None
            has_season = "season_id" in r_col_names

            if "id" not in r_col_names or r_pk_cols == ["round_number"] or div_dflt is None or not has_season:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS rounds_v3 (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        season_id INTEGER NOT NULL DEFAULT 1,
                        division_id INTEGER NOT NULL DEFAULT 1,
                        round_number INTEGER NOT NULL,
                        is_open BOOLEAN DEFAULT 0,
                        deadline TEXT,
                        UNIQUE(season_id, division_id, round_number)
                    )
                """)
                if "id" in r_col_names and "division_id" in r_col_names:
                    cursor.execute("""
                        INSERT OR IGNORE INTO rounds_v3 (id, season_id, division_id, round_number, is_open, deadline)
                        SELECT id, 1, COALESCE(division_id, 1), round_number, is_open, deadline
                        FROM rounds
                    """)
                elif "division_id" in r_col_names:
                    cursor.execute("""
                        INSERT OR IGNORE INTO rounds_v3 (season_id, division_id, round_number, is_open, deadline)
                        SELECT 1, COALESCE(division_id, 1), round_number, is_open, deadline
                        FROM rounds
                    """)
                else:
                    cursor.execute("""
                        INSERT OR IGNORE INTO rounds_v3 (season_id, division_id, round_number, is_open, deadline)
                        SELECT 1, 1, round_number, is_open, deadline
                        FROM rounds
                    """)
                cursor.execute("DROP TABLE rounds")
                cursor.execute("ALTER TABLE rounds_v3 RENAME TO rounds")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_rounds_season_div_round ON rounds(season_id, division_id, round_number)")
                logger.info("Migrated 'rounds' table to composite (season_id, division_id, round_number) schema successfully.")
        except Exception as e:
            logger.exception(f"Error during rounds table migration check: {e}")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rounds_season_div_round ON rounds(season_id, division_id, round_number)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS round_reminders (
                division_id INTEGER DEFAULT 1,
                round_number INTEGER,
                reminder_type TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(division_id, round_number, reminder_type)
            )
        """)
        try:
            cursor.execute("ALTER TABLE round_reminders ADD COLUMN division_id INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS debt_reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL,
                stage TEXT NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(match_id, stage),
                FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_warns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                admin_id INTEGER,
                reason TEXT,
                type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_reports (
                match_id INTEGER PRIMARY KEY,
                reporter_id INTEGER NOT NULL,
                payload TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN warn_count INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE users ADD COLUMN squad_photo_id TEXT")
        except sqlite3.OperationalError:
            pass
            
        try:
            cursor.execute("ALTER TABLE matches ADD COLUMN is_extended INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE users ADD COLUMN pending_notification INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
            
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cup_series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stage TEXT NOT NULL,
                series_num INTEGER NOT NULL,
                team1_name TEXT NOT NULL,
                team2_name TEXT NOT NULL,
                team1_wins INTEGER DEFAULT 0,
                team2_wins INTEGER DEFAULT 0,
                winner_name TEXT,
                status TEXT DEFAULT 'active'
            )
        """)

        # Safely migration-add new columns to matches using predefined SAFE_COLUMNS tuple.
        # Note: String interpolation is safe here as column names/types are hardcoded internal constants, not user input.
        SAFE_COLUMNS = (
            ("photo_id", "TEXT"),
            ("dispute_photos", "TEXT"),
            ("reported_by", "INTEGER"),
            ("proposed_time", "TEXT"),
            ("proposed_by", "INTEGER"),
            ("time_status", "TEXT DEFAULT 'none'"),
            ("tournament_type", "TEXT DEFAULT 'league'"),
            ("cup_stage", "TEXT"),
            ("cup_series_id", "INTEGER"),
            ("game_num_in_series", "INTEGER DEFAULT 1"),
            ("player1_team", "TEXT"),
            ("player2_team", "TEXT"),
            ("frozen_seconds", "INTEGER DEFAULT 0"),
            ("frozen_at", "TEXT"),
            ("ht_score1", "INTEGER"),
            ("ht_score2", "INTEGER"),
            ("match_date", "TEXT"),
            ("match_time", "TEXT"),
            ("stadium", "TEXT"),
            ("referee", "TEXT"),
            ("live_minute", "INTEGER"),
            ("division_id", "INTEGER DEFAULT NULL"),
            ("season_id", "INTEGER NOT NULL DEFAULT 1"),
        )
        for col_name, col_type in SAFE_COLUMNS:
            try:
                cursor.execute(f"ALTER TABLE matches ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_season_div ON matches(season_id, division_id)")

        # Safely migration-add division_id to users and rounds
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN division_id INTEGER DEFAULT NULL")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE rounds ADD COLUMN division_id INTEGER DEFAULT NULL")
        except sqlite3.OperationalError:
            pass

        # Safely migration-add new columns to user_bets, bet_items, coin_transactions, user_wallets
        for col_name, col_type in (
            ("system_config", "TEXT"),
            ("actual_payout", "INTEGER DEFAULT 0"),
            ("idempotency_key", "TEXT"),
            ("cashout_at", "TIMESTAMP"),
        ):
            try:
                cursor.execute(f"ALTER TABLE user_bets ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass

        for col_name, col_type in (
            ("market_id", "INTEGER"),
            ("selection_id", "INTEGER"),
            ("odds_at_placement", "REAL"),
        ):
            try:
                cursor.execute(f"ALTER TABLE bet_items ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass

        for col_name, col_type in (
            ("reference_type", "TEXT"),
            ("balance_after", "INTEGER"),
        ):
            try:
                cursor.execute(f"ALTER TABLE coin_transactions ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass

        try:
            cursor.execute("ALTER TABLE user_wallets ADD COLUMN daily_limit INTEGER")
        except sqlite3.OperationalError:
            pass
            
        # Performance indexes for matches and match_events
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_p1 ON matches(player1_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_p2 ON matches(player2_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_round ON matches(round_number)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_tourn ON matches(tournament_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_division ON matches(division_id, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_division ON users(division_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_match ON match_events(match_id)")

        # Enforce one owner per club: de-duplicate team_name (prefer a real
        # positive telegram_id over a temporary negative one) then add a UNIQUE
        # index. Prevents duplicated cup debt rows and warns being attributed
        # to the wrong duplicate account.
        cursor.execute("""
            UPDATE users SET team_name = NULL
            WHERE team_name IS NOT NULL AND telegram_id NOT IN (
                SELECT MAX(telegram_id) FROM users WHERE team_name IS NOT NULL GROUP BY LOWER(TRIM(team_name))
            )
        """)
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_team_name_unique ON users(LOWER(TRIM(team_name)))"
        )

        # Persistent chat history for AI mode
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_history_user ON chat_history(user_id, id)")

        # Style samples for AI persona learning (real messages from a source user)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS style_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Feature flags for safe testing and staged rollout of new bot modules
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feature_flags (
                feature_key TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'admin_only',
                config_json TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            INSERT INTO feature_flags (feature_key, status)
            VALUES ('betting_market', 'public')
            ON CONFLICT(feature_key) DO UPDATE SET status = 'public'
            WHERE status != 'disabled'
        """)

        # High-performance Telegram file_id deduplication & media caching
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS telegram_media_cache (
                file_hash TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                media_type TEXT NOT NULL DEFAULT 'animation',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_cache_type ON telegram_media_cache(media_type)")

        # ─── Logovo.bet: Virtual Prediction & Betting System ───
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_wallets (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 1000,
                total_wagered INTEGER NOT NULL DEFAULT 0,
                total_won INTEGER NOT NULL DEFAULT 0,
                bets_count INTEGER NOT NULL DEFAULT 0,
                bets_won INTEGER NOT NULL DEFAULT 0,
                daily_limit INTEGER,
                last_bonus_at TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wallets_balance ON user_wallets(balance DESC)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bet_markets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL UNIQUE,
                tour INTEGER NOT NULL,
                team1_name TEXT NOT NULL,
                team2_name TEXT NOT NULL,
                odd_p1 REAL NOT NULL,
                odd_x REAL NOT NULL,
                odd_p2 REAL NOT NULL,
                odd_tb25 REAL NOT NULL DEFAULT 1.80,
                odd_tm25 REAL NOT NULL DEFAULT 1.95,
                odd_btts_yes REAL NOT NULL DEFAULT 1.70,
                odd_btts_no REAL NOT NULL DEFAULT 2.05,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_bet_markets_tour ON bet_markets(tour, is_active)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_bet_markets_match ON bet_markets(match_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                bet_type TEXT NOT NULL DEFAULT 'single' CHECK(bet_type IN ('single', 'express')),
                amount INTEGER NOT NULL,
                total_odd REAL NOT NULL,
                potential_win INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'won', 'lost', 'refunded', 'cancelled')),
                system_config TEXT,
                actual_payout INTEGER DEFAULT 0,
                idempotency_key TEXT,
                idempotency_payload_hash TEXT,
                cashout_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                settled_at TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_bets_user ON user_bets(user_id, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_bets_status ON user_bets(status)")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_bets_idempotency ON user_bets(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bet_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bet_id INTEGER NOT NULL,
                match_id INTEGER NOT NULL,
                outcome_type TEXT NOT NULL,
                odd REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'won', 'lost', 'refunded')),
                market_id INTEGER,
                selection_id INTEGER,
                odds_at_placement REAL,
                FOREIGN KEY(bet_id) REFERENCES user_bets(id) ON DELETE CASCADE,
                FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_bet_items_bet ON bet_items(bet_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_bet_items_match ON bet_items(match_id, status)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS coin_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                transaction_type TEXT NOT NULL,
                reference_id INTEGER,
                reference_type TEXT,
                balance_after INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_coin_tx_user ON coin_transactions(user_id, created_at)")

        # ─── Logovo.bet: Relational Markets & Extended Prediction Schema ───
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                short_name TEXT,
                logo_url TEXT,
                owner_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(owner_id) REFERENCES users(telegram_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_teams_name ON teams(LOWER(name))")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tournaments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'league' CHECK(type IN ('league', 'cup', 'friendly')),
                season TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO tournaments (id, name, type, season, is_active)
            VALUES (1, 'Логово Фифарей (Основная Лига)', 'league', 'Сезон 2026', 1)
        """)

        # ─── LOGOVO: Divisions & Multi-Topic Routing Tables ───
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS divisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL DEFAULT 1,
                name TEXT NOT NULL,
                code TEXT NOT NULL UNIQUE,
                season_id INTEGER NOT NULL DEFAULT 1,
                topic_id INTEGER DEFAULT NULL,
                is_active BOOLEAN DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(tournament_id) REFERENCES tournaments(id),
                FOREIGN KEY(season_id) REFERENCES seasons(id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_divisions_active ON divisions(is_active, sort_order)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_divisions_topic ON divisions(topic_id)")

        try:
            cursor.execute("PRAGMA table_info(divisions)")
            d_cols = cursor.fetchall()
            d_col_names = [c["name"] if isinstance(c, sqlite3.Row) else c[1] for c in d_cols]
            if "season_id" not in d_col_names:
                cursor.execute("ALTER TABLE divisions ADD COLUMN season_id INTEGER NOT NULL DEFAULT 1")
        except Exception as e:
            logger.debug(f"Notice during divisions season_id migration: {e}")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_divisions_season ON divisions(season_id)")

        cursor.execute("""
            INSERT OR IGNORE INTO divisions (id, tournament_id, name, code, season_id, sort_order)
            VALUES 
                (1, 1, 'Дивизион 1', 'DIV_1', 1, 1),
                (2, 1, 'Дивизион 2', 'DIV_2', 1, 2),
                (3, 1, 'Дивизион 3', 'DIV_3', 1, 3),
                (4, 1, 'Дивизион 4', 'DIV_4', 1, 4),
                (5, 1, 'Дивизион 5', 'DIV_5', 1, 5)
        """)

        cursor.execute("""
            INSERT OR IGNORE INTO schema_migrations (version, description)
            VALUES ('003_p2_seasons_and_isolation', 'Phase 2: Season entity, lifecycle, and strict isolation')
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS division_topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                division_id INTEGER NOT NULL,
                topic_type TEXT NOT NULL,
                message_thread_id INTEGER NOT NULL,
                group_chat_id INTEGER DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(division_id, topic_type),
                FOREIGN KEY(division_id) REFERENCES divisions(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_div_topics_lookup ON division_topics(message_thread_id, topic_type)")
        
        try:
            cursor.execute("ALTER TABLE division_topics ADD COLUMN group_chat_id INTEGER DEFAULT NULL")
        except sqlite3.OperationalError:
            pass

        # Check if division_topics has restrictive CHECK constraint
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='division_topics'")
        dt_schema = cursor.fetchone()
        if dt_schema and dt_schema["sql"] and "CHECK(topic_type IN" in dt_schema["sql"]:
            try:
                cursor.execute("PRAGMA foreign_keys=OFF")
                cursor.execute("""
                    CREATE TABLE division_topics__migration (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        division_id INTEGER NOT NULL,
                        topic_type TEXT NOT NULL,
                        message_thread_id INTEGER NOT NULL,
                        group_chat_id INTEGER DEFAULT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(division_id, topic_type),
                        FOREIGN KEY(division_id) REFERENCES divisions(id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("PRAGMA table_info(division_topics)")
                cols = [c["name"] for c in cursor.fetchall()]
                if "group_chat_id" in cols:
                    cursor.execute("""
                        INSERT INTO division_topics__migration (id, division_id, topic_type, message_thread_id, group_chat_id, created_at)
                        SELECT id, division_id, topic_type, message_thread_id, group_chat_id, created_at FROM division_topics
                    """)
                else:
                    cursor.execute("""
                        INSERT INTO division_topics__migration (id, division_id, topic_type, message_thread_id, created_at)
                        SELECT id, division_id, topic_type, message_thread_id, created_at FROM division_topics
                    """)
                cursor.execute("UPDATE division_topics__migration SET topic_type = 'draft' WHERE topic_type = 'drafts'")
                cursor.execute("DROP TABLE division_topics")
                cursor.execute("ALTER TABLE division_topics__migration RENAME TO division_topics")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_div_topics_lookup ON division_topics(message_thread_id, topic_type)")
            except Exception as e:
                logger.warning(f"Error migrating division_topics check constraint: {e}")

        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_div_topics_chat_thread ON division_topics(group_chat_id, message_thread_id) WHERE group_chat_id IS NOT NULL")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS division_admins (
                division_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(division_id, user_id),
                FOREIGN KEY(division_id) REFERENCES divisions(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_div_admins_user ON division_admins(user_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS markets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL,
                market_key TEXT NOT NULL,
                market_name TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'main',
                status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','suspended','closed','settled','voided')),
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(match_id, market_key),
                FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_markets_match ON markets(match_id, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_markets_key ON markets(market_key)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS market_selections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id INTEGER NOT NULL,
                selection_key TEXT NOT NULL,
                selection_name TEXT NOT NULL,
                odds_value REAL NOT NULL,
                odds_version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','locked','voided')),
                previous_odds REAL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(market_id, selection_key),
                FOREIGN KEY(market_id) REFERENCES markets(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_selections_market ON market_selections(market_id, status)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS odds_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                selection_id INTEGER NOT NULL,
                old_value REAL,
                new_value REAL NOT NULL,
                changed_by INTEGER,
                reason TEXT,
                changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(selection_id) REFERENCES market_selections(id) ON DELETE CASCADE,
                FOREIGN KEY(changed_by) REFERENCES users(telegram_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_odds_hist_sel ON odds_history(selection_id, changed_at DESC)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                target_type TEXT NOT NULL CHECK(target_type IN ('match','team','tournament')),
                target_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, target_type, target_id),
                FOREIGN KEY(user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id, target_type)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                reference_id INTEGER,
                is_read BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read, created_at DESC)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_notification_settings (
                user_id INTEGER NOT NULL,
                notification_type TEXT NOT NULL,
                is_enabled BOOLEAN NOT NULL DEFAULT 1,
                PRIMARY KEY(user_id, notification_type),
                FOREIGN KEY(user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id INTEGER,
                old_value TEXT,
                new_value TEXT,
                division_id INTEGER DEFAULT NULL,
                season_id INTEGER DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(admin_id) REFERENCES users(telegram_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_admin ON admin_audit_log(admin_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_target ON admin_audit_log(target_type, target_id)")
        for col_name, col_type in (
            ("division_id", "INTEGER DEFAULT NULL"),
            ("season_id", "INTEGER DEFAULT NULL"),
        ):
            try:
                cursor.execute(f"ALTER TABLE admin_audit_log ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass

        # ─── Phase 5: Betting Audit Log ───────────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bet_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                old_value TEXT,
                new_value TEXT,
                division_id INTEGER,
                season_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_bet_audit_actor ON bet_audit_log(actor_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_bet_audit_entity ON bet_audit_log(entity_type, entity_id)")

        # ─── Phase 5: Idempotency payload hash migration ──────────────────────
        try:
            cursor.execute("ALTER TABLE user_bets ADD COLUMN idempotency_payload_hash TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS saved_coupons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT,
                selections_json TEXT NOT NULL,
                total_odd REAL NOT NULL,
                status TEXT DEFAULT 'active' CHECK(status IN ('active','expired','updated')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_saved_user ON saved_coupons(user_id, status)")

        # ─── Logovo.bet: Gamification, Progression, Quests & Social Tables ───
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_progression (
                user_id INTEGER PRIMARY KEY,
                level INTEGER NOT NULL DEFAULT 1,
                current_xp INTEGER NOT NULL DEFAULT 0,
                total_xp_earned INTEGER NOT NULL DEFAULT 0,
                current_streak INTEGER NOT NULL DEFAULT 0,
                best_streak INTEGER NOT NULL DEFAULT 0,
                last_active_date TEXT,
                streak_shields INTEGER NOT NULL DEFAULT 1,
                equipped_frame TEXT NOT NULL DEFAULT 'default',
                equipped_title TEXT NOT NULL DEFAULT 'Новичок',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_progression_level ON user_progression(level DESC, current_xp DESC)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS achievements_catalog (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                category TEXT NOT NULL,
                rarity TEXT NOT NULL,
                reward_xp INTEGER NOT NULL DEFAULT 100,
                reward_coins INTEGER NOT NULL DEFAULT 250,
                badge_icon TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_achievements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                achievement_id TEXT NOT NULL,
                is_claimed BOOLEAN NOT NULL DEFAULT 0,
                unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, achievement_id),
                FOREIGN KEY (achievement_id) REFERENCES achievements_catalog(id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_achievements_uid ON user_achievements(user_id)")

        # (quests_catalog, user_quests, pvp_duels tables removed in v2.0 cleanup)

        # ─── Phase 6: Live Match States ──────────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS live_match_states (
                match_id INTEGER PRIMARY KEY,
                season_id INTEGER NOT NULL DEFAULT 1,
                division_id INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'SCHEDULED',
                period TEXT NOT NULL DEFAULT 'pre_match',
                minute INTEGER,
                home_score INTEGER NOT NULL DEFAULT 0,
                away_score INTEGER NOT NULL DEFAULT 0,
                provider TEXT NOT NULL DEFAULT 'none',
                provider_match_id TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_live_states_status ON live_match_states(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_live_states_div_season ON live_match_states(division_id, season_id)")

        # ─── Phase 6: Live Events ────────────────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS live_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                provider_event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                minute INTEGER NOT NULL,
                added_time INTEGER,
                team_id INTEGER,
                team_name TEXT,
                player_id INTEGER,
                player_name TEXT,
                payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE,
                UNIQUE(provider, provider_event_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_live_events_match ON live_events(match_id, minute)")

        # ─── Phase 6: Live Statistics (NULL = unavailable) ───────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS live_statistics (
                match_id INTEGER PRIMARY KEY,
                possession_home REAL,
                possession_away REAL,
                shots_home INTEGER,
                shots_away INTEGER,
                shots_on_target_home INTEGER,
                shots_on_target_away INTEGER,
                corners_home INTEGER,
                corners_away INTEGER,
                fouls_home INTEGER,
                fouls_away INTEGER,
                offsides_home INTEGER,
                offsides_away INTEGER,
                yellow_cards_home INTEGER,
                yellow_cards_away INTEGER,
                red_cards_home INTEGER,
                red_cards_away INTEGER,
                dangerous_attacks_home INTEGER,
                dangerous_attacks_away INTEGER,
                attacks_home INTEGER,
                attacks_away INTEGER,
                passes_home INTEGER,
                passes_away INTEGER,
                pass_accuracy_home REAL,
                pass_accuracy_away REAL,
                xg_home REAL,
                xg_away REAL,
                saves_home INTEGER,
                saves_away INTEGER,
                substitutions_home INTEGER,
                substitutions_away INTEGER,
                provider TEXT NOT NULL DEFAULT 'none',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE
            )
        """)

        # ─── Phase 6: Odds Movement Tracking ─────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS odds_movement (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                selection_id INTEGER NOT NULL,
                market_id INTEGER NOT NULL,
                match_id INTEGER NOT NULL,
                old_odds REAL NOT NULL,
                new_odds REAL NOT NULL,
                pct_change REAL NOT NULL,
                direction TEXT NOT NULL CHECK(direction IN ('up', 'down', 'neutral')),
                velocity REAL NOT NULL DEFAULT 0.0,
                reason TEXT,
                source TEXT NOT NULL DEFAULT 'system',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(selection_id) REFERENCES market_selections(id) ON DELETE CASCADE,
                FOREIGN KEY(market_id) REFERENCES markets(id) ON DELETE CASCADE,
                FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_odds_mov_sel ON odds_movement(selection_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_odds_mov_match ON odds_movement(match_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_odds_mov_created ON odds_movement(created_at DESC)")

        # ─── Phase 6: Notification Events (Deduplicated) ─────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notification_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                link TEXT,
                priority TEXT NOT NULL DEFAULT 'normal',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sent_at TIMESTAMP,
                UNIQUE(user_id, event_type, source_event_id),
                FOREIGN KEY(user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_notif_events_user ON notification_events(user_id, status, created_at DESC)")

        # ─── Phase 6: Provider Sync State ─────────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS provider_sync_state (
                provider TEXT PRIMARY KEY,
                last_sync_at TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'idle',
                error_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ─── Phase 7: Team Elo Ratings ───────────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS team_ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_name TEXT NOT NULL,
                division_id INTEGER NOT NULL DEFAULT 1,
                season_id INTEGER NOT NULL DEFAULT 1,
                elo_rating REAL NOT NULL DEFAULT 1500.0,
                matches_counted INTEGER NOT NULL DEFAULT 0,
                last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(team_name, division_id, season_id),
                FOREIGN KEY(division_id) REFERENCES divisions(id) ON DELETE CASCADE,
                FOREIGN KEY(season_id) REFERENCES seasons(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_team_ratings_div ON team_ratings(division_id, season_id)")

        # ─── Phase 7: AI Predictions with Versioning & Resolution Tracking ───
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL,
                division_id INTEGER NOT NULL DEFAULT 1,
                season_id INTEGER NOT NULL DEFAULT 1,
                model_version TEXT NOT NULL DEFAULT 'ensemble_v1',
                feature_version TEXT NOT NULL DEFAULT 'features_v1',
                home_probability REAL NOT NULL,
                draw_probability REAL NOT NULL,
                away_probability REAL NOT NULL,
                over_1_5_probability REAL,
                over_2_5_probability REAL,
                over_3_5_probability REAL,
                btts_yes_probability REAL,
                btts_no_probability REAL,
                confidence REAL NOT NULL DEFAULT 0.5,
                key_factors TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP,
                actual_result TEXT,
                is_correct BOOLEAN,
                brier_score REAL,
                FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE,
                FOREIGN KEY(division_id) REFERENCES divisions(id) ON DELETE CASCADE,
                FOREIGN KEY(season_id) REFERENCES seasons(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_predictions_match ON predictions(match_id, model_version)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_predictions_div_season ON predictions(division_id, season_id, created_at DESC)")

        # ─── Phase 7: Live & Pre-Match Prediction Snapshots ───────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS prediction_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL,
                stage TEXT NOT NULL CHECK(stage IN ('PRE_MATCH', 'LIVE', 'FINAL')),
                minute INTEGER,
                home_score INTEGER NOT NULL DEFAULT 0,
                away_score INTEGER NOT NULL DEFAULT 0,
                home_prob REAL NOT NULL,
                draw_prob REAL NOT NULL,
                away_prob REAL NOT NULL,
                confidence REAL NOT NULL,
                snapshot_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pred_snapshots_match ON prediction_snapshots(match_id, snapshot_at DESC)")

        # ─── Phase 8: Real Sports Provider Integration & Telemetry ────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sports_providers (
                provider_name TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                base_url TEXT,
                rate_limit_rpm INTEGER DEFAULT 60,
                circuit_breaker_status TEXT DEFAULT 'CLOSED',
                consecutive_failures INTEGER DEFAULT 0,
                last_sync_at TIMESTAMP,
                last_error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS provider_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                provider_match_id TEXT NOT NULL,
                match_id INTEGER NOT NULL,
                division_id INTEGER NOT NULL DEFAULT 1,
                season_id INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'SCHEDULED',
                home_score INTEGER NOT NULL DEFAULT 0,
                away_score INTEGER NOT NULL DEFAULT 0,
                minute INTEGER,
                last_update_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                payload TEXT,
                UNIQUE(provider, provider_match_id),
                FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_provider_matches_lookup ON provider_matches(provider, provider_match_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_provider_matches_match ON provider_matches(match_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_provider_matches_update ON provider_matches(last_update_at)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS provider_sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                status_code INTEGER,
                records_count INTEGER DEFAULT 0,
                latency_ms REAL DEFAULT 0.0,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_provider_sync_log_provider ON provider_sync_log(provider, created_at DESC)")

        # ─── Phase 9: Risk Engine, Centralized Limits & Alerts ──────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS risk_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL CHECK(severity IN ('low', 'medium', 'high', 'critical')),
                division_id INTEGER,
                match_id INTEGER,
                market_id INTEGER,
                selection_id INTEGER,
                message TEXT NOT NULL,
                details_json TEXT,
                status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'acknowledged', 'resolved')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP,
                FOREIGN KEY(division_id) REFERENCES divisions(id) ON DELETE CASCADE,
                FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE,
                FOREIGN KEY(market_id) REFERENCES markets(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_risk_alerts_status ON risk_alerts(status, severity)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_risk_alerts_div ON risk_alerts(division_id, created_at)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS risk_limits_config (
                scope_type TEXT NOT NULL,
                scope_id INTEGER NOT NULL DEFAULT 0,
                limit_key TEXT NOT NULL,
                limit_value REAL NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(scope_type, scope_id, limit_key)
            )
        """)

        # Performance indexes for risk and exposure aggregations
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_bets_exposure ON user_bets(status, settled_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_bet_items_exposure ON bet_items(market_id, selection_id, status)")

        cursor.execute("""
            INSERT OR IGNORE INTO schema_migrations (version, description)
            VALUES ('009_phase9_risk_and_limits', 'Phase 9: Production Betting Intelligence, Risk Engine & Alerts')
        """)

        # ─── Phase 10: Economy, Ranking & Seasonal Progression ──────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS season_player_stats (
                user_id INTEGER NOT NULL,
                season_id INTEGER NOT NULL DEFAULT 1,
                division_id INTEGER NOT NULL DEFAULT 1,
                rating REAL NOT NULL DEFAULT 1200.0,
                confidence REAL NOT NULL DEFAULT 350.0,
                season_points REAL NOT NULL DEFAULT 0.0,
                total_bets INTEGER NOT NULL DEFAULT 0,
                settled_bets INTEGER NOT NULL DEFAULT 0,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                voids INTEGER NOT NULL DEFAULT 0,
                win_rate REAL NOT NULL DEFAULT 0.0,
                roi REAL NOT NULL DEFAULT 0.0,
                total_stake INTEGER NOT NULL DEFAULT 0,
                total_payout INTEGER NOT NULL DEFAULT 0,
                current_streak INTEGER NOT NULL DEFAULT 0,
                best_streak INTEGER NOT NULL DEFAULT 0,
                value_bets_hit INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'QUALIFYING', 'INACTIVE')),
                rank INTEGER DEFAULT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, season_id, division_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sps_leaderboard ON season_player_stats(season_id, division_id, rating DESC, season_points DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sps_user ON season_player_stats(user_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS season_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                season_id INTEGER NOT NULL,
                division_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                final_rank INTEGER NOT NULL,
                final_rating REAL NOT NULL,
                season_points REAL NOT NULL,
                wins INTEGER NOT NULL,
                losses INTEGER NOT NULL,
                voids INTEGER NOT NULL DEFAULT 0,
                settled_bets INTEGER NOT NULL,
                win_rate REAL NOT NULL,
                roi REAL NOT NULL,
                total_stake INTEGER NOT NULL DEFAULT 0,
                total_payout INTEGER NOT NULL DEFAULT 0,
                best_streak INTEGER NOT NULL DEFAULT 0,
                promotion_status TEXT NOT NULL CHECK(promotion_status IN ('PROMOTED', 'RELEGATED', 'STAY', 'INACTIVE')),
                rewards_json TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(season_id, division_id, user_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_season_div ON season_snapshots(season_id, division_id, final_rank)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_user ON season_snapshots(user_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS season_rules_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                season_id INTEGER NOT NULL,
                division_id INTEGER NOT NULL,
                promotion_slots INTEGER NOT NULL DEFAULT 3,
                relegation_slots INTEGER NOT NULL DEFAULT 3,
                min_bets_qualification INTEGER NOT NULL DEFAULT 5,
                min_matches_qualification INTEGER NOT NULL DEFAULT 3,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(season_id, division_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS season_rewards_catalog (
                id TEXT PRIMARY KEY,
                season_id INTEGER,
                division_id INTEGER,
                name TEXT NOT NULL,
                reward_type TEXT NOT NULL CHECK(reward_type IN ('coins', 'xp', 'badge', 'title')),
                amount INTEGER NOT NULL DEFAULT 0,
                badge_id TEXT DEFAULT NULL,
                title TEXT DEFAULT NULL,
                criteria TEXT NOT NULL CHECK(criteria IN ('CHAMPION', 'TOP_3', 'TOP_10', 'PROMOTION', 'PARTICIPATION'))
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS season_reward_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                season_id INTEGER NOT NULL,
                division_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                reward_id TEXT NOT NULL,
                reward_type TEXT NOT NULL,
                coins_awarded INTEGER NOT NULL DEFAULT 0,
                xp_awarded INTEGER NOT NULL DEFAULT 0,
                badge_awarded TEXT DEFAULT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'DISTRIBUTED')),
                distributed_at TIMESTAMP DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, season_id, reward_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reward_ledger_user ON season_reward_ledger(user_id, season_id)")

        cursor.execute("""
            INSERT OR IGNORE INTO schema_migrations (version, description)
            VALUES ('010_phase10_economy_and_progression', 'Phase 10: Logovo Economy, Ranking & Seasonal Progression')
        """)

        # Standardize and migrate canonical team names across all tables
        migrate_team_names_canonical(cursor)

        # Safe migration for squad_players.position
        cursor.execute("PRAGMA table_info(squad_players)")
        squad_cols = [c[1] for c in cursor.fetchall()]
        if "position" not in squad_cols:
            cursor.execute("ALTER TABLE squad_players ADD COLUMN position TEXT")
            logger.info("Migrated squad_players table: added 'position' column.")

        # Seed initial catalog data
        seed_gamification_catalog(cursor)

        logger.info("Database tables initialized successfully.")


def get_cached_telegram_media(file_hash: str, media_type: str = "animation") -> str | None:
    """Retrieve cached Telegram file_id by media SHA-256 hash."""
    if not file_hash:
        return None
    try:
        with transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT file_id FROM telegram_media_cache WHERE file_hash = ? AND media_type = ?",
                (file_hash, media_type)
            )
            row = cursor.fetchone()
            return row["file_id"] if row else None
    except Exception as e:
        logger.warning(f"Error fetching cached telegram media for hash {file_hash[:8]}: {e}")
        return None


def save_cached_telegram_media(file_hash: str, file_id: str, media_type: str = "animation") -> None:
    """Save or update Telegram file_id for given media SHA-256 hash."""
    if not file_hash or not file_id:
        return
    try:
        with transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO telegram_media_cache (file_hash, file_id, media_type)
                VALUES (?, ?, ?)
                ON CONFLICT(file_hash) DO UPDATE SET file_id = excluded.file_id, media_type = excluded.media_type
                """,
                (file_hash, file_id, media_type)
            )
    except Exception as e:
        logger.warning(f"Error saving cached telegram media for hash {file_hash[:8]}: {e}")


def clear_telegram_media_cache(media_type: str | None = None) -> int:
    """Clear cached Telegram file_ids from telegram_media_cache."""
    try:
        with transaction() as conn:
            cursor = conn.cursor()
            if media_type:
                cursor.execute("SELECT COUNT(*) FROM telegram_media_cache WHERE media_type = ?", (media_type,))
                cnt = cursor.fetchone()[0]
                cursor.execute("DELETE FROM telegram_media_cache WHERE media_type = ?", (media_type,))
            else:
                cursor.execute("SELECT COUNT(*) FROM telegram_media_cache")
                cnt = cursor.fetchone()[0]
                cursor.execute("DELETE FROM telegram_media_cache")
            return cnt
    except Exception as e:
        logger.warning(f"Error clearing telegram media cache: {e}")
        return 0


def migrate_team_names_canonical(cursor: sqlite3.Cursor) -> None:
    """Migrate and standardize legacy/variant team spellings across all DB tables."""
    try:
        # Standardize 'Будё Глимт' (latin ë \u00eb, 'Буде Глимт', 'Буде-Глимт', etc.)
        for tbl, cols in [
            ("users", ["team_name"]),
            ("matches", ["player1_team", "player2_team"]),
            ("squad_players", ["team_name"]),
            ("match_events", ["team_name"]),
            ("cup_series", ["team1_name", "team2_name", "winner_name"])
        ]:
            for col in cols:
                cursor.execute(f"""
                    UPDATE {tbl} 
                    SET {col} = 'Будё Глимт' 
                    WHERE {col} IS NOT NULL AND (
                        {col} LIKE '%буд%глимт%' OR {col} LIKE '%bodo%glimt%' OR {col} = 'Буде Глимт' OR {col} = 'Будë Глимт'
                    ) AND {col} != 'Будё Глимт'
                """)
                cursor.execute(f"""
                    UPDATE {tbl} 
                    SET {col} = 'Порту' 
                    WHERE {col} IS NOT NULL AND LOWER({col}) IN ('порто', 'porto', 'portu') AND {col} != 'Порту'
                """)
                cursor.execute(f"""
                    UPDATE {tbl} 
                    SET {col} = 'Фейеноорд' 
                    WHERE {col} IS NOT NULL AND LOWER({col}) IN ('фейенорд', 'фейноорд', 'фейнорд', 'feyenoord') AND {col} != 'Фейеноорд'
                """)
    except Exception as e:
        logger.warning(f"migrate_team_names_canonical notice: {e}")

TEAM_ALIASES = {
    # Расинг
    "расинг": "Расинг", "расинг клаб": "Расинг", "расинг клуб": "Расинг", "расинга": "Расинг",
    "racing": "Расинг", "racing club": "Расинг", "rcing": "Расинг",
    
    # Брага
    "брага": "Брага", "брагу": "Брага", "браге": "Брага", "браги": "Брага",
    "braga": "Брага", "sc braga": "Брага", "сп брага": "Брага", "сц брага": "Брага",
    
    # Бенфика
    "бенфика": "Бенфика", "бенфику": "Бенфика", "бенфике": "Бенфика", "бенфики": "Бенфика", "бенфа": "Бенфика",
    "benfica": "Бенфика", "sl benfica": "Бенфика", "бенфика лиссабон": "Бенфика",
    
    # АЕК
    "аек": "АЕК", "аека": "АЕК", "аеку": "АЕК", "аек афины": "АЕК",
    "aek": "АЕК", "aek athens": "АЕК",
    
    # Аякс
    "аякс": "Аякс", "аякса": "Аякс", "аяксу": "Аякс", "аяксе": "Аякс",
    "ajax": "Аякс", "afc ajax": "Аякс",
    
    # ПСВ
    "псв": "ПСВ", "псв эйндховен": "ПСВ",
    "psv": "ПСВ", "psv eindhoven": "ПСВ",
    
    # Фейеноорд
    "фейеноорд": "Фейеноорд", "фейенорд": "Фейеноорд", "фейноорд": "Фейеноорд", "фейнорд": "Фейеноорд",
    "фейе": "Фейеноорд", "фейеноорда": "Фейеноорд", "фейенорда": "Фейеноорд",
    "feyenoord": "Фейеноорд", "feyenoor": "Фейеноорд", "feyenord": "Фейеноорд",
    
    # Будё Глимт
    "будё глимт": "Будё Глимт", "буде глимт": "Будё Глимт", "будë глимт": "Будё Глимт",
    "буде-глимт": "Будё Глимт", "будё-глимт": "Будё Глимт", "будеглимт": "Будё Глимт", "будёглимт": "Будё Глимт",
    "буде": "Будё Глимт", "будё": "Будё Глимт", "будë": "Будё Глимт", "глимт": "Будё Глимт",
    "bodo glimt": "Будё Глимт", "bodø glimt": "Будё Глимт", "bodo/glimt": "Будё Глимт", "bodø/glimt": "Будё Глимт",
    "bodo": "Будё Глимт", "glimt": "Будё Глимт", "bodoe glimt": "Будё Глимт",
    
    # Порту
    "порту": "Порту", "порто": "Порту", "порт": "Порту", "португал": "Порту",
    "porto": "Порту", "portu": "Порту", "fc porto": "Порту", "фк порту": "Порту", "фк порто": "Порту",
    
    # Спортинг
    "спортинг": "Спортинг", "спортнг": "Спортинг", "спортинга": "Спортинг", "спорт": "Спортинг",
    "sporting": "Спортинг", "sporting cp": "Спортинг", "спортинг лиссабон": "Спортинг",
    
    # Копенгаген
    "копенгаген": "Копенгаген", "копен": "Копенгаген", "копенгагн": "Копенгаген", "копенгагена": "Копенгаген",
    "copenhagen": "Копенгаген", "kobenhavn": "Копенгаген", "fc kobenhavn": "Копенгаген", "фк копенгаген": "Копенгаген",
    
    # Рейнджерс
    "рейнджерс": "Рейнджерс", "рейнджер": "Рейнджерс", "рейнджерсы": "Рейнджерс", "ренджерс": "Рейнджерс", "ренджер": "Рейнджерс",
    "рейнджерса": "Рейнджерс", "rangers": "Рейнджерс", "glasgow rangers": "Рейнджерс", "рейнджерс глазго": "Рейнджерс",
    
    # Бока Хуниорс
    "бока хуниорс": "Бока Хуниорс", "бока": "Бока Хуниорс", "боку": "Бока Хуниорс", "боке": "Бока Хуниорс", "хуниорс": "Бока Хуниорс",
    "boca juniors": "Бока Хуниорс", "boca": "Бока Хуниорс", "boca jrs": "Бока Хуниорс",
    
    # Селтик
    "селтик": "Селтик", "кельтик": "Селтик", "селтика": "Селтик", "селтику": "Селтик",
    "celtic": "Селтик", "celtic fc": "Селтик",
    
    # Брюгге
    "брюгге": "Брюгге", "брюге": "Брюгге", "брюгг": "Брюгге", "брюг": "Брюгге", "брюгге фк": "Брюгге",
    "brugge": "Брюгге", "club brugge": "Брюгге", "клуб брюгге": "Брюгге",
    
    # Ривер Плейт
    "ривер плейт": "Ривер Плейт", "ривер": "Ривер Плейт", "плейт": "Ривер Плейт", "ривера": "Ривер Плейт",
    "river plate": "Ривер Плейт", "river": "Ривер Плейт",
}

def normalize_team_name(name: str | None) -> str:
    """Normalize team name for fuzzy matching (handles ё/е, latin ë, hyphens, slashes, extra spaces)."""
    if not name:
        return ""
    s = str(name).lower()
    # Replace variants of 'ё', latin 'ë' (\u00eb), 'ø', 'ö'
    s = s.replace("ё", "е").replace("\u00eb", "е").replace("ø", "o").replace("ö", "o")
    # Replace punctuation and separators
    s = re.sub(r"[\-_/\\.,]", " ", s)
    # Collapse multiple spaces
    s = re.sub(r"\s+", " ", s).strip()
    return s


def resolve_team_name(name: str | None) -> str:
    """Intelligently resolve any user-entered team name, typo, alias, or transliteration to canonical KPL team name."""
    if not name:
        return ""
    
    raw = str(name).strip()
    norm = normalize_team_name(raw)
    if not norm:
        return raw

    # 1. Direct alias dictionary lookup
    if norm in TEAM_ALIASES:
        return TEAM_ALIASES[norm]

    # 2. Check tokens / joined words
    tokens = norm.split()
    if len(tokens) > 1:
        joined = "".join(tokens)
        if joined in TEAM_ALIASES:
            return TEAM_ALIASES[joined]

    # 3. Check exact match against canonical KPL_TEAMS in config
    import config
    all_canon = getattr(config, "KPL_TEAMS", [])
    for canon in all_canon:
        c_norm = normalize_team_name(canon)
        if norm == c_norm:
            return canon

    # 4. Prefix / Substring match against aliases
    for alias, canon in TEAM_ALIASES.items():
        a_norm = normalize_team_name(alias)
        if len(norm) >= 3 and (norm == a_norm or (len(a_norm) >= 4 and (norm in a_norm or a_norm in norm))):
            return canon

    # 5. Fuzzy string similarity using difflib
    best_match = None
    best_score = 0.0

    for alias, canon in TEAM_ALIASES.items():
        score = difflib.SequenceMatcher(None, norm, normalize_team_name(alias)).ratio()
        if score > best_score:
            best_score = score
            best_match = canon

    for canon in all_canon:
        score = difflib.SequenceMatcher(None, norm, normalize_team_name(canon)).ratio()
        if score > best_score:
            best_score = score
            best_match = canon

    if best_match and best_score >= 0.65:
        return best_match

    return raw


def teams_match(team_a: str | None, team_b: str | None) -> bool:
    """Check if two team names refer to the same team (smart fuzzy/normalized match)."""
    if not team_a or not team_b:
        return False

    res_a = resolve_team_name(team_a)
    res_b = resolve_team_name(team_b)
    if res_a and res_b and res_a.lower() == res_b.lower():
        return True

    # Two distinct canonical clubs must never be conflated by fuzzy matching
    # (e.g. "Атлетико" vs "Атлетик" score ~0.93 on plain string similarity).
    try:
        from config import CLUBS as _KPL_CLUBS
        canon = {normalize_team_name(c) for c in (_KPL_CLUBS or []) if isinstance(c, str)}
        a_c = normalize_team_name(team_a)
        b_c = normalize_team_name(team_b)
        if canon and a_c in canon and b_c in canon and a_c != b_c:
            return False
    except Exception:
        pass

    a_norm = normalize_team_name(team_a)
    b_norm = normalize_team_name(team_b)
    if not a_norm or not b_norm:
        return False
    if a_norm == b_norm or a_norm in b_norm or b_norm in a_norm:
        return True

    # Fuzzy ratio check (raised to 0.85 so similar-but-distinct clubs like
    # "Атлетик"/"Атлетико" are not matched).
    if difflib.SequenceMatcher(None, a_norm, b_norm).ratio() >= 0.85:
        return True
        
    # Word-level match
    a_words = [w for w in a_norm.split() if len(w) > 2]
    b_words = [w for w in b_norm.split() if len(w) > 2]
    if a_words and b_words:
        if all(any(aw in bw or bw in aw for bw in b_words) for aw in a_words):
            return True
        if all(any(bw in aw or aw in bw for aw in a_words) for bw in b_words):
            return True
    return False


def get_team_owner(team_name: str) -> int | None:
    """Return the telegram_id of the user who owns the given team."""
    if not team_name:
        return None
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id, team_name FROM users WHERE team_name IS NOT NULL")
        rows = cursor.fetchall()
        for r in rows:
            if teams_match(r['team_name'], team_name):
                return r['telegram_id']
        return None

def get_user(telegram_id: int) -> sqlite3.Row | None:
    """Retrieve a user record by Telegram ID."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT telegram_id, username, team_name, league_name, role, registered_at, squad_photo_id, warn_count, pending_notification, division_id FROM users WHERE telegram_id = ?
        """, (telegram_id,))
        return cursor.fetchone()

def register_user(telegram_id: int, username: str | None, role: str = 'player', team_name: str | None = None, league_name: str | None = None) -> None:
    """Create or update user profile with team and league assignment."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM users WHERE telegram_id = ?", (telegram_id,))
        if cursor.fetchone():
            cursor.execute(
                "UPDATE users SET username = ?, role = ?, team_name = COALESCE(?, team_name), league_name = COALESCE(?, league_name) WHERE telegram_id = ?",
                (username, role, team_name, league_name, telegram_id)
            )
        else:
            cursor.execute(
                "INSERT INTO users (telegram_id, username, role, team_name, league_name) VALUES (?, ?, ?, ?, ?)",
                (telegram_id, username, role, team_name, league_name)
            )


def upsert_user(telegram_id: int, username: str | None, role: str = 'user') -> None:
    """Create a new user or update their username and role if they exist."""
    with transaction() as conn:
        cursor = conn.cursor()
        # Find if user already exists
        cursor.execute("SELECT role FROM users WHERE telegram_id = ?", (telegram_id,))
        exists = cursor.fetchone()
        if exists:
            # Update username and role, preserve team_name, league_name
            cursor.execute(
                "UPDATE users SET username = ?, role = ? WHERE telegram_id = ?",
                (username, role, telegram_id)
            )
        else:
            cursor.execute(
                "INSERT INTO users (telegram_id, username, role) VALUES (?, ?, ?)",
                (telegram_id, username, role)
            )

def update_profile(telegram_id: int, team_name: str, league_name: str) -> None:
    """Update game profile details for a registered user."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET team_name = ?, league_name = ? WHERE telegram_id = ?",
            (team_name, league_name, telegram_id)
        )

def list_users() -> list[sqlite3.Row]:
    """Retrieve all registered users."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT telegram_id, username, team_name, league_name, role, division_id, registered_at, COALESCE(warn_count, 0) AS warn_count FROM users ORDER BY registered_at DESC"
        )
        return cursor.fetchall()

def get_player_stats(telegram_id: int) -> dict:
    """Calculate and return match statistics for a player using a single aggregated SQL query."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT team_name FROM users WHERE telegram_id = ?", (telegram_id,))
        u_row = cursor.fetchone()
        u_team = u_row["team_name"] if u_row and u_row["team_name"] else ""

        cursor.execute("""
            SELECT
                SUM(CASE 
                    WHEN (player1_team = ? AND player1_score > player2_score) OR (player2_team = ? AND player2_score > player1_score) THEN 1 
                    ELSE 0 
                END) AS wins,
                SUM(CASE 
                    WHEN player1_score = player2_score THEN 1 
                    ELSE 0 
                END) AS draws,
                SUM(CASE 
                    WHEN (player1_team = ? AND player1_score < player2_score) OR (player2_team = ? AND player2_score < player1_score) THEN 1 
                    ELSE 0 
                END) AS losses,
                SUM(CASE 
                    WHEN player1_team = ? THEN COALESCE(player1_score, 0)
                    WHEN player2_team = ? THEN COALESCE(player2_score, 0)
                    ELSE 0 
                END) AS goals_scored,
                SUM(CASE 
                    WHEN player1_team = ? THEN COALESCE(player2_score, 0)
                    WHEN player2_team = ? THEN COALESCE(player1_score, 0)
                    ELSE 0 
                END) AS goals_conceded
            FROM matches
            WHERE status = 'confirmed' AND (player1_team = ? OR player2_team = ?)
        """, (u_team, u_team, u_team, u_team, u_team, u_team, u_team, u_team, u_team, u_team))
        
        row = cursor.fetchone()
        wins = row["wins"] or 0 if row else 0
        draws = row["draws"] or 0 if row else 0
        losses = row["losses"] or 0 if row else 0
        goals_scored = row["goals_scored"] or 0 if row else 0
        goals_conceded = row["goals_conceded"] or 0 if row else 0
        
        played = wins + draws + losses
        points = wins * 3 + draws
        
        return {
            "played": played,
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "goals_scored": goals_scored,
            "goals_conceded": goals_conceded,
            "points": points
        }

def get_active_matches(telegram_id: int, only_expired_deadlines: bool = False, division_id: int | None = None) -> list[dict]:
    """Retrieve active matches for a user from Round 1 up to the highest OPEN round number in their division."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT team_name, division_id FROM users WHERE telegram_id = ?", (telegram_id,))
        u_row = cursor.fetchone()
        u_team = u_row["team_name"] if u_row and u_row["team_name"] else ""
        user_div_id = division_id if division_id is not None else (u_row["division_id"] if u_row and "division_id" in u_row.keys() else None)

        # Get highest open round number for user's division (or global if unassigned)
        if user_div_id is not None:
            cursor.execute("SELECT MAX(round_number) FROM rounds WHERE is_open = 1 AND division_id = ?", (user_div_id,))
        else:
            cursor.execute("SELECT MAX(round_number) FROM rounds WHERE is_open = 1")
        max_open_row = cursor.fetchone()
        max_open_round = max_open_row[0] if max_open_row and max_open_row[0] is not None else 0

        # Collect round numbers whose deadline has already passed (for debt filtering)
        expired_rounds: set[int] = set()
        if only_expired_deadlines:
            now = datetime.datetime.now()
            if user_div_id is not None:
                cursor.execute("SELECT round_number, deadline FROM rounds WHERE is_open = 1 AND division_id = ?", (user_div_id,))
            else:
                cursor.execute("SELECT round_number, deadline FROM rounds WHERE is_open = 1")
            for r_num, dl_str in cursor.fetchall():
                if not dl_str:
                    continue
                dl_dt = parse_flexible_datetime(dl_str)
                if dl_dt and dl_dt <= now:
                    expired_rounds.add(r_num)

        div_filter = f"AND (m.division_id = {user_div_id} OR m.division_id IS NULL) " if user_div_id is not None else ""
        league_condition = (
            f"(m.tournament_type IS NULL OR m.tournament_type = 'league') "
            f"{div_filter}"
            f"AND m.round_number IN ({','.join('?' * len(expired_rounds))}) "
            "AND m.status IN ('pending', 'reported', 'disputed')"
            if only_expired_deadlines and expired_rounds
            else (
                f"(m.tournament_type IS NULL OR m.tournament_type = 'league') "
                f"{div_filter}"
                "AND m.round_number >= 1 "
                "AND m.round_number <= ? "
                "AND m.status IN ('pending', 'reported', 'disputed')"
            )
        )

        params: list = [u_team, u_team, u_team, u_team]
        if only_expired_deadlines and expired_rounds:
            params.extend(sorted(expired_rounds))
        else:
            params.append(max_open_round)

        cursor.execute(f"""
            SELECT 
                m.id, m.round_number, m.status, m.tournament_type, m.cup_stage, m.cup_series_id, m.game_num_in_series,
                m.player1_team, m.player2_team, u1.telegram_id AS player1_id, u2.telegram_id AS player2_id,
                u1.username AS p1_username, u1.team_name AS p1_team,
                u2.username AS p2_username, u2.team_name AS p2_team
            FROM matches m
            LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
            LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
            WHERE (
                (LOWER(m.player1_team) = LOWER(?) AND ? != '')
                OR (LOWER(m.player2_team) = LOWER(?) AND ? != '')
            )
            AND (
                (m.tournament_type = 'cup' AND m.status IN ('pending', 'reported', 'disputed'))
                OR
                {league_condition}
            )
            ORDER BY m.round_number ASC, m.id ASC
        """, params)
        
        matches = []
        for row in cursor.fetchall():
            d = dict(row)
            
            # Skip cup matches where one of the teams is still a placeholder
            if d['tournament_type'] == 'cup':
                if (d['player1_team'] and d['player1_team'].startswith("Победитель")) or \
                   (d['player2_team'] and d['player2_team'].startswith("Победитель")):
                    continue
                    
            if u_team and d['player1_team'] and d['player1_team'].lower() == u_team.lower():
                d['opponent_team'] = d['player2_team'] or d['p2_team']
                d['opponent_username'] = d['p2_username']
            else:
                d['opponent_team'] = d['player1_team'] or d['p1_team']
                d['opponent_username'] = d['p1_username']
            matches.append(d)
        return matches

get_pending_matches = get_active_matches

def get_match_history(telegram_id: int) -> list[dict]:
    """Retrieve played (confirmed) matches for a user, including opponent profile details."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT team_name FROM users WHERE telegram_id = ?", (telegram_id,))
        u_row = cursor.fetchone()
        u_team = u_row["team_name"] if u_row and u_row["team_name"] else ""
        if not u_team:
            return []

        cursor.execute("""
            SELECT 
                m.id, m.round_number, m.player1_score, m.player2_score,
                u1.telegram_id AS player1_id, u2.telegram_id AS player2_id,
                m.player1_team, m.player2_team,
                o.telegram_id AS opponent_id,
                o.username AS opponent_username,
                COALESCE(o.team_name, CASE WHEN LOWER(m.player1_team) = LOWER(?) THEN m.player2_team ELSE m.player1_team END) AS opponent_team
            FROM matches m
            LEFT JOIN users o ON (
                (LOWER(m.player1_team) = LOWER(?) AND LOWER(m.player2_team) = LOWER(o.team_name)) OR
                (LOWER(m.player2_team) = LOWER(?) AND LOWER(m.player1_team) = LOWER(o.team_name))
            )
            WHERE (LOWER(m.player1_team) = LOWER(?) OR LOWER(m.player2_team) = LOWER(?)) AND m.status = 'confirmed'
            ORDER BY m.played_at DESC, m.round_number DESC
        """, (u_team, u_team, u_team, u_team, u_team))
        return [dict(row) for row in cursor.fetchall()]

def update_single_field(telegram_id: int, field_name: str, value: str) -> None:
    """Update a single specific field for a user profile safely."""
    if field_name not in ("team_name", "league_name", "squad_photo_id"):
        raise ValueError(f"Invalid field name: {field_name}")
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE users SET {field_name} = ? WHERE telegram_id = ?",
            (value, telegram_id)
        )

def log_admin_action(
    admin_id: int,
    action: str,
    target_type: str = "",
    target_id: int | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    reason: str | None = None,
    division_id: int | None = None,
    season_id: int | None = None,
    metadata: str | None = None
) -> None:
    """Record an administrative action into admin_audit_log."""
    try:
        with transaction() as conn:
            conn.cursor().execute(
                """
                INSERT INTO admin_audit_log (
                    admin_id, action, target_type, target_id, old_value, new_value, division_id, season_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (admin_id, action, target_type, target_id, old_value, new_value or reason or metadata, division_id, season_id)
            )
    except Exception as e:
        logger.warning(f"Failed to log admin action '{action}': {e}")


def create_season(name: str, created_by: int | None = None) -> int:
    """Create a new season in draft status."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO seasons (name, status, created_by) VALUES (?, 'draft', ?)",
            (name.strip(), created_by)
        )
        season_id = cursor.lastrowid
        log_admin_action(
            admin_id=created_by or 0,
            action="create_season",
            target_type="season",
            target_id=season_id,
            new_value=name.strip(),
            season_id=season_id
        )
        return season_id


def get_season(season_id: int) -> dict | None:
    """Retrieve a season by ID."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM seasons WHERE id = ?", (season_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_active_season() -> dict | None:
    """Retrieve the currently active season."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM seasons WHERE status = 'active' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        if row:
            return dict(row)
        cursor.execute("SELECT * FROM seasons ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        return dict(row) if row else None


def list_seasons(status: str | None = None) -> list[dict]:
    """List all seasons, optionally filtered by status."""
    with transaction() as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute("SELECT * FROM seasons WHERE status = ? ORDER BY id DESC", (status,))
        else:
            cursor.execute("SELECT * FROM seasons ORDER BY id DESC")
        return [dict(r) for r in cursor.fetchall()]


def activate_season(season_id: int, actor_user_id: int | None = None) -> tuple[bool, str]:
    """
    Transition a season to 'active'.
    Marks previous active season as 'finished'.
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM seasons WHERE id = ?", (season_id,))
        target = cursor.fetchone()
        if not target:
            return False, f"Сезон #{season_id} не найден."
        if target["status"] == "active":
            return True, f"Сезон #{season_id} уже активен."
        if target["status"] in ("finished", "archived"):
            return False, f"Нельзя напрямую активировать завершённый или архивированный сезон #{season_id}."

        cursor.execute(
            "UPDATE seasons SET status = 'finished', finished_at = ? WHERE status = 'active'",
            (now,)
        )
        cursor.execute(
            "UPDATE seasons SET status = 'active', started_at = ? WHERE id = ?",
            (now, season_id)
        )
        log_admin_action(
            admin_id=actor_user_id or 0,
            action="activate_season",
            target_type="season",
            target_id=season_id,
            new_value="active",
            season_id=season_id
        )
        return True, f"Сезон #{season_id} ('{target['name']}') успешно активирован."


def finish_season(season_id: int, actor_user_id: int | None = None) -> tuple[bool, str]:
    """Transition an active season to 'finished'."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM seasons WHERE id = ?", (season_id,))
        target = cursor.fetchone()
        if not target:
            return False, f"Сезон #{season_id} не найден."
        if target["status"] != "active":
            return False, f"Завершить можно только активный сезон (текущий статус: {target['status']})."

        cursor.execute(
            "UPDATE seasons SET status = 'finished', finished_at = ? WHERE id = ?",
            (now, season_id)
        )
        log_admin_action(
            admin_id=actor_user_id or 0,
            action="finish_season",
            target_type="season",
            target_id=season_id,
            new_value="finished",
            season_id=season_id
        )
        return True, f"Сезон #{season_id} успешно завершён."


def archive_season(season_id: int, actor_user_id: int | None = None) -> tuple[bool, str]:
    """Transition a finished season to 'archived'."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM seasons WHERE id = ?", (season_id,))
        target = cursor.fetchone()
        if not target:
            return False, f"Сезон #{season_id} не найден."
        if target["status"] != "finished":
            return False, f"Архивировать можно только завершённый сезон (текущий статус: {target['status']})."

        cursor.execute("UPDATE seasons SET status = 'archived' WHERE id = ?", (season_id,))
        log_admin_action(
            admin_id=actor_user_id or 0,
            action="archive_season",
            target_type="season",
            target_id=season_id,
            new_value="archived",
            season_id=season_id
        )
        return True, f"Сезон #{season_id} перенесён в архив."


def get_standings(division_id: int | None = None, season_id: int | None = None) -> list[dict]:
    """Calculate the standings of registered players dynamically, strictly scoped by division and season."""
    with transaction() as conn:
        cursor = conn.cursor()
        
        target_season_id = season_id
        if target_season_id is None:
            act = get_active_season()
            target_season_id = act["id"] if act else 1

        teams = {}
        if division_id is not None:
            # Users assigned to this specific division
            cursor.execute(
                "SELECT telegram_id, team_name, username FROM users WHERE division_id = ? AND team_name IS NOT NULL AND team_name != ''",
                (division_id,)
            )
            div_users = cursor.fetchall()
            for row in div_users:
                canon = resolve_team_name(row["team_name"]) or row["team_name"]
                teams[canon] = {
                    "telegram_id": row["telegram_id"],
                    "team_name": canon,
                    "username": row["username"] if row["username"] else "",
                    "played": 0,
                    "wins": 0,
                    "draws": 0,
                    "losses": 0,
                    "goals_scored": 0,
                    "goals_conceded": 0,
                    "points": 0,
                }
            
            # Fetch confirmed league matches strictly for this division & season
            cursor.execute("""
                SELECT 
                    COALESCE(m.player1_team, u1.team_name) AS player1_team, 
                    COALESCE(m.player2_team, u2.team_name) AS player2_team, 
                    m.player1_score, 
                    m.player2_score 
                FROM matches m
                LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
                LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
                WHERE m.status = 'confirmed' 
                  AND (m.tournament_type IS NULL OR m.tournament_type = 'league')
                  AND m.division_id = ?
                  AND (m.season_id = ? OR m.season_id IS NULL)
            """, (division_id, target_season_id))
        else:
            from config import KPL_TEAMS
            cursor.execute("SELECT telegram_id, team_name, username FROM users WHERE team_name IS NOT NULL AND team_name != ''")
            all_users = cursor.fetchall()
            for t in KPL_TEAMS:
                canon = resolve_team_name(t) or t
                u = None
                for row in all_users:
                    if teams_match(row["team_name"], canon):
                        u = row
                        break
                teams[canon] = {
                    "telegram_id": u["telegram_id"] if u else None,
                    "team_name": canon,
                    "username": u["username"] if u and u["username"] else "",
                    "played": 0,
                    "wins": 0,
                    "draws": 0,
                    "losses": 0,
                    "goals_scored": 0,
                    "goals_conceded": 0,
                    "points": 0,
                }

            # Get all confirmed matches for this season
            cursor.execute("""
                SELECT 
                    COALESCE(m.player1_team, u1.team_name) AS player1_team, 
                    COALESCE(m.player2_team, u2.team_name) AS player2_team, 
                    m.player1_score, 
                    m.player2_score 
                FROM matches m
                LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
                LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
                WHERE m.status = 'confirmed' 
                  AND (m.tournament_type IS NULL OR m.tournament_type = 'league')
                  AND (m.season_id = ? OR m.season_id IS NULL)
                  AND (m.division_id = 1 OR m.division_id IS NULL)
            """, (target_season_id,))
        
        matches = cursor.fetchall()

        for match in matches:
            raw_t1 = match["player1_team"] or ""
            raw_t2 = match["player2_team"] or ""
            t1 = resolve_team_name(raw_t1) or raw_t1
            t2 = resolve_team_name(raw_t2) or raw_t2
            p1_score = match["player1_score"]
            p2_score = match["player2_score"]

            if p1_score is None or p2_score is None:
                continue

            # Match t1
            matched_u1 = teams.get(t1)
            if not matched_u1:
                for k, obj in teams.items():
                    if teams_match(k, t1):
                        matched_u1 = obj
                        break

            # Match t2
            matched_u2 = teams.get(t2)
            if not matched_u2:
                for k, obj in teams.items():
                    if teams_match(k, t2):
                        matched_u2 = obj
                        break

            # If division_id is set and team was not in div_users, dynamically add it
            if matched_u1 is None and division_id is not None and t1:
                teams[t1] = {
                    "telegram_id": None,
                    "team_name": t1,
                    "username": "",
                    "played": 0,
                    "wins": 0,
                    "draws": 0,
                    "losses": 0,
                    "goals_scored": 0,
                    "goals_conceded": 0,
                    "points": 0,
                }
                matched_u1 = teams[t1]

            if matched_u2 is None and division_id is not None and t2:
                teams[t2] = {
                    "telegram_id": None,
                    "team_name": t2,
                    "username": "",
                    "played": 0,
                    "wins": 0,
                    "draws": 0,
                    "losses": 0,
                    "goals_scored": 0,
                    "goals_conceded": 0,
                    "points": 0,
                }
                matched_u2 = teams[t2]

            if matched_u1:
                matched_u1["played"] += 1
                matched_u1["goals_scored"] += p1_score
                matched_u1["goals_conceded"] += p2_score
                if p1_score > p2_score:
                    matched_u1["wins"] += 1
                    matched_u1["points"] += 3
                elif p1_score < p2_score:
                    matched_u1["losses"] += 1
                else:
                    matched_u1["draws"] += 1
                    matched_u1["points"] += 1

            if matched_u2:
                matched_u2["played"] += 1
                matched_u2["goals_scored"] += p2_score
                matched_u2["goals_conceded"] += p1_score
                if p2_score > p1_score:
                    matched_u2["wins"] += 1
                    matched_u2["points"] += 3
                elif p2_score < p1_score:
                    matched_u2["losses"] += 1
                else:
                    matched_u2["draws"] += 1
                    matched_u2["points"] += 1

        # Convert to list and sort
        standings_list = list(teams.values())
        standings_list.sort(
            key=lambda x: (
                x["points"],
                x["goals_scored"] - x["goals_conceded"],
                x["goals_scored"],
                x["wins"]
            ),
            reverse=True
        )
        return standings_list

def clear_all_matches() -> None:
    """Delete all matches from the matches table."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM matches")

def batch_insert_matches(matches_list: list[tuple[int, int, int]]) -> None:
    """Batch insert generated fixtures into the database."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO matches (round_number, player1_id, player2_id, tournament_id, status) VALUES (?, ?, ?, 1, 'pending')",
            matches_list
        )

def get_match(match_id: int) -> dict | None:
    """Retrieve a single match by ID with player nicknames, team names, and cup details."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                m.id, m.round_number, COALESCE(u1.telegram_id, m.player1_id) AS player1_id, COALESCE(u2.telegram_id, m.player2_id) AS player2_id,
                m.player1_score, m.player2_score, m.status, m.played_at, m.is_extended,
                COALESCE(m.frozen_seconds, 0) AS frozen_seconds, m.frozen_at,
                m.photo_id, m.dispute_photos, m.reported_by,
                m.proposed_time, m.proposed_by, m.time_status,
                m.tournament_type, m.cup_stage, m.cup_series_id, m.game_num_in_series, m.division_id, m.season_id,
                m.player1_team AS direct_p1_team, m.player2_team AS direct_p2_team,
                u1.username AS player1_nickname, u1.team_name AS u1_team, u1.username AS player1_username,
                u2.username AS player2_nickname, u2.team_name AS u2_team, u2.username AS player2_username
            FROM matches m
            LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
            LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
            WHERE m.id = ?
        """, (match_id,))
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        d['player1_team'] = d['direct_p1_team'] or d['u1_team']
        d['player2_team'] = d['direct_p2_team'] or d['u2_team']
        if not d.get('player1_id') and d.get('player1_team'):
            u1 = find_user_by_team(d['player1_team'])
            if u1:
                d['player1_id'] = u1['telegram_id']
                if not d.get('player1_username'):
                    d['player1_username'] = u1.get('username')
                    d['player1_nickname'] = u1.get('username')
        if not d.get('player2_id') and d.get('player2_team'):
            u2 = find_user_by_team(d['player2_team'])
            if u2:
                d['player2_id'] = u2['telegram_id']
                if not d.get('player2_username'):
                    d['player2_username'] = u2.get('username')
                    d['player2_nickname'] = u2.get('username')
        return d

get_match_by_id = get_match

def confirm_and_finalize_match(match_id: int, p1_score: int, p2_score: int, events: list, reporter_id: int = None, photo_id: str = None) -> str | None:
    """Instantly save and confirm a match with events in database."""
    if p1_score < 0 or p2_score < 0:
        raise ValueError("Scores must be non-negative integers")
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM match_events WHERE match_id = ?", (match_id,))
        aggregated = {}
        for item in events:
            t_name = item[0].strip()
            p_name = item[1].strip()
            e_type = item[2]
            cnt = item[3] if len(item) > 3 else 1
            key = (t_name, p_name, e_type)
            aggregated[key] = aggregated.get(key, 0) + cnt

        for (t_name, p_name, e_type), cnt in aggregated.items():
            cursor.execute(
                "INSERT INTO match_events (match_id, team_name, player_name, event_type, count) VALUES (?, ?, ?, ?, ?)",
                (match_id, t_name, p_name, e_type, cnt)
            )
        cursor.execute(
            "UPDATE matches SET player1_score = ?, player2_score = ?, reported_by = ?, photo_id = ?, status = 'confirmed', played_at = ? WHERE id = ?",
            (p1_score, p2_score, reporter_id, photo_id, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), match_id)
        )
        try:
            settle_match_bets(match_id, p1_score, p2_score)
        except Exception as e:
            logger.warning(f"Error settling bets for match {match_id}: {e}")
    return process_cup_match_completion(match_id)

def set_technical_result(match_id: int, p1_score: int, p2_score: int) -> str | None:
    """Set technical result for match and update cup series if applicable."""
    if p1_score < 0 or p2_score < 0:
        raise ValueError("Scores must be non-negative integers")
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE matches SET player1_score = ?, player2_score = ?, status = 'confirmed', played_at = ? WHERE id = ?",
            (p1_score, p2_score, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), match_id)
        )
        cursor.execute("DELETE FROM match_events WHERE match_id = ?", (match_id,))
        try:
            settle_match_bets(match_id, p1_score, p2_score)
        except Exception as e:
            logger.warning(f"Error settling bets on technical result for match {match_id}: {e}")
    return process_cup_match_completion(match_id)

def save_pending_report(match_id: int, reporter_id: int, payload: dict) -> None:
    """Persist a pending match report payload (JSON) awaiting opponent/admin confirmation."""
    import json
    with transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pending_reports (match_id, reporter_id, payload) VALUES (?, ?, ?)",
            (match_id, reporter_id, json.dumps(payload, ensure_ascii=False))
        )


def get_pending_report(match_id: int) -> dict | None:
    """Load a pending report payload. Returns dict with reporter_id and parsed payload fields."""
    import json
    with transaction() as conn:
        row = conn.execute(
            "SELECT reporter_id, payload FROM pending_reports WHERE match_id = ?", (match_id,)
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data.update(json.loads(data.pop("payload")))
        except Exception:
            return None
        return data


def delete_pending_report(match_id: int) -> None:
    """Remove a stored pending report after final decision."""
    with transaction() as conn:
        conn.execute("DELETE FROM pending_reports WHERE match_id = ?", (match_id,))


def reset_match(match_id: int) -> None:
    """Reset match status to pending, clear scores/events, and update cup series if cup match."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT cup_series_id, status FROM matches WHERE id = ?", (match_id,))
        m = cursor.fetchone()
        s_id = m["cup_series_id"] if m else None

        cursor.execute(
            "UPDATE matches SET status = 'pending', player1_score = NULL, player2_score = NULL, reported_by = NULL, photo_id = NULL, dispute_photos = NULL WHERE id = ?",
            (match_id,)
        )
        # A fresh start also drops any freeze state from the previous result
        cursor.execute(
            "UPDATE matches SET is_extended = 0, frozen_at = NULL, frozen_seconds = 0 WHERE id = ?",
            (match_id,)
        )
        cursor.execute("DELETE FROM match_events WHERE match_id = ?", (match_id,))
        # Reset debt lifecycle: recorded warn milestones and pending confirmation reports
        cursor.execute("DELETE FROM debt_reminders WHERE match_id = ?", (match_id,))
        cursor.execute("DELETE FROM pending_reports WHERE match_id = ?", (match_id,))

        if s_id:
            # Recalculate cup_series wins
            cursor.execute("SELECT player1_team, player2_team, player1_score, player2_score FROM matches WHERE cup_series_id = ? AND status = 'confirmed'", (s_id,))
            confirmed = cursor.fetchall()
            cursor.execute("SELECT team1_name, team2_name FROM cup_series WHERE id = ?", (s_id,))
            s_row = cursor.fetchone()
            if s_row:
                t1, t2 = s_row["team1_name"], s_row["team2_name"]
                t1_wins = 0
                t2_wins = 0
                for cm in confirmed:
                    c1, c2 = cm["player1_score"] or 0, cm["player2_score"] or 0
                    if c1 > c2 and cm["player1_team"] and cm["player1_team"].lower() == t1.lower(): t1_wins += 1
                    elif c2 > c1 and cm["player2_team"] and cm["player2_team"].lower() == t2.lower(): t2_wins += 1
                    elif c1 > c2 and cm["player1_team"] and cm["player1_team"].lower() == t2.lower(): t2_wins += 1
                    elif c2 > c1 and cm["player2_team"] and cm["player2_team"].lower() == t1.lower(): t1_wins += 1
                cursor.execute("UPDATE cup_series SET team1_wins = ?, team2_wins = ?, winner_name = NULL, status = 'active' WHERE id = ?", (t1_wins, t2_wins, s_id))

def propose_match_time(match_id: int, user_id: int, time_str: str) -> None:
    """Propose or update match time by player."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE matches SET proposed_time = ?, proposed_by = ?, time_status = 'proposed' WHERE id = ?",
            (time_str, user_id, match_id)
        )

def accept_match_time(match_id: int) -> None:
    """Accept the proposed match time."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE matches SET time_status = 'accepted' WHERE id = ?",
            (match_id,)
        )

def save_match_events(match_id: int, events: list[tuple[str, str, int]], team_name: str = None) -> None:
    """Insert match events cleanly, aggregating counts and deleting previous events for specified team or match."""
    if not events:
        return
    with transaction() as conn:
        cursor = conn.cursor()
        if team_name:
            cursor.execute("DELETE FROM match_events WHERE match_id = ? AND LOWER(team_name) = LOWER(?)", (match_id, team_name.strip()))
        else:
            t_names = set(item[0].strip() for item in events)
            for tn in t_names:
                cursor.execute("DELETE FROM match_events WHERE match_id = ? AND LOWER(team_name) = LOWER(?)", (match_id, tn.lower()))

        aggregated = {}
        for item in events:
            t_name = item[0].strip()
            p_name = item[1].strip()
            e_type = item[2]
            cnt = item[3] if len(item) > 3 else 1
            key = (t_name, p_name, e_type)
            aggregated[key] = aggregated.get(key, 0) + cnt

        for (t_name, p_name, e_type), cnt in aggregated.items():
            cursor.execute(
                "INSERT INTO match_events (match_id, team_name, player_name, event_type, count) VALUES (?, ?, ?, ?, ?)",
                (match_id, t_name, p_name, e_type, cnt)
            )

def get_active_match_by_teams(team1: str, team2: str, caption: str | None = None, division_id: int | None = None) -> dict | None:
    """Find an active (pending/reported/disputed) match given two team names, optional caption, and optional division_id."""
    if not team1 or not team2:
        return None
    
    t1_canon = resolve_team_name(team1) or team1
    t2_canon = resolve_team_name(team2) or team2
    t1_lower = t1_canon.lower().strip()
    t2_lower = t2_canon.lower().strip()
    
    caption_clean = (caption or "").lower()
    
    # Detect Cup keywords (including typos like 'кубак')
    cup_keywords = [
        "кубок", "кубак", "кубк", "кубка", "кубке", "cup",
        "1/8", "1/4", "1/2", "полуфинал", "финал", "плей-офф", "плейофф", "playoff", "1/16"
    ]
    is_cup_hint = any(w in caption_clean for w in cup_keywords)
    
    # Detect specific Round number (e.g. "16 тур", "тур 16", "25 тур")
    round_match = re.search(r'(?:тур|турн|round|r|т|раунд)\s*[:\.\-—#]?\s*(\d+)', caption_clean)
    if not round_match:
        round_match = re.search(r'(\d+)\s*[:\.\-—#]?\s*(?:тур|round|раунд)', caption_clean)
    target_round = int(round_match.group(1)) if round_match else None
    
    now = datetime.datetime.now()
    
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                m.id, m.status, m.tournament_type, m.round_number, m.cup_stage, m.cup_series_id, m.game_num_in_series,
                m.division_id,
                m.player1_team AS direct_p1_team, m.player2_team AS direct_p2_team,
                u1.team_name AS u1_team, u2.team_name AS u2_team,
                COALESCE(r.is_open, 0) AS is_round_open,
                r.deadline AS round_deadline,
                COALESCE(s.status, '') AS series_status,
                (SELECT COUNT(*) FROM match_events me WHERE me.match_id = m.id) AS events_count
            FROM matches m
            LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
            LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
            LEFT JOIN rounds r ON m.round_number = r.round_number AND (m.tournament_type IS NULL OR m.tournament_type = 'league')
            LEFT JOIN cup_series s ON m.cup_series_id = s.id
            WHERE m.status IN ('pending', 'reported', 'disputed')
               OR (m.status = 'confirmed' AND (SELECT COUNT(*) FROM match_events me WHERE me.match_id = m.id) = 0)
        """)
        rows = cursor.fetchall()
        
        candidates = []
        for row in rows:
            d = dict(row)
            p1 = (d['direct_p1_team'] or d['u1_team'] or "").lower()
            p2 = (d['direct_p2_team'] or d['u2_team'] or "").lower()
            
            if not p1 or not p2:
                continue
            
            # Substring match for team names using normalized matching
            is_match = False
            if (teams_match(t1_lower, p1) and teams_match(t2_lower, p2)) or \
               (teams_match(t1_lower, p2) and teams_match(t2_lower, p1)):
                is_match = True
                
            if is_match:
                # If division_id is specified, enforce division boundary unless it's a cup match hint
                if division_id is not None and not is_cup_hint:
                    if d.get("division_id") != division_id:
                        continue

                score = 0
                t_type = d.get('tournament_type') or 'league'
                is_pending = d['status'] in ('pending', 'reported', 'disputed')
                is_technical = (d['status'] == 'confirmed' and d.get('events_count', 0) == 0)
                
                # Check if deadline is expired for open rounds (Case 1)
                is_deadline_expired = False
                dl_str = d.get('round_deadline')
                if dl_str:
                    dl_dt = parse_flexible_datetime(dl_str)
                    if dl_dt and dl_dt <= now:
                        is_deadline_expired = True
                
                if is_cup_hint:
                    if t_type == 'cup':
                        if is_pending:
                            score += 2500
                            if d.get('series_status') == 'active':
                                score += 500
                        elif is_technical:
                            score += 1500
                        else:
                            score += 1000
                    else:
                        score -= 1000
                else:
                    if target_round is not None:
                        if t_type == 'league' and d.get('round_number') == target_round:
                            if is_pending:
                                score += 3000
                            elif is_technical:
                                score += 2500  # Case 2: replace TP/TN in specified round
                            else:
                                score += 2000
                        elif t_type == 'league':
                            score -= 500
                    else:
                        if t_type == 'league':
                            rn = d.get('round_number', 50)
                            rn_bonus = max(0, 100 - rn)
                            
                            if is_pending:
                                if d.get('is_round_open') == 1:
                                    if is_deadline_expired:
                                        # Case 1: Open round + deadline expired (active debt!)
                                        score += 700 + rn_bonus
                                    else:
                                        # Open round + deadline not expired
                                        score += 500 + rn_bonus
                                else:
                                    # Closed/future round
                                    score += 50 + rn_bonus
                            elif is_technical:
                                # Case 2: Closed/open round where admin set TP/TN
                                if d.get('is_round_open') == 0:
                                    score += 350 + rn_bonus  # Closed round with TP/TN
                                else:
                                    score += 300 + rn_bonus
                        elif t_type == 'cup' and d.get('series_status') == 'active':
                            score += 300

                candidates.append((score, d['id']))
                
        if not candidates:
            return None
            
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_match_id = candidates[0][1]
        return get_match(best_match_id)

def get_match_events(match_id: int) -> list[dict]:
    """Retrieve all events (goals/assists) for a match, aggregated by team, player, and event_type."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT team_name, player_name, event_type, SUM(count) AS count FROM match_events WHERE match_id = ? GROUP BY team_name, player_name, event_type",
            (match_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

def get_matches_in_rounds(round_numbers: list[int]) -> list[dict]:
    """Retrieve all matches in a list of rounds with player details."""
    if not round_numbers:
        return []
    placeholders = ",".join(["?"] * len(round_numbers))
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT 
                m.id, m.round_number, u1.telegram_id AS player1_id, u2.telegram_id AS player2_id, m.status,
                u1.username AS player1_username, u1.team_name AS player1_team,
                u2.username AS player2_username, u2.team_name AS player2_team
            FROM matches m
            LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
            LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
            WHERE m.round_number IN ({placeholders})
            ORDER BY m.round_number ASC, m.id ASC
        """, tuple(round_numbers))
        return [dict(row) for row in cursor.fetchall()]

def get_unplayed_matches_by_round(round_number: int, division_id: int | None = None) -> list[dict]:
    """Retrieve unplayed (pending) matches in a specific round with player details, optionally filtered by division."""
    with transaction() as conn:
        cursor = conn.cursor()
        if division_id is not None:
            cursor.execute("""
                SELECT 
                    m.id, m.round_number, m.division_id, u1.telegram_id AS player1_id, u2.telegram_id AS player2_id, m.status,
                    u1.username AS player1_username, u1.team_name AS player1_team,
                    u2.username AS player2_username, u2.team_name AS player2_team
                FROM matches m
                LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
                LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
                WHERE m.round_number = ? AND m.status = 'pending' AND (m.division_id = ? OR m.division_id IS NULL)
                ORDER BY m.id ASC
            """, (round_number, division_id))
        else:
            cursor.execute("""
                SELECT 
                    m.id, m.round_number, m.division_id, u1.telegram_id AS player1_id, u2.telegram_id AS player2_id, m.status,
                    u1.username AS player1_username, u1.team_name AS player1_team,
                    u2.username AS player2_username, u2.team_name AS player2_team
                FROM matches m
                LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
                LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
                WHERE m.round_number = ? AND m.status = 'pending'
                ORDER BY m.id ASC
            """, (round_number,))
        return [dict(row) for row in cursor.fetchall()]

def get_open_rounds_with_deadlines(division_id: int | None = None, season_id: int | None = None) -> list[dict]:
    """Retrieve all open rounds that have a deadline set, optionally filtered by division and season."""
    with transaction() as conn:
        cursor = conn.cursor()
        query = "SELECT round_number, deadline, division_id, season_id FROM rounds WHERE is_open = 1 AND deadline IS NOT NULL AND deadline != ''"
        params = []
        if division_id is not None:
            query += " AND division_id = ?"
            params.append(division_id)
        if season_id is not None:
            query += " AND season_id = ?"
            params.append(season_id)
        else:
            act = get_active_season()
            if act:
                query += " AND (season_id = ? OR season_id IS NULL)"
                params.append(act["id"])
        cursor.execute(query, tuple(params))
        return [dict(row) for row in cursor.fetchall()]

def get_teams_recent_form(limit: int = 5, division_id: int | None = None) -> dict[str, list[str]]:
    """
    Retrieve the last `limit` confirmed match outcomes for each team by team_name.
    Returns dict mapping lowercase team_name -> list of 'W', 'D', 'L' outcomes.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        
        if division_id is not None:
            cursor.execute("""
                SELECT player1_team, player2_team, player1_score, player2_score
                FROM matches
                WHERE status = 'confirmed' 
                  AND (tournament_type IS NULL OR tournament_type = 'league')
                  AND division_id = ?
                ORDER BY round_number DESC, id DESC
            """, (division_id,))
            all_matches = cursor.fetchall()

            cursor.execute("SELECT team_name FROM users WHERE division_id = ? AND team_name IS NOT NULL AND team_name != ''", (division_id,))
            team_candidates = [r["team_name"] for r in cursor.fetchall()]
        else:
            from config import KPL_TEAMS
            cursor.execute("""
                SELECT player1_team, player2_team, player1_score, player2_score
                FROM matches
                WHERE status = 'confirmed' AND (tournament_type IS NULL OR tournament_type = 'league')
                ORDER BY round_number DESC, id DESC
            """)
            all_matches = cursor.fetchall()
            team_candidates = list(KPL_TEAMS)

        form_map = {}
        for t in team_candidates:
            canon = resolve_team_name(t) or t
            outcomes = []
            for r in all_matches:
                p1 = r["player1_team"] or ""
                p2 = r["player2_team"] or ""
                s1, s2 = r["player1_score"], r["player2_score"]
                if s1 is None or s2 is None:
                    continue
                if teams_match(p1, canon):
                    if s1 > s2: outcomes.append('W')
                    elif s1 < s2: outcomes.append('L')
                    else: outcomes.append('D')
                elif teams_match(p2, canon):
                    if s2 > s1: outcomes.append('W')
                    elif s2 < s1: outcomes.append('L')
                    else: outcomes.append('D')
                if len(outcomes) >= limit:
                    break
            
            reversed_outcomes = list(reversed(outcomes))
            form_map[canon.lower()] = reversed_outcomes
            if t.lower() != canon.lower():
                form_map[t.lower()] = reversed_outcomes
        return form_map

def has_reminder_been_sent(round_number: int, reminder_type: str, division_id: int | None = None) -> bool:
    """Check if a specific reminder type (24h, 6h, 1h) has already been sent for a round."""
    with transaction() as conn:
        cursor = conn.cursor()
        if division_id is not None:
            cursor.execute(
                "SELECT 1 FROM round_reminders WHERE round_number = ? AND reminder_type = ? AND (division_id = ? OR division_id IS NULL)",
                (round_number, reminder_type, division_id)
            )
        else:
            cursor.execute(
                "SELECT 1 FROM round_reminders WHERE round_number = ? AND reminder_type = ?",
                (round_number, reminder_type)
            )
        return cursor.fetchone() is not None

def clear_all_rounds_and_matches() -> None:
    """Completely wipe all rounds, matches, reminders, and match events from DB."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM match_events")
        cursor.execute("DELETE FROM round_reminders")
        cursor.execute("DELETE FROM matches")
        cursor.execute("DELETE FROM rounds")

def create_round(round_number: int, deadline: str = None, division_id: int | None = None) -> None:
    """Create a new round in DB if it doesn't already exist (closed by default)."""
    div_id = division_id if division_id is not None else 1
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO rounds (division_id, round_number, is_open, deadline) VALUES (?, ?, 0, ?)",
            (div_id, round_number, deadline)
        )

def create_match(round_number: int, player1_id: int, player2_id: int, division_id: int | None = None) -> int:
    """Create a new pending match between two players in a round."""
    if player1_id == player2_id:
        raise ValueError("Игрок не может играть сам с собой (player1_id == player2_id).")
        
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT team_name FROM users WHERE telegram_id IN (?, ?)", (player1_id, player2_id))
        teams = [r[0] for r in cursor.fetchall() if r[0]]
        if len(teams) == 2 and teams[0].lower() == teams[1].lower():
            raise ValueError(f"Команды участников совпадают: {teams[0]}")

        cursor.execute(
            "INSERT INTO matches (round_number, player1_id, player2_id, status, division_id) VALUES (?, ?, ?, 'pending', ?)",
            (round_number, player1_id, player2_id, division_id)
        )
        return cursor.lastrowid

def record_reminder_sent(round_number: int, reminder_type: str, division_id: int | None = None) -> None:
    """Mark a specific reminder type as sent for a round."""
    with transaction() as conn:
        cursor = conn.cursor()
        div_id = division_id if division_id is not None else 1
        cursor.execute(
            "INSERT OR REPLACE INTO round_reminders (division_id, round_number, reminder_type) VALUES (?, ?, ?)",
            (div_id, round_number, reminder_type)
        )

def admin_set_match_score(match_id: int, player1_score: int, player2_score: int, admin_id: int | None = None) -> None:
    """Manually set match score and confirm it by admin, clearing any previous match events."""
    if player1_score < 0 or player2_score < 0:
        raise ValueError("Scores must be non-negative integers")
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT player1_score, player2_score, status, division_id, season_id FROM matches WHERE id = ?", (match_id,))
        old_m = cursor.fetchone()

        is_correction = bool(old_m and old_m["status"] == "confirmed" and old_m["player1_score"] is not None and old_m["player2_score"] is not None)
        old_score_str = f"{old_m['player1_score']}:{old_m['player2_score']}" if is_correction else None

        cursor.execute("DELETE FROM match_events WHERE match_id = ?", (match_id,))
        cursor.execute(
            "UPDATE matches SET player1_score = ?, player2_score = ?, status = 'confirmed', played_at = ? WHERE id = ?",
            (player1_score, player2_score, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), match_id)
        )

        if is_correction and admin_id:
            div_id = old_m["division_id"] if old_m else None
            s_id = old_m["season_id"] if old_m else None
            try:
                log_admin_action(
                    admin_id=admin_id,
                    action="correct_match_score",
                    target_type="match",
                    target_id=match_id,
                    old_value=old_score_str,
                    new_value=f"{player1_score}:{player2_score}",
                    reason="Admin corrected confirmed match score",
                    division_id=div_id,
                    season_id=s_id
                )
            except Exception as e:
                logger.warning(f"Could not log admin score correction for match {match_id}: {e}")

        try:
            settle_match_bets(match_id, player1_score, player2_score)
        except Exception as e:
            logger.warning(f"Error settling bets in admin_set_match_score for match {match_id}: {e}")

def get_config(key: str) -> str | None:
    """Retrieve a configuration value by key."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM system_config WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else None

def set_config(key: str, value: str) -> None:
    """Insert or update a configuration key-value pair."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "REPLACE INTO system_config (key, value) VALUES (?, ?)",
            (key, value)
        )

# ─────────────────────────────────────────────────────────────────────────────
# Feature Flags (Admin Sandbox & Phased Feature Rollout)
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_FEATURE_FLAGS = {
    "fc_player_cards": "admin_only",
    "totw_infographics": "admin_only",
    "hype_match_posters": "admin_only",
    "match_roast_ai": "admin_only",
    "betting_market": "public",
    "fantasy_league": "disabled",
    "achievements_hall_of_fame": "admin_only",
}

def get_feature_flag(key: str, default: str = "admin_only") -> str:
    """Retrieve the status of a feature flag ('disabled', 'admin_only', 'public')."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM feature_flags WHERE feature_key = ?", (key,))
        row = cursor.fetchone()
        if row:
            return row[0]
        return DEFAULT_FEATURE_FLAGS.get(key, default)

def set_feature_flag(key: str, status: str) -> None:
    """Set the status of a feature flag ('disabled', 'admin_only', 'public')."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "REPLACE INTO feature_flags (feature_key, status, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (key, status)
        )

def get_all_feature_flags() -> dict[str, str]:
    """Retrieve all feature flags with defaults merged."""
    flags = dict(DEFAULT_FEATURE_FLAGS)
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT feature_key, status FROM feature_flags")
        rows = cursor.fetchall()
        for r in rows:
            flags[r[0]] = r[1]
    return flags

def is_feature_accessible(key: str, user_id: int) -> bool:
    """
    Check if a feature is accessible to a given user.
    - 'public': accessible to all users
    - 'admin_only': accessible ONLY to users in ADMIN_IDS or with admin role
    - 'disabled': accessible to nobody (not even regular users, admins can test via lab)
    """
    status = get_feature_flag(key)
    if status == "public":
        return True
    if status == "admin_only":
        if not user_id:
            return False
        from config import ADMIN_IDS
        if user_id in ADMIN_IDS:
            return True
        try:
            user = get_user(user_id)
            if user and user.get("role") == "admin":
                return True
        except Exception:
            pass
        return False
    return False

def get_group_id() -> int | None:
    """Retrieve the automatically tracked Telegram Group ID."""
    val = get_config("group_id")
    try:
        return int(val) if val else None
    except ValueError:
        return None


def get_chat_history(user_id: int, limit: int = 10) -> list[dict]:
    """Retrieve recent AI chat history for a user (oldest first)."""
    with transaction() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT role, text FROM ("
            "  SELECT id, role, text FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?"
            ") ORDER BY id ASC",
            (user_id, limit)
        )
        rows = cursor.fetchall()
        return [{"role": r["role"], "text": r["text"]} for r in rows]


def append_chat_history(user_id: int, role: str, text: str) -> None:
    """Append one message to the AI chat history."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO chat_history (user_id, role, text) VALUES (?, ?, ?)",
            (user_id, role, text)
        )


def trim_chat_history(user_id: int, keep: int = 10) -> None:
    """Delete oldest AI chat history rows beyond the newest `keep` for a user."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM chat_history WHERE user_id = ? AND id NOT IN ("
            "  SELECT id FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?"
            ")",
            (user_id, user_id, keep)
        )


def append_style_sample(text: str) -> None:
    """Append one real message of the persona source user (e.g. @t3miy)."""
    text_clean = text.strip()
    if not text_clean:
        return
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO style_samples (text) VALUES (?)",
            (text_clean,)
        )


def get_style_samples(limit: int = 20) -> list[str]:
    """Return the newest style samples (oldest first)."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT text FROM ("
            "  SELECT id, text FROM style_samples ORDER BY id DESC LIMIT ?"
            ") ORDER BY id ASC",
            (limit,)
        )
        return [r["text"] for r in cursor.fetchall()]


def trim_style_samples(keep: int = 100) -> None:
    """Keep only the newest `keep` style samples."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM style_samples WHERE id NOT IN ("
            "  SELECT id FROM style_samples ORDER BY id DESC LIMIT ?"
            ")",
            (keep,)
        )


def open_rounds_batch(start_round: int, end_round: int, deadline: str, division_id: int | None = None, season_id: int | None = None) -> None:
    """Open multiple rounds and set a shared deadline, scoped by division and season."""
    div_id = division_id if division_id is not None else 1
    if season_id is None:
        act = get_active_season()
        s_id = act["id"] if act else 1
    else:
        s_id = season_id

    act = get_season(s_id)
    if act and act.get("status") not in ("active", None):
        raise ValueError(f"Cannot open rounds in season #{s_id} with status '{act.get('status')}'")

    with transaction() as conn:
        cursor = conn.cursor()
        for r_num in range(start_round, end_round + 1):
            cursor.execute(
                "INSERT OR IGNORE INTO rounds (season_id, division_id, round_number, is_open, deadline) VALUES (?, ?, ?, 0, NULL)",
                (s_id, div_id, r_num)
            )
        if division_id is not None:
            cursor.execute(
                "UPDATE rounds SET is_open = 1, deadline = ? WHERE (season_id = ? OR season_id IS NULL) AND division_id = ? AND round_number >= ? AND round_number <= ?",
                (deadline, s_id, division_id, start_round, end_round)
            )
        else:
            cursor.execute(
                "UPDATE rounds SET is_open = 1, deadline = ? WHERE (season_id = ? OR season_id IS NULL) AND round_number >= ? AND round_number <= ?",
                (deadline, s_id, start_round, end_round)
            )

    # 🎰 Auto-generate betting lines for opened rounds
    for r_num in range(start_round, end_round + 1):
        try:
            from services.betting_engine import generate_round_markets
            generate_round_markets(r_num, division_id=div_id, season_id=s_id)
        except Exception as e:
            logger.exception(f"Error generating round markets for round {r_num}: {e}")

def get_open_pending_matches() -> list[dict]:
    """Get all pending matches where the round is open and not extended, scoped by division and season."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                m.id, m.round_number, m.division_id, m.season_id, u1.telegram_id AS player1_id, u2.telegram_id AS player2_id, m.status, m.is_extended,
                u1.username AS player1_nickname, u1.team_name AS player1_team,
                u2.username AS player2_nickname, u2.team_name AS player2_team,
                r.deadline
            FROM matches m
            JOIN rounds r ON m.round_number = r.round_number
                         AND (m.division_id = r.division_id OR (m.division_id IS NULL AND r.division_id = 1))
                         AND (m.season_id = r.season_id OR (m.season_id IS NULL AND (r.season_id = 1 OR r.season_id IS NULL)))
            LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
            LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
            WHERE m.status = 'pending' 
              AND m.is_extended = 0
              AND r.is_open = 1
            ORDER BY m.round_number ASC, m.id ASC
        """)
        return [dict(row) for row in cursor.fetchall()]

def extend_match_deadline(match_id: int) -> int:
    """Toggle is_extended between 0 and 1 for an overdue match. Returns new value.
    Freeze time is accumulated so auto-warn schedules shift, not skip."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_extended FROM matches WHERE id = ?", (match_id,))
        row = cursor.fetchone()
        cur = row["is_extended"] if row and row["is_extended"] else 0
        new_val = 0 if cur == 1 else 1
        _apply_freeze_state(cursor, match_id, new_val)
        return new_val


def set_match_extended(match_id: int, value: int) -> None:
    """Explicitly set is_extended (1 = freeze auto-warns, 0 = resume). No toggle.
    Freeze intervals are recorded so that hours_overdue excludes frozen time."""
    with transaction() as conn:
        cursor = conn.cursor()
        _apply_freeze_state(cursor, match_id, 1 if value else 0)


def _apply_freeze_state(cursor: sqlite3.Cursor, match_id: int, new_val: int) -> None:
    """Shared freeze bookkeeping: stamp frozen_at on freeze, accumulate elapsed
    seconds into frozen_seconds on unfreeze."""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if new_val == 1:
        # Start freezing: remember when (idempotent if already frozen)
        cursor.execute(
            "UPDATE matches SET is_extended = 1, "
            "frozen_at = COALESCE(frozen_at, ?) WHERE id = ?",
            (now_str, match_id)
        )
    else:
        # Resume: bank the elapsed frozen interval, then clear the marker
        cursor.execute("SELECT frozen_at, COALESCE(frozen_seconds, 0) AS fs FROM matches WHERE id = ?", (match_id,))
        row = cursor.fetchone()
        extra = 0
        if row and row["frozen_at"]:
            f_at = parse_flexible_datetime(row["frozen_at"])
            if f_at:
                extra = max(0, int((datetime.datetime.now() - f_at).total_seconds()))
        cursor.execute(
            "UPDATE matches SET is_extended = 0, frozen_at = NULL, "
            "frozen_seconds = COALESCE(frozen_seconds, 0) + ? WHERE id = ?",
            (extra, match_id)
        )


def get_match_frozen_seconds(match_id: int) -> float:
    """Total seconds the match has spent frozen, INCLUDING the current ongoing
    freeze interval (if is_extended=1 right now)."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT is_extended, frozen_at, COALESCE(frozen_seconds, 0) AS fs FROM matches WHERE id = ?",
            (match_id,)
        )
        row = cursor.fetchone()
        if not row:
            return 0.0
        total = float(row["fs"] or 0)
        if row["is_extended"] and row["frozen_at"]:
            f_at = parse_flexible_datetime(row["frozen_at"])
            if f_at:
                total += max(0.0, (datetime.datetime.now() - f_at).total_seconds())
        return total

def get_matches_by_round(round_number: int, division_id: int | None = None) -> list[dict]:
    """Retrieve all matches for a specific round with player details, optionally filtered by division."""
    with transaction() as conn:
        cursor = conn.cursor()
        if division_id is not None:
            cursor.execute("""
                SELECT 
                    m.id, m.round_number, m.division_id, u1.telegram_id AS player1_id, u2.telegram_id AS player2_id,
                    m.player1_score, m.player2_score, m.status,
                    u1.username AS player1_nickname, COALESCE(m.player1_team, u1.team_name, 'Команда 1') AS player1_team,
                    u2.username AS player2_nickname, COALESCE(m.player2_team, u2.team_name, 'Команда 2') AS player2_team
                FROM matches m
                LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
                LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
                WHERE m.round_number = ? AND m.division_id = ?
                ORDER BY m.id ASC
            """, (round_number, division_id))
        else:
            cursor.execute("""
                SELECT 
                    m.id, m.round_number, m.division_id, u1.telegram_id AS player1_id, u2.telegram_id AS player2_id,
                    m.player1_score, m.player2_score, m.status,
                    u1.username AS player1_nickname, COALESCE(m.player1_team, u1.team_name, 'Команда 1') AS player1_team,
                    u2.username AS player2_nickname, COALESCE(m.player2_team, u2.team_name, 'Команда 2') AS player2_team
                FROM matches m
                LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
                LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
                WHERE m.round_number = ?
                ORDER BY m.id ASC
            """, (round_number,))
        return [dict(row) for row in cursor.fetchall()]

def get_admins() -> list[dict]:
    """Retrieve all users with admin role."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id, username FROM users WHERE role = 'admin'")
        return [dict(row) for row in cursor.fetchall()]

def pre_register_player(username: str, team_name: str) -> int:
    """Pre-register a player with a temporary negative ID."""
    username_clean = username.strip().lstrip("@")
    team_name_clean = team_name.strip()
    with transaction() as conn:
        cursor = conn.cursor()
        
        # Unassign previous owner of this team if any
        cursor.execute(
            "UPDATE users SET team_name = NULL, warn_count = 0 WHERE LOWER(team_name) = LOWER(?) AND LOWER(username) != LOWER(?)",
            (team_name_clean, username_clean)
        )
        cursor.execute(
            "DELETE FROM user_warns WHERE user_id IN (SELECT telegram_id FROM users WHERE LOWER(team_name) = LOWER(?) AND LOWER(username) != LOWER(?))",
            (team_name_clean, username_clean)
        )
        
        # Check if username already exists in users table
        cursor.execute("SELECT telegram_id FROM users WHERE LOWER(username) = LOWER(?)", (username_clean,))
        row = cursor.fetchone()
        if row:
            # Update club name, reset warns, set player role
            cursor.execute(
                "UPDATE users SET team_name = ?, role = 'player', warn_count = 0 WHERE LOWER(username) = LOWER(?)",
                (team_name_clean, username_clean)
            )
            cursor.execute("DELETE FROM user_warns WHERE user_id = ?", (row[0],))
            return row[0]
        
        # Generate a new unique negative ID for pre-registration
        cursor.execute("SELECT MIN(telegram_id) FROM users")
        min_row = cursor.fetchone()
        min_id = min_row[0] if min_row and min_row[0] else 0
        temp_id = min(min_id - 1, -1)
        
        cursor.execute(
            "INSERT INTO users (telegram_id, username, team_name, league_name, role, warn_count) VALUES (?, ?, ?, ?, ?, 0)",
            (temp_id, username_clean, team_name_clean, "Основная", "player")
        )
        return temp_id

def handle_user_startup(telegram_id: int, username: str | None, default_role: str = 'user') -> None:
    """
    Handle a user starting the bot.
    If the user has a pre-registered entry (matched by username), update their telegram_id
    and matches referencing their temporary ID. Otherwise, perform a standard upsert.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        
        # Check if there is a pre-registered user with this username (negative id)
        pre_reg = None
        if username:
            cursor.execute(
                "SELECT * FROM users WHERE LOWER(username) = LOWER(?) AND telegram_id < 0",
                (username.strip(),)
            )
            pre_reg = cursor.fetchone()

        # 1. Check if the exact telegram_id already exists
        cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        exists = cursor.fetchone()
        
        if exists:
            if pre_reg:
                old_id = pre_reg['telegram_id']
                new_team = exists['team_name'] or pre_reg['team_name']
                new_league = exists['league_name'] or pre_reg['league_name']
                new_role = pre_reg['role'] if exists['role'] == 'user' else exists['role']
                new_notif = 1 if (pre_reg['team_name'] and not exists['team_name']) else exists['pending_notification']
                
                # Free team_name on old record to prevent UNIQUE constraint conflict
                cursor.execute("UPDATE users SET team_name = NULL WHERE telegram_id = ?", (old_id,))
                
                # Re-point references to real telegram_id (foreign keys valid since exists is already in users)
                cursor.execute("UPDATE matches SET player1_id = ? WHERE player1_id = ?", (telegram_id, old_id))
                cursor.execute("UPDATE matches SET player2_id = ? WHERE player2_id = ?", (telegram_id, old_id))
                cursor.execute("UPDATE matches SET reported_by = ? WHERE reported_by = ?", (telegram_id, old_id))
                cursor.execute("UPDATE matches SET proposed_by = ? WHERE proposed_by = ?", (telegram_id, old_id))
                cursor.execute("UPDATE user_warns SET user_id = ? WHERE user_id = ?", (telegram_id, old_id))
                cursor.execute("UPDATE pending_reports SET reporter_id = ? WHERE reporter_id = ?", (telegram_id, old_id))
                
                # Delete old temporary record
                cursor.execute("DELETE FROM users WHERE telegram_id = ?", (old_id,))
                
                cursor.execute(
                    "UPDATE users SET username = ?, team_name = ?, league_name = ?, role = ?, pending_notification = ? WHERE telegram_id = ?",
                    (username, new_team, new_league, new_role, new_notif, telegram_id)
                )
                logger.info(f"Merged pre-registered user @{username} (old_id: {old_id}) into existing user {telegram_id}")
            else:
                cursor.execute(
                    "UPDATE users SET username = ? WHERE telegram_id = ?",
                    (username, telegram_id)
                )
            return

        if pre_reg:
            old_id = pre_reg['telegram_id']
            old_team = pre_reg['team_name']
            
            # 1. Clear team_name on old record so inserting new record doesn't violate unique constraint
            cursor.execute("UPDATE users SET team_name = NULL WHERE telegram_id = ?", (old_id,))
            
            # 2. Insert new user record first so foreign keys (matches, user_warns) can reference real telegram_id
            cursor.execute(
                "INSERT INTO users (telegram_id, username, team_name, league_name, role, registered_at, pending_notification, warn_count, squad_photo_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    telegram_id, username, old_team, pre_reg['league_name'], 
                    pre_reg['role'], pre_reg['registered_at'], 1 if old_team else 0,
                    pre_reg['warn_count'] if 'warn_count' in pre_reg.keys() else 0,
                    pre_reg['squad_photo_id'] if 'squad_photo_id' in pre_reg.keys() else None
                )
            )
            
            # 3. Re-point references to real telegram_id
            cursor.execute("UPDATE matches SET player1_id = ? WHERE player1_id = ?", (telegram_id, old_id))
            cursor.execute("UPDATE matches SET player2_id = ? WHERE player2_id = ?", (telegram_id, old_id))
            cursor.execute("UPDATE matches SET reported_by = ? WHERE reported_by = ?", (telegram_id, old_id))
            cursor.execute("UPDATE matches SET proposed_by = ? WHERE proposed_by = ?", (telegram_id, old_id))
            cursor.execute("UPDATE user_warns SET user_id = ? WHERE user_id = ?", (telegram_id, old_id))
            cursor.execute("UPDATE pending_reports SET reporter_id = ? WHERE reporter_id = ?", (telegram_id, old_id))
            
            # 4. Delete old temporary record
            cursor.execute("DELETE FROM users WHERE telegram_id = ?", (old_id,))
            
            logger.info(f"Matched pre-registered user @{username} (old_id: {old_id}) to real id: {telegram_id}")
            return

        # 3. Fallback: regular insert
        cursor.execute(
            "INSERT INTO users (telegram_id, username, role) VALUES (?, ?, ?)",
            (telegram_id, username, default_role)
        )

def remove_player(player_ref: str) -> tuple[bool, str]:
    """
    Remove player from users table after cleaning up their matches/events to prevent FK constraint failures.
    """
    player_ref_clean = player_ref.strip().lstrip("@")
    with transaction() as conn:
        cursor = conn.cursor()
        
        # Find user first
        cursor.execute("SELECT telegram_id, team_name, username FROM users WHERE telegram_id = ? OR LOWER(username) = LOWER(?)", (player_ref_clean, player_ref_clean))
        row = cursor.fetchone()
        if not row:
            return False, "Игрок не найден."
            
        p_id, team, uname = row[0], row[1], row[2]
        
        # Unlink player from matches instead of deleting them to preserve league history
        cursor.execute("UPDATE matches SET player1_id = NULL WHERE player1_id = ?", (p_id,))
        cursor.execute("UPDATE matches SET player2_id = NULL WHERE player2_id = ?", (p_id,))
        cursor.execute("DELETE FROM users WHERE telegram_id = ?", (p_id,))
        
        display_name = f"@{uname}" if uname else f"ID {p_id}"
        return True, f"Игрок **{display_name}** ({team or 'без названия'}) успешно удален из лиги."

def set_player_club(player_ref: str, new_club: str) -> tuple[bool, str]:
    """Change player's club/team."""
    player_ref_clean = player_ref.strip().lstrip("@")
    new_club_clean = new_club.strip()
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id FROM users WHERE telegram_id = ? OR LOWER(username) = LOWER(?)", (player_ref_clean, player_ref_clean))
        row = cursor.fetchone()
        if not row:
            return False, "Игрок не найден."
        p_id = row[0]

        cursor.execute("SELECT team_name FROM users WHERE telegram_id = ?", (p_id,))
        old_club_row = cursor.fetchone()
        old_club = (old_club_row[0] or "").strip() if old_club_row else ""

        # Unassign previous owner of new_club if any
        cursor.execute(
            "UPDATE users SET team_name = NULL, warn_count = 0 WHERE LOWER(team_name) = LOWER(?) AND telegram_id != ?",
            (new_club_clean, p_id)
        )
        cursor.execute(
            "DELETE FROM user_warns WHERE user_id IN (SELECT telegram_id FROM users WHERE LOWER(team_name) = LOWER(?) AND telegram_id != ?)",
            (new_club_clean, p_id)
        )

        cursor.execute("UPDATE users SET team_name = ?, role = 'player', warn_count = 0 WHERE telegram_id = ?", (new_club_clean, p_id))
        cursor.execute("DELETE FROM user_warns WHERE user_id = ?", (p_id,))

        # Keep pending fixtures and active cup series pointing at the club the
        # player now owns, so they don't become orphaned debts of the old club.
        if old_club and old_club.lower() != new_club.strip().lower():
            cursor.execute(
                "UPDATE matches SET player1_team = ? WHERE status = 'pending' AND LOWER(player1_team) = LOWER(?)",
                (new_club.strip(), old_club)
            )
            cursor.execute(
                "UPDATE matches SET player2_team = ? WHERE status = 'pending' AND LOWER(player2_team) = LOWER(?)",
                (new_club.strip(), old_club)
            )
            cursor.execute(
                "UPDATE cup_series SET team1_name = ? WHERE status = 'active' AND LOWER(team1_name) = LOWER(?)",
                (new_club.strip(), old_club)
            )
            cursor.execute(
                "UPDATE cup_series SET team2_name = ? WHERE status = 'active' AND LOWER(team2_name) = LOWER(?)",
                (new_club.strip(), old_club)
            )
            # Fresh debt lifecycle for the transferred fixtures
            cursor.execute(
                "DELETE FROM debt_reminders WHERE match_id IN "
                "(SELECT id FROM matches WHERE status = 'pending' AND (LOWER(player1_team) = LOWER(?) OR LOWER(player2_team) = LOWER(?)))",
                (new_club.strip(), new_club.strip())
            )

        return True, f"Клуб игрока **@{player_ref_clean}** изменен на **{new_club.strip()}**."

def update_player_username(telegram_id: int, username: str) -> tuple[bool, str]:
    """Update player's Telegram username."""
    username_clean = username.strip().lstrip("@")
    if not username_clean:
        return False, "Юзернейм не может быть пустым."
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id FROM users WHERE LOWER(username) = LOWER(?) AND telegram_id != ?", (username_clean, telegram_id))
        existing = cursor.fetchone()
        if existing:
            return False, f"Юзернейм @{username_clean} уже используется другим игроком."
        cursor.execute("UPDATE users SET username = ? WHERE telegram_id = ?", (username_clean, telegram_id))
        return True, f"Telegram-юзернейм успешно изменен на @{username_clean}."

def update_player_role(telegram_id: int, role: str) -> tuple[bool, str]:
    """Update user's system role (player or admin)."""
    if role not in ("player", "admin"):
        return False, "Неверная роль."
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET role = ? WHERE telegram_id = ?", (role, telegram_id))
        return True, f"Роль успешно изменена на {role}."

def delete_player_completely(telegram_id: int) -> tuple[bool, str]:
    """Delete player completely and remove all matches involving them."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cursor.fetchone()
        if not row:
            return False, "Игрок не найден."
        nickname = row[0] or str(telegram_id)
        cursor.execute("""
            DELETE FROM match_events WHERE match_id IN (
                SELECT id FROM matches WHERE player1_id = ? OR player2_id = ?
            )
        """, (telegram_id, telegram_id))
        cursor.execute("DELETE FROM matches WHERE player1_id = ? OR player2_id = ?", (telegram_id, telegram_id))
        cursor.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
        return True, f"Игрок **@{nickname}** и все матчи с его участием полностью стерты из базы данных."

def clear_entire_league() -> None:
    """Clear all matches and users (retaining users with admin role)."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM match_events")
        cursor.execute("DELETE FROM round_reminders")
        cursor.execute("DELETE FROM matches")
        cursor.execute("DELETE FROM rounds")
        cursor.execute("DELETE FROM users WHERE role != 'admin'")
        logger.info("Entire league matches and players cleared (admins kept).")


def assign_player_to_club(username: str, club: str) -> tuple[int, str | None]:
    """
    Assign a player by username/tag to a club.
    If the club was previously assigned to someone else, unlink (or delete) the old player.
    Returns (telegram_id, old_player_username).
    """
    username_clean = username.strip().lstrip("@")
    club_clean = club.strip()
    
    with transaction() as conn:
        cursor = conn.cursor()
        
        # 1. Find who is currently assigned to this club
        cursor.execute("SELECT telegram_id, username FROM users WHERE LOWER(team_name) = LOWER(?)", (club_clean,))
        old_player = cursor.fetchone()
        old_username = None
        if old_player:
            old_id, old_username = old_player[0], old_player[1]
            # Detach played matches from the old owner (preserve league history),
            # then remove their unplayed fixtures BEFORE deleting the user row
            # (FK constraint requires children gone first).
            cursor.execute(
                "UPDATE matches SET player1_id = NULL WHERE player1_id = ? AND status != 'pending'",
                (old_id,)
            )
            cursor.execute(
                "UPDATE matches SET player2_id = NULL WHERE player2_id = ? AND status != 'pending'",
                (old_id,)
            )
            cursor.execute("DELETE FROM matches WHERE player1_id = ? OR player2_id = ?", (old_id, old_id))
            cursor.execute("DELETE FROM users WHERE telegram_id = ?", (old_id,))

            # Fresh debt lifecycle for the club's remaining pending fixtures:
            # the new owner must not inherit recorded auto-warn milestones.
            cursor.execute("""
                DELETE FROM debt_reminders WHERE match_id IN (
                    SELECT id FROM matches
                    WHERE status = 'pending'
                      AND (LOWER(player1_team) = LOWER(?) OR LOWER(player2_team) = LOWER(?))
                )
            """, (club_clean, club_clean))

        # 2. Check if the new player already exists in the system
        cursor.execute("SELECT telegram_id FROM users WHERE LOWER(username) = LOWER(?)", (username_clean,))
        exists = cursor.fetchone()
        if exists:
            new_id = exists[0]
            # Reset warns so a previously excluded player does not get instantly re-kicked
            cursor.execute(
                "UPDATE users SET team_name = ?, warn_count = 0 WHERE telegram_id = ?",
                (club_clean, new_id)
            )
        else:
            # Generate negative temp ID
            cursor.execute("SELECT MIN(telegram_id) FROM users")
            min_row = cursor.fetchone()
            min_id = min_row[0] if min_row and min_row[0] else 0
            new_id = min(min_id - 1, -1)
            cursor.execute(
                "INSERT INTO users (telegram_id, username, team_name, league_name, role) VALUES (?, ?, ?, ?, ?)",
                (new_id, username_clean, club_clean, "Основная", "player")
            )
            
        return new_id, old_username


def set_pending_notification(telegram_id: int, value: int = 1) -> None:
    """Set or clear the pending notification flag for a user."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET pending_notification = ? WHERE telegram_id = ?", (value, telegram_id))


def get_pending_notification(telegram_id: int) -> bool:
    """Check if a user has a pending notification."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT pending_notification FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cursor.fetchone()
        return bool(row and row[0])


def get_user_team(telegram_id: int) -> str | None:
    """Get the team_name assigned to a user, or None if not assigned."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT team_name FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cursor.fetchone()
        return row["team_name"] if row else None


def get_user_warn_count(user_id: int) -> int:
    """Get current warn count for user."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT warn_count FROM users WHERE telegram_id = ?", (user_id,))
        row = cursor.fetchone()
        return row["warn_count"] if row and row["warn_count"] is not None else 0



def add_warn(user_id: int, admin_id: int | None, reason: str) -> tuple[int, bool]:
    from config import MAX_WARNS_LIMIT
    with transaction() as conn:
        cursor = conn.cursor()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Atomic increment inside the write transaction: two concurrent callers
        # can no longer read the same warn_count and lose an increment.
        cursor.execute("UPDATE users SET warn_count = warn_count + 1 WHERE telegram_id = ?", (user_id,))
        if cursor.rowcount == 0:
            return 0, False
        cursor.execute("SELECT warn_count FROM users WHERE telegram_id = ?", (user_id,))
        new_count = cursor.fetchone()["warn_count"] or 0

        cursor.execute(
            "INSERT INTO user_warns (user_id, admin_id, reason, type, created_at) VALUES (?, ?, ?, 'WARN_ADD', ?)",
            (user_id, admin_id, reason, now_str)
        )
        is_exceeded = new_count >= MAX_WARNS_LIMIT
        return new_count, is_exceeded


def remove_warn(user_id: int, admin_id: int | None, reason: str) -> tuple[int, bool]:
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT warn_count FROM users WHERE telegram_id = ?", (user_id,))
        row = cursor.fetchone()
        current_count = row["warn_count"] if row and row["warn_count"] is not None else 0
        
        if current_count <= 0:
            return 0, False
            
        new_count = max(0, current_count - 1)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("UPDATE users SET warn_count = ? WHERE telegram_id = ?", (new_count, user_id))
        cursor.execute(
            "INSERT INTO user_warns (user_id, admin_id, reason, type, created_at) VALUES (?, ?, ?, 'WARN_REMOVE', ?)",
            (user_id, admin_id, reason, now_str)
        )
        return new_count, True


def get_user_warns(user_id: int) -> list[dict]:
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, admin_id, reason, type, created_at
            FROM user_warns
            WHERE user_id = ?
            ORDER BY created_at DESC
        """, (user_id,))
        return [dict(row) for row in cursor.fetchall()]


def ban_and_remove_from_league(user_id: int) -> str | None:
    from config import MAX_WARNS_LIMIT
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT team_name FROM users WHERE telegram_id = ?", (user_id,))
        row = cursor.fetchone()
        team_name = row["team_name"] if row else None

        cursor.execute("UPDATE users SET team_name = NULL, warn_count = 0 WHERE telegram_id = ?", (user_id,))
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO user_warns (user_id, admin_id, reason, type, created_at) VALUES (?, NULL, ?, 'AUTO_KICK', ?)",
            (user_id, f"Превышен лимит варнов ({MAX_WARNS_LIMIT}/{MAX_WARNS_LIMIT}). Авто-удаление из клуба.", now_str)
        )
        return team_name


def reset_season_warns() -> None:
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET warn_count = 0")
        cursor.execute("DELETE FROM user_warns")


def amnesty_player(user_id: int, admin_id: int | None = None) -> None:
    with transaction() as conn:
        cursor = conn.cursor()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("UPDATE users SET warn_count = 0 WHERE telegram_id = ?", (user_id,))
        cursor.execute(
            "INSERT INTO user_warns (user_id, admin_id, reason, type, created_at) VALUES (?, ?, 'Амнистия (сброс варнов)', 'WARN_REMOVE', ?)",
            (user_id, admin_id, now_str)
        )


def reset_user_warns(user_id: int, admin_id: int | None = None) -> None:
    """Reset all warns for a specific user to 0."""
    amnesty_player(user_id, admin_id)


def find_user_by_ref(ref: str) -> dict | None:
    """Find user by telegram_id, @username, or team_name."""
    ref_clean = ref.strip().lstrip("@")
    with transaction() as conn:
        cursor = conn.cursor()
        if ref_clean.isdigit():
            cursor.execute("SELECT telegram_id, username, team_name, role, warn_count FROM users WHERE telegram_id = ?", (int(ref_clean),))
            r = cursor.fetchone()
            if r:
                return dict(r)
        
        cursor.execute("SELECT telegram_id, username, team_name, role, warn_count FROM users WHERE LOWER(username) = LOWER(?)", (ref_clean,))
        r = cursor.fetchone()
        if r:
            return dict(r)
            
        cursor.execute("SELECT telegram_id, username, team_name, role, warn_count FROM users WHERE LOWER(team_name) = LOWER(?)", (ref_clean,))
        r = cursor.fetchone()
        if r:
            return dict(r)
        return None


def get_all_active_warns() -> list[dict]:
    """Retrieve all active league users (who currently own a club) with warn_count > 0."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT telegram_id, username, team_name, warn_count 
            FROM users 
            WHERE warn_count > 0 AND team_name IS NOT NULL AND TRIM(team_name) != ''
            ORDER BY warn_count DESC, username ASC
        """)
        return [dict(row) for row in cursor.fetchall()]


# --- Squad management ---

def get_all_teams() -> list[str]:
    """Retrieve all unique team names from users table."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT team_name FROM users WHERE team_name IS NOT NULL AND team_name != ''")
        return [r[0] for r in cursor.fetchall()]

def get_team_squad_photo(team_name: str) -> str | None:
    """Retrieve squad_photo_id for a team name."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT squad_photo_id FROM users WHERE LOWER(team_name) = LOWER(?) AND squad_photo_id IS NOT NULL AND squad_photo_id != ''",
            (team_name.strip(),)
        )
        row = cursor.fetchone()
        return row[0] if row and row[0] else None

def save_squad_players(team_name: str, player_names: list) -> int:
    """Save or add players to a club squad."""
    return add_squad(team_name, player_names)

def add_squad(team_name: str, player_names: list) -> int:
    """
    Add players to a club's squad with authentic positions.
    Accepts list of names: ["Vinicius Jr", ...] or tuples: [("Vinicius Jr", "LW"), ...] or dicts.
    Auto-detects authentic real-world position if not specified.
    """
    from services.player_positions import detect_player_position, normalize_position

    added = 0
    with transaction() as conn:
        cursor = conn.cursor()
        for item in player_names:
            pos = None
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                name, pos = item[0], item[1]
            elif isinstance(item, dict):
                name = item.get("player_name") or item.get("name")
                pos = item.get("position") or item.get("pos")
            else:
                name = str(item)

            clean = name.strip() if name else ""
            if not clean or len(clean) > 50:
                continue

            if not pos:
                pos = detect_player_position(clean, team_name)
            else:
                pos = normalize_position(pos)

            try:
                cursor.execute(
                    """
                    INSERT INTO squad_players (team_name, player_name, position) 
                    VALUES (?, ?, ?)
                    ON CONFLICT(team_name, player_name) DO UPDATE SET
                        position = COALESCE(excluded.position, squad_players.position)
                    """,
                    (team_name.strip(), clean, pos)
                )
                if cursor.rowcount > 0:
                    added += 1
            except sqlite3.Error as e:
                logger.warning(f"Failed to add player '{clean}' to {team_name}: {e}")
    return added


def get_squad(team_name: str) -> list[str]:
    """Get list of player names in a club's squad."""
    if not team_name:
        return []
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT player_name FROM squad_players WHERE LOWER(team_name) = LOWER(?) ORDER BY id ASC",
            (team_name.strip(),)
        )
        res = [row["player_name"] for row in cursor.fetchall()]
        if res:
            return res
        
        # Fallback with fuzzy matching
        cursor.execute("SELECT DISTINCT team_name FROM squad_players")
        all_t = [r["team_name"] for r in cursor.fetchall()]
        for t in all_t:
            if teams_match(t, team_name):
                cursor.execute("SELECT player_name FROM squad_players WHERE team_name = ? ORDER BY id ASC", (t,))
                return [row["player_name"] for row in cursor.fetchall()]
        return []


def get_squad_with_positions(team_name: str) -> list[dict]:
    """Get list of players with their positions: [{'player_name': ..., 'position': ...}]."""
    from services.player_positions import detect_player_position

    if not team_name:
        return []
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT player_name, position FROM squad_players WHERE LOWER(team_name) = LOWER(?) ORDER BY id ASC",
            (team_name.strip(),)
        )
        rows = cursor.fetchall()
        if not rows:
            cursor.execute("SELECT DISTINCT team_name FROM squad_players")
            all_t = [r["team_name"] for r in cursor.fetchall()]
            for t in all_t:
                if teams_match(t, team_name):
                    cursor.execute("SELECT player_name, position FROM squad_players WHERE team_name = ? ORDER BY id ASC", (t,))
                    rows = cursor.fetchall()
                    break

        result = []
        for r in rows:
            p_name = r["player_name"]
            pos = r["position"]
            if not pos:
                pos = detect_player_position(p_name, team_name)
                # Auto-heal position in background DB
                cursor.execute(
                    "UPDATE squad_players SET position = ? WHERE LOWER(team_name) = LOWER(?) AND LOWER(player_name) = LOWER(?)",
                    (pos, team_name.strip(), p_name.strip())
                )
            result.append({"player_name": p_name, "position": pos})
        return result


def get_player_position(player_name: str, team_name: str | None = None) -> str:
    """
    Get the authentic position of a player (e.g. ST, LW, RW, CAM, CB, GK).
    Checks DB squad_players, resolves canonical/online position, and caches in DB.
    """
    from services.player_positions import detect_player_position, normalize_position

    if not player_name:
        return "ST"

    p_clean = player_name.strip()
    with transaction() as conn:
        cursor = conn.cursor()
        if team_name:
            cursor.execute(
                "SELECT position FROM squad_players WHERE LOWER(player_name) = LOWER(?) AND LOWER(team_name) = LOWER(?)",
                (p_clean, team_name.strip())
            )
            row = cursor.fetchone()
            if row and row["position"]:
                return row["position"]

        cursor.execute(
            "SELECT position FROM squad_players WHERE LOWER(player_name) = LOWER(?) AND position IS NOT NULL LIMIT 1",
            (p_clean,)
        )
        row = cursor.fetchone()
        if row and row["position"]:
            return row["position"]

        # Resolve position dynamically
        pos = detect_player_position(p_clean, team_name)
        if team_name and pos:
            cursor.execute(
                "UPDATE squad_players SET position = ? WHERE LOWER(player_name) = LOWER(?) AND LOWER(team_name) = LOWER(?)",
                (pos, p_clean, team_name.strip())
            )
        return pos


def set_player_position(player_name: str, team_name: str, position: str) -> bool:
    """Set or update the authentic position for a player in a squad."""
    from services.player_positions import normalize_position

    norm_pos = normalize_position(position)
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO squad_players (team_name, player_name, position)
            VALUES (?, ?, ?)
            ON CONFLICT(team_name, player_name) DO UPDATE SET position = excluded.position
            """,
            (team_name.strip(), player_name.strip(), norm_pos)
        )
        return cursor.rowcount > 0


def clear_squad(team_name: str) -> int:
    """Remove all players from a club's squad. Returns count of deleted players."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM squad_players WHERE LOWER(team_name) = LOWER(?)",
            (team_name.strip(),)
        )
        return cursor.rowcount


def remove_player_from_squad(team_name: str, player_name: str) -> bool:
    """Remove a single player from a club's squad."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM squad_players WHERE LOWER(team_name) = LOWER(?) AND LOWER(player_name) = LOWER(?)",
            (team_name.strip(), player_name.strip())
        )
        return cursor.rowcount > 0


def get_missing_squad_players(team_name: str) -> list[str]:
    """Return player names that appear in match_events for a club but are absent from its squad."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT me.player_name
            FROM match_events me
            LEFT JOIN squad_players sp
              ON LOWER(sp.player_name) = LOWER(me.player_name)
             AND LOWER(sp.team_name) = LOWER(me.team_name)
            WHERE LOWER(me.team_name) = LOWER(?)
              AND me.player_name IS NOT NULL AND me.player_name != ''
              AND sp.id IS NULL
            ORDER BY me.player_name COLLATE NOCASE ASC
        """, (team_name.strip(),))
        return [row["player_name"] for row in cursor.fetchall()]


def add_missing_squad_players(team_name: str | None = None) -> int:
    """Add to club squads any players that appear in match_events but are not yet in the squad.

    If team_name is given, only that club is processed; otherwise all clubs are processed.
    Returns the total number of players added.
    """
    added = 0
    with transaction() as conn:
        cursor = conn.cursor()
        if team_name:
            cursor.execute(
                "SELECT DISTINCT me.player_name, me.team_name FROM match_events me WHERE LOWER(me.team_name) = LOWER(?)",
                (team_name.strip(),)
            )
        else:
            cursor.execute(
                "SELECT DISTINCT me.player_name, me.team_name FROM match_events me"
            )
        rows = cursor.fetchall()
        for row in rows:
            pname = row["player_name"]
            tname = row["team_name"]
            if not pname or not tname:
                continue
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO squad_players (team_name, player_name) VALUES (?, ?)",
                    (tname.strip(), pname.strip())
                )
                if cursor.rowcount > 0:
                    added += 1
            except sqlite3.Error as e:
                logger.warning(f"Failed to add player '{pname}' to {tname}: {e}")
    return added


def get_club_top_scorers(team_name: str) -> list[dict]:
    """Get top goal scorers for a club across confirmed matches."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT me.player_name, SUM(me.count) as total
            FROM match_events me
            JOIN matches m ON me.match_id = m.id
            WHERE LOWER(me.team_name) = LOWER(?) AND me.event_type = 'goal' AND m.status = 'confirmed'
            GROUP BY me.player_name
            ORDER BY total DESC, me.player_name ASC
        """, (team_name.strip(),))
        return [{"player_name": row["player_name"], "total": row["total"]} for row in cursor.fetchall()]


def clear_all_matches(season_id: int | None = None) -> None:
    """Clear all matches from the database for a specific season or all seasons."""
    with transaction() as conn:
        cursor = conn.cursor()
        if season_id is not None:
            cursor.execute("DELETE FROM matches WHERE season_id = ?", (season_id,))
            cursor.execute("DELETE FROM rounds WHERE season_id = ?", (season_id,))
        else:
            cursor.execute("DELETE FROM matches")
            cursor.execute("DELETE FROM rounds")


def clear_matches_by_division(division_id: int, season_id: int | None = None) -> None:
    """Clear matches and rounds for a specific division and season."""
    with transaction() as conn:
        cursor = conn.cursor()
        if season_id is None:
            act = get_active_season()
            s_id = act["id"] if act else 1
        else:
            s_id = season_id
        cursor.execute("DELETE FROM matches WHERE division_id = ? AND (season_id = ? OR season_id IS NULL)", (division_id, s_id))
        cursor.execute("DELETE FROM rounds WHERE division_id = ? AND (season_id = ? OR season_id IS NULL)", (division_id, s_id))


def batch_insert_matches(fixtures: list[tuple[int, int, int]], division_id: int | None = None, season_id: int | None = None) -> None:
    """Insert a list of matches. fixtures format: (round_number, p1, p2)"""
    valid_fixtures = [f for f in fixtures if f[1] != f[2]]
    if not valid_fixtures:
        return
    if season_id is None:
        act = get_active_season()
        s_id = act["id"] if act else 1
    else:
        s_id = season_id

    div_id = division_id if division_id is not None else 1
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO matches (round_number, player1_id, player2_id, status, division_id, season_id) VALUES (?, ?, ?, 'pending', ?, ?)",
            [(f[0], f[1], f[2], division_id, s_id) for f in valid_fixtures]
        )
        rounds = set([f[0] for f in valid_fixtures])
        cursor.executemany(
            "INSERT OR IGNORE INTO rounds (season_id, division_id, round_number, is_open, deadline) VALUES (?, ?, ?, 0, NULL)",
            [(s_id, div_id, r) for r in rounds]
        )


def get_round_info(round_number: int, division_id: int | None = None, season_id: int | None = None) -> dict | None:
    with transaction() as conn:
        cursor = conn.cursor()
        if season_id is None:
            act = get_active_season()
            s_id = act["id"] if act else 1
        else:
            s_id = season_id

        if division_id is not None:
            cursor.execute(
                "SELECT round_number, is_open, deadline, division_id, season_id FROM rounds WHERE (season_id = ? OR season_id IS NULL) AND division_id = ? AND round_number = ?",
                (s_id, division_id, round_number)
            )
        else:
            cursor.execute(
                "SELECT round_number, is_open, deadline, division_id, season_id FROM rounds WHERE (season_id = ? OR season_id IS NULL) AND round_number = ? LIMIT 1",
                (s_id, round_number)
            )
        row = cursor.fetchone()
        return dict(row) if row else None


def update_round_status(round_number: int, is_open: bool, deadline: str | None = None, division_id: int | None = None, season_id: int | None = None) -> None:
    """Open/close a round. When opening without an explicit deadline, any stale
    stored deadline is cleared so it cannot instantly mark matches as overdue.
    Whenever the deadline is (re)set, per-round reminder flags are reset so
    the 24h/6h/1h pipeline works for the new deadline window."""
    if season_id is None:
        act = get_active_season()
        s_id = act["id"] if act else 1
    else:
        s_id = season_id

    if is_open:
        act = get_season(s_id)
        if act and act.get("status") not in ("active", None):
            raise ValueError(f"Cannot open round in season #{s_id} with status '{act.get('status')}'")

    with transaction() as conn:
        cursor = conn.cursor()
        if is_open:
            if division_id is not None:
                cursor.execute(
                    "INSERT OR IGNORE INTO rounds (season_id, division_id, round_number, is_open, deadline) VALUES (?, ?, ?, 0, NULL)",
                    (s_id, division_id, round_number)
                )
            else:
                cursor.execute(
                    "INSERT OR IGNORE INTO rounds (season_id, division_id, round_number, is_open, deadline) VALUES (?, 1, ?, 0, NULL)",
                    (s_id, round_number)
                )

        if division_id is not None:
            if deadline is not None:
                cursor.execute(
                    "UPDATE rounds SET is_open = ?, deadline = ? WHERE (season_id = ? OR season_id IS NULL) AND division_id = ? AND round_number = ?",
                    (1 if is_open else 0, deadline, s_id, division_id, round_number)
                )
            elif is_open:
                cursor.execute(
                    "UPDATE rounds SET is_open = ?, deadline = NULL WHERE (season_id = ? OR season_id IS NULL) AND division_id = ? AND round_number = ?",
                    (1, s_id, division_id, round_number)
                )
            else:
                cursor.execute(
                    "UPDATE rounds SET is_open = ? WHERE (season_id = ? OR season_id IS NULL) AND division_id = ? AND round_number = ?",
                    (0, s_id, division_id, round_number)
                )
                cursor.execute("UPDATE bet_markets SET is_active = 0 WHERE tour = ?", (round_number,))

            if deadline is not None:
                cursor.execute(
                    "DELETE FROM round_reminders WHERE round_number = ? AND (division_id = ? OR division_id IS NULL)",
                    (round_number, division_id)
                )
        else:
            if deadline is not None:
                cursor.execute(
                    "UPDATE rounds SET is_open = ?, deadline = ? WHERE (season_id = ? OR season_id IS NULL) AND round_number = ?",
                    (1 if is_open else 0, deadline, s_id, round_number)
                )
            elif is_open:
                # Re-opening without a new deadline: drop the stale one
                cursor.execute(
                    "UPDATE rounds SET is_open = ?, deadline = NULL WHERE (season_id = ? OR season_id IS NULL) AND round_number = ?",
                    (1, s_id, round_number)
                )
            else:
                cursor.execute(
                    "UPDATE rounds SET is_open = ? WHERE (season_id = ? OR season_id IS NULL) AND round_number = ?",
                    (0, s_id, round_number)
                )
                # 🎰 Close betting line when round is closed
                cursor.execute("UPDATE bet_markets SET is_active = 0 WHERE tour = ?", (round_number,))

            if deadline is not None:
                cursor.execute("DELETE FROM round_reminders WHERE round_number = ?", (round_number,))

    if is_open:
        # 🎰 Auto-generate betting line when round is opened
        try:
            from services.betting_engine import generate_round_markets
            generate_round_markets(round_number, division_id=division_id, season_id=s_id)
        except Exception as e:
            logger.exception(f"Error generating betting line for round {round_number}: {e}")


def get_all_rounds() -> list[int]:
    """Get a list of all round numbers present in the database."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT round_number FROM matches WHERE round_number > 0 ORDER BY round_number ASC")
        return [row["round_number"] for row in cursor.fetchall()]

def get_club_top_assisters(team_name: str) -> list[dict]:
    """Get top assist providers for a club across confirmed matches."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT me.player_name, SUM(me.count) as total
            FROM match_events me
            JOIN matches m ON me.match_id = m.id
            WHERE LOWER(me.team_name) = LOWER(?) AND me.event_type = 'assist' AND m.status = 'confirmed'
            GROUP BY me.player_name
            ORDER BY total DESC, me.player_name ASC
        """, (team_name.strip(),))
        return [{"player_name": row["player_name"], "total": row["total"]} for row in cursor.fetchall()]


def get_unplayed_matches_in_round(round_number: int) -> list[dict]:
    """Get all unplayed (pending) matches for a specific round with user details."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT m.id, m.round_number,
                   u1.telegram_id AS player1_id, u1.username as p1_username, u1.team_name as p1_team,
                   u2.telegram_id AS player2_id, u2.username as p2_username, u2.team_name as p2_team
            FROM matches m
            LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
            LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
            WHERE m.round_number = ? AND m.status = 'pending'
            ORDER BY m.id ASC
        """, (round_number,))
        return [dict(row) for row in cursor.fetchall()]

def get_top_scorers(limit: int = 20, division_id: int | None = None) -> list[dict]:
    """Get top goalscorers in the league aggregated from match_events (strictly confirmed league matches, round_number > 0)."""
    with transaction() as conn:
        cursor = conn.cursor()
        query = """
            SELECT me.player_name, me.team_name, SUM(me.count) AS total_goals
            FROM match_events me
            JOIN matches m ON me.match_id = m.id
            WHERE me.event_type = 'goal'
              AND (m.tournament_type IS NULL OR m.tournament_type = 'league')
              AND m.round_number > 0
              AND m.status = 'confirmed'
        """
        params = []
        if division_id is not None:
            query += " AND m.division_id = ?"
            params.append(division_id)
        query += """
            GROUP BY me.player_name, me.team_name
            ORDER BY total_goals DESC, me.player_name ASC
            LIMIT ?
        """
        params.append(limit)
        cursor.execute(query, tuple(params))
        return [dict(row) for row in cursor.fetchall()]

def get_top_assists(limit: int = 20, division_id: int | None = None) -> list[dict]:
    """Get top assist providers in the league aggregated from match_events (strictly confirmed league matches, round_number > 0)."""
    with transaction() as conn:
        cursor = conn.cursor()
        query = """
            SELECT me.player_name, me.team_name, SUM(me.count) AS total_assists
            FROM match_events me
            JOIN matches m ON me.match_id = m.id
            WHERE me.event_type = 'assist'
              AND (m.tournament_type IS NULL OR m.tournament_type = 'league')
              AND m.round_number > 0
              AND m.status = 'confirmed'
        """
        params = []
        if division_id is not None:
            query += " AND m.division_id = ?"
            params.append(division_id)
        query += """
            GROUP BY me.player_name, me.team_name
            ORDER BY total_assists DESC, me.player_name ASC
            LIMIT ?
        """
        params.append(limit)
        cursor.execute(query, tuple(params))
        return [dict(row) for row in cursor.fetchall()]

def get_recent_confirmed_matches(limit: int = 15) -> list[dict]:
    """Retrieve recent confirmed matches across the league."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                m.id, m.round_number, m.player1_score, m.player2_score,
                u1.team_name AS team1, u1.username AS user1,
                u2.team_name AS team2, u2.username AS user2
            FROM matches m
            LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
            LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
            WHERE m.status = 'confirmed'
            ORDER BY m.id DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

def get_all_squads() -> dict[str, list[str]]:
    """Retrieve all player squads grouped by team_name."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT team_name, player_name FROM squad_players ORDER BY team_name, id ASC")
        squads = {}
        for row in cursor.fetchall():
            team = row["team_name"]
            if team not in squads:
                squads[team] = []
            squads[team].append(row["player_name"])
        return squads


def get_all_unique_players() -> list[tuple[str, str]]:
    """Retrieve all unique (player_name, team_name) tuples from squad_players and match_events."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT player_name, team_name FROM squad_players
            UNION
            SELECT player_name, team_name FROM match_events WHERE player_name IS NOT NULL AND player_name != ''
            ORDER BY team_name, player_name ASC
        """)
        return [(row["player_name"], row["team_name"]) for row in cursor.fetchall()]


def detect_teams_from_players(
    side1_players: list[str],
    side2_players: list[str],
    caption: str | None = None
) -> tuple[str | None, str | None]:
    """
    Determines team1 (left side) and team2 (right side) from OCR-extracted player names
    (goals and assists) by matching them against club squads in the database.
    Also incorporates any club names mentioned in user caption.
    Returns (team1_name, team2_name).
    """
    all_squads = get_all_squads()
    all_teams = get_all_teams()
    for t in all_teams:
        if t not in all_squads:
            all_squads[t] = []

    caption_clean = (caption or "").lower()
    caption_teams = []
    for t in all_teams:
        t_clean = t.lower()
        t_norm = normalize_team_name(t)
        if t_clean in caption_clean or (t_norm and t_norm in normalize_team_name(caption_clean)):
            caption_teams.append(t)

    # Check words in caption with resolve_team_name
    if caption:
        for word in re.split(r'[\s,:;\-_/\\|]+', caption):
            resolved = resolve_team_name(word)
            if resolved and resolved in all_teams and resolved not in caption_teams:
                caption_teams.append(resolved)

    def score_side(player_list, squad_list):
        score = 0
        for p in player_list:
            if not p:
                continue
            p_clean = str(p).strip().lower()
            if not p_clean:
                continue
            for sp in squad_list:
                sp_clean = str(sp).strip().lower()
                if p_clean == sp_clean:
                    score += 6
                    break
                elif len(p_clean) >= 4 and (p_clean in sp_clean or sp_clean in p_clean):
                    score += 4
                    break
                else:
                    p_parts = [x for x in p_clean.split() if len(x) >= 3]
                    sp_parts = [x for x in sp_clean.split() if len(x) >= 3]
                    if p_parts and sp_parts and any(x in sp_parts for x in p_parts):
                        score += 3
                        break
        return score

    # Compute scores for every team on side1 and side2
    side1_scores = {}
    side2_scores = {}
    for team, squad in all_squads.items():
        s1 = score_side(side1_players, squad)
        s2 = score_side(side2_players, squad)
        # If team is explicitly in caption, give a bonus
        if team in caption_teams:
            s1 += 2
            s2 += 2
        side1_scores[team] = s1
        side2_scores[team] = s2

    s1_sorted = sorted(side1_scores.items(), key=lambda x: x[1], reverse=True)
    s2_sorted = sorted(side2_scores.items(), key=lambda x: x[1], reverse=True)

    best_t1, best_s1 = s1_sorted[0] if s1_sorted else (None, 0)

    best_t2 = None
    best_s2 = 0
    for t, s in s2_sorted:
        if t != best_t1:
            best_t2 = t
            best_s2 = s
            break

    # If side1 or side2 had no player matches, fill from caption if available
    if (best_s1 == 0 or not best_t1) and caption_teams:
        for ct in caption_teams:
            if ct != best_t2:
                best_t1 = ct
                break
    if (best_s2 == 0 or not best_t2) and caption_teams:
        for ct in caption_teams:
            if ct != best_t1:
                best_t2 = ct
                break

    return best_t1, best_t2




def get_player_card_stats(player_name: str, team_name: str) -> dict:
    """
    Get full season stats for a specific player:
    - total goals and assists (overall, league, cup)
    - per-round / per-stage breakdown.
    Only considers confirmed matches.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        p_name = player_name.strip()
        t_name = team_name.strip()

        # 1. Total overall goals & assists + breakdown by tournament
        cursor.execute("""
            SELECT 
                COALESCE(SUM(CASE WHEN me.event_type = 'goal' THEN me.count ELSE 0 END), 0) AS total_goals,
                COALESCE(SUM(CASE WHEN me.event_type = 'assist' THEN me.count ELSE 0 END), 0) AS total_assists,
                COALESCE(SUM(CASE WHEN me.event_type = 'goal' AND (m.tournament_type IS NULL OR m.tournament_type = 'league') AND m.round_number > 0 THEN me.count ELSE 0 END), 0) AS league_goals,
                COALESCE(SUM(CASE WHEN me.event_type = 'assist' AND (m.tournament_type IS NULL OR m.tournament_type = 'league') AND m.round_number > 0 THEN me.count ELSE 0 END), 0) AS league_assists,
                COALESCE(SUM(CASE WHEN me.event_type = 'goal' AND (m.tournament_type = 'cup' OR m.round_number = -1 OR (m.cup_series_id IS NOT NULL AND m.cup_series_id > 0)) THEN me.count ELSE 0 END), 0) AS cup_goals,
                COALESCE(SUM(CASE WHEN me.event_type = 'assist' AND (m.tournament_type = 'cup' OR m.round_number = -1 OR (m.cup_series_id IS NOT NULL AND m.cup_series_id > 0)) THEN me.count ELSE 0 END), 0) AS cup_assists
            FROM match_events me
            JOIN matches m ON me.match_id = m.id
            WHERE LOWER(me.player_name) = LOWER(?)
              AND LOWER(me.team_name) = LOWER(?)
              AND m.status = 'confirmed'
        """, (p_name, t_name))
        summary_row = cursor.fetchone()
        summary_dict = dict(summary_row) if summary_row else {}
        
        total_goals = summary_dict.get("total_goals", 0)
        total_assists = summary_dict.get("total_assists", 0)
        league_goals = summary_dict.get("league_goals", 0)
        league_assists = summary_dict.get("league_assists", 0)
        cup_goals = summary_dict.get("cup_goals", 0)
        cup_assists = summary_dict.get("cup_assists", 0)

        # 2. Detailed per-tour / stage breakdown
        cursor.execute("""
            SELECT
                m.round_number,
                m.tournament_type,
                m.cup_stage,
                m.cup_series_id,
                CASE 
                    WHEN LOWER(m.player1_team) = LOWER(?) THEN m.player2_team 
                    ELSE m.player1_team 
                END AS opponent,
                me.event_type,
                SUM(me.count) AS total
            FROM match_events me
            JOIN matches m ON me.match_id = m.id
            WHERE LOWER(me.player_name) = LOWER(?)
              AND LOWER(me.team_name) = LOWER(?)
              AND m.status = 'confirmed'
            GROUP BY 
                CASE 
                    WHEN m.tournament_type = 'cup' OR m.round_number = -1 OR (m.cup_series_id IS NOT NULL AND m.cup_series_id > 0) THEN -1
                    ELSE m.round_number
                END,
                me.event_type
            ORDER BY 
                CASE 
                    WHEN m.tournament_type = 'cup' OR m.round_number = -1 OR (m.cup_series_id IS NOT NULL AND m.cup_series_id > 0) THEN -1
                    ELSE m.round_number
                END ASC
        """, (t_name, p_name, t_name))

        rows = cursor.fetchall()
        
        # Build grouped rounds dict: {round_key: {"title": str, "opponent": str, "goals": int, "assists": int, "is_cup": bool}}
        rounds_dict = {}
        for r in rows:
            rn = r["round_number"]
            is_cup = bool(r["tournament_type"] == "cup" or rn == -1 or (r["cup_series_id"] and r["cup_series_id"] > 0))
            opp = "" if is_cup else (r["opponent"] or "")
            
            key = -1 if is_cup else rn
            if key not in rounds_dict:
                title = "Кубок КПЛ" if is_cup else f"Тур {rn}"
                rounds_dict[key] = {
                    "round_key": key,
                    "title": title,
                    "opponent": opp,
                    "goals": 0,
                    "assists": 0,
                    "is_cup": is_cup
                }
            elif opp and not rounds_dict[key].get("opponent"):
                rounds_dict[key]["opponent"] = opp

            if r["event_type"] == "goal":
                rounds_dict[key]["goals"] += r["total"]
            elif r["event_type"] == "assist":
                rounds_dict[key]["assists"] += r["total"]

        items = []
        for k in sorted(rounds_dict.keys()):
            item = rounds_dict[k]
            item["total"] = item["goals"] + item["assists"]
            items.append(item)

        return {
            "player_name": player_name,
            "team_name": team_name,
            "position": get_player_position(player_name, team_name),
            "total_goals": total_goals,
            "total_assists": total_assists,
            "total_points": total_goals + total_assists,
            "league_goals": league_goals,
            "league_assists": league_assists,
            "cup_goals": cup_goals,
            "cup_assists": cup_assists,
            "items": items,
            "rounds": {item["round_key"]: {"goals": item["goals"], "assists": item["assists"]} for item in items},
        }


def get_club_card_data(team_name: str) -> dict:
    """
    Get comprehensive club profile & statistics independent of who the current manager is.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        canon = resolve_team_name(team_name) or team_name.strip()

        # 1. Current Manager
        cursor.execute(
            "SELECT telegram_id, username, warn_count, registered_at, team_name FROM users WHERE team_name IS NOT NULL"
        )
        all_users = cursor.fetchall()
        u_row = None
        for r in all_users:
            if teams_match(r["team_name"], canon):
                u_row = r
                break

        manager = None
        if u_row:
            manager = {
                "telegram_id": u_row["telegram_id"],
                "username": u_row["username"],
                "warn_count": u_row["warn_count"] or 0,
                "registered_at": u_row["registered_at"],
            }

        # 2. Standings & League Stats
        standings = get_standings()
        league_stats = {
            "rank": 0,
            "played": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "goals_scored": 0,
            "goals_conceded": 0,
            "goal_diff": 0,
            "points": 0,
        }
        for rank, row in enumerate(standings, 1):
            if teams_match(row["team_name"], canon):
                league_stats = {
                    "rank": rank,
                    "played": row["played"],
                    "wins": row["wins"],
                    "draws": row["draws"],
                    "losses": row["losses"],
                    "goals_scored": row["goals_scored"],
                    "goals_conceded": row["goals_conceded"],
                    "goal_diff": row["goals_scored"] - row["goals_conceded"],
                    "points": row["points"],
                }
                break

        # 3. Recent Form (Last 5 matches)
        form_map = get_teams_recent_form(limit=5)
        recent_form = form_map.get(canon.lower(), [])

        # 4. Cup Stats
        cursor.execute("""
            SELECT id, stage, series_num, team1_name, team2_name, team1_wins, team2_wins, winner_name, status
            FROM cup_series
            ORDER BY id DESC
        """)
        c_row = None
        for cr in cursor.fetchall():
            if teams_match(cr["team1_name"], canon) or teams_match(cr["team2_name"], canon):
                c_row = cr
                break

        cup_stats = None
        if c_row:
            is_t1 = teams_match(c_row["team1_name"], canon)
            opp_name = c_row["team2_name"] if is_t1 else c_row["team1_name"]
            c_wins = c_row["team1_wins"] if is_t1 else c_row["team2_wins"]
            opp_wins = c_row["team2_wins"] if is_t1 else c_row["team1_wins"]
            cup_stats = {
                "series_id": c_row["id"],
                "stage": c_row["stage"],
                "series_num": c_row["series_num"],
                "opponent": opp_name,
                "club_wins": c_wins,
                "opp_wins": opp_wins,
                "status": c_row["status"],
                "winner_name": c_row["winner_name"],
                "is_winner": bool(c_row["winner_name"] and teams_match(c_row["winner_name"], canon)),
                "is_eliminated": bool(c_row["winner_name"] and not teams_match(c_row["winner_name"], canon)),
            }

        # 5. Top Scorers and Assisters of the Club
        cursor.execute("""
            SELECT 
                me.player_name, me.team_name,
                COALESCE(SUM(CASE WHEN me.event_type = 'goal' THEN me.count ELSE 0 END), 0) AS goals,
                COALESCE(SUM(CASE WHEN me.event_type = 'assist' THEN me.count ELSE 0 END), 0) AS assists
            FROM match_events me
            JOIN matches m ON me.match_id = m.id
            WHERE m.status = 'confirmed'
            GROUP BY LOWER(me.team_name), LOWER(me.player_name)
        """)
        all_events = cursor.fetchall()
        event_players_dict: dict[str, dict] = {}
        for r in all_events:
            if teams_match(r["team_name"], canon):
                p_name = r["player_name"]
                if p_name not in event_players_dict:
                    event_players_dict[p_name] = {"player_name": p_name, "goals": 0, "assists": 0}
                event_players_dict[p_name]["goals"] += r["goals"]
                event_players_dict[p_name]["assists"] += r["assists"]
        event_players = list(event_players_dict.values())
        
        top_scorers = [p for p in sorted(event_players, key=lambda x: (x["goals"], x["assists"]), reverse=True) if p["goals"] > 0][:5]
        top_assists = [p for p in sorted(event_players, key=lambda x: (x["assists"], x["goals"]), reverse=True) if p["assists"] > 0][:5]

        # 6. Registered Squad
        cursor.execute(
            "SELECT player_name, team_name FROM squad_players ORDER BY id ASC"
        )
        squad_names = [r["player_name"] for r in cursor.fetchall() if teams_match(r["team_name"], canon)]

        # 7. Unplayed Matches & Debts
        cursor.execute("SELECT round_number, is_open, deadline FROM rounds")
        rounds_rows = cursor.fetchall()
        round_info_map: dict[int, dict] = {}
        max_open_round = 0
        for r_num, is_open, dl_str in rounds_rows:
            parsed_dl = parse_flexible_datetime(dl_str)
            if is_open and r_num > max_open_round:
                max_open_round = r_num
            round_info_map[r_num] = {
                "is_open": bool(is_open),
                "deadline_str": dl_str,
                "deadline_dt": parsed_dl
            }

        cursor.execute("""
            SELECT 
                m.id, m.round_number, m.tournament_type, m.cup_stage, m.game_num_in_series,
                m.player1_team, m.player2_team
            FROM matches m
            WHERE m.status = 'pending'
            ORDER BY 
                CASE WHEN m.tournament_type = 'cup' OR m.round_number = -1 THEN 999 ELSE m.round_number END ASC,
                m.id ASC
        """)
        pending_matches = []
        debts_count = 0
        now_dt = datetime.datetime.now()
        start_dt = get_debt_tracking_start_datetime()

        for pm in cursor.fetchall():
            p1_t = pm["player1_team"] or ""
            p2_t = pm["player2_team"] or ""
            if not (teams_match(p1_t, canon) or teams_match(p2_t, canon)):
                continue

            is_p1 = teams_match(p1_t, canon)
            opp = p2_t if is_p1 else p1_t
            is_cup = bool(pm["tournament_type"] == "cup" or pm["round_number"] == -1)
            
            rn = pm["round_number"]
            r_info = round_info_map.get(rn)
            dl_dt = r_info.get("deadline_dt") if r_info else None
            is_open = r_info.get("is_open", False) if r_info else False

            overdue = False
            if not is_cup:
                if dl_dt and dl_dt <= now_dt:
                    if start_dt:
                        overdue = bool(now_dt >= max(dl_dt, start_dt))
                    else:
                        overdue = True
                elif dl_dt and dl_dt > now_dt:
                    overdue = False
                elif is_open and dl_dt is None:
                    if start_dt and now_dt >= start_dt:
                        overdue = True
                elif max_open_round > 0 and rn < max_open_round:
                    if start_dt and now_dt >= start_dt:
                        overdue = True
                elif not is_open and r_info and max_open_round > 0 and rn <= max_open_round:
                    if start_dt and now_dt >= start_dt:
                        overdue = True
            else:
                # Cup matches: overdue only if recorded in debt reminders
                cursor.execute("SELECT 1 FROM debt_reminders WHERE match_id = ? LIMIT 1", (pm["id"],))
                if cursor.fetchone():
                    overdue = True

            if overdue:
                debts_count += 1

            pending_matches.append({
                "match_id": pm["id"],
                "round_number": pm["round_number"],
                "tournament_type": pm["tournament_type"],
                "cup_stage": pm["cup_stage"],
                "game_num": pm["game_num_in_series"],
                "opponent": opp,
                "is_overdue": overdue,
                "deadline": r_info.get("deadline_str") if r_info else None,
            })

        return {
            "team_name": canon,
            "manager": manager,
            "league_stats": league_stats,
            "recent_form": recent_form,
            "cup_stats": cup_stats,
            "top_scorers": top_scorers,
            "top_assists": top_assists,
            "squad_names": squad_names,
            "squad_count": len(squad_names),
            "pending_matches": pending_matches,
            "debts_count": debts_count,
        }


def get_club_squad_stats(team_name: str) -> list[dict]:
    """
    Get full list of squad players for a club with their individual goal and assist stats.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        canon = resolve_team_name(team_name) or team_name.strip()

        cursor.execute(
            "SELECT player_name, team_name FROM squad_players ORDER BY id ASC"
        )
        squad_names = [r["player_name"] for r in cursor.fetchall() if teams_match(r["team_name"], canon)]

        cursor.execute("""
            SELECT 
                me.player_name, me.team_name,
                COALESCE(SUM(CASE WHEN me.event_type = 'goal' THEN me.count ELSE 0 END), 0) AS goals,
                COALESCE(SUM(CASE WHEN me.event_type = 'assist' THEN me.count ELSE 0 END), 0) AS assists
            FROM match_events me
            JOIN matches m ON me.match_id = m.id
            WHERE m.status = 'confirmed'
            GROUP BY LOWER(me.team_name), LOWER(me.player_name)
        """)
        stats_map: dict[str, dict] = {}
        for r in cursor.fetchall():
            if teams_match(r["team_name"], canon):
                p_l = r["player_name"].lower()
                if p_l not in stats_map:
                    stats_map[p_l] = {"goals": 0, "assists": 0}
                stats_map[p_l]["goals"] += r["goals"]
                stats_map[p_l]["assists"] += r["assists"]

        result = []
        seen = set()
        for p_name in squad_names:
            p_lower = p_name.lower()
            seen.add(p_lower)
            p_stat = stats_map.get(p_lower, {"goals": 0, "assists": 0})
            result.append({
                "player_name": p_name,
                "goals": p_stat["goals"],
                "assists": p_stat["assists"],
                "points": p_stat["goals"] + p_stat["assists"],
                "is_registered": True,
            })

        for p_lower, p_stat in stats_map.items():
            if p_lower not in seen:
                result.append({
                    "player_name": p_lower.title(),
                    "goals": p_stat["goals"],
                    "assists": p_stat["assists"],
                    "points": p_stat["goals"] + p_stat["assists"],
                    "is_registered": False,
                })

        return sorted(result, key=lambda x: (x["goals"], x["assists"], x["player_name"]), reverse=True)


def get_club_match_history(team_name: str, limit: int = 20) -> list[dict]:
    """
    Retrieve chronological match history for a club (League + Cup).
    """
    with transaction() as conn:
        cursor = conn.cursor()
        canon = resolve_team_name(team_name) or team_name.strip()

        cursor.execute("""
            SELECT 
                m.id, m.round_number, m.tournament_type, m.cup_stage, m.cup_series_id, m.game_num_in_series,
                m.player1_team, m.player2_team, m.player1_score, m.player2_score, m.status,
                u1.username AS p1_username, u2.username AS p2_username
            FROM matches m
            LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
            LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
            WHERE m.status = 'confirmed' AND (LOWER(m.player1_team) = LOWER(?) OR LOWER(m.player2_team) = LOWER(?))
            ORDER BY 
                CASE WHEN m.tournament_type = 'cup' OR m.round_number = -1 THEN 999 ELSE m.round_number END DESC,
                m.id DESC
            LIMIT ?
        """, (canon, canon, limit))

        matches = []
        for r in cursor.fetchall():
            is_p1 = teams_match(r["player1_team"], canon)
            club_score = r["player1_score"] if is_p1 else r["player2_score"]
            opp_score = r["player2_score"] if is_p1 else r["player1_score"]
            opp_team = r["player2_team"] if is_p1 else r["player1_team"]
            opp_user = r["p2_username"] if is_p1 else r["p1_username"]
            club_user = r["p1_username"] if is_p1 else r["p2_username"]

            if club_score > opp_score: outcome = "W"
            elif club_score < opp_score: outcome = "L"
            else: outcome = "D"

            is_cup = bool(r["tournament_type"] == "cup" or r["round_number"] == -1 or (r["cup_series_id"] and r["cup_series_id"] > 0))

            cursor.execute("""
                SELECT player_name, count 
                FROM match_events 
                WHERE match_id = ? AND LOWER(team_name) = LOWER(?) AND event_type = 'goal'
            """, (r["id"], canon))
            scorers = [f"{g['player_name']} ({g['count']})" if g['count'] > 1 else g['player_name'] for g in cursor.fetchall()]

            matches.append({
                "match_id": r["id"],
                "round_number": r["round_number"],
                "tournament_type": r["tournament_type"],
                "cup_stage": r["cup_stage"],
                "game_num": r["game_num_in_series"],
                "is_cup": is_cup,
                "opponent_team": opp_team,
                "opponent_username": opp_user,
                "club_username": club_user,
                "club_score": club_score,
                "opponent_score": opp_score,
                "outcome": outcome,
                "scorers": scorers,
            })

        return matches


def get_club_schedule_and_results(team_name: str, limit: int = 25) -> dict:
    """
    Retrieve chronological match schedule (both played results and upcoming/pending matches) for a club.
    Aggregates cup series so that 1 row = 1 cup stage/series.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        canon = resolve_team_name(team_name) or team_name.strip()

        # 1. Fetch Cup Series for this club
        cursor.execute("""
            SELECT id, stage, series_num, team1_name, team2_name, team1_wins, team2_wins, winner_name, status
            FROM cup_series
            WHERE (LOWER(team1_name) = LOWER(?) OR LOWER(team2_name) = LOWER(?))
            ORDER BY id ASC
        """, (canon, canon))
        cup_rows = [dict(r) for r in cursor.fetchall()]

        stage_order_map = {"1/8": 1, "1/4": 2, "1/2": 3, "final": 4}
        stage_title_map = {"1/8": "1/8", "1/4": "1/4", "1/2": "1/2", "final": "ФИНАЛ"}

        cup_items = []
        for s in cup_rows:
            s_id = s["id"]
            st_raw = (s.get("stage") or "1/8").lower()
            st_label = stage_title_map.get(st_raw, st_raw.upper())
            tour_title = f"КУБОК • {st_label}"

            t1_name = resolve_team_name(s["team1_name"]) or s["team1_name"]
            t2_name = resolve_team_name(s["team2_name"]) or s["team2_name"]
            is_club_t1 = teams_match(t1_name, canon)
            opp_team = t2_name if is_club_t1 else t1_name

            # Fetch matches for this cup series
            cursor.execute("""
                SELECT id, game_num_in_series, player1_team, player2_team, player1_score, player2_score, status
                FROM matches
                WHERE cup_series_id = ?
                ORDER BY game_num_in_series ASC
            """, (s_id,))
            s_matches = [dict(m) for m in cursor.fetchall()]

            # Fetch goalscorers for the club across all matches in this series
            cursor.execute("""
                SELECT me.player_name, SUM(me.count) AS total_goals
                FROM match_events me
                JOIN matches m ON me.match_id = m.id
                WHERE m.cup_series_id = ? AND LOWER(me.team_name) = LOWER(?) AND me.event_type = 'goal'
                GROUP BY LOWER(me.player_name)
                ORDER BY total_goals DESC, me.player_name ASC
            """, (s_id, canon))
            club_scorers = [f"{g['player_name']} ({g['total_goals']})" if g['total_goals'] > 1 else g['player_name'] for g in cursor.fetchall()]

            # Confirmed games list
            confirmed_matches = [m for m in s_matches if m["status"] == "confirmed" and m["player1_score"] is not None]
            games_scores = [f"{m['player1_score']}:{m['player2_score']}" for m in confirmed_matches]

            has_played_games = len(confirmed_matches) > 0
            is_completed = (s["status"] == "completed")

            w1 = s["team1_wins"] or 0
            w2 = s["team2_wins"] or 0
            winner = s["winner_name"]

            if is_completed and winner:
                outcome = "W" if teams_match(winner, canon) else "L"
            elif has_played_games:
                c_wins = w1 if is_club_t1 else w2
                o_wins = w2 if is_club_t1 else w1
                if c_wins > o_wins:
                    outcome = "W"
                elif c_wins < o_wins:
                    outcome = "L"
                else:
                    outcome = "D"
            else:
                outcome = "PENDING"

            games_str = ", ".join(games_scores)
            if games_str and club_scorers:
                subline = f"Матчи: {games_str} • Голы: {', '.join(club_scorers)}"
            elif games_str:
                subline = f"Матчи: {games_str}"
            elif club_scorers:
                subline = f"Голы клуба: {', '.join(club_scorers)}"
            else:
                subline = ""

            cup_items.append({
                "match_id": s_id,
                "round_number": -1,
                "stage_order": stage_order_map.get(st_raw, 0),
                "tour_title": tour_title,
                "is_cup": True,
                "home_team": t1_name,
                "away_team": t2_name,
                "home_score": w1 if (has_played_games or is_completed) else None,
                "away_score": w2 if (has_played_games or is_completed) else None,
                "club_score": w1 if is_club_t1 else w2,
                "opponent_score": w2 if is_club_t1 else w1,
                "is_home": is_club_t1,
                "opponent_team": opp_team,
                "status": "confirmed" if (is_completed or has_played_games) else "pending",
                "outcome": outcome,
                "scorers": club_scorers,
                "subline": subline,
                "is_completed": is_completed,
                "has_played": has_played_games,
            })

        # 2. Fetch League Matches for this club grouped by round_number
        cursor.execute("""
            SELECT 
                m.id, m.round_number, m.tournament_type,
                m.player1_team, m.player2_team, m.player1_score, m.player2_score, m.status,
                r.is_open, r.deadline,
                u1.username AS p1_username, u2.username AS p2_username
            FROM matches m
            LEFT JOIN rounds r ON m.round_number = r.round_number
            LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
            LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
            WHERE (m.tournament_type IS NULL OR m.tournament_type = 'league' OR m.tournament_type = '')
              AND (m.round_number IS NOT NULL AND m.round_number > 0)
              AND (LOWER(m.player1_team) = LOWER(?) OR LOWER(m.player2_team) = LOWER(?))
            ORDER BY m.round_number ASC, m.id ASC
        """, (canon, canon))
        league_rows = [dict(r) for r in cursor.fetchall()]

        # Group by round_number
        rounds_dict = {}
        for r in league_rows:
            rn = r["round_number"]
            rounds_dict.setdefault(rn, []).append(r)

        league_items = []
        for rn, r_matches in rounds_dict.items():
            first_m = r_matches[0]
            is_p1 = teams_match(first_m["player1_team"], canon)
            home_team = resolve_team_name(first_m["player1_team"]) or first_m["player1_team"]
            away_team = resolve_team_name(first_m["player2_team"]) or first_m["player2_team"]
            opp_team = away_team if is_p1 else home_team
            opp_user = first_m["p2_username"] if is_p1 else first_m["p1_username"]

            tour_title = f"ЛИГА • ТУР {rn}"

            # Collect match events / goals for all matches in this round
            m_ids = [m["id"] for m in r_matches]
            placeholders = ",".join(["?"] * len(m_ids))
            cursor.execute(f"""
                SELECT me.player_name, SUM(me.count) AS cnt
                FROM match_events me
                WHERE me.match_id IN ({placeholders}) AND LOWER(me.team_name) = LOWER(?) AND me.event_type = 'goal'
                GROUP BY LOWER(me.player_name)
                ORDER BY cnt DESC, me.player_name ASC
            """, (*m_ids, canon))
            club_scorers = [f"{g['player_name']} ({g['cnt']})" if g['cnt'] > 1 else g['player_name'] for g in cursor.fetchall()]

            confirmed_matches = [m for m in r_matches if m["status"] == "confirmed" and m["player1_score"] is not None and m["player2_score"] is not None]
            has_played = len(confirmed_matches) > 0
            all_confirmed = len(confirmed_matches) == len(r_matches)

            if len(r_matches) == 1:
                m = r_matches[0]
                h_score = m["player1_score"] if m["status"] == "confirmed" else None
                a_score = m["player2_score"] if m["status"] == "confirmed" else None
                if m["status"] == "confirmed" and h_score is not None and a_score is not None:
                    c_s = m["player1_score"] if is_p1 else m["player2_score"]
                    o_s = m["player2_score"] if is_p1 else m["player1_score"]
                    if c_s > o_s: outcome = "W"
                    elif c_s < o_s: outcome = "L"
                    else: outcome = "D"
                    subline = f"Голы клуба: {', '.join(club_scorers)}" if club_scorers else ""
                else:
                    outcome = "PENDING"
                    subline = ""
            else:
                games_scores = [f"{m['player1_score']}:{m['player2_score']}" for m in confirmed_matches]
                tot_p1 = sum(m["player1_score"] for m in confirmed_matches)
                tot_p2 = sum(m["player2_score"] for m in confirmed_matches)
                h_score = tot_p1 if has_played else None
                a_score = tot_p2 if has_played else None

                if has_played:
                    c_s = tot_p1 if is_p1 else tot_p2
                    o_s = tot_p2 if is_p1 else tot_p1
                    if c_s > o_s: outcome = "W"
                    elif c_s < o_s: outcome = "L"
                    else: outcome = "D"
                else:
                    outcome = "PENDING"

                games_str = ", ".join(games_scores)
                if games_str and club_scorers:
                    subline = f"Матчи: {games_str} • Голы: {', '.join(club_scorers)}"
                elif games_str:
                    subline = f"Матчи: {games_str}"
                elif club_scorers:
                    subline = f"Голы клуба: {', '.join(club_scorers)}"
                else:
                    subline = ""

            league_items.append({
                "match_id": first_m["id"],
                "round_number": rn,
                "stage_order": 0,
                "tour_title": tour_title,
                "is_cup": False,
                "home_team": home_team,
                "away_team": away_team,
                "home_score": h_score,
                "away_score": a_score,
                "club_score": h_score if is_p1 else a_score,
                "opponent_score": a_score if is_p1 else h_score,
                "is_home": is_p1,
                "opponent_team": opp_team,
                "opponent_username": opp_user,
                "status": "confirmed" if (has_played and all_confirmed) else "pending",
                "outcome": outcome,
                "scorers": club_scorers,
                "subline": subline,
                "is_completed": all_confirmed and has_played,
                "has_played": has_played,
            })

        # 3. Combine and Sort:
        played_cup = [c for c in cup_items if c["has_played"] or c["is_completed"]]
        played_cup.sort(key=lambda x: -x["stage_order"]) # Final first, then 1/2, 1/4, 1/8

        played_league = [l for l in league_items if l["is_completed"]]
        played_league.sort(key=lambda x: -x["round_number"]) # Recent rounds first

        pending_cup = [c for c in cup_items if not (c["has_played"] or c["is_completed"])]
        pending_cup.sort(key=lambda x: x["stage_order"])

        pending_league = [l for l in league_items if not l["is_completed"]]
        pending_league.sort(key=lambda x: x["round_number"])

        all_items = played_cup + played_league + pending_cup + pending_league

        # Calculate counts
        played_count = len(played_cup) + len(played_league)
        pending_count = len(pending_cup) + len(pending_league)

        return {
            "team_name": canon,
            "played_count": played_count,
            "pending_count": pending_count,
            "matches": all_items[:limit],
        }


def get_all_clubs_summary() -> list[dict]:
    """
    Get summary list of all KPL clubs for the clubs catalog.
    """
    from config import KPL_TEAMS
    standings = get_standings()
    form_map = get_teams_recent_form(limit=5)
    
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id, username, team_name, warn_count FROM users WHERE team_name IS NOT NULL AND team_name != ''")
        users = {resolve_team_name(u["team_name"]).lower(): dict(u) for u in cursor.fetchall() if resolve_team_name(u["team_name"])}

    standings_map = {resolve_team_name(s["team_name"]).lower(): (rank, s) for rank, s in enumerate(standings, 1) if resolve_team_name(s["team_name"])}

    result = []
    for t in KPL_TEAMS:
        canon = resolve_team_name(t) or t
        canon_lower = canon.lower()
        
        rank, s_row = standings_map.get(canon_lower, (0, {"played": 0, "points": 0, "wins": 0, "draws": 0, "losses": 0}))
        u_info = users.get(canon_lower)
        form = form_map.get(canon_lower, [])

        result.append({
            "team_name": canon,
            "rank": rank,
            "manager_username": u_info.get("username") if u_info else None,
            "manager_id": u_info.get("telegram_id") if u_info else None,
            "warn_count": u_info.get("warn_count", 0) if u_info else 0,
            "played": s_row["played"],
            "points": s_row["points"],
            "wins": s_row["wins"],
            "draws": s_row["draws"],
            "losses": s_row["losses"],
            "recent_form": form,
        })

    return sorted(result, key=lambda x: (x["rank"] if x["rank"] > 0 else 999, -x["points"], x["team_name"]))


def rename_player(old_name: str, new_name: str, team_name: str | None = None) -> tuple[int, int]:
    """
    Rename a player across squad_players and match_events.
    Returns (squad_updated_count, events_updated_count).
    """
    old_clean = old_name.strip()
    new_clean = new_name.strip()
    with transaction() as conn:
        cursor = conn.cursor()
        if team_name:
            t_clean = team_name.strip()
            cursor.execute(
                "UPDATE squad_players SET player_name = ? WHERE LOWER(player_name) = LOWER(?) AND LOWER(team_name) = LOWER(?)",
                (new_clean, old_clean, t_clean)
            )
            c1 = cursor.rowcount
            cursor.execute(
                "UPDATE match_events SET player_name = ? WHERE LOWER(player_name) = LOWER(?) AND LOWER(team_name) = LOWER(?)",
                (new_clean, old_clean, t_clean)
            )
            c2 = cursor.rowcount
        else:
            cursor.execute(
                "UPDATE squad_players SET player_name = ? WHERE LOWER(player_name) = LOWER(?)",
                (new_clean, old_clean)
            )
            c1 = cursor.rowcount
            cursor.execute(
                "UPDATE match_events SET player_name = ? WHERE LOWER(player_name) = LOWER(?)",
                (new_clean, old_clean)
            )
            c2 = cursor.rowcount
        return (c1, c2)


# --- KPL Cup Management ---

KPL_CUP_1_8_PAIRS = [
    ("Расинг", "Спортинг"),
    ("Копенгаген", "Рейнджерс"),
    ("Порту", "Селтик"),
    ("Аякс", "Будё Глимпт"),
    ("Бенфика", "Брюгге"),
    ("Фейеноорд", "ПСВ"),
    ("АЕК", "Ривер Плейт"),
    ("Бока Хуниорс", "Брага"),
]

def init_kpl_cup_all_stages() -> int:
    """
    Initialize all stages of KPL Cup (1/8, 1/4, 1/2, final).
    1/8 gets real teams. Subsequent stages get placeholders which are updated later.
    Returns count of created series.
    """
    created_count = 0
    with transaction() as conn:
        cursor = conn.cursor()
        
        # Check if 1/8 series already exists
        cursor.execute("SELECT COUNT(*) FROM cup_series WHERE stage = '1/8'")
        if cursor.fetchone()[0] == 0:
            # 1. Initialize 1/8 Final
            for i, (t1, t2) in enumerate(KPL_CUP_1_8_PAIRS, 1):
                cursor.execute(
                    "INSERT INTO cup_series (stage, series_num, team1_name, team2_name, team1_wins, team2_wins, status) VALUES ('1/8', ?, ?, ?, 0, 0, 'active')",
                    (i, t1, t2)
                )
                series_id = cursor.lastrowid
                created_count += 1
                
                cursor.execute("SELECT telegram_id FROM users WHERE LOWER(team_name) = LOWER(?)", (t1.strip(),))
                r1 = cursor.fetchone()
                p1_id = r1[0] if r1 else None
                
                cursor.execute("SELECT telegram_id FROM users WHERE LOWER(team_name) = LOWER(?)", (t2.strip(),))
                r2 = cursor.fetchone()
                p2_id = r2[0] if r2 else None
                
                cursor.execute("""
                    INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, status, tournament_type, cup_stage, cup_series_id, game_num_in_series)
                    VALUES (-1, ?, ?, ?, ?, 'pending', 'cup', '1/8', ?, 1)
                """, (p1_id, p2_id, t1, t2, series_id))
            
        # 2. Initialize 1/4 Final (4 series) if missing
        cursor.execute("SELECT COUNT(*) FROM cup_series WHERE stage = '1/4'")
        if cursor.fetchone()[0] == 0:
            for i in range(1, 5):
                t1 = f"Победитель 1/8 (С{(i-1)*2+1})"
                t2 = f"Победитель 1/8 (С{(i-1)*2+2})"
                cursor.execute("INSERT INTO cup_series (stage, series_num, team1_name, team2_name, team1_wins, team2_wins, status) VALUES ('1/4', ?, ?, ?, 0, 0, 'active')", (i, t1, t2))
                s_id = cursor.lastrowid
                created_count += 1
                cursor.execute("INSERT INTO matches (round_number, player1_team, player2_team, status, tournament_type, cup_stage, cup_series_id, game_num_in_series) VALUES (-1, ?, ?, 'pending', 'cup', '1/4', ?, 1)", (t1, t2, s_id))
            
        # 3. Initialize 1/2 Final (2 series) if missing
        cursor.execute("SELECT COUNT(*) FROM cup_series WHERE stage = '1/2'")
        if cursor.fetchone()[0] == 0:
            for i in range(1, 3):
                t1 = f"Победитель 1/4 (С{(i-1)*2+1})"
                t2 = f"Победитель 1/4 (С{(i-1)*2+2})"
                cursor.execute("INSERT INTO cup_series (stage, series_num, team1_name, team2_name, team1_wins, team2_wins, status) VALUES ('1/2', ?, ?, ?, 0, 0, 'active')", (i, t1, t2))
                s_id = cursor.lastrowid
                created_count += 1
                cursor.execute("INSERT INTO matches (round_number, player1_team, player2_team, status, tournament_type, cup_stage, cup_series_id, game_num_in_series) VALUES (-1, ?, ?, 'pending', 'cup', '1/2', ?, 1)", (t1, t2, s_id))
            
        # 4. Initialize Final (1 series) if missing
        cursor.execute("SELECT COUNT(*) FROM cup_series WHERE stage = 'final'")
        if cursor.fetchone()[0] == 0:
            t1 = "Победитель 1/2 (С1)"
            t2 = "Победитель 1/2 (С2)"
            cursor.execute("INSERT INTO cup_series (stage, series_num, team1_name, team2_name, team1_wins, team2_wins, status) VALUES ('final', 1, ?, ?, 0, 0, 'active')", (t1, t2))
            s_id = cursor.lastrowid
            created_count += 1
            cursor.execute("INSERT INTO matches (round_number, player1_team, player2_team, status, tournament_type, cup_stage, cup_series_id, game_num_in_series) VALUES (-1, ?, ?, 'pending', 'cup', 'final', ?, 1)", (t1, t2, s_id))
            
    return created_count

def sync_cup_bracket() -> int:
    """Synchronize all completed series winners to their next stage slots."""
    sync_count = 0
    with transaction() as conn:
        cursor = conn.cursor()
        
        # Get all completed series
        cursor.execute("SELECT id, stage, series_num, winner_name FROM cup_series WHERE status = 'completed' AND winner_name IS NOT NULL")
        completed = cursor.fetchall()
        
        for s_id, stage, series_num, winner_name in completed:
            if stage == 'final': continue
            
            stages = ['1/8', '1/4', '1/2', 'final']
            next_stage = stages[stages.index(stage) + 1]
            
            next_series_num = (series_num + 1) // 2
            is_team1 = (series_num % 2 != 0)
            
            # Try to resolve telegram ID if possible
            cursor.execute("SELECT telegram_id FROM users WHERE LOWER(team_name) = LOWER(?)", (winner_name.strip(),))
            r = cursor.fetchone()
            p_id = r[0] if r else None
            
            if is_team1:
                cursor.execute("UPDATE cup_series SET team1_name = ? WHERE stage = ? AND series_num = ?", (winner_name, next_stage, next_series_num))
                cursor.execute("UPDATE matches SET player1_team = ?, player1_id = COALESCE(?, player1_id) WHERE cup_stage = ? AND cup_series_id = (SELECT id FROM cup_series WHERE stage = ? AND series_num = ?) AND game_num_in_series = 1", (winner_name, p_id, next_stage, next_stage, next_series_num))
            else:
                cursor.execute("UPDATE cup_series SET team2_name = ? WHERE stage = ? AND series_num = ?", (winner_name, next_stage, next_series_num))
                cursor.execute("UPDATE matches SET player2_team = ?, player2_id = COALESCE(?, player2_id) WHERE cup_stage = ? AND cup_series_id = (SELECT id FROM cup_series WHERE stage = ? AND series_num = ?) AND game_num_in_series = 1", (winner_name, p_id, next_stage, next_stage, next_series_num))
            
            sync_count += 1
            
    return sync_count

def get_cup_series_list(stage: str = '1/8') -> list[dict]:
    """Retrieve all series for a given cup stage with match details."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, stage, series_num, team1_name, team2_name, team1_wins, team2_wins, winner_name, status
            FROM cup_series
            WHERE stage = ?
            ORDER BY series_num ASC
        """, (stage,))
        series_rows = [dict(r) for r in cursor.fetchall()]
        
        for s in series_rows:
            # Fetch matches for this series
            cursor.execute("""
                SELECT id, game_num_in_series, player1_team, player2_team, player1_score, player2_score, status, photo_id
                FROM matches
                WHERE cup_series_id = ?
                ORDER BY game_num_in_series ASC
            """, (s["id"],))
            s["matches"] = [dict(r) for r in cursor.fetchall()]
            
        return series_rows

def get_cup_series(series_id: int) -> dict | None:
    """Retrieve single cup series by id."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cup_series WHERE id = ?", (series_id,))
        r = cursor.fetchone()
        return dict(r) if r else None

def get_cup_match_by_series_and_game(series_id: int, game_num: int) -> dict | None:
    """Retrieve match by cup series ID and game number."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM matches WHERE cup_series_id = ? AND game_num_in_series = ?", (series_id, game_num))
        r = cursor.fetchone()
        if r:
            return get_match(r[0])
        return None

def ensure_cup_match_exists(cup_series_id: int, game_num: int) -> int | None:
    """Ensure a match row exists for the given cup series and game number, and return its match_id."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM matches WHERE cup_series_id = ? AND game_num_in_series = ?", (cup_series_id, game_num))
        r = cursor.fetchone()
        if r:
            return r[0]
        
        cursor.execute("SELECT * FROM cup_series WHERE id = ?", (cup_series_id,))
        series = cursor.fetchone()
        if not series:
            return None
        
        stage = series["stage"]
        t1_name = series["team1_name"]
        t2_name = series["team2_name"]
        
        if game_num % 2 == 0:
            hp_team, ap_team = t2_name, t1_name
        else:
            hp_team, ap_team = t1_name, t2_name
            
        cursor.execute("SELECT telegram_id FROM users WHERE LOWER(team_name) = LOWER(?)", (hp_team.strip(),))
        r1 = cursor.fetchone()
        hp_id = r1[0] if r1 else None
        
        cursor.execute("SELECT telegram_id FROM users WHERE LOWER(team_name) = LOWER(?)", (ap_team.strip(),))
        r2 = cursor.fetchone()
        ap_id = r2[0] if r2 else None
        
        cursor.execute("""
            INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, status, tournament_type, cup_stage, cup_series_id, game_num_in_series)
            VALUES (-1, ?, ?, ?, ?, 'pending', 'cup', ?, ?, ?)
        """, (hp_id, ap_id, hp_team, ap_team, stage, cup_series_id, game_num))
        return cursor.lastrowid

def get_cup_top_scorers(limit: int = 20) -> list[dict]:
    """Get top goalscorers in the KPL Cup aggregated from match_events."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT me.player_name, me.team_name, SUM(me.count) AS total_goals
            FROM match_events me
            JOIN matches m ON me.match_id = m.id
            WHERE me.event_type = 'goal'
              AND (m.tournament_type = 'cup' OR m.round_number = -1 OR m.cup_series_id IS NOT NULL OR m.cup_stage IS NOT NULL)
              AND m.status = 'confirmed'
            GROUP BY me.player_name, me.team_name
            ORDER BY total_goals DESC, me.player_name ASC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

def get_cup_top_assists(limit: int = 20) -> list[dict]:
    """Get top assist providers in the KPL Cup aggregated from match_events."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT me.player_name, me.team_name, SUM(me.count) AS total_assists
            FROM match_events me
            JOIN matches m ON me.match_id = m.id
            WHERE me.event_type = 'assist'
              AND (m.tournament_type = 'cup' OR m.round_number = -1 OR m.cup_series_id IS NOT NULL OR m.cup_stage IS NOT NULL)
              AND m.status = 'confirmed'
            GROUP BY me.player_name, me.team_name
            ORDER BY total_assists DESC, me.player_name ASC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

def get_full_cup_summary_for_ai() -> str:
    """Generate a comprehensive summary of the KPL Cup: rules, bracket state, matches and top stats for AI context."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, stage, series_num, team1_name, team2_name, team1_wins, team2_wins, winner_name, status 
            FROM cup_series 
            ORDER BY CASE stage WHEN '1/8' THEN 1 WHEN '1/4' THEN 2 WHEN '1/2' THEN 3 WHEN 'final' THEN 4 ELSE 5 END, series_num ASC
        """)
        series_all = [dict(r) for r in cursor.fetchall()]
        
        stages_map = {"1/8": "1/8 ФИНАЛА", "1/4": "1/4 ФИНАЛА", "1/2": "ПОЛУФИНАЛ (1/2)", "final": "ФИНАЛ"}
        
        bracket_lines = []
        if series_all:
            grouped = {}
            for s in series_all:
                st = s["stage"]
                grouped.setdefault(st, []).append(s)
                
            for st, s_list in grouped.items():
                st_title = stages_map.get(st, st)
                bracket_lines.append(f"\n--- {st_title} ---")
                for s in s_list:
                    t1 = s["team1_name"] or "TBD"
                    t2 = s["team2_name"] or "TBD"
                    w1 = s.get("team1_wins", 0)
                    w2 = s.get("team2_wins", 0)
                    status = s.get("status", "pending")
                    winner = s.get("winner_name")
                    
                    cursor.execute("""
                        SELECT game_num_in_series, player1_score, player2_score, status, player1_team, player2_team 
                        FROM matches 
                        WHERE cup_series_id = ? 
                        ORDER BY game_num_in_series ASC
                    """, (s["id"],))
                    matches = cursor.fetchall()
                    match_details = []
                    for m in matches:
                        if m["status"] == "confirmed":
                            match_details.append(f"Игра {m['game_num_in_series']}: {m['player1_team']} {m['player1_score']}:{m['player2_score']} {m['player2_team']}")
                        elif m["status"] in ("pending", "reported"):
                            match_details.append(f"Игра {m['game_num_in_series']} (ожидает)")
                            
                    details_str = f" [{', '.join(match_details)}]" if match_details else ""
                    
                    if winner:
                        bracket_lines.append(f"• Серия #{s['series_num']}: {t1} vs {t2} — Счёт серии: {w1}:{w2} (🏆 Победитель: {winner}){details_str}")
                    elif status == "active":
                        bracket_lines.append(f"• Серия #{s['series_num']}: {t1} vs {t2} — Счёт серии: {w1}:{w2} (🔥 Идёт серия){details_str}")
                    else:
                        bracket_lines.append(f"• Серия #{s['series_num']}: {t1} vs {t2} — Ожидает{details_str}")
        else:
            bracket_lines.append("Сетка кубка ещё не сформирована.")

        # Cup Scorers
        scorers = get_cup_top_scorers(limit=10)
        scorers_lines = []
        if scorers:
            for i, sc in enumerate(scorers, 1):
                scorers_lines.append(f"{i}. {sc['player_name']} ({sc['team_name']}) — {sc['total_goals']} голов")
        else:
            scorers_lines.append("Пока нет голов в кубке.")

        # Cup Assists
        assists = get_cup_top_assists(limit=10)
        assists_lines = []
        if assists:
            for i, asst in enumerate(assists, 1):
                assists_lines.append(f"{i}. {asst['player_name']} ({asst['team_name']}) — {asst['total_assists']} ассистов")
        else:
            assists_lines.append("Пока нет ассистов в кубке.")

        cup_text = (
            "🏆 ПОЛНАЯ ИНФОРМАЦИЯ О КУБКЕ КПЛ (РЕГЛАМЕНТ, СЕТКА И СТАТИСТИКА):\n\n"
            "📋 РЕГЛАМЕНТ И ПРАВИЛА КУБКА:\n"
            "• Формат турнира: Олимпийская система плей-офф (1/8 финала ➔ 1/4 финала ➔ 1/2 финала ➔ Финал).\n"
            "• Формат противостояний: Best-of-3 (серия до 2 побед одного из участников).\n"
            "  - Игра 1: Дома играет первая команда.\n"
            "  - Игра 2: Дома играет вторая команда.\n"
            "  - Игра 3 (при счёте 1:1 в серии): Решающий матч за выход дальше.\n"
            "• Правила матча: Ничьих в кубке не бывает — при равном счете в игре проводится дополнительное время (овертайм) и серия пенальти.\n"
            "• Награды и бонусы: За каждую победу в матче кубка клуб получает +1 тренировку. Победитель Кубка КПЛ получает кубковый трофей и гарантированную путевку в Еврокубки (Суперкубок / ЛЕ / ЛК).\n\n"
            "📜 ИСТОРИЯ ПОБЕДИТЕЛЕЙ КУБКА КПЛ:\n"
            "• Сезон 1: 🏆 АЕК (@Snikers2121) — обладатель Кубка КПЛ.\n"
            "• Сезон 2: 🏆 Бенфика (@vtrrgyg) — обладатель Кубка КПЛ (в финале обыграла Расинг 3:1 по сумме).\n"
            "• Прошлые триумфаторы: 🏆 Бока Хуниорс (2x обладатель Кубка — 2 и 3 сезоны), 🏆 Порту (3 сезон).\n\n"
            "⚔️ АКТУАЛЬНАЯ СЕТКА И РЕЗУЛЬТАТЫ СЕРИЙ КУБКА КПЛ:\n"
            + "\n".join(bracket_lines) + "\n\n"
            "⚽ БОМБАРДИРЫ КУБКА КПЛ:\n"
            + "\n".join(scorers_lines) + "\n\n"
            "🎯 АССИСТЕНТЫ КУБКА КПЛ:\n"
            + "\n".join(assists_lines)
        )
        return cup_text


def process_cup_match_completion(match_id: int) -> str | None:
    """
    Called whenever a Cup match status changes to 'confirmed'.
    Updates wins count in cup_series, generates Game 2 or 3 if needed, or completes the series
    and automatically forwards the winner to the next stage in the pre-generated bracket.
    Returns next_stage (e.g. '1/4', '1/2', 'final') if advanced.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, cup_series_id, cup_stage, game_num_in_series, player1_team, player2_team, player1_score, player2_score, status FROM matches WHERE id = ?", (match_id,))
        m = cursor.fetchone()
        if not m or m["status"] != "confirmed" or not m["cup_series_id"]:
            return None
            
        s_id = m["cup_series_id"]
        p1_team = m["player1_team"]
        p2_team = m["player2_team"]
        s1 = m["player1_score"] or 0
        s2 = m["player2_score"] or 0
        
        if s1 == s2:
            return None  # Cup matches should have a winner
            
        cursor.execute("SELECT * FROM cup_series WHERE id = ?", (s_id,))
        series = cursor.fetchone()
        if not series:
            return None
            
        t1_name = series["team1_name"]
        t2_name = series["team2_name"]
        
        # Calculate current wins in this series
        cursor.execute("SELECT player1_team, player2_team, player1_score, player2_score FROM matches WHERE cup_series_id = ? AND status = 'confirmed'", (s_id,))
        confirmed_matches = cursor.fetchall()
        
        t1_wins = 0
        t2_wins = 0
        for cm in confirmed_matches:
            c1, c2 = cm["player1_score"] or 0, cm["player2_score"] or 0
            if c1 > c2:
                winner = cm["player1_team"]
            elif c2 > c1:
                winner = cm["player2_team"]
            else:
                continue
            if winner and winner.lower() == t1_name.lower():
                t1_wins += 1
            elif winner and winner.lower() == t2_name.lower():
                t2_wins += 1
                
        cursor.execute("UPDATE cup_series SET team1_wins = ?, team2_wins = ? WHERE id = ?", (t1_wins, t2_wins, s_id))
        
        stage = series["stage"]
        wins_required = 3 if stage == 'final' else 2
        max_games = 5 if stage == 'final' else 3

        if t1_wins >= wins_required or t2_wins >= wins_required:
            series_winner = t1_name if t1_wins >= wins_required else t2_name
            cursor.execute("UPDATE cup_series SET winner_name = ?, status = 'completed' WHERE id = ?", (series_winner, s_id))
            
            # Forward winner to the next stage in the pre-generated bracket
            NEXT_STAGE_MAP = {'1/8': '1/4', '1/4': '1/2', '1/2': 'final'}
            next_stage = NEXT_STAGE_MAP.get(stage)
            
            if next_stage:
                next_series_num = (series["series_num"] - 1) // 2 + 1
                is_team1 = (series["series_num"] % 2 != 0)
                
                # Update next series
                if is_team1:
                    cursor.execute("UPDATE cup_series SET team1_name = ? WHERE stage = ? AND series_num = ?", (series_winner, next_stage, next_series_num))
                    cursor.execute("UPDATE matches SET player1_team = ? WHERE cup_stage = ? AND cup_series_id = (SELECT id FROM cup_series WHERE stage = ? AND series_num = ?) AND game_num_in_series = 1", (series_winner, next_stage, next_stage, next_series_num))
                else:
                    cursor.execute("UPDATE cup_series SET team2_name = ? WHERE stage = ? AND series_num = ?", (series_winner, next_stage, next_series_num))
                    cursor.execute("UPDATE matches SET player2_team = ? WHERE cup_stage = ? AND cup_series_id = (SELECT id FROM cup_series WHERE stage = ? AND series_num = ?) AND game_num_in_series = 1", (series_winner, next_stage, next_stage, next_series_num))
                
                # Fetch Telegram ID to update match
                cursor.execute("SELECT telegram_id FROM users WHERE LOWER(team_name) = LOWER(?)", (series_winner.strip(),))
                r = cursor.fetchone()
                if r:
                    if is_team1:
                        cursor.execute("UPDATE matches SET player1_id = ? WHERE cup_stage = ? AND cup_series_id = (SELECT id FROM cup_series WHERE stage = ? AND series_num = ?) AND game_num_in_series = 1", (r[0], next_stage, next_stage, next_series_num))
                    else:
                        cursor.execute("UPDATE matches SET player2_id = ? WHERE cup_stage = ? AND cup_series_id = (SELECT id FROM cup_series WHERE stage = ? AND series_num = ?) AND game_num_in_series = 1", (r[0], next_stage, next_stage, next_series_num))
                
                return next_stage
            
            return None
        else:
            # Need next game (Game 2, 3, 4 or 5)
            current_game_num = len(confirmed_matches)
            next_game_num = current_game_num + 1
            if next_game_num <= max_games:
                cursor.execute("SELECT COUNT(*) FROM matches WHERE cup_series_id = ? AND game_num_in_series = ?", (s_id, next_game_num))
                if cursor.fetchone()[0] == 0:
                    if next_game_num % 2 == 0:
                        hp_team, ap_team = t2_name, t1_name
                    else:
                        hp_team, ap_team = t1_name, t2_name
                        
                    cursor.execute("SELECT telegram_id FROM users WHERE LOWER(team_name) = LOWER(?)", (hp_team.strip(),))
                    r1 = cursor.fetchone()
                    hp_id = r1[0] if r1 else None
                    
                    cursor.execute("SELECT telegram_id FROM users WHERE LOWER(team_name) = LOWER(?)", (ap_team.strip(),))
                    r2 = cursor.fetchone()
                    ap_id = r2[0] if r2 else None
                    
                    cursor.execute("""
                        INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, status, tournament_type, cup_stage, cup_series_id, game_num_in_series)
                        VALUES (-1, ?, ?, ?, ?, 'pending', 'cup', ?, ?, ?)
                    """, (hp_id, ap_id, hp_team, ap_team, stage, s_id, next_game_num))
            return None
def parse_flexible_datetime(dt_str: str | None) -> datetime.datetime | None:
    """Parse date/datetime string supporting multiple formats commonly used in the league."""
    if not dt_str or not str(dt_str).strip():
        return None
    s = str(dt_str).strip()
    formats = [
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue

    # Try format without year (e.g. "20.08 12:00" or "20.08") -> use current year
    try:
        now_year = datetime.datetime.now().year
        return datetime.datetime.strptime(f"{s}.{now_year}", "%d.%m %H:%M.%Y")
    except ValueError:
        pass
    try:
        now_year = datetime.datetime.now().year
        return datetime.datetime.strptime(f"{s}.{now_year}", "%d.%m.%Y")
    except ValueError:
        pass
    return None


def get_all_unplayed_league_matches(division_id: int | None = None, season_id: int | None = None) -> list[dict]:
    """Retrieve pending league matches that are overdue: expired deadlines or past rounds, optionally filtered by division and season."""
    with transaction() as conn:
        cursor = conn.cursor()
        now = datetime.datetime.now()
        start_dt = get_debt_tracking_start_datetime()

        target_season_id = season_id
        if target_season_id is None:
            act = get_active_season()
            target_season_id = act["id"] if act else 1

        if division_id is not None:
            cursor.execute(
                "SELECT round_number, is_open, deadline, division_id, season_id FROM rounds WHERE (season_id = ? OR season_id IS NULL) AND division_id = ?",
                (target_season_id, division_id)
            )
        else:
            cursor.execute(
                "SELECT round_number, is_open, deadline, division_id, season_id FROM rounds WHERE (season_id = ? OR season_id IS NULL)",
                (target_season_id,)
            )
        rounds_rows = cursor.fetchall()

        round_info_map: dict[int, dict] = {}
        max_open_round = 0
        for row in rounds_rows:
            r_num = row["round_number"]
            is_open = row["is_open"]
            dl_str = row["deadline"]
            parsed_dl = parse_flexible_datetime(dl_str)
            if is_open and r_num > max_open_round:
                max_open_round = r_num
            round_info_map[r_num] = {
                "is_open": bool(is_open),
                "deadline_dt": parsed_dl
            }

        if division_id is not None:
            cursor.execute("""
                SELECT 
                    m.id, m.round_number, m.player1_team, m.player2_team, m.division_id, m.season_id
                FROM matches m
                WHERE (m.tournament_type IS NULL OR m.tournament_type = 'league')
                  AND m.status = 'pending'
                  AND m.division_id = ?
                  AND (m.season_id = ? OR m.season_id IS NULL)
                ORDER BY m.round_number ASC, m.id ASC
            """, (division_id, target_season_id))
        else:
            cursor.execute("""
                SELECT 
                    m.id, m.round_number, m.player1_team, m.player2_team, m.division_id, m.season_id
                FROM matches m
                WHERE (m.tournament_type IS NULL OR m.tournament_type = 'league')
                  AND m.status = 'pending'
                  AND (m.season_id = ? OR m.season_id IS NULL)
                ORDER BY m.round_number ASC, m.id ASC
            """, (target_season_id,))
        matches = [dict(row) for row in cursor.fetchall()]

        if division_id is not None:
            cursor.execute("SELECT telegram_id, username, team_name, division_id FROM users WHERE team_name IS NOT NULL AND (division_id = ? OR division_id IS NULL)", (division_id,))
        else:
            cursor.execute("SELECT telegram_id, username, team_name, division_id FROM users WHERE team_name IS NOT NULL")
        user_rows = [dict(r) for r in cursor.fetchall()]

        def get_team_owner(t_name: str | None) -> dict | None:
            if not t_name:
                return None
            t_clean = t_name.strip().lower()
            for u in user_rows:
                ut = (u.get("team_name") or "").strip().lower()
                if ut == t_clean:
                    return u
            for u in user_rows:
                ut = (u.get("team_name") or "").strip()
                if teams_match(ut, t_name):
                    return u
            return None

        unplayed = []
        for m in matches:
            rn = m["round_number"]
            r_info = round_info_map.get(rn)
            if not r_info:
                continue

            dl_dt = r_info.get("deadline_dt")
            is_open = r_info.get("is_open", False)

            # Include ONLY if the match is actually overdue:
            # 1. Deadline is set and has passed
            # 2. Round is open without deadline and debt tracking started
            # 3. Round is a past round (before max open round, or closed) and tracking started
            # Future unopened rounds and open rounds with future deadlines are ignored
            is_debt = False
            if dl_dt and dl_dt <= now:
                is_debt = True
            elif is_open and dl_dt is None:
                if start_dt and now >= start_dt:
                    is_debt = True
            elif r_info and max_open_round > 0 and rn <= max_open_round:
                # Skip any round (open or closed) whose deadline is still in the future
                if not (dl_dt and dl_dt > now):
                    if start_dt and now >= start_dt:
                        is_debt = True

            if not is_debt:
                continue

            t1 = m.get("player1_team")
            t2 = m.get("player2_team")
            u1 = get_team_owner(t1)
            u2 = get_team_owner(t2)

            m["player1_id"] = u1.get("telegram_id") if u1 else None
            m["p1_username"] = u1.get("username") if u1 else None
            m["p1_team"] = t1

            m["player2_id"] = u2.get("telegram_id") if u2 else None
            m["p2_username"] = u2.get("username") if u2 else None
            m["p2_team"] = t2

            unplayed.append(m)

        return unplayed


def get_all_unplayed_cup_matches() -> list[dict]:
    """Retrieve all pending matches across active cup series."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                m.id, m.cup_stage, m.cup_series_id, m.game_num_in_series,
                u1.telegram_id AS player1_id, u2.telegram_id AS player2_id, m.player1_team, m.player2_team,
                s.team1_name, s.team2_name, s.team1_wins, s.team2_wins,
                u1.username AS p1_username, u2.username AS p2_username,
                u1.team_name AS p1_team, u2.team_name AS p2_team
            FROM matches m
            JOIN cup_series s ON m.cup_series_id = s.id
            LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
            LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
            WHERE m.tournament_type = 'cup' AND m.status = 'pending' AND s.status = 'active'
              AND (m.player1_team NOT LIKE 'Победитель%' AND m.player2_team NOT LIKE 'Победитель%')
            ORDER BY s.series_num ASC, m.game_num_in_series ASC
        """)
        return [dict(row) for row in cursor.fetchall()]


def record_debt_stage(match_id: int, stage: str) -> None:
    """Record a debt lifecycle stage for a match (e.g. 'deadline_passed', 'warn_24h', 'warn_48h', etc.)."""
    with transaction() as conn:
        cursor = conn.cursor()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT OR REPLACE INTO debt_reminders (match_id, stage, sent_at) VALUES (?, ?, ?)",
            (match_id, stage, now_str)
        )


def has_debt_stage(match_id: int, stage: str) -> bool:
    """Check whether a debt lifecycle stage has already been recorded for a match."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM debt_reminders WHERE match_id = ? AND stage = ?", (match_id, stage))
        return cursor.fetchone() is not None


def record_debt_12h_reminder(match_id: int) -> None:
    """Record timestamp of 12h cycle debt reminder."""
    with transaction() as conn:
        cursor = conn.cursor()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT INTO debt_reminders (match_id, stage, sent_at)
            VALUES (?, 'cycle_reminder_last', ?)
            ON CONFLICT(match_id, stage) DO UPDATE SET sent_at = ?
        """, (match_id, now_str, now_str))


def get_last_debt_12h_reminder(match_id: int) -> datetime.datetime | None:
    """Get datetime when last cycle reminder was sent for a match."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT sent_at FROM debt_reminders WHERE match_id = ? AND stage = 'cycle_reminder_last'", (match_id,))
        row = cursor.fetchone()
        if not row or not row["sent_at"]:
            return None
        return parse_flexible_datetime(row["sent_at"])


def get_detailed_overdue_matches(division_id: int | None = None, season_id: int | None = None) -> list[dict]:
    """
    Retrieve all pending league matches that are legitimately overdue:
    - Round has an expired deadline (deadline_dt <= now).
    - Or round is currently open (is_open = 1) without a deadline, and start_dt <= now.
    - Or round is a past round (rn < max_open_round or is_open = 0 with unplayed matches).
    - Club participants are strictly resolved from current owners in users table.
    - Strictly filtered by division_id and season_id.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        now = datetime.datetime.now()
        start_dt = get_debt_tracking_start_datetime()

        target_season_id = season_id
        if target_season_id is None:
            act = get_active_season()
            target_season_id = act["id"] if act else 1

        # 1. Fetch rounds status
        if division_id is not None:
            cursor.execute(
                "SELECT round_number, is_open, deadline, division_id, season_id FROM rounds WHERE (season_id = ? OR season_id IS NULL) AND division_id = ?",
                (target_season_id, division_id)
            )
        else:
            cursor.execute(
                "SELECT round_number, is_open, deadline, division_id, season_id FROM rounds WHERE (season_id = ? OR season_id IS NULL)",
                (target_season_id,)
            )
        rounds_rows = cursor.fetchall()

        round_info_map: dict[int, dict] = {}
        max_open_round = 0
        for row in rounds_rows:
            r_num = row["round_number"]
            is_open = row["is_open"]
            dl_str = row["deadline"]
            parsed_dl = parse_flexible_datetime(dl_str)
            if is_open and r_num > max_open_round:
                max_open_round = r_num
            round_info_map[r_num] = {
                "is_open": bool(is_open),
                "deadline_str": dl_str,
                "deadline_dt": parsed_dl
            }

        # 2. Fetch pending league matches
        if division_id is not None:
            cursor.execute("""
                SELECT 
                    m.id, m.round_number, COALESCE(m.is_extended, 0) AS is_extended,
                    COALESCE(m.frozen_seconds, 0) AS frozen_seconds,
                    m.frozen_at,
                    m.player1_team, m.player2_team, m.division_id, m.season_id
                FROM matches m
                WHERE (m.tournament_type IS NULL OR m.tournament_type = 'league')
                  AND m.status = 'pending'
                  AND (m.division_id = ? OR m.division_id IS NULL)
                  AND (m.season_id = ? OR m.season_id IS NULL)
                ORDER BY m.round_number ASC, m.id ASC
            """, (division_id, target_season_id))
        else:
            cursor.execute("""
                SELECT 
                    m.id, m.round_number, COALESCE(m.is_extended, 0) AS is_extended,
                    COALESCE(m.frozen_seconds, 0) AS frozen_seconds,
                    m.frozen_at,
                    m.player1_team, m.player2_team, m.division_id, m.season_id
                FROM matches m
                WHERE (m.tournament_type IS NULL OR m.tournament_type = 'league')
                  AND m.status = 'pending'
                  AND (m.season_id = ? OR m.season_id IS NULL)
                ORDER BY m.round_number ASC, m.id ASC
            """, (target_season_id,))
        matches = [dict(row) for row in cursor.fetchall()]

        # 3. Load all active users mapped by team
        if division_id is not None:
            cursor.execute("SELECT telegram_id, username, team_name, warn_count, division_id FROM users WHERE team_name IS NOT NULL AND (division_id = ? OR division_id IS NULL)", (division_id,))
        else:
            cursor.execute("SELECT telegram_id, username, team_name, warn_count, division_id FROM users WHERE team_name IS NOT NULL")
        user_rows = [dict(r) for r in cursor.fetchall()]

        def get_team_owner(t_name: str | None) -> dict | None:
            if not t_name:
                return None
            t_clean = t_name.strip().lower()
            for u in user_rows:
                ut = (u.get("team_name") or "").strip().lower()
                if ut == t_clean:
                    return u
            for u in user_rows:
                ut = (u.get("team_name") or "").strip()
                if teams_match(ut, t_name):
                    return u
            return None

        overdue_list = []
        for m in matches:
            rn = m["round_number"]
            r_info = round_info_map.get(rn)

            dl_dt = r_info.get("deadline_dt") if r_info else None
            is_open = r_info.get("is_open", False) if r_info else False

            # CRITICAL: A pending league match is overdue if:
            # 1. dl_dt is set and dl_dt <= now
            # 2. Or is_open == True and dl_dt is None and start_dt and now >= start_dt
            # 3. Or rn < max_open_round (past tour before current open tours)
            # 4. Or is_open == False and rn <= max_open_round (closed tour with pending matches)
            is_overdue = False
            if dl_dt and dl_dt <= now:
                is_overdue = True
            elif is_open and dl_dt is None:
                if start_dt and now >= start_dt:
                    is_overdue = True
            elif max_open_round > 0 and rn < max_open_round:
                # Skip any round whose own deadline is still in the future
                if not (dl_dt and dl_dt > now):
                    if start_dt and now >= start_dt:
                        is_overdue = True
            elif not is_open and r_info and max_open_round > 0 and rn <= max_open_round:
                # Closed past round — but never overdue while its deadline is in the future
                if not (dl_dt and dl_dt > now):
                    if start_dt and now >= start_dt:
                        is_overdue = True

            if not is_overdue:
                continue

            t1 = m.get("player1_team")
            t2 = m.get("player2_team")
            u1 = get_team_owner(t1)
            u2 = get_team_owner(t2)

            m["player1_id"] = u1.get("telegram_id") if u1 else None
            m["p1_username"] = u1.get("username") if u1 else None
            m["p1_warns"] = u1.get("warn_count", 0) if u1 else 0

            m["player2_id"] = u2.get("telegram_id") if u2 else None
            m["p2_username"] = u2.get("username") if u2 else None
            m["p2_warns"] = u2.get("warn_count", 0) if u2 else 0

            # Calculate overdue hours relative to effective deadline / start_dt
            effective_dl = dl_dt if dl_dt else start_dt
            if start_dt and (effective_dl is None or effective_dl < start_dt):
                effective_dl = start_dt

            if effective_dl and now >= effective_dl:
                hours_overdue = (now - effective_dl).total_seconds() / 3600.0
            else:
                hours_overdue = 0.0

            # Exclude frozen time from the overdue clock: an admin freeze must
            # SHIFT the auto-warn schedule, not let it burn through milestones
            # the moment the match is unfrozen.
            frozen_total = float(m.get("frozen_seconds") or 0)
            if m.get("is_extended") and m.get("frozen_at"):
                f_at = parse_flexible_datetime(m["frozen_at"])
                if f_at and now > f_at:
                    frozen_total += (now - f_at).total_seconds()
            hours_overdue -= frozen_total / 3600.0

            m["deadline_str"] = r_info.get("deadline_str") if r_info and r_info.get("deadline_str") else (start_dt.strftime("%d.%m.%Y %H:%M") if start_dt else "—")
            m["deadline_dt"] = effective_dl
            m["frozen_hours"] = max(0.0, frozen_total / 3600.0)
            m["hours_overdue"] = max(0.0, hours_overdue)
            overdue_list.append(m)

        return overdue_list


def get_debt_tracking_start_datetime() -> datetime.datetime | None:
    """Parse configured debt tracking activation start datetime."""
    from config import DEBT_TRACKING_START_DATETIME
    if not DEBT_TRACKING_START_DATETIME:
        return None
    return parse_flexible_datetime(DEBT_TRACKING_START_DATETIME)


def is_match_overdue(match_id: int) -> bool:
    """Check if a match is overdue or was recorded as a debt."""
    with transaction() as conn:
        cursor = conn.cursor()
        # 1. If any debt stages/reminders were already recorded for this match, it is legitimately a debt
        cursor.execute("SELECT 1 FROM debt_reminders WHERE match_id = ? LIMIT 1", (match_id,))
        if cursor.fetchone():
            return True

        cursor.execute("SELECT round_number, division_id FROM matches WHERE id = ?", (match_id,))
        row = cursor.fetchone()
        if not row:
            return False
        rn = row["round_number"]
        m_div_id = row["division_id"]

        if m_div_id is not None:
            cursor.execute("SELECT is_open, deadline FROM rounds WHERE division_id = ? AND round_number = ?", (m_div_id, rn))
        else:
            cursor.execute("SELECT is_open, deadline FROM rounds WHERE round_number = ? LIMIT 1", (rn,))
        r_row = cursor.fetchone()
        if not r_row:
            return False

        dl_dt = parse_flexible_datetime(r_row["deadline"])
        is_open = bool(r_row["is_open"])
        start_dt = get_debt_tracking_start_datetime()
        now = datetime.datetime.now()

        if m_div_id is not None:
            cursor.execute("SELECT MAX(round_number) FROM rounds WHERE is_open = 1 AND division_id = ?", (m_div_id,))
        else:
            cursor.execute("SELECT MAX(round_number) FROM rounds WHERE is_open = 1")
        max_row = cursor.fetchone()
        max_open = max_row[0] if max_row and max_row[0] is not None else 0

        if dl_dt:
            effective_dl = dl_dt
            if start_dt and dl_dt < start_dt:
                effective_dl = start_dt
            return now >= effective_dl

        if is_open and start_dt:
            return now >= start_dt

        if max_open > 0 and rn < max_open and start_dt:
            return now >= start_dt

        return False


def division_has_played_matches(division_id: int | None = None, season_id: int | None = None) -> bool:
    """Check if any match in the division and season has already been confirmed or completed."""
    with transaction() as conn:
        cursor = conn.cursor()
        query = "SELECT 1 FROM matches WHERE status IN ('confirmed', 'completed')"
        params = []
        if division_id is not None:
            query += " AND division_id = ?"
            params.append(division_id)
        if season_id is not None:
            query += " AND season_id = ?"
            params.append(season_id)
        else:
            act = get_active_season()
            if act:
                query += " AND (season_id = ? OR season_id IS NULL)"
                params.append(act["id"])
        query += " LIMIT 1"
        cursor.execute(query, tuple(params))
        return cursor.fetchone() is not None


def find_user_by_team(team_name: str | None) -> dict | None:
    """Find a user record by assigned team name using case-insensitive and smart alias/fuzzy matching."""
    if not team_name:
        return None
    tn_target = team_name.strip().lower()
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE team_name IS NOT NULL")
        users = [dict(r) for r in cursor.fetchall()]
        
        # 1. Exact case-insensitive match
        for u in users:
            u_team = (u.get("team_name") or "").strip().lower()
            if u_team == tn_target:
                return u

        # 2. Smart teams_match / aliases
        for u in users:
            u_team = (u.get("team_name") or "").strip()
            if teams_match(u_team, team_name):
                return u

    return None


def has_user_been_warned_recently(user_id: int, hours: float = 20.0) -> bool:
    """Check if user has received a warn within the last N hours."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT created_at FROM user_warns 
            WHERE user_id = ? AND type = 'WARN_ADD'
            ORDER BY created_at DESC, id DESC LIMIT 1
        """, (user_id,))
        row = cursor.fetchone()
        if not row or not row["created_at"]:
            return False
        warn_dt = parse_flexible_datetime(row["created_at"])
        if not warn_dt:
            return False
        diff_sec = (datetime.datetime.now() - warn_dt).total_seconds()
        # Negative diff (clock stepped backwards) also counts as "recently warned"
        # so a rollback of the system clock cannot defeat the rate limiter.
        return diff_sec < (hours * 3600.0)



def reset_all_debt_reminders() -> None:
    """Clear all recorded debt stages and reminders."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM debt_reminders")


def admin_reset_all_warns_and_debts() -> int:
    """Reset all user warns to 0, clear debt_reminders, and return count of affected users."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET warn_count = 0 WHERE warn_count > 0")
        affected = cursor.rowcount
        cursor.execute("DELETE FROM debt_reminders")
        cursor.execute("DELETE FROM user_warns")
        return affected


def restore_user_team(telegram_id: int, team_name: str) -> None:
    """Assign/restore team_name for a user and clear warns."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET team_name = ?, warn_count = 0 WHERE telegram_id = ?", (team_name, telegram_id))
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO user_warns (user_id, admin_id, reason, type, created_at) VALUES (?, NULL, 'Восстановление клуба и сброс варнов', 'RESTORE', ?)",
            (telegram_id, now_str)
        )



def apply_debt_played_reward(user_id: int, round_number: int) -> tuple[int, bool]:
    """
    Reward player for clearing a debt match by removing 1 warn if warn_count > 0.
    If warn_count == 0, it remains 0.
    Returns (new_warn_count, was_unwarned).
    """
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT warn_count FROM users WHERE telegram_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            return 0, False
        current_warns = row["warn_count"] or 0
        if current_warns <= 0:
            return 0, False
        
        new_warns = max(0, current_warns - 1)
        cursor.execute("UPDATE users SET warn_count = ? WHERE telegram_id = ?", (new_warns, user_id))
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO user_warns (user_id, admin_id, reason, type, created_at) VALUES (?, NULL, ?, 'DEBT_UNWARN', ?)",
            (user_id, f"Снятие варна за закрытие долга ({round_number} тур)", now_str)
        )
        return new_warns, True


def count_user_remaining_debts(user_id: int) -> int:
    """Count how many overdue/debt matches currently remain for a given user."""
    overdue_matches = get_detailed_overdue_matches()
    count = 0
    for m in overdue_matches:
        if m.get("player1_id") == user_id or m.get("player2_id") == user_id:
            count += 1
    return count


# ═════════════════════════════════════════════════════════════════════════════
# 🎰 LOGOVO.BET — VIRTUAL SPORTS PREDICTION & BETTING REPOSITORY
# ═════════════════════════════════════════════════════════════════════════════

def get_active_round_number() -> int:
    """Return the lowest currently open round number or the latest created round."""
    try:
        open_rounds = get_open_rounds_with_deadlines()
        if open_rounds:
            return min(r["round_number"] for r in open_rounds)
        all_rounds = get_all_rounds()
        if all_rounds:
            return max(r["round_number"] for r in all_rounds)
    except Exception:
        pass
    return 1


def get_or_create_wallet(user_id: int) -> dict:
    """Get user's betting wallet or initialize a new one with 1,000 start coins."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM user_wallets WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        
        cursor.execute(
            """
            INSERT INTO user_wallets (user_id, balance, total_wagered, total_won, bets_count, bets_won)
            VALUES (?, 1000, 0, 0, 0, 0)
            """,
            (user_id,)
        )
        cursor.execute(
            "INSERT INTO coin_transactions (user_id, amount, transaction_type) VALUES (?, 1000, 'welcome_bonus')",
            (user_id,)
        )
        cursor.execute("SELECT * FROM user_wallets WHERE user_id = ?", (user_id,))
        new_row = cursor.fetchone()
        return dict(new_row) if new_row else {"user_id": user_id, "balance": 1000}


def get_wallet_balance(user_id: int) -> int:
    """Get the current coin balance of a user."""
    wallet = get_or_create_wallet(user_id)
    return wallet.get("balance", 0)


def add_coins(user_id: int, amount: int, tx_type: str = "deposit", ref_id: int | None = None) -> int:
    """Safely credit coins to user's wallet with transaction log."""
    if amount <= 0:
        return get_wallet_balance(user_id)

    with transaction() as conn:
        cursor = conn.cursor()
        get_or_create_wallet(user_id)
        cursor.execute(
            "UPDATE user_wallets SET balance = balance + ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (amount, user_id)
        )
        cursor.execute(
            "INSERT INTO coin_transactions (user_id, amount, transaction_type, reference_id) VALUES (?, ?, ?, ?)",
            (user_id, amount, tx_type, ref_id)
        )
        cursor.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return row["balance"] if row else 0


def deduct_coins(user_id: int, amount: int, tx_type: str = "bet_placed", ref_id: int | None = None) -> bool:
    """Deduct coins from wallet if balance is sufficient."""
    if amount <= 0:
        return False

    with transaction() as conn:
        cursor = conn.cursor()
        get_or_create_wallet(user_id)
        cursor.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row or row["balance"] < amount:
            return False

        cursor.execute(
            """
            UPDATE user_wallets 
            SET balance = balance - ?, total_wagered = total_wagered + ?, updated_at = CURRENT_TIMESTAMP 
            WHERE user_id = ?
            """,
            (amount, amount, user_id)
        )
        cursor.execute(
            "INSERT INTO coin_transactions (user_id, amount, transaction_type, reference_id) VALUES (?, ?, ?, ?)",
            (user_id, -amount, tx_type, ref_id)
        )
        return True


def claim_daily_bonus(user_id: int, bonus_amount: int = 250) -> tuple[bool, int, str]:
    """
    Claim daily bonus once every 24 hours.
    Returns (success, new_balance_or_remaining_hours, message).
    """
    from config import is_global_lockdown_enabled
    if is_global_lockdown_enabled():
        from handlers.base import is_global_admin
        if not is_global_admin(user_id):
            return False, 0, "Logovo.bet временно закрыт для пользователей."

    with transaction() as conn:
        cursor = conn.cursor()
        wallet = get_or_create_wallet(user_id)
        last_bonus = wallet.get("last_bonus_at")
        now = datetime.datetime.now()

        if last_bonus:
            try:
                last_time = datetime.datetime.fromisoformat(last_bonus)
                diff = now - last_time
                if diff.total_seconds() < 86400:
                    remaining_secs = 86400 - diff.total_seconds()
                    rem_hours = int(remaining_secs // 3600)
                    rem_mins = int((remaining_secs % 3600) // 60)
                    return False, rem_hours, f"⏳ Бонус уже получен! Следующий через {rem_hours}ч {rem_mins}м."
            except Exception:
                pass

        now_str = now.isoformat()
        cursor.execute(
            """
            UPDATE user_wallets 
            SET balance = balance + ?, last_bonus_at = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE user_id = ?
            """,
            (bonus_amount, now_str, user_id)
        )
        cursor.execute(
            "INSERT INTO coin_transactions (user_id, amount, transaction_type) VALUES (?, ?, 'daily_bonus')",
            (user_id, bonus_amount)
        )
        cursor.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        new_bal = row["balance"] if row else 0
        return True, new_bal, f"🎁 Ежедневный бонус получен: <b>+{bonus_amount} 🪙</b>!"


def get_top_bettors(limit: int = 10) -> list[dict]:
    """Leaderboard of top bettors by net coin balance and win rate."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT w.user_id, w.balance, w.total_won, w.bets_count, w.bets_won, u.username, u.team_name
            FROM user_wallets w
            LEFT JOIN users u ON w.user_id = u.telegram_id
            ORDER BY w.balance DESC, w.total_won DESC
            LIMIT ?
            """,
            (limit,)
        )
        return [dict(r) for r in cursor.fetchall()]


def save_bet_market(
    match_id: int,
    tour: int,
    team1_name: str,
    team2_name: str,
    odd_p1: float,
    odd_x: float,
    odd_p2: float,
    odd_tb25: float = 1.80,
    odd_tm25: float = 1.95,
    odd_btts_yes: float = 1.70,
    odd_btts_no: float = 2.05
) -> int:
    """Save or update betting odds for a given match."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bet_markets (
                match_id, tour, team1_name, team2_name,
                odd_p1, odd_x, odd_p2, odd_tb25, odd_tm25, odd_btts_yes, odd_btts_no, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(match_id) DO UPDATE SET
                tour = excluded.tour,
                team1_name = excluded.team1_name,
                team2_name = excluded.team2_name,
                odd_p1 = excluded.odd_p1,
                odd_x = excluded.odd_x,
                odd_p2 = excluded.odd_p2,
                odd_tb25 = excluded.odd_tb25,
                odd_tm25 = excluded.odd_tm25,
                odd_btts_yes = excluded.odd_btts_yes,
                odd_btts_no = excluded.odd_btts_no,
                is_active = 1
            """,
            (match_id, tour, team1_name, team2_name, odd_p1, odd_x, odd_p2, odd_tb25, odd_tm25, odd_btts_yes, odd_btts_no)
        )
        return cursor.lastrowid or match_id


def _parse_round_deadline(dl_str: str | None) -> datetime.datetime | None:
    """Safely parse deadline string from various common formats."""
    if not dl_str:
        return None
    dl_clean = str(dl_str).strip()
    formats = [
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%d/%m/%Y %H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.datetime.strptime(dl_clean, fmt)
        except Exception:
            continue
    try:
        return datetime.datetime.fromisoformat(dl_clean)
    except Exception:
        return None


def get_open_betting_tours(division_id: int | None = None, season_id: int | None = None) -> list[dict]:
    """
    Retrieve all currently open tours that have unplayed matches
    and where the round deadline has not expired.
    Strictly filters by rounds.is_open = 1 and optional division_id/season_id.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        query = """
            SELECT 
                r.round_number, r.deadline, r.division_id, r.season_id,
                COUNT(m.id) as total_matches,
                SUM(CASE WHEN m.status NOT IN ('confirmed', 'completed') THEN 1 ELSE 0 END) as unplayed_matches
            FROM rounds r
            JOIN matches m ON r.round_number = m.round_number AND (r.division_id = m.division_id OR r.division_id IS NULL OR m.division_id IS NULL)
            WHERE r.is_open = 1
        """
        params = []
        if division_id is not None:
            query += " AND (r.division_id = ? OR r.division_id IS NULL)"
            params.append(division_id)
        if season_id is not None:
            query += " AND (r.season_id = ? OR r.season_id IS NULL)"
            params.append(season_id)

        query += """
            GROUP BY r.round_number, r.deadline, r.division_id, r.season_id
            HAVING unplayed_matches > 0
            ORDER BY r.round_number ASC
        """
        cursor.execute(query, params)
        rows = cursor.fetchall()
        now = datetime.datetime.now()
        open_tours = []
        for row in rows:
            r_num = row["round_number"]
            dl_str = row["deadline"]
            dl_dt = _parse_round_deadline(dl_str)
            if dl_dt and now > dl_dt:
                # Deadline passed: close the betting market for this tour
                cursor.execute("UPDATE bet_markets SET is_active = 0 WHERE tour = ?", (r_num,))
                continue

            open_tours.append({
                "round_number": r_num,
                "deadline": dl_str,
                "division_id": row["division_id"],
                "season_id": row["season_id"],
                "total_matches": row["total_matches"],
                "unplayed_matches": row["unplayed_matches"]
            })
        return open_tours


def get_active_bet_markets(tour: int | None = None, division_id: int | None = None) -> list[dict]:
    """Retrieve open betting markets for unplayed matches strictly in open rounds."""
    with transaction() as conn:
        cursor = conn.cursor()
        query = """
            SELECT bm.*, m.status as match_status, m.round_number, m.division_id, r.deadline, r.is_open
            FROM bet_markets bm
            JOIN matches m ON bm.match_id = m.id
            JOIN rounds r ON m.round_number = r.round_number AND (r.division_id = m.division_id OR r.division_id IS NULL OR m.division_id IS NULL)
            WHERE bm.is_active = 1 AND m.status NOT IN ('confirmed', 'completed') AND r.is_open = 1
        """
        params = []
        if tour is not None:
            query += " AND bm.tour = ?"
            params.append(tour)
        if division_id is not None:
            query += " AND (m.division_id = ? OR m.division_id IS NULL)"
            params.append(division_id)
        query += " ORDER BY bm.tour ASC, bm.id ASC"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        now = datetime.datetime.now()
        valid_markets = []
        for r in rows:
            dl_dt = _parse_round_deadline(r["deadline"])
            if dl_dt and now > dl_dt:
                cursor.execute("UPDATE bet_markets SET is_active = 0 WHERE match_id = ?", (r["match_id"],))
                continue
            valid_markets.append(dict(r))
        return valid_markets


def get_bet_market_by_match_id(match_id: int) -> dict | None:
    """Fetch market odds for a specific match ID if round is open."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT bm.*, r.is_open, r.deadline
            FROM bet_markets bm
            JOIN matches m ON bm.match_id = m.id
            JOIN rounds r ON m.round_number = r.round_number
            WHERE bm.match_id = ? AND r.is_open = 1 AND m.status NOT IN ('confirmed', 'completed')
        """, (match_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


# Phase 5: Bet limits (server-side, cannot be bypassed by client)
_MAX_BET: int = 50_000
_MAX_PAYOUT: int = 500_000
_bet_placement_lock = threading.RLock()


def place_user_bet(
    user_id: int,
    amount: int,
    selections: list[dict],
    idempotency_key: str | None = None
) -> tuple[bool, int | str | dict]:
    """
    Validate, deduct coins, and store user prediction coupon atomically.
    Supports single & express parlays, relational selections, and idempotency protection.
    Phase 5: Adds MAX_BET/MAX_PAYOUT limits, ODDS_CHANGED detection, IDEMPOTENCY_KEY_REUSED.
    """
    from config import is_global_lockdown_enabled
    if is_global_lockdown_enabled():
        from handlers.base import is_global_admin
        if not is_global_admin(user_id):
            return False, {"error": "LOGOVO_LOCKDOWN", "message": "Logovo.bet временно закрыт для пользователей"}

    import datetime
    import hashlib as _hashlib
    import json as _json

    if amount < 10:
        return False, "Минимальная сумма прогноза — 10 🪙."

    if amount > _MAX_BET:
        return False, {"error": "MAX_BET_EXCEEDED", "max_bet": _MAX_BET,
                       "message": f"Максимальная сумма ставки — {_MAX_BET:,} 🪙."}

    if not selections or not isinstance(selections, list):
        return False, "Купон пуст."

    # Compute idempotency payload hash (Phase 5)
    _payload_for_hash = _json.dumps(
        {"amount": amount, "sel": sorted(
            [(s.get("match_id"), s.get("outcome") or s.get("selection_key")) for s in selections]
        )},
        sort_keys=True, separators=(',', ':')
    )
    _payload_hash = _hashlib.sha256(_payload_for_hash.encode()).hexdigest()

    with _bet_placement_lock, transaction() as conn:
        cursor = conn.cursor()

        # Idempotency 2.0: key + payload hash check (Phase 5)
        if idempotency_key:
            cursor.execute(
                "SELECT id, idempotency_payload_hash FROM user_bets WHERE user_id = ? AND idempotency_key = ?",
                (user_id, idempotency_key)
            )
            existing = cursor.fetchone()
            if existing:
                existing_hash = existing["idempotency_payload_hash"]
                if existing_hash and existing_hash != _payload_hash:
                    return False, {"error": "IDEMPOTENCY_KEY_REUSED",
                                   "message": "Ключ идемпотентности уже использован для другой ставки."}
                return True, existing["id"]

        # Phase 9: Risk Engine Evaluation
        try:
            from services.risk_engine import RiskEngine
            first_m_id = selections[0].get("match_id") if selections else None
            div_id = None
            if first_m_id:
                cursor.execute("SELECT division_id FROM matches WHERE id = ?", (first_m_id,))
                m_r = cursor.fetchone()
                div_id = m_r["division_id"] if m_r and "division_id" in m_r.keys() else None

            risk_decision = RiskEngine.evaluate_bet(
                user_id=user_id,
                amount=amount,
                selections=selections,
                division_id=div_id
            )
            if not risk_decision.allowed:
                if risk_decision.reason == "MIN_STAKE":
                    return False, "Минимальная сумма прогноза — 10 🪙."
                if risk_decision.reason == "MAX_STAKE":
                    return False, {"error": "MAX_BET_EXCEEDED", "max_bet": _MAX_BET,
                                   "message": f"Максимальная сумма ставки — {_MAX_BET:,} 🪙."}
                if risk_decision.reason == "MAX_PAYOUT":
                    return False, {"error": "MAX_PAYOUT_EXCEEDED", "max_payout": _MAX_PAYOUT,
                                   "message": f"Потенциальный выигрыш превышает максимум {_MAX_PAYOUT:,} 🪙."}
                if risk_decision.reason == "INSUFFICIENT_BALANCE":
                    wallet = get_or_create_wallet(user_id)
                    return False, f"Недостаточно монет на балансе (Баланс: {wallet['balance']} 🪙)."
                if risk_decision.reason in ("MARKET_SUSPENDED", "INVALID_MARKET"):
                    return False, risk_decision.message or "Рынок на данный исход временно приостановлен или закрыт."

                err_dict = {
                    "error": risk_decision.reason,
                    "message": risk_decision.message,
                }
                if risk_decision.max_allowed_stake is not None:
                    err_dict["max_allowed_stake"] = risk_decision.max_allowed_stake
                return False, err_dict
        except Exception as e:
            logger.debug(f"RiskEngine evaluation fallback: {e}")

        wallet = get_or_create_wallet(user_id)
        if wallet["balance"] < amount:
            return False, f"Недостаточно монет на балансе (Баланс: {wallet['balance']} 🪙)."

        # Validate selections
        total_odd = 1.0
        validated_items = []
        seen_matches = set()
        now = datetime.datetime.now(datetime.timezone.utc)

        for s in selections:
            m_id = s.get("match_id")
            out_type = s.get("outcome") or s.get("selection_key")
            mkt_id = s.get("market_id")
            sel_id = s.get("selection_id")

            if not m_id or not out_type:
                return False, "Некорректная структура исхода в купоне."

            if len(selections) > 1 and m_id in seen_matches:
                return False, f"Нельзя добавлять несколько исходов из одного матча #{m_id} в стандартный экспресс."
            seen_matches.add(m_id)

            # Check match status
            cursor.execute("SELECT * FROM matches WHERE id = ?", (m_id,))
            match_row = cursor.fetchone()
            if not match_row:
                return False, f"Матч #{m_id} не найден."
            if match_row["status"] not in ("scheduled", "pending", "live", "open"):
                return False, f"Матч #{m_id} уже сыгран или завершен."

            # Check round deadline if present (scoped by division)
            r_num = match_row["round_number"] if "round_number" in match_row.keys() else None
            m_div_id = match_row["division_id"] if "division_id" in match_row.keys() and match_row["division_id"] is not None else 1
            if r_num:
                cursor.execute("SELECT is_open, deadline FROM rounds WHERE division_id = ? AND round_number = ?", (m_div_id, r_num))
                r_row = cursor.fetchone()
                if not r_row:
                    cursor.execute("SELECT is_open, deadline FROM rounds WHERE round_number = ? ORDER BY is_open DESC, id DESC LIMIT 1", (r_num,))
                    r_row = cursor.fetchone()
                if r_row:
                    if not r_row["is_open"]:
                        return False, f"Приём прогнозов на Тур {r_num} закрыт."
                    if r_row["deadline"]:
                        raw_dl = str(r_row["deadline"]).strip()
                        dl_dt = None
                        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                            try:
                                dl_dt = datetime.datetime.strptime(raw_dl[:19], fmt)
                                break
                            except ValueError:
                                pass
                        if dl_dt and datetime.datetime.now() > dl_dt:
                            return False, f"Дедлайн для прогнозов на Тур {r_num} истек."

            # Determine odds value from relational schema or legacy bet_markets
            odd_val = None
            resolved_market_id = mkt_id
            resolved_sel_id = sel_id

            if resolved_market_id and resolved_sel_id:
                cursor.execute("""
                    SELECT ms.odds_value, ms.status as sel_status, m.status as mkt_status
                    FROM market_selections ms
                    JOIN markets m ON ms.market_id = m.id
                    WHERE ms.id = ? AND ms.market_id = ?
                """, (resolved_sel_id, resolved_market_id))
                ms_row = cursor.fetchone()
                if ms_row:
                    if ms_row["mkt_status"] in ("suspended", "closed", "settled") or ms_row["sel_status"] in ("locked", "suspended", "settled"):
                        return False, f"Рынок на исход '{out_type}' временно приостановлен или закрыт."
                    if ms_row["mkt_status"] in ("open", "active") and ms_row["sel_status"] == "active":
                        odd_val = float(ms_row["odds_value"])
            
            if odd_val is None:
                cursor.execute("""
                    SELECT ms.id as sel_id, ms.market_id, ms.odds_value, ms.status as sel_status, m.status as mkt_status
                    FROM market_selections ms
                    JOIN markets m ON ms.market_id = m.id
                    WHERE m.match_id = ? AND ms.selection_key = ?
                """, (m_id, out_type))
                ms_match = cursor.fetchone()
                if ms_match:
                    if ms_match["mkt_status"] in ("suspended", "closed", "settled") or ms_match["sel_status"] in ("locked", "suspended", "settled"):
                        return False, f"Рынок на исход '{out_type}' временно приостановлен или закрыт."
                    if ms_match["mkt_status"] in ("open", "active") and ms_match["sel_status"] == "active":
                        odd_val = float(ms_match["odds_value"])
                        resolved_market_id = ms_match["market_id"]
                        resolved_sel_id = ms_match["sel_id"]

            if odd_val is None:
                cursor.execute("SELECT * FROM bet_markets WHERE match_id = ? AND is_active = 1", (m_id,))
                bm_row = cursor.fetchone()
                if bm_row:
                    if out_type == "p1":
                        odd_val = bm_row["odd_p1"]
                    elif out_type == "x":
                        odd_val = bm_row["odd_x"]
                    elif out_type == "p2":
                        odd_val = bm_row["odd_p2"]
                    elif out_type in ("tb25", "over_2.5"):
                        odd_val = bm_row["odd_tb25"]
                    elif out_type in ("tm25", "under_2.5"):
                        odd_val = bm_row["odd_tm25"]
                    elif out_type in ("btts_yes", "yes"):
                        odd_val = bm_row["odd_btts_yes"]
                    elif out_type in ("btts_no", "no"):
                        odd_val = bm_row["odd_btts_no"]

            if odd_val is None:
                return False, f"Исход '{out_type}' на матч #{m_id} недоступен или заблокирован."

            odd_val = round(float(odd_val), 2)

            # Phase 5: ODDS_CHANGED detection — client odd vs server odd
            # Only trigger ODDS_CHANGED if client explicitly references a specific market selection
            # (relational betting via selection_id or market_id). Legacy bets without selection_id
            # have client odds safely ignored and overridden by server odds.
            client_odd = s.get("odd")
            if (s.get("selection_id") is not None or s.get("market_id") is not None) and client_odd is not None:
                client_odd_rounded = round(float(client_odd), 2)
                if abs(client_odd_rounded - odd_val) > 0.001:
                    return False, {
                        "error": "ODDS_CHANGED",
                        "match_id": m_id,
                        "outcome": out_type,
                        "old_odd": client_odd_rounded,
                        "new_odd": odd_val,
                        "message": f"Коэффициент изменился: {client_odd_rounded} → {odd_val}"
                    }

            total_odd *= max(1.01, odd_val)
            validated_items.append({
                "match_id": m_id,
                "outcome_type": out_type,
                "odd": odd_val,
                "market_id": resolved_market_id,
                "selection_id": resolved_sel_id
            })

        total_odd = round(total_odd, 2)
        potential_win = int(round(amount * total_odd))
        bet_type = "single" if len(validated_items) == 1 else "express"

        # Phase 5: MAX_PAYOUT check
        if potential_win > _MAX_PAYOUT:
            return False, {"error": "MAX_PAYOUT_EXCEEDED", "max_payout": _MAX_PAYOUT,
                           "message": f"Потенциальный выигрыш {potential_win:,} превышает максимум {_MAX_PAYOUT:,} 🪙."}

        # 1. Deduct coins with strict balance check
        # 1. Insert user_bet with idempotency check (Phase 5: includes idempotency_payload_hash)
        try:
            cursor.execute("""
                INSERT INTO user_bets (user_id, bet_type, amount, total_odd, potential_win, status, idempotency_key, idempotency_payload_hash)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """, (user_id, bet_type, amount, total_odd, potential_win, idempotency_key, _payload_hash))
            bet_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            if idempotency_key:
                cursor.execute(
                    "SELECT id, idempotency_payload_hash FROM user_bets WHERE user_id = ? AND idempotency_key = ?",
                    (user_id, idempotency_key)
                )
                existing = cursor.fetchone()
                if existing:
                    existing_hash = existing["idempotency_payload_hash"]
                    if existing_hash and existing_hash != _payload_hash:
                        return False, {"error": "IDEMPOTENCY_KEY_REUSED",
                                       "message": "Ключ идемпотентности уже использован для другой ставки."}
                    return True, existing["id"]
            raise

        # 2. Deduct coins with strict balance check
        cursor.execute("""
            UPDATE user_wallets 
            SET balance = balance - ?, total_wagered = total_wagered + ?, bets_count = bets_count + 1, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND balance >= ?
        """, (amount, amount, user_id, amount))

        if cursor.rowcount == 0:
            conn.rollback()
            return False, "Недостаточно монет на балансе."

        cursor.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (user_id,))
        new_balance = cursor.fetchone()["balance"]

        # 3. Insert items
        for item in validated_items:
            cursor.execute("""
                INSERT INTO bet_items (bet_id, match_id, outcome_type, odd, market_id, selection_id, odds_at_placement, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (
                bet_id,
                item["match_id"],
                item["outcome_type"],
                item["odd"],
                item["market_id"],
                item["selection_id"],
                item["odd"]
            ))

        # 4. Record transaction with balance_after
        cursor.execute("""
            INSERT INTO coin_transactions (user_id, amount, transaction_type, reference_id, reference_type, balance_after)
            VALUES (?, ?, 'bet_placed', ?, 'bet', ?)
        """, (user_id, -amount, bet_id, new_balance))

        return True, bet_id


def execute_cashout(
    user_id: int,
    bet_id: int,
    idempotency_key: str | None = None
) -> tuple[bool, dict | str]:
    """
    Execute atomic early cashout settlement (Phase 9).
    1. Check if bet is pending and not yet settled (settled_at IS NULL).
    2. Verify matches/markets are still active.
    3. Calculate cashout offer.
    4. Atomically update user_bets (actual_payout = offer, cashout_at = CURRENT_TIMESTAMP, settled_at = CURRENT_TIMESTAMP, status = 'won').
    5. Credit user_wallets and record coin_transactions with transaction_type = 'cashout'.
    """
    from config import is_global_lockdown_enabled
    if is_global_lockdown_enabled():
        from handlers.base import is_global_admin
        if not is_global_admin(user_id):
            return False, {"error": "LOGOVO_LOCKDOWN", "message": "Logovo.bet временно закрыт для пользователей"}

    with _bet_placement_lock, transaction() as conn:
        cursor = conn.cursor()

        # 1. Fetch bet
        cursor.execute("SELECT * FROM user_bets WHERE id = ? AND user_id = ?", (bet_id, user_id))
        bet = cursor.fetchone()
        if not bet:
            return False, {"error": "BET_NOT_FOUND", "message": f"Ставка #{bet_id} не найдена."}

        if bet["settled_at"] is not None or bet["status"] != "pending":
            return False, {"error": "ALREADY_SETTLED", "message": "Ставка уже рассчитана или закрыта."}

        # 2. Fetch bet items
        cursor.execute("""
            SELECT bi.*,
                   ms.odds_value as current_odd,
                   ms.status as sel_status,
                   m.status as market_status,
                   mat.status as match_status
            FROM bet_items bi
            LEFT JOIN market_selections ms ON bi.selection_id = ms.id
            LEFT JOIN markets m ON bi.market_id = m.id
            LEFT JOIN matches mat ON bi.match_id = mat.id
            WHERE bi.bet_id = ?
        """, (bet_id,))
        items = [dict(r) for r in cursor.fetchall()]

        # Resolve current odds fallback if selection_id is NULL
        for it in items:
            if it.get("current_odd") is None:
                cursor.execute("""
                    SELECT ms.odds_value, ms.status as sel_status, m.status as market_status
                    FROM market_selections ms
                    JOIN markets m ON ms.market_id = m.id
                    WHERE m.match_id = ? AND ms.selection_key = ?
                """, (it["match_id"], it["outcome_type"]))
                ms_r = cursor.fetchone()
                if ms_r:
                    it["current_odd"] = float(ms_r["odds_value"])
                    it["sel_status"] = ms_r["sel_status"]
                    it["market_status"] = ms_r["market_status"]
                else:
                    cursor.execute("SELECT * FROM bet_markets WHERE match_id = ? AND is_active = 1", (it["match_id"],))
                    bm_r = cursor.fetchone()
                    if bm_r:
                        bm_map = {
                            "p1": "odd_p1", "x": "odd_x", "p2": "odd_p2",
                            "over_2.5": "odd_tb25", "tb25": "odd_tb25",
                            "under_2.5": "odd_tm25", "tm25": "odd_tm25",
                            "btts_yes": "odd_btts_yes", "btts_no": "odd_btts_no"
                        }
                        col = bm_map.get(it["outcome_type"])
                        if col and bm_r[col]:
                            it["current_odd"] = float(bm_r[col])
                            it["sel_status"] = "active"
                            it["market_status"] = "open"

        for it in items:
            if it.get("match_status") in ("completed", "confirmed", "cancelled"):
                return False, {"error": "MATCH_TERMINAL", "message": "Один или несколько матчей уже завершены."}
            if it.get("market_status") in ("suspended", "closed", "settled") or it.get("sel_status") in ("suspended", "locked", "settled"):
                return False, {"error": "MARKET_UNAVAILABLE", "message": "Рынок временно приостановлен или закрыт."}

        # 3. Calculate cashout offer
        from services.cashout_engine import calculate_cashout_offer
        available, offer, reason = calculate_cashout_offer(
            stake=bet["amount"],
            potential_win=bet["potential_win"],
            items=items
        )

        if not available or offer <= 0:
            return False, {"error": "CASHOUT_UNAVAILABLE", "reason": reason, "message": "Cashout временно недоступен для данного купона."}

        # 4. Atomically settle bet as cashout
        cursor.execute("""
            UPDATE user_bets
            SET status = 'won',
                actual_payout = ?,
                cashout_at = CURRENT_TIMESTAMP,
                settled_at = CURRENT_TIMESTAMP
            WHERE id = ? AND settled_at IS NULL AND status = 'pending'
        """, (offer, bet_id))

        if cursor.rowcount == 0:
            return False, {"error": "ALREADY_SETTLED", "message": "Ставка уже рассчитана другим процессом."}

        # 5. Credit user wallet
        get_or_create_wallet(user_id)
        cursor.execute("""
            UPDATE user_wallets
            SET balance = balance + ?,
                total_won = total_won + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (offer, offer, user_id))

        cursor.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (user_id,))
        new_balance = cursor.fetchone()["balance"]

        # 6. Record transaction
        cursor.execute("""
            INSERT INTO coin_transactions (user_id, amount, transaction_type, reference_id, reference_type, balance_after)
            VALUES (?, ?, 'cashout', ?, 'bet', ?)
        """, (user_id, offer, bet_id, new_balance))

        return True, {
            "bet_id": bet_id,
            "status": "cashed_out",
            "cashout_payout": offer,
            "payout": offer,
            "balance": new_balance,
            "message": f"✅ Ставка #{bet_id} успешно закрыта досрочно (+{offer} 🪙)"
        }


def get_user_balance(user_id: int) -> int:
    """Convenience getter for user wallet balance."""
    wallet = get_or_create_wallet(user_id)
    return wallet.get("balance", 0)


def get_user_bet_by_id(arg1: int | None = None, arg2: int | None = None, user_id: int | None = None, bet_id: int | None = None) -> dict | None:
    """
    Fetch a single user prediction slip with its nested legs and status.
    Supports:
      - get_user_bet_by_id(user_id, bet_id)
      - get_user_bet_by_id(bet_id, user_id)
      - get_user_bet_by_id(user_id=user_id, bet_id=bet_id)
      - get_user_bet_by_id(bet_id, user_id=user_id)
    """
    with transaction() as conn:
        cursor = conn.cursor()
        row = None
        if user_id is not None and bet_id is not None:
            cursor.execute("SELECT * FROM user_bets WHERE id = ? AND user_id = ?", (bet_id, user_id))
            row = cursor.fetchone()
        elif user_id is not None and arg1 is not None:
            cursor.execute("SELECT * FROM user_bets WHERE id = ? AND user_id = ?", (arg1, user_id))
            row = cursor.fetchone()
        elif bet_id is not None and arg1 is not None:
            cursor.execute("SELECT * FROM user_bets WHERE id = ? AND user_id = ?", (bet_id, arg1))
            row = cursor.fetchone()
        elif arg1 is not None and arg2 is not None:
            cursor.execute("SELECT * FROM user_bets WHERE id = ? AND user_id = ?", (arg2, arg1))
            row = cursor.fetchone()
            if not row:
                cursor.execute("SELECT * FROM user_bets WHERE id = ? AND user_id = ?", (arg1, arg2))
                row = cursor.fetchone()
        else:
            return None

        if not row:
            return None
        bet = dict(row)
        target_bid = bet["id"]
        cursor.execute(
            """
            SELECT bi.*, 
                   COALESCE(m.player1_team, bm.team1_name, 'Хозяева') as team1_name,
                   COALESCE(m.player2_team, bm.team2_name, 'Гости') as team2_name,
                   COALESCE(m.round_number, bm.tour, 1) as tour,
                   m.status as match_status,
                   m.player1_score,
                   m.player2_score,
                   m.ht_score1,
                   m.ht_score2,
                   m.live_minute,
                   mkt.market_name,
                   ms.selection_name
            FROM bet_items bi
            LEFT JOIN matches m ON bi.match_id = m.id
            LEFT JOIN bet_markets bm ON bi.match_id = bm.match_id
            LEFT JOIN markets mkt ON bi.market_id = mkt.id
            LEFT JOIN market_selections ms ON bi.selection_id = ms.id
            WHERE bi.bet_id = ?
            """,
            (target_bid,)
        )
        bet["items"] = [dict(item) for item in cursor.fetchall()]
        return bet


def get_user_bets(user_id: int, status: str | None = None, limit: int = 20, offset: int = 0) -> list[dict]:
    """Fetch user's prediction slips with rich nested legs and match status."""
    with transaction() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM user_bets WHERE user_id = ?"
        params = [user_id]
        if status and status != "all":
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.append(limit)
        params.append(offset)

        cursor.execute(query, params)
        bets = [dict(r) for r in cursor.fetchall()]

        for b in bets:
            cursor.execute(
                """
                SELECT bi.*, 
                       COALESCE(m.player1_team, bm.team1_name, 'Хозяева') as team1_name,
                       COALESCE(m.player2_team, bm.team2_name, 'Гости') as team2_name,
                       COALESCE(m.round_number, bm.tour, 1) as tour,
                       m.status as match_status,
                       m.player1_score,
                       m.player2_score,
                       m.ht_score1,
                       m.ht_score2,
                       m.live_minute,
                       mkt.market_name,
                       ms.selection_name
                FROM bet_items bi
                LEFT JOIN matches m ON bi.match_id = m.id
                LEFT JOIN bet_markets bm ON bi.match_id = bm.match_id
                LEFT JOIN markets mkt ON bi.market_id = mkt.id
                LEFT JOIN market_selections ms ON bi.selection_id = ms.id
                WHERE bi.bet_id = ?
                """,
                (b["id"],)
            )
            b["items"] = [dict(item) for item in cursor.fetchall()]

        return bets


# ─── Phase 5: Betting Audit Log ──────────────────────────────────────────────

def log_betting_audit(
    actor_id: int,
    action: str,
    entity_type: str,
    entity_id: int,
    old_value: "dict | str | None" = None,
    new_value: "dict | str | None" = None,
    division_id: "int | None" = None,
    season_id: "int | None" = None,
) -> int:
    """
    Write a betting audit log entry to bet_audit_log.
    Returns the new log entry ID.
    """
    import json as _json
    old_v = _json.dumps(old_value, ensure_ascii=False) if old_value is not None else None
    new_v = _json.dumps(new_value, ensure_ascii=False) if new_value is not None else None
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bet_audit_log (actor_id, action, entity_type, entity_id, old_value, new_value, division_id, season_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (actor_id, action, entity_type, entity_id, old_v, new_v, division_id, season_id),
        )
        return cursor.lastrowid


write_bet_audit_log = log_betting_audit


# Market lifecycle valid transitions (Phase 5)
# Market lifecycle valid transitions — aligned with DB CHECK constraint:
# markets.status IN ('open','suspended','closed','settled','voided')
_MARKET_TRANSITIONS: dict[str, set[str]] = {
    "open":      {"suspended", "closed"},
    "suspended": {"open", "closed"},
    "closed":    {"settled", "voided"},
    "settled":   set(),   # terminal state
    "voided":    set(),   # terminal state
}


def transition_market_status(market_id: int, new_status: str, actor_id: int) -> dict:
    """
    Atomically transition a market to a new lifecycle status.
    Enforces the state machine: open→suspended→open→closed→settled/voided.
    Status values must match DB CHECK: open, suspended, closed, settled, voided.
    Returns dict with id, old_status, new_status.
    Raises ValueError for forbidden transitions.
    """
    allowed_statuses = {"open", "suspended", "closed", "settled", "voided"}
    if new_status not in allowed_statuses:
        raise ValueError(f"Invalid market status: '{new_status}'")

    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, status, match_id FROM markets WHERE id = ?", (market_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Market #{market_id} not found.")

        current_status = row["status"]
        allowed_next = _MARKET_TRANSITIONS.get(current_status, set())

        if new_status not in allowed_next:
            raise ValueError(
                f"Forbidden market transition: {current_status!r} → {new_status!r} for market #{market_id}."
            )

        cursor.execute(
            "UPDATE markets SET status = ? WHERE id = ?",
            (new_status, market_id),
        )

        # Fetch division_id via match
        division_id = None
        cursor.execute("SELECT division_id FROM matches WHERE id = ?", (row["match_id"],))
        m = cursor.fetchone()
        if m:
            division_id = m["division_id"]

    # Log outside of above transaction to avoid nested transaction issues with log_betting_audit
    log_betting_audit(
        actor_id=actor_id,
        action=f"market_{new_status}",
        entity_type="market",
        entity_id=market_id,
        old_value={"status": current_status},
        new_value={"status": new_status},
        division_id=division_id,
    )

    return {"id": market_id, "old_status": current_status, "new_status": new_status}


def update_selection_odds(selection_id: int, new_odd: float, actor_id: int) -> dict:
    """
    Update a market selection's odds, increment odds_version, store previous_odds, and log audit.
    Returns dict with selection_id, old_odd, new_odd.
    """
    if new_odd <= 1.00:
        raise ValueError("Odds must be greater than 1.00.")

    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, odds_value, market_id FROM market_selections WHERE id = ?",
            (selection_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Selection #{selection_id} not found.")

        old_odd = float(row["odds_value"])
        new_odd = round(float(new_odd), 2)

        cursor.execute(
            """
            UPDATE market_selections
            SET previous_odds = odds_value,
                odds_value = ?,
                odds_version = odds_version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_odd, selection_id),
        )

        # Also record in odds_history if table exists
        try:
            cursor.execute(
                """
                INSERT INTO odds_history (selection_id, old_odds, new_odds, changed_by)
                VALUES (?, ?, ?, ?)
                """,
                (selection_id, old_odd, new_odd, actor_id),
            )
        except Exception:
            pass  # odds_history may have different schema — skip silently

    log_betting_audit(
        actor_id=actor_id,
        action="odds_changed",
        entity_type="selection",
        entity_id=selection_id,
        old_value={"odds_value": old_odd},
        new_value={"odds_value": new_odd},
    )

    return {"selection_id": selection_id, "old_odd": old_odd, "new_odd": new_odd}


def void_user_bet(bet_id: int, actor_id: int) -> dict:
    """
    Void a user bet and refund the stake.
    Only pending/won/lost bets that have not already been voided can be voided.
    Returns dict with bet_id, refunded_amount, user_id.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM user_bets WHERE id = ?", (bet_id,))
        bet = cursor.fetchone()
        if not bet:
            raise ValueError(f"Bet #{bet_id} not found.")

        if bet["status"] != "pending":
            raise ValueError(f"Bet #{bet_id} cannot be voided because it is already {bet['status']}.")

        stake = bet["amount"]
        user_id = bet["user_id"]

        # Void the bet and all its items
        cursor.execute(
            "UPDATE user_bets SET status = 'refunded', actual_payout = ?, settled_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending' AND settled_at IS NULL",
            (stake, bet_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Bet #{bet_id} could not be voided (already settled or refunded).")

        cursor.execute(
            "UPDATE bet_items SET status = 'refunded' WHERE bet_id = ? AND status = 'pending'",
            (bet_id,),
        )

        # Refund stake
        get_or_create_wallet(user_id)
        cursor.execute(
            "UPDATE user_wallets SET balance = balance + ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (stake, user_id),
        )
        cursor.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (user_id,))
        bal_after = cursor.fetchone()["balance"]

        cursor.execute(
            """
            INSERT INTO coin_transactions (user_id, amount, transaction_type, reference_id, reference_type, balance_after)
            VALUES (?, ?, 'admin_refund', ?, 'bet', ?)
            """,
            (user_id, stake, bet_id, bal_after),
        )

    log_betting_audit(
        actor_id=actor_id,
        action="bet_voided",
        entity_type="bet",
        entity_id=bet_id,
        old_value={"status": bet["status"], "amount": stake},
        new_value={"status": "refunded", "refund": stake},
    )

    return {"bet_id": bet_id, "user_id": user_id, "refunded_amount": stake}


def get_betting_audit_log(
    limit: int = 50,
    offset: int = 0,
    actor_id: "int | None" = None,
    entity_type: "str | None" = None,
    division_id: "int | None" = None,
) -> list[dict]:
    """Fetch betting audit log entries with optional filters."""
    with transaction() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM bet_audit_log WHERE 1=1"
        params: list = []
        if actor_id is not None:
            query += " AND actor_id = ?"
            params.append(actor_id)
        if entity_type is not None:
            query += " AND entity_type = ?"
            params.append(entity_type)
        if division_id is not None:
            query += " AND division_id = ?"
            params.append(division_id)
        query += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]


def get_tournament_standings(division_id: int | None = None, season_id: int | None = None) -> list[dict]:
    """Alias for get_standings with division/season scoping."""
    return get_standings(division_id=division_id, season_id=season_id)


def get_tournament_results(limit: int = 30, division_id: int | None = None, season_id: int | None = None) -> list[dict]:
    """Retrieve finished matches archive filtered by division and season."""
    with transaction() as conn:
        cursor = conn.cursor()
        query = """
            SELECT m.*, 
                   COALESCE(m.player1_team, 'Хозяева') as team1_name,
                   COALESCE(m.player2_team, 'Гости') as team2_name
            FROM matches m
            WHERE m.status IN ('confirmed', 'completed', 'finished')
              AND m.player1_score IS NOT NULL AND m.player2_score IS NOT NULL
        """
        params = []
        if division_id is not None:
            query += " AND m.division_id = ?"
            params.append(division_id)
        if season_id is not None:
            query += " AND (m.season_id = ? OR m.season_id IS NULL)"
            params.append(season_id)
        query += " ORDER BY m.round_number DESC, m.id DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]


def settle_match_bets(match_id: int, score1: int, score2: int) -> list[dict]:
    """
    Settle all pending bets related to a finished match.
    Delegates to full-featured services.settlement_engine.
    """
    from services.settlement_engine import settle_match_predictions
    return settle_match_predictions(match_id, score1, score2)


def settle_all_pending_finished_matches() -> list[dict]:
    """
    Self-healing trigger: Scan all pending bet items for matches that are already
    completed/confirmed in the database and settle them immediately.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT m.id, m.player1_score, m.player2_score
            FROM bet_items bi
            JOIN matches m ON bi.match_id = m.id
            WHERE bi.status = 'pending' 
              AND m.status IN ('confirmed', 'completed')
              AND m.player1_score IS NOT NULL 
              AND m.player2_score IS NOT NULL
        """)
        matches = cursor.fetchall()
        all_payouts = []
        for m in matches:
            res = settle_match_bets(m["id"], m["player1_score"], m["player2_score"])
            all_payouts.extend(res)
        return all_payouts


# ═════════════════════════════════════════════════════════════════════════════
# 🎮 LOGOVO.BET — GAMIFICATION, PROGRESSION, QUESTS & SOCIAL REPOSITORY
# ═════════════════════════════════════════════════════════════════════════════

def seed_gamification_catalog(cursor) -> None:
    """Seed standard 20+ achievements catalog (quests removed in v2.0)."""
    achievements = [
        ("ACH_FIRST_BET", "🐺 Первый шаг", "Сделать свой первый прогноз", "general", "common", 100, 250, "🎯"),
        ("ACH_FIRST_WIN", "🏆 Первая кровь", "Выиграть свой первый прогноз", "general", "common", 150, 300, "⚔️"),
        ("ACH_STREAK_3", "🔥 В ударе", "Оформить серию из 3 побед подряд", "streaks", "common", 250, 500, "🔥"),
        ("ACH_STREAK_5", "🎯 Снайпер", "Оформить серию из 5 побед подряд", "streaks", "rare", 600, 1200, "🎯"),
        ("ACH_STREAK_10", "👑 Непобедимый", "Оформить серию из 10 побед подряд", "streaks", "legendary", 2500, 5000, "👑"),
        ("ACH_EXPRESS_3", "🚂 Экспресс-старт", "Собрать экспресс из 3+ событий", "parlays", "common", 200, 400, "🚂"),
        ("ACH_EXPRESS_ODD_5", "💥 Множитель x5", "Выиграть экспресс с коэффициентом 5.0+", "parlays", "rare", 500, 1000, "💥"),
        ("ACH_EXPRESS_ODD_15", "🚀 Ракета x15", "Выиграть экспресс с коэффициентом 15.0+", "parlays", "epic", 1500, 3000, "🚀"),
        ("ACH_EXPRESS_ODD_50", "🌌 Космос x50", "Выиграть экспресс с коэффициентом 50.0+", "parlays", "legendary", 5000, 10000, "🌌"),
        ("ACH_UNDERDOG", "🐺 Гроза Фаворитов", "Выиграть ординар с коэффициентом 3.5+", "odds", "rare", 400, 800, "⚡"),
        ("ACH_TOTAL_10_BETS", "📊 Любитель", "Сделать 10 любых прогнозов", "volume", "common", 300, 500, "📊"),
        ("ACH_TOTAL_50_BETS", "🏅 Регуляр", "Сделать 50 любых прогнозов", "volume", "rare", 1000, 2000, "🏅"),
        ("ACH_TOTAL_100_BETS", "💯 Центурион", "Сделать 100 любых прогнозов", "volume", "epic", 2500, 5000, "💯"),
        ("ACH_COIN_MILLIONAIRE", "💰 Мешок Монет", "Накопить 25 000 🪙 на балансе", "wealth", "epic", 2000, 2500, "💰"),
        ("ACH_COIN_TYCOON", "🏦 Олигарх Логова", "Накопить 100 000 🪙 на балансе", "wealth", "legendary", 5000, 10000, "🏦"),
        ("ACH_LOGIN_3", "📅 Разминка", "Заходить в игру 3 дня подряд", "loyalty", "common", 200, 400, "📅"),
        ("ACH_LOGIN_7", "🔥 Неделя в строю", "Заходить в игру 7 дней подряд", "loyalty", "rare", 800, 1500, "🔥"),
        ("ACH_LOGIN_30", "🐺 Вожак Стаи", "Заходить в игру 30 дней подряд", "loyalty", "legendary", 4000, 10000, "🐺"),
        ("ACH_TB_SPECIALIST", "⚽ Голевой Маньяк", "Выиграть 5 прогнозов на Тотал Больше 2.5", "markets", "rare", 500, 1000, "⚽"),
        ("ACH_BTTS_MASTER", "🤝 Обе Забьют", "Выиграть 5 прогнозов на Обе Забьют", "markets", "rare", 500, 1000, "🤝"),
        # Phase 10 Competitive & Seasonal Achievements
        ("ACH_10_WINS", "🎯 10 Побед", "Выиграть 10 любых прогнозов", "volume", "common", 400, 800, "🎯"),
        ("ACH_50_WINS", "🏆 50 Побед", "Выиграть 50 любых прогнозов", "volume", "rare", 1500, 3000, "🏆"),
        ("ACH_100_WINS", "👑 100 Побед", "Выиграть 100 любых прогнозов", "volume", "legendary", 3500, 7000, "👑"),
        ("ACH_POSITIVE_ROI", "📈 В Плюсе", "Достичь положительного ROI при 10+ прогнозах", "skill", "rare", 800, 1500, "📈"),
        ("ACH_VALUE_HUNTER", "💎 Охотник за Валуем", "Выиграть 5 валуйных прогнозов с перевесом", "skill", "epic", 1200, 2500, "💎"),
        ("ACH_HOT_STREAK", "🔥 Горячая Серия", "Оформить серию из 5 побед подряд", "streaks", "rare", 750, 1500, "🔥"),
        ("ACH_NO_LOSS_STREAK", "🛡 Без Поражений", "Оформить серию из 7 побед подряд без поражений", "streaks", "epic", 1800, 3500, "🛡"),
        ("ACH_SEASON_TOP_10", "🌟 Топ-10 Сезона", "Завершить сезон в топ-10 своего дивизиона", "seasonal", "epic", 2000, 4000, "🌟"),
        ("ACH_SEASON_CHAMPION", "🥇 Чемпион Сезона", "Занять 1-е место в дивизионе по итогам сезона", "seasonal", "legendary", 5000, 10000, "🥇"),
        ("ACH_PROMOTED", "🚀 Повышение в Классе", "Заработать повышение в высший дивизион", "seasonal", "rare", 1000, 2000, "🚀")
    ]
    for ach in achievements:
        cursor.execute("""
            INSERT INTO achievements_catalog (id, name, description, category, rarity, reward_xp, reward_coins, badge_icon)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                category = excluded.category,
                rarity = excluded.rarity,
                reward_xp = excluded.reward_xp,
                reward_coins = excluded.reward_coins,
                badge_icon = excluded.badge_icon
        """, ach)

    # Phase 10: Seed default season rewards
    default_rewards = [
        ("REW_CHAMPION", None, None, "Чемпион Дивизиона", "coins", 10000, "BADGE_CHAMPION", "Чемпион Дивизиона 👑", "CHAMPION"),
        ("REW_TOP_3", None, None, "Призер Сезона (Топ-3)", "coins", 5000, "BADGE_TOP_3", "Призер Сезона 🥈", "TOP_3"),
        ("REW_TOP_10", None, None, "Элита Дивизиона (Топ-10)", "coins", 2500, "BADGE_TOP_10", "Топ-10 🌟", "TOP_10"),
        ("REW_PROMOTION", None, None, "Награда за Повышение", "coins", 3000, "BADGE_PROMOTED", "Повышен в классе 🚀", "PROMOTION"),
        ("REW_PARTICIPATION", None, None, "Участник Сезона", "xp", 500, None, None, "PARTICIPATION"),
    ]
    for rew in default_rewards:
        cursor.execute("""
            INSERT INTO season_rewards_catalog (id, season_id, division_id, name, reward_type, amount, badge_id, title, criteria)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                reward_type = excluded.reward_type,
                amount = excluded.amount,
                badge_id = excluded.badge_id,
                title = excluded.title,
                criteria = excluded.criteria
        """, rew)

    # Phase 10: Seed default division rules
    for div_id in range(1, 6):
        cursor.execute("""
            INSERT OR IGNORE INTO season_rules_config (season_id, division_id, promotion_slots, relegation_slots, min_bets_qualification, min_matches_qualification)
            VALUES (1, ?, 3, 3, 5, 3)
        """, (div_id,))


def get_or_create_progression(user_id: int) -> dict:
    """Fetch user's XP, level, streak and cosmetic profile or create a fresh one."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM user_progression WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)

        cursor.execute("""
            INSERT INTO user_progression (user_id, level, current_xp, total_xp_earned, current_streak, best_streak, streak_shields, equipped_frame, equipped_title)
            VALUES (?, 1, 0, 0, 1, 1, 1, 'default', 'Новичок')
        """, (user_id,))
        cursor.execute("SELECT * FROM user_progression WHERE user_id = ?", (user_id,))
        return dict(cursor.fetchone())


def add_user_xp(user_id: int, xp_amount: int) -> dict:
    """
    Safely credit XP to user, calculate level ups, and award coin milestones.
    Returns {level, current_xp, total_xp, leveled_up, reward_coins, new_title}.
    """
    if xp_amount <= 0:
        p = get_or_create_progression(user_id)
        return {"level": p["level"], "current_xp": p["current_xp"], "total_xp": p["total_xp_earned"], "leveled_up": False, "reward_coins": 0}

    import math
    with transaction() as conn:
        cursor = conn.cursor()
        get_or_create_wallet(user_id)
        p = get_or_create_progression(user_id)
        cur_level = p["level"]
        new_total_xp = p["total_xp_earned"] + xp_amount
        
        # Level formula: Level = 1 + floor(sqrt(total_xp / 150))
        calculated_level = max(1, 1 + int(math.sqrt(new_total_xp / 150)))
        
        leveled_up = calculated_level > cur_level
        reward_coins = 0
        
        title = p["equipped_title"]
        if calculated_level >= 50:
            title = "Легенда Логова 👑"
        elif calculated_level >= 35:
            title = "Элитный Аналитик ⚡"
        elif calculated_level >= 20:
            title = "Мастер Экспрессов 🚂"
        elif calculated_level >= 10:
            title = "Опытный Каппер 🎯"
        elif calculated_level >= 5:
            title = "Тактик 🐾"

        if leveled_up:
            reward_coins = (calculated_level - cur_level) * 500
            cursor.execute("""
                UPDATE user_wallets 
                SET balance = balance + ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (reward_coins, user_id))
            cursor.execute("""
                INSERT INTO coin_transactions (user_id, amount, transaction_type, reference_id)
                VALUES (?, ?, 'level_up_reward', ?)
            """, (user_id, reward_coins, calculated_level))

        # XP required for next level
        xp_for_current_lvl = int(((calculated_level - 1) ** 2) * 150)
        xp_for_next_lvl = int((calculated_level ** 2) * 150)
        lvl_progress_xp = new_total_xp - xp_for_current_lvl

        cursor.execute("""
            UPDATE user_progression
            SET level = ?, current_xp = ?, total_xp_earned = ?, equipped_title = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (calculated_level, lvl_progress_xp, new_total_xp, title, user_id))

        return {
            "level": calculated_level,
            "current_xp": lvl_progress_xp,
            "next_level_xp": xp_for_next_lvl - xp_for_current_lvl,
            "total_xp": new_total_xp,
            "leveled_up": leveled_up,
            "reward_coins": reward_coins,
            "title": title
        }


def check_and_update_login_streak(user_id: int) -> dict:
    """
    Evaluate 7-day login streak for user.
    Handles streak increment, resets, and streak shield protection.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        p = get_or_create_progression(user_id)
        today_str = datetime.date.today().isoformat()
        last_active = p.get("last_active_date")

        if last_active == today_str:
            return {
                "streak": p["current_streak"],
                "best_streak": p["best_streak"],
                "shield_used": False,
                "streak_shield_count": p["streak_shields"]
            }

        cur_streak = p["current_streak"]
        shield_used = False
        shields = p["streak_shields"]

        if last_active:
            try:
                last_dt = datetime.date.fromisoformat(last_active)
                delta_days = (datetime.date.today() - last_dt).days
                if delta_days == 1:
                    cur_streak += 1
                elif delta_days == 2 and shields > 0:
                    # Shield consumed to save streak
                    shields -= 1
                    shield_used = True
                    cur_streak += 1
                else:
                    cur_streak = 1
            except Exception:
                cur_streak = 1
        else:
            cur_streak = 1

        best = max(cur_streak, p["best_streak"])
        cursor.execute("""
            UPDATE user_progression
            SET current_streak = ?, best_streak = ?, last_active_date = ?, streak_shields = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (cur_streak, best, today_str, shields, user_id))

        # Trigger login achievements
        if cur_streak >= 3:
            unlock_achievement(user_id, "ACH_LOGIN_3")
        if cur_streak >= 7:
            unlock_achievement(user_id, "ACH_LOGIN_7")
        if cur_streak >= 30:
            unlock_achievement(user_id, "ACH_LOGIN_30")

        return {
            "streak": cur_streak,
            "best_streak": best,
            "shield_used": shield_used,
            "streak_shield_count": shields
        }




def unlock_achievement(user_id: int, achievement_id: str) -> bool:
    """Unlock an achievement for user if not already unlocked."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM user_achievements WHERE user_id = ? AND achievement_id = ?", (user_id, achievement_id))
        if cursor.fetchone():
            return False

        cursor.execute("""
            INSERT OR IGNORE INTO user_achievements (user_id, achievement_id, is_claimed, unlocked_at)
            VALUES (?, ?, 0, CURRENT_TIMESTAMP)
        """, (user_id, achievement_id))
        return True


def get_user_achievements(user_id: int) -> list[dict]:
    """Return list of all catalog achievements with user unlocked status."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ac.*, 
                   CASE WHEN ua.id IS NOT NULL THEN 1 ELSE 0 END as is_unlocked,
                   COALESCE(ua.is_claimed, 0) as is_claimed,
                   ua.unlocked_at
            FROM achievements_catalog ac
            LEFT JOIN user_achievements ua ON ac.id = ua.achievement_id AND ua.user_id = ?
            ORDER BY is_unlocked DESC, ac.reward_xp DESC
        """, (user_id,))
        return [dict(r) for r in cursor.fetchall()]


def claim_achievement_reward(user_id: int, achievement_id: str) -> tuple[bool, str, dict]:
    """Claim reward for an unlocked achievement."""
    from config import is_global_lockdown_enabled
    if is_global_lockdown_enabled():
        from handlers.base import is_global_admin
        if not is_global_admin(user_id):
            return False, "Logovo.bet временно закрыт для пользователей.", {}

    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ua.id, ua.is_claimed, ac.reward_xp, ac.reward_coins, ac.name
            FROM user_achievements ua
            JOIN achievements_catalog ac ON ua.achievement_id = ac.id
            WHERE ua.user_id = ? AND ua.achievement_id = ?
        """, (user_id, achievement_id))
        row = cursor.fetchone()

        if not row:
            return False, "Достижение ещё не разблокировано.", {}
        cursor.execute("""
            UPDATE user_achievements
            SET is_claimed = 1
            WHERE user_id = ? AND achievement_id = ? AND is_claimed = 0
        """, (user_id, achievement_id))
        if cursor.rowcount == 0:
            return False, "Награда за достижение уже получена.", {}

        xp_res = add_user_xp(user_id, row["reward_xp"])
        add_coins(user_id, row["reward_coins"], tx_type="achievement_reward")

        return True, f"🏆 Достижение получено: +{row['reward_coins']} 🪙 и +{row['reward_xp']} XP!", {
            "coins": row["reward_coins"],
            "xp": row["reward_xp"],
            "progression": xp_res
        }


def evaluate_betting_achievements(user_id: int, bet_payload: dict | None = None) -> None:
    """Scan and trigger achievements on bet placement or win."""
    with transaction() as conn:
        cursor = conn.cursor()
        wallet = get_or_create_wallet(user_id)
        prog = get_or_create_progression(user_id)

        # Volume
        cnt = wallet["bets_count"]
        if cnt >= 1:
            unlock_achievement(user_id, "ACH_FIRST_BET")
        if cnt >= 10:
            unlock_achievement(user_id, "ACH_TOTAL_10_BETS")
        if cnt >= 50:
            unlock_achievement(user_id, "ACH_TOTAL_50_BETS")
        if cnt >= 100:
            unlock_achievement(user_id, "ACH_TOTAL_100_BETS")

        # Wealth
        bal = wallet["balance"]
        if bal >= 25000:
            unlock_achievement(user_id, "ACH_COIN_MILLIONAIRE")
        if bal >= 100000:
            unlock_achievement(user_id, "ACH_COIN_TYCOON")

        # Wins & Streaks
        won_cnt = wallet["bets_won"]
        if won_cnt >= 1:
            unlock_achievement(user_id, "ACH_FIRST_WIN")

        # Parlay / Odd achievements from payload
        if bet_payload:
            b_type = bet_payload.get("bet_type")
            odd = float(bet_payload.get("total_odd", 1.0))
            if b_type == "express":
                unlock_achievement(user_id, "ACH_EXPRESS_3")
                if odd >= 5.0:
                    unlock_achievement(user_id, "ACH_EXPRESS_ODD_5")
                if odd >= 15.0:
                    unlock_achievement(user_id, "ACH_EXPRESS_ODD_15")
                if odd >= 50.0:
                    unlock_achievement(user_id, "ACH_EXPRESS_ODD_50")
            elif b_type == "single" and odd >= 3.5:
                unlock_achievement(user_id, "ACH_UNDERDOG")




def get_public_gamer_profile(user_id: int) -> dict:
    """Assemble public esports gamer card with radar stats, badges and achievements."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,))
        user_row = cursor.fetchone()
        
        wallet = get_or_create_wallet(user_id)
        prog = get_or_create_progression(user_id)
        achievements = get_user_achievements(user_id)
        unlocked_ach = [a for a in achievements if a["is_unlocked"]]

        win_rate = round((wallet["bets_won"] / max(1, wallet["bets_count"])) * 100, 1)

        return {
            "user_id": user_id,
            "username": user_row["username"] if user_row else f"Каппер #{user_id}",
            "team_name": user_row["team_name"] if user_row else "Свободный игрок",
            "level": prog["level"],
            "current_xp": prog["current_xp"],
            "total_xp": prog["total_xp_earned"],
            "title": prog["equipped_title"],
            "frame": prog["equipped_frame"],
            "streak": prog["current_streak"],
            "best_streak": prog["best_streak"],
            "bets_count": wallet["bets_count"],
            "bets_won": wallet["bets_won"],
            "win_rate": win_rate,
            "unlocked_achievements_count": len(unlocked_ach),
            "achievements": unlocked_ach[:6]
        }


# ─── Phase 10: Competitive Profile, Season Stats & Career History ───────────

def get_user_favorite_stats(user_id: int) -> dict:
    """Analyze settled user bets to determine favorite markets, teams, prediction accuracy, and value hit rate."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT bi.market_id, bi.selection_id, bi.status, bi.odd,
                   m.market_key, m.match_id,
                   mat.player1_team, mat.player2_team
            FROM bet_items bi
            JOIN user_bets ub ON bi.bet_id = ub.id
            LEFT JOIN markets m ON bi.market_id = m.id
            LEFT JOIN matches mat ON bi.match_id = mat.id
            WHERE ub.user_id = ? AND bi.status IN ('won', 'lost')
        """, (user_id,))
        items = cursor.fetchall()

        if not items:
            return {
                "favorite_markets": [],
                "favorite_teams": [],
                "prediction_accuracy": 0.0,
                "value_hit_rate": 0.0
            }

        market_counts = {}
        team_counts = {}
        won_count = 0
        value_bets_count = 0
        value_won_count = 0

        for r in items:
            m_key = r["market_key"] or "1x2"
            market_counts[m_key] = market_counts.get(m_key, 0) + 1

            t1 = r["player1_team"]
            t2 = r["player2_team"]
            if t1:
                team_counts[t1] = team_counts.get(t1, 0) + 1
            if t2:
                team_counts[t2] = team_counts.get(t2, 0) + 1

            is_win = (r["status"] == "won")
            if is_win:
                won_count += 1

            odd = float(r["odd"] or 1.0)
            if odd >= 2.0:
                value_bets_count += 1
                if is_win:
                    value_won_count += 1

        sorted_markets = sorted(market_counts.keys(), key=lambda k: market_counts[k], reverse=True)[:3]
        sorted_teams = sorted(team_counts.keys(), key=lambda k: team_counts[k], reverse=True)[:3]
        accuracy = round((won_count / len(items)) * 100, 1)
        val_hit_rate = round((value_won_count / max(1, value_bets_count)) * 100, 1) if value_bets_count > 0 else 0.0

        return {
            "favorite_markets": sorted_markets,
            "favorite_teams": sorted_teams,
            "prediction_accuracy": accuracy,
            "value_hit_rate": val_hit_rate
        }


def get_or_create_season_stats(user_id: int, season_id: int | None = None, division_id: int | None = None) -> dict:
    """Fetch or initialize player's season-scoped stats record."""
    with transaction() as conn:
        cursor = conn.cursor()

        target_s_id = season_id
        if target_s_id is None:
            act = get_active_season()
            target_s_id = act["id"] if act else 1

        target_d_id = division_id
        if target_d_id is None:
            cursor.execute("SELECT division_id FROM users WHERE telegram_id = ?", (user_id,))
            u_row = cursor.fetchone()
            target_d_id = (u_row["division_id"] if u_row and u_row["division_id"] else 1)

        cursor.execute("""
            SELECT * FROM season_player_stats
            WHERE user_id = ? AND season_id = ? AND division_id = ?
        """, (user_id, target_s_id, target_d_id))
        row = cursor.fetchone()
        if row:
            return dict(row)

        cursor.execute("""
            INSERT OR IGNORE INTO season_player_stats (user_id, season_id, division_id, rating, confidence, season_points, status)
            VALUES (?, ?, ?, 1200.0, 350.0, 0.0, 'QUALIFYING')
        """, (user_id, target_s_id, target_d_id))

        cursor.execute("""
            SELECT * FROM season_player_stats
            WHERE user_id = ? AND season_id = ? AND division_id = ?
        """, (user_id, target_s_id, target_d_id))
        return dict(cursor.fetchone())


def update_season_player_stats(user_id: int, season_id: int, division_id: int, **kwargs) -> None:
    """Safely update dynamic season player stats fields."""
    if not kwargs:
        return
    with transaction() as conn:
        cursor = conn.cursor()
        # Ensure row exists
        get_or_create_season_stats(user_id, season_id, division_id)

        set_clauses = []
        params = []
        for k, v in kwargs.items():
            set_clauses.append(f"{k} = ?")
            params.append(v)
        set_clauses.append("updated_at = CURRENT_TIMESTAMP")

        params.extend([user_id, season_id, division_id])
        sql = f"UPDATE season_player_stats SET {', '.join(set_clauses)} WHERE user_id = ? AND season_id = ? AND division_id = ?"
        cursor.execute(sql, tuple(params))


def get_player_season_stats(user_id: int, season_id: int | None = None, division_id: int | None = None) -> dict:
    """Retrieve player season stats."""
    return get_or_create_season_stats(user_id, season_id, division_id)


def get_player_career_stats(user_id: int) -> dict:
    """Aggregate career-wide metrics across all completed and active seasons."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                COUNT(id) as total_bets,
                SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) as career_wins,
                SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) as career_losses,
                SUM(amount) as career_stake,
                SUM(CASE WHEN status = 'won' THEN actual_payout ELSE 0 END) as career_payout
            FROM user_bets
            WHERE user_id = ? AND status IN ('won', 'lost')
        """, (user_id,))
        b_row = cursor.fetchone()

        total_bets = b_row["total_bets"] or 0
        career_wins = b_row["career_wins"] or 0
        career_losses = b_row["career_losses"] or 0
        career_stake = b_row["career_stake"] or 0
        career_payout = b_row["career_payout"] or 0

        career_roi = round(((career_payout - career_stake) / max(1, career_stake)) * 100, 1) if career_stake > 0 else 0.0
        career_acc = round((career_wins / max(1, total_bets)) * 100, 1) if total_bets > 0 else 0.0

        cursor.execute("SELECT COUNT(DISTINCT season_id) as seasons_played FROM season_player_stats WHERE user_id = ?", (user_id,))
        s_played = cursor.fetchone()["seasons_played"] or 0

        cursor.execute("""
            SELECT
                SUM(CASE WHEN final_rank = 1 THEN 1 ELSE 0 END) as seasons_won,
                SUM(CASE WHEN promotion_status = 'PROMOTED' THEN 1 ELSE 0 END) as promotions,
                SUM(CASE WHEN promotion_status = 'RELEGATED' THEN 1 ELSE 0 END) as relegations,
                MIN(final_rank) as best_finish,
                MAX(best_streak) as longest_streak
            FROM season_snapshots
            WHERE user_id = ?
        """, (user_id,))
        snap_row = cursor.fetchone()

        prog = get_or_create_progression(user_id)
        ach = get_user_achievements(user_id)

        longest = max(prog.get("best_streak", 0), snap_row["longest_streak"] or 0)

        return {
            "user_id": user_id,
            "career_bets": total_bets,
            "career_wins": career_wins,
            "career_losses": career_losses,
            "career_roi": career_roi,
            "career_accuracy": career_acc,
            "seasons_played": max(1, s_played),
            "seasons_won": snap_row["seasons_won"] or 0,
            "promotions": snap_row["promotions"] or 0,
            "relegations": snap_row["relegations"] or 0,
            "achievements_count": sum(1 for a in ach if a["is_unlocked"]),
            "best_finish": snap_row["best_finish"] or 1,
            "longest_streak": longest
        }


def get_public_player_profile(user_id: int, season_id: int | None = None, division_id: int | None = None) -> dict:
    """
    Assemble strictly PUBLIC player profile 2.0.
    CRITICAL: Never reveals wallet balance, coin totals, or private betting transactions.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,))
        user_row = cursor.fetchone()

        prog = get_or_create_progression(user_id)
        s_stats = get_or_create_season_stats(user_id, season_id, division_id)
        fav = get_user_favorite_stats(user_id)
        achievements = get_user_achievements(user_id)
        unlocked_ach = [a for a in achievements if a["is_unlocked"]]

        # Calculate rank dynamically if not frozen
        cursor.execute("""
            SELECT COUNT(*) + 1 as rank
            FROM season_player_stats
            WHERE season_id = ? AND division_id = ? AND (rating > ? OR (rating = ? AND season_points > ?))
        """, (s_stats["season_id"], s_stats["division_id"], s_stats["rating"], s_stats["rating"], s_stats["season_points"]))
        curr_rank = cursor.fetchone()["rank"]

        return {
            "user_id": user_id,
            "username": user_row["username"] if user_row and user_row["username"] else f"Каппер #{user_id}",
            "team_name": user_row["team_name"] if user_row and user_row["team_name"] else "Свободный игрок",
            "division_id": s_stats["division_id"],
            "season_id": s_stats["season_id"],
            "rating": round(float(s_stats["rating"]), 1),
            "rank": curr_rank,
            "level": prog["level"],
            "current_xp": prog["current_xp"],
            "total_xp": prog["total_xp_earned"],
            "experience": prog["total_xp_earned"],
            "tier": ("MASTER" if s_stats["rating"] >= 1600 else "ELITE" if s_stats["rating"] >= 1450 else "PRO" if s_stats["rating"] >= 1300 else "RISING" if s_stats["rating"] >= 1150 else "ROOKIE"),
            "title": prog["equipped_title"],
            "frame": prog["equipped_frame"],
            "season_points": round(float(s_stats["season_points"]), 1),
            "total_bets": s_stats["total_bets"],
            "settled_bets": s_stats["settled_bets"],
            "wins": s_stats["wins"],
            "losses": s_stats["losses"],
            "win_rate": round(float(s_stats["win_rate"]), 1),
            "roi": round(float(s_stats["roi"]), 1),
            "best_streak": s_stats["best_streak"],
            "current_streak": s_stats["current_streak"],
            "favorite_markets": fav["favorite_markets"],
            "favorite_teams": fav["favorite_teams"],
            "prediction_accuracy": fav["prediction_accuracy"],
            "value_hit_rate": fav["value_hit_rate"],
            "status": s_stats["status"],
            "unlocked_achievements_count": len(unlocked_ach),
            "achievements": unlocked_ach[:6]
        }


def get_private_player_profile(user_id: int, season_id: int | None = None, division_id: int | None = None) -> dict:
    """
    Assemble PRIVATE player profile for authenticated user only.
    Contains wallet, stake volume, and financial metrics.
    """
    pub = get_public_player_profile(user_id, season_id, division_id)
    with transaction() as conn:
        cursor = conn.cursor()
        wallet = get_or_create_wallet(user_id)
        s_stats = get_player_season_stats(user_id, season_id, division_id)
        career = get_player_career_stats(user_id)

        pub.update({
            "balance": wallet["balance"],
            "wallet": wallet,
            "total_stake": s_stats["total_stake"],
            "total_payout": s_stats["total_payout"],
            "career": career
        })
        return pub


def create_season_snapshot(
    season_id: int,
    division_id: int,
    user_id: int,
    final_rank: int,
    final_rating: float,
    season_points: float,
    wins: int,
    losses: int,
    voids: int,
    settled_bets: int,
    win_rate: float,
    roi: float,
    total_stake: int,
    total_payout: int,
    best_streak: int,
    promotion_status: str,
    rewards_json: str | None = None
) -> int:
    """Create an immutable historical snapshot for a player upon season completion."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO season_snapshots (
                season_id, division_id, user_id, final_rank, final_rating, season_points,
                wins, losses, voids, settled_bets, win_rate, roi, total_stake, total_payout,
                best_streak, promotion_status, rewards_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(season_id, division_id, user_id) DO NOTHING
        """, (
            season_id, division_id, user_id, final_rank, final_rating, season_points,
            wins, losses, voids, settled_bets, win_rate, roi, total_stake, total_payout,
            best_streak, promotion_status, rewards_json
        ))
        return cursor.lastrowid or 0


def get_season_snapshots(season_id: int, division_id: int | None = None) -> list[dict]:
    """Retrieve immutable snapshots for a finished season."""
    with transaction() as conn:
        cursor = conn.cursor()
        if division_id is not None:
            cursor.execute("""
                SELECT ss.*, u.username, u.team_name
                FROM season_snapshots ss
                LEFT JOIN users u ON ss.user_id = u.telegram_id
                WHERE ss.season_id = ? AND ss.division_id = ?
                ORDER BY ss.final_rank ASC
            """, (season_id, division_id))
        else:
            cursor.execute("""
                SELECT ss.*, u.username, u.team_name
                FROM season_snapshots ss
                LEFT JOIN users u ON ss.user_id = u.telegram_id
                WHERE ss.season_id = ?
                ORDER BY ss.division_id ASC, ss.final_rank ASC
            """, (season_id,))
        return [dict(r) for r in cursor.fetchall()]


def get_season_rules(season_id: int, division_id: int) -> dict:
    """Retrieve promotion/relegation and qualification rules for division in season."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM season_rules_config
            WHERE season_id = ? AND division_id = ?
        """, (season_id, division_id))
        row = cursor.fetchone()
        if row:
            return dict(row)
        # Default fallback
        return {
            "season_id": season_id,
            "division_id": division_id,
            "promotion_slots": 3,
            "relegation_slots": 3,
            "min_bets_qualification": 5,
            "min_matches_qualification": 3
        }


def set_season_rules(
    season_id: int,
    division_id: int,
    promotion_slots: int = 3,
    relegation_slots: int = 3,
    min_bets_qualification: int = 5,
    min_matches_qualification: int = 3
) -> None:
    """Configure or update promotion, relegation, and qualification thresholds."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO season_rules_config (
                season_id, division_id, promotion_slots, relegation_slots,
                min_bets_qualification, min_matches_qualification
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(season_id, division_id) DO UPDATE SET
                promotion_slots = excluded.promotion_slots,
                relegation_slots = excluded.relegation_slots,
                min_bets_qualification = excluded.min_bets_qualification,
                min_matches_qualification = excluded.min_matches_qualification
        """, (season_id, division_id, promotion_slots, relegation_slots, min_bets_qualification, min_matches_qualification))


def record_season_reward_in_ledger(
    season_id: int,
    division_id: int,
    user_id: int,
    reward_id: str,
    reward_type: str,
    coins_awarded: int = 0,
    xp_awarded: int = 0,
    badge_awarded: str | None = None
) -> bool:
    """
    Idempotently record a season reward.
    Returns True if newly recorded, False if already recorded.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO season_reward_ledger (
                season_id, division_id, user_id, reward_id, reward_type,
                coins_awarded, xp_awarded, badge_awarded, status, distributed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'DISTRIBUTED', CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, season_id, reward_id) DO NOTHING
        """, (season_id, division_id, user_id, reward_id, reward_type, coins_awarded, xp_awarded, badge_awarded))
        return cursor.rowcount > 0


def get_user_season_rewards(user_id: int, season_id: int | None = None) -> list[dict]:
    """List claimed or distributed season rewards for a user."""
    with transaction() as conn:
        cursor = conn.cursor()
        if season_id is not None:
            cursor.execute("""
                SELECT * FROM season_reward_ledger
                WHERE user_id = ? AND season_id = ?
                ORDER BY created_at DESC
            """, (user_id, season_id))
        else:
            cursor.execute("""
                SELECT * FROM season_reward_ledger
                WHERE user_id = ?
                ORDER BY created_at DESC
            """, (user_id,))
        return [dict(r) for r in cursor.fetchall()]


# ─── LOGOVO: Division Management & Multi-Topic Routing ──────────────────────

def create_division(
    name: str,
    code: str,
    tournament_id: int = 1,
    topic_id: int | None = None,
    sort_order: int = 0
) -> int:
    """Create a new division within a tournament."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO divisions (name, code, tournament_id, topic_id, sort_order)
            VALUES (?, ?, ?, ?, ?)
        """, (name.strip(), code.strip().upper(), tournament_id, topic_id, sort_order))
        return cursor.lastrowid


def ensure_canonical_divisions() -> None:
    """Ensure the 5 canonical divisions exist in the database."""
    with transaction() as conn:
        conn.cursor().execute("""
            INSERT OR IGNORE INTO divisions (id, tournament_id, name, code, season_id, sort_order)
            VALUES
                (1, 1, 'Дивизион 1', 'DIV_1', 1, 1),
                (2, 1, 'Дивизион 2', 'DIV_2', 1, 2),
                (3, 1, 'Дивизион 3', 'DIV_3', 1, 3),
                (4, 1, 'Дивизион 4', 'DIV_4', 1, 4),
                (5, 1, 'Дивизион 5', 'DIV_5', 1, 5)
        """)


def get_divisions(is_active: bool | None = None, only_active: bool | None = None) -> list[dict]:
    """Retrieve all divisions, optionally filtered by is_active status."""
    ensure_canonical_divisions()
    if only_active is not None and is_active is None:
        is_active = only_active
    with transaction() as conn:
        cursor = conn.cursor()
        if is_active is None:
            cursor.execute("SELECT * FROM divisions ORDER BY sort_order ASC, id ASC")
        else:
            cursor.execute(
                "SELECT * FROM divisions WHERE is_active = ? ORDER BY sort_order ASC, id ASC",
                (1 if is_active else 0,)
            )
        return [dict(r) for r in cursor.fetchall()]


def get_division(division_id: int) -> dict | None:
    """Retrieve a single division by ID."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM divisions WHERE id = ?", (division_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_division_by_code(code: str) -> dict | None:
    """Retrieve a single division by unique code."""
    if not code:
        return None
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM divisions WHERE UPPER(code) = UPPER(?)", (code.strip(),))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_division(division_id: int, **kwargs) -> None:
    """Update division attributes dynamically (name, code, topic_id, is_active, sort_order)."""
    allowed_keys = {"name", "code", "tournament_id", "topic_id", "is_active", "sort_order"}
    updates = {k: v for k, v in kwargs.items() if k in allowed_keys}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [division_id]
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE divisions SET {set_clause} WHERE id = ?", values)


def assign_user_division(telegram_id: int, division_id: int | None) -> None:
    """Assign or unassign (if None) a user to a division."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET division_id = ? WHERE telegram_id = ?",
            (division_id, telegram_id)
        )


def get_division_users(division_id: int | None) -> list[dict]:
    """
    Retrieve all users belonging to a specific division.
    If division_id is None, returns legacy users without assigned division.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        if division_id is None:
            cursor.execute("""
                SELECT telegram_id, username, team_name, league_name, role, division_id, warn_count, squad_photo_id
                FROM users
                WHERE division_id IS NULL AND team_name IS NOT NULL AND team_name != ''
                ORDER BY team_name ASC
            """)
        else:
            cursor.execute("""
                SELECT telegram_id, username, team_name, league_name, role, division_id, warn_count, squad_photo_id
                FROM users
                WHERE division_id = ? AND team_name IS NOT NULL AND team_name != ''
                ORDER BY team_name ASC
            """, (division_id,))
        return [dict(r) for r in cursor.fetchall()]


# ─── LOGOVO: Canonical Division Topics & Roles ──────────────────────────────

CANONICAL_TOPIC_TYPES = {
    "draft": "draft",
    "drafts": "draft",
    "черновик": "draft",
    "previews": "previews",
    "preview": "previews",
    "преды": "previews",
    "results": "results",
    "result": "results",
    "результаты": "results",
    "reports": "reports",
    "report": "reports",
    "отчеты": "reports",
    "отчёты": "reports",
    "lineups": "lineups",
    "lineup": "lineups",
    "squads": "lineups",
    "squad": "lineups",
    "составы": "lineups",
    "tables": "tables",
    "таблицы": "tables",
    "warns": "warns",
    "варны": "warns"
}

TOPIC_DISPLAY_NAMES = {
    "draft": "🏟 ЧЕРНОВИК",
    "previews": "👤 ПРЕДЫ",
    "results": "🎛 РЕЗУЛЬТАТЫ",
    "reports": "📞 ОТЧЁТЫ",
    "lineups": "🗺 СОСТАВЫ",
    "tables": "📊 ТАБЛИЦЫ",
    "warns": "⚠️ ПРЕДУПРЕЖДЕНИЯ"
}

PRIMARY_DIVISION_TOPICS = ["draft", "previews", "results", "reports", "lineups"]

def normalize_topic_type(topic_type: str) -> str:
    raw = str(topic_type).strip().lower()
    return CANONICAL_TOPIC_TYPES.get(raw, raw)


def is_division_admin(user_id: int, division_id: int) -> bool:
    """
    Check if user is allowed to manage the given division.
    True if:
    1. User is Global Admin (in ADMIN_IDS or role == 'admin' with division_id IS NULL)
    2. User is explicitly registered in division_admins table
    3. User has role in ('admin', 'division_admin') and users.division_id == division_id
    """
    if not user_id:
        return False
    from config import ADMIN_IDS
    if user_id in ADMIN_IDS:
        return True

    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT role, division_id FROM users WHERE telegram_id = ?", (user_id,))
        u = cursor.fetchone()
        if u:
            if u["role"] == "admin" and (u["division_id"] is None or u["division_id"] == division_id):
                return True
            if u["role"] in ("admin", "division_admin") and u["division_id"] == division_id:
                return True

        cursor.execute("SELECT 1 FROM division_admins WHERE division_id = ? AND user_id = ?", (division_id, user_id))
        if cursor.fetchone():
            return True

    return False


def add_division_admin(division_id: int, user_id: int) -> None:
    """Grant division admin privileges to a user."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO division_admins (division_id, user_id)
            VALUES (?, ?)
            ON CONFLICT(division_id, user_id) DO NOTHING
        """, (division_id, user_id))


def remove_division_admin(division_id: int, user_id: int) -> None:
    """Revoke division admin privileges from a user."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM division_admins WHERE division_id = ? AND user_id = ?", (division_id, user_id))


def get_division_admins(division_id: int) -> list[int]:
    """List all admin user_ids for a division."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM division_admins WHERE division_id = ?", (division_id,))
        return [r["user_id"] for r in cursor.fetchall()]


def bind_division_topic(
    division_id: int, 
    group_chat_id: int, 
    message_thread_id: int, 
    topic_type: str, 
    force: bool = False
) -> dict:
    """
    Safely bind a Telegram forum topic (group_chat_id, message_thread_id) to a division.
    Enforces isolation, prevents duplicate bindings, and protects against cross-division topic theft.
    """
    norm_type = normalize_topic_type(topic_type)
    valid_types = set(CANONICAL_TOPIC_TYPES.values())
    if norm_type not in valid_types:
        return {"status": "error", "error": f"Invalid topic type: {topic_type}"}

    with transaction() as conn:
        cursor = conn.cursor()
        
        # 1. Validate division exists
        cursor.execute("SELECT id, name FROM divisions WHERE id = ?", (division_id,))
        div_row = cursor.fetchone()
        if not div_row:
            return {"status": "error", "error": f"Division {division_id} not found"}

        # 2. Check current binding of this exact topic (group_chat_id, message_thread_id)
        cursor.execute("""
            SELECT id, division_id, topic_type 
            FROM division_topics 
            WHERE group_chat_id = ? AND message_thread_id = ?
        """, (group_chat_id, message_thread_id))
        existing_topic = cursor.fetchone()

        # Idempotency: same topic already assigned to this exact division and type
        if existing_topic:
            e_div = existing_topic["division_id"]
            e_type = normalize_topic_type(existing_topic["topic_type"])
            if e_div == division_id and e_type == norm_type:
                return {
                    "status": "already_bound",
                    "division_id": division_id,
                    "division_name": div_row["name"],
                    "topic_type": norm_type,
                    "group_chat_id": group_chat_id,
                    "message_thread_id": message_thread_id
                }
            elif not force:
                # Conflict Scenario 1: topic belongs to another division or another type
                cursor.execute("SELECT name FROM divisions WHERE id = ?", (e_div,))
                other_div_row = cursor.fetchone()
                other_div_name = other_div_row["name"] if other_div_row else f"ID {e_div}"
                return {
                    "status": "conflict_topic",
                    "current_division_id": e_div,
                    "current_division_name": other_div_name,
                    "current_topic_type": e_type,
                    "requested_division_id": division_id,
                    "requested_division_name": div_row["name"],
                    "requested_topic_type": norm_type,
                    "group_chat_id": group_chat_id,
                    "message_thread_id": message_thread_id
                }

        # 3. Check if division already has a topic assigned for this topic_type
        cursor.execute("""
            SELECT id, group_chat_id, message_thread_id 
            FROM division_topics 
            WHERE division_id = ? AND (topic_type = ? OR topic_type = ?)
        """, (division_id, norm_type, "drafts" if norm_type == "draft" else norm_type))
        existing_type = cursor.fetchone()

        if existing_type and (existing_type["group_chat_id"] != group_chat_id or existing_type["message_thread_id"] != message_thread_id):
            if not force:
                # Conflict Scenario 2: division already has this topic type bound elsewhere
                return {
                    "status": "conflict_type",
                    "current_chat_id": existing_type["group_chat_id"],
                    "current_thread_id": existing_type["message_thread_id"],
                    "requested_chat_id": group_chat_id,
                    "requested_thread_id": message_thread_id,
                    "division_id": division_id,
                    "division_name": div_row["name"],
                    "topic_type": norm_type
                }

        # 4. If force, clean up old conflicting records
        if force:
            cursor.execute("""
                DELETE FROM division_topics 
                WHERE group_chat_id = ? AND message_thread_id = ?
            """, (group_chat_id, message_thread_id))
            cursor.execute("""
                DELETE FROM division_topics 
                WHERE division_id = ? AND (topic_type = ? OR topic_type = ?)
            """, (division_id, norm_type, "drafts" if norm_type == "draft" else norm_type))

        # 5. Insert or update new binding
        cursor.execute("""
            INSERT INTO division_topics (division_id, topic_type, message_thread_id, group_chat_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(division_id, topic_type) DO UPDATE SET
                message_thread_id = excluded.message_thread_id,
                group_chat_id = excluded.group_chat_id
        """, (division_id, norm_type, message_thread_id, group_chat_id))

        return {
            "status": "ok",
            "division_id": division_id,
            "division_name": div_row["name"],
            "topic_type": norm_type,
            "group_chat_id": group_chat_id,
            "message_thread_id": message_thread_id
        }


def unbind_division_topic(group_chat_id: int, message_thread_id: int) -> dict | None:
    """Unbind a topic by its (group_chat_id, message_thread_id). Returns unbind info or None."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT dt.id, dt.division_id, dt.topic_type, dt.group_chat_id, dt.message_thread_id, d.name as division_name
            FROM division_topics dt
            LEFT JOIN divisions d ON dt.division_id = d.id
            WHERE dt.group_chat_id = ? AND dt.message_thread_id = ?
        """, (group_chat_id, message_thread_id))
        row = cursor.fetchone()
        if not row:
            return None
        info = dict(row)
        cursor.execute("DELETE FROM division_topics WHERE id = ?", (row["id"],))
        return info


def get_topic_binding(group_chat_id: int | None, message_thread_id: int) -> dict | None:
    """Retrieve full binding record strictly for a given chat and thread."""
    if group_chat_id is None:
        return None
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT dt.*, d.name as division_name, d.code as division_code
            FROM division_topics dt
            JOIN divisions d ON dt.division_id = d.id
            WHERE dt.group_chat_id = ? AND dt.message_thread_id = ?
            ORDER BY dt.id DESC LIMIT 1
        """, (group_chat_id, message_thread_id))
        row = cursor.fetchone()
        if row:
            d = dict(row)
            d["topic_type"] = normalize_topic_type(d["topic_type"])
            return d
        return None


def get_division_topics_map(division_id: int) -> dict[str, dict]:
    """Retrieve mapping of topic_type -> {group_chat_id, message_thread_id} for a division."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT topic_type, group_chat_id, message_thread_id
            FROM division_topics
            WHERE division_id = ?
        """, (division_id,))
        res = {}
        for r in cursor.fetchall():
            res[normalize_topic_type(r["topic_type"])] = {
                "group_chat_id": r["group_chat_id"],
                "message_thread_id": r["message_thread_id"]
            }
        return res


def get_all_division_topics() -> list[dict]:
    """Retrieve all division topic bindings across all divisions."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT dt.*, d.name as division_name, d.code as division_code
            FROM division_topics dt
            LEFT JOIN divisions d ON dt.division_id = d.id
            ORDER BY d.sort_order ASC, dt.division_id ASC, dt.topic_type ASC
        """)
        rows = []
        for r in cursor.fetchall():
            item = dict(r)
            item["topic_type"] = normalize_topic_type(item["topic_type"])
            rows.append(item)
        return rows


def set_division_topic(
    division_id: int, 
    topic_type: str, 
    message_thread_id: int | None,
    group_chat_id: int | None = None
) -> None:
    """
    Register or update a Telegram forum topic for a division.
    If message_thread_id is None, removes the topic mapping.
    """
    norm_type = normalize_topic_type(topic_type)
    with transaction() as conn:
        cursor = conn.cursor()
        if message_thread_id is None:
            cursor.execute("""
                DELETE FROM division_topics
                WHERE division_id = ? AND (topic_type = ? OR topic_type = ?)
            """, (division_id, norm_type, topic_type))
        else:
            if group_chat_id is not None:
                cursor.execute("""
                    DELETE FROM division_topics
                    WHERE group_chat_id = ? AND message_thread_id = ?
                """, (group_chat_id, message_thread_id))
            cursor.execute("""
                INSERT INTO division_topics (division_id, topic_type, message_thread_id, group_chat_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(division_id, topic_type) DO UPDATE SET 
                    message_thread_id = excluded.message_thread_id,
                    group_chat_id = COALESCE(excluded.group_chat_id, division_topics.group_chat_id)
            """, (division_id, norm_type, message_thread_id, group_chat_id))


def get_division_topic(division_id: int, topic_type: str, group_chat_id: int | None = None) -> int | None:
    """Retrieve message_thread_id for a division's specific topic type."""
    norm_type = normalize_topic_type(topic_type)
    with transaction() as conn:
        cursor = conn.cursor()
        if group_chat_id is not None:
            cursor.execute("""
                SELECT message_thread_id FROM division_topics
                WHERE division_id = ? AND (topic_type = ? OR topic_type = ?) AND (group_chat_id = ? OR group_chat_id IS NULL)
                ORDER BY group_chat_id DESC LIMIT 1
            """, (division_id, norm_type, topic_type, group_chat_id))
        else:
            cursor.execute("""
                SELECT message_thread_id FROM division_topics
                WHERE division_id = ? AND (topic_type = ? OR topic_type = ?)
                LIMIT 1
            """, (division_id, norm_type, topic_type))
        row = cursor.fetchone()
        return row["message_thread_id"] if row else None


def get_division_by_topic(message_thread_id: int, topic_type: str = "drafts", group_chat_id: int | None = None) -> dict | None:
    """
    Find which division owns the given message_thread_id for the given topic_type.
    Returns division dictionary or None.
    """
    norm_type = normalize_topic_type(topic_type)
    with transaction() as conn:
        cursor = conn.cursor()
        if group_chat_id is not None:
            cursor.execute("""
                SELECT d.*
                FROM divisions d
                JOIN division_topics dt ON d.id = dt.division_id
                WHERE dt.message_thread_id = ? 
                  AND (dt.topic_type = ? OR dt.topic_type = ?)
                  AND dt.group_chat_id = ?
                LIMIT 1
            """, (message_thread_id, norm_type, topic_type, group_chat_id))
        else:
            cursor.execute("""
                SELECT d.*
                FROM divisions d
                JOIN division_topics dt ON d.id = dt.division_id
                WHERE dt.message_thread_id = ? 
                  AND (dt.topic_type = ? OR dt.topic_type = ?)
                  AND dt.group_chat_id IS NULL
                LIMIT 1
            """, (message_thread_id, norm_type, topic_type))
        row = cursor.fetchone()
        return dict(row) if row else None


# ─── Phase 7: AI & Sports Intelligence Database Helpers ──────────────────────

def get_team_elo(team_name: str, division_id: int = 1, season_id: int = 1) -> float:
    """Retrieve persisted Elo rating for a team, defaulting to 1500.0 if not tracked."""
    if not team_name:
        return 1500.0
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT elo_rating FROM team_ratings
            WHERE LOWER(team_name) = LOWER(?) AND division_id = ? AND season_id = ?
        """, (team_name, division_id, season_id))
        row = cursor.fetchone()
        return float(row["elo_rating"]) if row else 1500.0


def update_team_elo(
    team_name: str,
    division_id: int,
    season_id: int,
    new_elo: float,
    matches_counted: int | None = None
) -> None:
    """Upsert persisted Elo rating for a team within a division and season."""
    if not team_name:
        return
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, matches_counted FROM team_ratings
            WHERE LOWER(team_name) = LOWER(?) AND division_id = ? AND season_id = ?
        """, (team_name, division_id, season_id))
        existing = cursor.fetchone()

        if existing:
            cnt = matches_counted if matches_counted is not None else (existing["matches_counted"] + 1)
            cursor.execute("""
                UPDATE team_ratings
                SET elo_rating = ?, matches_counted = ?, last_updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (round(float(new_elo), 2), cnt, existing["id"]))
        else:
            cnt = matches_counted if matches_counted is not None else 1
            cursor.execute("""
                INSERT INTO team_ratings (team_name, division_id, season_id, elo_rating, matches_counted)
                VALUES (?, ?, ?, ?, ?)
            """, (team_name, division_id, season_id, round(float(new_elo), 2), cnt))


def save_ai_prediction(
    match_id: int,
    division_id: int,
    season_id: int,
    model_version: str,
    feature_version: str,
    home_prob: float,
    draw_prob: float,
    away_prob: float,
    confidence: float,
    over_1_5: float | None = None,
    over_2_5: float | None = None,
    over_3_5: float | None = None,
    btts_yes: float | None = None,
    btts_no: float | None = None,
    key_factors: list[str] | None = None
) -> int:
    """Persist an AI model prediction record."""
    import json as _json
    factors_json = _json.dumps(key_factors or [], ensure_ascii=False)
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO predictions (
                match_id, division_id, season_id, model_version, feature_version,
                home_probability, draw_probability, away_probability,
                over_1_5_probability, over_2_5_probability, over_3_5_probability,
                btts_yes_probability, btts_no_probability,
                confidence, key_factors
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            match_id, division_id, season_id, model_version, feature_version,
            round(home_prob, 4), round(draw_prob, 4), round(away_prob, 4),
            round(over_1_5, 4) if over_1_5 is not None else None,
            round(over_2_5, 4) if over_2_5 is not None else None,
            round(over_3_5, 4) if over_3_5 is not None else None,
            round(btts_yes, 4) if btts_yes is not None else None,
            round(btts_no, 4) if btts_no is not None else None,
            round(confidence, 4), factors_json
        ))
        return cursor.lastrowid


def get_ai_prediction(match_id: int, model_version: str = "ensemble_v1") -> dict | None:
    """Retrieve latest stored AI prediction for a match."""
    import json as _json
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM predictions
            WHERE match_id = ? AND model_version = ?
            ORDER BY id DESC LIMIT 1
        """, (match_id, model_version))
        row = cursor.fetchone()
        if not row:
            return None
        res = dict(row)
        if res.get("key_factors"):
            try:
                res["key_factors"] = _json.loads(res["key_factors"])
            except Exception:
                res["key_factors"] = []
        return res


def save_prediction_snapshot(
    match_id: int,
    stage: str,
    minute: int | None,
    home_score: int,
    away_score: int,
    home_prob: float,
    draw_prob: float,
    away_prob: float,
    confidence: float
) -> int:
    """Save temporal snapshot of prediction at a given game state."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO prediction_snapshots (
                match_id, stage, minute, home_score, away_score,
                home_prob, draw_prob, away_prob, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            match_id, stage, minute, home_score, away_score,
            round(home_prob, 4), round(draw_prob, 4), round(away_prob, 4),
            round(confidence, 4)
        ))
        return cursor.lastrowid


def get_prediction_snapshots(match_id: int, limit: int = 20) -> list[dict]:
    """Retrieve chronological snapshots for a match."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM prediction_snapshots
            WHERE match_id = ?
            ORDER BY id ASC LIMIT ?
        """, (match_id, limit))
        return [dict(r) for r in cursor.fetchall()]


def resolve_ai_predictions(match_id: int, home_score: int, away_score: int) -> int:
    """
    Resolve pending predictions after confirmed match result.
    Calculates Brier Score: (p_home - y_home)^2 + (p_draw - y_draw)^2 + (p_away - y_away)^2.
    """
    if home_score is None or away_score is None:
        return 0

    try:
        home_score = int(home_score)
        away_score = int(away_score)
    except (ValueError, TypeError):
        return 0

    if home_score > away_score:
        actual_result = "home"
        y = (1.0, 0.0, 0.0)
    elif home_score == away_score:
        actual_result = "draw"
        y = (0.0, 1.0, 0.0)
    else:
        actual_result = "away"
        y = (0.0, 0.0, 1.0)

    resolved_count = 0
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, home_probability, draw_probability, away_probability
            FROM predictions
            WHERE match_id = ? AND resolved_at IS NULL
        """, (match_id,))
        preds = cursor.fetchall()

        for p in preds:
            p_h = float(p["home_probability"])
            p_d = float(p["draw_probability"])
            p_a = float(p["away_probability"])

            # Multiclass Brier score
            brier = round(((p_h - y[0]) ** 2 + (p_d - y[1]) ** 2 + (p_a - y[2]) ** 2) / 2.0, 4)

            # Determine is_correct
            highest_prob = max(p_h, p_d, p_a)
            predicted_outcome = "home" if highest_prob == p_h else ("draw" if highest_prob == p_d else "away")
            is_correct = (predicted_outcome == actual_result)

            cursor.execute("""
                UPDATE predictions
                SET resolved_at = CURRENT_TIMESTAMP, actual_result = ?, is_correct = ?, brier_score = ?
                WHERE id = ?
            """, (actual_result, 1 if is_correct else 0, brier, p["id"]))
            resolved_count += 1

    return resolved_count


def correct_ai_predictions(match_id: int, new_home_score: int, new_away_score: int) -> int:
    """
    Recalculate and correct AI prediction accuracy when a confirmed match result
    undergoes administrative or official score correction.
    Strict Invariant: Re-evaluates AI analytical records without triggering bet settlement or wallet adjustments.
    """
    if new_home_score is None or new_away_score is None:
        return 0

    try:
        new_home_score = int(new_home_score)
        new_away_score = int(new_away_score)
    except (ValueError, TypeError):
        return 0

    if new_home_score > new_away_score:
        actual_result = "home"
        y = (1.0, 0.0, 0.0)
    elif new_home_score == new_away_score:
        actual_result = "draw"
        y = (0.0, 1.0, 0.0)
    else:
        actual_result = "away"
        y = (0.0, 0.0, 1.0)

    updated_count = 0
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, home_probability, draw_probability, away_probability
            FROM predictions
            WHERE match_id = ?
        """, (match_id,))
        preds = cursor.fetchall()

        for p in preds:
            p_h = float(p["home_probability"])
            p_d = float(p["draw_probability"])
            p_a = float(p["away_probability"])

            brier = round(((p_h - y[0]) ** 2 + (p_d - y[1]) ** 2 + (p_a - y[2]) ** 2) / 2.0, 4)
            highest_prob = max(p_h, p_d, p_a)
            predicted_outcome = "home" if highest_prob == p_h else ("draw" if highest_prob == p_d else "away")
            is_correct = (predicted_outcome == actual_result)

            cursor.execute("""
                UPDATE predictions
                SET resolved_at = CURRENT_TIMESTAMP, actual_result = ?, is_correct = ?, brier_score = ?
                WHERE id = ?
            """, (actual_result, 1 if is_correct else 0, brier, p["id"]))
            updated_count += 1

    return updated_count


# ─── Phase 8: Real Sports Provider Repository Helpers ─────────────────────────

def record_provider_sync_log(
    provider: str,
    endpoint: str,
    status_code: int,
    records_count: int = 0,
    latency_ms: float = 0.0,
    error_message: str | None = None
) -> int:
    """Record an audit entry in provider_sync_log."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO provider_sync_log (
                provider, endpoint, status_code, records_count, latency_ms, error_message
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (provider, endpoint, status_code, records_count, round(latency_ms, 2), error_message))
        return cursor.lastrowid


def link_provider_match(
    provider: str,
    provider_match_id: str,
    match_id: int,
    division_id: int = 1,
    season_id: int = 1,
    status: str = "SCHEDULED",
    home_score: int = 0,
    away_score: int = 0,
    minute: int | None = None,
    payload: dict | None = None
) -> int:
    """Link an external provider match identifier to an internal match entity."""
    import json as _json
    payload_str = _json.dumps(payload, ensure_ascii=False) if payload else None
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO provider_matches (
                provider, provider_match_id, match_id, division_id, season_id,
                status, home_score, away_score, minute, payload, last_update_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(provider, provider_match_id) DO UPDATE SET
                status = excluded.status,
                home_score = excluded.home_score,
                away_score = excluded.away_score,
                minute = excluded.minute,
                payload = excluded.payload,
                last_update_at = CURRENT_TIMESTAMP
        """, (provider, str(provider_match_id), match_id, division_id, season_id, status, home_score, away_score, minute, payload_str))
        return cursor.lastrowid


def get_provider_match(provider: str, provider_match_id: str) -> dict | None:
    """Retrieve internal match link for given provider fixture."""
    import json as _json
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM provider_matches
            WHERE provider = ? AND provider_match_id = ?
            LIMIT 1
        """, (provider, str(provider_match_id)))
        row = cursor.fetchone()
        if not row:
            return None
        res = dict(row)
        if res.get("payload"):
            try:
                res["payload"] = _json.loads(res["payload"])
            except Exception:
                pass
        return res


def get_provider_match_by_internal_id(match_id: int) -> dict | None:
    """Retrieve external provider details for given internal match_id."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM provider_matches
            WHERE match_id = ?
            ORDER BY id DESC LIMIT 1
        """, (match_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_provider_health_state(
    provider_name: str,
    status: str,
    consecutive_failures: int = 0,
    last_error: str | None = None
) -> None:
    """Update circuit breaker and connectivity status for a sports provider."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sports_providers (
                provider_name, display_name, circuit_breaker_status, consecutive_failures, last_sync_at, last_error
            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(provider_name) DO UPDATE SET
                circuit_breaker_status = excluded.circuit_breaker_status,
                consecutive_failures = excluded.consecutive_failures,
                last_sync_at = CURRENT_TIMESTAMP,
                last_error = excluded.last_error,
                updated_at = CURRENT_TIMESTAMP
        """, (provider_name, provider_name.upper(), status, consecutive_failures, last_error))


def get_provider_health_state(provider_name: str) -> dict | None:
    """Fetch stored health state of a sports provider."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM sports_providers
            WHERE provider_name = ?
            LIMIT 1
        """, (provider_name,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_stale_provider_matches_count(stale_threshold_seconds: int = 120) -> int:
    """Count number of currently open/live matches that haven't received updates within threshold."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) as cnt
            FROM live_match_states
            WHERE status IN ('LIVE', 'HALFTIME')
              AND (julianday('now') - julianday(last_updated_at)) * 86400 > ?
        """, (stale_threshold_seconds,))
        row = cursor.fetchone()
        return row["cnt"] if row else 0
