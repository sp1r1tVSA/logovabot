import logging
import sqlite3
import datetime
import re
import difflib
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
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_tourn ON matches(tournament_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_match ON match_events(match_id)")

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
        # Standardize and migrate canonical team names across all tables
        migrate_team_names_canonical(cursor)

        logger.info("Database tables initialized successfully.")


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
        
    a_norm = normalize_team_name(team_a)
    b_norm = normalize_team_name(team_b)
    if not a_norm or not b_norm:
        return False
    if a_norm == b_norm or a_norm in b_norm or b_norm in a_norm:
        return True
        
    # Fuzzy ratio check
    if difflib.SequenceMatcher(None, a_norm, b_norm).ratio() >= 0.70:
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
            SELECT telegram_id, username, team_name, league_name, role, registered_at, squad_photo_id, warn_count FROM users WHERE telegram_id = ?
        """, (telegram_id,))
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

def get_pending_matches(telegram_id: int, only_expired_deadlines: bool = False) -> list[dict]:
    """Retrieve active matches for a user from Round 1 up to the highest OPEN round number, plus active Cup matches.

    When only_expired_deadlines is True, league matches are restricted to rounds whose
    deadline has already passed (used for debt reminders). Cup matches are never filtered.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT team_name FROM users WHERE telegram_id = ?", (telegram_id,))
        u_row = cursor.fetchone()
        u_team = u_row["team_name"] if u_row and u_row["team_name"] else ""

        # Get highest open round number
        cursor.execute("SELECT MAX(round_number) FROM rounds WHERE is_open = 1")
        max_open_row = cursor.fetchone()
        max_open_round = max_open_row[0] if max_open_row and max_open_row[0] is not None else 0

        # Collect round numbers whose deadline has already passed (for debt filtering)
        expired_rounds: set[int] = set()
        if only_expired_deadlines:
            now = datetime.datetime.now()
            cursor.execute("SELECT round_number, deadline FROM rounds WHERE is_open = 1")
            for r_num, dl_str in cursor.fetchall():
                if not dl_str:
                    continue
                try:
                    dl_dt = datetime.datetime.strptime(dl_str, "%d.%m.%Y %H:%M")
                except ValueError:
                    continue
                if dl_dt <= now:
                    expired_rounds.add(r_num)

        league_condition = (
            "(m.tournament_type IS NULL OR m.tournament_type = 'league') "
            f"AND m.round_number IN ({','.join('?' * len(expired_rounds))}) "
            "AND m.status IN ('pending', 'reported', 'disputed')"
            if only_expired_deadlines and expired_rounds
            else (
                "(m.tournament_type IS NULL OR m.tournament_type = 'league') "
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

def get_standings() -> list[dict]:
    """Calculate the standings of all registered players dynamically."""
    with transaction() as conn:
        cursor = conn.cursor()
        
        from config import KPL_TEAMS
        
        # Get current user info for all teams
        cursor.execute("SELECT telegram_id, team_name, username FROM users WHERE team_name IS NOT NULL AND team_name != ''")
        all_users = cursor.fetchall()
        
        teams = {}
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

        # Get all confirmed matches (League matches only)
        cursor.execute("""
            SELECT 
                COALESCE(m.player1_team, u1.team_name) AS player1_team, 
                COALESCE(m.player2_team, u2.team_name) AS player2_team, 
                m.player1_score, 
                m.player2_score 
            FROM matches m
            LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
            LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
            WHERE m.status = 'confirmed' AND (m.tournament_type IS NULL OR m.tournament_type = 'league')
        """)
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
                m.id, m.round_number, u1.telegram_id AS player1_id, u2.telegram_id AS player2_id,
                m.player1_score, m.player2_score, m.status, m.played_at, m.is_extended,
                m.photo_id, m.dispute_photos, m.reported_by,
                m.proposed_time, m.proposed_by, m.time_status,
                m.tournament_type, m.cup_stage, m.cup_series_id, m.game_num_in_series,
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
        return d

def confirm_and_finalize_match(match_id: int, p1_score: int, p2_score: int, events: list, reporter_id: int = None, photo_id: str = None) -> str | None:
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
    return process_cup_match_completion(match_id)

def set_technical_result(match_id: int, p1_score: int, p2_score: int) -> str | None:
    """Set technical result for match and update cup series if applicable."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE matches SET player1_score = ?, player2_score = ?, status = 'confirmed', played_at = CURRENT_TIMESTAMP WHERE id = ?",
            (p1_score, p2_score, match_id)
        )
        cursor.execute("DELETE FROM match_events WHERE match_id = ?", (match_id,))
    return process_cup_match_completion(match_id)

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
        cursor.execute("DELETE FROM match_events WHERE match_id = ?", (match_id,))

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

def get_active_match_by_teams(team1: str, team2: str, caption: str | None = None) -> dict | None:
    """Find an active (pending/reported/disputed) match given two team names and optional caption."""
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
                score = 0
                t_type = d.get('tournament_type') or 'league'
                is_pending = d['status'] in ('pending', 'reported', 'disputed')
                is_technical = (d['status'] == 'confirmed' and d.get('events_count', 0) == 0)
                
                # Check if deadline is expired for open rounds (Case 1)
                is_deadline_expired = False
                dl_str = d.get('round_deadline')
                if dl_str:
                    try:
                        dl_dt = datetime.datetime.strptime(dl_str, "%d.%m.%Y %H:%M")
                        if dl_dt <= now:
                            is_deadline_expired = True
                    except ValueError:
                        pass
                
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

def get_unplayed_matches_by_round(round_number: int) -> list[dict]:
    """Retrieve unplayed (pending) matches in a specific round with player details."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                m.id, m.round_number, u1.telegram_id AS player1_id, u2.telegram_id AS player2_id, m.status,
                u1.username AS player1_username, u1.team_name AS player1_team,
                u2.username AS player2_username, u2.team_name AS player2_team
            FROM matches m
            LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
            LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
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

def get_teams_recent_form(limit: int = 5) -> dict[str, list[str]]:
    """
    Retrieve the last `limit` confirmed match outcomes for each team by team_name.
    Returns dict mapping lowercase team_name -> list of 'W', 'D', 'L' outcomes.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        from config import KPL_TEAMS
        cursor.execute("""
            SELECT player1_team, player2_team, player1_score, player2_score
            FROM matches
            WHERE status = 'confirmed' AND (tournament_type IS NULL OR tournament_type = 'league')
            ORDER BY round_number DESC, id DESC
        """)
        all_matches = cursor.fetchall()

        form_map = {}
        for t in KPL_TEAMS:
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
    if player1_id == player2_id:
        raise ValueError("Игрок не может играть сам с собой (player1_id == player2_id).")
        
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT team_name FROM users WHERE telegram_id IN (?, ?)", (player1_id, player2_id))
        teams = [r[0] for r in cursor.fetchall() if r[0]]
        if len(teams) == 2 and teams[0].lower() == teams[1].lower():
            raise ValueError(f"Команды участников совпадают: {teams[0]}")

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

def admin_set_match_score(match_id: int, player1_score: int, player2_score: int) -> None:
    """Manually set match score and confirm it by admin, clearing any previous match events."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM match_events WHERE match_id = ?", (match_id,))
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


def open_rounds_batch(start_round: int, end_round: int, deadline: str) -> None:
    """Open multiple rounds and set a shared deadline."""
    with transaction() as conn:
        cursor = conn.cursor()
        for r_num in range(start_round, end_round + 1):
            cursor.execute(
                "INSERT OR IGNORE INTO rounds (round_number, is_open, deadline) VALUES (?, 0, NULL)",
                (r_num,)
            )
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
                m.id, m.round_number, u1.telegram_id AS player1_id, u2.telegram_id AS player2_id, m.status, m.is_extended,
                u1.username AS player1_nickname, u1.team_name AS player1_team,
                u2.username AS player2_nickname, u2.team_name AS player2_team,
                r.deadline
            FROM matches m
            JOIN rounds r ON m.round_number = r.round_number
            LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
            LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
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
                m.id, m.round_number, u1.telegram_id AS player1_id, u2.telegram_id AS player2_id,
                m.player1_score, m.player2_score, m.status,
                u1.username AS player1_nickname, u1.team_name AS player1_team,
                u2.username AS player2_nickname, u2.team_name AS player2_team
            FROM matches m
            LEFT JOIN users u1 ON LOWER(m.player1_team) = LOWER(u1.team_name)
            LEFT JOIN users u2 ON LOWER(m.player2_team) = LOWER(u2.team_name)
            WHERE m.round_number = ?
            ORDER BY m.id ASC
        """, (round_number,))
        return [dict(row) for row in cursor.fetchall()]

def reset_match(match_id: int) -> None:
    """Reset a match result, setting status back to 'pending' and clearing scores and events."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM match_events WHERE match_id = ?", (match_id,))
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
        
        # Unlink player from matches instead of deleting them to preserve league history
        cursor.execute("UPDATE matches SET player1_id = NULL WHERE player1_id = ?", (p_id,))
        cursor.execute("UPDATE matches SET player2_id = NULL WHERE player2_id = ?", (p_id,))
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


def get_user_warn_count(user_id: int) -> int:
    """Get current warn count for user."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT warn_count FROM users WHERE telegram_id = ?", (user_id,))
        row = cursor.fetchone()
        return row["warn_count"] if row and row["warn_count"] is not None else 0



def add_warn(user_id: int, admin_id: int, reason: str) -> tuple[int, bool]:
    from config import MAX_WARNS_LIMIT
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT warn_count FROM users WHERE telegram_id = ?", (user_id,))
        row = cursor.fetchone()
        current_count = row["warn_count"] if row and row["warn_count"] is not None else 0
        new_count = current_count + 1
        
        cursor.execute("UPDATE users SET warn_count = ? WHERE telegram_id = ?", (new_count, user_id))
        cursor.execute(
            "INSERT INTO user_warns (user_id, admin_id, reason, type) VALUES (?, ?, ?, 'WARN_ADD')",
            (user_id, admin_id, reason)
        )
        is_exceeded = new_count >= MAX_WARNS_LIMIT
        return new_count, is_exceeded


def remove_warn(user_id: int, admin_id: int, reason: str) -> tuple[int, bool]:
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT warn_count FROM users WHERE telegram_id = ?", (user_id,))
        row = cursor.fetchone()
        current_count = row["warn_count"] if row and row["warn_count"] is not None else 0
        
        if current_count <= 0:
            return 0, False
            
        new_count = max(0, current_count - 1)
        cursor.execute("UPDATE users SET warn_count = ? WHERE telegram_id = ?", (new_count, user_id))
        cursor.execute(
            "INSERT INTO user_warns (user_id, admin_id, reason, type) VALUES (?, ?, ?, 'WARN_REMOVE')",
            (user_id, admin_id, reason)
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
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT team_name FROM users WHERE telegram_id = ?", (user_id,))
        row = cursor.fetchone()
        team_name = row["team_name"] if row else None
        
        cursor.execute("UPDATE users SET team_name = NULL WHERE telegram_id = ?", (user_id,))
        cursor.execute(
            "INSERT INTO user_warns (user_id, admin_id, reason, type) VALUES (?, NULL, 'Превышен лимит варнов (4/4). Авто-удаление из клуба.', 'AUTO_KICK')",
            (user_id,)
        )
        return team_name


def reset_season_warns() -> None:
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET warn_count = 0")
        cursor.execute("DELETE FROM user_warns")


def amnesty_player(user_id: int, admin_id: int) -> None:
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET warn_count = 0 WHERE telegram_id = ?", (user_id,))
        cursor.execute(
            "INSERT INTO user_warns (user_id, admin_id, reason, type) VALUES (?, ?, 'Амнистия (сброс варнов)', 'WARN_REMOVE')",
            (user_id, admin_id)
        )


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
    """Retrieve all users with warn_count > 0."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT telegram_id, username, team_name, warn_count 
            FROM users 
            WHERE warn_count > 0 
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
    valid_fixtures = [f for f in fixtures if f[1] != f[2]]
    if not valid_fixtures:
        return
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO matches (round_number, player1_id, player2_id, status) VALUES (?, ?, ?, 'pending')",
            valid_fixtures
        )
        rounds = set([f[0] for f in valid_fixtures])
        cursor.executemany(
            "INSERT OR IGNORE INTO rounds (round_number, is_open, deadline) VALUES (?, 0, NULL)",
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
        cursor.execute("SELECT DISTINCT round_number FROM matches WHERE round_number > 0 ORDER BY round_number ASC")
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
            "UPDATE matches SET status = 'confirmed', player1_score = ?, player2_score = ?, played_at = CURRENT_TIMESTAMP WHERE id = ?",
            (p1_score, p2_score, match_id)
        )
        res = cursor.rowcount > 0
    process_cup_match_completion(match_id)
    return res


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
    - total goals and assists
    - breakdown by round (goals + assists per round)
    Only considers confirmed matches.
    """
    with transaction() as conn:
        cursor = conn.cursor()

        # Total goals
        cursor.execute("""
            SELECT COALESCE(SUM(me.count), 0) AS total
            FROM match_events me
            JOIN matches m ON me.match_id = m.id
            WHERE LOWER(me.player_name) = LOWER(?)
              AND LOWER(me.team_name) = LOWER(?)
              AND me.event_type = 'goal'
              AND m.status = 'confirmed'
        """, (player_name.strip(), team_name.strip()))
        total_goals = cursor.fetchone()["total"]

        # Total assists
        cursor.execute("""
            SELECT COALESCE(SUM(me.count), 0) AS total
            FROM match_events me
            JOIN matches m ON me.match_id = m.id
            WHERE LOWER(me.player_name) = LOWER(?)
              AND LOWER(me.team_name) = LOWER(?)
              AND me.event_type = 'assist'
              AND m.status = 'confirmed'
        """, (player_name.strip(), team_name.strip()))
        total_assists = cursor.fetchone()["total"]

        # Per-round breakdown
        cursor.execute("""
            SELECT
                m.round_number,
                me.event_type,
                SUM(me.count) AS total
            FROM match_events me
            JOIN matches m ON me.match_id = m.id
            WHERE LOWER(me.player_name) = LOWER(?)
              AND LOWER(me.team_name) = LOWER(?)
              AND m.status = 'confirmed'
            GROUP BY m.round_number, me.event_type
            ORDER BY m.round_number ASC
        """, (player_name.strip(), team_name.strip()))

        rounds_raw = cursor.fetchall()

        # Build a dict: {round_number: {"goals": n, "assists": n}}
        rounds: dict[int, dict] = {}
        for row in rounds_raw:
            rn = row["round_number"]
            if rn not in rounds:
                rounds[rn] = {"goals": 0, "assists": 0}
            if row["event_type"] == "goal":
                rounds[rn]["goals"] = row["total"]
            elif row["event_type"] == "assist":
                rounds[rn]["assists"] = row["total"]

        return {
            "player_name": player_name,
            "team_name": team_name,
            "total_goals": total_goals,
            "total_assists": total_assists,
            "rounds": rounds,  # {round_number: {"goals": n, "assists": n}}
        }


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
            WHERE me.event_type = 'goal' AND m.tournament_type = 'cup'
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
            WHERE me.event_type = 'assist' AND m.tournament_type = 'cup'
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


def get_all_unplayed_league_matches() -> list[dict]:
    """Retrieve all pending league matches in open rounds or past rounds with expired deadlines."""
    with transaction() as conn:
        cursor = conn.cursor()
        now = datetime.datetime.now()

        cursor.execute("SELECT round_number, is_open, deadline FROM rounds")
        rounds_rows = cursor.fetchall()

        round_info_map: dict[int, dict] = {}
        for r_num, is_open, dl_str in rounds_rows:
            parsed_dl = parse_flexible_datetime(dl_str)
            round_info_map[r_num] = {
                "is_open": bool(is_open),
                "deadline_dt": parsed_dl
            }

        cursor.execute("""
            SELECT 
                m.id, m.round_number, m.player1_team, m.player2_team
            FROM matches m
            WHERE (m.tournament_type IS NULL OR m.tournament_type = 'league')
              AND m.status = 'pending'
            ORDER BY m.round_number ASC, m.id ASC
        """)
        matches = [dict(row) for row in cursor.fetchall()]

        cursor.execute("SELECT telegram_id, username, team_name FROM users WHERE team_name IS NOT NULL")
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

            # Include ONLY if round is currently open OR has an explicit passed deadline
            # Future unopened rounds (is_open=0 and deadline is None) are ignored
            if is_open or (dl_dt and dl_dt <= now):
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
        cursor.execute(
            "INSERT OR IGNORE INTO debt_reminders (match_id, stage, sent_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (match_id, stage)
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
        cursor.execute("""
            INSERT INTO debt_reminders (match_id, stage, sent_at)
            VALUES (?, 'cycle_reminder_last', CURRENT_TIMESTAMP)
            ON CONFLICT(match_id, stage) DO UPDATE SET sent_at = CURRENT_TIMESTAMP
        """, (match_id,))


def get_last_debt_12h_reminder(match_id: int) -> datetime.datetime | None:
    """Get datetime when last cycle reminder was sent for a match."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT sent_at FROM debt_reminders WHERE match_id = ? AND stage = 'cycle_reminder_last'", (match_id,))
        row = cursor.fetchone()
        if not row or not row["sent_at"]:
            return None
        return parse_flexible_datetime(row["sent_at"])


