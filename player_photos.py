"""
player_photos.py

Fetches and caches player portrait photos using 4 free providers:
1. TheSportsDB API (Cutouts / Transparent PNG)
2. FotMob Search API (Massive Database)
3. SofaScore Search API 
4. Transfermarkt API (Ultimate Fallback)
"""

import os
import re
import json
import time
import logging
import threading
import unicodedata
import urllib.request
import urllib.parse

logger = logging.getLogger(__name__)

BASE_DIR   = os.path.dirname(__file__)
PHOTOS_DIR = os.path.join(BASE_DIR, "assets", "players")

# Индивидуальные маскировочные заголовки для обхода Cloudflare и CORS
FOTMOB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.fotmob.com/"
}

SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Origin": "https://www.sofascore.com",
    "Referer": "https://www.sofascore.com/"
}

TM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.transfermarkt.com/"
}

STD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

_photos_lock = threading.Lock()


def _ensure_photos_dir() -> None:
    os.makedirs(PHOTOS_DIR, exist_ok=True)


def _normalize_name(name: str) -> str:
    """Убирает акценты/диакритику из имени."""
    nfkd_form = unicodedata.normalize('NFKD', name)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)]).strip()


def _slugify(name: str) -> str:
    """Convert player name to a safe filename slug."""
    clean = _normalize_name(name).lower()
    clean = re.sub(r"[^\w\s-]", "", clean)
    return re.sub(r"[\s]+", "_", clean)


def get_cached_photo_path(player_name: str, disambiguator: str | None = None) -> str:
    slug = _slugify(player_name)
    if disambiguator:
        slug = f"{slug}_{_slugify(str(disambiguator))}"
    return os.path.join(PHOTOS_DIR, f"{slug}.png")


def is_cached(player_name: str, disambiguator: str | None = None) -> bool:
    path = get_cached_photo_path(player_name, disambiguator)
    return os.path.isfile(path) and os.path.getsize(path) > 0


def get_photo_path(player_name: str, disambiguator: str | None = None) -> str | None:
    if disambiguator:
        path = get_cached_photo_path(player_name, disambiguator)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    # Check without disambiguator
    path = get_cached_photo_path(player_name, None)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path
    return None


def _get_thesportsdb_url(player_name: str) -> str | None:
    clean_name = _normalize_name(player_name)
    encoded = urllib.parse.quote(clean_name)
    url = f"https://www.thesportsdb.com/api/v1/json/3/searchplayers.php?p={encoded}"
    req = urllib.request.Request(url, headers=STD_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            players = data.get("player")
            if players and len(players) > 0:
                p = players[0]
                return p.get("strCutout") or p.get("strRender") or p.get("strThumb")
    except Exception as e:
        logger.debug(f"[TheSportsDB] Error for '{player_name}': {e}")
    return None


def _get_fotmob_url(player_name: str) -> str | None:
    clean_name = _normalize_name(player_name)
    encoded = urllib.parse.quote(clean_name)
    url = f"https://www.fotmob.com/api/searchData?term={encoded}"
    req = urllib.request.Request(url, headers=FOTMOB_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            players = data.get("player", []) or data.get("players", [])
            if not players and isinstance(data, dict):
                for key, val in data.items():
                    if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                        if "id" in val[0] and ("name" in val[0] or "p" in val[0]):
                            players = val
                            break

            if players:
                player_id = players[0].get("id")
                if player_id:
                    return f"https://images.fotmob.com/image_resources/playerimages/{player_id}.png"
    except Exception as e:
        logger.debug(f"[FotMob] Error for '{player_name}': {e}")
    return None


def _get_sofascore_url(player_name: str) -> str | None:
    clean_name = _normalize_name(player_name)
    encoded = urllib.parse.quote(clean_name)
    url = f"https://api.sofascore.com/api/v1/search/all?q={encoded}"
    req = urllib.request.Request(url, headers=SOFASCORE_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", [])
            for res in results:
                if res.get("type") == "player":
                    p_id = res.get("entity", {}).get("id")
                    if p_id:
                        return f"https://api.sofascore.app/api/v1/player/{p_id}/image"
    except Exception as e:
        logger.debug(f"[SofaScore] Error for '{player_name}': {e}")
    return None


def _get_transfermarkt_url(player_name: str) -> str | None:
    clean_name = _normalize_name(player_name)
    encoded = urllib.parse.quote(clean_name)
    url = f"https://www.transfermarkt.com/quickselect/autosuggest?query={encoded}"
    req = urllib.request.Request(url, headers=TM_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for item in data:
                img = item.get("image", "")
                if img and "default" not in img.lower():
                    return img.replace("/small/", "/medium/")
    except Exception as e:
        logger.debug(f"[Transfermarkt] Error for '{player_name}': {e}")
    return None


def _download_photo(url: str, dest_path: str, headers: dict | None = None) -> bool:
    if headers is None:
        headers = STD_HEADERS
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
            if len(data) < 500:
                return False
            with open(dest_path, "wb") as f:
                f.write(data)
            return True
    except Exception as e:
        logger.debug(f"[Download] Error fetching from {url}: {e}")
        return False



def fetch_and_cache(player_name: str, team: str | None = None) -> str | None:
    """Fetch photo for player_name and cache it locally."""
    _ensure_photos_dir()
    cached = get_cached_photo_path(player_name, team)
    
    if is_cached(player_name, team):
        return cached

    with _photos_lock:
        # Задержка 0.5 сек защищает от банов при массовой загрузке
        time.sleep(0.5)

        providers = [
            ("TheSportsDB", _get_thesportsdb_url, STD_HEADERS),
            ("FotMob", _get_fotmob_url, FOTMOB_HEADERS),
            ("SofaScore", _get_sofascore_url, SOFASCORE_HEADERS),
            ("Transfermarkt", _get_transfermarkt_url, TM_HEADERS)
        ]

        for provider_name, get_url_func, p_headers in providers:
            photo_url = get_url_func(player_name)
            if photo_url:
                if _download_photo(photo_url, cached, headers=p_headers):
                    logger.info(f"[{provider_name}] ✅ Downloaded photo for '{player_name}'")
                    return cached
                else:
                    logger.debug(f"[{provider_name}] ⚠️ Photo URL found but file is missing (404/403).")
                    
        logger.info(f"No photo found for player '{player_name}' in any provider")
        return None


def fetch_all_players(players: list[str] | list[tuple[str, str]]) -> dict[str, str | None]:
    """Bulk-fetch photos for a list of players."""
    results: dict[str, str | None] = {}
    for item in players:
        name, team = item if isinstance(item, tuple) else (item, None)
        key = f"{name} ({team})" if team else name
        results[key] = fetch_and_cache(name, team)
    return results