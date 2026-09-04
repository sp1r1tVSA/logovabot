# LOGOVO.BET — PHASE 6 IMPLEMENTATION PLAN
# LIVE BETTING & SPORTS INTELLIGENCE PLATFORM
# Production-Grade Architecture & Execution Strategy

**Author:** Senior/Staff Backend Engineer + FinTech/Betting Systems Architect + Security Engineer + QA Lead  
**Baseline Status:** Phase 5 ACCEPTED (226/226 tests passing, 26/26 Phase 5 tests green).  
**Execution Target:** Phase 6 Live Betting & Intelligence Platform.

---

## 1. Current Architecture Review

Logovo.bet is built on a high-concurrency, asynchronous Python 3.11+ architecture:
- **Core Bot:** `python-telegram-bot` v21 async application with modular handlers (`handlers/drafts.py`, `handlers/cabinet.py`, `handlers/admin.py`, `handlers/cup.py`, `handlers/common.py`, `handlers/betting.py`).
- **Data Layer:** SQLite in Write-Ahead Logging (WAL) mode (`PRAGMA journal_mode=WAL;`), managed strictly via `with transaction() as conn:` context managers in `database.py`. All queries are parameterized with `?` placeholders.
- **Web & Mini App API:** `aiohttp` web server started via `api/server.py` on `0.0.0.0:8080`, hosting static SPA assets from `web/` and serving 99 REST endpoints.
- **Authentication & RBAC:** Telegram Mini App HMAC-SHA256 initData validation (`api/auth.py`), strict `check_user_access` (Lab mode protection), Global Admin (`config.ADMIN_IDS`), Division Admin (`division_admins` table), Player isolation.
- **Isolation Hierarchy:** Season (`season_id`) → Division (`division_id`, 5 tiers) → Round (`round_number`, `rounds`) → Match (`matches`). Topic routing uses composite keys `(group_chat_id, topic_thread_id)` via `division_topics`.

---

## 2. Existing Betting Flow & Invariants

```
Client (Mini App / Bot)
  ↓ POST /api/predictions (Bearer initData)
Auth Layer (HMAC validation, user identification)
  ↓
api/routes_predictions.py:handle_place_prediction
  ↓
database.place_user_bet(user_id, amount, selections, idempotency_key)
  ├── Lock: _bet_placement_lock (process-level concurrency guard)
  ├── DB Transaction: with transaction() as conn
  ├── Constraints:
  │     - MIN_BET = 10 🪙
  │     - MAX_BET = 50,000 🪙
  │     - MAX_PAYOUT = 500,000 🪙
  │     - Round open & deadline check (scoped by division)
  │     - Single match multi-selection prohibited in standard express
  ├── Idempotency 2.0:
  │     - Key check in user_bets
  │     - SHA-256 payload hash verification
  │     - Reused key with different payload → IDEMPOTENCY_KEY_REUSED (409)
  │     - Reused key with same payload → returns existing bet_id (200)
  ├── Odds Snapshot & Validation:
  │     - Server-authoritative odds lookup in market_selections / bet_markets
  │     - Market status check (suspended, closed, settled rejected)
  │     - Selection status check (locked, suspended, settled rejected)
  │     - Client vs Server odd check: diff > 0.001 → ODDS_CHANGED (409)
  ├── Atomic Wallet Debit:
  │     - Balance check (balance >= amount)
  │     - UPDATE user_wallets SET balance = balance - amount ...
  │     - INSERT INTO coin_transactions (type='bet_placed')
  ├── Bet & Item Persistence:
  │     - INSERT INTO user_bets (...)
  │     - INSERT INTO bet_items (odds_at_placement snapshot)
  └── Audit Logging:
        - log_betting_audit(action='place_bet', ...)
```

---

## 3. Existing Match, Market & Settlement Lifecycles

### Match Lifecycle
- Handled via `matches.status`: `('scheduled', 'pending', 'live', 'open', 'confirmed', 'completed', 'finished', 'cancelled')`.
- Attributes: `player1_score`, `player2_score`, `ht_score1`, `ht_score2`, `live_minute`, `is_live`.
- Gap: No strict state machine in Python; arbitrary status updates are possible in legacy code.

