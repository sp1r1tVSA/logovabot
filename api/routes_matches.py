"""
api/routes_matches.py

Logovo.bet — Match Center 3.0 API:
- Match overview, schedule & filters
- Form & performance analytics
- Head-to-Head (H2H) history
- Statistical Prediction Insights
- Live match score, minute & timeline
"""

import logging
from aiohttp import web
import database
from api.auth import get_authenticated_user, check_user_access

logger = logging.getLogger(__name__)


async def handle_get_matches(request: web.Request) -> web.Response:
    """
    GET /api/matches?tour=5&status=scheduled
    List fixtures with optional tour and status filters.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    tour_param = request.query.get("tour")
    status_param = request.query.get("status")
    division_id_param = request.query.get("division_id")
    season_id_param = request.query.get("season_id")

    with database.transaction() as conn:
        cursor = conn.cursor()
        query = """
            SELECT m.*, 
                   COALESCE(m.player1_team, 'Хозяева') as team1_name,
                   COALESCE(m.player2_team, 'Гости') as team2_name,
                   bm.odd_p1, bm.odd_x, bm.odd_p2,
                   bm.odd_tb25, bm.odd_tm25, bm.odd_btts_yes, bm.odd_btts_no
            FROM matches m
            LEFT JOIN bet_markets bm ON m.id = bm.match_id AND bm.is_active = 1
            WHERE 1=1
        """
        params = []
        if tour_param and tour_param.isdigit():
            query += " AND m.round_number = ?"
            params.append(int(tour_param))
        if status_param:
            query += " AND m.status = ?"
            params.append(status_param)
        if division_id_param and division_id_param.isdigit():
            query += " AND m.division_id = ?"
            params.append(int(division_id_param))
        if season_id_param and season_id_param.isdigit():
            query += " AND (m.season_id = ? OR m.season_id IS NULL)"
            params.append(int(season_id_param))

        query += " ORDER BY m.round_number ASC, m.id ASC LIMIT 100"
        cursor.execute(query, params)
        raw_rows = cursor.fetchall()
        matches = []
        for r in raw_rows:
            m_dict = dict(r)
            m_dict["odds"] = {
                "p1": round(m_dict.pop("odd_p1") or 1.90, 2),
                "x": round(m_dict.pop("odd_x") or 3.20, 2),
                "p2": round(m_dict.pop("odd_p2") or 2.10, 2),
                "tb25": round(m_dict.pop("odd_tb25") or 1.85, 2),
                "tm25": round(m_dict.pop("odd_tm25") or 1.85, 2),
                "btts_yes": round(m_dict.pop("odd_btts_yes") or 1.75, 2),
                "btts_no": round(m_dict.pop("odd_btts_no") or 1.95, 2),
            }
            matches.append(m_dict)

    return web.json_response({
        "status": "ok",
        "matches": matches
    })


async def handle_get_match_detail(request: web.Request) -> web.Response:
    """
    GET /api/matches/{id}
    Returns complete overview of a single match.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    try:
        match_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Некорректный ID матча."}, status=400)

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT m.*, 
                   COALESCE(m.player1_team, 'Хозяева') as team1_name,
                   COALESCE(m.player2_team, 'Гости') as team2_name,
                   t.name as tournament_name
            FROM matches m
            LEFT JOIN tournaments t ON m.tournament_id = t.id
            WHERE m.id = ?
        """, (match_id,))
        row = cursor.fetchone()

    if not row:
        return web.json_response({"status": "error", "message": "Матч не найден."}, status=404)

    return web.json_response({
        "status": "ok",
        "match": dict(row)
    })


def _get_team_recent_matches(cursor, team_name: str, limit: int = 5) -> list[dict]:
    """Retrieve last N finished matches for a team."""
    cursor.execute("""
        SELECT id, round_number, player1_team, player2_team, player1_score, player2_score, status, played_at
        FROM matches
        WHERE (LOWER(player1_team) = LOWER(?) OR LOWER(player2_team) = LOWER(?))
          AND status IN ('confirmed', 'completed', 'finished')
          AND player1_score IS NOT NULL AND player2_score IS NOT NULL
        ORDER BY id DESC
        LIMIT ?
    """, (team_name, team_name, limit))
    return [dict(r) for r in cursor.fetchall()]


async def handle_get_match_stats(request: web.Request) -> web.Response:
    """
    GET /api/matches/{id}/stats
    Returns team forms (W/D/L), goals scored/conceded, avg goals, Over 2.5 %.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    try:
        match_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Некорректный ID матча."}, status=400)

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
        m = cursor.fetchone()
        if not m:
            return web.json_response({"status": "error", "message": "Матч не найден."}, status=404)

        t1 = m["player1_team"] or "Команда 1"
        t2 = m["player2_team"] or "Команда 2"

        t1_matches = _get_team_recent_matches(cursor, t1, limit=5)
        t2_matches = _get_team_recent_matches(cursor, t2, limit=5)

    def compute_form(matches_list, team):
        norm = team.lower()
        form_letters = []
        gf = 0
        ga = 0
        over_25 = 0
        btts_count = 0

        for r in matches_list:
            is_home = (r["player1_team"] or "").lower() == norm
            my_score = r["player1_score"] if is_home else r["player2_score"]
            opp_score = r["player2_score"] if is_home else r["player1_score"]

            gf += my_score
            ga += opp_score
            if (my_score + opp_score) > 2.5:
                over_25 += 1
            if my_score > 0 and opp_score > 0:
                btts_count += 1

            if my_score > opp_score:
                form_letters.append("W")
            elif my_score == opp_score:
                form_letters.append("D")
            else:
                form_letters.append("L")

        total = max(1, len(matches_list))
        return {
            "form": form_letters,
            "goals_scored": gf,
            "goals_conceded": ga,
            "avg_goals_scored": round(gf / total, 1),
            "avg_goals_conceded": round(ga / total, 1),
            "over_25_pct": round((over_25 / total) * 100),
            "btts_pct": round((btts_count / total) * 100),
            "matches_count": len(matches_list)
        }

    return web.json_response({
        "status": "ok",
        "match_id": match_id,
        "team1": {
            "name": t1,
            "stats": compute_form(t1_matches, t1)
        },
        "team2": {
            "name": t2,
            "stats": compute_form(t2_matches, t2)
        }
    })


