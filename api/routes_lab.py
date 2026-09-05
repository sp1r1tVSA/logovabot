"""
api/routes_lab.py

Logovo.bet — 🧪 ЛАБОРАТОРИЯ (Logovo Lab) REST API.
Admin and QA control endpoints for safe sportsbook sandbox testing on synthetic data.
"""

import json
import logging
from aiohttp import web

from api.auth import get_authenticated_user
from services import lab_service

logger = logging.getLogger("api.routes_lab")


def _resolve_actor_user_id(request: web.Request) -> int:
    """
    Resolve user ID for testing:
    1. Telegram initData user if authenticated.
    2. Header X-Test-User-Id or Query param user_id.
    3. Active lab config test user ID (defaults to 999999999).
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if user_info and "id" in user_info:
        return int(user_info["id"])

    header_uid = request.headers.get("X-Test-User-Id")
    if header_uid and header_uid.isdigit():
        return int(header_uid)

    query_uid = request.query.get("user_id")
    if query_uid and query_uid.isdigit():
        return int(query_uid)

    return lab_service.get_active_test_user_id()


async def handle_lab_status(request: web.Request) -> web.Response:
    """GET /api/lab/status — Dashboard overview & active environment metrics."""
    uid = _resolve_actor_user_id(request)
    data = lab_service.get_lab_dashboard_status(user_id=uid)
    return web.json_response({"status": "ok", **data})


async def handle_lab_create_season(request: web.Request) -> web.Response:
    """
    POST /api/lab/season/create — Generate synthetic test season.
    Body: {"season_name": "...", "division_name": "...", "teams_count": 16, "rounds_count": 30, "seed": 20260905}
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    s_name = body.get("season_name", lab_service.TEST_SEASON_NAME)
    d_name = body.get("division_name", lab_service.TEST_DIVISION_NAME)
    t_cnt = int(body.get("teams_count", 16))
    r_cnt = int(body.get("rounds_count", 30))
    seed = int(body.get("seed", lab_service.DEFAULT_SEED))

    try:
        result = lab_service.create_test_season(
            season_name=s_name,
            division_name=d_name,
            teams_count=t_cnt,
            rounds_count=r_cnt,
            seed=seed,
        )
        return web.json_response(result)
    except Exception as e:
        logger.exception(f"Error creating test season: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=400)


async def handle_lab_reset_season(request: web.Request) -> web.Response:
    """POST /api/lab/season/reset — Delete all synthetic test data."""
    uid = _resolve_actor_user_id(request)
    result = lab_service.reset_test_lab(user_id=uid)
    return web.json_response(result)


async def handle_lab_teams(request: web.Request) -> web.Response:
    """GET /api/lab/teams — Standings table for 16 synthetic teams."""
    standings = lab_service.get_teams_standings()
    return web.json_response({"status": "ok", "standings": standings})


async def handle_lab_matches(request: web.Request) -> web.Response:
    """GET /api/lab/matches — List matches in test season with round/status filters."""
    round_param = request.query.get("round")
    status_param = request.query.get("status")
    limit_param = request.query.get("limit", "50")
    offset_param = request.query.get("offset", "0")

    round_num = int(round_param) if round_param and round_param.isdigit() else None
    limit = int(limit_param) if limit_param.isdigit() else 50
    offset = int(offset_param) if offset_param.isdigit() else 0

    matches = lab_service.get_lab_matches(
        round_number=round_num,
        status=status_param,
        limit=limit,
        offset=offset,
    )
    return web.json_response({"status": "ok", "matches": matches, "count": len(matches)})


async def handle_lab_match_detail(request: web.Request) -> web.Response:
    """GET /api/lab/matches/{id} — Single match detail with markets, odds, live state."""
    m_id_str = request.match_info.get("id")
    if not m_id_str or not m_id_str.isdigit():
        return web.json_response({"status": "error", "message": "Invalid match ID."}, status=400)

    m_dict = lab_service.get_lab_match_detail(int(m_id_str))
    if not m_dict:
        return web.json_response({"status": "error", "message": "Match not found."}, status=404)

    return web.json_response({"status": "ok", "match": m_dict})


async def handle_lab_prepare_match(request: web.Request) -> web.Response:
    """
    POST /api/lab/matches/{id}/prepare — Prepare match for manual testing.
    Body: {"scenario_id": "...", "custom_odds": 1.85, "custom_score": [2, 0]}
    """
    m_id_str = request.match_info.get("id")
    if not m_id_str or not m_id_str.isdigit():
        return web.json_response({"status": "error", "message": "Invalid match ID."}, status=400)

    try:
        body = await request.json()
    except Exception:
        body = {}

    sc_id = body.get("scenario_id")
    c_odds = float(body["custom_odds"]) if "custom_odds" in body else None
    c_score = tuple(body["custom_score"]) if "custom_score" in body and isinstance(body["custom_score"], list) else None

    try:
        res = lab_service.prepare_match_for_test(
            match_id=int(m_id_str),
            scenario_id=sc_id,
            custom_odds=c_odds,
            custom_score=c_score,
        )
        return web.json_response(res)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)


async def handle_lab_transition_match(request: web.Request) -> web.Response:
    """
    POST /api/lab/matches/{id}/status — Change match lifecycle state.
    Body: {"status": "OPEN" | "LIVE" | "HALFTIME" | "FINISHED" | "COMPLETED"}
    """
    m_id_str = request.match_info.get("id")
    if not m_id_str or not m_id_str.isdigit():
        return web.json_response({"status": "error", "message": "Invalid match ID."}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Invalid JSON."}, status=400)

    target_status = body.get("status")
    if not target_status:
        return web.json_response({"status": "error", "message": "Target status required."}, status=400)

    try:
        res = lab_service.transition_match_lifecycle(int(m_id_str), target_status)
        return web.json_response(res)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)


