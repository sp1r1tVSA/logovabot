"""
services/ensemble_engine.py

Logovo.bet — Multi-Model Ensemble Prediction Engine.
Unifies:
1. Poisson 2.0 Bivariate Goal Expectancy (goals, totals, correct scores)
2. Elo Rating Engine (structural team strength & rating disparity)
3. Form Model (recent momentum, home/away split, clean sheets)
4. Calibration Layer (Platt scaling & reliability bounds)

Strict Invariants:
1. Re-normalizes weights dynamically if any sub-model has insufficient data.
2. Inter-model consensus drives the composite confidence score.
3. Completely deterministic & reproducible for identical inputs.
4. Generates transparent, verifiable "Key Factors" based on actual feature metrics.
"""

import math
import logging
from typing import Any, Optional

import database
from services.feature_engine import FeatureEngine
from services.elo_engine import EloEngine
from services.form_model import FormModel
from services.poisson_model import PoissonModel
from services.calibration import ProbabilityCalibrator

logger = logging.getLogger(__name__)

MODEL_VERSION = "ensemble_v1"
FEATURE_VERSION = "features_v1"

# Centralized ensemble weights
DEFAULT_WEIGHT_POISSON = 0.40
DEFAULT_WEIGHT_ELO = 0.35
DEFAULT_WEIGHT_FORM = 0.25


