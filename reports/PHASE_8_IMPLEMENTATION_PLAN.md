# PHASE 8 IMPLEMENTATION PLAN
## Production Sports Data Provider Integration & Live AI

---

## 1. Executive Summary & Objective
The goal of Phase 8 is to integrate a production-ready real external sports data provider into **LogovoBot / Logovo.bet** through the existing provider abstraction (`SportsDataProvider`), connecting live football fixtures, real-time events, match statistics, lineups, injuries, and live odds to the Phase 7 AI intelligence pipeline and Telegram Mini App.

### Strict Non-Negotiable Invariants
1. **Financial Read-Only AI**: AI and live sports data pipelines NEVER debit coins, credit coins, place bets, void bets, modify wallets, or trigger settlements.
2. **Authoritative Financial Core**: Betting Engine, Wallet, and Settlement remain the single source of truth for all coin movements.
3. **Zero Fake Data**: If live data, xG, lineups, or statistics are unavailable, the system explicitly reports `available: false`, `xg_available: false`, or `None`. Zero synthetic hallucinated stats.
4. **Production Fallback**: When `SPORTS_API_KEY` is not provisioned or circuit breaker trips, the system gracefully operates on `NullSportsDataProvider` ("LIVE DATA UNAVAILABLE").
5. **No Data Leakage**: In-play and pre-match predictions strictly evaluate data timestamped $\le T$.
6. **No Breaking Regressions**: All 330 baseline tests must remain 100% passing.

---

## 2. Architecture & Component Map

```
                ┌──────────────────────────────────────────────┐
                │          Real Sports API (e.g. API-Sports)   │
                └──────────────────────┬───────────────────────┘
                                       │ HTTP / JSON
                                       ▼
                ┌──────────────────────────────────────────────┐
                │     ProviderRateLimiter & Circuit Breaker    │
                │        (60 RPM, Exponential Backoff)        │
                └──────────────────────┬───────────────────────┘
                                       │
                                       ▼
                ┌──────────────────────────────────────────────┐
                │             ProviderCacheLayer               │
                │         (sports:{provider}:{key})            │
                └──────────────────────┬───────────────────────┘
                                       │
                                       ▼
                ┌──────────────────────────────────────────────┐
                │     Concrete Provider Adapter (APISports)    │
                └──────────────────────┬───────────────────────┘
                                       │ Raw Payload
                                       ▼
                ┌──────────────────────────────────────────────┐
                │            Normalization Layer               │
                │  ProviderMatch, ProviderEvent, ProviderStats │
                │  ProviderLineup, ProviderInjury, ProviderOdds│
                └──────────────────────┬───────────────────────┘
                                       │
                                       ▼
                ┌──────────────────────────────────────────────┐
                │       Idempotent Live Ingestion Pipeline     │
                │     (live_match_states, live_events, stats)  │
                └──────────────────────┬───────────────────────┘
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            │                                                     │
            ▼                                                     ▼
┌──────────────────────┐                              ┌──────────────────────┐
│  AI Feature Engine   │                              │ Live Odds Sync Engine│
│ (Real stats, xG, H2H)│                              │(Markets, OddsMovers) │
└───────────┬──────────┘                              └───────────┬──────────┘
            ▼                                                     ▼
┌──────────────────────┐                              ┌──────────────────────┐
│ Ensemble Prediction  │                              │ Value Radar & Movers │
│(Poisson + Elo + Form)│                              │(Real Edge Detection) │
└───────────┬──────────┘                              └───────────┬──────────┘
            │                                                     │
            └──────────────────────────┬──────────────────────────┘
                                       │
                                       ▼
                ┌──────────────────────────────────────────────┐
                │    Telegram Mini App & Intelligence REST API │
                │ (Server Authoritative, Freshness Badges)     │
                └──────────────────────────────────────────────┘
```

---

## 3. Provider-Neutral Data Models
Define canonical dataclasses in `services/sports/models.py` (and re-export in `services/sports_provider.py`):
1. `ProviderTeam`: `team_id`, `name`, `code`, `logo`, `is_home`.
2. `ProviderMatch`: `match_id`, `provider`, `home_team`, `away_team`, `status`, `period`, `minute`, `home_score`, `away_score`, `start_time`, `league_id`, `league_name`, `round`, `venue`, `updated_at`.
3. `ProviderEvent`: `provider_event_id`, `match_id`, `provider`, `event_type`, `minute`, `added_time`, `team_id`, `team_name`, `player_id`, `player_name`, `detail`, `payload`.
4. `ProviderStatistics`: `match_id`, `provider`, `possession_home`, `possession_away`, `shots_home`, `shots_away`, `shots_on_target_home`, `shots_on_target_away`, `corners_home`, `corners_away`, `fouls_home`, `fouls_away`, `offsides_home`, `offsides_away`, `yellow_cards_home`, `yellow_cards_away`, `red_cards_home`, `red_cards_away`, `xg_home`, `xg_away`, `saves_home`, `saves_away`.
5. `ProviderLineup`: `match_id`, `provider`, `team_id`, `team_name`, `formation`, `starting_xi`, `substitutes`, `coach_name`.
6. `ProviderInjury`: `player_id`, `player_name`, `team_id`, `team_name`, `injury_type`, `status`, `fixture_id`, `last_update`.
7. `ProviderOdds`: `match_id`, `provider`, `bookmaker_id`, `bookmaker_name`, `market_key`, `market_name`, `selections`, `updated_at`.

---

