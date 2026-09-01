"""
api/routes_gamification.py
Progression, Achievements & Profile API endpoints.
(Quests and PvP Duels removed in v2.0 architecture cleanup.)
"""

import logging
from aiohttp import web
from .auth import get_authenticated_user, check_user_access
import database

logger = logging.getLogger("api.gamification")


async def handle_get_progression(request: web.Request) -> web.Response:
    """
    GET /api/progression
    Returns user's level, XP, login streak calendar status.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]
    if not check_user_access(user_id):
        return web.json_response({"status": "error", "error": "lab_mode"}, status=403)

    # 1. Update streak & progression
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


async def handle_get_achievements(request: web.Request) -> web.Response:
    """
    GET /api/achievements
    Returns all catalog achievements and user unlock state.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]
    if not check_user_access(user_id):
        return web.json_response({"status": "error", "error": "lab_mode"}, status=403)

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
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]
    if not check_user_access(user_id):
        return web.json_response({"status": "error", "error": "lab_mode"}, status=403)

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


async def handle_get_profile(request: web.Request) -> web.Response:
    """
    GET /api/profile/{user_id}
    Returns public gamer card for any player.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    try:
        target_uid = int(request.match_info.get("user_id", user_info["id"]))
    except Exception:
        target_uid = user_info["id"]

    profile = database.get_public_gamer_profile(target_uid)
    return web.json_response({
        "status": "ok",
        "profile": profile
    })
