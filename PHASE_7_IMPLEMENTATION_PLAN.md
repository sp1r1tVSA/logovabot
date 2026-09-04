# Phase 7: AI & Advanced Sports Intelligence Implementation Plan

## Executive Summary

Phase 7 elevates **Logovo.bet** from basic statistical indicators into a comprehensive, mathematically grounded, modular, and explainable **Sports Intelligence & Predictive Analytics Platform**.

In strict adherence to the project invariants and Phase 7 directives:
- **NO NEW BETTING/WALLET ENGINES**: We build on top of the existing betting core, settlement engine, and wallet ledger.
- **ZERO FINANCIAL CONTROL**: The AI/Intelligence layer is strictly read-only and analytical. It can NEVER debit balances, place bets, or trigger settlements.
- **DATA LEAKAGE PROTECTION**: Features and predictions are strictly conditioned on temporal information available at or before the match event. H2H calculations exclude the subject match.
- **HONESTY & NO HALLUCINATIONS**: Zero fake statistics or fabricated xG. If external or historical data is missing, the system explicitly returns `"data_unavailable"` or `xg_available: false`.
- **NO GUARANTEED PROFIT**: Language is strictly analytical ("Model signal", "Potential value", "High confidence" — never "Guaranteed", "Sure win", or "100%").

---

## Baseline Verification
- **Current Pytest Baseline:** **282 passed, 20 warnings in 66s**
- **Failures / Errors:** 0
- **All Phase 1–6.1 safeguards verified active.**

---

## 1. Current Architecture Audit

### 1.1 Existing Intelligence & Analytics Modules
1. **`services/intelligence_engine.py`**:
   - `IntelligenceEngine.get_match_intelligence(match_id)`: Fetches recent matches (last 5) and H2H (last 10) within same season and division.
   - `_poisson_probability(k, lamb)`: Basic univariate Poisson PMF.
   - `_calculate_probabilities_and_edge`: Bivariate Poisson 7x7 grid up to 6 goals. Calculates $P(\text{Home Win}), P(\text{Draw}), P(\text{Away Win}), P(\text{Over 2.5}), P(\text{BTTS})$. Computes naive edge $(\text{model\_prob} - \text{implied\_prob})$.
   - `_analyze_team_form`: Calculates wins, draws, losses, avg scored, avg conceded, clean sheets, over 2.5 rate, btts rate, momentum score.
   - `_generate_insights`: Generates 3–5 Russian text bullet points based on thresholds.

2. **`services/recommendation_engine.py`**:
   - `calculate_match_hot_score()`: Composite 0–100 score based on `is_live`, `odds_movement_count`, `betting_volume`, `h2h_count`, and `is_open`.
   - `get_hot_matches()`: Queries open/scheduled matches, joins `live_match_states` and `odds_movement`, and ranks them.
   - `get_user_recommendations()`: Filters matches by user's division, checks favorite clubs and market preferences, and outputs prioritized matches with suggested odds.

3. **`services/analytics_service.py`**:
   - `get_user_betting_analytics()`: ROI (None if staked == 0), Win Rate (None if settled == 0), status breakdowns.
   - `get_division_capper_leaderboard()`: Division and season-scoped leaderboard enforcing `MIN_LEADERBOARD_BETS = 5`.

4. **`services/odds_movers.py`**:
   - `record_odds_movement()`: Writes to `odds_movement` table with percentage change, direction, and velocity.
   - `get_odds_movers()`: Categorizes biggest drops, biggest rises, fastest velocity, and suspended markets.

5. **`api/routes_matches.py` & `api/routes_live.py`**:
   - `/api/matches/{id}/stats`, `/api/matches/{id}/h2h`, `/api/matches/{id}/insights`, `/api/matches/{id}/live`.
   - `/api/live/{id}/intelligence`, `/api/matches/hot`, `/api/recommendations`, `/api/odds/movers`.

6. **`api/routes_predictions.py`**:
   - Manages user betting slips and coupon placement (`user_bets`). Does not currently store AI predictions or model version snapshots.

---

## 2. Reusability, Expansion, and Deduplication Map

