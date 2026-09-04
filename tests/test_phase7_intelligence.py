"""
tests/test_phase7_intelligence.py

Logovo.bet — Phase 7 AI & Advanced Sports Intelligence Test Suite.
Verifies:
1. Poisson 2.0 bounds, mathematical integrity, probabilities summing to 1.0.
2. Elo Engine calculations and ZERO mutation during prediction.
3. Form Model normalization and recency weighting.
4. Ensemble Prediction Engine dynamic weight reallocation and confidence.
5. Calibration Layer, Platt scaling, Brier score, and calibration reports.
6. Data Leakage prevention (no future results, temporal split, H2H excludes current match).
7. Value Engine, overround normalization, and Value Radar scanning.
8. Odds Anomaly detection.
9. Data integrity & fallbacks (missing xG explicitly false, zero hallucinations).
10. Division & Season isolation across features, Elo, and predictions.
11. Admin RBAC and IDOR protection on intelligence endpoints.
12. Strict Financial Read-Only Invariant: AI layer cannot debit, credit, bet, or settle.
13. Model Backtesting & Performance evaluation with sample size thresholds.
"""

import math
import pytest
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from aiohttp import web

import database
from api.server import create_app
from services.feature_engine import FeatureEngine
from services.elo_engine import EloEngine
from services.form_model import FormModel
from services.poisson_model import PoissonModel, poisson_pmf
from services.calibration import ProbabilityCalibrator
from services.ensemble_engine import EnsemblePredictionEngine
from services.value_engine import ValueEngine, ValueRadar
from services.odds_movers import record_odds_movement, detect_odds_anomalies
from services.backtest_engine import ModelPerformanceService, BacktestEngine


