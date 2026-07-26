import logging
import sqlite3
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
        logger.exception("Failed to connect to database at {DB_PATH}")
        raise

@contextmanager
def transaction() -> Generator[sqlite3.Connection, None, None]:
    """Provide a transactional scope around database operations."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.exception("Transaction rolled back due to error")
        raise
    finally:
        conn.close()

def init_db() -> None:
    """Initialize the database tables."""
    logger.info("Initializing database tables...")
    with transaction() as conn:
        cursor = conn.cursor()
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
                round_number INTEGER PRIMARY KEY,
                is_open BOOLEAN DEFAULT 0,
                deadline TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS round_reminders (
                round_number INTEGER,
                reminder_type TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(round_number, reminder_type)
            )
        """)
        # Add pending_notification column if missing (safe for existing DBs)
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN pending_notification INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # Column already exists
            
        try:
            cursor.execute("ALTER TABLE matches ADD COLUMN is_extended INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
            
        # Safely migration-add new columns to matches using predefined SAFE_COLUMNS tuple.
        # Note: String interpolation is safe here as column names/types are hardcoded internal constants, not user input.
        SAFE_COLUMNS = (
            ("photo_id", "TEXT"),
            ("dispute_photos", "TEXT"),
            ("reported_by", "INTEGER"),
            ("proposed_time", "TEXT"),
            ("proposed_by", "INTEGER"),
            ("time_status", "TEXT DEFAULT 'none'"),
        )
        for col_name, col_type in SAFE_COLUMNS:
            try:
                cursor.execute(f"ALTER TABLE matches ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass
            
        # Performance indexes for matches and match_events
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_p1 ON matches(player1_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_p2 ON matches(player2_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_round ON matches(round_number)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_match ON match_events(match_id)")

        logger.info("Database tables initialized successfully.")

def get_user(telegram_id: int) -> sqlite3.Row | None:
    """Retrieve a user record by Telegram ID."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT telegram_id, username, team_name, league_name, role, registered_at, squad_photo_id FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        return cursor.fetchone()

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
            "SELECT telegram_id, username, team_name, league_name, role, registered_at FROM users ORDER BY registered_at DESC"
        )
        return cursor.fetchall()

def get_player_stats(telegram_id: int) -> dict:
    """Calculate and return match statistics for a player using a single aggregated SQL query."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                SUM(CASE 
                    WHEN (player1_id = ? AND player1_score > player2_score) OR (player2_id = ? AND player2_score > player1_score) THEN 1 
                    ELSE 0 
                END) AS wins,
                SUM(CASE 
                    WHEN player1_score = player2_score THEN 1 
                    ELSE 0 
                END) AS draws,
                SUM(CASE 
                    WHEN (player1_id = ? AND player1_score < player2_score) OR (player2_id = ? AND player2_score < player1_score) THEN 1 
                    ELSE 0 
                END) AS losses,
                SUM(CASE 
                    WHEN player1_id = ? THEN COALESCE(player1_score, 0)
                    WHEN player2_id = ? THEN COALESCE(player2_score, 0)
                    ELSE 0 
                END) AS goals_scored,
                SUM(CASE 
                    WHEN player1_id = ? THEN COALESCE(player2_score, 0)
                    WHEN player2_id = ? THEN COALESCE(player1_score, 0)
                    ELSE 0 
                END) AS goals_conceded
            FROM matches
            WHERE status = 'confirmed' AND (player1_id = ? OR player2_id = ?)
        """, (telegram_id, telegram_id, telegram_id, telegram_id, telegram_id, telegram_id, telegram_id, telegram_id, telegram_id, telegram_id))
        
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

def get_pending_matches(telegram_id: int) -> list[dict]:
    """Retrieve active matches for a user in OPEN rounds only, including opponent details."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                m.id, m.round_number, m.status,
                o.telegram_id AS opponent_id,
                o.username AS opponent_username,
                o.team_name AS opponent_team
            FROM matches m
            JOIN users o ON (
                (m.player1_id = ? AND m.player2_id = o.telegram_id) OR
                (m.player2_id = ? AND m.player1_id = o.telegram_id)
            )
            JOIN rounds r ON m.round_number = r.round_number
            WHERE r.is_open = 1 AND m.status IN ('pending', 'reported', 'disputed')
            ORDER BY m.round_number ASC
        """, (telegram_id, telegram_id))
        return [dict(row) for row in cursor.fetchall()]

def get_match_history(telegram_id: int) -> list[dict]:
    """Retrieve played (confirmed) matches for a user, including opponent profile details."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                m.id, m.round_number, m.player1_score, m.player2_score,
                m.player1_id, m.player2_id,
                o.telegram_id AS opponent_id,
                o.username AS opponent_username,
                o.team_name AS opponent_team
            FROM matches m
            JOIN users o ON (
                (m.player1_id = ? AND m.player2_id = o.telegram_id) OR
                (m.player2_id = ? AND m.player1_id = o.telegram_id)
            )
            WHERE m.status = 'confirmed'
            ORDER BY m.played_at DESC, m.round_number DESC
        """, (telegram_id, telegram_id))
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

def get_standings() -> list[dict]:
    """Calculate the standings of all registered players dynamically."""
    with transaction() as conn:
        cursor = conn.cursor()
        # Get all registered users who completed profile registration
        cursor.execute(
            "SELECT telegram_id, team_name, username FROM users WHERE team_name IS NOT NULL"
        )
        users = {
            row["telegram_id"]: {
                "telegram_id": row["telegram_id"],
                "team_name": row["team_name"] or "",
                "username": row["username"] or "",
                "played": 0,
                "wins": 0,
                "draws": 0,
                "losses": 0,
                "goals_scored": 0,
                "goals_conceded": 0,
                "points": 0,
            }
            for row in cursor.fetchall()
        }

        # Get all confirmed matches
        cursor.execute(
            "SELECT player1_id, player2_id, player1_score, player2_score FROM matches WHERE status = 'confirmed'"
        )
        matches = cursor.fetchall()

        for match in matches:
            p1_id = match["player1_id"]
            p2_id = match["player2_id"]
            p1_score = match["player1_score"]
            p2_score = match["player2_score"]

            if p1_score is None or p2_score is None:
                continue

            if p1_id in users:
                u1 = users[p1_id]
                u1["played"] += 1
                u1["goals_scored"] += p1_score
                u1["goals_conceded"] += p2_score
                if p1_score > p2_score:
                    u1["wins"] += 1
                    u1["points"] += 3
                elif p1_score < p2_score:
                    u1["losses"] += 1
                else:
                    u1["draws"] += 1
                    u1["points"] += 1

            if p2_id in users:
                u2 = users[p2_id]
                u2["played"] += 1
                u2["goals_scored"] += p2_score
                u2["goals_conceded"] += p1_score
                if p2_score > p1_score:
                    u2["wins"] += 1
                    u2["points"] += 3
                elif p2_score < p1_score:
                    u2["losses"] += 1
                else:
                    u2["draws"] += 1
                    u2["points"] += 1

        # Convert to list and sort
        standings_list = list(users.values())
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
    """Retrieve a single match by ID with player nicknames and team names."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                m.id, m.round_number, m.player1_id, m.player2_id,
                m.player1_score, m.player2_score, m.status, m.played_at, m.is_extended,
                m.photo_id, m.dispute_photos, m.reported_by,
                m.proposed_time, m.proposed_by, m.time_status,
                u1.username AS player1_nickname, u1.team_name AS player1_team, u1.username AS player1_username,
                u2.username AS player2_nickname, u2.team_name AS player2_team, u2.username AS player2_username
            FROM matches m
            LEFT JOIN users u1 ON m.player1_id = u1.telegram_id
            LEFT JOIN users u2 ON m.player2_id = u2.telegram_id
            WHERE m.id = ?
        """, (match_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def report_match_score(match_id: int, player1_score: int, player2_score: int, reporter_id: int = None, photo_id: str = None) -> None:
    """Set the proposed scores, reporter, photo and update status to 'reported'."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE matches SET player1_score = ?, player2_score = ?, reported_by = ?, photo_id = ?, status = 'reported' WHERE id = ?",
            (player1_score, player2_score, reporter_id, photo_id, match_id)
        )

def save_dispute_evidence(match_id: int, dispute_photos_json: str) -> None:
    """Save dispute photos from guest and set status to 'disputed'."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE matches SET dispute_photos = ?, status = 'disputed' WHERE id = ?",
            (dispute_photos_json, match_id)
        )

def confirm_match(match_id: int) -> None:
    """Confirm the match score and set status to 'confirmed'."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE matches SET status = 'confirmed', played_at = CURRENT_TIMESTAMP WHERE id = ?",
            (match_id,)
        )

def confirm_and_finalize_match(match_id: int, p1_score: int, p2_score: int, events: list, reporter_id: int = None, photo_id: str = None) -> None:
    """Instantly save and confirm a match with events in database."""
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
            "UPDATE matches SET player1_score = ?, player2_score = ?, reported_by = ?, photo_id = ?, status = 'confirmed', played_at = CURRENT_TIMESTAMP WHERE id = ?",
            (p1_score, p2_score, reporter_id, photo_id, match_id)
        )

