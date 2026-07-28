"""
player_photos.py

Fetches and caches player portrait photos from api-sports.io (API-Football).

Cache layout:  assets/players/<slugified_name>[_<slugified_disambiguator>].jpg
The optional disambiguator (e.g. team name or a stable player ID) should be
passed by callers whenever available, so two different players who happen
to share the same name don't collide on the same cache file.
If a photo cannot be fetched (no API key, player not found, rate limit),
the functions return None and the card generator falls back gracefully.
"""

import os
import re
import time
import difflib
import logging
import threading
import urllib.request
import urllib.error
import urllib.parse
import json
from datetime import date

import config

logger = logging.getLogger(__name__)

BASE_DIR    = os.path.dirname(__file__)
PHOTOS_DIR  = os.path.join(BASE_DIR, "assets", "players")
API_HOST    = "v3.football.api-sports.io"

# Minimum similarity ratio (0-1) for a search result to be treated as a
# genuine name match rather than an unrelated player.
MATCH_THRESHOLD = 0.6

# Minimum spacing between real API-Football requests, and backoff before
# retrying a single request that got rate-limited (HTTP 429).
# The free api-sports.io plan allows 10 requests/minute (confirmed via
# their dashboard) — 6.5s gives ~9.2 req/min, safely under that.
REQUEST_DELAY_SECONDS   = getattr(config, "APISPORTS_REQUEST_DELAY", 6.5)
RATE_LIMIT_BACKOFF_SECONDS = getattr(config, "APISPORTS_RATE_LIMIT_BACKOFF", 5.0)

_rate_limit_lock  = threading.Lock()
_last_request_at  = 0.0


class RateLimitExceeded(Exception):
    """
    Raised when API-Football keeps returning 429 even after backing off
    and retrying once. This usually means the plan's daily quota (not
    just the per-minute rate) is exhausted — at that point every
    remaining player would also fail, so callers doing a bulk fetch
    should catch this and stop the batch instead of treating each
    remaining player as "not found".
    """


def _throttle() -> None:
    """
    Block until at least REQUEST_DELAY_SECONDS has passed since the last
    real API-Football request, no matter which function or call site
    triggered it. This lives here — right next to the actual urlopen()
    call — rather than in fetch_all_players(), because that was the bug:
    a caller that fetches players in its own loop (e.g. calling
    fetch_and_cache() directly, one by one) bypassed that pacing
    entirely. A lock makes this safe even if callers run fetch_and_cache
    from multiple threads (e.g. via asyncio.to_thread) concurrently.
    """
    global _last_request_at
    with _rate_limit_lock:
        wait = REQUEST_DELAY_SECONDS - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _default_season() -> int:
    """
    Best-effort guess at the current season year if none is configured.
    КПЛ here is this tournament's own custom season label (run via the
    Telegram bot), not the real Kazakhstan Premier League — its season
    is tracked as a single calendar year, so no month-based cutoff is
    needed; this just returns the current year.
    """
    return date.today().year


# Prefer an explicit config.APISPORTS_SEASON if set, otherwise compute it
# from today's date so this doesn't silently go stale year over year.
SEASON = getattr(config, "APISPORTS_SEASON", None) or _default_season()


def _ensure_photos_dir() -> None:
    os.makedirs(PHOTOS_DIR, exist_ok=True)