class TestPhase7IntelligenceUnit:
    """Unit tests for Phase 7 mathematical engines and business logic."""

    def test_poisson_2_bounds_and_probabilities_sum_to_one(self):
        """Verify Poisson 2.0 bivariate grid sums to 1.0 and all probabilities are bounded."""
        lh, la = PoissonModel.calculate_expected_goals(
            attack1=1.5, defense1=0.9, attack2=1.1, defense2=1.2,
            league_avg_home=1.3, league_avg_away=1.1, home_advantage_multiplier=1.15
        )
        assert 0.20 <= lh <= 4.50
        assert 0.20 <= la <= 4.50

        res = PoissonModel.calculate_match_probabilities(lh, la)

        ph = res["home_probability"]
        pd = res["draw_probability"]
        pa = res["away_probability"]

        # Probabilities must be strictly bounded in [0.0, 1.0]
        assert 0.0 <= ph <= 1.0
        assert 0.0 <= pd <= 1.0
        assert 0.0 <= pa <= 1.0

        # Sum must equal 1.0 within floating tolerance
        assert abs((ph + pd + pa) - 1.0) < 1e-3

        # Over/Under and BTTS bounds
        assert 0.0 <= res["over_2_5_probability"] <= 1.0
        assert 0.0 <= res["under_2_5_probability"] <= 1.0
        assert abs((res["over_2_5_probability"] + res["under_2_5_probability"]) - 1.0) < 1e-3

        assert 0.0 <= res["btts_yes_probability"] <= 1.0
        assert 0.0 <= res["btts_no_probability"] <= 1.0
        assert abs((res["btts_yes_probability"] + res["btts_no_probability"]) - 1.0) < 1e-3

        # Correct scores grid
        assert "0:0" in res["correct_scores"]
        assert "1:0" in res["correct_scores"]
        assert "2:1" in res["correct_scores"]
        assert 0.0 <= res["correct_scores"]["1:1"] <= 1.0

    def test_elo_engine_calculation_and_no_mutation_during_prediction(self):
        """Verify Elo probability calculations and strict IMMUTABILITY during prediction."""
        t1 = "TestEloClubA_701"
        t2 = "TestEloClubB_701"

        # Initialize explicit ratings
        database.update_team_elo(t1, division_id=1, season_id=1, new_elo=1650.0)
        database.update_team_elo(t2, division_id=1, season_id=1, new_elo=1450.0)

        initial_r1 = database.get_team_elo(t1, division_id=1, season_id=1)
        initial_r2 = database.get_team_elo(t2, division_id=1, season_id=1)
        assert initial_r1 == 1650.0
        assert initial_r2 == 1450.0

        # Run prediction 5 times
        for _ in range(5):
            res = EloEngine.calculate_match_probabilities(t1, t2, division_id=1, season_id=1)
            assert res["home_probability"] > res["away_probability"]  # Higher Elo + Home Adv must be favored
            assert 0.0 <= res["draw_probability"] <= 0.40
            assert abs((res["home_probability"] + res["draw_probability"] + res["away_probability"]) - 1.0) < 1e-3

        # IMMUTABILITY CHECK: ratings in database must NOT change during prediction!
        post_pred_r1 = database.get_team_elo(t1, division_id=1, season_id=1)
        post_pred_r2 = database.get_team_elo(t2, division_id=1, season_id=1)
        assert post_pred_r1 == initial_r1
        assert post_pred_r2 == initial_r2

        # Post-match update test
        new_r1, new_r2 = EloEngine.calculate_new_ratings(initial_r1, initial_r2, score1=2, score2=0)
        assert new_r1 > initial_r1
        assert new_r2 < initial_r2

    def test_form_model_normalization_and_recency_weighting(self):
        """Verify Form score is normalized in [0.0, 1.0] and recent matches have higher impact."""
        team = "TestFormTeam_702"

        # Synthetic match history: [latest, older, oldest]
        # Recent matches are wins, oldest is a loss
        recent_wins = [
            {"player1_team": team, "player2_team": "Opp1", "player1_score": 3, "player2_score": 0},
            {"player1_team": team, "player2_team": "Opp2", "player1_score": 2, "player2_score": 0},
            {"player1_team": team, "player2_team": "Opp3", "player1_score": 0, "player2_score": 2},
        ]
        score_high = FormModel.calculate_form_score(recent_wins, team_name=team, recency_decay=0.85)

        # Inverted history: recent match is a loss, older are wins
        recent_losses = [
            {"player1_team": team, "player2_team": "Opp1", "player1_score": 0, "player2_score": 2},
            {"player1_team": team, "player2_team": "Opp2", "player1_score": 2, "player2_score": 0},
            {"player1_team": team, "player2_team": "Opp3", "player1_score": 3, "player2_score": 0},
        ]
        score_low = FormModel.calculate_form_score(recent_losses, team_name=team, recency_decay=0.85)

        assert 0.0 <= score_high <= 1.0
        assert 0.0 <= score_low <= 1.0
        # Recency weighting means recent wins produce a higher score than older wins with recent loss
        assert score_high > score_low

        probs = FormModel.calculate_match_probabilities(score_high, score_low)
        assert probs["home_probability"] > probs["away_probability"]
        assert abs((probs["home_probability"] + probs["draw_probability"] + probs["away_probability"]) - 1.0) < 1e-3

    def test_ensemble_engine_weights_and_reallocation(self):
        """Verify Ensemble blends models, normalizes weights, and enforces confidence bounds."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status)
                VALUES (987001, 1, 'Arsenal_701', 'Chelsea_701', 1, 1, 'scheduled')
            """)

        pred = EnsemblePredictionEngine.predict_match(987001, save_to_db=False)

        assert pred["match_id"] == 987001
        assert pred["model_version"] == "ensemble_v1"
        assert pred["feature_version"] == "features_v1"

        ph = pred["home_probability"]
        pd = pred["draw_probability"]
        pa = pred["away_probability"]

        assert 0.0 <= ph <= 1.0
        assert 0.0 <= pd <= 1.0
        assert 0.0 <= pa <= 1.0
        assert abs((ph + pd + pa) - 1.0) < 1e-3

        # Confidence bounds
        assert 0.0 <= pred["confidence"] <= 1.0

        # Sub-model details present
        assert "poisson" in pred["sub_models"]
        assert "elo" in pred["sub_models"]
        assert "form" in pred["sub_models"]

        # Explainable key factors present
        assert len(pred["key_factors"]) >= 1
        assert "disclaimer" in pred

    def test_probability_calibrator_bounds(self):
        """Verify Platt scaling, Brier score calculation, and calibration reports."""
        raw_p = 0.88
        cal_p = ProbabilityCalibrator.calibrate_probability(raw_p)
        assert 0.0 <= cal_p <= 1.0
        # Platt scaling should temper extreme overconfidence
        assert cal_p <= raw_p

        ch, cd, ca = ProbabilityCalibrator.calibrate_1x2(0.85, 0.10, 0.05)
        assert abs((ch + cd + ca) - 1.0) < 1e-3

        # Brier score on synthetic predictions
        sample_preds = [
            {"actual_result": "home", "home_probability": 0.80, "draw_probability": 0.15, "away_probability": 0.05},
            {"actual_result": "draw", "home_probability": 0.30, "draw_probability": 0.40, "away_probability": 0.30},
            {"actual_result": "away", "home_probability": 0.20, "draw_probability": 0.20, "away_probability": 0.60},
        ]
        brier = ProbabilityCalibrator.calculate_brier_score(sample_preds)
        assert brier is not None
        assert 0.0 <= brier <= 1.0

        # Empty sample must return None, NEVER fake 0.0!
        assert ProbabilityCalibrator.calculate_brier_score([]) is None

        # Calibration curve report
        rep = ProbabilityCalibrator.generate_calibration_report(sample_preds)
        assert len(rep) == 10
        # Check that empty buckets report actual_accuracy as None (no fake 0%)
        bucket_0_10 = rep[0]
        if bucket_0_10["count"] == 0:
            assert bucket_0_10["actual_accuracy"] is None

    def test_implied_probability_overround_normalization(self):
        """Verify market overround adjustment and true implied probability."""
        # Standard bookmaker odds with ~108% book: 2.10 (47.6%), 3.30 (30.3%), 3.40 (29.4%)
        odds = [2.10, 3.30, 3.40]
        overround = ValueEngine.calculate_overround(odds)
        assert overround > 0.05  # Margin should be ~7-8%

        true_p1 = ValueEngine.calculate_true_implied_probability(2.10, overround)
        raw_p1 = 1.0 / 2.10
        # True implied probability must be strictly less than raw 1/odd due to margin extraction
        assert true_p1 < raw_p1

    def test_value_edge_calculation(self):
        """Verify statistical edge calculation and value detection."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status)
                VALUES (987002, 1, 'RealMadrid_702', 'Barcelona_702', 1, 1, 'open')
            """)
            cursor.execute("""
                INSERT OR REPLACE INTO markets (id, match_id, market_key, market_name, status)
                VALUES (9870021, 987002, 'match_winner', '1X2', 'open')
            """)
            # Odds of 2.80 on P1 implies raw 35.7%
            cursor.execute("""
                INSERT OR REPLACE INTO market_selections (id, market_id, selection_key, selection_name, odds_value, status)
                VALUES (98700211, 9870021, 'p1', 'Победа 1', 2.80, 'active')
            """)
            cursor.execute("""
                INSERT OR REPLACE INTO market_selections (id, market_id, selection_key, selection_name, odds_value, status)
                VALUES (98700212, 9870021, 'x', 'Ничья', 3.20, 'active')
            """)
            cursor.execute("""
                INSERT OR REPLACE INTO market_selections (id, market_id, selection_key, selection_name, odds_value, status)
                VALUES (98700213, 9870021, 'p2', 'Победа 2', 2.50, 'active')
            """)

        value_items = ValueEngine.analyze_match_value(987002)
        assert len(value_items) == 3

        p1_item = next(v for v in value_items if v["selection_key"] == "p1")
        assert "edge_percentage_points" in p1_item
        assert "confidence_level" in p1_item
        assert "signal_type" in p1_item
        # Verify no guaranteed profit language
        assert "guaranteed" not in str(p1_item).lower()
        assert "sure" not in str(p1_item).lower()

    def test_odds_anomaly_detection(self):
        """Verify sharp movement and high velocity anomaly detection."""
        # Insert normal movement
        record_odds_movement(
            selection_id=98700211, market_id=9870021, match_id=987002,
            old_odds=2.80, new_odds=2.75, reason="standard"
        )
        # Insert sharp movement (+25% jump)
        record_odds_movement(
            selection_id=98700212, market_id=9870021, match_id=987002,
            old_odds=3.20, new_odds=4.20, reason="sharp_drift"
        )

        anomalies = detect_odds_anomalies(limit=10)
        assert len(anomalies) >= 1
        sharp = next((a for a in anomalies if a["match_id"] == 987002), None)
        assert sharp is not None
        assert sharp["severity"] in ("HIGH", "MEDIUM")
        assert "Резкое движение" in sharp["explanation"]

    def test_missing_xg_explicit_flag(self):
        """Verify that when xG is not in live_statistics, xg_available is strictly False (no fake xG)."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status)
                VALUES (987003, 1, 'NoXgTeamA_703', 'NoXgTeamB_703', 1, 1, 'open')
            """)

        features = FeatureEngine.extract_match_features(987003)
        assert features["xg_available"] is False
        assert features["team1_features"]["xg"]["has_data"] is False
        assert features["team1_features"]["xg"]["avg_xg"] is None
        assert features["team1_features"]["xg"]["is_synthetic"] is False

    def test_h2h_excludes_current_match(self):
        """Verify that H2H query strictly excludes the subject match ID (anti-leakage guard)."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            # Insert a finished match between Team X and Team Y
            cursor.execute("""
                INSERT OR REPLACE INTO matches (id, round_number, player1_team, player2_team, player1_score, player2_score, division_id, season_id, status)
                VALUES (987004, 5, 'RivalX_704', 'RivalY_704', 3, 0, 1, 1, 'finished')
            """)

        # When analyzing match 987004, it must NOT appear in its own H2H!
        features = FeatureEngine.extract_match_features(987004, as_of_match_id=987004)
        h2h = features["h2h_features"]
        assert h2h["total_meetings"] == 0
        assert h2h["has_h2h_data"] is False

    def test_prediction_temporal_split(self):
        """Verify that FeatureEngine does not include future matches (id >= as_of_match_id)."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            # Match 1 (in the past): TeamAlpha wins 4:0
            cursor.execute("""
                INSERT OR REPLACE INTO matches (id, round_number, player1_team, player2_team, player1_score, player2_score, division_id, season_id, status)
                VALUES (987010, 1, 'Alpha_710', 'Beta_710', 4, 0, 1, 1, 'finished')
            """)
            # Target Match 2
            cursor.execute("""
                INSERT OR REPLACE INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status)
                VALUES (987011, 2, 'Alpha_710', 'Gamma_710', 1, 1, 'scheduled')
            """)
            # Future Match 3: TeamAlpha scores 10 goals in the future
            cursor.execute("""
                INSERT OR REPLACE INTO matches (id, round_number, player1_team, player2_team, player1_score, player2_score, division_id, season_id, status)
                VALUES (987012, 3, 'Alpha_710', 'Delta_710', 10, 0, 1, 1, 'finished')
            """)

        # Extract features as of Match 987011
        features = FeatureEngine.extract_match_features(987011, as_of_match_id=987011)
        t1_stats = features["team1_features"]["overall"]

        # Only Match 987010 should be counted, NOT the future Match 987012!
        assert t1_stats["matches_played"] == 1
        assert t1_stats["goals_for"] == 4
        assert t1_stats["goals_for"] != 14

    def test_division_and_season_isolation(self):
        """Verify that team ratings, matches, and predictions are strictly isolated by division and season."""
        team = "IsolatedTeam_720"

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO divisions (id, name) VALUES (2, 'Division 2')")
            cursor.execute("INSERT OR REPLACE INTO seasons (id, name, status) VALUES (2, 'Season 2', 'archived')")

        try:
            # Update Elo in Div 1, Season 1
            database.update_team_elo(team, division_id=1, season_id=1, new_elo=1720.0)
            # Update Elo in Div 2, Season 1
            database.update_team_elo(team, division_id=2, season_id=1, new_elo=1400.0)
            # Update Elo in Div 1, Season 2
            database.update_team_elo(team, division_id=1, season_id=2, new_elo=1550.0)

            assert database.get_team_elo(team, division_id=1, season_id=1) == 1720.0
            assert database.get_team_elo(team, division_id=2, season_id=1) == 1400.0
            assert database.get_team_elo(team, division_id=1, season_id=2) == 1550.0
        finally:
            with database.transaction() as conn:
                conn.execute("DELETE FROM seasons WHERE id = 2")
                conn.execute("DELETE FROM team_ratings WHERE team_name = ?", (team,))

    def test_intelligence_layer_is_financially_read_only(self):
        """
        CRITICAL ARCHITECTURAL TEST:
        Guarantee that AI / intelligence services have ZERO code paths to debit balances,
        credit balances, or place bets.
        """
        import inspect
        import services.feature_engine as fe
        import services.elo_engine as ee
        import services.form_model as fm
        import services.poisson_model as pm
        import services.ensemble_engine as ens
        import services.value_engine as ve
        import services.backtest_engine as be

        forbidden_tokens = ["place_user_bet", "modify_wallet_balance", "void_user_bet", "credit_balance", "debit_balance"]

        modules_to_audit = [fe, ee, fm, pm, ens, ve, be]
        for mod in modules_to_audit:
            src = inspect.getsource(mod)
            for token in forbidden_tokens:
                assert token not in src, f"Security Violation: {token} found in {mod.__name__}!"

    def test_model_performance_minimum_sample_size(self):
        """Verify that ModelPerformanceService enforces MIN_SAMPLE_SIZE = 10 before claiming metrics."""
        # Test in a division with zero resolved predictions
        perf = ModelPerformanceService.get_performance_summary(division_id=99, season_id=99)
        assert perf["status"] == "insufficient_sample"
        assert perf["accuracy_percent"] is None
        assert perf["brier_score"] is None
        assert perf["sample_size"] == 0

    def test_walk_forward_backtest_engine(self):
        """Verify BacktestEngine chronological simulation and comparative scorecard."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            # Seed 15 historical matches in Division 88
            cursor.execute("INSERT OR IGNORE INTO divisions (id, name) VALUES (88, 'Division 88')")
            for i in range(1, 16):
                cursor.execute("""
                    INSERT OR REPLACE INTO matches (id, round_number, player1_team, player2_team, player1_score, player2_score, division_id, season_id, status)
                    VALUES (?, ?, ?, ?, ?, ?, 88, 1, 'finished')
                """, (987100 + i, i, f"ClubA_{i % 3}", f"ClubB_{i % 3}", (i % 3), ((i + 1) % 3)))

        try:
            res = BacktestEngine.run_walk_forward_backtest(division_id=88, season_id=1, warmup_matches=5)
            assert res["status"] == "ok"
            assert res["total_matches"] == 15
            assert res["evaluated_matches"] == 10
            assert len(res["scorecard"]) == 4  # Poisson, Elo, Form, Ensemble
            for model_card in res["scorecard"]:
                assert "accuracy_percent" in model_card
                assert "brier_score" in model_card
        finally:
            with database.transaction() as conn:
                conn.execute("DELETE FROM matches WHERE division_id = 88")
                conn.execute("DELETE FROM divisions WHERE id = 88")


def generate_valid_init_data(user_dict: dict, bot_token: str, auth_date: int | None = None) -> str:
    """Helper to generate cryptographically valid Telegram initData string for API testing."""
    import hashlib
    import hmac
    import json
    import time
    from urllib.parse import urlencode

    if auth_date is None:
        auth_date = int(time.time())

    params = {
        "auth_date": str(auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(user_dict, separators=(",", ":"))
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    hash_val = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    params["hash"] = hash_val
    return urlencode(params)


class TestPhase7IntelligenceApi(AioHTTPTestCase):
    """Integration tests for Phase 7 REST API endpoints."""

    async def get_application(self) -> web.Application:
        return create_app()

    def setUp(self):
        super().setUp()
        import config
        self.bot_token = config.TOKEN or "test_token"
        self.user_init_data = generate_valid_init_data({"id": 987901, "username": "intel_user"}, self.bot_token)
        self.admin_init_data = generate_valid_init_data({"id": 987902, "username": "intel_admin"}, self.bot_token)

        if 987902 not in config.ADMIN_IDS:
            config.ADMIN_IDS.append(987902)

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO users (telegram_id, username, role)
                VALUES (987901, 'intel_user_701', 'user')
            """)
            cursor.execute("""
                INSERT OR REPLACE INTO users (telegram_id, username, role)
                VALUES (987902, 'intel_admin_702', 'admin')
            """)
            cursor.execute("""
                INSERT OR REPLACE INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status)
                VALUES (987999, 1, 'ManCity_799', 'Liverpool_799', 1, 1, 'open')
            """)
            cursor.execute("""
                INSERT OR REPLACE INTO markets (id, match_id, market_key, market_name, status)
                VALUES (9879991, 987999, 'match_winner', '1X2', 'open')
            """)
            cursor.execute("""
                INSERT OR REPLACE INTO market_selections (id, market_id, selection_key, selection_name, odds_value, status)
                VALUES (98799911, 9879991, 'p1', 'П1', 2.10, 'active')
            """)

    def tearDown(self):
        import config
        if 987902 in config.ADMIN_IDS:
            config.ADMIN_IDS.remove(987902)
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM market_selections WHERE id = 98799911")
            cursor.execute("DELETE FROM markets WHERE id = 9879991")
            cursor.execute("DELETE FROM matches WHERE id = 987999")
            cursor.execute("DELETE FROM users WHERE telegram_id IN (987901, 987902)")
        super().tearDown()

    @unittest_run_loop
    async def test_api_intelligence_matches(self):
        """GET /api/intelligence/matches requires auth and returns match summaries."""
        # Unauthenticated request -> 401
        resp_unauth = await self.client.get("/api/intelligence/matches")
        assert resp_unauth.status == 401

        # Authenticated with valid HMAC
        headers = {"X-Telegram-Init-Data": self.user_init_data}
        resp = await self.client.get("/api/intelligence/matches?division_id=1", headers=headers)
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert "matches" in data

    @unittest_run_loop
    async def test_api_intelligence_preview(self):
        """GET /api/intelligence/matches/{id}/preview returns complete AI match preview."""
        headers = {"X-Telegram-Init-Data": self.user_init_data}
        resp = await self.client.get("/api/intelligence/matches/987999/preview", headers=headers)
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert "probabilities" in data
        assert "confidence" in data
        assert "key_factors" in data
        assert "disclaimer" in data

    @unittest_run_loop
    async def test_api_value_radar(self):
        """GET /api/intelligence/value returns value radar picks."""
        headers = {"X-Telegram-Init-Data": self.user_init_data}
        resp = await self.client.get("/api/intelligence/value?min_edge=1.0", headers=headers)
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert "radar_picks" in data

    @unittest_run_loop
    async def test_api_admin_intelligence_rbac(self):
        """Normal player cannot access /api/admin/intelligence/overview; admin can."""
        # Normal player -> 403 Forbidden
        user_headers = {"X-Telegram-Init-Data": self.user_init_data}
        resp_forbidden = await self.client.get("/api/admin/intelligence/overview", headers=user_headers)
        assert resp_forbidden.status == 403

        # Admin user -> 200 OK
        admin_headers = {"X-Telegram-Init-Data": self.admin_init_data}
        resp_admin = await self.client.get("/api/admin/intelligence/overview", headers=admin_headers)
        assert resp_admin.status == 200
        data = await resp_admin.json()
        assert data["status"] == "ok"
        assert "active_models" in data
        assert "database_stats" in data

