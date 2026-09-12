"""
scripts/refresh_all_player_cards.py

Comprehensive Server & Local Synchronization Script for EA FC Player Cards:
1. Scans all active players from squad_players and match_events in league.db.
2. Identifies and re-fetches player photos using prioritized transparent cutouts (TheSportsDB).
3. Удаляет из кэша `assets/players/` мусор, до которого проход по БД не доходит:
   слаги от прежнего написания имени и плохие файлы без клуба, затенённые хорошими.
4. Clears obsolete Telegram media cache (telegram_media_cache) in league.db so the bot
   guarantees delivering newly rendered, authentic cards.
5. Generates high-resolution preview cards for all players into assets/cards_preview/
   to verify that both full cutouts and headshots render with authentic proportions.

Настоящая база проекта живёт на сервере, поэтому осмысленный прогон делается там:
на пустой локальной БД скрипт честно сообщает, что игроков нет, и ничего не трогает.

    python scripts/refresh_all_player_cards.py --dry-run     # посмотреть план
    python scripts/refresh_all_player_cards.py --skip-previews
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

from services.graphics import player_photos
from services.graphics import fc_card_generator
import database

# Что в каталоге кэша считается фотографией игрока.
PHOTO_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


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
    """
    Check if cached photo is a low-res headshot (<=250px or aspect >= 0.80).

    Отсутствие прозрачности сюда намеренно не входит: `fetch_and_cache` сначала
    обходит всех провайдеров в поиске вырезки и пишет плоскую картинку только
    если её ни у кого нет. Считать такой файл браком значило бы перекачивать его
    каждый прогон с тем же результатом.
    """
    if not photo_path or not os.path.exists(photo_path):
        return True
    try:
        im = Image.open(photo_path)
        w, h = im.size
        if h <= 250 or w <= 250:
            return True
        bbox = im.getbbox()
        if bbox:
            bw = bbox[2] - bbox[0]
            bh = bbox[3] - bbox[1]
            aspect = bw / float(bh) if bh > 0 else 1.0
            if aspect >= 0.80 and bh <= 300:
                return True
        return False
    except Exception:
        return True


def refresh_photos(
    players: list[tuple[str, str]], force_all: bool = False, dry_run: bool = False
) -> tuple[int, int, int, int]:
    """
    Re-download player photos with TheSportsDB prioritized for authentic cutouts.
    Returns: (cutouts_downloaded, headshots_kept, missing_count, planned_count)
    """
    logger.info("=== 1. Checking and refreshing player photos ===")
    cutouts = 0
    headshots = 0
    missing = 0
    planned = 0

    for name, team in players:
        curr_path = player_photos.get_photo_path(name, team)
        should_refetch = force_all or is_low_quality_or_headshot(curr_path)

        if should_refetch and dry_run:
            planned += 1
            logger.info(f"  [dry-run] would refetch '{name}' ({team})")
        elif should_refetch:
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

    return cutouts, headshots, missing, planned


def _expected_cache_paths(players: list[tuple[str, str]]) -> set[str]:
    """
    Канонические пути кэша для игроков из БД.

    Для каждого игрока их два: со клубом и без. Оба легитимны — `get_photo_path`
    читает вариант без клуба как запасной, и часть вызовов клуб не передаёт.
    """
    expected = set()
    for name, team in players:
        expected.add(os.path.normcase(player_photos.get_cached_photo_path(name, team)))
        expected.add(os.path.normcase(player_photos.get_cached_photo_path(name, None)))
    return expected


def _iter_cached_photos() -> list[str]:
    """Файлы фотографий в кэше. Служебные файлы вроде `_name_map.json` пропускаются."""
    cache_dir = player_photos.PHOTOS_DIR
    if not os.path.isdir(cache_dir):
        return []
    return [
        os.path.join(cache_dir, entry)
        for entry in sorted(os.listdir(cache_dir))
        if not entry.startswith("_") and entry.lower().endswith(PHOTO_EXTENSIONS)
    ]


def _drop_cached_photo(path: str, reason: str, dry_run: bool) -> int:
    """Удаляет один файл кэша. Возвращает 1, если файл убран (или был бы убран)."""
    cache_dir = os.path.abspath(player_photos.PHOTOS_DIR)
    target = os.path.abspath(path)
    if os.path.dirname(target) != cache_dir:
        logger.error(f"  refusing to delete outside the photo cache: {path}")
        return 0

    name = os.path.basename(target)
    if dry_run:
        logger.info(f"  [dry-run] would remove {name} ({reason})")
        return 1
    try:
        os.remove(target)
        logger.info(f"  removed {name} ({reason})")
        return 1
    except OSError as e:
        logger.error(f"  could not remove {name}: {e}")
        return 0


def clean_photo_cache(players: list[tuple[str, str]], dry_run: bool = False) -> int:
    """
    Убирает из кэша файлы, до которых проход по БД не доходит:

    * **сироты** — слаги, которых больше нет ни у одного игрока в БД: остатки от
      прежнего написания имени («gyokeres» после переименования в «Viktor Gyökeres»);
    * **затенённые** — плохой файл без клуба при хорошем файле с клубом. Проход по
      БД перезаписывает только вариант с клубом, а вариант без клуба продолжают
      читать вызовы, которые клуб не передают.

    Работает целиком по диску. Пустой список игроков означает, что сверять не с чем:
    тогда не удаляем ничего, иначе сиротами оказался бы весь кэш.
    """
    logger.info("=== 2. Cleaning stale photo cache ===")
    if not os.path.isdir(player_photos.PHOTOS_DIR):
        logger.info("Photo cache directory does not exist yet, nothing to clean.")
        return 0
    if not players:
        logger.warning("No players to compare against — skipping cleanup to avoid wiping the cache.")
        return 0

    expected = _expected_cache_paths(players)
    removed = 0

    for path in _iter_cached_photos():
        if os.path.normcase(path) not in expected:
            removed += _drop_cached_photo(path, "orphan slug", dry_run)

    for name, team in players:
        if not team:
            continue
        with_team = player_photos.get_cached_photo_path(name, team)
        without_team = player_photos.get_cached_photo_path(name, None)
        if os.path.normcase(with_team) == os.path.normcase(without_team):
            continue
        if not os.path.exists(without_team) or not os.path.exists(with_team):
            continue
        if is_low_quality_or_headshot(with_team) or not is_low_quality_or_headshot(without_team):
            continue
        removed += _drop_cached_photo(
            without_team, f"low quality, shadowed by {os.path.basename(with_team)}", dry_run
        )

    if not removed:
        logger.info("✅ Photo cache is clean, nothing to remove.")
    return removed


def clear_telegram_media_cache(db_path: str = "league.db", dry_run: bool = False) -> int:
    """
    Clear obsolete telegram_media_cache entries so the bot will re-upload
    and re-render fresh cards instead of using old cached file_ids.
    """
    logger.info("=== 3. Purging Telegram media cache in database ===")
    full_db_path = os.path.join(BASE_DIR, db_path) if not os.path.isabs(db_path) else db_path
    if not os.path.exists(full_db_path):
        logger.warning(f"Database {full_db_path} does not exist.")
        return 0

    if dry_run:
        conn = sqlite3.connect(full_db_path)
        try:
            count = conn.execute("SELECT COUNT(*) FROM telegram_media_cache").fetchone()[0]
            logger.info(f"  [dry-run] would clear {count} cached media items.")
            return count
        except Exception as e:
            logger.error(f"Error reading telegram_media_cache: {e}")
            return 0
        finally:
            conn.close()

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
    logger.info("=== 4. Generating visual preview cards ===")
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
    parser.add_argument("--skip-clean", action="store_true", help="Keep stale/orphan files in the photo cache")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without touching anything")
    parser.add_argument("--db-path", default="league.db", help="Path to SQLite database")
    args = parser.parse_args()

    if args.dry_run:
        logger.info("DRY RUN — никакие файлы и записи БД изменены не будут.")

    players = get_all_squad_players(args.db_path)
    logger.info(f"Found {len(players)} total unique squad players in database.")

    if not players:
        logger.warning(
            "No players found in the database — nothing to refresh or clean. "
            "Настоящая база проекта живёт на сервере; локальная копия пустая."
        )
        return

    # 1. Photos
    cutouts, headshots, missing, planned = refresh_photos(
        players, force_all=args.force_photos, dry_run=args.dry_run
    )

    # 2. Photo cache hygiene (работает по диску, а не по БД)
    removed = 0 if args.skip_clean else clean_photo_cache(players, dry_run=args.dry_run)

    # 3. Database Cache
    cleared = clear_telegram_media_cache(args.db_path, dry_run=args.dry_run)

    # 4. Card Previews
    previews = 0
    if args.dry_run:
        logger.info("=== 4. Generating visual preview cards === skipped (dry run)")
    elif not args.skip_previews:
        previews = generate_all_preview_cards(players)

    logger.info("==========================================")
    logger.info("🎉 DRY RUN COMPLETE (ничего не изменено)" if args.dry_run else "🎉 CARD REFRESH COMPLETE!")
    logger.info(f"• Total Players: {len(players)}")
    if args.dry_run:
        logger.info(f"• Photos to refetch: {planned}")
    else:
        logger.info(f"• Cutouts / High-res: {cutouts}")
        logger.info(f"• Headshots (with jersey silhouette): {headshots}")
        logger.info(f"• Missing photos: {missing}")
    logger.info(f"• Stale cache files {'to remove' if args.dry_run else 'removed'}: {removed}")
    logger.info(f"• Database cache {'to clear' if args.dry_run else 'cleared'}: {cleared} entries")
    logger.info(f"• Previews generated: {previews}")
    logger.info("==========================================")


if __name__ == "__main__":
    main()