### Market Lifecycle (Phase 5 Hardened)
- `markets.status`: `('open', 'active', 'suspended', 'closed', 'settled', 'void')`.
- State transitions strictly validated by `database.transition_market_status(market_id, new_status, actor_id)`:
  - `created` → `open`/`active`
  - `open`/`active` → `suspended`, `closed`
  - `suspended` → `open`/`active`, `closed`
  - `closed` → `settled`, `void`
  - `settled` / `void` → terminal (forbidden to reopen).

### Settlement Lifecycle
- Unified in `services/settlement_engine.py` using `services/market_settler.py`:
  - `settle_match_predictions(match_id, score1, score2, match_status, ht_score1, ht_score2)`.
  - Atomically evaluates all markets, updates selection statuses (`voided`, `locked`), evaluates each `bet_item`, and settles `user_bets` (`won`, `lost`, `voided`).
  - Automatic wallet crediting (`UPDATE user_wallets SET balance = balance + payout`), transaction log (`type='bet_payout'`), and payout notification dispatch.
  - Double settlement protection: only operates on pending coupons/items.

---

## 4. Gaps & Deficiencies for Phase 6

| Area | Current State | Phase 6 Requirement |
|---|---|---|
| **Live Sports Data Provider** | Only `APISPORTS_KEY` in `config.py`; no provider abstraction or data fetcher. | Formal `SportsDataProvider` interface, `NullSportsDataProvider` (default, reports "LIVE DATA UNAVAILABLE"), `APISportsProvider`, `MockSportsDataProvider` for dev/test. **NO FAKE DATA in production**. |
| **Data Normalization** | Raw `matches` table fields only; OCR post-match events in `match_events`. | Unified `LiveMatchState`, `LiveEvent` (all 15 match events), `LiveStatistics` (possession, xG, shots, corners, etc., NULL=unavailable). |
| **Match State Machine** | Unvalidated status strings in `matches`. | Formal `LiveMatchStateMachine`: `SCHEDULED` → `PRE_MATCH` → `LIVE` ↔ `HALFTIME` → `FINISHED`. Strict transition guards and audit trail. |
| **Live Event Ingestion** | None. | Deduplicating, idempotent ingestion pipeline with composite unique constraint `(provider, provider_event_id)` and replay protection. |
| **Score Consistency** | Direct score assignment. | Sequence/timestamp ordering protection against duplicate goals, stale events, and out-of-order provider payloads. |
| **Live Odds Movement** | `odds_history` exists; no movement velocity or analytics. | Odds movement tracking (`odds_movement` table), calculation of absolute change, % change, direction, velocity, and `GET /api/odds/movers`. |
| **Live Market Suspension** | Manual admin suspension only. | Automated `LIVE_EVENT_SUSPEND_RULES` on key live events (goal, penalty, red card, VAR, halftime, finished). |
| **Match Intelligence** | Basic static text facts in `routes_matches.py`. | Formal `IntelligenceEngine`: form, H2H, scoring trends, implied probability (`1/odds`), model probability, and edge calculation. |
| **Hot Matches & Recommendations** | No ranking or recommendation engine. | Configurable weighted `hot_score` algorithm (`GET /api/matches/hot`) and explainable `RecommendationEngine` (`GET /api/recommendations`). |
| **User Betting Analytics** | `GET /api/stats/me` returns ROI=0.0 when wagered=0. | Complete analytics model with ROI=NULL on 0 stake, best/worst markets, recent form, win rate. |
| **Bettor Leaderboard** | Simple ranking by coin balance (`routes_wallet.py`). | Division & season isolated leaderboard with `MIN_LEADERBOARD_BETS` threshold, ROI %, profit, and win rate. |
| **Smart Notifications** | Basic `notifications` table without event deduplication. | Deduplicating `notification_events` with `UNIQUE(user_id, event_type, source_event_id)`, cooldowns, priority, and preferences. |
| **Background Processing** | Periodic reminders in bot job queue. | Observable, retry-safe background jobs for live sync, odds sync, intelligence refresh, and notifications. |
| **Admin Live Center** | Admin markets list only. | Live admin dashboard for match control, provider health, market suspension/resumption, and result correction audit. |
| **Mini App UI** | Basic Match Center and Lobby tabs. | High-performance LIVE Match Center tab, odds movement indicators (▲/▼), Hot Matches widget, and Bet Slip 2.0. |

---

## 5. LIVE Sports Data Provider Architecture (No Fake Data)

