"""
player_photos.py

Fetches and caches player portrait photos using hybrid free providers:
1. TheSportsDB API (Cutouts / Transparent PNG)
2. FotMob Search API (Massive Database)
3. Wikipedia / Wikimedia Commons API (High-res portraits)
4. Transfermarkt / SofaScore (Fallbacks)
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

# Latin ligatures and special character replacement
TRANSLIT_LATIN = {
    'ø': 'o', 'Ø': 'O',
    'æ': 'ae', 'Æ': 'AE',
    'œ': 'oe', 'Œ': 'OE',
    'ß': 'ss',
    'ł': 'l', 'Ł': 'L',
    'đ': 'd', 'Đ': 'D',
    'ð': 'd', 'Ð': 'D',
    'þ': 'th', 'Þ': 'TH',
    'ı': 'i', 'İ': 'I',
}

# Individual headers for API providers
FOTMOB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.fotmob.com/"
}

WIKI_HEADERS = {
    "User-Agent": "Logovobot/1.0 (https://t.me/logovobot; contact@logovo.bot)",
    "Accept": "application/json"
}

TM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
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
    """Убирает акценты/диакритику и нормализует спецсимволы латиницы."""
    for k, v in TRANSLIT_LATIN.items():
        name = name.replace(k, v)
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
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return True
    path_no_dis = get_cached_photo_path(player_name, None)
    return os.path.isfile(path_no_dis) and os.path.getsize(path_no_dis) > 0


def get_photo_path(player_name: str, disambiguator: str | None = None) -> str | None:
    """Check if photo exists on disk without network request."""
    if disambiguator:
        path = get_cached_photo_path(player_name, disambiguator)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    path = get_cached_photo_path(player_name, None)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path
    return None


def _get_fotmob_url(player_name: str) -> str | None:
    clean_name = _normalize_name(player_name)
    search_terms = [clean_name]
    if "-" in clean_name:
        search_terms.append(clean_name.replace("-", " "))
        search_terms.append(clean_name.split("-")[-1].strip())
    parts = clean_name.split()
    if len(parts) > 2:
        search_terms.append(f"{parts[0]} {parts[-1]}")
    if len(parts) >= 2 and parts[-1] not in search_terms:
        search_terms.append(parts[-1])

    for term in search_terms:
        encoded = urllib.parse.quote(term)
        url = f"https://apigw.fotmob.com/searchapi/suggest?term={encoded}"
        req = urllib.request.Request(url, headers=FOTMOB_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                squad = data.get("squadMemberSuggest", [])
                if squad and len(squad) > 0:
                    options = squad[0].get("options", [])
                    for opt in options:
                        payload = opt.get("payload", {})
                        if payload.get("isCoach"):
                            continue
                        player_id = payload.get("id")
                        if player_id:
                            return f"https://images.fotmob.com/image_resources/playerimages/{player_id}.png"
        except Exception as e:
            logger.debug(f"[FotMob] Error for '{term}': {e}")
    return None


def _get_thesportsdb_url(player_name: str) -> str | None:
    clean_name = _normalize_name(player_name).lower().strip()
    search_terms = [clean_name]
    if "-" in clean_name:
        search_terms.append(clean_name.replace("-", " "))
    parts = clean_name.split()
    if len(parts) > 2:
        search_terms.append(f"{parts[0]} {parts[-1]}")

    for term in search_terms:
        encoded = urllib.parse.quote(term)
        url = f"https://www.thesportsdb.com/api/v1/json/3/searchplayers.php?p={encoded}"
        req = urllib.request.Request(url, headers=STD_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                players = data.get("player")
                if players:
                    for p in players:
                        pos = str(p.get("strPosition") or "").lower()
                        if "manager" in pos or "coach" in pos:
                            continue
                        found_name = _normalize_name(p.get("strPlayer") or "").lower()
                        # Strict name matching
                        if clean_name not in found_name and found_name not in clean_name:
                            continue
                        img = p.get("strCutout") or p.get("strRender") or p.get("strThumb")
                        if img:
                            return img
        except Exception as e:
            logger.debug(f"[TheSportsDB] Error for '{term}': {e}")
    return None


def _get_wikipedia_url(player_name: str) -> str | None:
    clean_name = _normalize_name(player_name)
    search_terms = [player_name, clean_name]
    for term in search_terms:
        encoded = urllib.parse.quote(term)
        url = f"https://en.wikipedia.org/w/api.php?action=query&titles={encoded}&prop=pageimages&format=json&pithumbsize=300"
        req = urllib.request.Request(url, headers=WIKI_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                pages = data.get("query", {}).get("pages", {})
                for _, pdata in pages.items():
                    if "thumbnail" in pdata:
                        return pdata["thumbnail"]["source"]
        except Exception as e:
            logger.debug(f"[Wikipedia] Error for '{term}': {e}")
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
    
    existing = get_photo_path(player_name, team)
    if existing:
        return existing

    with _photos_lock:
        # Double check inside lock
        existing = get_photo_path(player_name, team)
        if existing:
            return existing

        providers = [
            ("FotMob", _get_fotmob_url, FOTMOB_HEADERS),
            ("TheSportsDB", _get_thesportsdb_url, STD_HEADERS),
            ("Wikipedia", _get_wikipedia_url, WIKI_HEADERS),
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


def get_player_photo(player_name: str, team: str | None = None) -> str | None:
    """Convenience alias to fetch and return cached photo path on demand."""
    cached = get_photo_path(player_name, team)
    if cached:
        return cached
    return fetch_and_cache(player_name, team)


def fetch_all_players(players: list[str] | list[tuple[str, str]]) -> dict[str, str | None]:
    """Bulk-fetch photos for a list of players."""
    results: dict[str, str | None] = {}
    for item in players:
        name, team = item if isinstance(item, tuple) else (item, None)
        key = f"{name} ({team})" if team else name
        results[key] = fetch_and_cache(name, team)
    return results


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if len(sys.argv) > 1:
        p_name = sys.argv[1]
        t_name = sys.argv[2] if len(sys.argv) > 2 else None
        res = fetch_and_cache(p_name, t_name)
        if res:
            print(f"✅ Photo downloaded to: {res}")
        else:
            print(f"❌ Failed to fetch photo for '{p_name}'")
    else:
        print("Usage: python player_photos.py <Player Name> [Team Name]")