def dispute_match(match_id: int) -> None:
    """Set match status to 'disputed'."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE matches SET status = 'disputed' WHERE id = ?",
            (match_id,)
        )

def reset_match_report(match_id: int) -> None:
    """Reset match status to pending and clear reported values."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE matches SET status = 'pending', player1_score = NULL, player2_score = NULL, reported_by = NULL, photo_id = NULL, dispute_photos = NULL WHERE id = ?",
            (match_id,)
        )
        cursor.execute("DELETE FROM match_events WHERE match_id = ?", (match_id,))

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
                m.id, m.round_number, m.player1_id, m.player2_id, m.status,
                u1.username AS player1_username, u1.team_name AS player1_team,
                u2.username AS player2_username, u2.team_name AS player2_team
            FROM matches m
            LEFT JOIN users u1 ON m.player1_id = u1.telegram_id
            LEFT JOIN users u2 ON m.player2_id = u2.telegram_id
            WHERE m.round_number IN ({placeholders})
            ORDER BY m.round_number ASC, m.id ASC
        """, tuple(round_numbers))
        return [dict(row) for row in cursor.fetchall()]

def get_unplayed_matches_by_round(round_number: int) -> list[dict]:
    """Retrieve unplayed (pending) matches in a specific round with player details."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                m.id, m.round_number, m.player1_id, m.player2_id, m.status,
                u1.username AS player1_username, u1.team_name AS player1_team,
                u2.username AS player2_username, u2.team_name AS player2_team
            FROM matches m
            LEFT JOIN users u1 ON m.player1_id = u1.telegram_id
            LEFT JOIN users u2 ON m.player2_id = u2.telegram_id
            WHERE m.round_number = ? AND m.status = 'pending'
            ORDER BY m.id ASC
        """, (round_number,))
        return [dict(row) for row in cursor.fetchall()]

def get_open_rounds_with_deadlines() -> list[dict]:
    """Retrieve all open rounds that have a deadline set."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT round_number, deadline FROM rounds WHERE is_open = 1 AND deadline IS NOT NULL AND deadline != ''"
        )
        return [dict(row) for row in cursor.fetchall()]