### Interface Definition
```python
# services/sports_provider.py

class SportsDataProvider(ABC):
    @abstractmethod
    async def get_matches(self, division_id: int | None = None, season_id: int | None = None) -> list[LiveMatchState]: ...
    @abstractmethod
    async def get_match(self, match_id: int) -> LiveMatchState | None: ...
    @abstractmethod
    async def get_live_matches(self) -> list[LiveMatchState]: ...
    @abstractmethod
    async def get_match_events(self, match_id: int) -> list[LiveEvent]: ...
    @abstractmethod
    async def get_match_statistics(self, match_id: int) -> LiveStatistics | None: ...
    @abstractmethod
    async def get_match_odds(self, match_id: int) -> list[dict]: ...
    @abstractmethod
    def get_provider_status(self) -> dict: ...
```

### Implementations
1. **`NullSportsDataProvider` (Production Default):**
   - Active when no live feed credentials are configured or feed is inactive.
   - `get_provider_status()` returns `{"status": "unavailable", "message": "LIVE DATA UNAVAILABLE"}`.
   - All query methods return empty datasets or None.
   - **Guarantees zero fake data in production.**
2. **`MockSportsDataProvider` (Dev & Test Only):**
   - Active strictly when `ENV == 'test'` or `ALLOW_DEV_MOCK_PROVIDER=1`.
   - Generates deterministic fixture events and statistics for regression testing.
3. **`APISportsProvider` (Real Provider Integration):**
   - Uses `APISPORTS_KEY` from `config.py` with exponential backoff, rate limiting, and circuit breaker.
   - Maps raw API payloads to normalized models.

---

## 6. Data Normalization & Database Schema Migrations

All migrations are **strictly additive** and backward-compatible with existing Phase 1–5 data.

### M1. `live_match_states`
Stores the normalized real-time state of a match.
```sql
CREATE TABLE IF NOT EXISTS live_match_states (
    match_id INTEGER PRIMARY KEY,
    season_id INTEGER NOT NULL DEFAULT 1,
    division_id INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'SCHEDULED',
    period TEXT NOT NULL DEFAULT 'pre_match',
    minute INTEGER,
    home_score INTEGER NOT NULL DEFAULT 0,
    away_score INTEGER NOT NULL DEFAULT 0,
    provider TEXT NOT NULL DEFAULT 'none',
    provider_match_id TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_live_states_status ON live_match_states(status);
CREATE INDEX IF NOT EXISTS idx_live_states_div_season ON live_match_states(division_id, season_id);
```

### M2. `live_events`
Stores fine-grained optical / provider match events with strict deduplication.
*(Note: Distinct from legacy `match_events`, preserving OCR backwards-compatibility!)*
```sql
CREATE TABLE IF NOT EXISTS live_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    provider_event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    minute INTEGER NOT NULL,
    added_time INTEGER,
    team_id INTEGER,
    team_name TEXT,
    player_id INTEGER,
    player_name TEXT,
    payload TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE,
    UNIQUE(provider, provider_event_id)
);
CREATE INDEX IF NOT EXISTS idx_live_events_match ON live_events(match_id, minute);
```

### M3. `live_statistics`
Stores real-time match statistics. **Rule: `NULL` = unavailable, NEVER `0`.**
```sql
CREATE TABLE IF NOT EXISTS live_statistics (
    match_id INTEGER PRIMARY KEY,
    possession_home REAL,
    possession_away REAL,
    shots_home INTEGER,
    shots_away INTEGER,
    shots_on_target_home INTEGER,
    shots_on_target_away INTEGER,
    corners_home INTEGER,
    corners_away INTEGER,
    fouls_home INTEGER,
    fouls_away INTEGER,
    offsides_home INTEGER,
    offsides_away INTEGER,
    yellow_cards_home INTEGER,
    yellow_cards_away INTEGER,
    red_cards_home INTEGER,
    red_cards_away INTEGER,
    dangerous_attacks_home INTEGER,
    dangerous_attacks_away INTEGER,
    attacks_home INTEGER,
    attacks_away INTEGER,
    passes_home INTEGER,
    passes_away INTEGER,
    pass_accuracy_home REAL,
    pass_accuracy_away REAL,
    xg_home REAL,
    xg_away REAL,
    saves_home INTEGER,
    saves_away INTEGER,
    substitutions_home INTEGER,
    substitutions_away INTEGER,
    provider TEXT NOT NULL DEFAULT 'none',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE
);
```

