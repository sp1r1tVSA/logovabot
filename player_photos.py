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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.sofascore.com",
    "Referer": "https://www.sofascore.com/"
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
    path = get_cached_photo_path(player_name, disambiguator)
    return path if (os.path.isfile(path) and os.path.getsize(path) > 0) else None


def _get_thesportsdb_url(player_name: str) -> str | None:
    clean_name = _normalize_name(player_name)
    encoded = urllib.parse.quote(clean_name)
    url = f"https://www.thesportsdb.com/api/v1/json/3/searchplayers.php?p={encoded}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            players = data.get("player")
            if players and len(players) > 0:
                p = players[0]
                return p.get("strCutout") or p.get("strRender") or p.get("strThumb")
    except Exception:
        pass
    return None


def _get_fotmob_url(player_name: str) -> str | None:
    clean_name = _normalize_name(player_name)
    encoded = urllib.parse.quote(clean_name)
    url = f"https://www.fotmob.com/api/searchData?term={encoded}"
    req = urllib.request.Request(url, headers=HEADERS)
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
    except Exception:
        pass
    return None


def _get_sofascore_url(player_name: str) -> str | None:
    clean_name = _normalize_name(player_name)
    encoded = urllib.parse.quote(clean_name)
    url = f"https://api.sofascore.com/api/v1/search/all?q={encoded}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", [])
            for res in results:
                if res.get("type") == "player":
                    p_id = res.get("entity", {}).get("id")
                    if p_id:
                        return f"https://api.sofascore.app/api/v1/player/{p_id}/image"
    except Exception:
        pass
    return None


def _get_transfermarkt_url(player_name: str) -> str | None:
    """Ультимативный фоллбэк: Transfermarkt (имеет 100% базу всех игроков в мире)"""
    clean_name = _normalize_name(player_name)
    encoded = urllib.parse.quote(clean_name)
    url = f"https://www.transfermarkt.com/quickselect/autosuggest?query={encoded}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for item in data:
                img = item.get("image", "")
                if img and "default" not in img.lower():
                    # Меняем /small/ на /medium/ для лучшего качества
                    return img.replace("/small/", "/medium/")
    except Exception:
        pass
    return None


def _download_photo(url: str, dest_path: str) -> bool:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
            if len(data) < 500: # Заглушка или битый файл
                return False
            with open(dest_path, "wb") as f:
                f.write(data)
            return True
    except Exception:
        return False


def fetch_and_cache(player_name: str, team: str | None = None) -> str | None:
    """Fetch photo for player_name and cache it locally."""
    _ensure_photos_dir()
    cached = get_cached_photo_path(player_name, team)
    
    if is_cached(player_name, team):
        return cached

    with _photos_lock:
        # Задержка 0.4 сек защищает от банов Cloudflare (Снимает ошибки 403 Forbidden)
        time.sleep(0.4)

        providers = [
            ("TheSportsDB", _get_thesportsdb_url),
            ("FotMob", _get_fotmob_url),
            ("SofaScore", _get_sofascore_url),
            ("Transfermarkt", _get_transfermarkt_url)
        ]

        # Умный цикл: перебирает источники, и если картинка битая (404), идет к следующему
        for provider_name, get_url_func in providers:
            try:
                photo_url = get_url_func(player_name)
                if photo_url:
                    if _download_photo(photo_url, cached):
                        logger.info(f"[{provider_name}] ✅ Downloaded photo for '{player_name}'")
                        return cached
                    else:
                        logger.debug(f"[{provider_name}] ⚠️ Photo URL found but file is missing (404). Trying next provider...")
            except Exception as e:
                logger.debug(f"[{provider_name}] ❌ Error for '{player_name}': {e}")
                
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