def get_teams_recent_form(limit: int = 5) -> dict[int, list[str]]:
    """
    Retrieve the last `limit` confirmed match outcomes for each user by telegram_id.
    Returns dict mapping user telegram_id -> list of 'W', 'D', 'L' outcomes.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id FROM users")
        user_ids = [row["telegram_id"] for row in cursor.fetchall()]

        form_map = {}
        for uid in user_ids:
            cursor.execute("""
                SELECT player1_id, player2_id, player1_score, player2_score
                FROM matches
                WHERE (player1_id = ? OR player2_id = ?) AND status = 'confirmed'
                ORDER BY round_number DESC, id DESC
                LIMIT ?
            """, (uid, uid, limit))
            rows = cursor.fetchall()
            outcomes = []
            for r in reversed(rows):
                p1, p2 = r["player1_id"], r["player2_id"]
                s1, s2 = r["player1_score"], r["player2_score"]
                if s1 is None or s2 is None:
                    continue
                if uid == p1:
                    if s1 > s2: outcomes.append('W')
                    elif s1 < s2: outcomes.append('L')
                    else: outcomes.append('D')
                else:
                    if s2 > s1: outcomes.append('W')
                    elif s2 < s1: outcomes.append('L')
                    else: outcomes.append('D')
            form_map[uid] = outcomes
        return form_map

def has_reminder_been_sent(round_number: int, reminder_type: str) -> bool:
    """Check if a specific reminder type (24h, 6h, 1h) has already been sent for a round."""
    with transaction() as conn:
        cursor = conn.cursor()
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

def create_round(round_number: int, deadline: str = None) -> None:
    """Create a new round in DB if it doesn't already exist (closed by default)."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO rounds (round_number, is_open, deadline) VALUES (?, 0, ?)",
            (round_number, deadline)
        )