### M4. `odds_movement`
Tracks odds velocity, percentage changes, and direction.
```sql
CREATE TABLE IF NOT EXISTS odds_movement (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    selection_id INTEGER NOT NULL,
    market_id INTEGER NOT NULL,
    match_id INTEGER NOT NULL,
    old_odds REAL NOT NULL,
    new_odds REAL NOT NULL,
    pct_change REAL NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('up', 'down', 'neutral')),
    velocity REAL NOT NULL DEFAULT 0.0,
    reason TEXT,
    source TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(selection_id) REFERENCES market_selections(id) ON DELETE CASCADE,
    FOREIGN KEY(market_id) REFERENCES markets(id) ON DELETE CASCADE,
    FOREIGN KEY(match_id) REFERENCES matches(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_odds_mov_sel ON odds_movement(selection_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_odds_mov_match ON odds_movement(match_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_odds_mov_created ON odds_movement(created_at DESC);
```

### M5. `notification_events`
Deduplicating notification dispatch pipeline.
```sql
CREATE TABLE IF NOT EXISTS notification_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    link TEXT,
    priority TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP,
    UNIQUE(user_id, event_type, source_event_id),
    FOREIGN KEY(user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_notif_events_user ON notification_events(user_id, status, created_at DESC);
```

### M6. `provider_sync_state`
Tracks provider health, last sync timestamps, and errors.
```sql
CREATE TABLE IF NOT EXISTS provider_sync_state (
    provider TEXT PRIMARY KEY,
    last_sync_at TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'idle',
    error_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 7. Match State Machine & Ingestion Pipeline

### State Machine Transition Rules
- `SCHEDULED` → `PRE_MATCH`, `POSTPONED`, `CANCELLED`
- `PRE_MATCH` → `LIVE`, `POSTPONED`, `CANCELLED`
- `LIVE` → `HALFTIME`, `SUSPENDED`, `FINISHED`, `ABANDONED`
- `HALFTIME` → `LIVE`, `ABANDONED`
- `SUSPENDED` → `LIVE`, `ABANDONED`
- `POSTPONED` → `SCHEDULED`, `CANCELLED`
- `FINISHED`, `CANCELLED`, `ABANDONED` → **Terminal states**.
  - Any transition out of `FINISHED` is prohibited without explicit Admin Result Correction (`force_reopen_match` with audit logging).

### Ingestion & Score Consistency
```
Incoming Event (provider, provider_event_id, minute, event_type, score, payload)
  ↓
