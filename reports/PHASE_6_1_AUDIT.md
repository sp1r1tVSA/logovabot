# PHASE 6.1 PRODUCTION ACCEPTANCE & RED TEAM AUDIT

## 1. Executive Summary & Audit Baseline
- **Audit Date:** 2026-09-04
- **Audit Objective:** Ruthlessly stress-test, attack, and attempt to break Phase 1–6 implementations without adding new features.
- **Baseline Test Suite Execution:**
  - Command: `python -m pytest tests/ -q`
  - Result: **262 passed, 20 warnings** (aiohttp `@unittest_run_loop` deprecation).
  - Baseline Test Count: **262**
- **Git Working Tree Status:**
  - Branch: `main` (up to date with `origin/main`)
  - Tracked modifications: 14 files
  - Untracked Phase 6 files: 21 files
  - No secrets detected in repo.

---

## 2. Architecture Map

### High-Level System Topology
```
Telegram Client (Bot Chat / WebApp Mini App)
   │
   ▼
Telegram WebApp / Mini App (Vanilla JS/CSS, viewport 320px–430px)
   │
   ▼ (HTTP REST API / Bearer initData HMAC)
aiohttp Web Server (api/server.py, port 8080)
   ├── Auth Middleware (api/auth.py, HMAC-SHA256, auth_date expiry)
   ├── Route Controllers (api/routes_*.py)
   │     ├── Live Center: api/routes_live.py, api/routes_admin_live.py
   │     ├── Predictions/Bets: api/routes_predictions.py
   │     ├── Matches & Hot: api/routes_matches.py
   │     ├── Wallet & Leaderboard: api/routes_wallet.py
   │     └── User Extras & Analytics: api/routes_user_extras.py
   ▼
Domain Services Layer
   ├── Live: services/sports_provider.py, services/live_ingestion.py, services/live_state_machine.py, services/market_safety.py
   ├── Betting & Odds: services/odds_engine.py, services/odds_movers.py, services/settlement_engine.py, services/market_settler.py
   ├── Intelligence: services/intelligence_engine.py, services/recommendation_engine.py, services/analytics_service.py
   └── Dispatch: services/notification_service.py, services/background_sync.py
   ▼
Database Reliability Layer (database.py)
   ├── SQLite 3 (WAL mode: PRAGMA journal_mode=WAL)
   ├── Parameterized transactions: `with transaction() as conn:`
   ├── Process concurrency guard: `_bet_placement_lock`
   └── Integrity constraints: FOREIGN KEY, UNIQUE, CHECK
```

### Subsystem Flow: Live Ingestion & Processing
```
SportsDataProvider (NullSportsDataProvider default, MockSportsDataProvider in test)
   │
   ▼
Validation & Normalization (LiveMatchState, LiveEvent, LiveStatistics [NULL preserved])
   │
   ▼
Live Ingestion Engine (services/live_ingestion.py)
   ├── Deduplication: UNIQUE(provider, provider_event_id)
   ├── Sequence / Monotonic Score Check
   ├── Out-of-order & stale event filter
   ▼
Live State Machine (services/live_state_machine.py)
   ├── Valid transitions: SCHEDULED → PRE_MATCH → LIVE ↔ HALFTIME → FINISHED
   ├── Terminal states: FINISHED, CANCELLED, ABANDONED
   ▼
Side-Effect Triggering:
   ├── Market Safety Engine (services/market_safety.py): Auto-suspend markets on GOAL, RED_CARD, VAR
   ├── Odds Movement Tracker (services/odds_movers.py): Log velocity & pct_change to odds_movement
   └── Smart Notification Dispatcher (services/notification_service.py): Queue to notification_events
   ▼
Live DB (live_match_states, live_events, live_statistics)
   │
   ▼
Live API (api/routes_live.py) → Mini App UI (Live Center 2.0)
```

### Subsystem Flow: Betting & Financial Settlement
```
Client (Mini App)
   │ POST /api/predictions (stake, selections, idempotency_key, odds_snapshot)
   ▼
API Layer (api/routes_predictions.py)
   ├── HMAC Auth Validation (Telegram initData)
   ├── Client manipulation filter (ignore client-provided payouts/probabilities)
   ▼
database.place_user_bet(...)
   ├── Lock: _bet_placement_lock
   ├── DB Transaction: with transaction() as conn:
   ├── Idempotency Check:
   │     - Key match + identical payload -> return existing bet_id (200)
   │     - Key match + different payload -> IDEMPOTENCY_KEY_REUSED (409)
   ├── Market Status Validation: MUST NOT be suspended, closed, settled, voided
   ├── Server Odds Check: MUST match current server odds within drift tolerance; else ODDS_CHANGED (409)
   ├── Financial Limit Checks: MIN_BET <= stake <= MAX_BET, potential_payout <= MAX_PAYOUT
   ├── Atomic Wallet Check & Debit: balance >= stake -> UPDATE user_wallets balance = balance - stake
   ├── INSERT coin_transactions (type='bet_placed')
   ├── INSERT user_bets + bet_items
   └── Audit Log: bet_audit_log
   ▼
Settlement (Match Confirmed / Finished)
   │
   ▼
services/settlement_engine.py
   ├── Atomic conditional check: WHERE settled_at IS NULL
   ├── Multi-item express calculation
   ├── Wallet Crediting: UPDATE user_wallets SET balance = balance + payout
   ├── INSERT coin_transactions (type='bet_payout')
   ├── UPDATE user_bets SET status = 'won'/'lost'/'refunded', settled_at = CURRENT_TIMESTAMP
   └── Notification trigger (deduplicated)
```

