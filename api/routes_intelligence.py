"""
api/routes_intelligence.py

Logovo.bet — Phase 7 Sports Intelligence & AI Prediction REST API Routes.
Provides:
- GET /api/intelligence/matches
- GET /api/intelligence/matches/{id}
- GET /api/intelligence/matches/{id}/preview
- GET /api/intelligence/matches/{id}/prediction
- GET /api/intelligence/matches/{id}/insights
- GET /api/intelligence/value (Value Radar)
- GET /api/intelligence/hot (Hot Matches 2.0)
- GET /api/intelligence/movers (Odds Anomaly Detection)
- GET /api/intelligence/history
- GET /api/intelligence/performance (Accuracy, Brier, Calibration)
- GET /api/admin/intelligence/overview (Admin monitoring)

Strict Invariants:
1. Analytical layer only — NEVER executes financial debits, credits, bets, or settlements.
2. Division and Season isolation strictly enforced.
3. User authorization and IDOR protection.
4. Input validation and pagination clamping on all endpoints.
"""

import logging
from typing import Any, Optional
from aiohttp import web

import database
from api.auth import get_authenticated_user, check_user_access
from services.intelligence_engine import IntelligenceEngine
from services.ensemble_engine import EnsemblePredictionEngine
from services.value_engine import ValueEngine, ValueRadar
from services.odds_movers import detect_odds_anomalies
from services.recommendation_engine import get_hot_matches
from services.backtest_engine import ModelPerformanceService, BacktestEngine

logger = logging.getLogger(__name__)


def _authenticate(request: web.Request) -> tuple[Optional[dict], Optional[web.Response]]:
    """Helper for Telegram initData authentication with closed lab check."""
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return None, web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]
    if not check_user_access(user_id):
        return None, web.json_response({
            "status": "error",
            "error": "lab_mode",
            "message": "Logovo.bet находится на закрытом тесте в Лаборатории."
        }, status=403)

    return user_info, None


async def handle_get_intelligence_matches(request: web.Request) -> web.Response:
    """
    GET /api/intelligence/matches?division_id=1&season_id=1&limit=20
    List upcoming matches with summary prediction indicators.
    """
    user_info, err_resp = _authenticate(request)
    if err_resp is not None:
        return err_resp

    div_str = request.query.get("division_id")
    sea_str = request.query.get("season_id")
    div_id = int(div_str) if div_str and div_str.isdigit() else None
    season_id = int(sea_str) if sea_str and sea_str.isdigit() else None

    try:
        limit = max(1, min(50, int(request.query.get("limit", 20))))
    except (ValueError, TypeError):
        limit = 20

    with database.transaction() as conn:
        cursor = conn.cursor()
        base_sql = """
            SELECT id, round_number, player1_team, player2_team, division_id, season_id, status
            FROM matches
            WHERE status IN ('open', 'scheduled', 'pending', 'live')
        """
        params: list[Any] = []
        if div_id is not None:
            base_sql += " AND division_id = ?"
            params.append(div_id)
        if season_id is not None:
            base_sql += " AND season_id = ?"
            params.append(season_id)

        base_sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cursor.execute(base_sql, params)
        matches = [dict(r) for r in cursor.fetchall()]

    summaries = []
    for m in matches:
        try:
            pred = EnsemblePredictionEngine.predict_match(m["id"])
            summaries.append({
                "match_id": m["id"],
                "round_number": m["round_number"],
                "division_id": m["division_id"],
                "season_id": m["season_id"],
                "team1": m["player1_team"],
                "team2": m["player2_team"],
                "status": m["status"],
                "home_probability": pred["home_probability"],
                "draw_probability": pred["draw_probability"],
                "away_probability": pred["away_probability"],
                "confidence": pred["confidence"],
                "model_version": pred["model_version"]
            })
        except Exception as ex:
            logger.debug(f"Could not calculate summary for match #{m['id']}: {ex}")

    return web.json_response({
        "status": "ok",
        "count": len(summaries),
        "matches": summaries
    })


