"""
api/routes_gamification.py
Gamification, Quests, 30+ Achievements, 7-Day Streaks and PvP Duels API endpoints.
"""

import logging
from aiohttp import web
from .auth import get_authenticated_user, check_user_access
import database

logger = logging.getLogger("api.gamification")


async def handle_get_progression(request: web.Request) -> web.Response:
    """
    GET /api/progression
    Returns user's level, XP, login streak calendar status, and active quests.
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
    quests = database.get_user_quests(user_id)
    achievements = database.get_user_achievements(user_id)

    unclaimed_quests = sum(1 for q in quests if q["is_completed"] and not q["is_claimed"])
    unclaimed_ach = sum(1 for a in achievements if a["is_unlocked"] and not a["is_claimed"])

    return web.json_response({
        "status": "ok",
        "progression": progression,
        "streak": streak_info,
        "quests": quests,
        "unclaimed_quests_count": unclaimed_quests,
        "unclaimed_achievements_count": unclaimed_ach,
        "total_achievements_count": len(achievements),
        "unlocked_achievements_count": sum(1 for a in achievements if a["is_unlocked"])
    })


async def handle_claim_quest(request: web.Request) -> web.Response:
    """
    POST /api/quests/claim
    Body: {"quest_id": 12}
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
        quest_id = int(data.get("quest_id", 0))
    except Exception:
        return web.json_response({"status": "error", "error": "invalid_payload"}, status=400)

    success, msg, payload = database.claim_quest_reward(user_id, quest_id)
    if not success:
        return web.json_response({"status": "error", "message": msg}, status=400)

    return web.json_response({
        "status": "ok",
        "message": msg,
        "reward": payload
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


async def handle_get_duels(request: web.Request) -> web.Response:
    """
    GET /api/duels
    Returns list of open and active PvP duels.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]
    if not check_user_access(user_id):
        return web.json_response({"status": "error", "error": "lab_mode"}, status=403)

    duels = database.get_pvp_duels(user_id)
    return web.json_response({
        "status": "ok",
        "duels": duels
    })


async def handle_create_duel(request: web.Request) -> web.Response:
    """
    POST /api/duels/create
    Body: {"stake": 500, "round_number": 5, "match_ids": [1, 2], "picks": {"1": "p1", "2": "tb25"}}
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
        stake = int(data.get("stake", 0))
        round_num = int(data.get("round_number", 1))
        match_ids = list(data.get("match_ids", []))
        picks = dict(data.get("picks", {}))
    except Exception:
        return web.json_response({"status": "error", "error": "invalid_payload"}, status=400)

    success, res = database.create_pvp_duel(user_id, stake, round_num, match_ids, picks)
    if not success:
        return web.json_response({"status": "error", "message": str(res)}, status=400)

    # Award XP for initiating social challenge
    database.add_user_xp(user_id, 50)
    database.evaluate_quest_progress(user_id, "place_bets", 1)

    return web.json_response({
        "status": "ok",
        "duel_id": res,
        "message": f"⚔️ Дуэль на {stake} 🪙 создана!"
    })


async def handle_accept_duel(request: web.Request) -> web.Response:
    """
    POST /api/duels/accept
    Body: {"duel_id": 3, "picks": {"1": "p2", "2": "tm25"}}
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
        duel_id = int(data.get("duel_id", 0))
        picks = dict(data.get("picks", {}))
    except Exception:
        return web.json_response({"status": "error", "error": "invalid_payload"}, status=400)

    success, msg = database.accept_pvp_duel(duel_id, user_id, picks)
    if not success:
        return web.json_response({"status": "error", "message": msg}, status=400)

    database.add_user_xp(user_id, 75)
    database.evaluate_quest_progress(user_id, "place_bets", 1)

    return web.json_response({
        "status": "ok",
        "message": msg
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
