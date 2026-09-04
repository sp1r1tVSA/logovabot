# PHASE 7.1 — TEST MATRIX
## RED TEAM & PRODUCTION ACCEPTANCE AUDIT

| Test ID | Category | Scenario | Expected | Actual | Status | Severity |
|---------|----------|----------|----------|--------|--------|----------|
| RT-FIN-01 | FINANCIAL | AI service inspects codebase for wallet debit tokens | Zero references to `place_user_bet`, `modify_wallet_balance`, `debit_balance` | 0 references found | PASS | CRITICAL |
| RT-FIN-02 | FINANCIAL | Runtime execution of AI prediction does not mutate user balance | User wallet balance is identical before and after prediction | Balance unchanged | PASS | CRITICAL |
| RT-FIN-03 | FINANCIAL | Prediction resolution does not trigger financial settlement | `user_bets` balances and statuses remain untouched | Bets untouched | PASS | CRITICAL |
| RT-LEAK-01 | DATA_LEAKAGE | Target match prediction when future match exists in DB | Future match goals excluded from historical form stats | Goals excluded | PASS | CRITICAL |
| RT-LEAK-02 | DATA_LEAKAGE | Target match H2H analysis with current match | Current match strictly excluded from head-to-head records | Excluded (`total_meetings=0`) | PASS | CRITICAL |
| RT-LEAK-03 | DATA_LEAKAGE | Walk-forward backtesting evaluates strictly out-of-sample | Evaluated matches do not leak into warmup training state | Zero future leakage | PASS | CRITICAL |
| RT-LEAK-04 | DATA_LEAKAGE | Cross-season feature extraction | Matches from Season 2 do not appear in Season 1 features | Strictly isolated | PASS | CRITICAL |
| RT-MATH-01 | MATHEMATICS | Poisson PMF with NaN or negative lambda | Safely bounded to $[0.20, 4.50]$, zero crashes or NaNs | Handled safely | PASS | HIGH |
| RT-MATH-02 | MATHEMATICS | Poisson bivariate grid probabilities sum to 1.0 | Sum of 1X2 equals $1.0 \pm 10^{-3}$ across extreme ratios | Sums to 1.0 | PASS | HIGH |
| RT-MATH-03 | MATHEMATICS | Correct score probability distribution | Probabilities non-negative and bounded in $[0.0, 1.0]$ | All bounded | PASS | HIGH |
| RT-MATH-04 | MATHEMATICS | Log loss calculation with probabilities 0.0 and 1.0 | Clipped to $[\epsilon, 1-\epsilon]$, finite, no $\log(0)$ error | Finite float | PASS | HIGH |
| RT-ELO-01 | MODEL | Pure inference Elo calculation | Database Elo rating is identical before and after prediction | Rating unchanged | PASS | CRITICAL |
| RT-ELO-02 | MODEL | Elo post-match result update | Ratings updated only upon confirmed scores | Updated correctly | PASS | HIGH |
| RT-ENS-01 | MODEL | Ensemble with missing sub-model | Weights dynamically reallocated to sum to 100% | Reallocated cleanly | PASS | HIGH |
| RT-ENS-02 | MODEL | Ensemble with invalid/negative/zero weights | Rejects with descriptive ValueError, no ZeroDivisionError | Rejects safely | PASS | HIGH |
| RT-CAL-01 | CALIBRATION | Platt scaling with extreme probabilities (0.9999, 0.0001) | Values compressed towards mean, strictly in $(0, 1)$ | Valid range | PASS | HIGH |
| RT-CAL-02 | CALIBRATION | Multiclass Brier score on empty sample | Returns None (never fake 0.0) | Returns None | PASS | HIGH |
| RT-VAL-01 | VALUE | Value calculation with NaN / Infinity / negative odds | Invalid odds rejected or filtered out, no crashes | Filtered cleanly | PASS | HIGH |
| RT-VAL-02 | VALUE | Value Radar edge threshold boundary (2.9pp vs 3.0pp) | Picks strictly meeting or exceeding threshold returned | Deterministic filtering | PASS | MEDIUM |
| RT-ANOM-01 | ODDS | Odds anomaly detector with rapid drift | Severity HIGH/MEDIUM assigned, no division by zero | Flagged correctly | PASS | MEDIUM |
| RT-DATA-01 | DATA | Missing xG reporting | Returns `xg_available: false` with zero synthetic data | Zero hallucination | PASS | HIGH |
| RT-PROV-01 | PROVIDER | Production NullSportsDataProvider verification | Returns clean fallback, no fake live data | Clean fallback | PASS | HIGH |
| RT-VER-01 | DATABASE | Prediction immutability across model updates | Old prediction records retain original model version | Immutable | PASS | CRITICAL |
| RT-RBAC-01 | RBAC | Division Admin accessing another division overview | Returns 403 Forbidden | 403 Forbidden | PASS | CRITICAL |
| RT-RBAC-02 | RBAC | Global Admin accessing any division overview | Returns 200 OK | 200 OK | PASS | HIGH |
| RT-IDOR-01 | IDOR | User A accessing User B personalized recommendations | Recommendations strictly scoped to authenticated user | User-isolated | PASS | CRITICAL |
| RT-ISOL-01 | DIVISION | Cross-division team rating isolation | Same club name has separate ratings per division | Scoped independently | PASS | CRITICAL |
| RT-ISOL-02 | SEASON | Cross-season team rating isolation | Same club name has separate ratings per season | Scoped independently | PASS | CRITICAL |
| RT-FUZZ-01 | API | Negative and huge match IDs (`-1`, `9999999`) | Returns 404 or 400 safely, no 500 crashes | Handled safely | PASS | MEDIUM |
| RT-FUZZ-02 | API | Pagination limit fuzzing (`-100`, `100000`, `abc`) | Clamped to safe range $[1, 50]$ | Clamped safely | PASS | MEDIUM |
| RT-SQLI-01 | SECURITY | SQL injection payloads in query params (`' OR '1'='1`) | Parameterized queries prevent syntax errors or data leaks | Parameterized | PASS | CRITICAL |
| RT-CORR-01 | DATABASE | Match result correction re-evaluates predictions | Brier score and correctness updated, no duplicate bet settlement | Updated cleanly | PASS | HIGH |
| RT-REG-01 | REGRESSION | Full pytest regression suite execution | 100% passing across all existing and new test suites | All passed | PASS | CRITICAL |