async def handle_get_intelligence_match_detail(request: web.Request) -> web.Response:
    """
    GET /api/intelligence/matches/{id}
    Comprehensive match intelligence report.
    """
    user_info, err_resp = _authenticate(request)
    if err_resp is not None:
        return err_resp

    try:
        match_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Некорректный ID матча."}, status=400)

    try:
        data = IntelligenceEngine.get_match_intelligence(match_id)
        return web.json_response(data)
    except ValueError as ex:
        return web.json_response({"status": "error", "message": str(ex)}, status=404)
    except Exception as ex:
        logger.error(f"Error fetching intelligence for match #{match_id}: {ex}")
        return web.json_response({"status": "error", "message": "Внутренняя ошибка сервиса аналитики."}, status=500)


async def handle_get_intelligence_preview(request: web.Request) -> web.Response:
    """
    GET /api/intelligence/matches/{id}/preview
    Match preview card with Elo, form ratings, goal expectancies, and 'Why?' factors.
    """
    user_info, err_resp = _authenticate(request)
    if err_resp is not None:
        return err_resp

    try:
        match_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Некорректный ID матча."}, status=400)

    try:
        preview = IntelligenceEngine.get_match_preview(match_id)
        from services.live_ingestion import get_live_match_state
        from services.sports.freshness import evaluate_match_freshness
        live_state = get_live_match_state(match_id)
        if live_state and live_state.get("last_updated_at"):
            freshness = evaluate_match_freshness(live_state["last_updated_at"])
            preview["freshness"] = freshness
            if freshness.get("confidence_multiplier", 1.0) < 1.0:
                preview["confidence"] = round(preview.get("confidence", 0.70) * freshness["confidence_multiplier"], 3)
        else:
            preview["freshness"] = evaluate_match_freshness(None)
        return web.json_response(preview)
    except ValueError as ex:
        return web.json_response({"status": "error", "message": str(ex)}, status=404)
    except Exception as ex:
        logger.error(f"Error generating preview for match #{match_id}: {ex}")
        return web.json_response({"status": "error", "message": "Внутренняя ошибка генерации превью."}, status=500)


async def handle_get_intelligence_prediction(request: web.Request) -> web.Response:
    """
    GET /api/intelligence/matches/{id}/prediction
    Raw ensemble model prediction with probability distribution and confidence score.
    """
    user_info, err_resp = _authenticate(request)
    if err_resp is not None:
        return err_resp

    try:
        match_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Некорректный ID матча."}, status=400)

    try:
        pred = IntelligenceEngine.get_match_prediction(match_id)
        from services.live_ingestion import get_live_match_state
        from services.sports.freshness import evaluate_match_freshness
        live_state = get_live_match_state(match_id)
        if live_state and live_state.get("last_updated_at"):
            freshness = evaluate_match_freshness(live_state["last_updated_at"])
            pred["freshness"] = freshness
            if freshness.get("confidence_multiplier", 1.0) < 1.0:
                pred["confidence"] = round(pred.get("confidence", 0.70) * freshness["confidence_multiplier"], 3)
        else:
            pred["freshness"] = evaluate_match_freshness(None)
        return web.json_response({
            "status": "ok",
            "prediction": pred
        })
    except ValueError as ex:
        return web.json_response({"status": "error", "message": str(ex)}, status=404)
    except Exception as ex:
        logger.error(f"Error generating prediction for match #{match_id}: {ex}")
        return web.json_response({"status": "error", "message": "Внутренняя ошибка расчета прогноза."}, status=500)