def create_match(round_number: int, player1_id: int, player2_id: int) -> int:
    """Create a new pending match between two players in a round."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO matches (round_number, player1_id, player2_id, status) VALUES (?, ?, ?, 'pending')",
            (round_number, player1_id, player2_id)
        )
        return cursor.lastrowid

def record_reminder_sent(round_number: int, reminder_type: str) -> None:
    """Mark a specific reminder type as sent for a round."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO round_reminders (round_number, reminder_type) VALUES (?, ?)",
            (round_number, reminder_type)
        )

def get_disputed_matches() -> list[dict]:
    """Retrieve all disputed matches with player details."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                m.id, m.round_number, m.player1_id, m.player2_id,
                m.player1_score, m.player2_score, m.status,
                u1.username AS player1_nickname, u1.team_name AS player1_team,
                u2.username AS player2_nickname, u2.team_name AS player2_team
            FROM matches m
            LEFT JOIN users u1 ON m.player1_id = u1.telegram_id
            LEFT JOIN users u2 ON m.player2_id = u2.telegram_id
            WHERE m.status = 'disputed'
            ORDER BY m.round_number ASC
        """)
        return [dict(row) for row in cursor.fetchall()]

def admin_set_match_score(match_id: int, player1_score: int, player2_score: int) -> None:
    """Manually set match score and confirm it by admin."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE matches SET player1_score = ?, player2_score = ?, status = 'confirmed', played_at = CURRENT_TIMESTAMP WHERE id = ?",
            (player1_score, player2_score, match_id)
        )

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

def get_group_id() -> int | None:
    """Retrieve the automatically tracked Telegram Group ID."""
    val = get_config("group_id")
    try:
        return int(val) if val else None
    except ValueError:
        return None

def get_all_rounds() -> list[int]:
    """Retrieve all unique round numbers from matches."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT round_number FROM matches ORDER BY round_number ASC")
        return [row[0] for row in cursor.fetchall()]

def open_rounds_batch(start_round: int, end_round: int, deadline: str) -> None:
    """Open multiple rounds and set a shared deadline."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE rounds SET is_open = 1, deadline = ? WHERE round_number >= ? AND round_number <= ?",
            (deadline, start_round, end_round)
        )

def get_open_pending_matches() -> list[dict]:
    """Get all pending matches where the round is open and not extended."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                m.id, m.round_number, m.player1_id, m.player2_id, m.status, m.is_extended,
                u1.username AS player1_nickname, u1.team_name AS player1_team,
                u2.username AS player2_nickname, u2.team_name AS player2_team,
                r.deadline
            FROM matches m
            JOIN rounds r ON m.round_number = r.round_number
            LEFT JOIN users u1 ON m.player1_id = u1.telegram_id
            LEFT JOIN users u2 ON m.player2_id = u2.telegram_id
            WHERE m.status = 'pending' 
              AND m.is_extended = 0
              AND r.is_open = 1
            ORDER BY m.round_number ASC, m.id ASC
        """)
        return [dict(row) for row in cursor.fetchall()]

def extend_match_deadline(match_id: int) -> None:
    """Allow players to report score for an overdue match."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE matches SET is_extended = 1 WHERE id = ?", (match_id,))