1. Deduplication: Check UNIQUE(provider, provider_event_id). If exists, return (idempotent no-op).
2. Out-of-order check: If event.minute < state.minute - 5 and not a correction event, flag as late/warning.
3. State Transition: Apply state machine transition if event_type in (match_started, halftime, second_half, match_finished).
4. Score Consistency: Update home_score and away_score only if sequence / version is monotonic.
5. Market Suspension: Evaluate LIVE_EVENT_SUSPEND_RULES (goal, penalty, red card, VAR -> auto-suspend markets).
6. Smart Notifications: Generate notification_events with UNIQUE dedup keys.
7. Commit: Atomically persist event, state, stats, and audit log.
```

---

## 8. Live Odds & Market Safety

### Live Market Suspension Rules
```python
LIVE_EVENT_SUSPEND_RULES = {
    "goal": {"action": "suspend_all", "delay_sec": 0, "reason": "Goal scored"},
    "penalty": {"action": "suspend_all", "delay_sec": 0, "reason": "Penalty awarded"},
    "VAR": {"action": "suspend_all", "delay_sec": 0, "reason": "VAR review in progress"},
    "red_card": {"action": "suspend_types", "types": ["1x2", "totals", "handicap"], "reason": "Red card issued"},
    "halftime": {"action": "suspend_types", "types": ["1st_half"], "reason": "Halftime break"},
    "match_finished": {"action": "close_all", "reason": "Match completed"}
}
```

### Safety Guards in `place_user_bet`
- If market status is `suspended`, `closed`, or `settled` → reject immediately.
- If match status is `finished` or `completed` → reject live betting immediately.
- If odds changed beyond drift tolerance → return `ODDS_CHANGED` with server values.

---

## 9. Intelligence Engine, Odds Movers & Recommendations

### Intelligence Engine (`services/intelligence_engine.py`)
1. **Form & Momentum:** 5-match form string (`W-W-D-L-W`), goals scored/conceded, clean sheet frequency.
2. **H2H Analysis:** Historical meetings between the two clubs, win rates, goal averages.
3. **Probabilities & Value Edge (`ValueAnalyzer`):**
   - `implied_probability = round((1.0 / decimal_odds) * 100, 2)`
   - `model_probability`: Derived from team scoring rates and Poisson distribution.
   - `edge = round(model_probability - implied_probability, 2)`
   - UI disclaimer: Predictions and value edges are analytical indicators, NOT guarantees of outcome.
4. **Data-Driven Insights:** 3–5 verifiable trends (e.g., "Порту забивает в 4 матчах подряд", "ТБ 2.5 пробивался в 70% личных встреч").

### Hot Matches (`services/recommendation_engine.py`)
Configurable weighted score:
$$\text{HotScore} = 40 \times \text{is\_live} + 20 \times \text{odds\_movement} + 20 \times \text{betting\_volume} + 10 \times \text{h2h\_rivalry} + 10 \times \text{start\_proximity}$$
Endpoint: `GET /api/matches/hot`.

### Odds Movers (`services/odds_movers.py`)
Endpoint `GET /api/odds/movers`:
- **Biggest Drops:** Top 10 selections with the steepest negative percentage movement.
- **Biggest Rises:** Top 10 selections with the highest positive odds movement.
- **Fastest Movement:** Selections with the highest velocity ($\frac{|\Delta \text{odds}|}{\Delta t}$) in the last 24h.
- **Suspended Markets:** Currently locked/suspended markets.

### Personalized Recommendations
Considers user's active division, favorite teams, betting history (preferred markets), and active tournaments. Generates transparent reasoning ("Потому что вы следите за Дивизионом 1 и часто ставите на Тотал Больше").

---

## 10. Bettor Analytics & Division Leaderboards

### User Analytics (`GET /api/profile/analytics`)
- Settled bets, wins, losses, voids, total staked, total payout, net profit.
- **Strict ROI Formula:** $\text{ROI} = \frac{\text{net\_profit}}{\text{total\_staked}} \times 100$. If `total_staked == 0`, return `NULL` (`None` in JSON), never `0.0`.
- Win rate percentage: $\frac{\text{wins}}{\text{settled\_bets}} \times 100$. If `settled_bets == 0`, return `NULL`.
- Best market, worst market, favorite market, recent form array.

### Division & Season Isolated Leaderboard
- `GET /api/leaderboard`: Global leaderboard (with optional `season_id` filter).
- `GET /api/leaderboard/division/{division_id}`: Strict division-scoped leaderboard.
- **Minimum Bets Threshold:** `MIN_LEADERBOARD_BETS = 5` (configurable). Prevents 1-bet users from dominating ROI rankings.
- Ranks by net profit, ROI, and win rate.

---

## 11. Smart Notifications Pipeline

- Dispatched via `notification_events` table.
- Composite deduplication: `UNIQUE(user_id, event_type, source_event_id)`.
- User preferences: Respected via `user_notification_settings`.
- Cooldown timer to prevent notification floods on fast-moving games.
- Supported events: `MATCH_STARTED`, `GOAL`, `RED_CARD`, `HALFTIME`, `MATCH_FINISHED`, `ODDS_MOVEMENT`, `MARKET_SUSPENDED`, `BET_SETTLED`, `HOT_MATCH`.

---

## 12. Background Processing Architecture

Implemented via safe async tasks / JobQueue:
1. `live_sync_job`: Polls active live data provider every 15–30s.
2. `odds_sync_job`: Monitors market odds updates and logs movement.
3. `intelligence_refresh_job`: Updates hot matches scoring and cached trends every 5m.
4. `notification_dispatch_job`: Batches and sends pending notifications with rate-limit backoff.
5. `data_cleanup_job`: Prunes raw provider logs older than retention limits (financial bets and audit logs are never deleted).

---

## 13. Security, RBAC & Data Isolation

- **HMAC Authentication:** Required on all private endpoints. No mock admin in production (`ALLOW_DEV_AUTH_BYPASS` strictly gated).
- **IDOR Protection:** All user endpoints (`/api/profile/analytics`, `/api/predictions`, `/api/recommendations`) bind exclusively to the authenticated Telegram ID from `initData`.
- **Division RBAC:** Division admins can only suspend/manage markets and trigger corrections within their assigned division and season.
- **Audit Trail:** All destructive admin actions (suspend, resume, void, result correction) require confirmation, explicit reason, and log to `bet_audit_log`.

---

## 14. API Endpoints Map (Phase 6)

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/live` | Optional | List active live matches with score and minute |
| GET | `/api/live/{id}` | Optional | Detailed live match state & provider status |
| GET | `/api/live/{id}/events` | Optional | Real-time event timeline (goals, cards, VAR) |
| GET | `/api/live/{id}/stats` | Optional | Real-time match statistics (NULL = unavailable) |
| GET | `/api/live/{id}/markets` | Optional | Active and suspended live markets & odds |
| GET | `/api/live/{id}/intelligence` | Optional | Match momentum, form, H2H, implied vs model odds |
| GET | `/api/odds/movers` | Optional | Biggest drops, rises, fastest velocity, suspended |
| GET | `/api/matches/hot` | Optional | Hot matches ranked by weighted intensity score |
| GET | `/api/recommendations` | Required | Personalized, explainable betting suggestions |
| GET | `/api/profile/analytics` | Required | Comprehensive bettor analytics (ROI=NULL on 0 stake) |
| GET | `/api/leaderboard` | Optional | Season-scoped capper leaderboard (min bets enforced) |
| GET | `/api/leaderboard/division/{id}` | Optional | Division-isolated capper leaderboard |
| GET | `/api/admin/live/dashboard` | Admin | Admin Live Center overview & provider health |
| POST | `/api/admin/live/markets/{id}/suspend` | Admin | Manual market suspension with reason |
| POST | `/api/admin/live/markets/{id}/resume` | Admin | Resume suspended market |
| POST | `/api/admin/live/match/{id}/correct-result` | Admin | Result correction with audit log & safe recalc |

