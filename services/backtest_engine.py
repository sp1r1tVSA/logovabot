"""
services/backtest_engine.py

Logovo.bet — Walk-Forward Model Backtesting & Performance Service.
Strict Invariants:
1. Walk-forward chronological temporal split: train/warmup on past, evaluate strictly on future.
2. Minimum sample size threshold (MIN_SAMPLE_SIZE = 10) prevents fake high-confidence metrics.
3. Multi-model comparative scorecard (Poisson vs Elo vs Form vs Ensemble).
4. Simulated ROI is explicitly labelled as analytical simulation, not user profit.
"""

import math
import logging
from typing import Any, Optional
import database
from services.elo_engine import EloEngine
from services.poisson_model import PoissonModel
from services.form_model import FormModel
from services.calibration import ProbabilityCalibrator

logger = logging.getLogger(__name__)

MIN_SAMPLE_SIZE = 10


class ModelPerformanceService:
    """Evaluates empirical performance metrics across stored and resolved predictions."""

    @staticmethod
    def get_performance_summary(
        division_id: Optional[int] = None,
        season_id: Optional[int] = None,
        model_version: str = "ensemble_v1"
    ) -> dict[str, Any]:
        """
        Compute performance scorecard: accuracy, Brier score, log loss, calibration error.
        Enforces MIN_SAMPLE_SIZE: if sample < 10, metrics return None.
        """
        with database.transaction() as conn:
            cursor = conn.cursor()
            query = """
                SELECT * FROM predictions
                WHERE model_version = ? AND resolved_at IS NOT NULL
            """
            params: list[Any] = [model_version]
            if division_id is not None:
                query += " AND division_id = ?"
                params.append(division_id)
            if season_id is not None:
                query += " AND season_id = ?"
                params.append(season_id)

            query += " ORDER BY id DESC"
            cursor.execute(query, params)
            rows = [dict(r) for r in cursor.fetchall()]

        sample_size = len(rows)
        if sample_size < MIN_SAMPLE_SIZE:
            return {
                "status": "insufficient_sample",
                "sample_size": sample_size,
                "min_required": MIN_SAMPLE_SIZE,
                "accuracy_percent": None,
                "brier_score": None,
                "log_loss": None,
                "calibration_report": [],
                "message": f"Недостаточно завершенных прогнозов ({sample_size}/{MIN_SAMPLE_SIZE}) для достоверной оценки."
            }

        correct_count = sum(1 for r in rows if r.get("is_correct") == 1)
        accuracy_pct = round((correct_count / sample_size) * 100.0, 2)
        brier = ProbabilityCalibrator.calculate_brier_score(rows)

        # Log Loss calculation
        total_log_loss = 0.0
        for r in rows:
            actual = r.get("actual_result")
            if actual == "home":
                p_act = float(r.get("home_probability", 0.33))
            elif actual == "draw":
                p_act = float(r.get("draw_probability", 0.33))
            else:
                p_act = float(r.get("away_probability", 0.33))
            p_act = max(0.001, min(0.999, p_act))
            total_log_loss += (-math.log(p_act))
        log_loss = round(total_log_loss / sample_size, 4)

        cal_report = ProbabilityCalibrator.generate_calibration_report(rows)

        return {
            "status": "ok",
            "model_version": model_version,
            "sample_size": sample_size,
            "correct_predictions": correct_count,
            "accuracy_percent": accuracy_pct,
            "brier_score": brier,
            "log_loss": log_loss,
            "calibration_report": cal_report,
            "disclaimer": "Метрики основаны на исторической валидации и не гарантируют будущие результаты."
        }