def get_matches_by_round(round_number: int) -> list[dict]:
    """Retrieve all matches for a specific round with player details."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                m.id, m.round_number, m.player1_id, m.player2_id,
                m.player1_score, m.player2_score, m.status,
                u1.username AS player1_nickname, u1.team_name AS player1_team,
                u2.username AS player2_nickname, u2.team_name AS player2_team
            FROM matches m
            LEFT JOIN users u1 ON m.player1_id = u1.telegram_id
            LEFT JOIN users u2 ON m.player2_id = u2.telegram_id
            WHERE m.round_number = ?
            ORDER BY m.id ASC
        """, (round_number,))
        return [dict(row) for row in cursor.fetchall()]

def reset_match(match_id: int) -> None:
    """Reset a match result, setting status back to 'pending' and clearing scores."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE matches SET player1_score = NULL, player2_score = NULL, status = 'pending', played_at = NULL WHERE id = ?",
            (match_id,)
        )

def get_admins() -> list[dict]:
    """Retrieve all users with admin role."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id, username FROM users WHERE role = 'admin'")
        return [dict(row) for row in cursor.fetchall()]

def pre_register_player(username: str, team_name: str) -> int:
    """Pre-register a player with a temporary negative ID."""
    username_clean = username.strip().lstrip("@")
    with transaction() as conn:
        cursor = conn.cursor()
        
        # Check if username already exists in users table
        cursor.execute("SELECT telegram_id FROM users WHERE LOWER(username) = LOWER(?)", (username_clean,))
        row = cursor.fetchone()
        if row:
            # Update club name and role if already exists
            cursor.execute(
                "UPDATE users SET team_name = ?, role = 'player' WHERE LOWER(username) = LOWER(?)",
                (team_name.strip(), username_clean)
            )
            return row[0]
        
        # Generate a new unique negative ID for pre-registration
        cursor.execute("SELECT MIN(telegram_id) FROM users")
        min_row = cursor.fetchone()
        min_id = min_row[0] if min_row and min_row[0] else 0
        temp_id = min(min_id - 1, -1)
        
        cursor.execute(
            "INSERT INTO users (telegram_id, username, team_name, league_name, role) VALUES (?, ?, ?, ?, ?)",
            (temp_id, username_clean, team_name.strip(), "Основная", "player")
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
        
        # 1. Check if the exact telegram_id already exists
        cursor.execute("SELECT role FROM users WHERE telegram_id = ?", (telegram_id,))
        exists = cursor.fetchone()
        if exists:
            # Update username
            cursor.execute(
                "UPDATE users SET username = ? WHERE telegram_id = ?",
                (username, telegram_id)
            )
            return
            
        # 2. Check if there is a pre-registered user with this username (negative id)
        if username:
            cursor.execute(
                "SELECT telegram_id FROM users WHERE LOWER(username) = LOWER(?) AND telegram_id < 0",
                (username.strip(),)
            )
            pre_reg = cursor.fetchone()
            if pre_reg:
                old_id = pre_reg[0]
                
                # Fetch all data from the old user
                cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (old_id,))
                old_user = cursor.fetchone()
                
                if old_user:
                    # Insert the new record with the new ID, preserving all data
                    cursor.execute(
                        "INSERT INTO users (telegram_id, username, team_name, league_name, role, registered_at, pending_notification) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (telegram_id, username, old_user['team_name'], old_user['league_name'], old_user['role'], old_user['registered_at'], old_user['pending_notification'])
                    )
                    
                    # Update matches table
                    cursor.execute("UPDATE matches SET player1_id = ? WHERE player1_id = ?", (telegram_id, old_id))
                    cursor.execute("UPDATE matches SET player2_id = ? WHERE player2_id = ?", (telegram_id, old_id))
                    
                    # Delete the old user
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
        
        # Delete match_events & matches for this player first to satisfy FK constraints
        cursor.execute("""
            DELETE FROM match_events WHERE match_id IN (
                SELECT id FROM matches WHERE player1_id = ? OR player2_id = ?
            )
        """, (p_id, p_id))
        cursor.execute("DELETE FROM matches WHERE player1_id = ? OR player2_id = ?", (p_id, p_id))
        
        # Delete user
        cursor.execute("DELETE FROM users WHERE telegram_id = ?", (p_id,))
        
        display_name = f"@{uname}" if uname else f"ID {p_id}"
        return True, f"Игрок **{display_name}** ({team or 'без названия'}) успешно удален из лиги."

