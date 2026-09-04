# PHASE 10 — TEST MATRIX
## Production Gamification, Player Rating & Seasonal Progression

| Test ID | Category | Scenario | Expected Result | Status | Severity |
|---|---|---|---|---|---|
| P10-PROF-01 | PROFILE | Public profile access for third party | Strictly returns rating, level, win_rate, streaks, achievements; wallet balances and raw coin wagers strictly omitted | PASSED | CRITICAL |
| P10-PROF-02 | PROFILE | Private profile access for authenticated owner | Returns complete profile including wallet balance, career metrics, and active season progression | PASSED | HIGH |
| P10-PROF-03 | PROFILE | Profile stats computation (favorite markets & accuracy) | Accurately groups settled bets by market type, computes accuracy and ROI per market | PASSED | HIGH |
| P10-PROF-04 | PROFILE | Career stats multi-season aggregation | Aggregates all lifetime settled bets, stakes, payouts, and historical seasons into career stats | PASSED | HIGH |
| P10-PROF-05 | PROFILE | Player comparison endpoint security | Comparison between two players exposes exclusively public competitive stats | PASSED | CRITICAL |
| P10-RATE-01 | RATING | Rating update on bet win and bet loss | Rating increases on win, decreases on loss proportionally to market implied probability | PASSED | HIGH |
| P10-RATE-02 | RATING | Rating non-stake bias (10,000 coins vs 10 coins) | High roller and micro bettor receive identical rating delta for same odds outcome | PASSED | CRITICAL |
| P10-RATE-03 | RATING | Minimum sample requirement (< 5 settled bets) | Player flagged as `is_qualified = False` and status `NOT_ENOUGH_DATA` | PASSED | HIGH |
| P10-RATE-04 | RATING | Transition from QUALIFYING to ACTIVE | After reaching 5 settled bets, status becomes `ACTIVE` and rank is calculated | PASSED | HIGH |
| P10-RATE-05 | RATING | Neutrality of voided / refunded bets | Voided bet results in exactly 0 rating change and preserves confidence | PASSED | HIGH |
| P10-RATE-06 | RATING | Player tier & badge resolution | Rating correctly resolves into tiers (Novice, Bronze, Silver, Gold, Platinum, Diamond, Master, Grandmaster, Legend) | PASSED | MEDIUM |
| P10-LEAD-01 | LEADERBOARD | Global leaderboard sorting | Correctly ranks players by rating descending with season points tie-breaker | PASSED | HIGH |
| P10-LEAD-02 | LEADERBOARD | Division leaderboard isolation | Standings filtered strictly to queried division without cross-division leakage | PASSED | CRITICAL |
| P10-LEAD-03 | LEADERBOARD | Fair leaderboard disqualification flag | Unqualified players flagged with `NOT_ENOUGH_DATA` in leaderboard entries | PASSED | HIGH |
| P10-LEAD-04 | LEADERBOARD | Pagination boundary enforcement | Enforces limit clamping (1..50) and valid offsets | PASSED | HIGH |
| P10-LEAD-05 | LEADERBOARD | Authenticated user pin computation | Attached user pin correctly reflects true rank even if user is not in top-N page | PASSED | HIGH |
| P10-LEAD-06 | LEADERBOARD | Cache invalidation on bet settlement | Settling a match invalidates leaderboard cache for the season and division | PASSED | HIGH |
| P10-SEAS-01 | SEASONS | Season lifecycle state transitions | Strict state machine: `created -> active -> finished -> archived` | PASSED | HIGH |
| P10-SEAS-02 | SEASONS | Cannot reactivate finished or archived season | Attempt to reactivate invalid state raises ValueError or returns error | PASSED | HIGH |
| P10-SEAS-03 | SEASONS | Career stats persistence across season boundary | Creating Season 2 preserves lifetime career stats without zeroing out | PASSED | CRITICAL |
| P10-SEAS-04 | SEASONS | Season-specific stats isolation | Season 1 stats and Season 2 stats maintain separate isolated rows | PASSED | CRITICAL |
| P10-SEAS-05 | SEASONS | Season snapshot creation and immutability | Finalized season writes immutable snapshot rows; updates to old season disallowed | PASSED | CRITICAL |
| P10-PROM-01 | PROMOTION | Promotion and relegation zone calculation | Top N assigned `PROMOTED`, bottom M assigned `RELEGATED`, middle assigned `STAY` | PASSED | HIGH |
| P10-PROM-02 | PROMOTION | Inactive player exclusion from promotion | Players below activity qualification threshold barred from promotion (`INACTIVE`) | PASSED | HIGH |
| P10-PROM-03 | PROMOTION | Custom configurable rules per division | Custom promotion/relegation slots applied accurately per division rules | PASSED | HIGH |
| P10-PROM-04 | PROMOTION | Promotion status persistence in snapshot | `promotion_status` column accurately recorded in `season_snapshots` | PASSED | HIGH |
| P10-PROM-05 | PROMOTION | Relegation boundary edge condition | Handles small leagues and ties gracefully without invalid index bounds | PASSED | HIGH |
| P10-REWD-01 | REWARDS | Season reward ledger idempotency | Attempt to insert duplicate `(user_id, season_id, reward_id)` raises IntegrityError | PASSED | CRITICAL |
| P10-REWD-02 | REWARDS | Wallet integration via coin_transactions | Coins awarded strictly via `database.add_coins()` with type `season_reward` | PASSED | CRITICAL |
| P10-REWD-03 | REWARDS | Season finalization awards rewards once | Running finalization twice does not duplicate coins or XP | PASSED | CRITICAL |
| P10-REWD-04 | REWARDS | Season reward catalog listing | Catalog accurately returns tiers: CHAMPION, TOP_3, TOP_10, PROMOTION, PARTICIPATION | PASSED | MEDIUM |
| P10-REWD-05 | REWARDS | Reward ledger filtering by season | Queries for rewards correctly filter by season and user | PASSED | MEDIUM |
| P10-ACHV-01 | ACHIEVEMENTS | Unlock achievement idempotency | Unlocking existing achievement is idempotent and does not duplicate records | PASSED | HIGH |
| P10-ACHV-02 | ACHIEVEMENTS | Claim reward concurrency and duplicate prevention | Claiming reward twice credits wallet exactly once; second attempt rejected | PASSED | CRITICAL |
| P10-ACHV-03 | ACHIEVEMENTS | Volume achievements evaluation | Automatically awards FIRST_BET, BETS_10, BETS_50, BETS_100 upon milestone | PASSED | HIGH |
| P10-ACHV-04 | ACHIEVEMENTS | Express odds achievements evaluation | Awards EXPRESS_WIN_X10 on express odds $\ge 10.0$ settlement | PASSED | HIGH |
| P10-ACHV-05 | ACHIEVEMENTS | Underdog single win achievement | Awards UNDERDOG_WIN on single bet win with odds $\ge 3.0$ | PASSED | HIGH |
| P10-STRK-01 | STREAKS | Won bet increments streak and updates best streak | `current_streak` + 1 and `best_streak = max(best_streak, current_streak)` | PASSED | HIGH |
| P10-STRK-02 | STREAKS | Lost bet resets current streak to zero | `current_streak` becomes 0, `best_streak` is preserved | PASSED | HIGH |
| P10-STRK-03 | STREAKS | Voided / refunded bet neutrality | Streak is preserved unchanged on void or refund | PASSED | HIGH |
| P10-STRK-04 | STREAKS | Pending bet has zero effect on streak | Unsettled bets do not alter current or best streak | PASSED | MEDIUM |
| P10-STRK-05 | STREAKS | Streak milestone achievements unlock | Automatically unlocks WIN_STREAK_3, WIN_STREAK_5, WIN_STREAK_10 | PASSED | HIGH |
| P10-ABUS-01 | ANTI_ABUSE | Repeated micro-bets rating abuse defense | Spamming small bets does not artificially inflate rating over authentic skill | PASSED | HIGH |
| P10-ABUS-02 | ANTI_ABUSE | Diminishing rating returns on low-risk farming | Heavy odds (e.g. 1.02) yield minimal rating gain ($<0.5$) with high penalty on loss | PASSED | HIGH |
| P10-ABUS-03 | ANTI_ABUSE | Minimum qualification filter protects leaderboards | Unqualified spam accounts flagged with `NOT_ENOUGH_DATA` | PASSED | CRITICAL |
| P10-ABUS-04 | ANTI_ABUSE | Cancelled / refunded bets award zero points | Zero rating, zero season points, and zero streak change on refunds | PASSED | HIGH |
| P10-ABUS-05 | ANTI_ABUSE | Extreme odds bounding in rating formula | Odds bounded between 1.01 and 100.0 for implied probability calculation | PASSED | HIGH |
| P10-SEC-01 | SECURITY | Unauthenticated request returns 401 | Requests without valid Telegram WebApp initData header rejected | PASSED | CRITICAL |
| P10-SEC-02 | SECURITY | Regular player accessing admin season returns 403 | RBAC blocks non-admins from admin season management | PASSED | CRITICAL |
| P10-SEC-03 | SECURITY | Division admin scoped to assigned division | Division admin cannot view or edit rules of unauthorized divisions | PASSED | CRITICAL |
| P10-SEC-04 | SECURITY | Division admin cannot finalize season | Only global admin can finalize entire season (returns 403) | PASSED | CRITICAL |
| P10-SEC-05 | SECURITY | IDOR defense (public vs private profile) | Querying other user ID returns strictly public profile; private fields 404 or masked | PASSED | CRITICAL |
| P10-SEC-06 | SECURITY | SQL injection defense in leaderboard queries | Query parameters (scope, metric, period) parameterized; injection attempts neutralized | PASSED | CRITICAL |
| P10-CONC-01 | CONCURRENCY | Concurrent season finalization race | Two concurrent finalization requests: exactly 1 succeeds, second cleanly fails | PASSED | CRITICAL |
| P10-CONC-02 | CONCURRENCY | Concurrent achievement claims race | Two threads claiming same achievement: exactly 1 credits wallet, second rejected | PASSED | CRITICAL |
| P10-CONC-03 | CONCURRENCY | Concurrent reward ledger inserts | Database uniqueness constraint `(user_id, season_id, reward_id)` prevents duplicate records | PASSED | CRITICAL |
| P10-CONC-04 | CONCURRENCY | Concurrent bet settlements streak consistency | Multiple concurrent settlements result in consistent streak without lost updates | PASSED | CRITICAL |
| P10-CONC-05 | CONCURRENCY | Concurrent settlement during season finalization | Database transaction locks protect state integrity during finalization | PASSED | CRITICAL |
| P10-ISOL-01 | DIVISION_SEASON | Standings isolated across all 5 divisions | Standings for Divisions 1 to 5 are strictly separate | PASSED | CRITICAL |
| P10-ISOL-02 | DIVISION_SEASON | Division 1 points do not bleed into Division 2 | Points earned in one division never alter standings in another | PASSED | CRITICAL |
| P10-ISOL-03 | DIVISION_SEASON | Historical season rankings isolated from active season | Season 1 final standings remain unchanged when Season 2 starts | PASSED | CRITICAL |
| P10-ISOL-04 | DIVISION_SEASON | Snapshot queries filtered by division | Querying historical snapshots for division returns only that division's data | PASSED | HIGH |
| P10-ISOL-05 | DIVISION_SEASON | Division rules isolation | Configuring promotion slots for Division 1 does not overwrite Division 2 | PASSED | HIGH |
| P10-API-01 | API | `GET /api/profile` returns private profile for self | Endpoint returns user's wallet, level, XP, and season stats | PASSED | HIGH |
| P10-API-02 | API | `GET /api/player/{id}/public` returns public profile | Endpoint returns public stats, omitting balance and raw stakes | PASSED | CRITICAL |
| P10-API-03 | API | `GET /api/profile/stats` returns breakdown | Endpoint returns favorite markets, accuracy, and streak metrics | PASSED | HIGH |
| P10-API-04 | API | `GET /api/leaderboard` pagination and metrics | Supports `scope`, `metric`, `division_id`, `season_id`, `limit`, `offset` | PASSED | HIGH |
| P10-API-05 | API | `GET /api/season` and `/rewards` | Returns active season info, rules, and rewards catalog | PASSED | HIGH |
| P10-API-06 | API | Admin season management endpoints | Global admin can create, configure, and finalize seasons via REST | PASSED | HIGH |

---
**Summary**: 69 Comprehensive Test Scenarios Planned & Verified (100% Pass Rate across 13 Test Suites).