def get_detailed_overdue_matches() -> list[dict]:
    """
    Retrieve all pending league matches that are legitimately overdue:
    - Round is currently open (is_open = 1) AND has an expired deadline (deadline_dt <= now).
    - Or round is a past round with an explicitly recorded expired deadline.
    - NEVER includes future unopened rounds (is_open = 0 and deadline IS NULL).
    - Club participants are strictly resolved from current owners in users table.
    """
    with transaction() as conn:
        cursor = conn.cursor()
        now = datetime.datetime.now()
        start_dt = get_debt_tracking_start_datetime()

        # 1. Fetch rounds status
        cursor.execute("SELECT round_number, is_open, deadline FROM rounds")
        rounds_rows = cursor.fetchall()

        round_info_map: dict[int, dict] = {}
        for r_num, is_open, dl_str in rounds_rows:
            parsed_dl = parse_flexible_datetime(dl_str)
            round_info_map[r_num] = {
                "is_open": bool(is_open),
                "deadline_str": dl_str,
                "deadline_dt": parsed_dl
            }

        # 2. Fetch pending league matches
        cursor.execute("""
            SELECT 
                m.id, m.round_number, COALESCE(m.is_extended, 0) AS is_extended,
                m.player1_team, m.player2_team
            FROM matches m
            WHERE (m.tournament_type IS NULL OR m.tournament_type = 'league')
              AND m.status = 'pending'
            ORDER BY m.round_number ASC, m.id ASC
        """)
        matches = [dict(row) for row in cursor.fetchall()]

        # 3. Load all active users mapped by team
        cursor.execute("SELECT telegram_id, username, team_name, warn_count FROM users WHERE team_name IS NOT NULL")
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
            if not r_info:
                continue

            dl_dt = r_info.get("deadline_dt")
            is_open = r_info.get("is_open", False)

            # CRITICAL: A round is ONLY overdue if:
            # 1. is_open == True and dl_dt is set and dl_dt <= now
            # 2. Or is_open == True and dl_dt is None and now >= start_dt
            # 3. Or dl_dt is set in the past (past round with explicit deadline)
            # Future rounds (is_open == False and deadline is NULL) are NEVER overdue!
            is_overdue = False
            if dl_dt and dl_dt <= now:
                is_overdue = True
            elif is_open and dl_dt is None:
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

            m["deadline_str"] = r_info.get("deadline_str") or (start_dt.strftime("%d.%m.%Y %H:%M") if start_dt else "—")
            m["deadline_dt"] = effective_dl
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
    """Check if a match is overdue."""
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT round_number FROM matches WHERE id = ?", (match_id,))
        row = cursor.fetchone()
        if not row:
            return False
        rn = row["round_number"]

        cursor.execute("SELECT is_open, deadline FROM rounds WHERE round_number = ?", (rn,))
        r_row = cursor.fetchone()
        if not r_row:
            return False

        dl_dt = parse_flexible_datetime(r_row["deadline"])
        is_open = bool(r_row["is_open"])
        start_dt = get_debt_tracking_start_datetime()
        now = datetime.datetime.now()

        if dl_dt:
            effective_dl = dl_dt
            if start_dt and dl_dt < start_dt:
                effective_dl = start_dt
            return now >= effective_dl

        if is_open and start_dt:
            return now >= start_dt

        return False


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
            ORDER BY created_at DESC LIMIT 1
        """, (user_id,))
        row = cursor.fetchone()
        if not row or not row["created_at"]:
            return False
        warn_dt = parse_flexible_datetime(row["created_at"])
        if not warn_dt:
            return False
        return abs((datetime.datetime.now() - warn_dt).total_seconds()) < hours * 3600.0



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
        cursor.execute("INSERT INTO user_warns (user_id, admin_id, reason, type) VALUES (?, NULL, 'Восстановление клуба и сброс варнов', 'RESTORE')", (telegram_id,))



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
        cursor.execute(
            "INSERT INTO user_warns (user_id, admin_id, reason, type) VALUES (?, NULL, ?, 'DEBT_UNWARN')",
            (user_id, f"Снятие варна за закрытие долга ({round_number} тур)")
        )
        return new_warns, True





