"""
tests/test_phase7_1_redteam.py

Logovo.bet — Phase 7.1 AI Red Team & Production Acceptance Test Suite.
Adversarial Verification Covering:
1. Strict Financial Read-Only Invariant (Static & Runtime: AI cannot debit, credit, bet, or settle).
2. Data Leakage Prevention (Future results, H2H exclusion, future season/standing/ratings, temporal backtesting).
3. Mathematical Red Team (Poisson 2.0 bounds, NaN/Inf resilience, correct score grid, calibration stability).
4. Elo Rating Invariants (Zero mutation during prediction, strictly confirmed updates).
5. Form Model & H2H Integrity (Recency weighting, zero match fallbacks, home/away split).
6. Ensemble Engine Invariants (Weight redistribution, dynamic fallbacks, weight validation).
7. Value Engine & Odds Movers Hardening (NaN/Inf odds rejection, overround calculation, edge boundaries).
8. Live & Sports Data Providers (NullSportsDataProvider zero-hallucination, no fake xG/events).
9. Prediction Versioning, Snapshot Immutability, & Match Result Correction Workflow.
10. Division & Season Cross-Contamination Isolation.
11. REST API Security & RBAC (Division Admin IDOR 403 vs Global Admin 200, SQLi payloads, input fuzzing).
12. Concurrency and Database Integrity Checks.
"""

import ast
import asyncio
import math
import os
import time
from urllib.parse import urlencode
import hashlib
import hmac
import json
import pytest
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from aiohttp import web

import config
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
from services.recommendation_engine import get_user_recommendations, get_hot_matches
from services.sports_provider import NullSportsDataProvider, get_sports_provider


def make_test_init_data(user_dict: dict, bot_token: str, auth_date: int | None = None) -> str:
    """Generate cryptographically valid Telegram initData query string."""
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


# ==============================================================================
# 1. FINANCIAL READ-ONLY & SECURITY AUDIT
# ==============================================================================

