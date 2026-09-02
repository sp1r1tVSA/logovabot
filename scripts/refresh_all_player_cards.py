"""
scripts/refresh_all_player_cards.py

Comprehensive Server & Local Synchronization Script for EA FC Player Cards:
1. Scans all active players from squad_players and match_events in league.db.
2. Identifies and re-fetches player photos using prioritized transparent cutouts (TheSportsDB).
3. Clears obsolete Telegram media cache (telegram_media_cache) in league.db so the bot
   guarantees delivering newly rendered, authentic cards.
4. Generates high-resolution preview cards for all players into assets/cards_preview/
   to verify that both full cutouts and headshots render with authentic proportions.
"""

import os
import sys
import sqlite3
import logging
import argparse

# Add project root to sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

# Auto-reexec with virtual environment if running in bare system python without PIL
venv_candidates = [
    os.path.join(BASE_DIR, "venv", "bin", "python3"),
    os.path.join(BASE_DIR, "venv", "bin", "python"),
    os.path.join(BASE_DIR, ".venv", "bin", "python3"),
    os.path.join(BASE_DIR, ".venv", "bin", "python"),
    os.path.join(BASE_DIR, "venv", "Scripts", "python.exe"),
]
for venv_py in venv_candidates:
    if os.path.isfile(venv_py) and os.path.abspath(sys.executable) != os.path.abspath(venv_py):
        try:
            from PIL import Image
        except ImportError:
            os.execv(venv_py, [venv_py] + sys.argv)

try:
    from PIL import Image
except ImportError:
    print(
        "\n❌ Ошибка: Библиотека Pillow (PIL) не найдена в текущем интерпретаторе Python.\n"
        "Бот использует виртуальное окружение. Запустите команду через venv:\n\n"
        "  venv/bin/python3 scripts/refresh_all_player_cards.py\n\n"
        "или активируйте его перед запуском:\n\n"
        "  source venv/bin/activate && python3 scripts/refresh_all_player_cards.py\n"
    )
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("refresh_cards")

import player_photos
import fc_card_generator
import database


