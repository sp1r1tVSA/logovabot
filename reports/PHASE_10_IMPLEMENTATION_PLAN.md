# PHASE 10 — LOGOVO ECONOMY, RANKING & SEASONAL PROGRESSION
## Architecture Specification & Implementation Plan

---

### Executive Summary

Phase 10 establishes a long-term competitive betting economy and seasonal progression framework for **Logovo.bet**. It introduces:
1. **Competitive Player Rating System**: Authoritative, transparent, non-stake-biased rating engine (distinct from Football Team Elo).
2. **Fair Leaderboard Engine**: Multi-scoped (`GLOBAL`, `DIVISION`, `SEASON`, `WEEKLY`, `MONTHLY`) with multiple ranking metrics, strict minimum sample thresholds (`NOT_ENOUGH_DATA`), and cached pagination.
3. **Season & Division Progression Engine**: Structured lifecycle (`ACTIVE -> QUALIFICATION -> FINAL STANDINGS -> REWARDS -> PROMOTION/RELEGATION -> NEW SEASON`) with configurable promotion/relegation zones.
4. **Separation of Concerns**:
   - Financial System: `user_wallets`, `coin_transactions`, `database.place_user_bet`, `services/settlement_engine.py`.
   - Competitive System: `services/player_rating.py`, `services/leaderboard_service.py`, `services/season_progression.py`.
   - XP, Rating, Season Points, Badges, and Ranks **NEVER mutate wallet balances directly** or bypass risk limits.
   - Financial rewards from seasons route strictly through `database.add_coins()` with `transaction_type = 'season_reward'` and immutable ledger tracking `(user_id, season_id, reward_id)`.
5. **Player Profile 2.0**: Strict partition between **Public Profile** (rating, level, season points, accuracy, ROI, streaks, achievements) and **Private Profile** (wallet balance, detailed wager history, risk limits).

---

### Baseline Status
- Pre-Phase 10 Baseline: **415 passed** in 170.91s, 0 failures, 0 errors.
- Schema Status: Clean WAL mode, `PRAGMA integrity_check` = `ok`, `PRAGMA foreign_key_check` = 0 violations.

---

### Database Schema Migration (`010_phase10_economy_and_progression`)

```sql
-- 1. Season-scoped player competitive statistics
CREATE TABLE IF NOT EXISTS season_player_stats (
    user_id INTEGER NOT NULL,
    season_id INTEGER NOT NULL,
    division_id INTEGER NOT NULL,
    rating REAL NOT NULL DEFAULT 1200.0,
    confidence REAL NOT NULL DEFAULT 350.0,
    season_points REAL NOT NULL DEFAULT 0.0,
    total_bets INTEGER NOT NULL DEFAULT 0,
    settled_bets INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    voids INTEGER NOT NULL DEFAULT 0,
    win_rate REAL NOT NULL DEFAULT 0.0,
    roi REAL NOT NULL DEFAULT 0.0,
    total_stake INTEGER NOT NULL DEFAULT 0,
    total_payout INTEGER NOT NULL DEFAULT 0,
    current_streak INTEGER NOT NULL DEFAULT 0,
    best_streak INTEGER NOT NULL DEFAULT 0,
    value_bets_hit INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'QUALIFYING', 'INACTIVE')),
    rank INTEGER DEFAULT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, season_id, division_id),
    FOREIGN KEY(user_id) REFERENCES users(telegram_id),
    FOREIGN KEY(season_id) REFERENCES seasons(id),
    FOREIGN KEY(division_id) REFERENCES divisions(id)
);

CREATE INDEX IF NOT EXISTS idx_sps_leaderboard 
ON season_player_stats(season_id, division_id, rating DESC, season_points DESC);

-- 2. Immutable season historical snapshots
CREATE TABLE IF NOT EXISTS season_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL,
    division_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    final_rank INTEGER NOT NULL,
    final_rating REAL NOT NULL,
    season_points REAL NOT NULL,
    wins INTEGER NOT NULL,
    losses INTEGER NOT NULL,
    voids INTEGER NOT NULL DEFAULT 0,
    settled_bets INTEGER NOT NULL,
    win_rate REAL NOT NULL,
    roi REAL NOT NULL,
    total_stake INTEGER NOT NULL DEFAULT 0,
    total_payout INTEGER NOT NULL DEFAULT 0,
    best_streak INTEGER NOT NULL DEFAULT 0,
    promotion_status TEXT NOT NULL CHECK(promotion_status IN ('PROMOTED', 'RELEGATED', 'STAY', 'INACTIVE')),
    rewards_json TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(season_id, division_id, user_id),
    FOREIGN KEY(season_id) REFERENCES seasons(id),
    FOREIGN KEY(division_id) REFERENCES divisions(id),
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

-- 3. Configurable rules per division & season
CREATE TABLE IF NOT EXISTS season_rules_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL,
    division_id INTEGER NOT NULL,
    promotion_slots INTEGER NOT NULL DEFAULT 3,
    relegation_slots INTEGER NOT NULL DEFAULT 3,
    min_bets_qualification INTEGER NOT NULL DEFAULT 5,
    min_matches_qualification INTEGER NOT NULL DEFAULT 3,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(season_id, division_id),
    FOREIGN KEY(season_id) REFERENCES seasons(id),
    FOREIGN KEY(division_id) REFERENCES divisions(id)
);

-- 4. Season rewards catalog
CREATE TABLE IF NOT EXISTS season_rewards_catalog (
    id TEXT PRIMARY KEY,
    season_id INTEGER,
    division_id INTEGER,
    name TEXT NOT NULL,
    reward_type TEXT NOT NULL CHECK(reward_type IN ('coins', 'xp', 'badge', 'title')),
    amount INTEGER NOT NULL DEFAULT 0,
    badge_id TEXT DEFAULT NULL,
    title TEXT DEFAULT NULL,
    criteria TEXT NOT NULL CHECK(criteria IN ('CHAMPION', 'TOP_3', 'TOP_10', 'PROMOTION', 'PARTICIPATION'))
);

-- 5. Idempotent season reward ledger
CREATE TABLE IF NOT EXISTS season_reward_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL,
    division_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    reward_id TEXT NOT NULL,
    reward_type TEXT NOT NULL,
    coins_awarded INTEGER NOT NULL DEFAULT 0,
    xp_awarded INTEGER NOT NULL DEFAULT 0,
    badge_awarded TEXT DEFAULT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'DISTRIBUTED')),
    distributed_at TIMESTAMP DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, season_id, reward_id),
    FOREIGN KEY(season_id) REFERENCES seasons(id),
    FOREIGN KEY(division_id) REFERENCES divisions(id),
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);
```