def _slugify(name: str) -> str:
    """Convert player name to a safe filename slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s]+", "_", slug)
    return slug


def _build_slug(player_name: str, disambiguator: str | None = None) -> str:
    """
    Build the cache slug for a player, optionally disambiguated by e.g.
    team name or a stable player ID. Without this, two different players
    who share the same name (there are plenty of "David Silva"s and
    "Danilo"s across leagues) would silently overwrite each other's
    cached photo.
    """
    slug = _slugify(player_name)
    if disambiguator:
        slug = f"{slug}_{_slugify(str(disambiguator))}"
    return slug


def get_cached_photo_path(player_name: str, disambiguator: str | None = None) -> str:
    """
    Return the expected cache path for a player (may not exist yet).
    Pass `disambiguator` (team name, player ID, etc.) whenever available
    to avoid collisions between same-named players.
    """
    return os.path.join(PHOTOS_DIR, _build_slug(player_name, disambiguator) + ".jpg")


def is_cached(player_name: str, disambiguator: str | None = None) -> bool:
    path = get_cached_photo_path(player_name, disambiguator)
    return os.path.isfile(path) and os.path.getsize(path) > 0


def _best_match(query: str, results: list[dict], team: str | None = None) -> dict | None:
    """
    Pick the result whose player name is most similar to the query.

    Uses a similarity ratio instead of naive substring containment —
    "in"/"contains" checks give false positives for short names or
    nicknames (e.g. a query for "Mo" would match "Mohamed Salah" just
    as readily as the player actually named "Mo").

    If `team` is given, results whose team name matches it get a score
    boost — this is what actually resolves same-name collisions (e.g.
    two different "Danilo"s), since name similarity alone can't tell
    them apart.
    """
    query_lower = query.lower().strip()
    team_lower = team.lower().strip() if team else None
    best_entry, best_score = None, 0.0

    for entry in results:
        player = entry.get("player", {})
        full = player.get("name", "").lower()
        if not full:
            continue
        score = difflib.SequenceMatcher(None, query_lower, full).ratio()

        if team_lower:
            stats = entry.get("statistics") or []
            entry_team = (stats[0].get("team") or {}).get("name", "").lower() if stats else ""
            if entry_team and team_lower in entry_team:
                score += 0.25

        if score > best_score:
            best_entry, best_score = player, score

    if best_entry and best_score >= MATCH_THRESHOLD:
        return best_entry
    return None


def _api_search_player(name: str, team: str | None = None, retries: int = 1) -> dict | None:
    """
    Search api-sports.io for a player by name.
    Returns the best-matching player dict or None.
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

    _throttle()
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 429 and retries > 0:
            logger.warning(
                "Rate limited by API-Football, backing off before retrying '%s'", name
            )
            time.sleep(RATE_LIMIT_BACKOFF_SECONDS)
            return _api_search_player(name, team=team, retries=retries - 1)
        if e.code == 429:
            logger.error(
                "Still rate-limited for '%s' after retrying — likely daily quota exhausted", name
            )
            raise RateLimitExceeded(f"API-Football rate limit exceeded for '{name}'") from e
        logger.error("API-Football HTTP error for '%s': %s", name, e)
        return None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        # Covers connection failures, DNS errors, and socket timeouts —
        # a bare urlopen(timeout=...) timeout isn't always a URLError,
        # so it must be caught explicitly or it propagates uncaught.
        logger.error("API-Football request failed for '%s': %s", name, e)
        return None

    results = data.get("response", [])
    if not results:
        logger.info("No API result for player '%s'", name)
        return None

    return _best_match(name, results, team=team) or results[0]["player"]


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


def fetch_and_cache(player_name: str, team: str | None = None) -> str | None:
    """
    Fetch photo for player_name from API-Football and cache it locally.
    Pass `team` when known — it's used both to disambiguate the cache
    file from other same-named players and to help pick the right
    player among the API's search results.
    Returns path to cached file, or None if unavailable.

    Raises RateLimitExceeded if the API is still rate-limiting requests
    after backing off and retrying — callers doing a bulk fetch should
    catch this and stop rather than keep calling for every remaining
    player (they'll all fail identically until the quota resets).
    """
    _ensure_photos_dir()

    cached = get_cached_photo_path(player_name, team)
    if is_cached(player_name, team):
        return cached

    player_data = _api_search_player(player_name, team=team)
    if not player_data:
        return None

    photo_url = player_data.get("photo", "")
    if not photo_url:
        return None

    logger.info("Downloading photo for '%s' from %s", player_name, photo_url)
    ok = _download_photo(photo_url, cached)
    return cached if ok else None


def fetch_all_players(
    players: list[str] | list[tuple[str, str]]
) -> dict[str, str | None]:
    """
    Bulk-fetch photos for a list of players.
    Each item can be a plain name string, or a (name, team) tuple —
    pass the tuple form whenever you have a team available, so that
    two different players sharing a name don't collide: both in the
    on-disk cache and in the keys of the dict this returns (in that
    case the key becomes "Name (Team)" instead of just "Name").
    Skips players that are already cached.

    Pacing between real API calls is handled automatically by
    _throttle() inside _api_search_player() — no manual delay needed
    here, and it applies no matter who calls fetch_and_cache().
    """
    results: dict[str, str | None] = {}
    for item in players:
        name, team = item if isinstance(item, tuple) else (item, None)
        key = f"{name} ({team})" if team else name
        results[key] = fetch_and_cache(name, team)
    return results


def get_photo_path(player_name: str, disambiguator: str | None = None) -> str | None:
    """
    Return a cached photo path if it exists, otherwise None.
    Pass the same `disambiguator` (team, ID, etc.) used when caching,
    or this will look up the wrong (name-only) cache slot.
    Does NOT make API calls — use fetch_and_cache() for that.
    """
    path = get_cached_photo_path(player_name, disambiguator)
    return path if (os.path.isfile(path) and os.path.getsize(path) > 0) else None