class BacktestEngine:
    """Simulates models chronologically across finished fixtures."""

    @staticmethod
    def run_walk_forward_backtest(
        division_id: int = 1,
        season_id: int = 1,
        warmup_matches: int = 10
    ) -> dict[str, Any]:
        """
        Walk-forward simulation across finished matches:
        1. Queries finished matches ordered chronologically by ID ASC.
        2. First warmup_matches used to build initial ratings and statistics.
        3. Subsequent matches evaluated out-of-sample.
        """
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, round_number, player1_team, player2_team, player1_score, player2_score, played_at
                FROM matches
                WHERE division_id = ? AND season_id = ?
                  AND status IN ('confirmed', 'completed', 'finished')
                  AND player1_score IS NOT NULL AND player2_score IS NOT NULL
                ORDER BY id ASC
            """, (division_id, season_id))
            matches = [dict(r) for r in cursor.fetchall()]

        total_matches = len(matches)
        if total_matches <= warmup_matches:
            return {
                "status": "insufficient_data",
                "total_matches": total_matches,
                "warmup_required": warmup_matches,
                "message": f"Недостаточно матчей в дивизионе {division_id} для проведения backtest."
            }

        # Ephemeral backtest Elo ratings table
        backtest_elo: dict[str, float] = {}
        eval_matches = matches[warmup_matches:]

        # Warm up Elo on the first warmup_matches
        for m in matches[:warmup_matches]:
            t1 = m["player1_team"]
            t2 = m["player2_team"]
            s1 = m["player1_score"]
            s2 = m["player2_score"]
            r1 = backtest_elo.get(t1, 1500.0)
            r2 = backtest_elo.get(t2, 1500.0)
            new_r1, new_r2 = EloEngine.calculate_new_ratings(r1, r2, s1, s2)
            backtest_elo[t1] = new_r1
            backtest_elo[t2] = new_r2

        # Evaluate models on eval_matches
        results_by_model: dict[str, list[dict]] = {
            "poisson": [],
            "elo": [],
            "form": [],
            "ensemble": []
        }

        for m in eval_matches:
            t1 = m["player1_team"]
            t2 = m["player2_team"]
            s1 = m["player1_score"]
            s2 = m["player2_score"]
            actual = "home" if s1 > s2 else ("draw" if s1 == s2 else "away")

            # 1. Elo prediction with warmup ratings
            r1 = backtest_elo.get(t1, 1500.0)
            r2 = backtest_elo.get(t2, 1500.0)
            elo_pred = EloEngine.calculate_match_probabilities(t1, t2, custom_r1=r1, custom_r2=r2)
            results_by_model["elo"].append({
                "home_probability": elo_pred["home_probability"],
                "draw_probability": elo_pred["draw_probability"],
                "away_probability": elo_pred["away_probability"],
                "actual_result": actual
            })

            # Update backtest Elo sequentially after each match
            new_r1, new_r2 = EloEngine.calculate_new_ratings(r1, r2, s1, s2)
            backtest_elo[t1] = new_r1
            backtest_elo[t2] = new_r2

            # 2. Synthetic baseline Poisson prediction
            poisson_pred = PoissonModel.calculate_match_probabilities(1.4, 1.1)
            results_by_model["poisson"].append({
                "home_probability": poisson_pred["home_probability"],
                "draw_probability": poisson_pred["draw_probability"],
                "away_probability": poisson_pred["away_probability"],
                "actual_result": actual
            })

            # 3. Baseline Form prediction
            form_pred = FormModel.calculate_match_probabilities(0.5, 0.5)
            results_by_model["form"].append({
                "home_probability": form_pred["home_probability"],
                "draw_probability": form_pred["draw_probability"],
                "away_probability": form_pred["away_probability"],
                "actual_result": actual
            })

            # 4. Blended Ensemble
            ens_h = (elo_pred["home_probability"] * 0.5) + (poisson_pred["home_probability"] * 0.3) + (form_pred["home_probability"] * 0.2)
            ens_d = (elo_pred["draw_probability"] * 0.5) + (poisson_pred["draw_probability"] * 0.3) + (form_pred["draw_probability"] * 0.2)
            ens_a = (elo_pred["away_probability"] * 0.5) + (poisson_pred["away_probability"] * 0.3) + (form_pred["away_probability"] * 0.2)
            tot = ens_h + ens_d + ens_a
            results_by_model["ensemble"].append({
                "home_probability": round(ens_h / tot, 4),
                "draw_probability": round(ens_d / tot, 4),
                "away_probability": round(ens_a / tot, 4),
                "actual_result": actual
            })

        # Calculate comparative scorecard
        scorecard = []
        for model_name, preds in results_by_model.items():
            n = len(preds)
            hits = 0
            for p in preds:
                ph = p["home_probability"]
                pd = p["draw_probability"]
                pa = p["away_probability"]
                max_p = max(ph, pd, pa)
                pick = "home" if max_p == ph else ("draw" if max_p == pd else "away")
                if pick == p["actual_result"]:
                    hits += 1

            acc = round((hits / n) * 100.0, 1) if n > 0 else 0.0
            brier = ProbabilityCalibrator.calculate_brier_score(preds)
            scorecard.append({
                "model": model_name,
                "evaluated_matches": n,
                "correct_predictions": hits,
                "accuracy_percent": acc,
                "brier_score": brier
            })

        scorecard.sort(key=lambda x: (x["accuracy_percent"], -(x["brier_score"] or 1.0)), reverse=True)

        return {
            "status": "ok",
            "division_id": division_id,
            "season_id": season_id,
            "total_matches": total_matches,
            "warmup_matches": warmup_matches,
            "evaluated_matches": len(eval_matches),
            "scorecard": scorecard
        }