## 4. Configuration & Secrets Management
Add configuration options in `config.py`:
- `SPORTS_PROVIDER`: Auto-detection (`auto`, `api_sports`, `null`, `mock`).
- `SPORTS_API_KEY`: Sourced from environment (`SPORTS_API_KEY` or `APISPORTS_KEY`). Never logged, never returned in HTTP responses.
- `SPORTS_API_BASE_URL`: Base URL (default `https://v3.football.api-sports.io`).
- `SPORTS_TIMEOUT_SECONDS`: Request timeout (default `10.0`).
- `SPORTS_CACHE_TTL_SECONDS`: Cache TTL (default `30` seconds).
- `SPORTS_LIVE_POLL_SECONDS`: Polling frequency (default `15` seconds).
- `SPORTS_MAX_RETRIES`: Retry attempts (default `3`).
- `SPORTS_RATE_LIMIT_RPM`: Rate limit (default `60` requests/minute).
- `LIVE_DATA_STALE_AFTER_SECONDS`: Threshold for marking data stale (default `120` seconds).
- `LIVE_DATA_EXPIRED_AFTER_SECONDS`: Threshold for marking data expired (default `300` seconds).

---

## 5. Rate Limiting, Circuit Breaker & Caching
1. **`ProviderRateLimiter`**:
   - Sliding window / token bucket algorithm limiting requests to `SPORTS_RATE_LIMIT_RPM`.
   - Parses HTTP 429 and `Retry-After` header to back off politely.
2. **`ProviderCircuitBreaker`**:
   - Trips to `OPEN` after 5 consecutive failures.
   - Enters `HALF_OPEN` probe mode after 60-second cooldown.
   - Automatically resets to `CLOSED` upon successful probe.
3. **`ProviderCache`**:
   - Namespaced keys: `sports:{provider}:{endpoint}:{query_hash}`.
   - Configurable TTL for live fixtures (15s), statistics (30s), standings (300s), lineups (300s).
   - Prevents cross-provider cache poisoning.

---

## 6. Live Ingestion & Score Consistency Pipeline
1. **Idempotency**:
   - Strict `UNIQUE(provider, provider_event_id)` in `live_events`. Duplicate events return `status: duplicate` without state mutation.
2. **Monotonic Score & Finished Match Protection**:
   - Live events only mutate open/live matches.
   - Once a match transitions to `FINISHED`, `CANCELLED`, or `ABANDONED`, non-correction events are rejected.
3. **Automated Market Suspension**:
   - Scoring events (goal, penalty, VAR) immediately trigger market suspension (`status = 'suspended'`) and write audit logs.

---

## 7. Odds Synchronization & Value Radar Integration
1. **Validation**:
   - Ingested odds must be finite, numeric, $> 1.0$, and non-NaN.
2. **Odds Movement Recording**:
   - Computes percentage change, direction, and velocity relative to the previous odds snapshot.
   - Detects abnormal line drops/rises and triggers anomaly flags.
3. **Value Radar**:
   - Compares live model probabilities against provider market true implied probabilities, surfacing true analytical value edges.

---

## 8. Stale Data & Freshness Indicators
1. **Freshness Tracking**:
   - Every live state record tracks `last_updated_at`.
   - Classification:
     - `FRESH`: updated $\le 120\text{s}$ ago.
     - `STALE`: updated $121\text{s} - 300\text{s}$ ago.
     - `EXPIRED`: updated $> 300\text{s}$ ago.
2. **AI & UI Behavior**:
   - Mini App displays badges: 🟢 `LIVE DATA FRESH`, 🟡 `DATA DELAYED`, 🔴 `LIVE DATA UNAVAILABLE`.
   - When data is stale, AI confidence scores are discounted, and disclaimer warnings are surfaced.

---

## 9. Admin Health & Observability Endpoint
- Endpoint: `GET /api/admin/sports/health`.
- Access Control: Global Admin only (`is_admin(user_id)`). Division admins and players receive 403.
- Response Data: Provider name, connection status, circuit breaker state, latency, total requests, total errors, stale matches count.
- Security Invariant: API keys are masked or completely omitted from responses.

---

## 10. Database Schema Additions (Idempotent)
Additive, non-destructive SQLite tables:
1. `sports_providers`: Configuration and health state of registered providers.
2. `provider_matches`: Mapping between external provider match IDs and internal match IDs.
3. `provider_sync_log`: Request latency, HTTP status codes, and error telemetry.

---

## 11. Testing & Verification Matrix
Create 8 dedicated test suites in `tests/`:
- `test_phase8_sports_provider.py`: Provider abstraction, model normalization, Null/Mock/APISports adapter unit tests.
- `test_phase8_live_pipeline.py`: Ingestion pipeline, score consistency, deduplication, event idempotency.
- `test_phase8_provider_security.py`: API key non-disclosure, admin health RBAC, input sanitization.
- `test_phase8_stale_data.py`: Stale/expired thresholds, freshness degradation, UI indicator logic.
- `test_phase8_odds_sync.py`: Live odds synchronization, NaN/Inf rejection, market engine integrity.
- `test_phase8_data_leakage.py`: Temporal isolation, pre-match and in-play prediction time boundaries.
- `test_phase8_failover.py`: Circuit breaker tripping, degraded mode, graceful fallback to NullSportsDataProvider.
- `test_phase8_division_season.py`: Multi-division and multi-season scoping of provider fixtures and ratings.

---

## 12. Full Regression Plan
- Run `python -m pytest tests/ -q` to guarantee all 330 baseline tests pass plus new Phase 8 suites.
- Verify `PRAGMA integrity_check` = `ok` and `PRAGMA foreign_key_check` = `0 violations`.