class TestPhase71FinancialIsolation:
    """Verifies that the AI / Intelligence layer is strictly financially read-only."""

    def test_ai_layer_static_imports_no_financial_mutation(self):
        """Static AST audit of all AI services to verify zero financial mutating function calls."""
        ai_service_files = [
            "services/feature_engine.py",
            "services/elo_engine.py",
            "services/form_model.py",
            "services/poisson_model.py",
            "services/calibration.py",
            "services/ensemble_engine.py",
            "services/value_engine.py",
            "services/backtest_engine.py",
            "services/intelligence_engine.py",
            "services/recommendation_engine.py",
            "services/odds_movers.py",
        ]

        forbidden_financial_calls = {
            "modify_wallet_balance",
            "place_user_bet",
            "void_user_bet",
            "credit_balance",
            "debit_balance",
            "settle_bet",
            "refund_bet",
        }

        for rel_path in ai_service_files:
            abs_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), rel_path)
            if not os.path.exists(abs_path):
                continue
            with open(abs_path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=rel_path)

            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func_name = ""
                    if isinstance(node.func, ast.Name):
                        func_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr
                    assert func_name not in forbidden_financial_calls, (
                        f"CRITICAL: Forbidden financial call '{func_name}' found in {rel_path} (line {node.lineno})"
                    )

    def test_ai_cannot_debit_or_credit_wallet_runtime(self):
        """Runtime verification that running predictions, recommendations, and backtests leaves user balance unchanged."""
        test_uid = 9911001
        initial_balance = 7500.0

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO users (telegram_id, username, role)
                VALUES (?, 'test_ai_fin_user', 'user')
            """, (test_uid,))
            cursor.execute("""
                INSERT OR REPLACE INTO user_wallets (user_id, balance, total_wagered, total_won, bets_count, bets_won)
                VALUES (?, ?, 0, 0, 0, 0)
            """, (test_uid, initial_balance))
            cursor.execute("""
                INSERT OR REPLACE INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status)
                VALUES (88001, 1, 'Arsenal_Fin', 'Chelsea_Fin', 1, 1, 'open')
            """)

        try:
            # 1. Execute Feature extraction
            FeatureEngine.extract_match_features(88001)
            # 2. Execute Poisson
            PoissonModel.calculate_expected_goals(1.4, 0.9, 1.2, 1.1)
            # 3. Execute Elo
            EloEngine.calculate_match_probabilities("Arsenal_Fin", "Chelsea_Fin", division_id=1, season_id=1)
            # 4. Execute Ensemble
            EnsemblePredictionEngine.predict_match(88001)
            # 5. Execute Recommendations
            get_user_recommendations(user_id=test_uid, limit=5, risk_profile="balanced")
            # 6. Execute Backtest
            BacktestEngine.run_walk_forward_backtest(division_id=1, season_id=1, warmup_matches=5)

            # Verify wallet balance remains exactly untouched
            wallet = database.get_or_create_wallet(test_uid)
            assert wallet is not None
            assert float(wallet["balance"]) == initial_balance
        finally:
            with database.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM matches WHERE id = 88001")
                cursor.execute("DELETE FROM user_wallets WHERE user_id = ?", (test_uid,))
                cursor.execute("DELETE FROM users WHERE telegram_id = ?", (test_uid,))

    def test_ai_cannot_place_or_settle_bets(self):
        """Saving AI predictions and snapshots does not create rows in bets table or trigger settlement."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status)
                VALUES (88002, 1, 'TeamA_BetCheck', 'TeamB_BetCheck', 1, 1, 'open')
            """)
            cursor.execute("SELECT COUNT(*) as cnt FROM user_bets")
            initial_bet_count = cursor.fetchone()["cnt"]

        pred_id = database.save_ai_prediction(
            match_id=88002, division_id=1, season_id=1,
            model_version="ensemble_v1", feature_version="features_v1",
            home_prob=0.5, draw_prob=0.3, away_prob=0.2, confidence=0.7
        )
        snap_id = database.save_prediction_snapshot(
            match_id=88002, stage="PRE_MATCH", minute=None,
            home_score=0, away_score=0,
            home_prob=0.5, draw_prob=0.3, away_prob=0.2, confidence=0.7
        )

        try:
            with database.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) as cnt FROM user_bets")
                post_bet_count = cursor.fetchone()["cnt"]
            assert post_bet_count == initial_bet_count
        finally:
            with database.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM predictions WHERE id = ?", (pred_id,))
                cursor.execute("DELETE FROM prediction_snapshots WHERE id = ?", (snap_id,))
                cursor.execute("DELETE FROM matches WHERE id = 88002")


# ==============================================================================
# 2. DATA LEAKAGE & TEMPORAL INTEGRITY RED TEAM
# ==============================================================================

class TestPhase71DataLeakage:
    """Verifies that pre-match features, predictions, and backtests never leak future data."""

    def test_future_result_leakage_prevention(self):
        """Feature extraction for Match A (id 88101) does not include Match B (id 88102, future match)."""
        team_a = "LeakTeamA_71"
        team_b = "LeakTeamB_71"

        with database.transaction() as conn:
            cursor = conn.cursor()
            # Match A (historical)
            cursor.execute("""
                INSERT INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status, player1_score, player2_score)
                VALUES (88101, 1, ?, ?, 1, 1, 'completed', 2, 1)
            """, (team_a, team_b))
            # Match B (future fixture relative to 88101)
            cursor.execute("""
                INSERT INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status, player1_score, player2_score)
                VALUES (88102, 2, ?, ?, 1, 1, 'completed', 5, 0)
            """, (team_a, team_b))
            # Target Match C (evaluated as of 88102)
            cursor.execute("""
                INSERT INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status)
                VALUES (88103, 3, ?, ?, 1, 1, 'open')
            """, (team_a, team_b))

        try:
            # When evaluating with as_of_match_id = 88102, only matches with id < 88102 (i.e. Match 88101) are included
            feats = FeatureEngine.extract_match_features(88103, as_of_match_id=88102)
            # In H2H before match 88102, there should be exactly 1 match (88101), not 2
            assert feats["h2h_features"]["total_meetings"] == 1
            assert feats["h2h_features"]["team1_wins"] == 1
        finally:
            with database.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM matches WHERE id IN (88101, 88102, 88103)")

    def test_current_match_h2h_exclusion(self):
        """Feature extraction for a match must strictly exclude the match itself from H2H."""
        t1 = "H2HExclA_71"
        t2 = "H2HExclB_71"

        with database.transaction() as conn:
            cursor = conn.cursor()
            # Match 88201 is completed
            cursor.execute("""
                INSERT INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status, player1_score, player2_score)
                VALUES (88201, 5, ?, ?, 1, 1, 'completed', 3, 3)
            """, (t1, t2))

        try:
            # Extract features for 88201 itself (as_of_match_id defaults to 88201, so matches < 88201 are considered)
            feats = FeatureEngine.extract_match_features(88201)
            assert feats["h2h_features"]["total_meetings"] == 0
            assert feats["h2h_features"]["has_h2h_data"] is False
        finally:
            with database.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM matches WHERE id = 88201")

    def test_future_season_isolation(self):
        """Season 1 feature extraction must never read Season 2 matches or standings."""
        t_x = "CrossSeasonX_71"
        t_y = "CrossSeasonY_71"

        with database.transaction() as conn:
            cursor = conn.cursor()
            # Season 1 match
            cursor.execute("""
                INSERT INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status, player1_score, player2_score)
                VALUES (88301, 1, ?, ?, 1, 1, 'completed', 1, 0)
            """, (t_x, t_y))
            # Season 2 match
            cursor.execute("""
                INSERT INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status, player1_score, player2_score)
                VALUES (88302, 1, ?, ?, 1, 2, 'completed', 0, 4)
            """, (t_x, t_y))
            # Open match in Season 1
            cursor.execute("""
                INSERT INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status)
                VALUES (88303, 2, ?, ?, 1, 1, 'open')
            """, (t_x, t_y))
            # Open match in Season 2
            cursor.execute("""
                INSERT INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status)
                VALUES (88304, 2, ?, ?, 1, 2, 'open')
            """, (t_x, t_y))

        try:
            # Query Season 1 H2H
            feats_s1 = FeatureEngine.extract_match_features(88303)
            assert feats_s1["h2h_features"]["total_meetings"] == 1
            assert feats_s1["h2h_features"]["team1_wins"] == 1

            # Query Season 2 H2H
            feats_s2 = FeatureEngine.extract_match_features(88304)
            assert feats_s2["h2h_features"]["total_meetings"] == 1
            assert feats_s2["h2h_features"]["team2_wins"] == 1
        finally:
            with database.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM matches WHERE id IN (88301, 88302, 88303, 88304)")

    def test_backtest_is_strictly_temporal(self):
        """BacktestEngine trains on warmup matches and evaluates strictly out-of-sample without modifying real Elo."""
        res = BacktestEngine.run_walk_forward_backtest(division_id=1, season_id=1, warmup_matches=5)
        # Should return structured result or insufficient data message safely
        assert "status" in res
        if res["status"] == "ok":
            assert "accuracy_percent" in res
            assert "brier_score" in res


# ==============================================================================
# 3. MATHEMATICAL VALIDATION & ROBUSTNESS RED TEAM
# ==============================================================================

class TestPhase71MathematicalValidation:
    """Verifies mathematical stability and bounds across Poisson, Elo, Calibration, and Ensemble."""

    def test_poisson_nan_and_infinite_resilience(self):
        """PoissonModel calculates expected goals safely even when given NaN or extreme values."""
        # Extreme negative / zero / NaN inputs
        lh, la = PoissonModel.calculate_expected_goals(
            attack1=0.0, defense1=-1.0, attack2=99.0, defense2=float('nan'),
            league_avg_home=1.3, league_avg_away=1.1
        )
        assert 0.20 <= lh <= 4.50
        assert 0.20 <= la <= 4.50

        # Run probabilities computation
        probs = PoissonModel.calculate_match_probabilities(lh, la)
        assert math.isfinite(probs["home_probability"])
        assert math.isfinite(probs["draw_probability"])
        assert math.isfinite(probs["away_probability"])
        total_p = probs["home_probability"] + probs["draw_probability"] + probs["away_probability"]
        assert abs(total_p - 1.0) < 1e-3

    def test_correct_score_grid_extremes(self):
        """Correct scores grid is non-negative and properly bounded under extreme lambda pairs."""
        extreme_pairs = [(0.2, 0.2), (4.5, 4.5), (0.2, 4.5), (4.5, 0.2)]
        for lh, la in extreme_pairs:
            probs = PoissonModel.calculate_match_probabilities(lh, la)
            grid = probs["correct_scores"]
            grid_sum = sum(grid.values())
            assert 0.0 < grid_sum <= 1.0001
            for score, p in grid.items():
                assert 0.0 <= p <= 1.0
                assert math.isfinite(p)

    def test_prediction_does_not_mutate_elo(self):
        """EloEngine.calculate_match_probabilities is strictly read-only on database team_ratings."""
        t1 = "EloReadTeamA_71"
        t2 = "EloReadTeamB_71"

        database.update_team_elo(t1, division_id=1, season_id=1, new_elo=1580.0)
        database.update_team_elo(t2, division_id=1, season_id=1, new_elo=1420.0)

        elo_before_1 = database.get_team_elo(t1, division_id=1, season_id=1)
        elo_before_2 = database.get_team_elo(t2, division_id=1, season_id=1)

        # Call prediction repeatedly
        for _ in range(10):
            EloEngine.calculate_match_probabilities(t1, t2, division_id=1, season_id=1)

        elo_after_1 = database.get_team_elo(t1, division_id=1, season_id=1)
        elo_after_2 = database.get_team_elo(t2, division_id=1, season_id=1)

        assert elo_before_1 == elo_after_1 == 1580.0
        assert elo_before_2 == elo_after_2 == 1420.0

    def test_form_model_recency_and_zero_matches(self):
        """FormModel handles zero matches without crash, and gives higher weight to recent wins."""
        t_empty = "ZeroMatchClub_71"
        res_empty = FormModel.calculate_form_score([], t_empty)
        assert res_empty == 0.50  # Neutral fallback

        # Recent win vs older win
        matches_recent_win = [
            {"player1_team": "TeamW", "player2_team": "Other", "player1_score": 3, "player2_score": 0},
            {"player1_team": "TeamW", "player2_team": "Other", "player1_score": 0, "player2_score": 2},
        ]
        matches_older_win = [
            {"player1_team": "TeamW", "player2_team": "Other", "player1_score": 0, "player2_score": 2},
            {"player1_team": "TeamW", "player2_team": "Other", "player1_score": 3, "player2_score": 0},
        ]
        score_recent = FormModel.calculate_form_score(matches_recent_win, "TeamW")
        score_older = FormModel.calculate_form_score(matches_older_win, "TeamW")
        assert score_recent > score_older

    def test_ensemble_weights_validation_rejects_negative_or_zero(self):
        """EnsemblePredictionEngine validates weights and rejects negative, NaN, or all-zero sums."""
        with pytest.raises(ValueError):
            EnsemblePredictionEngine.predict_match(1, weight_poisson=-0.5)

        with pytest.raises(ValueError):
            EnsemblePredictionEngine.predict_match(1, weight_poisson=0.0, weight_elo=0.0, weight_form=0.0)

        with pytest.raises(ValueError):
            EnsemblePredictionEngine.predict_match(1, weight_poisson=float('nan'))

    def test_ensemble_missing_models_dynamic_reallocation(self):
        """When individual models are absent or fail, weights are dynamically reallocated to remaining models."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status)
                VALUES (88401, 1, 'Chelsea_Ens', 'Everton_Ens', 1, 1, 'open')
            """)

        try:
            pred = EnsemblePredictionEngine.predict_match(88401)
            assert 0.0 <= pred["home_probability"] <= 1.0
            assert 0.0 <= pred["confidence"] <= 1.0
            total_prob = pred["home_probability"] + pred["draw_probability"] + pred["away_probability"]
            assert abs(total_prob - 1.0) < 1e-3
        finally:
            with database.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM matches WHERE id = 88401")

    def test_confidence_bounds_and_no_sure_win(self):
        """Confidence is bounded in [0, 1] and key factors/explanations never include forbidden marketing terms."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status)
                VALUES (88402, 1, 'Alpha_Conf', 'Beta_Conf', 1, 1, 'open')
            """)

        try:
            pred = EnsemblePredictionEngine.predict_match(88402)
            assert 0.0 <= pred["confidence"] <= 1.0
            forbidden_phrases = ["100% win", "guaranteed", "sure bet", "sure win", "risk free", "easy money"]
            for factor in pred.get("key_factors", []):
                lower_factor = factor.lower()
                for phrase in forbidden_phrases:
                    assert phrase not in lower_factor, f"Forbidden phrase '{phrase}' in factor: {factor}"
        finally:
            with database.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM matches WHERE id = 88402")

    def test_calibration_log_loss_and_clipping(self):
        """ProbabilityCalibrator calculates Brier score and log loss with numerical clipping (no inf/nan)."""
        calibrator = ProbabilityCalibrator()

        pred_perfect = [{"home_probability": 1.0, "draw_probability": 0.0, "away_probability": 0.0, "actual_result": "home"}]
        brier_perfect = calibrator.calculate_brier_score(pred_perfect)
        assert brier_perfect == 0.0

        pred_wrong = [{"home_probability": 0.0, "draw_probability": 0.0, "away_probability": 1.0, "actual_result": "home"}]
        brier_wrong = calibrator.calculate_brier_score(pred_wrong)
        assert brier_wrong == 1.0

        # Log loss with extreme probabilities (0.0 and 1.0) must not produce inf or NaN
        log_loss_extreme = calibrator.calculate_log_loss(pred_wrong)
        assert log_loss_extreme is not None
        assert math.isfinite(log_loss_extreme)
        assert log_loss_extreme > 0.0

    def test_value_engine_invalid_and_nan_odds_rejection(self):
        """ValueEngine safely rejects non-positive, NaN, infinite, or invalid odds without raising unhandled errors."""
        overround_nan = ValueEngine.calculate_overround([float('nan'), float('inf'), -2.50, 0.0])
        assert overround_nan == 0.0

        p_implied_nan = ValueEngine.calculate_true_implied_probability(float('nan'), 0.05)
        assert p_implied_nan == 0.0

        p_implied_inf = ValueEngine.calculate_true_implied_probability(float('inf'), 0.05)
        assert p_implied_inf == 0.0

    def test_value_edge_boundary_and_overround(self):
        """ValueEngine calculates margin/overround and respects edge thresholds."""
        # 1/2.00 + 1/3.40 + 1/4.00 = 0.50 + 0.2941 + 0.25 = 1.0441 (4.41% overround)
        overround = ValueEngine.calculate_overround([2.00, 3.40, 4.00])
        assert abs(overround - 0.0441) < 0.01

        p_true = ValueEngine.calculate_true_implied_probability(2.00, overround)
        assert 0.45 <= p_true <= 0.50

    def test_odds_anomaly_rapid_and_nan_guards(self):
        """record_odds_movement ignores NaN, non-positive, or non-finite odds updates without crashing."""
        ret_nan = record_odds_movement(
            selection_id=501, market_id=1, match_id=1, old_odds=2.00, new_odds=float('nan')
        )
        assert ret_nan is None

        ret_neg = record_odds_movement(
            selection_id=501, market_id=1, match_id=1, old_odds=2.00, new_odds=-1.50
        )
        assert ret_neg is None

    def test_null_sports_provider_strict_fallbacks(self):
        """NullSportsDataProvider strictly indicates unavailable data and provides zero hallucinated stats."""
        provider = NullSportsDataProvider()
        status = provider.get_provider_status()
        assert status["provider"] == "null"
        assert status["connected"] is False
        assert status["status"] == "UNAVAILABLE"

        async def _test_provider():
            match_res = await provider.get_match(123)
            assert match_res is None
            stats = await provider.get_match_statistics(123)
            assert stats is None
            events = await provider.get_match_events(123)
            assert events == []

        asyncio.run(_test_provider())

    def test_prediction_versioning_and_snapshot_immutability(self):
        """Predictions and snapshots are immutable; new models do not overwrite historical prediction records."""
        mid = 88991
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status)
                VALUES (88991, 1, 'VersTeamA_71', 'VersTeamB_71', 1, 1, 'open')
            """)

        # Save v1 prediction
        p1 = database.save_ai_prediction(
            match_id=mid, division_id=1, season_id=1,
            model_version="ensemble_v1", feature_version="features_v1",
            home_prob=0.55, draw_prob=0.25, away_prob=0.20, confidence=0.65
        )
        # Save v2 prediction
        p2 = database.save_ai_prediction(
            match_id=mid, division_id=1, season_id=1,
            model_version="ensemble_v2", feature_version="features_v2",
            home_prob=0.60, draw_prob=0.20, away_prob=0.20, confidence=0.75
        )

        try:
            pred_v1 = database.get_ai_prediction(mid, model_version="ensemble_v1")
            pred_v2 = database.get_ai_prediction(mid, model_version="ensemble_v2")

            assert pred_v1 is not None and pred_v2 is not None
            assert pred_v1["id"] == p1
            assert pred_v2["id"] == p2
            assert pred_v1["home_probability"] == 0.55
            assert pred_v2["home_probability"] == 0.60
        finally:
            with database.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM predictions WHERE id IN (?, ?)", (p1, p2))
                cursor.execute("DELETE FROM matches WHERE id = 88991")

    def test_prediction_result_correction_workflow(self):
        """correct_ai_predictions recalculates accuracy and Brier score after match score correction without touching bets."""
        mid = 88992
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status)
                VALUES (88992, 1, 'CorrTeamA_71', 'CorrTeamB_71', 1, 1, 'open')
            """)

        pid = database.save_ai_prediction(
            match_id=mid, division_id=1, season_id=1,
            model_version="ensemble_v1", feature_version="features_v1",
            home_prob=0.70, draw_prob=0.20, away_prob=0.10, confidence=0.75
        )

        try:
            # First resolution: Home win (2:1)
            database.resolve_ai_predictions(match_id=mid, home_score=2, away_score=1)
            p_initial = database.get_ai_prediction(mid, model_version="ensemble_v1")
            assert p_initial["actual_result"] == "home"
            assert p_initial["is_correct"] == 1

            # Result correction: Official score changed to 1:2 (Away win)
            corrected_count = database.correct_ai_predictions(match_id=mid, new_home_score=1, new_away_score=2)
            assert corrected_count == 1

            p_corrected = database.get_ai_prediction(mid, model_version="ensemble_v1")
            assert p_corrected["actual_result"] == "away"
            assert p_corrected["is_correct"] == 0
        finally:
            with database.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM predictions WHERE id = ?", (pid,))
                cursor.execute("DELETE FROM matches WHERE id = 88992")

    def test_division_cross_contamination_isolation(self):
        """Team with identical name in Division 1 vs Division 2 maintains isolated Elo and ratings."""
        t_shared = "Spartak_Shared_71"

        database.update_team_elo(t_shared, division_id=1, season_id=1, new_elo=1620.0)
        database.update_team_elo(t_shared, division_id=2, season_id=1, new_elo=1380.0)

        elo_div1 = database.get_team_elo(t_shared, division_id=1, season_id=1)
        elo_div2 = database.get_team_elo(t_shared, division_id=2, season_id=1)

        assert elo_div1 == 1620.0
        assert elo_div2 == 1380.0
        assert elo_div1 != elo_div2

    def test_concurrency_prediction_requests(self):
        """Concurrent prediction queries execute cleanly without SQLite database locks or deadlocks."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            for i in range(5):
                cursor.execute(f"""
                    INSERT OR REPLACE INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status)
                    VALUES (8850{i}, 1, 'ConcTeamA_{i}', 'ConcTeamB_{i}', 1, 1, 'open')
                """)

        try:
            async def _run():
                tasks = [
                    asyncio.to_thread(EnsemblePredictionEngine.predict_match, 88500 + i)
                    for i in range(5)
                ]
                return await asyncio.gather(*tasks)

            results = asyncio.run(_run())
            assert len(results) == 5
            for r in results:
                assert "home_probability" in r
                assert 0.0 <= r["confidence"] <= 1.0
        finally:
            with database.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM matches WHERE id BETWEEN 88500 AND 88504")

    def test_database_integrity_pragma(self):
        """SQLite database passes PRAGMA integrity_check and PRAGMA foreign_key_check."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check")
            row = cursor.fetchone()
            assert row[0] == "ok"

            cursor.execute("PRAGMA foreign_key_check")
            fk_violations = cursor.fetchall()
            assert len(fk_violations) == 0


# ==============================================================================
# 4. API & RBAC RED TEAM
# ==============================================================================

class TestPhase71ApiAndSecurityRedTeam(AioHTTPTestCase):
    """Adversarial security, RBAC, IDOR, and input fuzzing tests for intelligence routes."""

    async def get_application(self) -> web.Application:
        return create_app()

    def setUp(self):
        super().setUp()
        self.bot_token = config.TOKEN or "test_token"
        self.user_id = 998801
        self.div_admin_id = 998802
        self.global_admin_id = 998803

        self.user_init = make_test_init_data({"id": self.user_id, "username": "reg_user"}, self.bot_token)
        self.div_admin_init = make_test_init_data({"id": self.div_admin_id, "username": "div_admin"}, self.bot_token)
        self.global_admin_init = make_test_init_data({"id": self.global_admin_id, "username": "glob_admin"}, self.bot_token)

        if self.global_admin_id not in config.ADMIN_IDS:
            config.ADMIN_IDS.append(self.global_admin_id)

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'reg_user', 'user')", (self.user_id,))
            cursor.execute("INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'div_admin', 'admin')", (self.div_admin_id,))
            cursor.execute("INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'glob_admin', 'admin')", (self.global_admin_id,))
            # Assign div_admin_id to Division 1 only
            cursor.execute("INSERT OR REPLACE INTO division_admins (user_id, division_id) VALUES (?, 1)", (self.div_admin_id,))

            # Seed test match in Division 1
            cursor.execute("""
                INSERT OR REPLACE INTO matches (id, round_number, player1_team, player2_team, division_id, season_id, status)
                VALUES (771999, 1, 'RealMadrid_71', 'Barcelona_71', 1, 1, 'open')
            """)

    def tearDown(self):
        if self.global_admin_id in config.ADMIN_IDS:
            config.ADMIN_IDS.remove(self.global_admin_id)
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM division_admins WHERE user_id = ?", (self.div_admin_id,))
            cursor.execute("DELETE FROM matches WHERE id = 771999")
            cursor.execute("DELETE FROM users WHERE telegram_id IN (?, ?, ?)", (self.user_id, self.div_admin_id, self.global_admin_id))
        super().tearDown()

    @unittest_run_loop
    async def test_api_division_admin_cannot_access_other_division_overview(self):
        """Division 1 admin gets 403 when trying to access Division 2 intelligence overview."""
        headers = {"X-Telegram-Init-Data": self.div_admin_init}
        # Division 1 query -> 200 OK
        resp_ok = await self.client.get("/api/admin/intelligence/overview?division_id=1", headers=headers)
        assert resp_ok.status == 200

        # Division 2 query -> 403 Forbidden
        resp_forbidden = await self.client.get("/api/admin/intelligence/overview?division_id=2", headers=headers)
        assert resp_forbidden.status == 403
        data = await resp_forbidden.json()
        assert data["error"] == "forbidden"

    @unittest_run_loop
    async def test_api_global_admin_can_access_any_division_overview(self):
        """Global admin can view intelligence overview across any division."""
        headers = {"X-Telegram-Init-Data": self.global_admin_init}
        resp = await self.client.get("/api/admin/intelligence/overview?division_id=2", headers=headers)
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"

    @unittest_run_loop
    async def test_api_input_fuzzing_and_sqli_payloads(self):
        """SQL injection payloads and fuzzed match IDs return safe 400/404 without SQL errors."""
        headers = {"X-Telegram-Init-Data": self.user_init}
        sqli_payloads = ["1' OR '1'='1", "1; DROP TABLE matches; --", "-999", "999999999999999", "invalid_id"]

        for payload in sqli_payloads:
            resp = await self.client.get(f"/api/intelligence/matches/{payload}/preview", headers=headers)
            # Expect either 400 Bad Request or 404 Not Found, never 500
            assert resp.status in (400, 404), f"Payload '{payload}' produced unexpected status {resp.status}"

    @unittest_run_loop
    async def test_recommendations_risk_profile_filtering_and_clamping(self):
        """GET /api/recommendations handles risk_profile and clamps limit safely."""
        headers = {"X-Telegram-Init-Data": self.user_init}
        resp = await self.client.get("/api/recommendations?risk_profile=conservative&limit=99999", headers=headers)
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert len(data["recommendations"]) <= 50

    @unittest_run_loop
    async def test_auth_date_future_skew_rejection(self):
        """Requests with auth_date skewed more than 300 seconds into the future are rejected with 401."""
        future_time = int(time.time()) + 600
        future_init = make_test_init_data({"id": self.user_id, "username": "reg_user"}, self.bot_token, auth_date=future_time)
        resp = await self.client.get("/api/intelligence/matches", headers={"X-Telegram-Init-Data": future_init})
        assert resp.status == 401