---

## 15. Mini App Frontend Architecture

- **Mobile Viewport:** Responsive design tuned for 320px–430px.
- **Navigation:**
  - Added visual 🔥 **LIVE** tab / pill with real-time pulse animation.
  - Home 2.0: Hot Matches carousel, Live Now widget, Odds Movers banner, Recommendations.
- **Match Center 2.0:**
  - Header with live badge, minute, period, score.
  - Event Timeline: goals, yellow/red cards, substitutions, VAR checks.
  - Statistics Bar Chart: possession, shots, corners (cleanly hides unavailable stats).
  - Live Markets Grid with dynamic odds movement indicators (▲ green / ▼ red).
- **Bet Slip 2.0:**
  - Disabled state for suspended/closed markets with informative tooltip.
  - Express combined odds calculation.
  - Odds changed inline modal acceptance.
- **Performance & Polling:** Efficient 8–12s polling interval for active live screens; zero full-page reloads.

---

## 16. Step-by-Step Execution Plan (Phase 6A → 6L)

```
PHASE 6A: Audit & Implementation Plan (Completed)
  ├── Full repo audit, baseline test verification (226 passed)
  ├── PHASE_6_IMPLEMENTATION_PLAN.md creation
  └── User review & approval

PHASE 6B: Provider Abstraction & Data Normalization
  ├── services/sports_provider.py (SportsDataProvider, NullProvider, MockProvider)
  ├── Data models: LiveMatchState, LiveEvent, LiveStatistics
  └── database.py schema migrations (M1–M6)

PHASE 6C: Live Event Ingestion & State Machine
  ├── services/live_state_machine.py (Strict transitions, validation, audit)
  ├── services/live_ingestion.py (Deduplication, sequence ordering, consistency)
  └── Unit tests for ingestion, duplicates, replay, illegal transitions

PHASE 6D: Live Odds, Market Suspension & Movement
  ├── services/market_safety.py (LIVE_EVENT_SUSPEND_RULES)
  ├── services/odds_movers.py (Movement velocity, drops, rises)
  ├── Extend database.place_user_bet with live market guards
  └── Unit tests for odds movement, suspension rules, and odds snapshots

PHASE 6E: Live Match Center & API Routes
  ├── api/routes_live.py (Endpoints: /api/live, events, stats, markets, intelligence)
  ├── Register routes in api/server.py
  └── Integration tests for all live endpoints

PHASE 6F: Intelligence Engine
  ├── services/intelligence_engine.py (Form, H2H, trends, implied vs model prob, edge)
  └── Analytical unit tests

PHASE 6G: Recommendations & Hot Matches
  ├── services/recommendation_engine.py (Hot scoring, personalized recommendations)
  ├── API routes: /api/matches/hot, /api/recommendations
  └── Unit tests for ranking, weights, and privacy boundaries

PHASE 6H: Bettor Analytics & Division Leaderboards
  ├── services/analytics_service.py (ROI with NULL handling, win rate, best/worst)
  ├── API routes: /api/profile/analytics, /api/leaderboard, /api/leaderboard/division/{id}
  └── Unit tests for division/season isolation and min bets filtering

PHASE 6I: Smart Notifications
  ├── services/notification_service.py (Deduplicated dispatch, cooldowns, preferences)
  └── Unit tests for notification idempotency

PHASE 6J: Admin Live Center & Background Processing
  ├── api/routes_admin_live.py (Live dashboard, market suspension, result correction)
  ├── Background jobs integration in main.py / api/server.py
  └── RBAC & safety tests for admin actions

PHASE 6K: Mini App Frontend Enhancements
  ├── web/index.html, web/js/app.js, web/js/ui.js, web/js/api.js
  ├── LIVE tab, Match Center 2.0 timeline & stats, Odds Movers widget
  └── Bet slip suspended state handling

PHASE 6L: Comprehensive Verification & Final Report
  ├── Run full test suite (baseline 226 + Phase 6 tests)
  ├── Migration tests on fresh and existing DBs
  ├── Git diff and security audit
  └── Generate PHASE_6_FINAL_REPORT.md
```

