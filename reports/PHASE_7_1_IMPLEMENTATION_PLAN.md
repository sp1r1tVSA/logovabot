# PHASE 7.1 — AI RED TEAM & PRODUCTION ACCEPTANCE
## IMPLEMENTATION PLAN

### 1. Architecture Audit
The Phase 7 implementation introduced a layered analytical pipeline:
- `FeatureEngine`: Historical feature aggregation (rolling form, recency weighting, home/away splits, H2H, xG detection).
- `PoissonModel`: Bivariate Poisson 2.0 with dynamic goal expectancy bounds $[0.20, 4.50]$.
- `EloEngine`: Football Elo rating system with home advantage bonus ($+65.0$), draw decay, and zero inference mutation.
- `FormModel`: Normalized FormScore $\in [0.0, 1.0]$.
- `EnsemblePredictionEngine`: Blending Poisson ($40\%$), Elo ($35\%$), Form ($25\%$) with dynamic weight reallocation and explainable key factors.
- `ProbabilityCalibrator`: Platt scaling, multi-class Brier score, and reliability reports.
- `ValueEngine` & `ValueRadar`: Overround calculation, true implied probability, and edge detection.
- `BacktestEngine` & `ModelPerformanceService`: Walk-forward chronological backtesting enforcing `MIN_SAMPLE_SIZE = 10`.
- `api/routes_intelligence.py`: 11 REST endpoints mounted on embedded aiohttp server.
- `web/`: Match Center with AI probabilities, confidence, Elo comparison, and key factors.

### 2. Threat Model
- **Financial Contamination**: AI layer attempting to debit/credit balances, place bets, or manipulate settlement.
- **Temporal Data Leakage**: Future match results, future odds, or current match stats leaking into feature extraction or backtests.
- **Cross-Scope Contamination**: Mixing Division 1–5 or Season 1–2 metrics, team ratings, and predictions.
- **Numerical Singularities**: `NaN`, `Infinity`, negative/zero values causing zero-division or uncaught math domain exceptions.
- **Authorization & RBAC Bypasses**: Division Admin accessing other division metrics, unauthenticated requests bypassing auth due to aiohttp falsy evaluation.
- **Fuzzing & Injection**: Malformed IDs, extreme limits, SQL injection payloads in query parameters.
- **Data Integrity & Hallucinations**: Fabricating xG, lineups, or statistics when external live provider is absent.

### 3. Mathematical Audit
- Poisson bivariate grid must strictly produce probabilities in $[0.0, 1.0]$ summing to $1.0 \pm \epsilon$.
- Lambda inputs must be validated against `NaN`, `Infinity`, and non-positive floats.
- Platt scaling logit transformation must clip inputs away from $0.0$ and $1.0$ ($[\epsilon, 1-\epsilon]$).
- Log loss computation must strictly clip probabilities to prevent $-\log(0) = \infty$.

### 4. Data Leakage Audit
- `FeatureEngine`: Must strictly filter `id < ref_match_id` across overall, home, away, and H2H queries.
- H2H extraction must never treat the target fixture as an existing historical encounter.
- Backtest engine must train strictly on past matches ($id < target\_id$) before evaluating each match.

### 5. Security Audit
- HMAC authentication validation on all `/api/intelligence/*` routes.
- Constant-time signature comparison and 24-hour freshness check.
- Parameterized SQL across all queries to prevent SQL injection.

### 6. Financial Isolation Audit
- AI and Intelligence services must be strictly read-only.
- Runtime and static inspection to verify zero access to wallet manipulation functions (`place_user_bet`, `modify_wallet_balance`, `credit_balance`, `debit_balance`, `void_user_bet`).

### 7. Division and Season Isolation
- Team Elo ratings must remain strictly partitioned by `(division_id, season_id)`.
- Features, predictions, and recommendations must never blend data across different divisions or seasons.

### 8. Prediction Integrity & Snapshots
- Predictions and snapshots must be immutable once created.
- Post-match resolution must calculate Brier score and accuracy against verified final scores.
- Result correction must update prediction metrics without re-triggering bet settlements.

### 9. Calibration Audit
- Brier score calculation must return `None` on empty or unsettled datasets (never fake 0.0).
- Reliability curve buckets must report `None` for empty buckets without fabricating 0% accuracy.

### 10. Backtest Audit
- Enforce `MIN_SAMPLE_SIZE = 10` before reporting valid statistical summaries.
- Comparative scorecard must rank models deterministically by accuracy and Brier score.

### 11. API Audit
- Enforce strict Division Admin RBAC on `/api/admin/intelligence/overview` (prevent cross-division snooping).
- Input fuzzing: negative match IDs, string parameters, huge pagination limits must be safely handled.

### 12. Mini App Audit
- UI must strictly treat backend as authoritative; client-side modifications cannot alter odds or probabilities.
- Phrasing must remain strictly analytical (zero "guaranteed win" claims).

### 13. Concurrency Audit
- Concurrent prediction requests and simultaneous odds/result updates must maintain SQLite WAL consistency.

### 14. Database Audit
- Foreign keys, composite unique constraints, and schema idempotency verified via `PRAGMA integrity_check`.

### 15. Performance Audit
- Avoid heavy backtests on high-frequency HTTP endpoints.
- Scans and list endpoints must be properly indexed and bounded (`LIMIT 50`).

### 16. Test Plan
- Create `tests/test_phase7_1_redteam.py` containing targeted adversarial attack vectors.
- Cover all 20 attack categories specified in Phase 7.1.

### 17. Fix Plan
- Fix Division Admin RBAC check in `api/routes_intelligence.py`.
- Harden `ValueEngine.analyze_match_value` against `NaN` and `Infinity` odds.
- Harden `EnsemblePredictionEngine` against negative/zero/NaN weights.
- Add `correct_ai_predictions` in `database.py` for match score corrections.
- Add `calculate_log_loss` with numerical clipping in `services/calibration.py`.

### 18. Regression Plan
- Run `python -m pytest tests/test_phase7_1_redteam.py -v`.
- Run full test suite `python -m pytest tests/ -q` to guarantee 301 baseline + new tests all pass.
