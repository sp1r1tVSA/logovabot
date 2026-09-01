"""
scripts/sync_server_player_positions.py

Universal Server & Local Squad Position Synchronization & Code Extractor.
1. Extracts all players across squad_players, match_events, and assets/players.
2. Resolves their real-world authentic football positions (ST, LW, RW, CAM, CM, CDM, CB, LB, RB, GK).
3. Updates the database (league.db) with the detected positions.
4. Generates/updates the KNOWN_PLAYER_POSITIONS registry in services/player_positions.py.
"""

import os
import sys
import sqlite3
import logging
import argparse

# Add project root to sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

from services.player_positions import (
    detect_player_position,
    normalize_position,
    KNOWN_PLAYER_POSITIONS
)


def get_all_players_from_db(db_path: str = "league.db") -> list[tuple[str, str]]:
    """Retrieve all (team_name, player_name) pairs from DB."""
    if not os.path.exists(db_path):
        logger.warning(f"Database {db_path} not found.")
        return []

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    players = set()

    try:
        cursor.execute("SELECT team_name, player_name FROM squad_players")
        for row in cursor.fetchall():
            if row[0] and row[1]:
                players.add((row[0].strip(), row[1].strip()))
    except Exception as e:
        logger.debug(f"Error reading squad_players: {e}")

    try:
        cursor.execute("SELECT team_name, player_name FROM match_events")
        for row in cursor.fetchall():
            if row[0] and row[1]:
                players.add((row[0].strip(), row[1].strip()))
    except Exception as e:
        logger.debug(f"Error reading match_events: {e}")

    conn.close()
    return sorted(list(players))


def get_players_from_assets(assets_dir: str = "assets/players") -> list[tuple[str, str]]:
    """Extract player and team names from assets/players/*.png filenames."""
    full_path = os.path.join(BASE_DIR, assets_dir)
    if not os.path.exists(full_path):
        return []

    results = []
    for fn in os.listdir(full_path):
        if not fn.endswith(".png"):
            continue
        base = fn[:-4]
        # Format usually: name_club.png or name.png
        parts = base.split("_")
        if len(parts) >= 2:
            club = parts[-1].capitalize()
            p_name = " ".join(parts[:-1]).title()
            results.append((club, p_name))
        else:
            results.append(("—", base.replace("_", " ").title()))
    return results


def sync_positions(db_path: str = "league.db", update_code: bool = True) -> dict[str, str]:
    """
    Main runner: resolves positions for all server players and updates DB & Code.
    """
    db_full_path = os.path.join(BASE_DIR, db_path) if not os.path.isabs(db_path) else db_path
    
    # 1. Collect all players
    db_players = get_all_players_from_db(db_full_path)
    asset_players = get_players_from_assets()

    all_pairs = set(db_players) | set(asset_players)
    logger.info(f"Collected {len(all_pairs)} unique player-club entries.")

    resolved_map: dict[str, str] = {}
    db_updates = []

    # 2. Resolve positions
    for team, player in all_pairs:
        pos = detect_player_position(player, team)
        resolved_map[player] = pos
        db_updates.append((pos, team, player))
        logger.info(f"⚽ [{pos:3s}] {player} ({team})")

    # 3. Update SQLite database
    if os.path.exists(db_full_path) and db_updates:
        try:
            conn = sqlite3.connect(db_full_path)
            cursor = conn.cursor()
            
            # Ensure column exists
            cursor.execute("PRAGMA table_info(squad_players)")
            cols = [c[1] for c in cursor.fetchall()]
            if "position" not in cols:
                cursor.execute("ALTER TABLE squad_players ADD COLUMN position TEXT")
                
            for pos, team, player in db_updates:
                cursor.execute(
                    """
                    INSERT INTO squad_players (team_name, player_name, position)
                    VALUES (?, ?, ?)
                    ON CONFLICT(team_name, player_name) DO UPDATE SET position = excluded.position
                    """,
                    (team, player, pos)
                )
            conn.commit()
            conn.close()
            logger.info(f"✅ Successfully updated {len(db_updates)} records in {db_path}!")
        except Exception as e:
            logger.error(f"Failed to update database: {e}")

    # 4. Update services/player_positions.py if requested
    if update_code:
        _update_code_registry(resolved_map)

    return resolved_map


def _update_code_registry(new_entries: dict[str, str]) -> None:
    """Safely append or update resolved positions in services/player_positions.py."""
    pos_file = os.path.join(BASE_DIR, "services", "player_positions.py")
    if not os.path.exists(pos_file):
        logger.warning(f"{pos_file} not found.")
        return

    with open(pos_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Build updated dictionary entries
    entries_list = []
    for player_name, pos in sorted(new_entries.items()):
        clean_key = player_name.lower().strip()
        entries_list.append(f'    "{clean_key}": "{pos}",')

    # Find KNOWN_PLAYER_POSITIONS block
    marker = "KNOWN_PLAYER_POSITIONS: dict[str, str] = {"
    if marker in content:
        # Check if already present, if not inject at top of dict
        to_inject = "\n    # ── Auto-synced Club Players from Server DB ──\n"
        for player_name, pos in sorted(new_entries.items()):
            key = player_name.lower().strip()
            if f'"{key}":' not in content:
                to_inject += f'    "{key}": "{pos}",\n'

        if to_inject.strip() != "# ── Auto-synced Club Players from Server DB ──":
            new_content = content.replace(marker, marker + to_inject)
            with open(pos_file, "w", encoding="utf-8") as f:
                f.write(new_content)
            logger.info("✅ Updated services/player_positions.py with newly discovered players!")
        else:
            logger.info("ℹ️ All discovered players already exist in services/player_positions.py.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync server squad player positions.")
    parser.add_argument("--db", default="league.db", help="Path to SQLite database")
    parser.add_argument("--no-code-update", action="store_true", help="Do not modify services/player_positions.py")
    args = parser.parse_args()

    sync_positions(db_path=args.db, update_code=not args.no_code_update)