| Existing Component | Action | Details |
|:---|:---:|:---|
| `_poisson_probability` in `intelligence_engine.py` | **Reuse & Expand** | Move to Poisson 2.0 with dynamic $\lambda_{home}, \lambda_{away}$ incorporating attack/defense ratings, home advantage, and Over/Under (1.5, 2.5, 3.5), BTTS, and Correct Score grid. |
| `_analyze_team_form` in `intelligence_engine.py` | **Extract & Modularize** | Integrate into centralized `FeatureEngine` (`services/feature_engine.py`) with recency decay weights (e.g. 50%, 30%, 20% or linear weighting over last 5/10 games) and home/away split. |
| `_get_h2h_matches` | **Harden** | Ensure SQL query strictly excludes the current match ID (`AND id != ?`) and respects division/season boundaries. |
| `EloEngine` | **NEW Component** | Dedicated `services/elo_engine.py`. Implements Elo rating tracking, expected scores, rating updates post-match, and draw probability calculation. Zero mutation during prediction. |
| `EnsemblePredictionEngine` | **NEW Component** | Dedicated `services/ensemble_engine.py`. Combines Poisson 2.0 + Elo + Form + xG (if present) with normalized, configurable weights and confidence scoring. |
| `ProbabilityCalibrator` | **NEW Component** | Dedicated `services/calibration.py`. Maps raw probabilities to calibrated probabilities using parametric Platt scaling / empirical reliability bins. |
| `ValueRadar` | **NEW Component** | Dedicated `services/value_engine.py`. Implements overround normalization, true implied probability, and edge scanning with confidence filters and analytical wording. |
| `OddsAnomalyDetector` | **NEW Component** | Integrated in `services/odds_movers.py` or `services/odds_anomaly.py`. Detects abnormal velocity, sharp drift, and suspended market states. |
| `ModelPerformanceService` & `BacktestEngine` | **NEW Component** | `services/model_performance.py` & `services/backtest_engine.py`. Implements Brier score, accuracy, log loss, calibration curves, and temporal walk-forward backtesting. |
| DB Tables (`predictions`, `prediction_snapshots`, `team_ratings`) | **NEW Tables** | Add to `database.init_db()` with foreign keys, indexes, model versioning, and feature versioning. |
| `/api/intelligence/*` routes | **NEW / Extended Routes** | Add dedicated blueprint routes in `api/routes_intelligence.py` and mount in `api/server.py`. |
| Mini App UI (`web/`) | **Enhance** | Integrate AI Match Preview, Value Radar, Form charts, and "Why?" explainability drawer into Match Center. |

---

## 3. Database Migrations (`database.py`)

We will add three new tables to `database.init_db()`:

```sql
-- 1. Persistent Team Elo Ratings
CREATE TABLE IF NOT EXISTS team_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_name TEXT NOT NULL,
    division_id INTEGER NOT NULL DEFAULT 1,
    season_id INTEGER NOT NULL DEFAULT 1,
    elo_rating REAL NOT NULL DEFAULT 1500.0,
    matches_counted INTEGER NOT NULL DEFAULT 0,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(team_name, division_id, season_id),
    FOREIGN KEY(division_id) REFERENCES divisions(id) ON DELETE CASCADE,
    FOREIGN KEY(season_id) REFERENCES seasons(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_team_ratings_div ON team_ratings(division_id, season_id);

-- 2. Stored AI Predictions with Versioning & Resolution Tracking
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL,
    division_id INTEGER NOT NULL DEFAULT 1,
    season_id INTEGER NOT NULL DEFAULT 1,
    model_version TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    home_probability REAL NOT NULL,
    draw_probability REAL NOT NULL,
    away_probability REAL NOT NULL,
    over_1_5_probability REAL,
    over_2_5_probability REAL,
    over_3_5_probability REAL,
    btts_yes_probability REAL,
    btts_no_probability REAL,
    confidence REAL NOT NULL,
    key_factors TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    actual_result TEXT,
    is_correct BOOLEAN,
    brier_score REAL,
    FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE,
    FOREIGN KEY(division_id) REFERENCES divisions(id) ON DELETE CASCADE,
    FOREIGN KEY(season_id) REFERENCES seasons(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_predictions_match ON predictions(match_id, model_version);
CREATE INDEX IF NOT EXISTS idx_predictions_div_season ON predictions(division_id, season_id, created_at DESC);

-- 3. Live & Pre-match Prediction Snapshots (Temporal audit trail)
CREATE TABLE IF NOT EXISTS prediction_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('PRE_MATCH', 'LIVE', 'FINAL')),
    minute INTEGER,
    home_score INTEGER NOT NULL DEFAULT 0,
    away_score INTEGER NOT NULL DEFAULT 0,
    home_prob REAL NOT NULL,
    draw_prob REAL NOT NULL,
    away_prob REAL NOT NULL,
    confidence REAL NOT NULL,
    snapshot_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_pred_snapshots_match ON prediction_snapshots(match_id, snapshot_at DESC);
```

---

## 4. Architectural Component Specifications

### 4.1 Feature Engine (`services/feature_engine.py`)
- Calculates rolling statistics with recency decay:
  $w_i = \text{decay}^{(i-1)}$ for match index $i$ in chronological order (most recent has highest weight).
