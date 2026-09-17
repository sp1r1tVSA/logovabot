# PHASE 9 — TEST MATRIX
## Production Betting Intelligence & Risk Engine

| Test ID | Category | Scenario | Expected Result | Status | Severity |
|---------|----------|----------|-----------------|--------|----------|
| P9-RISK-01 | RISK_ENGINE | Bet with stake below MIN_BET (e.g. 5 coins) | Rejected with MIN_BET error | PASSED | HIGH |
| P9-RISK-02 | RISK_ENGINE | Bet with stake above MAX_BET (e.g. 60,000 coins) | Rejected or limited to MAX_BET | PASSED | HIGH |
| P9-RISK-03 | RISK_ENGINE | Bet with potential payout > MAX_PAYOUT | Rejected or stake limited to cap payout | PASSED | HIGH |
| P9-RISK-04 | RISK_ENGINE | User exceeding daily betting limit | Rejected with DAILY_LIMIT error | PASSED | CRITICAL |
| P9-RISK-05 | RISK_ENGINE | Bet on suspended or locked market | Rejected with MARKET_SUSPENDED error | PASSED | CRITICAL |
| P9-RISK-06 | RISK_ENGINE | Bet on stale odds (>300s since update) | Rejected with ODDS_STALE error | PASSED | HIGH |
| P9-RISK-07 | RISK_ENGINE | Bet with insufficient wallet balance | Rejected with INSUFFICIENT_BALANCE error | PASSED | CRITICAL |
| P9-EXPO-01 | EXPOSURE | Calculate market total stake and liability | Accurate breakdown per selection | PASSED | HIGH |
| P9-EXPO-02 | EXPOSURE | Market net exposure exceeds ceiling | Risk engine flags or limits further betting on selection | PASSED | HIGH |
| P9-EXPO-03 | EXPOSURE | Division exposure aggregation | Only includes markets belonging to specific division | PASSED | CRITICAL |
| P9-EXPO-04 | EXPOSURE | Global exposure aggregation | Aggregates all divisions without double-counting | PASSED | HIGH |
| P9-LIMIT-01 | LIMITS | Centralized BettingLimitsService single source | Same limits returned to API, DB, and UI | PASSED | HIGH |
| P9-LIMIT-02 | LIMITS | Global Admin updates division limits | Updated limits take effect immediately for division | PASSED | HIGH |
| P9-LIMIT-03 | LIMITS | Division Admin cannot alter global limits | Returns 403 Forbidden | PASSED | CRITICAL |
| P9-ODDS-01 | ODDS_VALID | Ingest valid odds update | Selection odds_version increments, history recorded | PASSED | HIGH |
| P9-ODDS-02 | ODDS_VALID | Ingest NaN, Inf, or negative odds | Rejected without mutating database | PASSED | CRITICAL |
| P9-ODDS-03 | ODDS_VALID | Extreme odds update (>1000 or <1.01) | Rejected as out of bounds | PASSED | HIGH |
| P9-ODDS-04 | ODDS_VALID | Odds movement classification | Classified correctly into STABLE, MOVING, FAST, ANOMALY | PASSED | MEDIUM |
| P9-ODDS-05 | ODDS_VALID | Rapid odds drop triggers risk alert | RiskAlert created in database | PASSED | HIGH |
| P9-ATOM-01 | ATOMIC_BET | Normal valid single bet placement | Balance deducted, bet & items inserted, transaction logged | PASSED | CRITICAL |
| P9-ATOM-02 | ATOMIC_BET | Valid express bet with multiple matches | All legs recorded, combined odds calculated correctly | PASSED | HIGH |
| P9-ATOM-03 | ATOMIC_BET | Express bet with one invalid leg | Entire bet rejected, zero coins deducted | PASSED | CRITICAL |
| P9-ATOM-04 | ATOMIC_BET | Concurrent overdraft race (1000 bal, 800+800 bets) | Exactly one bet succeeds, balance never drops below 0 | PASSED | CRITICAL |
| P9-ATOM-05 | ATOMIC_BET | Idempotent duplicate submission | Returns existing bet_id, zero duplicate debit | PASSED | CRITICAL |
| P9-ATOM-06 | ATOMIC_BET | Idempotency key reused with different payload | Rejected with IDEMPOTENCY_KEY_REUSED | PASSED | HIGH |
| P9-CASH-01 | CASHOUT | Cashout quote calculation for winning leg | Fair offer calculated, positive and $< \text{potential\_win}$ | PASSED | HIGH |
| P9-CASH-02 | CASHOUT | Cashout quote for lost leg | Returns 0 or unavailable | PASSED | HIGH |
| P9-CASH-03 | CASHOUT | Cashout execution atomic settlement | Actual payout credited, status won/cashed_out, transaction logged | PASSED | CRITICAL |
| P9-CASH-04 | CASHOUT | Duplicate cashout attempt | Second attempt rejected as already settled | PASSED | CRITICAL |
| P9-CASH-05 | CASHOUT | Cashout on suspended/closed market | Rejected as unavailable | PASSED | HIGH |
| P9-CASH-06 | CASHOUT | Post-cashout match settlement | Settlement engine skips cashed-out bet, zero duplicate payout | PASSED | CRITICAL |
| P9-CONC-01 | CONCURRENCY | Bet placement simultaneous with market suspension | Bet rejected or suspended cleanly | PASSED | HIGH |
| P9-CONC-02 | CONCURRENCY | Bet placement simultaneous with odds update | Triggers ODDS_CHANGED rejection | PASSED | HIGH |
| P9-CONC-03 | CONCURRENCY | Bet placement simultaneous with match finish | Rejected as match no longer open | PASSED | HIGH |
| P9-CONC-04 | CONCURRENCY | Two concurrent settlements on same match | Match settled exactly once, zero duplicate winnings | PASSED | CRITICAL |
| P9-CONC-05 | CONCURRENCY | Two concurrent admin void requests | Bet refunded exactly once | PASSED | CRITICAL |
| P9-SEC-01 | SECURITY_RBAC | Unauthenticated request to admin risk endpoints | Returns 401 Unauthorized | PASSED | CRITICAL |
| P9-SEC-02 | SECURITY_RBAC | Division Admin querying other division's exposure | Returns 403 Forbidden | PASSED | CRITICAL |
| P9-SEC-03 | SECURITY_RBAC | Player querying admin risk center | Returns 403 Forbidden | PASSED | CRITICAL |
| P9-SEC-04 | SECURITY_RBAC | Client tampering with payout (stake=1, payout=1M) | Server calculates true payout, client value discarded | PASSED | CRITICAL |
| P9-SEC-05 | SECURITY_RBAC | SQL injection payloads in risk queries | Safely parameterized, zero SQL syntax error or breach | PASSED | CRITICAL |
| P9-ISOL-01 | ISOLATION | Division 1 bets do not affect Division 2 exposure | Strict division boundary enforcement | PASSED | CRITICAL |
| P9-ISOL-02 | ISOLATION | Season 1 bets do not affect Season 2 analytics | Strict season boundary enforcement | PASSED | CRITICAL |
| P9-APP-01 | MINIAPP | Bet slip 2.0 API contract verification | Returns probabilities, edges, and fresh odds | PASSED | HIGH |
| P9-APP-02 | MINIAPP | Cashout quote and execute API endpoints | Responds with structured quote and successful execution | PASSED | HIGH |
| P9-FIN-01 | FINANCIAL | Zero financial access in AI & Risk telemetry | AI and Risk modules cannot debit/credit outside Betting/Settlement | PASSED | CRITICAL |
| P9-REG-01 | REGRESSION | Full regression test suite execution | 365 baseline tests + Phase 9 tests pass (100%) | PASSED | CRITICAL |

---
**Summary**: 47 Comprehensive Test Scenarios Planned & Verified (100% Pass Rate).