---

## 17. Test Strategy & Acceptance Matrix

We will create dedicated test suites:
1. `tests/test_phase6_provider.py`: Provider interface, NullProvider fallback, MockProvider ingestion.
2. `tests/test_phase6_live.py`: Match state machine, event ingestion idempotency, duplicate/stale event rejection, score consistency.
3. `tests/test_phase6_odds.py`: Market suspension rules, odds movement velocity, odds change 409 rejection.
4. `tests/test_phase6_intelligence.py`: Implied vs model probability, value edge, hot score weights, recommendations.
5. `tests/test_phase6_analytics.py`: User ROI (NULL on 0 stake), division leaderboard isolation, min bets threshold.
6. `tests/test_phase6_notifications.py`: Notification idempotency `(user_id, event_type, source_event_id)` and preferences.
7. `tests/test_phase6_security.py`: HMAC auth, IDOR protection, division admin RBAC, result correction audit.

**Acceptance Criteria:**
- 100% of existing 226 baseline tests pass without modifications or weakened assertions.
- All Phase 6 test suites pass cleanly.
- Fresh DB initialization and existing DB migration execute with zero errors.

---

## 18. Critical Risks & Mitigations

| Risk | Impact | Mitigation Strategy |
|---|---|---|
| **Fake Live Data Leakage** | High (Reputation & Legal) | `NullSportsDataProvider` is default in production; returns explicit `"LIVE DATA UNAVAILABLE"`. Mocks strictly isolated to `ENV == 'test'`. |
| **Double Bet Settlement** | Critical (Financial loss) | Reuse existing `settlement_engine.py` with `WHERE settled_at IS NULL` atomic guards. No duplicate settlement engines. |
| **Duplicate Event Ingestion** | High (Corrupted scores) | Database-level `UNIQUE(provider, provider_event_id)` constraint + sequence number verification. |
| **Market Status Race Conditions** | High (Bets on finished/suspended games) | `_bet_placement_lock` + atomic SQLite transaction checks market status inside the transaction immediately before wallet debit. |
| **Division / Season Leakage** | Medium (Stats corruption) | All leaderboard, analytics, and recommendation queries require explicit `division_id` and `season_id` parameterization. |
| **Notification Floods** | Medium (Bad UX / API limits) | `notification_events` unique constraint + per-user cooldown timers. |

---

## 19. Untouchable Core Components

The following systems are battle-tested and **must NOT be replaced, rewritten, or duplicated**:
1. `database.transaction()` context manager and SQLite WAL settings.
2. Core wallet deduction and crediting in `place_user_bet` and `settlement_engine.py`.
3. `services/settlement_engine.py` and `services/market_settler.py` rule evaluation logic.
4. Topic assignment and routing in `services/topic_cache.py` with composite `(group_chat_id, topic_thread_id)`.
5. OCR screenshot recognition in `services/ai/ai_recognizer.py` and legacy post-match `match_events`.
6. Existing Phase 1–5 test assertions in `tests/`.