def set_player_club(player_ref: str, new_club: str) -> tuple[bool, str]:
    """Change player's club/team."""
    player_ref_clean = player_ref.strip().lstrip("@")
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id FROM users WHERE telegram_id = ? OR LOWER(username) = LOWER(?)", (player_ref_clean, player_ref_clean))
        row = cursor.fetchone()
        if not row:
            return False, "Игрок не найден."
        p_id = row[0]
        cursor.execute("UPDATE users SET team_name = ? WHERE telegram_id = ?", (new_club.strip(), p_id))
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
            # Unlink / delete old player
            cursor.execute("DELETE FROM users WHERE telegram_id = ?", (old_id,))
            # Also clean up unplayed matches for the deleted player
            cursor.execute("DELETE FROM matches WHERE player1_id = ? OR player2_id = ?", (old_id, old_id))
            
        # 2. Check if the new player already exists in the system
        cursor.execute("SELECT telegram_id FROM users WHERE LOWER(username) = LOWER(?)", (username_clean,))
        exists = cursor.fetchone()
        if exists:
            new_id = exists[0]
            cursor.execute("UPDATE users SET team_name = ? WHERE telegram_id = ?", (club_clean, new_id))
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


# --- Squad management ---

def add_squad(team_name: str, player_names: list[str]) -> int:
    """Add players to a club's squad. Returns count of newly added players."""
    added = 0
    with transaction() as conn:
        cursor = conn.cursor()
        for name in player_names:
            clean = name.strip()
            if not clean or len(clean) > 50:
                continue
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO squad_players (team_name, player_name) VALUES (?, ?)",
                    (team_name.strip(), clean)
                )
                if cursor.rowcount > 0:
                    added += 1
            except sqlite3.Error as e:
                logger.warning(f"Failed to add player '{clean}' to {team_name}: {e}")
    return added


def get_squad(team_name: str) -> list[str]:
    """Get list of player names in a club's squad."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT player_name FROM squad_players WHERE LOWER(team_name) = LOWER(?) ORDER BY id ASC",
            (team_name.strip(),)
        )
        return [row["player_name"] for row in cursor.fetchall()]


def clear_squad(team_name: str) -> int:
    """Remove all players from a club's squad. Returns count of deleted players."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM squad_players WHERE LOWER(team_name) = LOWER(?)",
            (team_name.strip(),)
        )
        return cursor.rowcount


def get_club_top_scorers(team_name: str) -> list[dict]:
    """Get top goal scorers for a club across all matches."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT player_name, SUM(count) as total
            FROM match_events
            WHERE LOWER(team_name) = LOWER(?) AND event_type = 'goal'
            GROUP BY player_name
            ORDER BY total DESC, player_name ASC
        """, (team_name.strip(),))
        return [{"player_name": row["player_name"], "total": row["total"]} for row in cursor.fetchall()]


def clear_all_matches() -> None:
    """Clear all matches from the database."""
    with transaction() as conn:
        conn.cursor().execute("DELETE FROM matches")
        conn.cursor().execute("DELETE FROM rounds")