- Extracts:
  - Overall form (last 5 and last 10).
  - Home-specific form for Team 1, Away-specific form for Team 2.
  - Attack strength ($\alpha = \frac{\text{goals scored by team}}{\text{league avg}}$).
  - Defense weakness ($\beta = \frac{\text{goals conceded by team}}{\text{league avg}}$).
  - Clean sheet rate, BTTS rate, Over 2.5 rate.
  - Head-to-Head record between both teams (strictly excluding current match).
  - Actual xG & xGA if available from `live_statistics` table; otherwise explicit `xg_available = false`.
- **Temporal Enforcement**:
  Every query accepts `as_of_time` or `before_match_id` to strictly prevent looking ahead at future matches or post-match results.

### 4.2 Poisson 2.0
- Base goal expectations:
  $\lambda_{home} = \alpha_{home} \times \beta_{away} \times \gamma_{home\_adv} \times \mu_{league}$
  $\lambda_{away} = \alpha_{away} \times \beta_{home} \times \mu_{league}$
  Clamped between $0.15$ and $4.5$ to prevent degenerate extremes.
- Computes $P(g_1 = i, g_2 = j) = \text{Poisson}(i; \lambda_{home}) \times \text{Poisson}(j; \lambda_{away})$ for $i, j \in [0, 6]$.
- Evaluates:
  - $P(\text{Home}) = \sum_{i > j} P(i, j)$
  - $P(\text{Draw}) = \sum_{i = j} P(i, j)$
  - $P(\text{Away}) = \sum_{i < j} P(i, j)$
  - Over/Under 1.5, 2.5, 3.5
  - Both Teams To Score (BTTS Yes/No)
  - Correct score probabilities.

### 4.3 Elo Engine (`services/elo_engine.py`)
- Default initial rating: $1500.0$.
- Home advantage: $+65.0$ rating points.
- Win expectancy: $E_A = \frac{1}{1 + 10^{(R_B - (R_A + H)) / 400}}$.
- Draw probability estimated via rating parity: $P_{draw} = \text{draw\_base} \times e^{-\frac{(R_A - R_B)^2}{2 \sigma^2}}$.
- Outcome probabilities:
  $P(\text{Home}) = E_A - 0.5 \times P_{draw}$, $P(\text{Away}) = 1 - P(\text{Home}) - P_{draw}$.
- **Zero Mutation Invariant**: Prediction queries never update Elo. Updates only occur via `record_match_result(match_id)` after confirmed settlement.

### 4.4 Form Model (`services/form_model.py`)
- Form score normalized to $[0.0, 1.0]$.
- $0.0 = \text{very poor}$, $0.5 = \text{average}$, $1.0 = \text{flawless form}$.
- Weighted by match recency and opponent strength (opponent Elo at time of match).

### 4.5 Ensemble Engine (`services/ensemble_engine.py`)
- Weights:
  - Poisson: $0.40$
  - Elo: $0.35$
  - Form: $0.25$
  - xG (when available): incorporates into attack/defense strength.
- Dynamic weight re-normalization: If a component has insufficient data, its weight is re-allocated proportionally across available models.
- Generates composite confidence score $[0.0, 1.0]$ based on sample size, model agreement (low variance between models = higher confidence), and data completeness.

### 4.6 Calibration Layer (`services/calibration.py`)
- Standardizes raw probabilities against empirical calibration curves.
- Brier Score tracking: $\text{Brier} = \frac{1}{N} \sum_{k=1}^N (P_k - Y_k)^2$.
- Calibration reporting in 10% buckets ($50-60\%, 60-70\%, \dots$).

### 4.7 Value Radar (`services/value_engine.py`)
- Overround adjustment:
  $\text{Margin} = \sum \frac{1}{\text{odd}_i} - 1.0$.
  True implied probability: $P_{implied, i} = \frac{1 / \text{odd}_i}{1.0 + \text{Margin}}$.
- Value Edge: $\text{Edge} = (P_{model, i} - P_{implied, i}) \times 100\%$.
- Selections with $\text{Edge} \ge +3.0\text{pp}$ and $\text{Confidence} \ge 0.50$ surfaced to Value Radar.

### 4.8 Explainable AI Engine
- Decomposes why a probability was assigned into human-readable, factual bullets:
  - Form advantage: "Команда 1 набрала 13 очков из 15 последних возможных (+12% к исходу)".
  - Elo superiority: "Преимущество по рейтингу силы Elo: 1640 против 1490".
  - Home dominance: "Дома забивает в среднем 2.1 гола за матч".
  - Negative factors: "В личных встречах за 2 года лишь 1 победа в 5 матчах".
- Zero hallucination: statements are programmatically bound to computed metric deltas.