### Subsystem Flow: Intelligence & Recommendations
```
Match Data (matches, live_match_states, live_statistics)
   │
   ▼
Intelligence Engine (services/intelligence_engine.py)
   ├── Form Momentum (5-match record, goals scored/conceded)
   ├── Head-to-Head (H2H past encounters, win rates, goal averages)
   ├── Poisson Goal Distribution Model (λ_home, λ_away)
   │     - Strict bounds check: 0.0 <= P <= 1.0, no NaN, no Inf
   ├── Implied Probability: 1 / decimal_odds
   └── Value Edge: model_prob - implied_prob (Edge > 3% points)
   ▼
Recommendation Engine (services/recommendation_engine.py)
   ├── User preferences & active division isolation
   └── Multi-factor Hot Match scoring
   ▼
Analytics & Leaderboard (services/analytics_service.py)
   ├── Strict KPI: ROI = NULL if staked == 0; win_rate = NULL if settled == 0
   └── Capper Leaderboard: MIN_LEADERBOARD_BETS = 5 threshold
```

---

## 3. Production Sports Data Provider Audit (Step 3)

| Criterion | Verified Finding | Status |
| :--- | :--- | :---: |
| Default Provider | `NullSportsDataProvider` in `services/sports_provider.py` | Verified |
| Mock Provider Isolation | `MockSportsDataProvider` strictly gated to test/dev environment | Verified |
| Live Data Availability | Explicitly reports `"LIVE DATA UNAVAILABLE"` | Verified |
| Fake Data Emitted | Zero fake scores, events, or statistics emitted in production | Verified |
| Real Live Provider API | External API integration (`APISportsProvider`) requires `APISPORTS_KEY` | Pending Key |

### Formal Declaration
- **REAL LIVE DATA PROVIDER:** **NO**
- **PRODUCTION LIVE DATA:** **NOT AVAILABLE**
- **Architecture Integrity:** Live pipeline abstractions, normalizers, deduplication, state machines, and safety triggers are implemented, but real production sports-data feed integration remains in honest standby until external credentials/OCR stream are connected.

---

## 4. Red Team Attack Surface & Plan (Steps 4–50)

We have mapped the 50 attack scenarios into a comprehensive, adversarial test matrix to break Phase 1–6:

1. **Live Event & Score Attacks (Steps 4, 5, 6, 31, 32):**
   - Duplicate events, duplicate goals, out-of-order minutes, future-dated events, unknown event types.
   - Non-monotonic score changes (e.g. attempting to decrement score from 1:0 to 0:0 without correction flow).
   - Illegal state machine transitions (`FINISHED` → `LIVE`, `HALFTIME` → `PRE_MATCH`, `PRE_MATCH` → `FINISHED`).
   - Provider failure handling (connection timeout, 500, malformed JSON).
   - Stale match data detection and warning.

2. **Market Suspension & Odds Race Attacks (Steps 7, 8, 9):**
   - Race condition: Placing a bet simultaneously with market auto-suspension on a goal event.
   - Odds race: Placing bet with client odds `2.50` when server odds changed to `1.80` -> Must return `HTTP 409 ODDS_CHANGED`.
   - Client financial manipulation: Client tampering with `odds`, `payout`, `potential_payout`, `probability`, `stake_limits` -> Server MUST compute authoritatively.

3. **Financial Concurrency & Settlement Attacks (Steps 10, 11, 12, 13, 14, 15, 16, 44, 45):**
   - Double bet attack: 10–50 concurrent identical bet placement requests -> Exactly one bet placed and debited.
   - Idempotency key attack: Same key with altered payload -> `409 IDEMPOTENCY_KEY_REUSED`.
   - Wallet concurrency attack: User with balance `1000` places two simultaneous bets of `700` each -> Only one succeeds; balance never drops below zero.
   - Limits attack: `stake = MAX_BET + 1` or `payout > MAX_PAYOUT` -> Rejected.
   - Settlement race: Concurrent settlement runs on the same winning bet -> Exactly one payout transaction and crediting.
   - Refund attacks: Double refund / void on settled bet -> Conditional update guarantees single refund.
   - Result correction attack: Changing confirmed score -> Old score preserved, audit logged, affected bets reconciled.

4. **Security, Isolation & RBAC Attacks (Steps 17, 18, 19, 20, 21, 22, 23):**
   - Division isolation: Requesting Division 1 data with Division 2 parameters -> Denied or empty.
   - Season isolation: Season 1 stats mixed with Season 2 -> Strict boundary enforcement.
   - IDOR: User A accessing User B's bets, wallet, analytics, or recommendations -> 403 / 404.
   - Admin RBAC: Division Admin A modifying Division B match/market -> 403 Forbidden.
   - Topic isolation: Chat A `thread_id=100` vs Chat B `thread_id=100` -> Composite key separation `(group_chat_id, topic_thread_id)`.
   - Auth red team: Tampered HMAC, expired `auth_date`, future `auth_date`, mock admin in production.

5. **Intelligence, Math & Notification Attacks (Steps 24, 25, 26, 27, 28, 29, 30):**
   - Poisson math: Ensure $0 \le P \le 1$, no NaN, no Inf, safe fallback on zero or insufficient data.
   - Value edge calculation: Strict mathematical rounding and disclaimer verification.
   - Hot match scoring: Volume-only runaway score prevention.
   - Capper leaderboard: Ensure 1-bet flukes are excluded via `MIN_LEADERBOARD_BETS >= 5`.
   - Notification flood: 100 duplicate events -> Exactly 1 notification record.

6. **Database Integrity & Legacy Compatibility (Steps 34, 35, 36, 37, 38, 39):**
   - SQL injection testing on all search, filter, and pagination parameters.
   - Pagination bounds testing (`page=-1`, `limit=0`, `limit=999999999`).
   - Fresh database migration vs existing database migration with legacy NULLs.
   - Foreign keys, orphaned record prevention, and WAL integrity.
