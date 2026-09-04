"""
api/routes_gamification.py

Phase 10: Player Profile 2.0, Fair Leaderboard, Achievements & Season API Endpoints.
Strict Invariants:
1. Public Profile vs Private Profile:
   Public profile and player comparison NEVER expose wallet balance, raw coins, or private wagers.
   Private profile is available exclusively to the authenticated user.
2. Pagination Boundary:
   Leaderboard limit is clamped strictly between 1 and 50.
3. Scoped Leaderboards:
   Supports GLOBAL, DIVISION, SEASON scopes with configurable metrics and pinned user standing.
4. Telegram WebApp HMAC Authentication & Access Controls.
"""

import logging
from aiohttp import web
from .auth import get_authenticated_user, check_user_access
import database
from services.leaderboard_service import LeaderboardService
from services.season_progression import SeasonProgressionEngine

logger = logging.getLogger("api.gamification")


def _get_auth_user(request: web.Request) -> tuple[dict | None, web.Response | None]:
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return None, web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]
    if not check_user_access(user_id):
        return None, web.json_response({"status": "error", "error": "lab_mode"}, status=403)

    return user_info, None


async def handle_get_progression(request: web.Request) -> web.Response:
    """
    GET /api/progression
    Returns user's level, XP, login streak calendar status.
    """
    user_info, err = _get_auth_user(request)
    if err is not None:
        return err
    user_id = user_info["id"]

    streak_info = database.check_and_update_login_streak(user_id)
    progression = database.get_or_create_progression(user_id)
    achievements = database.get_user_achievements(user_id)

    unclaimed_ach = sum(1 for a in achievements if a["is_unlocked"] and not a["is_claimed"])

    return web.json_response({
        "status": "ok",
        "progression": progression,
        "streak": streak_info,
        "unclaimed_achievements_count": unclaimed_ach,
        "total_achievements_count": len(achievements),
        "unlocked_achievements_count": sum(1 for a in achievements if a["is_unlocked"])
    })


async def handle_get_profile(request: web.Request) -> web.Response:
    """
    GET /api/profile
    GET /api/profile/{user_id}
    Returns private profile if requested for self; returns strictly public profile if requested for other player.
    """
    user_info, err = _get_auth_user(request)
    if err is not None:
        return err
    auth_uid = user_info["id"]

    target_uid = auth_uid
    if "user_id" in request.match_info:
        try:
            target_uid = int(request.match_info["user_id"])
        except ValueError:
            return web.json_response({"status": "error", "message": "Invalid user ID."}, status=400)

    # Privacy enforcement
    if target_uid == auth_uid:
        profile = database.get_private_player_profile(target_uid)
    else:
        profile = database.get_public_player_profile(target_uid)

    return web.json_response({
        "status": "ok",
        "profile": profile,
        "is_self": (target_uid == auth_uid)
    })


async def handle_get_player_public(request: web.Request) -> web.Response:
    """
    GET /api/player/{id}/public
    Returns strictly PUBLIC gamer card. Never exposes wallet or private stakes.
    """
    user_info, err = _get_auth_user(request)
    if err is not None:
        return err

    try:
        target_uid = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Invalid player ID."}, status=400)

    profile = database.get_public_player_profile(target_uid)
    return web.json_response({
        "status": "ok",
        "player": profile
    })


async def handle_get_profile_stats(request: web.Request) -> web.Response:
    """
    GET /api/profile/stats
    Returns detailed personalized stats for the authenticated user.
    """
    user_info, err = _get_auth_user(request)
    if err is not None:
        return err
    user_id = user_info["id"]

    fav = database.get_user_favorite_stats(user_id)
    season_stats = database.get_player_season_stats(user_id)
    career = database.get_player_career_stats(user_id)

    return web.json_response({
        "status": "ok",
        "user_id": user_id,
        "favorite_markets": fav["favorite_markets"],
        "favorite_teams": fav["favorite_teams"],
        "prediction_accuracy": fav["prediction_accuracy"],
        "value_hit_rate": fav["value_hit_rate"],
        "season_stats": season_stats,
        "career_stats": career
    })


async def handle_get_leaderboard(request: web.Request) -> web.Response:
    """
    GET /api/leaderboard
    Global leaderboard with pagination and user pin.
    Query params: page, limit, metric, period, season_id.
    """
    user_info, err = _get_auth_user(request)
    if err is not None:
        return web.json_response({
            "status": "error",
            "error": "lab_mode",
            "message": "Logovo.bet находится на закрытом тесте в Лаборатории."
        }, status=403)
    user_id = user_info["id"]

    from api.routes_wallet import check_user_access
    if not check_user_access(user_id):
        return web.json_response({
            "status": "error",
            "error": "lab_mode",
            "message": "Logovo.bet находится на закрытом тесте в Лаборатории."
        }, status=403)

    try:
        page = int(request.query.get("page", 1))
        limit = int(request.query.get("limit", 20))
    except ValueError:
        return web.json_response({"status": "error", "message": "page and limit must be integers."}, status=400)

    metric = request.query.get("metric", "RATING").strip().upper()
    period = request.query.get("period", "ALL_TIME").strip().upper()
    s_id = int(request.query["season_id"]) if "season_id" in request.query else None

    result = LeaderboardService.get_leaderboard(
        season_id=s_id,
        division_id=None,
        scope="GLOBAL",
        period=period,
        metric=metric,
        page=page,
        limit=limit,
        user_id=user_id
    )

    coin_leaders = database.get_top_bettors(20)
    from services.analytics_service import get_capper_leaderboard
    capper_leaders = get_capper_leaderboard(division_id=None, season_id=s_id, min_bets=5)

    return web.json_response({
        "status": "ok",
        "leaders": coin_leaders,
        "capper_leaders": capper_leaders,
        **result
    })