class EnsemblePredictionEngine:
    """Orchestrates feature extraction, sub-model execution, and ensemble probability blending."""

    @staticmethod
    def predict_match(
        match_id: int,
        save_to_db: bool = False,
        weight_poisson: float = DEFAULT_WEIGHT_POISSON,
        weight_elo: float = DEFAULT_WEIGHT_ELO,
        weight_form: float = DEFAULT_WEIGHT_FORM
    ) -> dict[str, Any]:
        """
        Generate full ensemble prediction for a fixture.
        Returns 1X2 probabilities, Over/Under, BTTS, Correct Scores, Confidence, and Key Factors.
        """
        # Validate weights
        for w_name, w_val in (("weight_poisson", weight_poisson), ("weight_elo", weight_elo), ("weight_form", weight_form)):
            if not isinstance(w_val, (int, float)) or not math.isfinite(w_val) or w_val < 0.0:
                raise ValueError(f"Ensemble weight '{w_name}' must be a finite, non-negative number.")
        if (weight_poisson + weight_elo + weight_form) <= 0.0:
            raise ValueError("Sum of ensemble weights must be strictly positive.")

        # 1. Extract leakage-free features
        features = FeatureEngine.extract_match_features(match_id)

        t1 = features["team1"]
        t2 = features["team2"]
        div_id = features["division_id"]
        season_id = features["season_id"]

        t1_feat = features["team1_features"]
        t2_feat = features["team2_features"]
        h2h_feat = features["h2h_features"]

        # 2. Sub-model 1: Poisson 2.0
        lh, la = PoissonModel.calculate_expected_goals(
            attack1=t1_feat["attack_strength"],
            defense1=t1_feat["defense_weakness"],
            attack2=t2_feat["attack_strength"],
            defense2=t2_feat["defense_weakness"],
            league_avg_home=features["league_averages"]["avg_home_goals"],
            league_avg_away=features["league_averages"]["avg_away_goals"]
        )
        poisson_res = PoissonModel.calculate_match_probabilities(lh, la)

        # 3. Sub-model 2: Elo Engine
        elo_res = EloEngine.calculate_match_probabilities(
            t1, t2, division_id=div_id, season_id=season_id
        )

        # 4. Sub-model 3: Form Model
        t1_form_score = FormModel.calculate_form_score(
            matches=[], team_name=t1
        ) if t1_feat["overall"]["matches_played"] == 0 else (
            # Approximate form score from calculated metrics
            max(0.05, min(0.95, (t1_feat["overall"]["win_rate"] * 0.6) + (t1_feat["overall"]["draw_rate"] * 0.2) + 0.2))
        )
        t2_form_score = FormModel.calculate_form_score(
            matches=[], team_name=t2
        ) if t2_feat["overall"]["matches_played"] == 0 else (
            max(0.05, min(0.95, (t2_feat["overall"]["win_rate"] * 0.6) + (t2_feat["overall"]["draw_rate"] * 0.2) + 0.2))
        )
        form_res = FormModel.calculate_match_probabilities(t1_form_score, t2_form_score)

        # 5. Dynamic Weight Normalization
        active_weights = {}
        sample_size = features["sample_size"]

        if sample_size >= 2:
            active_weights["poisson"] = weight_poisson
            active_weights["form"] = weight_form
        else:
            active_weights["poisson"] = 0.20
            active_weights["form"] = 0.10

        active_weights["elo"] = weight_elo

        total_w = sum(active_weights.values())
        w_p = active_weights["poisson"] / total_w
        w_e = active_weights["elo"] / total_w
        w_f = active_weights["form"] / total_w

        # 6. Blended 1X2 Probabilities
        raw_home = (poisson_res["home_probability"] * w_p) + (elo_res["home_probability"] * w_e) + (form_res["home_probability"] * w_f)
        raw_draw = (poisson_res["draw_probability"] * w_p) + (elo_res["draw_probability"] * w_e) + (form_res["draw_probability"] * w_f)
        raw_away = (poisson_res["away_probability"] * w_p) + (elo_res["away_probability"] * w_e) + (form_res["away_probability"] * w_f)

        # 7. Apply Probability Calibration Layer
        cal_home, cal_draw, cal_away = ProbabilityCalibrator.calibrate_1x2(raw_home, raw_draw, raw_away)

        # 8. Composite Confidence Calculation
        # Factor A: Sample size (0.3 to 1.0)
        sample_conf = min(1.0, 0.4 + (sample_size / 20.0) * 0.6)

        # Factor B: Model consensus (std dev of home probabilities)
        p_estimates = [poisson_res["home_probability"], elo_res["home_probability"], form_res["home_probability"]]
        mean_p = sum(p_estimates) / 3.0
        variance = sum((x - mean_p) ** 2 for x in p_estimates) / 3.0
        consensus_penalty = min(0.35, math.sqrt(variance) * 1.5)
        raw_conf = max(0.30, min(0.95, sample_conf - consensus_penalty))
        confidence = round(raw_conf, 2)

        # 9. Explainable Key Factors
        key_factors = EnsemblePredictionEngine._derive_key_factors(
            t1, t2, t1_feat, t2_feat, elo_res, h2h_feat, cal_home, cal_away
        )

        result = {
            "match_id": match_id,
            "division_id": div_id,
            "season_id": season_id,
            "model_version": MODEL_VERSION,
            "feature_version": FEATURE_VERSION,
            "team1": t1,
            "team2": t2,
            "home_probability": cal_home,
            "draw_probability": cal_draw,
            "away_probability": cal_away,
            "confidence": confidence,
            "expected_goals": {
                "team1": lh,
                "team2": la,
                "total": round(lh + la, 2)
            },
            "goals_markets": {
                "over_1_5": poisson_res["over_1_5_probability"],
                "under_1_5": poisson_res["under_1_5_probability"],
                "over_2_5": poisson_res["over_2_5_probability"],
                "under_2_5": poisson_res["under_2_5_probability"],
                "over_3_5": poisson_res["over_3_5_probability"],
                "under_3_5": poisson_res["under_3_5_probability"],
                "btts_yes": poisson_res["btts_yes_probability"],
                "btts_no": poisson_res["btts_no_probability"]
            },
            "correct_scores": poisson_res["correct_scores"],
            "sub_models": {
                "poisson": {
                    "home": poisson_res["home_probability"],
                    "draw": poisson_res["draw_probability"],
                    "away": poisson_res["away_probability"],
                    "weight": round(w_p, 2)
                },
                "elo": {
                    "home": elo_res["home_probability"],
                    "draw": elo_res["draw_probability"],
                    "away": elo_res["away_probability"],
                    "rating_t1": elo_res["rating_team1"],
                    "rating_t2": elo_res["rating_team2"],
                    "weight": round(w_e, 2)
                },
                "form": {
                    "home": form_res["home_probability"],
                    "draw": form_res["draw_probability"],
                    "away": form_res["away_probability"],
                    "form_t1": t1_form_score,
                    "form_t2": t2_form_score,
                    "weight": round(w_f, 2)
                }
            },
            "key_factors": key_factors,
            "xg_available": features["xg_available"],
            "sample_size": sample_size,
            "disclaimer": "Прогноз AI — аналитическая оценка, а не гарантия результата."
        }

        # 10. Persist to database if requested
        if save_to_db:
            try:
                database.save_ai_prediction(
                    match_id=match_id,
                    division_id=div_id,
                    season_id=season_id,
                    model_version=MODEL_VERSION,
                    feature_version=FEATURE_VERSION,
                    home_prob=cal_home,
                    draw_prob=cal_draw,
                    away_prob=cal_away,
                    confidence=confidence,
                    over_1_5=poisson_res["over_1_5_probability"],
                    over_2_5=poisson_res["over_2_5_probability"],
                    over_3_5=poisson_res["over_3_5_probability"],
                    btts_yes=poisson_res["btts_yes_probability"],
                    btts_no=poisson_res["btts_no_probability"],
                    key_factors=key_factors
                )
            except Exception as e:
                logger.warning(f"Could not persist prediction for Match #{match_id}: {e}")

        return result

    @staticmethod
    def _derive_key_factors(
        t1: str,
        t2: str,
        t1_feat: dict[str, Any],
        t2_feat: dict[str, Any],
        elo_res: dict[str, Any],
        h2h: dict[str, Any],
        p_home: float,
        p_away: float
    ) -> list[str]:
        """Derive 3-5 factual explanation bullet points strictly from computed values."""
        factors: list[str] = []

        # 1. Elo difference factor
        diff_elo = elo_res["rating_team1"] - elo_res["rating_team2"]
        if abs(diff_elo) >= 50:
            favored = t1 if diff_elo > 0 else t2
            factors.append(
                f"⚡ Рейтинг силы Elo: преимущество у {favored} ({round(abs(diff_elo))} очков разницы)."
            )

        # 2. Attack and Scoring form
        avg_t1 = t1_feat["overall"].get("avg_scored", 1.2)
        avg_t2 = t2_feat["overall"].get("avg_scored", 1.2)
        if avg_t1 >= 1.8:
            factors.append(f"⚽ {t1} в высокой результативности: в среднем {avg_t1} гола за последние игры.")
        elif avg_t2 >= 1.8:
            factors.append(f"⚽ {t2} демонстрирует активную атаку: в среднем {avg_t2} гола за игру.")

        # 3. Defense / Clean Sheets
        cs1 = t1_feat["overall"].get("clean_sheets", 0)
        cs2 = t2_feat["overall"].get("clean_sheets", 0)
        if cs1 >= 2:
            factors.append(f"🛡 {t1} надежен сзади: {cs1} сухих матча в недавней серии.")
        elif cs2 >= 2:
            factors.append(f"🛡 {t2} сохраняет надежность в обороне ({cs2} сухих матча).")

        # 4. H2H rivalry
        if h2h.get("total_meetings", 0) >= 3:
            w1 = h2h.get("team1_wins", 0)
            w2 = h2h.get("team2_wins", 0)
            if w1 > w2:
                factors.append(f"📊 Историческое преимущество: в личных встречах {t1} ведет со счетом {w1}:{w2}.")
            elif w2 > w1:
                factors.append(f"📊 Историческое преимущество: {t2} выиграл {w2} из {h2h['total_meetings']} очных дуэлей.")

        # 5. Over/Under pattern
        o25_1 = t1_feat["overall"].get("over_25_rate", 0.0)
        o25_2 = t2_feat["overall"].get("over_25_rate", 0.0)
        if o25_1 >= 0.6 and o25_2 >= 0.6:
            factors.append("🔥 Обе команды играют открыто: Тотал Больше 2.5 пробивался чаще чем в 60% игр.")

        if not factors:
            factors.append(f"⚖️ Сбалансированное противостояние: {t1} и {t2} подходят к матчу в сопоставимой форме.")

        return factors