def batch_insert_matches(fixtures: list[tuple[int, int, int]]) -> None:
    """Insert a list of matches. fixtures format: (round_number, p1, p2)"""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO matches (round_number, player1_id, player2_id, status) VALUES (?, ?, ?, 'pending')",
            fixtures
        )
        rounds = set([f[0] for f in fixtures])
        cursor.executemany(
            "INSERT INTO rounds (round_number, is_open, deadline) VALUES (?, 0, NULL)",
            [(r,) for r in rounds]
        )

def get_round_info(round_number: int) -> dict | None:
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT round_number, is_open, deadline FROM rounds WHERE round_number = ?", (round_number,))
        row = cursor.fetchone()
        return dict(row) if row else None

def update_round_status(round_number: int, is_open: bool, deadline: str | None = None) -> None:
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE rounds SET is_open = ?, deadline = ? WHERE round_number = ?",
            (1 if is_open else 0, deadline, round_number)
        )


def get_all_rounds() -> list[int]:
    """Get a list of all round numbers present in the database."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT round_number FROM matches ORDER BY round_number ASC")
        return [row["round_number"] for row in cursor.fetchall()]

def get_club_top_assisters(team_name: str) -> list[dict]:
    """Get top assist providers for a club across all matches."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT player_name, SUM(count) as total
            FROM match_events
            WHERE LOWER(team_name) = LOWER(?) AND event_type = 'assist'
            GROUP BY player_name
            ORDER BY total DESC, player_name ASC
        """, (team_name.strip(),))
        return [{"player_name": row["player_name"], "total": row["total"]} for row in cursor.fetchall()]


def reset_match(match_id: int) -> bool:
    """Reset a match status back to pending and clear scores/events."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM match_events WHERE match_id = ?", (match_id,))
        cursor.execute(
            "UPDATE matches SET status = 'pending', player1_score = NULL, player2_score = NULL, played_at = NULL WHERE id = ?",
            (match_id,)
        )
        return cursor.rowcount > 0


def set_technical_result(match_id: int, p1_score: int, p2_score: int) -> bool:
    """Set technical result for a match without detailed player events."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM match_events WHERE match_id = ?", (match_id,))
        cursor.execute(
            "UPDATE matches SET status = 'completed', player1_score = ?, player2_score = ?, played_at = CURRENT_TIMESTAMP WHERE id = ?",
            (p1_score, p2_score, match_id)
        )
        return cursor.rowcount > 0


def get_unplayed_matches_in_round(round_number: int) -> list[dict]:
    """Get all unplayed (pending) matches for a specific round with user details."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT m.id, m.round_number,
                   m.player1_id, u1.username as p1_username, u1.team_name as p1_team,
                   m.player2_id, u2.username as p2_username, u2.team_name as p2_team
            FROM matches m
            LEFT JOIN users u1 ON m.player1_id = u1.telegram_id
            LEFT JOIN users u2 ON m.player2_id = u2.telegram_id
            WHERE m.round_number = ? AND m.status != 'completed'
            ORDER BY m.id ASC
        """, (round_number,))
        return [dict(row) for row in cursor.fetchall()]

def get_top_scorers(limit: int = 20) -> list[dict]:
    """Get top goalscorers in the league aggregated from match_events."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT player_name, team_name, SUM(count) AS total_goals
            FROM match_events
            WHERE event_type = 'goal'
            GROUP BY player_name, team_name
            ORDER BY total_goals DESC, player_name ASC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

def get_top_assists(limit: int = 20) -> list[dict]:
    """Get top assist providers in the league aggregated from match_events."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT player_name, team_name, SUM(count) AS total_assists
            FROM match_events
            WHERE event_type = 'assist'
            GROUP BY player_name, team_name
            ORDER BY total_assists DESC, player_name ASC
            LIMIT ?
        """, (limit,))
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
            LEFT JOIN users u1 ON m.player1_id = u1.telegram_id
            LEFT JOIN users u2 ON m.player2_id = u2.telegram_id
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