def get_all_squad_players(db_path: str = "league.db") -> list[tuple[str, str]]:
    """Retrieve all unique (player_name, team_name) pairs from database."""
    full_db_path = os.path.join(BASE_DIR, db_path) if not os.path.isabs(db_path) else db_path
    if not os.path.exists(full_db_path):
        logger.error(f"Database not found at {full_db_path}")
        return []

    conn = sqlite3.connect(full_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT player_name, team_name FROM squad_players WHERE player_name IS NOT NULL AND player_name != ''
        UNION
        SELECT player_name, team_name FROM match_events WHERE player_name IS NOT NULL AND player_name != ''
        ORDER BY team_name, player_name ASC
    """)
    players = [(row["player_name"], row["team_name"]) for row in cursor.fetchall()]
    conn.close()
    return players


def is_low_quality_or_headshot(photo_path: str) -> bool:
    """Check if cached photo is a low-res headshot (<=250px or aspect >= 0.80)."""
    if not photo_path or not os.path.exists(photo_path):
        return True
    try:
        im = Image.open(photo_path)
        w, h = im.size
        if h <= 250 or w <= 250:
            return True
        bbox = im.getbbox() if "A" in im.getbands() else None
        if bbox:
            bw = bbox[2] - bbox[0]
            bh = bbox[3] - bbox[1]
            aspect = bw / float(bh) if bh > 0 else 1.0
            if aspect >= 0.80 and bh <= 300:
                return True
        return False
    except Exception:
        return True


def refresh_photos(players: list[tuple[str, str]], force_all: bool = False) -> tuple[int, int, int]:
    """
    Re-download player photos with TheSportsDB prioritized for authentic cutouts.
    Returns: (cutouts_downloaded, headshots_kept, missing_count)
    """
    logger.info("=== 1. Checking and refreshing player photos ===")
    cutouts = 0
    headshots = 0
    missing = 0

    for name, team in players:
        curr_path = player_photos.get_photo_path(name, team)
        should_refetch = force_all or is_low_quality_or_headshot(curr_path)

        if should_refetch:
            logger.info(f"Refetching photo for '{name}' ({team})...")
            new_path = player_photos.fetch_and_cache(name, team, force_refresh=True)
            if new_path and os.path.exists(new_path):
                if is_low_quality_or_headshot(new_path):
                    headshots += 1
                    logger.info(f"  -> Headshot saved: {os.path.basename(new_path)}")
                else:
                    cutouts += 1
                    logger.info(f"  -> High-res Cutout saved: {os.path.basename(new_path)}")
            else:
                missing += 1
                logger.warning(f"  -> No photo found for '{name}'")
        else:
            cutouts += 1

    return cutouts, headshots, missing


def clear_telegram_media_cache(db_path: str = "league.db") -> int:
    """
    Clear obsolete telegram_media_cache entries so the bot will re-upload
    and re-render fresh cards instead of using old cached file_ids.
    """
    logger.info("=== 2. Purging Telegram media cache in database ===")
    full_db_path = os.path.join(BASE_DIR, db_path) if not os.path.isabs(db_path) else db_path
    if not os.path.exists(full_db_path):
        logger.warning(f"Database {full_db_path} does not exist.")
        return 0

    conn = sqlite3.connect(full_db_path)
    cursor = conn.cursor()
    deleted_count = 0

    try:
        cursor.execute("SELECT COUNT(*) FROM telegram_media_cache")
        before_count = cursor.fetchone()[0]
        cursor.execute("DELETE FROM telegram_media_cache")
        conn.commit()
        deleted_count = before_count
        logger.info(f"✅ Cleared {deleted_count} cached media items from telegram_media_cache.")
    except Exception as e:
        logger.error(f"Error clearing telegram_media_cache: {e}")
    finally:
        conn.close()

    return deleted_count


def generate_all_preview_cards(players: list[tuple[str, str]], output_dir: str = "assets/cards_preview") -> int:
    """
    Render static EA FC cards for all players into preview directory
    to ensure full visual validation.
    """
    logger.info("=== 3. Generating visual preview cards ===")
    full_out_dir = os.path.join(BASE_DIR, output_dir)
    os.makedirs(full_out_dir, exist_ok=True)

    generated_count = 0
    for name, team in players:
        # Get player stats from DB if available
        stats = database.get_player_stats_for_card(name, team) if hasattr(database, "get_player_stats_for_card") else {}
        if not stats:
            stats = {
                "player_name": name,
                "team_name": team,
                "ovr": 88,
                "position": "ST"
            }

        ovr = stats.get("ovr", 88)
        tier = fc_card_generator.get_kpl_tier_by_ovr(ovr)

        try:
            buf = fc_card_generator.generate_ea_fc_card(stats, theme_name=tier)
            slug = f"{name}_{team}".lower().replace(" ", "_")
            out_path = os.path.join(full_out_dir, f"{slug}_{tier}.png")
            with open(out_path, "wb") as f:
                f.write(buf.getvalue())
            generated_count += 1
        except Exception as e:
            logger.error(f"Failed to generate preview for '{name}' ({team}): {e}")

    logger.info(f"✅ Generated {generated_count} preview cards in {full_out_dir}")
    return generated_count


def main():
    parser = argparse.ArgumentParser(description="Refresh EA FC player cards, photos, and DB media cache.")
    parser.add_argument("--force-photos", action="store_true", help="Force re-download all player photos")
    parser.add_argument("--skip-previews", action="store_true", help="Skip generating preview PNG cards")
    parser.add_argument("--db-path", default="league.db", help="Path to SQLite database")
    args = parser.parse_args()

    players = get_all_squad_players(args.db_path)
    logger.info(f"Found {len(players)} total unique squad players in database.")

    if not players:
        logger.warning("No players found. Exiting.")
        return

    # 1. Photos
    cutouts, headshots, missing = refresh_photos(players, force_all=args.force_photos)

    # 2. Database Cache
    cleared = clear_telegram_media_cache(args.db_path)

    # 3. Card Previews
    previews = 0
    if not args.skip_previews:
        previews = generate_all_preview_cards(players)

    logger.info("==========================================")
    logger.info("🎉 CARD REFRESH COMPLETE!")
    logger.info(f"• Total Players: {len(players)}")
    logger.info(f"• Cutouts / High-res: {cutouts}")
    logger.info(f"• Headshots (with jersey silhouette): {headshots}")
    logger.info(f"• Missing photos: {missing}")
    logger.info(f"• Database cache cleared: {cleared} entries")
    logger.info(f"• Previews generated: {previews}")
    logger.info("==========================================")


if __name__ == "__main__":
    main()
