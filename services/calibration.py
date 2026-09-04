"""
services/calibration.py

Logovo.bet — Probability Calibration & Reliability Analysis Service.
Provides:
1. Sigmoidal Platt calibration layer to prevent model overconfidence.
2. Multiclass Brier score and Log Loss computation.
3. Reliability curve / calibration report across 10% confidence buckets.
"""

import math
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ProbabilityCalibrator:
    """Calibrates model outputs and measures probability reliability."""

    @staticmethod
    def calibrate_probability(raw_prob: float, a: float = -0.92, b: float = 0.0) -> float:
        """
        Apply Platt scaling transformation to compress overconfident extremes towards the mean.
        P_cal = 1 / (1 + exp(a * logit(P) + b))
        """
        if not isinstance(raw_prob, (int, float)) or not math.isfinite(raw_prob):
            return 0.3333
        p = max(0.001, min(0.999, float(raw_prob)))
        try:
            logit = math.log(p / (1.0 - p))
            calibrated = 1.0 / (1.0 + math.exp(a * logit + b))
            return round(max(0.01, min(0.99, calibrated)), 4)
        except (ValueError, OverflowError):
            return round(p, 4)

    @staticmethod
    def calibrate_1x2(p_home: float, p_draw: float, p_away: float) -> tuple[float, float, float]:
        """Calibrate a 1X2 distribution and re-normalize to 1.0."""
        c_home = ProbabilityCalibrator.calibrate_probability(p_home)
        c_draw = ProbabilityCalibrator.calibrate_probability(p_draw)
        c_away = ProbabilityCalibrator.calibrate_probability(p_away)

        total = c_home + c_draw + c_away
        norm_h = round(c_home / total, 4)
        norm_d = round(c_draw / total, 4)
        norm_a = round(1.0 - norm_h - norm_d, 4)
        return norm_h, norm_d, norm_a

    @staticmethod
    def calculate_brier_score(predictions: list[dict]) -> Optional[float]:
        """
        Compute multiclass Brier score over resolved predictions.
        Brier = (1/N) * sum_i sum_c (P_{i,c} - Y_{i,c})^2
        Lower is better (0.0 = perfect, ~0.66 = random 3-way guess).
        Returns None if sample is empty.
        """
        if not predictions:
            return None

        total_loss = 0.0
        counted = 0

        for p in predictions:
            actual = p.get("actual_result")
            if not actual or actual not in ("home", "draw", "away"):
                continue

            y_h = 1.0 if actual == "home" else 0.0
            y_d = 1.0 if actual == "draw" else 0.0
            y_a = 1.0 if actual == "away" else 0.0

            try:
                ph = float(p.get("home_probability", 0.33))
                pd = float(p.get("draw_probability", 0.33))
                pa = float(p.get("away_probability", 0.33))
            except (ValueError, TypeError):
                continue

            loss = ((ph - y_h) ** 2) + ((pd - y_d) ** 2) + ((pa - y_a) ** 2)
            total_loss += loss
            counted += 1

        if counted == 0:
            return None
        return round(total_loss / (counted * 2.0), 4)

    @staticmethod
    def calculate_log_loss(predictions: list[dict], eps: float = 1e-6) -> Optional[float]:
        """
        Compute multiclass cross-entropy loss (Log Loss) with numerical clipping.
        Clips probabilities to [eps, 1.0 - eps] to eliminate log(0) singularities.
        Returns None if sample is empty.
        """
        if not predictions:
            return None

        total_loss = 0.0
        counted = 0

        for p in predictions:
            actual = p.get("actual_result")
            if not actual or actual not in ("home", "draw", "away"):
                continue

            try:
                if actual == "home":
                    p_act = float(p.get("home_probability", 0.33))
                elif actual == "draw":
                    p_act = float(p.get("draw_probability", 0.33))
                else:
                    p_act = float(p.get("away_probability", 0.33))
            except (ValueError, TypeError):
                continue

            p_clipped = max(eps, min(1.0 - eps, p_act))
            total_loss += (-math.log(p_clipped))
            counted += 1

        if counted == 0:
            return None
        return round(total_loss / counted, 4)

    @staticmethod
    def generate_calibration_report(predictions: list[dict]) -> list[dict[str, Any]]:
        """
        Group predictions into 10% confidence buckets and compare predicted probability vs actual hit rate.
        If a bucket has 0 predictions, returns count=0 and actual_accuracy=None (NO FAKE 0.0s).
        """
        buckets = [
            {"bucket": "0-10%", "min": 0.0, "max": 0.10, "predictions": 0, "hits": 0},
            {"bucket": "10-20%", "min": 0.10, "max": 0.20, "predictions": 0, "hits": 0},
            {"bucket": "20-30%", "min": 0.20, "max": 0.30, "predictions": 0, "hits": 0},
            {"bucket": "30-40%", "min": 0.30, "max": 0.40, "predictions": 0, "hits": 0},
            {"bucket": "40-50%", "min": 0.40, "max": 0.50, "predictions": 0, "hits": 0},
            {"bucket": "50-60%", "min": 0.50, "max": 0.60, "predictions": 0, "hits": 0},
            {"bucket": "60-70%", "min": 0.60, "max": 0.70, "predictions": 0, "hits": 0},
            {"bucket": "70-80%", "min": 0.70, "max": 0.80, "predictions": 0, "hits": 0},
            {"bucket": "80-90%", "min": 0.80, "max": 0.90, "predictions": 0, "hits": 0},
            {"bucket": "90-100%", "min": 0.90, "max": 1.00, "predictions": 0, "hits": 0},
        ]

        for p in predictions:
            actual = p.get("actual_result")
            if not actual or actual not in ("home", "draw", "away"):
                continue

            # Evaluate the highest-probability pick
            ph = float(p.get("home_probability", 0.0))
            pd = float(p.get("draw_probability", 0.0))
            pa = float(p.get("away_probability", 0.0))

            max_p = max(ph, pd, pa)
            pick = "home" if max_p == ph else ("draw" if max_p == pd else "away")
            is_hit = (pick == actual)

            for b in buckets:
                if (b["min"] <= max_p < b["max"]) or (b["max"] == 1.00 and max_p == 1.00):
                    b["predictions"] += 1
                    if is_hit:
                        b["hits"] += 1
                    break

        report = []
        for b in buckets:
            n = b["predictions"]
            acc = round((b["hits"] / n) * 100.0, 1) if n > 0 else None
            report.append({
                "bucket": b["bucket"],
                "count": n,
                "hits": b["hits"],
                "actual_accuracy": acc
            })

        return report
