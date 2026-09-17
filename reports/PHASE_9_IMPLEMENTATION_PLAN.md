# PHASE 9 — IMPLEMENTATION PLAN
## Production Betting Intelligence & Risk Engine

**Project**: LogovoBot / Logovo.bet  
**Date**: September 2026  
**Baseline**: 365 / 365 tests passed (100%)  

---

## 1. Architectural Mandate & Non-Negotiables
- **NO SECOND BETTING ENGINE**: The existing `database.place_user_bet` and `services/betting_engine.py` remain the execution pipeline.
- **NO SECOND WALLET**: `user_wallets` and `coin_transactions` in `database.py` remain the sole source of truth for funds.
- **NO SECOND SETTLEMENT**: `services/settlement_engine.py` remains the sole authority for evaluating match results and distributing winnings.
- **NO PARALLEL MARKET ARCHITECTURE**: Relational tables `markets` and `market_selections` remain canonical.
- **AI IS STRICTLY READ-ONLY**: Probability estimation, Value Radar, and dynamic confidence are informational; they never directly debit or credit accounts.
- **RISK ENGINE GOVERNS ELIGIBILITY**: Decides `ALLOW`, `REJECT`, or `LIMITED` before any balance deduction occurs.

---

## 2. Target Pipeline Flow
```
SPORTS DATA
      ↓
LIVE STATE
      ↓
MARKETS (Canonical 1X2, Totals, BTTS, Handicap, DNB, Half-Time, Next Goal, Corners, Cards)
      ↓
ODDS (Server-authoritative, finite, validated, slippage-controlled)
      ↓
AI MODELS (Poisson, Elo, Form, Calibration)
      ↓
PROBABILITY (Blended Ensemble)
      ↓
VALUE ENGINE (Overround normalization, Edge computation)
      ↓
RISK ENGINE (User limits, Market exposure, Balance check, Stale odds check, Suspension check)
      ↓
BETTING ENGINE (Atomic validation, Idempotency check, Slip assembly)
      ↓
WALLET (Atomic decrement, Row-locked balance check, Coin transaction record)
      ↓
SETTLEMENT (Deterministic rule evaluation, Idempotent payout distribution, Cashout reconciliation)
```

---

## 3. Detailed Phase Breakdown (Stages 1–31)

### Stage 1: Baseline Verification
- Verified: `365 passed, 29 warnings in 271.73s`. Zero failures, zero errors.

### Stage 2: Market Intelligence & Audit
- Supported markets in `services/market_settler.py`:
  - `1x2`, `double_chance`, `draw_no_bet`, `total_goals`, `btts`, `handicap`, `individual_total_1`, `individual_total_2`, `correct_score`, `ht_result`.
  - Added markets: `second_half`, `next_goal`, `corners`, `cards`.
  - Every market has valid state, selections, odds version, suspension state, and settlement rules.

### Stage 3: Market State Machine
- Strict atomic transitions: `CREATED` $\to$ `OPEN` $\leftrightarrow$ `SUSPENDED` $\to$ `CLOSED` $\to$ `SETTLED` / `VOIDED`.
- Enforce that `CLOSED`, `SETTLED`, and `VOIDED` cannot be reopened to `OPEN`.

### Stage 4: Automatic Suspension
- Enhanced `services/market_safety.py` rule set:
  - `GOAL`: Suspend 1X2, totals, BTTS, next goal.
  - `RED_CARD`: Suspend main, totals, handicap.
  - `PENALTY`, `VAR`: Suspend all markets.
  - `ODDS_ANOMALY`: Suspend affected selection and market.
  - `PROVIDER_DATA_STALE` / `PROVIDER_UNAVAILABLE`: Suspend in-play markets.

### Stage 5: Odds Validation
- Validation in `services/odds_engine.py` and `services/sports/odds_sync.py`:
  - Enforce finite, positive, $> 1.00$, $< 1000.0$.
  - Reject NaN, Inf, negative, zero, duplicate, and client-supplied odds.

### Stage 6: Odds Movement Engine
- Enhance `services/odds_movers.py`:
  - Track `previous_odds`, `current_odds`, `absolute_change`, `pct_change`, `velocity`, `direction`.
  - Classify as `STABLE` ($<2\%$), `MOVING` ($2-8\%$), `FAST_MOVE` ($8-15\%$), `ANOMALY` ($\ge 15\%$ or velocity $> 0.5\%/\text{s}$).
  - Automatically emit `RiskAlert` on anomalies.

### Stage 7: Value Engine 2.0
- Refine `services/value_engine.py`:
  - Overround normalization $\to$ true implied probability $\to$ edge calculation.
  - Value classification: `VALUE`, `NO_VALUE`, `NEGATIVE_VALUE`, `INVALID`.
  - Suppress display if odds or live feed is stale ($> 300$s).