async def handle_get_leaderboard_division(request: web.Request) -> web.Response:
    """
    GET /api/leaderboard/division
    Division-scoped leaderboard.
    Query params: division_id (required or defaults to user's division), page, limit, metric.
    """
    user_info, err = _get_auth_user(request)
    if err is not None:
        return err
    user_id = user_info["id"]

    try:
        page = int(request.query.get("page", 1))
        limit = int(request.query.get("limit", 20))
    except ValueError:
        return web.json_response({"status": "error", "message": "page and limit must be integers."}, status=400)

    div_id = None
    if "division_id" in request.query:
        try:
            div_id = int(request.query["division_id"])
        except ValueError:
            return web.json_response({"status": "error", "message": "division_id must be integer."}, status=400)
    else:
        # Default to user's division
        s_stats = database.get_player_season_stats(user_id)
        div_id = s_stats["division_id"]

    metric = request.query.get("metric", "RATING").strip().upper()
    period = request.query.get("period", "ALL_TIME").strip().upper()
    s_id = int(request.query["season_id"]) if "season_id" in request.query else None

    result = LeaderboardService.get_leaderboard(
        season_id=s_id,
        division_id=div_id,
        scope="DIVISION",
        period=period,
        metric=metric,
        page=page,
        limit=limit,
        user_id=user_id
    )

    return web.json_response({"status": "ok", **result})


async def handle_get_leaderboard_season(request: web.Request) -> web.Response:
    """
    GET /api/leaderboard/season
    Season-scoped leaderboard.
    Query params: season_id (optional, defaults to active season), page, limit, metric.
    """
    user_info, err = _get_auth_user(request)
    if err is not None:
        return err
    user_id = user_info["id"]

    try:
        page = int(request.query.get("page", 1))
        limit = int(request.query.get("limit", 20))
    except ValueError:
        return web.json_response({"status": "error", "message": "page and limit must be integers."}, status=400)

    s_id = int(request.query["season_id"]) if "season_id" in request.query else None
    metric = request.query.get("metric", "RATING").strip().upper()

    result = LeaderboardService.get_leaderboard(
        season_id=s_id,
        division_id=None,
        scope="SEASON",
        period="SEASON",
        metric=metric,
        page=page,
        limit=limit,
        user_id=user_id
    )

    return web.json_response({"status": "ok", **result})


async def handle_get_season(request: web.Request) -> web.Response:
    """
    GET /api/season
    Active season information, division standings, promotion zones, and rules.
    """
    user_info, err = _get_auth_user(request)
    if err is not None:
        return err
    user_id = user_info["id"]

    act = database.get_active_season()
    if not act:
        return web.json_response({"status": "error", "message": "No active season found."}, status=404)

    s_id = act["id"]
    s_stats = database.get_player_season_stats(user_id, season_id=s_id)
    div_id = s_stats["division_id"]

    standings = SeasonProgressionEngine.get_division_standings(s_id, div_id)
    rules = database.get_season_rules(s_id, div_id)

    # Current user standing
    user_standing = next((p for p in standings if p["user_id"] == user_id), None)

    return web.json_response({
        "status": "ok",
        "season": act,
        "division_id": div_id,
        "rules": rules,
        "user_standing": user_standing,
        "standings": standings
    })


async def handle_get_season_rewards(request: web.Request) -> web.Response:
    """
    GET /api/season/rewards
    Season rewards catalog and authenticated user's reward distribution ledger.
    """
    user_info, err = _get_auth_user(request)
    if err is not None:
        return err
    user_id = user_info["id"]

    act = database.get_active_season()
    s_id = act["id"] if act else 1

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM season_rewards_catalog")
        catalog = [dict(r) for r in cursor.fetchall()]

    user_rewards = database.get_user_season_rewards(user_id, season_id=s_id)

    return web.json_response({
        "status": "ok",
        "season_id": s_id,
        "catalog": catalog,
        "user_rewards": user_rewards
    })


async def handle_get_achievements(request: web.Request) -> web.Response:
    """
    GET /api/achievements
    Returns all catalog achievements and user unlock state.
    """
    user_info, err = _get_auth_user(request)
    if err is not None:
        return err
    user_id = user_info["id"]

    achievements = database.get_user_achievements(user_id)
    return web.json_response({
        "status": "ok",
        "achievements": achievements
    })


async def handle_claim_achievement(request: web.Request) -> web.Response:
    """
    POST /api/achievements/claim
    Body: {"achievement_id": "ACH_FIRST_BET"}
    """
    user_info, err = _get_auth_user(request)
    if err is not None:
        return err
    user_id = user_info["id"]

    try:
        data = await request.json()
        ach_id = str(data.get("achievement_id", "")).strip()
    except Exception:
        return web.json_response({"status": "error", "error": "invalid_payload"}, status=400)

    success, msg, payload = database.claim_achievement_reward(user_id, ach_id)
    if not success:
        return web.json_response({"status": "error", "message": msg}, status=400)

    return web.json_response({
        "status": "ok",
        "message": msg,
        "reward": payload
    })