async def handle_lab_live_event(request: web.Request) -> web.Response:
    """
    POST /api/lab/matches/{id}/live-event — Dispatch live event (+goal, cards, halftime).
    Body: {"action": "goal" | "yellow_card" | "red_card" | "halftime" | "fulltime", "side": "home"|"away", "minute": 62}
    """
    m_id_str = request.match_info.get("id")
    if not m_id_str or not m_id_str.isdigit():
        return web.json_response({"status": "error", "message": "Invalid match ID."}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Invalid JSON."}, status=400)

    action = body.get("action")
    side = body.get("side", "home")
    minute = int(body["minute"]) if "minute" in body and str(body["minute"]).isdigit() else None

    if not action:
        return web.json_response({"status": "error", "message": "Action required."}, status=400)

    try:
        res = lab_service.send_live_event_action(int(m_id_str), action=action, side=side, minute=minute)
        return web.json_response(res)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)


async def handle_lab_match_result(request: web.Request) -> web.Response:
    """
    POST /api/lab/matches/{id}/result — Set final score and/or settle bets.
    Body: {"score1": 2, "score2": 0, "settle": true}
    """
    m_id_str = request.match_info.get("id")
    if not m_id_str or not m_id_str.isdigit():
        return web.json_response({"status": "error", "message": "Invalid match ID."}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Invalid JSON."}, status=400)

    if "score1" not in body or "score2" not in body:
        return web.json_response({"status": "error", "message": "score1 and score2 required."}, status=400)

    score1 = int(body["score1"])
    score2 = int(body["score2"])
    confirm_settle = bool(body.get("settle", True))

    try:
        res = lab_service.set_match_result_and_settle(
            match_id=int(m_id_str),
            score1=score1,
            score2=score2,
            confirm_and_settle=confirm_settle,
        )
        return web.json_response(res)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)


async def handle_lab_scenarios(request: web.Request) -> web.Response:
    """GET /api/lab/scenarios — List all predefined quick test scenarios."""
    scenarios = lab_service.list_predefined_scenarios()
    return web.json_response({"status": "ok", "scenarios": scenarios})


async def handle_lab_apply_scenario(request: web.Request) -> web.Response:
    """POST /api/lab/scenarios/{id}/apply — One-click apply scenario."""
    sc_id = request.match_info.get("id")
    if not sc_id:
        return web.json_response({"status": "error", "message": "Scenario ID required."}, status=400)

    try:
        res = lab_service.apply_scenario(sc_id)
        return web.json_response(res)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)


async def handle_lab_step_tracker(request: web.Request) -> web.Response:
    """GET /api/lab/step-tracker — Current test step & manual bet detection status."""
    uid = _resolve_actor_user_id(request)
    data = lab_service.get_step_tracker_status(user_id=uid)
    return web.json_response({"status": "ok", **data})


async def handle_lab_bets(request: web.Request) -> web.Response:
    """GET /api/lab/bets — Retrieve bets placed by test player."""
    uid = _resolve_actor_user_id(request)
    limit_param = request.query.get("limit", "50")
    limit = int(limit_param) if limit_param.isdigit() else 50
    bets = lab_service.get_test_player_bets(user_id=uid, limit=limit)
    return web.json_response({"status": "ok", "bets": bets, "count": len(bets)})


async def handle_lab_financial(request: web.Request) -> web.Response:
    """GET /api/lab/financial — Mathematical balance audit & transaction log."""
    uid = _resolve_actor_user_id(request)
    data = lab_service.get_financial_reconciliation(user_id=uid)
    return web.json_response(data)


async def handle_lab_season_control(request: web.Request) -> web.Response:
    """GET /api/lab/season/control — Overview of all 30 rounds."""
    data = lab_service.get_season_control_overview()
    return web.json_response(data)


async def handle_lab_round_action(request: web.Request) -> web.Response:
    """
    POST /api/lab/season/rounds/{round}/action — Execute action on test round.
    Body: {"action": "open" | "close" | "complete"}
    """
    r_str = request.match_info.get("round")
    if not r_str or not r_str.isdigit():
        return web.json_response({"status": "error", "message": "Invalid round number."}, status=400)

    try:
        body = await request.json()
    except Exception:
        body = {}

    action = body.get("action", "open")
    try:
        res = lab_service.manage_round_action(int(r_str), action)
        return web.json_response(res)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)


async def handle_lab_settings_user(request: web.Request) -> web.Response:
    """
    POST /api/lab/settings/user — Set active test user ID or reset wallet balance.
    Body: {"user_id": 999999999, "balance": 100000}
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Invalid JSON."}, status=400)

    target_uid = int(body.get("user_id", lab_service.DEFAULT_TEST_USER_ID))
    target_bal = int(body.get("balance", lab_service.INITIAL_TEST_BALANCE))

    lab_service.set_active_test_user_id(target_uid)
    lab_service.ensure_test_user(target_uid, target_bal)

    return web.json_response({
        "status": "ok",
        "message": f"Active test player switched to #{target_uid}. Balance set to {target_bal:,} 🪙.",
        "test_user_id": target_uid,
        "balance": target_bal,
    })