async def handle_get_match_h2h(request: web.Request) -> web.Response:
    """
    GET /api/matches/{id}/h2h
    Returns historical head-to-head match results between both clubs.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    try:
        match_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Некорректный ID матча."}, status=400)

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
        m = cursor.fetchone()
        if not m:
            return web.json_response({"status": "error", "message": "Матч не найден."}, status=404)

        t1 = m["player1_team"] or ""
        t2 = m["player2_team"] or ""

        cursor.execute("""
            SELECT id, round_number, player1_team, player2_team, player1_score, player2_score, status, played_at
            FROM matches
            WHERE ((LOWER(player1_team) = LOWER(?) AND LOWER(player2_team) = LOWER(?))
                OR (LOWER(player1_team) = LOWER(?) AND LOWER(player2_team) = LOWER(?)))
              AND status IN ('confirmed', 'completed', 'finished')
              AND player1_score IS NOT NULL AND player2_score IS NOT NULL
            ORDER BY id DESC
            LIMIT 10
        """, (t1, t2, t2, t1))
        h2h_matches = [dict(r) for r in cursor.fetchall()]

    t1_wins = 0
    draws = 0
    t2_wins = 0
    total_goals = 0

    for r in h2h_matches:
        s1 = r["player1_score"]
        s2 = r["player2_score"]
        total_goals += (s1 + s2)
        is_t1_p1 = (r["player1_team"] or "").lower() == t1.lower()
        t1_s = s1 if is_t1_p1 else s2
        t2_s = s2 if is_t1_p1 else s1

        if t1_s > t2_s:
            t1_wins += 1
        elif t1_s == t2_s:
            draws += 1
        else:
            t2_wins += 1

    total_h2h = max(1, len(h2h_matches))

    return web.json_response({
        "status": "ok",
        "match_id": match_id,
        "team1_name": t1,
        "team2_name": t2,
        "summary": {
            "total_meetings": len(h2h_matches),
            "team1_wins": t1_wins,
            "draws": draws,
            "team2_wins": t2_wins,
            "avg_goals_per_match": round(total_goals / total_h2h, 1) if h2h_matches else 0.0
        },
        "meetings": h2h_matches
    })


async def handle_get_match_insights(request: web.Request) -> web.Response:
    """
    GET /api/matches/{id}/insights
    Returns 3-5 data-driven statistical facts & trends.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    try:
        match_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Некорректный ID матча."}, status=400)

    insights = []
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
        m = cursor.fetchone()
        if not m:
            return web.json_response({"status": "error", "message": "Матч не найден."}, status=404)

        t1 = m["player1_team"] or "Команда 1"
        t2 = m["player2_team"] or "Команда 2"

        t1_matches = _get_team_recent_matches(cursor, t1, limit=6)
        t2_matches = _get_team_recent_matches(cursor, t2, limit=6)

    # 1. Scoring streak
    t1_scored_cnt = sum(
        1 for r in t1_matches 
        if (r["player1_score"] if (r["player1_team"] or "").lower() == t1.lower() else r["player2_score"]) > 0
    )
    if t1_scored_cnt >= 4:
        insights.append(f"⚽ {t1} забивает в {t1_scored_cnt} последних матчах подряд.")

    # 2. Over 2.5 trend
    over_25_count = sum(1 for r in (t1_matches + t2_matches) if (r["player1_score"] + r["player2_score"]) > 2.5)
    tot_checked = len(t1_matches) + len(t2_matches)
    if tot_checked > 0 and (over_25_count / tot_checked) >= 0.6:
        insights.append(f"🔥 В {over_25_count} из {tot_checked} недавних матчей команд проходил Тотал Больше 2.5.")

    # 3. Unbeaten run
    t2_unbeaten = 0
    for r in t2_matches:
        is_p1 = (r["player1_team"] or "").lower() == t2.lower()
        my_s = r["player1_score"] if is_p1 else r["player2_score"]
        opp_s = r["player2_score"] if is_p1 else r["player1_score"]
        if my_s >= opp_s:
            t2_unbeaten += 1
        else:
            break
    if t2_unbeaten >= 3:
        insights.append(f"🛡 {t2} идет без поражений в последних {t2_unbeaten} играх.")

    # 4. BTTS trend
    btts_cnt = sum(1 for r in t1_matches if r["player1_score"] > 0 and r["player2_score"] > 0)
    if btts_cnt >= 3:
        insights.append(f"🤝 Ставка 'Обе забьют' заходила в {btts_cnt} из {len(t1_matches)} последних матчей {t1}.")

    # Fallback default facts if few matches played
    if not insights:
        r_num = m["round_number"] if "round_number" in m.keys() else 1
        insights.append(f"📊 {t1} и {t2} готовятся к принципиальной встрече в Туре #{r_num}.")
        insights.append("⚡ Аналитика и коэффициенты формируются на основе текущей таблицы.")

    return web.json_response({
        "status": "ok",
        "match_id": match_id,
        "insights": insights
    })


async def handle_get_match_live(request: web.Request) -> web.Response:
    """
    GET /api/matches/{id}/live
    Returns live score, minute, and match event timeline.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    try:
        match_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Некорректный ID матча."}, status=400)

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
        m = cursor.fetchone()
        if not m:
            return web.json_response({"status": "error", "message": "Матч не найден."}, status=404)

        cursor.execute("""
            SELECT id, team_name, player_name, event_type, count
            FROM match_events
            WHERE match_id = ?
            ORDER BY id ASC
        """, (match_id,))
        events = [dict(r) for r in cursor.fetchall()]

    return web.json_response({
        "status": "ok",
        "match_id": match_id,
        "status": m["status"],
        "live_minute": m["live_minute"],
        "score1": m["player1_score"] or 0,
        "score2": m["player2_score"] or 0,
        "events": events
    })