---

### Core Module Architecture

#### 1. Player Competitive Rating (`services/player_rating.py`)
- Independent from team football Elo.
- Formula updates upon settled prediction:
  $$R_{new} = R_{old} + K \cdot (S - \frac{1}{\text{odds}}) \cdot W_{conf} \cdot W_{sample}$$
  - $S = 1$ for won bet, $0$ for lost bet.
  - Void/refund has 0 rating change.
  - $K$-factor starts higher for placement bets and stabilizes as settled bets grow.
  - Excludes stake size: a 10,000 coin bet has the exact same rating impact as a 10 coin bet.
  - Returns `status = 'QUALIFYING'` if settled bets < `min_bets_qualification` (default 5).

#### 2. Leaderboard Service (`services/leaderboard_service.py`)
- Scopes: `GLOBAL`, `DIVISION`, `SEASON`, `WEEKLY`, `MONTHLY`.
- Metrics: `RATING` (primary default), `ROI`, `ACCURACY`, `VALUE`, `STREAK`, `SEASON_POINTS`.
- Fair ranking: Users below minimum settled bets are marked `is_qualified = False` and displayed with `NOT_ENOUGH_DATA` status.
- Key-scoped cache: `leaderboard:{season_id}:{division_id}:{scope}:{period}:{metric}` with automatic invalidation on bet settlement and match result correction.
- Pagination: Enforces `limit` between 1 and 50. Always attaches current user's pinned position.

#### 3. Season Progression & Finalization (`services/season_progression.py`)
- Configurable zones per division:
  - Promotion zone: Ranks $1 \dots \text{promotion\_slots}$
  - Relegation zone: Bottom $\text{relegation\_slots}$
  - Safe zone: Middle ranks
  - Inactive users (below minimum activity) are assigned `INACTIVE` and barred from promotion.
- Idempotent `finalize_season(season_id, actor_id)`:
  1. Verifies season is `active` inside transaction lock.
  2. Freezes standings.
  3. Inserts immutable rows into `season_snapshots`.
  4. Resolves promotion / relegation.
  5. Computes reward allocations into `season_reward_ledger`.
  6. Dispatches coins through `database.add_coins()` and XP through `database.add_user_xp()`.
  7. Unlocks achievements (`SEASON_CHAMPION`, `SEASON_TOP_10`, `PROMOTED`).
  8. Transitions season to `finished`.
  9. Audit log entry recorded in `admin_audit_log`.

#### 4. Streak Engine (`services/streak_engine.py`)
- Evaluates consecutive settled bets chronologically.
- `won` $\to$ increments `current_streak`, updates `best_streak`.
- `lost` $\to$ resets `current_streak` to 0.
- `refunded` / `voided` $\to$ neutral; maintains current streak.

---

### Verification Matrix & Test Strategy (13 Test Suites, 60+ Tests)

1. `tests/test_phase10_profile.py` — Public vs private profile isolation, stats accuracy.
2. `tests/test_phase10_rating.py` — Rating formula, non-stake bias, minimum sample requirement.
3. `tests/test_phase10_leaderboard.py` — Scopes, metrics, pagination limits, NOT_ENOUGH_DATA status, cache invalidation.
4. `tests/test_phase10_seasons.py` — Season lifecycle, state transitions, career vs season stats.
5. `tests/test_phase10_promotion.py` — Promotion/relegation zones, inactive exclusion.
6. `tests/test_phase10_rewards.py` — Reward ledger idempotency, wallet integration, duplicate prevention.
7. `tests/test_phase10_achievements.py` — Non-duplicable unlocks, complete Phase 10 achievement catalog.
8. `tests/test_phase10_streaks.py` — Deterministic streak calculation, void/refund neutrality.
9. `tests/test_phase10_anti_abuse.py` — Spam betting, small bet farming, qualification thresholds.
10. `tests/test_phase10_security.py` — RBAC, IDOR protection, HMAC validation, SQL injection immunity.
11. `tests/test_phase10_concurrency.py` — Concurrent season finalization, concurrent reward claims, race with settlement.
12. `tests/test_phase10_division_season.py` — Multi-division (5 divisions) and multi-season (Seasons 1–3) isolation.
13. `tests/test_phase10_api.py` — Server-authoritative endpoints, pagination boundary checks.