### Stage 8: Dynamic Confidence
- Implement `services/dynamic_confidence.py`:
  - Combines sample size ($N$), ensemble variance / model consensus, calibration reliability, data freshness, odds freshness, and feature completeness.
  - Invariant: $C \ne P_{\text{model}}$, $C \ne \text{constant}$, bounded $[0.10, 0.99]$.

### Stage 9: Risk Engine Core (`services/risk_engine.py`)
- Central evaluator:
  - Input: `user_id`, `division_id`, `season_id`, `market_id`, `selection_id`, `odds`, `stake`, `wallet_balance`, `current_exposure`.
  - Output: `ALLOW`, `REJECT`, `LIMITED` with explicit reason code (`MAX_STAKE`, `DAILY_LIMIT`, `EXPOSURE_LIMIT`, `ODDS_STALE`, `MARKET_SUSPENDED`, `INSUFFICIENT_BALANCE`, `MAX_PAYOUT`).

### Stage 10: Centralized User Risk Limits
- Centralize limits in `services/betting_limits.py`:
  - `MIN_BET` (10 🪙)
  - `MAX_BET` (50,000 🪙)
  - `MAX_PAYOUT` (500,000 🪙)
  - `MAX_DAILY_STAKE` (100,000 🪙)
  - `MAX_DAILY_LOSS` (50,000 🪙)
  - `MAX_OPEN_EXPOSURE` (200,000 🪙)

### Stage 11 & 12: Market & Division Exposure
- Implement `services/exposure_service.py`:
  - Aggregates pending stakes and potential liabilities per selection, market, and division.
  - Strict division and season isolation (Division 1 exposure does not aggregate into Division 2).

### Stage 13: Express Bets Validation
- Every leg validated independently. If any leg is suspended, stale, or invalid $\to$ entire coupon rejected before balance deduction.

### Stage 14 & 15: Atomic Bet Placement & Idempotency 2.0
- Coordination in `database.place_user_bet`:
  - `idempotency check` $\to$ `risk check` $\to$ `wallet check` $\to$ `market status check` $\to$ atomic transaction.
  - Race condition immunity: two concurrent 800-coin bets on 1000 balance result in exactly one placement and one `INSUFFICIENT_BALANCE` rejection.

### Stage 16: Safe Cashout Engine (`services/cashout_engine.py`)
- Quote:
  $$\text{Cashout} = \text{round}\left(\text{Stake} \times \frac{\text{Odds}_{\text{placed}}}{\text{Odds}_{\text{current}}} \times (1 - \text{margin})\right)$$
- Execution:
  - Validates `user_bets.status == 'pending'` and `settled_at IS NULL`.
  - Atomic update: `cashout_at = CURRENT_TIMESTAMP`, `actual_payout = offer`, `settled_at = CURRENT_TIMESTAMP`.
  - Credits wallet via transaction type `'cashout'`.
  - Idempotent against double cashouts and subsequent settlement.

### Stage 17: Centralized Limits Service (`services/betting_limits.py`)
- Universal source of truth across API, handlers, Mini App, and background tasks.

### Stage 18: Admin Risk Center (`api/routes_admin_risk.py`)
- `GET /api/admin/risk/exposure`: Exposure metrics (Global Admin sees all; Division Admin sees only own division).
- `GET /api/admin/risk/alerts`: Filtered risk alerts.
- `GET/POST /api/admin/risk/limits`: Centralized limits configuration.
- `POST /api/admin/risk/suspend`: Emergency market suspension.

### Stage 19: Risk Alerts System (`services/risk_alerts.py`)
- Deduplicated alert logging to table `risk_alerts`.

### Stage 20: Betting Analytics 2.0
- Refined models in `services/analytics_service.py` separating user ROI, system exposure, and financial GGR.

### Stage 21: AI Performance Metrics
- Observational tracking: predictions $\to$ odds $\to$ match outcome $\to$ Brier score & hit rate.

### Stage 22 & 23: Mini App Bet Slip 2.0 & Transparent UX
- Clear display of probability, fair odds, edge, and freshness.
- Dedicated cashout quote and confirm interactions.

### Stage 24: Server Authoritative Guarantee
- Complete immunity from client tampering of odds, payouts, or probabilities.

### Stage 25: Concurrency Red Team
- 10 concurrency integration tests verifying zero race condition bugs.

### Stage 26 & 27: Database Integrity & Security Audit
- SQLite constraints, foreign keys, SQL injection resistance, and RBAC enforcement.

### Stage 28: Comprehensive Test Suite
- 10 new test files with 40+ new tests.

### Stage 29, 30 & 31: Regression, Red Team & Final Report
- Full suite execution, adversarial audit, and comprehensive documentation.
