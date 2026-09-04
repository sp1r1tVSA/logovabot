"""
api/routes_admin_season.py

Admin Season Center & Seasonal Economy Management API.
Strict Invariants:
1. RBAC:
   Global Admins: Full control over season lifecycle, division configurations, and finalization.
   Division Admins: Strictly scoped to their assigned division(s). Cannot finalize or create seasons.
   Players: 403 Forbidden.
2. Idempotency: Season finalization can only succeed once per season.
3. Transactional Audit Logging for every administrative change.
"""

import json
import logging
from aiohttp import web
from config import ADMIN_IDS
from .auth import get_authenticated_user
import database
from services.season_progression import SeasonProgressionEngine

logger = logging.getLogger("api.admin_season")


def _get_admin_actor(request: web.Request) -> tuple[int | None, bool, list[int], web.Response | None]:
    """
    Authenticate and extract actor ID, global admin flag, and assigned division IDs.
    Returns (actor_id, is_global_admin, assigned_divisions, error_response).
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return None, False, [], web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    actor_id = int(user_info["id"])
    is_global = (actor_id in ADMIN_IDS)

    assigned_divs = []
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT division_id FROM division_admins WHERE user_id = ?", (actor_id,))
        assigned_divs = [r["division_id"] for r in cursor.fetchall()]

    if not is_global and not assigned_divs:
        return None, False, [], web.json_response({"status": "error", "error": "forbidden"}, status=403)

    return actor_id, is_global, assigned_divs, None


async def handle_admin_get_season(request: web.Request) -> web.Response:
    """
    GET /api/admin/season
    Returns season overview, divisions, and standings.
    Division Admins are strictly scoped to their assigned divisions.
    """
    actor_id, is_global, assigned_divs, err = _get_admin_actor(request)
    if err is not None:
        return err

    seasons = database.list_seasons()
    active_s = database.get_active_season()
    s_id = active_s["id"] if active_s else 1

    with database.transaction() as conn:
        cursor = conn.cursor()
        if is_global:
            cursor.execute("SELECT * FROM divisions WHERE is_active = 1 ORDER BY sort_order")
        else:
            placeholders = ",".join("?" for _ in assigned_divs)
            cursor.execute(f"SELECT * FROM divisions WHERE is_active = 1 AND id IN ({placeholders}) ORDER BY sort_order", tuple(assigned_divs))
        divisions = [dict(r) for r in cursor.fetchall()]

    # Collect standings & rules per accessible division
    div_summaries = []
    for d in divisions:
        d_id = d["id"]
        standings = SeasonProgressionEngine.get_division_standings(s_id, d_id)
        rules = database.get_season_rules(s_id, d_id)
        div_summaries.append({
            "division": d,
            "rules": rules,
            "total_players": len(standings),
            "standings_preview": standings[:5]
        })

    return web.json_response({
        "status": "ok",
        "active_season": active_s,
        "all_seasons": seasons,
        "accessible_divisions": div_summaries,
        "is_global_admin": is_global
    })


async def handle_admin_create_season(request: web.Request) -> web.Response:
    """
    POST /api/admin/season
    Global Admin only: Create a new draft season or configure division rules.
    Body: {"action": "create", "name": "Season 2"}
    OR {"action": "configure_rules", "season_id": 1, "division_id": 2, "promotion_slots": 4, ...}
    """
    actor_id, is_global, assigned_divs, err = _get_admin_actor(request)
    if err is not None:
        return err

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Invalid JSON payload."}, status=400)

    action = data.get("action", "create")

    if action == "create":
        if not is_global:
            return web.json_response({"status": "error", "error": "forbidden", "message": "Only Global Admins can create new seasons."}, status=403)
        name = str(data.get("name", "")).strip()
        if not name:
            return web.json_response({"status": "error", "message": "Season name is required."}, status=400)

        s_id = database.create_season(name=name, created_by=actor_id)
        return web.json_response({
            "status": "ok",
            "message": f"Сезон #{s_id} ('{name}') создан.",
            "season_id": s_id
        })

    elif action == "configure_rules":
        s_id = int(data.get("season_id", 1))
        d_id = int(data.get("division_id", 1))

        if not is_global and d_id not in assigned_divs:
            return web.json_response({"status": "error", "error": "forbidden", "message": "Cannot configure division outside your assignment."}, status=403)

        prom_slots = int(data.get("promotion_slots", 3))
        rel_slots = int(data.get("relegation_slots", 3))
        min_b = int(data.get("min_bets_qualification", 5))
        min_m = int(data.get("min_matches_qualification", 3))

        database.set_season_rules(
            season_id=s_id,
            division_id=d_id,
            promotion_slots=prom_slots,
            relegation_slots=rel_slots,
            min_bets_qualification=min_b,
            min_matches_qualification=min_m
        )

        return web.json_response({
            "status": "ok",
            "message": f"Правила для сезона #{s_id} дивизиона #{d_id} обновлены.",
            "rules": {
                "season_id": s_id,
                "division_id": d_id,
                "promotion_slots": prom_slots,
                "relegation_slots": rel_slots,
                "min_bets_qualification": min_b,
                "min_matches_qualification": min_m
            }
        })

    return web.json_response({"status": "error", "message": f"Unknown action '{action}'."}, status=400)


async def handle_admin_finalize_season(request: web.Request) -> web.Response:
    """
    POST /api/admin/season/finalize
    Global Admin only: Idempotent season finalization, standings snapshot, and reward distribution.
    Body: {"season_id": 1, "confirm": true}
    """
    actor_id, is_global, _, err = _get_admin_actor(request)
    if err is not None:
        return err

    if not is_global:
        return web.json_response({"status": "error", "error": "forbidden", "message": "Only Global Admins can finalize seasons."}, status=403)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Invalid JSON payload."}, status=400)

    if not data.get("confirm"):
        return web.json_response({"status": "error", "message": "Explicit 'confirm: true' required to finalize season."}, status=400)

    try:
        season_id = int(data.get("season_id", 0))
    except ValueError:
        return web.json_response({"status": "error", "message": "Invalid season_id."}, status=400)

    if season_id <= 0:
        act = database.get_active_season()
        if not act:
            return web.json_response({"status": "error", "message": "No active season to finalize."}, status=400)
        season_id = act["id"]

    success, message, result = SeasonProgressionEngine.finalize_season(season_id, actor_id=actor_id)
    if not success:
        return web.json_response({"status": "error", "message": message, "details": result}, status=400)

    return web.json_response({
        "status": "ok",
        "message": message,
        "result": result
    })


async def handle_admin_season_rewards(request: web.Request) -> web.Response:
    """
    POST /api/admin/season/rewards
    Configure or create custom season rewards.
    Global Admin only.
    """
    actor_id, is_global, _, err = _get_admin_actor(request)
    if err is not None:
        return err

    if not is_global:
        return web.json_response({"status": "error", "error": "forbidden"}, status=403)

    try:
        data = await request.json()
        reward_id = str(data.get("id", "")).strip()
        name = str(data.get("name", "")).strip()
        r_type = str(data.get("reward_type", "coins")).strip()
        amount = int(data.get("amount", 0))
        criteria = str(data.get("criteria", "PARTICIPATION")).strip()
    except Exception:
        return web.json_response({"status": "error", "message": "Invalid reward payload."}, status=400)

    if not reward_id or not name:
        return web.json_response({"status": "error", "message": "Reward id and name are required."}, status=400)

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO season_rewards_catalog (id, name, reward_type, amount, criteria)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                reward_type = excluded.reward_type,
                amount = excluded.amount,
                criteria = excluded.criteria
        """, (reward_id, name, r_type, amount, criteria))

    return web.json_response({
        "status": "ok",
        "message": f"Награда '{name}' сохранена.",
        "reward_id": reward_id
    })