async def handle_get_intelligence_insights(request: web.Request) -> web.Response:
    """
    GET /api/intelligence/matches/{id}/insights
    Factual bullet point insights for the match.
    """
    user_info, err_resp = _authenticate(request)
    if err_resp is not None:
        return err_resp

    try:
        match_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Некорректный ID матча."}, status=400)

    try:
        data = IntelligenceEngine.get_match_intelligence(match_id)
        return web.json_response({
            "status": "ok",
            "match_id": match_id,
            "insights": data.get("insights", [])
        })
    except ValueError as ex:
        return web.json_response({"status": "error", "message": str(ex)}, status=404)
    except Exception as ex:
        logger.error(f"Error fetching insights for match #{match_id}: {ex}")
        return web.json_response({"status": "error", "message": "Внутренняя ошибка инсайтов."}, status=500)


async def handle_get_value_radar(request: web.Request) -> web.Response:
    """
    GET /api/intelligence/value?division_id=1&season_id=1&min_edge=3.0&limit=15
    Value Radar scanner: identified selections with statistical positive edge.
    """
    user_info, err_resp = _authenticate(request)
    if err_resp is not None:
        return err_resp

    div_str = request.query.get("division_id")
    sea_str = request.query.get("season_id")
    div_id = int(div_str) if div_str and div_str.isdigit() else None
    season_id = int(sea_str) if sea_str and sea_str.isdigit() else None

    try:
        min_edge = float(request.query.get("min_edge", 3.0))
    except (ValueError, TypeError):
        min_edge = 3.0

    try:
        limit = max(1, min(50, int(request.query.get("limit", 15))))
    except (ValueError, TypeError):
        limit = 15

    radar_picks = ValueRadar.scan_radar(
        division_id=div_id,
        season_id=season_id,
        min_edge=min_edge,
        limit=limit
    )

    return web.json_response({
        "status": "ok",
        "count": len(radar_picks),
        "radar_picks": radar_picks,
        "disclaimer": "Положительный перевес модели указывает на статистическое расхождение с рынком и не является гарантией выигрыша."
    })


async def handle_get_hot_matches_v2(request: web.Request) -> web.Response:
    """
    GET /api/intelligence/hot?division_id=1&season_id=1&limit=10
    Multi-factor Hot Matches ranking.
    """
    user_info, err_resp = _authenticate(request)
    if err_resp is not None:
        return err_resp

    div_str = request.query.get("division_id")
    sea_str = request.query.get("season_id")
    div_id = int(div_str) if div_str and div_str.isdigit() else None
    season_id = int(sea_str) if sea_str and sea_str.isdigit() else None

    try:
        limit = max(1, min(50, int(request.query.get("limit", 10))))
    except (ValueError, TypeError):
        limit = 10

    hot = get_hot_matches(division_id=div_id, season_id=season_id, limit=limit)
    return web.json_response({
        "status": "ok",
        "count": len(hot),
        "hot_matches": hot
    })


async def handle_get_intelligence_movers(request: web.Request) -> web.Response:
    """
    GET /api/intelligence/movers?division_id=1&season_id=1&limit=20
    Odds Anomaly & Sharp Movement detection.
    """
    user_info, err_resp = _authenticate(request)
    if err_resp is not None:
        return err_resp

    div_str = request.query.get("division_id")
    sea_str = request.query.get("season_id")
    div_id = int(div_str) if div_str and div_str.isdigit() else None
    season_id = int(sea_str) if sea_str and sea_str.isdigit() else None

    try:
        limit = max(1, min(50, int(request.query.get("limit", 20))))
    except (ValueError, TypeError):
        limit = 20

    anomalies = detect_odds_anomalies(division_id=div_id, season_id=season_id, limit=limit)
    return web.json_response({
        "status": "ok",
        "count": len(anomalies),
        "anomalies": anomalies
    })


