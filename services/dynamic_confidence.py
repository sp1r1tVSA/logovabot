"""
services/dynamic_confidence.py

Logovo.bet — Multi-Factor Dynamic Confidence Scoring Engine.
Strict Invariants:
1. Never equal to raw model probability (C != P).
2. Never fixed constant across varying conditions.
3. Strictly deterministic & reproducible (never random).
4. Multi-factor synthesis:
   - Sample size (N matches)
   - Inter-model consensus (ensemble variance)
   - Calibration quality (Brier score)
   - Live data & odds freshness
   - Feature completeness
   - Provider health
"""

import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def calculate_dynamic_confidence(
    sample_size: int,
    model_estimates: list[float],
    brier_score: Optional[float] = None,
    data_freshness_seconds: Optional[float] = None,
    odds_freshness_seconds: Optional[float] = None,
    feature_completeness: float = 1.0,
    provider_status: str = "HEALTHY"
) -> float:
    """
    Compute bounded, multi-factor confidence score in [0.10, 0.99].
    """
    # 1. Sample Size Component [0.35, 1.00]
    sample_factor = min(1.0, 0.35 + (max(0, sample_size) / 20.0) * 0.65)

    # 2. Inter-model Consensus Penalty [0.0, 0.35]
    valid_estimates = [e for e in model_estimates if isinstance(e, (int, float)) and math.isfinite(e)]
    consensus_penalty = 0.0
    if len(valid_estimates) >= 2:
        mean_p = sum(valid_estimates) / len(valid_estimates)
        variance = sum((x - mean_p) ** 2 for x in valid_estimates) / len(valid_estimates)
        std_dev = math.sqrt(variance)
        consensus_penalty = min(0.35, std_dev * 1.5)

    base_confidence = max(0.20, sample_factor - consensus_penalty)

    # 3. Calibration Adjustment
    cal_adjustment = 0.0
    if brier_score is not None and math.isfinite(brier_score):
        if brier_score < 0.18:
            cal_adjustment = +0.05
        elif brier_score > 0.30:
            cal_adjustment = -0.10

    # 4. Data Freshness Multiplier
    data_multiplier = 1.0
    if data_freshness_seconds is not None:
        if data_freshness_seconds <= 120.0:
            data_multiplier = 1.0
        elif data_freshness_seconds <= 300.0:
            data_multiplier = 0.80
        else:
            data_multiplier = 0.40

    # 5. Odds Freshness Multiplier
    odds_multiplier = 1.0
    if odds_freshness_seconds is not None:
        if odds_freshness_seconds <= 60.0:
            odds_multiplier = 1.0
        elif odds_freshness_seconds <= 300.0:
            odds_multiplier = 0.85
        else:
            odds_multiplier = 0.50

    # 6. Feature Completeness Multiplier [0.70, 1.00]
    feat_multiplier = max(0.70, min(1.0, feature_completeness))

    # 7. Provider Status Multiplier
    prov_multiplier = 1.0
    if provider_status.upper() in ("DEGRADED", "STALE"):
        prov_multiplier = 0.75
    elif provider_status.upper() in ("CIRCUIT_OPEN", "UNAVAILABLE", "ERROR"):
        prov_multiplier = 0.40

    # Composite calculation
    score = (base_confidence + cal_adjustment) * data_multiplier * odds_multiplier * feat_multiplier * prov_multiplier
    final_conf = round(max(0.10, min(0.99, score)), 2)

    return final_conf