### 4.9 Model Performance & Backtesting (`services/backtest_engine.py`)
- Walk-forward temporal evaluation:
  Splits chronological fixtures: matches $1 \dots K$ for training Elo/strengths, matches $K+1 \dots N$ for out-of-sample evaluation.
- Compares Poisson vs Elo vs Form vs Ensemble on:
  - Accuracy %
  - Mean Brier Score
  - Log Loss
  - Simulated flat-stake ROI %

### 4.10 Financial Read-Only Safety Layer
- Confirmed invariant: Neither `IntelligenceEngine`, `EnsemblePredictionEngine`, nor any API in `routes_intelligence` imports or calls wallet debit, credit, or `place_user_bet`.
- Enforced by automated test `test_intelligence_layer_is_financially_read_only`.

---

## 5. API Endpoints (`api/routes_intelligence.py`)

| Method | Path | Description | Access |
|:---|:---|:---|:---|
| `GET` | `/api/intelligence/matches` | List upcoming matches with ensemble prediction summaries | Authenticated |
| `GET` | `/api/intelligence/matches/{id}` | Full intelligence package (Ensemble, Poisson, Elo, Form) | Authenticated |
| `GET` | `/api/intelligence/matches/{id}/preview` | Match Preview card with Form, Elo, Probabilities, "Why?" | Authenticated |
| `GET` | `/api/intelligence/matches/{id}/prediction` | Raw ensemble probabilities & confidence | Authenticated |
| `GET` | `/api/intelligence/matches/{id}/insights` | Factual bullet points derived from features | Authenticated |
| `GET` | `/api/intelligence/value` | Value Radar (selections with positive statistical edge) | Authenticated |
| `GET` | `/api/intelligence/hot` | Hot Matches 2.0 (composite scoring) | Authenticated |
| `GET` | `/api/intelligence/movers` | Odds Anomaly & Sharp Movement detection | Authenticated |
| `GET` | `/api/intelligence/history` | User's viewed or resolved prediction history | Authenticated |
| `GET` | `/api/intelligence/performance` | Model accuracy, Brier score, and calibration metrics | Authenticated |
| `GET` | `/api/admin/intelligence/overview` | Admin monitoring: models status, data health, drift | Admin Only |

---

## 6. Mini App Integration (`web/`)

1. **Match Center 3.0 Prediction Card**:
   - Probability Bar: Home (Blue) | Draw (Gray) | Away (Red).
   - Confidence Indicator: High / Medium / Low badge.
   - Value Radar Tag: Highlights value picks with $+X.X\%$ edge.
2. **AI Match Preview Drawer ("Why?")**:
   - Displays Elo ratings, 5-game form gauge, and factual breakdown bullets.
3. **Value Radar Screen**:
   - Lists best analytical edges with implied vs model probability comparison.
4. **Transparent Disclaimer**:
   - Embedded on all prediction components: *"Прогноз AI — аналитическая оценка, а не гарантия результата."*

---

## 7. Testing Plan (`tests/test_phase7_intelligence.py`)

A comprehensive suite covering:
1. **Model Math & Invariants**:
   - `test_poisson_2_bounds_and_probabilities_sum_to_one`
   - `test_elo_engine_calculation_and_no_mutation_during_prediction`
   - `test_form_model_normalization_and_recency_weighting`
   - `test_ensemble_engine_weights_and_reallocation`
   - `test_probability_calibrator_bounds`
2. **Data Leakage & Temporal Safety**:
   - `test_prediction_does_not_use_future_results`
   - `test_prediction_does_not_use_future_odds`
   - `test_prediction_temporal_split`
   - `test_h2h_excludes_current_match`
3. **Value & Odds**:
   - `test_implied_probability_overround_normalization`
   - `test_value_edge_calculation`
   - `test_odds_anomaly_detection`
4. **Data Integrity & Fallbacks**:
   - `test_missing_xg_explicit_flag`
   - `test_insufficient_data_safe_fallback`
   - `test_zero_and_negative_odds_handling`
5. **Security & Isolation**:
   - `test_prediction_division_isolation`
   - `test_prediction_season_isolation`
   - `test_prediction_idor_protection`
   - `test_admin_intelligence_rbac`
6. **Financial Invariant**:
   - `test_intelligence_layer_is_financially_read_only` (verifies AI modules have zero execution paths to wallet debit/credit/bet placement).
7. **Backtesting & Verification**:
   - `test_walk_forward_backtest_engine`
   - `test_model_performance_brier_and_calibration`

---

## 8. Verification Strategy
1. Unit tests for each engine in isolation.
2. Integration tests for API routes.
3. Regression check: all 282 baseline tests must remain green.
4. Total expected test count post-Phase 7: **>315 tests**.