async def handle_get_intelligence_history(request: web.Request) -> web.Response:
    """
    GET /api/intelligence/history?division_id=1&season_id=1&limit=30
    Resolved predictions history with accuracy and Brier score.
    """
    user_info, err_resp = _authenticate(request)
    if err_resp is not None:
        return err_resp

    div_str = request.query.get("division_id")
    sea_str = request.query.get("season_id")
    div_id = int(div_str) if div_str and div_str.isdigit() else None
    season_id = int(sea_str) if sea_str and sea_str.isdigit() else None

    try:
        limit = max(1, min(100, int(request.query.get("limit", 30))))
    except (ValueError, TypeError):
        limit = 30

    with database.transaction() as conn:
        cursor = conn.cursor()
        base_sql = """
            SELECT p.*, m.player1_team, m.player2_team, m.round_number, m.player1_score, m.player2_score
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            WHERE p.resolved_at IS NOT NULL
        """
        params: list[Any] = []
        if div_id is not None:
            base_sql += " AND p.division_id = ?"
            params.append(div_id)
        if season_id is not None:
            base_sql += " AND p.season_id = ?"
            params.append(season_id)

        base_sql += " ORDER BY p.id DESC LIMIT ?"
        params.append(limit)
        cursor.execute(base_sql, params)
        rows = [dict(r) for r in cursor.fetchall()]

    return web.json_response({
        "status": "ok",
        "count": len(rows),
        "history": rows
    })


async def handle_get_intelligence_performance(request: web.Request) -> web.Response:
    """
    GET /api/intelligence/performance?division_id=1&season_id=1
    Empirical model performance scorecard and calibration curves.
    """
    user_info, err_resp = _authenticate(request)
    if err_resp is not None:
        return err_resp

    div_str = request.query.get("division_id")
    sea_str = request.query.get("season_id")
    div_id = int(div_str) if div_str and div_str.isdigit() else None
    season_id = int(sea_str) if sea_str and sea_str.isdigit() else None

    perf = ModelPerformanceService.get_performance_summary(division_id=div_id, season_id=season_id)
    return web.json_response(perf)


async def handle_admin_intelligence_overview(request: web.Request) -> web.Response:
    """
    GET /api/admin/intelligence/overview
    Admin health dashboard for predictive models, data completeness, and backtest results.
    """
    user_info, err_resp = _authenticate(request)
    if err_resp is not None:
        return err_resp

    # Check admin role
    user_id = user_info["id"]
    from config import ADMIN_IDS
    is_global = (user_id in ADMIN_IDS)

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT division_id FROM division_admins WHERE user_id = ?", (user_id,))
        admin_divs = [r["division_id"] for r in cursor.fetchall()]

    if not is_global and not admin_divs:
        return web.json_response({"status": "error", "error": "forbidden", "message": "Доступ запрещен."}, status=403)

    div_str = request.query.get("division_id")
    sea_str = request.query.get("season_id")
    div_id = int(div_str) if div_str and div_str.isdigit() else (admin_divs[0] if admin_divs else 1)
    season_id = int(sea_str) if sea_str and sea_str.isdigit() else 1

    if not is_global and div_id not in admin_divs:
        return web.json_response({
            "status": "error",
            "error": "forbidden",
            "message": f"Доступ запрещен для дивизиона #{div_id}."
        }, status=403)

    perf = ModelPerformanceService.get_performance_summary(division_id=div_id, season_id=season_id)
    backtest = BacktestEngine.run_walk_forward_backtest(division_id=div_id, season_id=season_id, warmup_matches=5)

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as total_preds FROM predictions")
        total_predictions = cursor.fetchone()["total_preds"]
        cursor.execute("SELECT COUNT(*) as total_ratings FROM team_ratings")
        total_ratings = cursor.fetchone()["total_ratings"]

    return web.json_response({
        "status": "ok",
        "active_models": {
            "primary": "ensemble_v1",
            "feature_version": "features_v1",
            "sub_models": ["poisson_2", "elo", "form"]
        },
        "database_stats": {
            "total_stored_predictions": total_predictions,
            "tracked_teams_elo": total_ratings
        },
        "performance": perf,
        "backtest_sample": backtest
    })
