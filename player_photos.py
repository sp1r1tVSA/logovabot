"""
player_photos.py

Fetches and caches player portrait photos from api-sports.io (API-Football).

Cache layout:  assets/players/<slugified_name>.jpg
If a photo cannot be fetched (no API key, player not found, rate limit),
the functions return None and the card generator falls back gracefully.
"""

import os
import re
import logging
import urllib.request
import urllib.error
import urllib.parse
import json

import config

logger = logging.getLogger(__name__)

BASE_DIR    = os.path.dirname(__file__)
PHOTOS_DIR  = os.path.join(BASE_DIR, "assets", "players")
API_HOST    = "v3.football.api-sports.io"
SEASON      = 2024  # current EA FC / FIFA season to search


def _ensure_photos_dir() -> None:
    os.makedirs(PHOTOS_DIR, exist_ok=True)


def _slugify(name: str) -> str:
    """Convert player name to a safe filename slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s]+", "_", slug)
    return slug


def get_cached_photo_path(player_name: str) -> str:
    """Return the expected cache path for a player (may not exist yet)."""
    return os.path.join(PHOTOS_DIR, _slugify(player_name) + ".jpg")


def is_cached(player_name: str) -> bool:
    path = get_cached_photo_path(player_name)
    return os.path.isfile(path) and os.path.getsize(path) > 0


def _api_search_player(name: str) -> dict | None:
    """
    Search api-sports.io for a player by name.
    Returns the first matching player dict or None.
    """
    if not config.APISPORTS_KEY:
        logger.warning("APISPORTS_KEY not set — skipping photo fetch")
        return None

    params = urllib.parse.urlencode({"search": name, "season": SEASON})
    url    = f"https://{API_HOST}/players?{params}"

    req = urllib.request.Request(url, headers={
        "x-apisports-key": config.APISPORTS_KEY,
        "Accept": "application/json",
    })

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        logger.error("API-Football request failed for '%s': %s", name, e)
        return None

    results = data.get("response", [])
    if not results:
        logger.info("No API result for player '%s'", name)
        return None

    # Try exact match first, then fall back to first result
    name_lower = name.lower()
    for entry in results:
        full = entry.get("player", {}).get("name", "").lower()
        if name_lower in full or full in name_lower:
            return entry["player"]

    return results[0]["player"]


def _download_photo(url: str, dest_path: str) -> bool:
    """Download a photo URL to dest_path. Returns True on success."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "logovobot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        if len(data) < 500:          # suspiciously small → probably a placeholder
            return False
        with open(dest_path, "wb") as f:
            f.write(data)
        return True
    except Exception as e:
        logger.error("Failed to download photo from %s: %s", url, e)
        return False


def fetch_and_cache(player_name: str) -> str | None:
    """
    Fetch photo for player_name from API-Football and cache it locally.
    Returns path to cached file, or None if unavailable.
    """
    _ensure_photos_dir()

    cached = get_cached_photo_path(player_name)
    if is_cached(player_name):
        return cached

    player_data = _api_search_player(player_name)
    if not player_data:
        return None

    photo_url = player_data.get("photo", "")
    if not photo_url:
        return None

    logger.info("Downloading photo for '%s' from %s", player_name, photo_url)
    ok = _download_photo(photo_url, cached)
    return cached if ok else None


def fetch_all_players(player_names: list[str]) -> dict[str, str | None]:
    """
    Bulk-fetch photos for a list of player names.
    Returns {player_name: path_or_None}.
    Skips players that are already cached.
    """
    results: dict[str, str | None] = {}
    for name in player_names:
        results[name] = fetch_and_cache(name)
    return results


def get_photo_path(player_name: str) -> str | None:
    """
    Return a cached photo path if it exists, otherwise None.
    Does NOT make API calls — use fetch_and_cache() for that.
    """
    path = get_cached_photo_path(player_name)
    return path if (os.path.isfile(path) and os.path.getsize(path) > 0) else None
