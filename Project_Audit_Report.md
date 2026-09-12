# Project Audit Report — Logovo.bet Telegram Bot

**Auditor:** Lead Systems Architect & Code Auditor
**Date:** 2026-09-07
**Commit:** `c8abe69` (branch `main`)
**Scope:** 179 tracked Python modules, 59 SQLite tables, 78 test files
**Method:** ast-grep code graph (rebuilt this session), pyflakes 3.4.0, custom AST visitors,
ripgrep pattern sweeps, targeted file reads, empirical reproduction of suspected runtime errors.

> **Status:** the audit itself (§1–§4) was analysis-only. The action plan in §5 was
> subsequently **approved and partially executed** — all of Priority 0 and part of
> Priority 1 have landed. See **§6 Execution Log** for what changed, what it broke, and
> what remains.

---

## 1. Executive Summary

### Overall Health Score: **6.5 / 10** — *Structurally sound, operationally fragile*

The project has an unusually disciplined **data layer** and an unusually undisciplined
**concurrency layer**. The Season 1 → Season 2 multi-division migration was executed
*additively* — new division-aware code paths were added alongside the old global ones, but
the old paths were never removed and, critically, they remain the **default** in several
places. The result is a codebase where the new architecture works when explicitly invoked
and silently degrades to Season 1 behaviour when it is not.

**What is genuinely excellent:**
- **Zero SQL injection risk.** Every one of the ~1,400 queries is parameterized. All
  f-string SQL sites were inspected individually; each interpolates only `?` placeholders
  or identifiers from hardcoded whitelists.
- **Zero bare `except:`** across 502 exception handlers.
- The re-entrant `transaction()` context manager is a genuinely good piece of engineering
  and is respected essentially everywhere.
- 548 tests pass.

**The four main risks, in order:**

| # | Risk | Impact |
|---|---|---|
| 1 | **Four latent `NameError`s in production code paths** | The AI chat feature is **completely non-functional**. Guest match confirmation **always** falls back to admin. Bet risk evaluation crashes on any round with a deadline. Club schedule crashes when unplayed matches exist. |
| 2 | **96 synchronous DB I/O calls on the event loop** | 63 of them are in `api/`, which shares the bot's event loop. Every Mini App request stalls Telegram message handling. |
| 3 | **Season 1 defaults still active** | `division_id=None` / `season_id=None` defaults mean unscoped queries return cross-division data. The `/table` text command renders a **global** 16-team table. |
| 4 | **144 silent `except X: pass` handlers** | Failures are invisible. Risk #1 went undetected precisely because one of the four `NameError`s is swallowed by such a handler. |

**Why the test suite did not catch any of this:** all four `NameError`s live on branches
the tests do not execute (conditional text assembly, opponent-notification paths,
deadline-present rounds, pending-fixture lists). 548 green tests is a genuine asset, but
coverage is structural, not behavioural.

---

## 2. 🔴 Critical Violations

### 2.1 Broken Logic — Confirmed Runtime `NameError`s

These are not style issues. Each is a guaranteed exception on a reachable code path.

---

#### 🔴 C-1 — AI chat is entirely dead
**File:** `handlers/chat.py:250` · **Function:** `handle_ai_chat()` (starts line 17)

```python
context_data = (            # line 234 — unconditional assignment
    ...
    f"{cup_info_text}\n\n"  # line 250 — cup_info_text is NEVER assigned in this file
    ...
)
```

`cup_info_text` is not defined anywhere in the module — not as a local, parameter, import,
or global. The assignment at line 234 is **unconditional** and sits **outside** every
`try` block in the function (the only `try` blocks are at lines 37–41, 49–51, 71–73).

**Impact:** every invocation of the AI chat handler raises `NameError` before reaching
Gemini. Since this handler is registered as the **catch-all text/voice `MessageHandler`**
(the last handler in `register_all_handlers()`), the entire «Темшик» persona feature is
non-functional. Users get no reply at all.

**Severity:** Critical — a headline feature is 100% broken in production.

---

#### 🔴 C-2 — Guest match confirmation silently always escalates to admin
**File:** `handlers/cabinet.py:2965` · **Function:** `submit_report_to_guest()` (starts line 2855)

```python
try:
    if photo_id:                    # line 2965 — photo_id is never assigned in this scope
        await context.bot.send_photo(chat_id=opp_id, photo=photo_id, ...)
    else:
        await context.bot.send_message(chat_id=opp_id, text=text, ...)
    await safe_edit_or_reply(...)
except Exception as e:              # line 2971 — swallows the NameError
    logger.warning(f"Could not send match confirmation to opponent {opp_id}: {e}")
    await query.answer("⚠️ Не удалось связаться с соперником. Отправляем администратору.", show_alert=True)
    await submit_report_to_admin(update, context)
```

The name exists elsewhere in the file as `payload.get("photo_id")` (e.g. line 2890) but is
never bound in this function. The `NameError` is caught by the broad handler at line 2971,
which is **indistinguishable from a real Telegram delivery failure**.

**Impact:** the opponent is *never* notified. Every guest report is misrouted to an admin,
and the log line blames a network problem. This is the single best illustration of why
risk #4 (silent exception handlers) matters — the bug is invisible by construction.

**Severity:** Critical — core workflow broken, and masked.

---

#### 🔴 C-3 — Missing `datetime` import crashes bet risk evaluation
**File:** `services/risk_engine.py:203, 207` · **Function:** `evaluate_bet()`

Module imports (lines 14–21) are: `math`, `logging`, `dataclasses`, `typing`, `database`,
`betting_limits`, `exposure_service`, `risk_alerts`. **`datetime` is absent.**

```python
datetime.datetime.strptime(...)   # line 203
datetime.datetime.now()           # line 207
```

**Impact:** `NameError` whenever `evaluate_bet()` reaches the deadline branch — i.e. for
any round that has a `deadline` set, which is the normal case. Bets on live rounds fail
risk evaluation.

**Severity:** Critical — money-adjacent (virtual currency) logic fails open or closed
unpredictably depending on the caller's exception handling.

---

#### 🔴 C-4 — Python 3 comprehension scoping bug in club schedule
**File:** `database.py:5003` · **Function:** `get_club_schedule_and_results()` (starts line 4872)

```python
played_league  = [l for l in league_items if l["is_completed"]]
played_league.sort(key=lambda x: -x["round_number"])
pending_league = [l for l in league_items if not l["is_completed"]]
pending_league.sort(key=lambda x: l["round_number"])   # line 5003 — `l` does not leak
```

In Python 3 the comprehension variable `l` does not escape into the enclosing scope. The
lambda closes over a name that is never bound.

Confirmed two independent ways: `pyflakes` reports
`database.py:5003:43: undefined name 'l'`, and an isolated reproduction printed
`CONFIRMED NameError: name 'l' is not defined`.

**Impact:** the exception fires only when `pending_league` is **non-empty** — i.e. exactly
when a club still has unplayed fixtures, which is the normal mid-season state. Called from
`handlers/cabinet.py:520`.

**Severity:** Critical — user-facing club card breaks mid-season.

> Note the lambda is also *semantically* wrong even if it ran: the sort key should be
> `x["round_number"]`, and the intent is presumably ascending order for pending fixtures
> (contrast the descending `-x["round_number"]` used for played ones).

---

### 2.2 Event Loop Blocking

The Mini App API is started with `await start_api_server_background(...)` from
`post_init` (`main.py:24`), which uses `web.AppRunner` + `web.TCPSite` on the **currently
running loop** — the same loop that drives Telegram polling. There is no separate thread or
process. **Therefore every synchronous SQLite call inside an `api/` request handler blocks
Telegram message processing for the whole bot.**

AST scan (calls to `database.<fn>()` lexically inside an `async def`, excluding pure-CPU
helpers such as `resolve_team_name`, `teams_match`, `normalize_team_name`,
`parse_flexible_datetime`):

**96 real I/O calls on the event loop, 63 of them in `api/`.**

| File | Blocking I/O calls | Notes |
|---|---:|---|
| `handlers/topic_management.py` | **20** | Worst single file. e.g. `cmd_assign_topic():78 → get_division()`, `cb_set_topic():142 → bind_division_topic()` |
| `api/routes_gamification.py` | **18** | e.g. `handle_get_progression():49 → check_and_update_login_streak()` (a write) |
| `api/routes_admin_live.py` | 12 | e.g. `handle_admin_void_market():312 → transaction()` — full write txn inline |
| `api/routes_predictions.py` | 8 | `handle_place_prediction():59 → place_user_bet()`; `handle_get_predictions():136 → settle_all_pending_finished_matches()` — **a settlement sweep on the request path** |
| `api/routes_admin_season.py` | 6 | |
| `api/routes_tournaments.py` | 6 | |
| `api/routes_wallet.py` | 6 | |
| `handlers/admin.py` | 6 | |
| `api/routes_admin_betting.py` | 4 | |
| `api/routes_markets.py` | 2 | |
| `handlers/chat.py` | 2 | lines 256, 259 — `get_chat_history()`, `get_config()` |
| `handlers/drafts.py` | 2 | |
| `services/animation_sender.py` | 2 | |
| `api/routes_admin_risk.py` | 1 | |
| `handlers/__init__.py` | 1 | |

Most frequently blocked-on functions: `is_division_admin` (9×), `transition_market_status`
(5×), `log_admin_action` (5×), `get_config` (5×), `get_active_season` (4×),
`get_topic_binding` (4×).

**Severity assessment, honestly stated:** most individual SQLite reads in WAL mode complete
in well under a millisecond, so this is *not* an emergency in steady state. It becomes one
in three specific conditions, all of which this codebase meets:
1. `routes_predictions.py:136` runs a **full settlement sweep** synchronously inside a GET.
2. `routes_admin_live.py:312` opens a **write transaction** inline; with `busy_timeout=10000`
   a lock contention stalls the *entire bot* for up to 10 seconds.
3. `check_and_update_login_streak()` (`routes_gamification.py:49`) is a write on a hot read path.

---

#### 🔴 C-5 — Synchronous HTTP with 8s timeouts on the event loop
**File:** `handlers/admin.py:182` · **Function:** `admin_test_ai()`

```python
import socket                                    # 190
import urllib.request                            # 191
_check_proxy_alive("http://127.0.0.1:4001")      # 195 — blocking socket probe
for model in GEMINI_MODELS:
    with opener.open(req, timeout=8) as res:     # 228 — blocking HTTP, per model
```

No `asyncio.to_thread`. The handler serially probes a proxy socket and then every model in
`GEMINI_MODELS` with an 8-second timeout each. On a network failure the bot is frozen for
`8 × len(GEMINI_MODELS)` seconds plus the proxy probe.

**Severity:** Critical — worst-case multi-tens-of-seconds full-bot freeze, triggerable by
one admin button press.

> For contrast, this is done **correctly** elsewhere: `handlers/cabinet.py:2408` and
> `handlers/chat.py:261` both wrap the (synchronous, `urllib`-based) Gemini calls in
> `asyncio.to_thread`. `admin_test_ai` is the outlier, not the rule.

---

### 2.3 SQL Injection — **None Found** ✅

Every dynamic-SQL site was inspected individually. Findings:

- All value binding uses `?` placeholders. No user input reaches an f-string.
- `database.py:7829 update_division(**kwargs)` builds a dynamic `SET` clause but filters
  column names through an `allowed_keys` whitelist first — correct.
- `database.py:1348 migrate_team_names_canonical(cursor)` interpolates `{tbl}` / `{col}`
  at lines 1365, 1372, 1377 from a **hardcoded module-level list**. Safe, but it is a
  *second* interpolation site beyond the `SAFE_COLUMNS` one that `CLAUDE.md` documents as
  "the one intentional exception". → **Documentation gap, not a vulnerability** (see D-14).
- Remaining f-string SQL sites build only `",".join("?" * n)` placeholder runs.

### 2.4 Connection Discipline — **Clean** ✅ (SQL locality is **not** — see D-15)

Exactly one `sqlite3.connect()` in the entire repository: `database.py:17`, inside
`get_connection()`. It correctly sets `journal_mode=WAL`, `busy_timeout=10000`,
`foreign_keys=ON`, `row_factory = sqlite3.Row`. **Every** connection in the codebase comes
from that single factory, and every one is acquired through `transaction()`. That part of
the storage rule holds without exception.

The *other* half of the rule — "`database.py` owns all SQL" — does **not** hold. See
**D-15** in §3.6. That is a maintainability finding, not a safety one: the SQL written
outside `database.py` is still fully parameterized (§2.3) and still runs on the shared
`transaction()` connection.

An AST scan of `database.py` for `cursor.execute` outside a `with transaction()` scope
surfaced 12 candidates; all 12 were manually verified to be **helper functions that
receive a cursor from a caller already inside a transaction** —
`migrate_team_names_canonical` (1348), `_apply_freeze_state`, `seed_gamification_catalog`
(6958, covering lines 6994/7016/7030). **No violations.**

---

## 3. 🟡 Technical Debt & Legacy (Season 1 Leftovers)

### 3.1 Dead Shadowed Definitions

Python keeps the **last** definition. These six earlier definitions are unreachable dead
code that actively misleads readers — three of them are Season 1 implementations sitting
directly above their Season 2 replacements.

| File | Dead def | Live def | Nature of the difference |
|---|---|---|---|
| `database.py` | **2249** `clear_all_matches()` | 3994 `clear_all_matches(season_id=None)` | Dead version only `DELETE FROM matches`; live version also clears `rounds` |
| `database.py` | **2255** `batch_insert_matches(matches_list)` | 4019 `batch_insert_matches(fixtures, division_id=None, season_id=None)` | Dead version **hardcodes `tournament_id=1`** — pure Season 1 |
| `handlers/admin.py` | **911** `admin_list_players_page` | 3093 | Registered at `handlers/__init__.py:671` — later def wins |
| `handlers/admin.py` | **1414** `admin_view_player` | 3148 | Registered at `handlers/__init__.py:672` |
| `handlers/admin.py` | **1485** `admin_delete_player_execute` | 3391 | Registered at `handlers/__init__.py:680` |
| `services/sports/adapters/api_sports.py` | **314** `get_provider_status` | 632 | Dead version returns a static `UNCONFIGURED` dict; live version returns real health telemetry |

### 3.2 Global-Scope Defaults — the core architectural drift

The migration made division/season **optional parameters with `None` defaults**. Every
caller that forgets to pass them silently gets Season 1 global behaviour.

| Function | Line | Signature |
|---|---|---|
| `get_standings` | 2062 | `(division_id=None, season_id=None)` |
| `get_teams_recent_form` | 2695 | `(limit=5, division_id=None, season_id=None)` |
| `get_top_scorers` | 4203 | `(limit=20, division_id=None, season_id=None)` |
| `get_all_unplayed_league_matches` | 5214 | `(division_id=None, season_id=None)` |

And these have **no division/season parameter at all**:

| Function | Line |
|---|---|
| `list_users()` | 1638 |
| `get_player_stats(telegram_id)` | 1647 |
| `get_squad(team_name)` | 3784 |
| `get_squad_with_positions(team_name)` | 3808 |

### 3.3 Confirmed Consequences of §3.2

#### 🟡 D-1 — `/table` renders a global, cross-division table
**File:** `handlers/text_commands.py:81`

```python
img_buf = await asyncio.to_thread(generate_league_table_image)   # no arguments
```

With no `standings` argument, `generate_league_table_image` (`services/graphics/table_generator.py:138`)
falls back at lines 143–146 to `database.get_standings()` and
`database.get_teams_recent_form(limit=5)` — both unscoped. Line 152 then defaults to
`num_rows = 16` (the Season 1 club count) and line 184 renders the legacy title
«ТУРНИРНАЯ ТАБЛИЦА» instead of «СЕЗОН 2 • {division_name}».

The callback is even named `refresh_div_table_0` — division zero.

Same pattern at lines 96 (`get_top_scorers`, no scope), 110 and 139 (top-stats, no scope).

**Impact:** the most-used public command in the bot shows Season 1 data in a Season 2 world.

#### 🟡 D-2 — Graphics generators default to global data
**File:** `services/graphics/table_generator.py:143-146, 152, 184`
The fallback described above is a property of the generator itself, so *any* caller that
omits arguments inherits the bug.

#### 🟡 D-3 — `squad_players` has no division or season scope
**File:** `database.py:122-128` — `UNIQUE(team_name, player_name)`.
A player name is globally unique per team across all divisions and all seasons. Squads
cannot diverge between Season 1 and Season 2, and `get_squad()` / `get_squad_with_positions()`
have no way to ask for a scoped view.

#### 🟡 D-4 — Base schemas predate the migration
**File:** `database.py:91-98` (`users`), `101-113` (`matches`)
Neither base `CREATE TABLE` includes `division_id` or `season_id`; both were bolted on via
later `ALTER TABLE`. Correct per the additive-migration policy, but it means a fresh
`init_db()` produces columns in an order that does not reflect the domain model, and
nullable scope columns that the schema cannot enforce.

### 3.4 Single-Group Season 1 Assumptions

| Item | Location | Issue |
|---|---|---|
| `GROUP_ID` | `config.py:55` | One global group id from `TELEGRAM_GROUP_ID`. Season 2 routes per division via `divisions.group_chat_id` + `division_topics`. |
| `get_group_id()` | `database.py:2952` | Reads a single global `system_config.group_id`. |
| Hardcoded club lists | `config.py:64-68` `KPL_TEAMS`, `70-87` `CLUBS` | 16 fixed clubs. Divisions should be populated dynamically from `divisions` + `users.division_id`. |
| Legacy warns-topic fallback | `handlers/admin.py:521` | Falls back to `get_config("warns_topic_id")` instead of `get_division_topic(div_id, 'warns')`. |
| `div_id = r.get("division_id") or 1` | `handlers/admin.py:4218` | `job_check_deadlines_and_remind` is division-aware but silently defaults orphaned rounds to division 1. Note `or 1` also swallows a legitimate `division_id = 0`. |
| Hardcoded Season 1 results text | `handlers/chat.py:190-218` | ~28 lines of literal Season 1 standings and rules baked into the AI system prompt. |

#### 🟡 D-5 — ~53 lines of unreachable global fallback
**File:** `handlers/admin.py:578-643` · `_post_or_update_debts_in_warns()`

The global (non-division) fallback branch at lines 591–643 is dead: it is guarded on
`get_active_divisions()` returning empty, but `get_active_divisions()` calls
`ensure_canonical_divisions()` first, which seeds 5 divisions via `INSERT OR IGNORE`
(`database.py:555-594`). The list can never be empty. The division-aware sibling
`_post_or_update_debts_for_division()` (line 508) is the only live path.

### 3.5 Error Handling

- **Zero bare `except:`** — genuinely good. ✅
- **144 silent `except X: pass`** across 21 files, out of 502 total handlers (29%):

| File | Count |
|---|---:|
| `handlers/cabinet.py` | 33 |
| `handlers/base.py` | 30 |
| `handlers/admin.py` | 29 |
| `database.py` | 25 |
| `services/animation_sender.py` | 5 |
| `services/player_positions.py` | 3 |
| 15 other files | 1–2 each |

Many are legitimate (optional Telegram UI operations that may fail harmlessly, e.g.
deleting an already-deleted message). But `handlers/cabinet.py:2971` proves the category is
dangerous: it converted a hard `NameError` into a plausible-looking warning about network
connectivity, and the bug survived into production as a result.

`main.py:51-52` is the same pattern in the startup path — a per-admin
`set_chat_menu_button` failure is swallowed with `except Exception: pass`.

### 3.6 Dead Code & Codebase Health

#### Orphaned table
**`teams`** — `database.py:526-536`. Created with an index
(`idx_teams_name ON teams(LOWER(name))`), and **never read or written** anywhere in
production code. The only references are in `tests/test_purge_old_season.py`. Team data
lives in `config.CLUBS` and `users.team_name` instead.

#### Write-only tables
- **`schema_migrations`** — `database.py:69`. Written at lines 597, 1168, 1277; **never
  read**. `CLAUDE.md` states migrations are "guarded by `CREATE TABLE IF NOT EXISTS` and
  `schema_migrations`", but in practice only the former guard is active. This table is
  bookkeeping that nothing consults.
- **`provider_sync_log`** — `database.py:1116`. Written via `record_provider_sync_log()`
  (8554), called from `services/sports/health.py:55`; never read back. Acceptable as a
  pure audit trail, but there is no reader, admin view, or retention policy.

#### `rounds_v3` — not orphaned, but worth knowing
`database.py:165-194` creates `rounds_v3`, seeds it, then `ALTER TABLE rounds_v3 RENAME TO
rounds` at line 194. This is a **table-rebuild migration**, not a live table. It is the
standard SQLite pattern for changing constraints, but it does technically rewrite an
existing table, which sits in tension with the `CLAUDE.md` rule "Never rewrite or drop an
existing table". Worth an explicit annotation so a future reader does not "fix" it.

#### Unused functions (35)
Defined exactly once, with the name appearing exactly once in the entire repository
(including tests) — i.e. defined and never called:

`database.py` — `upsert_user` (1610), `async_get_active_season` (1955),
`async_get_active_divisions` (1960), `save_match_events` (2447),
`get_match_frozen_seconds` (3146), `get_admins` (3201), `pre_register_player` (3208),
`get_unplayed_matches_in_round` (4187), `reset_all_debt_reminders` (5689),
`get_active_round_number` (5760), `get_public_gamer_profile` (7305),
`remove_division_admin` (7996), `get_division_admins` (8003),
`get_prediction_snapshots` (8428), `get_provider_match_by_internal_id` (8628),
`update_provider_health_state` (8641), `get_provider_health_state` (8663)

`handlers/admin.py` — `admin_remove_player_command` (2999), `admin_list_players_command` (3019), `admin_ai_summary` (5242)
`handlers/cabinet.py` — `match_squad_player_names` (36)
`services/ai/ai_recognizer.py` — `recognize_match_screenshot_bytes` (461) *(singular; the plural `recognize_match_screenshots_bytes` at 305 is the live one)*
`services/betting_limits.py` — `set_global_limits` (135)
`services/dynamic_confidence.py` — `calculate_dynamic_confidence` (25)
`services/elo_engine.py` — `update_ratings_post_match` (133)
`services/gamification_notifications.py` — `notify_achievement` (60), `notify_season_reward` (66), `notify_promotion` (72), `notify_relegation` (78)
`services/graphics/club_card_generator.py` — `_clean_white_background_if_needed` (66)
`services/graphics/player_photos.py` — `fetch_all_players` (280)
`services/sports/adapters/mock_provider.py` — `seed_odds` (66), `seed_neutral_event` (72), `seed_neutral_statistics` (75)
`services/tournament_validator.py` — `assert_valid_fixtures` (167)

> ⚠️ Two of these deserve scrutiny rather than deletion:
> - **`remove_division_admin` / `get_division_admins`** — per-division admin management is a
>   *Season 2* feature that appears to be **unfinished**, not obsolete.
> - **All four `gamification_notifications` functions** — an entire notification surface
>   that was built and never wired up.
> - The four `mock_provider` seeders are test scaffolding and are fine as-is.

#### Commented-out legacy code
**None found.** A scan for commented-out code blocks of ≥3 consecutive lines returned zero
results across the codebase. ✅

#### Circular import pressure
15 circular dependency chains, worked around by **13 deferred imports** of `services.*` /
`handlers.*` placed inside function bodies in `database.py`: lines 3070, 3743, 3810, 3850,
3887, 4146, 5861, 6105, 6151, 6415, 6483, 6926, 7223. This is a symptom of `database.py`
having grown to 8,687 lines and absorbed responsibilities beyond persistence.

#### 🟡 D-14 — `CLAUDE.md` inaccuracies
Two documented facts are contradicted by the code:
1. *"Google Gemini via raw REST over `aiohttp` — there is no Google SDK dependency."* The
   second half is true; the first is not. Both `services/ai/ai_recognizer.py` (line 362)
   and `services/ai/ai_chat.py` (lines 89, 98) use **synchronous `urllib.request`**. This
   matters: it is precisely *why* those calls must be wrapped in `to_thread`, and why
   `admin_test_ai` blocking the loop (C-5) is an easy mistake to make.
2. *"The one intentional exception is the migration helper that interpolates column names
   from the hardcoded `SAFE_COLUMNS` tuple."* There is a **second** such site —
   `migrate_team_names_canonical` (`database.py:1348`). Also safe, also undocumented.

#### 🟡 D-15 — "`database.py` owns all SQL" is not true

`CLAUDE.md` states: *"`database.py` owns all SQL. Do not open connections or write queries
elsewhere."* The first clause is violated at scale.

**244 `cursor.execute` / `executemany` calls live in 38 runtime modules outside
`database.py`** (291 across 44 files if one-off `scripts/` and `purge_old_season.py` are
included). By package:

| Package | execute calls |
|---|---|
| `services/` | 178 |
| `api/` | 63 |
| `handlers/` | 3 |

Top offenders:

| File | Calls |
|---|---|
| `services/settlement_engine.py` | 28 |
| `services/odds_engine.py` | 25 |
| `api/routes_admin_live.py` | 20 |
| `services/live_ingestion.py` | 16 |
| `services/market_safety.py` | 11 |
| `api/routes_matches.py` | 10 |
| `services/notification_service.py` | 10 |
| `services/analytics_service.py` | 9 |
| `services/risk_engine.py` | 9 |
| `api/routes_user_extras.py` | 8 |

Concrete examples: `api/routes_matches.py:35` builds a `SELECT m.* FROM matches LEFT JOIN
bet_markets …` inline in a request handler; `api/routes_user_extras.py:75` runs an
`INSERT INTO saved_coupons` inline.

**Severity: maintainability, not safety.** All of it is parameterized (§2.3) and all of it
runs on the shared `transaction()` connection (§2.4). But it means schema knowledge is
spread across 38 files, so a column rename is a repo-wide grep rather than a single-file
edit — and it is exactly what makes finishing the Season 2 `division_id` / `season_id`
migration (§3.2, P3) expensive.

Two sub-cases worth separating when this is addressed:

- **`services/` betting engines (178 calls).** These are cohesive query-heavy engines. The
  pragmatic fix is not to move them into `database.py` (already 8.7k lines) but to
  acknowledge them as a second legitimate persistence layer and amend `CLAUDE.md`.
- **`api/` route handlers (63 calls).** These *should* move behind repository functions —
  a request handler doing its own JOINs is the harder problem, and it is the layer where
  the event-loop rules (§2.2) also bite.

#### 🟡 D-16 — `transaction()` is thread-local, which constrains the `to_thread` fix

`_tx_local` is a `threading.local`. The re-entrancy that makes `transaction()` so valuable
(§4.1) **only works within one thread**. Consequence: a `database.*` call that sits inside
an open `with database.transaction()` block cannot be moved to `asyncio.to_thread` — the
worker thread sees an empty stack, opens a *second* connection, and blocks on the write
lock the caller is still holding. SQLite reports `database is locked` after
`busy_timeout`.

This was confirmed empirically, not theoretically: five such calls in
`api/routes_admin_live.py` (market void + result correction) produced HTTP 500s and a
failing `tests/test_phase6_security.py` until they were reverted to synchronous calls with
an explanatory comment.

**Rule for anyone continuing the P1 work:** wrap a call in `to_thread` only if it is *not*
lexically inside a `with database.transaction()` block. Blocks that hold a transaction open
across multiple statements must instead be extracted wholesale into a single sync function
in `database.py` and that whole function wrapped once.

---

## 4. 🟢 Clean Code Highlights

Credit where it is due — several things here are done better than in most codebases of
this size.

1. **The re-entrant `transaction()` context manager.** A nested call on the same thread
   joins the outer transaction and commits only at the outermost exit. This makes
   composite operations (confirm match → advance cup series → award XP) atomic without any
   hand-rolled multi-step commit logic. It is the single best design decision in the
   project, and it is **respected everywhere** — the AST audit found zero violations.

2. **Perfect connection discipline.** Exactly one `sqlite3.connect()` in 179 modules, with
   WAL, a 10s busy timeout, foreign keys enforced, and `sqlite3.Row` row factory all set in
   one place. Every connection in the codebase comes from that factory, and every one is
   acquired through `transaction()` — so pooling, WAL semantics and atomicity are uniform
   no matter which module runs the query.

   *Correction to an earlier draft of this report:* only the **connection** half of the
   storage rule held. SQL *statements* are written in 38 runtime modules outside
   `database.py`. That is tracked as **D-15** in §3.6, not as a highlight.

3. **100% parameterized SQL.** Not a single injection vector across ~1,400 queries. Where
   dynamic SQL was genuinely necessary, it is gated behind whitelists
   (`update_division`'s `allowed_keys`) or restricted to generated `?` runs.

4. **Zero bare `except:`.** All 502 handlers name a type.

5. **Correct async offloading where it counts most.** The two heaviest operations —
   Gemini Vision OCR (`handlers/cabinet.py:2408`) and Gemini chat
   (`handlers/chat.py:261`) — are both correctly wrapped in `asyncio.to_thread`. The
   author clearly understood the pattern; C-5 is an isolated lapse, not ignorance.

6. **Fault-isolated startup.** `main.py:21-26` wraps the Mini App API launch so an API
   failure can never prevent the bot from starting, and `register_jobs()` wraps the Phase 6
   job registration separately (lines 66-76) so one failing subsystem cannot take the
   others down. This is exactly right.

7. **Deliberate handler registration order.** `global_lockdown_guard` at group=-1, group
   tracking at group=1, then user/cabinet/admin/betting conversations, and the AI catch-all
   registered strictly last. The ordering constraint is real, non-obvious, and correctly
   maintained.

8. **Additive migration policy.** Schema evolution is `CREATE TABLE IF NOT EXISTS` plus
   guarded `ALTER TABLE`, with no destructive rewrites (the `rounds_v3` rebuild being the
   sole, and legitimate, exception).

9. **548 passing tests** across 78 files, with genuinely adversarial ones —
   `test_phase7_1_redteam.py`, `test_phase8_provider_security.py` (which explicitly asserts
   `record_provider_sync_log` survives injection strings), `test_phase10_concurrency.py`,
   `test_phase10_security.py`.

10. **Perceptual OCR boundary.** The rule that Gemini extracts only what is visibly on
    screen, with team identification and squad enrichment done deterministically afterwards
    in Python/SQLite (`detect_teams_from_players`, `match_and_enrich_squad`), is a sound
    architectural boundary that keeps model hallucination out of the data layer.

---

## 5. 📋 Action Plan

Ordered by (impact × confidence) ÷ effort. Every item below has a verified file and line.

### Priority 0 — Broken in production, fix immediately

Each is a one-to-three-line change with no architectural risk.

| # | File:Line | Action | Effort |
|---|---|---|---|
| **P0-1** | `handlers/chat.py:250` | Define `cup_info_text` before the `context_data` assembly at line 234 (build it from cup data, or bind `cup_info_text = ""` if the feature is not ready). **Restores the entire AI chat feature.** | 5 min |
| **P0-2** | `handlers/cabinet.py:2965` | Bind `photo_id` in `submit_report_to_guest()` — mirror the `payload.get("photo_id")` pattern used at line 2890. **Restores opponent notification.** | 5 min |
| **P0-3** | `services/risk_engine.py` (imports, ~line 14) | Add `import datetime`. Verifies against usages at lines 203 and 207. | 2 min |
| **P0-4** | `database.py:5003` | `pending_league.sort(key=lambda x: x["round_number"])` — fix the name *and* confirm ascending order is intended for pending fixtures. | 5 min |
| **P0-5** | `handlers/admin.py:182` | Wrap the whole blocking body of `admin_test_ai` — the `_check_proxy_alive` probe at line 195 and the `opener.open(...)` loop at line 228 — in a single `await asyncio.to_thread(...)`. | 20 min |

**Verification for P0:** add four focused regression tests, one per `NameError`, that
exercise the specific branch (AI chat invocation; guest report with and without a photo;
`evaluate_bet` on a round *with* a deadline; `get_club_schedule_and_results` for a club
*with* pending fixtures). These four branches are exactly the coverage gap that let all
four bugs ship past 548 green tests.

### Priority 1 — Event loop protection

| # | Target | Action | Effort |
|---|---|---|---|
| **P1-1** | `api/routes_predictions.py:136` | Move `settle_all_pending_finished_matches()` off the GET request path entirely — it belongs in the existing `register_jobs()` schedule, not in `handle_get_predictions`. | 1 h |
| **P1-2** | `api/routes_admin_live.py:312` | Wrap the inline `database.transaction()` write in `asyncio.to_thread`. Highest-risk single call: a lock wait can freeze the bot for the full 10s `busy_timeout`. | 15 min |
| **P1-3** | `api/routes_gamification.py:49` | Wrap `check_and_update_login_streak()` (a write on a hot read path) in `to_thread`. | 15 min |
| **P1-4** | `api/` (all 13 route modules, 63 calls) | Systematic sweep. **Recommended approach:** rather than 63 individual `to_thread` wraps, introduce one `async def db_call(fn, *args)` helper in `api/server.py` and migrate call sites to it. Cheaper to write, far cheaper to review, and makes future violations greppable. **Revised estimate (see D-16): 1–2 days, not 4–6 h.** Only the ~60 *standalone* calls are mechanical; the ~30 calls inside open `with database.transaction()` blocks cannot be wrapped at all and must each be extracted into a new sync function in `database.py` first. | 1–2 d |
| **P1-5** | `handlers/topic_management.py` (20 calls) | Same treatment. Lower urgency than `api/` — these are admin-only commands with low call frequency. | 2 h |
| **P1-6** | `handlers/chat.py:256,259` · `handlers/admin.py` (6) · `handlers/drafts.py` (2) · `services/animation_sender.py` (2) · `handlers/__init__.py` (1) · `api/routes_markets.py` (2) · `api/routes_admin_risk.py` (1) | Remaining 15 calls. | 1 h |

> **Explicitly out of scope:** the 50 calls to `resolve_team_name`, `teams_match`,
> `normalize_team_name`, and `parse_flexible_datetime` inside async functions. These are
> pure-CPU helpers that touch no I/O; wrapping them in `to_thread` would make things
> *slower*, not faster.

### Priority 2 — Delete dead code (zero-risk cleanup)

Do this **before** P3 — it removes six misleading Season 1 functions that would otherwise
confuse the refactor.

| # | File:Line | Action |
|---|---|---|
| **P2-1** | `database.py:2249-2254` | Delete dead `clear_all_matches()` (shadowed by 3994) |
| **P2-2** | `database.py:2255-…` | Delete dead `batch_insert_matches()` (shadowed by 4019; hardcodes `tournament_id=1`) |
| **P2-3** | `handlers/admin.py:911, 1414, 1485` | Delete the three shadowed handler definitions (live versions at 3093, 3148, 3391) |
| **P2-4** | `services/sports/adapters/api_sports.py:314` | Delete the shadowed `get_provider_status` (live version at 632) |
| **P2-5** | `handlers/admin.py:591-643` | Delete the unreachable global debts fallback (`get_active_divisions()` can never return empty) |
| **P2-6** | `database.py:526-536` | Remove the orphaned `teams` table + index — **or** decide it is the intended future home for club data and populate it. Requires a decision, not just a deletion. |
| **P2-7** | 35 unused functions (§3.6) | Triage, do not bulk-delete. Three groups: **(a)** genuinely dead → delete; **(b)** `remove_division_admin` (7996) + `get_division_admins` (8003) → **unfinished Season 2 feature, wire up**; **(c)** all four `services/gamification_notifications.py` functions (60/66/72/78) → **built and never connected, decide to wire or drop**. |

### Priority 3 — Architectural: finish the Season 2 migration

This is the real work. Sequence matters.

| # | Step | Files |
|---|---|---|
| **P3-1** | **Fix the visible symptom first.** Pass division and season explicitly at every graphics call site. | `handlers/text_commands.py:81` (the unscoped `generate_league_table_image()`), plus lines 96, 110, 139 |
| **P3-2** | **Remove the global fallback from the generator** so no caller can silently inherit Season 1 data. Make `division_id` / `season_id` required. Delete the `get_standings()` / `get_teams_recent_form()` fallback at `table_generator.py:143-146`; drop the `num_rows = 16` default at 152; drop the legacy title branch at 184. | `services/graphics/table_generator.py` |
| **P3-3** | **Flip the defaults in the data layer.** Change `division_id=None, season_id=None` to required parameters on `get_standings` (2062), `get_teams_recent_form` (2695), `get_top_scorers` (4203), `get_all_unplayed_league_matches` (5214). The type checker and test suite will then enumerate every unscoped caller for you. **This is the single highest-value structural change in the plan** — it converts silent wrong-data bugs into loud failures. | `database.py` |
| **P3-4** | **Add scope to the unscoped four:** `list_users` (1638), `get_player_stats` (1647), `get_squad` (3784), `get_squad_with_positions` (3808). | `database.py` |
| **P3-5** | **Schema:** add `division_id` / `season_id` to `squad_players` (122-128) via additive migration and widen `UNIQUE(team_name, player_name)` accordingly. Squads currently cannot differ across divisions or seasons. | `database.py` |
| **P3-6** | **Retire single-group config.** Replace `config.GROUP_ID` (`config.py:55`) and `database.get_group_id()` (2952) with `divisions.group_chat_id` + `get_division_topic()` lookups. Remove the legacy `get_config("warns_topic_id")` fallback at `handlers/admin.py:521`. | `config.py`, `database.py`, `handlers/admin.py` |
| **P3-7** | Replace hardcoded `KPL_TEAMS` / `CLUBS` (`config.py:64-87`) with dynamic per-division queries. **Partly done:** name resolution no longer reads the 16 КПЛ names — it moved to `club_registry.py` behind `config.CLUB_REGISTRY`, the 0.65 fuzzy threshold and the substring tier are gone, and `tests/test_standings_name_collision.py` pins the reported symptom (three clubs, two rows). **Still open:** `CLUB_REGISTRY` is an empty stub falling back to `KPL_TEAMS ∪ CLUBS`. Bulk-filling it from the VPS was attempted on 2026-09-12 and returned nothing to fill it with: the audit found five divisions but an empty roster (no `users.team_name` set), since the previous season was purged and registration has not restarted. The list now grows per registration instead. Also still open: the `division_id is None` branch of `get_standings` (2087) still iterates `KPL_TEAMS` — that one belongs to P3-3. | `config.py` + all consumers |
| **P3-8** | Replace `div_id = r.get("division_id") or 1` (`handlers/admin.py:4218`) with explicit handling — `or 1` both hides orphaned rounds and mishandles a legitimate `division_id = 0`. | `handlers/admin.py` |
| **P3-9** | Move the hardcoded Season 1 results/rules text (`handlers/chat.py:190-218`) into a division-scoped query. Do this **with** P0-1, since both touch the same prompt assembly. | `handlers/chat.py` |

### Priority 4 — Observability & hygiene

| # | Action |
|---|---|
| **P4-1** | Audit the 144 silent `except X: pass` handlers, starting with `handlers/cabinet.py` (33), `handlers/base.py` (30), `handlers/admin.py` (29). Add `logger.debug`/`logger.warning` to each. **Rationale: C-2 hid in one of these for an unknown length of time.** Adopt a rule that a swallowed exception must be logged. |
| **P4-2** | Either read `schema_migrations` in the migration guard or document it as write-only bookkeeping. Right now `CLAUDE.md` claims a guard that does not exist. |
| **P4-3** | Add a reader/retention policy for `provider_sync_log`, or document it as append-only. |
| **P4-4** | Annotate the `rounds_v3` → `rounds` rebuild (`database.py:165-194`) explaining why it rewrites a table, so it is not "corrected" later. |
| **P4-5** | Correct `CLAUDE.md`: Gemini uses **synchronous `urllib.request`**, not `aiohttp` (this is *why* `to_thread` is mandatory); and document the second safe-interpolation site at `database.py:1348`. |
| **P4-6** | Reduce the 15 circular chains by extracting the 13 deferred-import call sites in `database.py` into a service layer. Large effort, low urgency — the deferred imports work. |

---

### Suggested sequencing

1. **P0 (≈40 min)** — restores two broken user-facing features and removes a
   multi-tens-of-seconds bot freeze. Do this first regardless of everything else.
2. **P1-1, P1-2, P1-3 (≈1.5 h)** — the three genuinely dangerous blocking calls.
3. **P2 (≈2 h)** — clears misleading dead code before touching architecture.
4. **P3-1 → P3-3 (≈1 day)** — flipping the defaults in P3-3 makes the compiler and test
   suite find every remaining unscoped caller, which de-risks the rest of P3 substantially.
5. **P1-4 (≈half day)**, then remaining P3, then P4.

---

## 6. ⚙️ Execution Log

Plan approved 2026-09-08. What follows is what actually changed on disk.

### ✅ Priority 0 — complete

| # | File | Change | Verified |
|---|---|---|---|
| **P0-1** | `handlers/chat.py` | Added the missing cup-bracket section. Rather than stubbing `cup_info_text = ""`, added a real reader `database.get_all_cup_series()` and built the block from it — every sibling block in that prompt is a real data section, so a stub would have shipped a silently degraded AI. | AI chat prompt assembles |
| **P0-2** | `handlers/cabinet.py:2855` | Bound `h_score`, `a_score`, `scorers`, `assists`, `photo_id` from `payload` in `submit_report_to_guest()`. | |
| **P0-3** | `services/risk_engine.py` | Added `import datetime`. | |
| **P0-4** | `database.py:5016` | `lambda x: l["round_number"]` → `lambda x: x["round_number"]`. The `l` from the enclosing comprehension does not leak in Python 3. | |
| **P0-5** | `handlers/admin.py` | Extracted the blocking proxy probe + per-model reachability loop into `_run_diagnostics()` and called it via a single `await asyncio.to_thread(...)`. Moved the API-key check *ahead* of the blocking work. Removed unused `import socket`, added explicit `import urllib.error`. | |

**Repo-wide pyflakes: zero undefined names in production code.** (Two remain in tests —
`tests/test_phase9_miniapp.py:196` and `tests/test_phase9_security.py:193`, both an
undefined `unittest`; both pre-existing and out of scope.)

### 🟡 Priority 1 — partially complete

**P1-1 — done, and it was worse than the report described.** `settle_all_pending_finished_matches()`
had **no** background job at all; the two request paths (`GET /api/predictions` and the
wallet bootstrap) were the *only* things that ever settled bets. Deleting the inline calls
first would have silently stopped settlement. Order of work was therefore: add
`settle_finished_bets_job` to `services/background_sync.py` → register it in
`main.py:register_jobs()` at `interval=60, first=25` → *then* remove both inline sweeps,
leaving a NOTE comment at each site.

**P1-4 / P1-6 — 60 of ~90 calls done.** Wrapped mechanically with a throwaway AST rewriter
(byte-offset splicing, `ast.parse` validation before write; script deleted afterwards)
across nine modules: `routes_gamification` (18), `routes_admin_live` (12),
`routes_predictions` (7), `routes_admin_season` (6), `routes_tournaments` (6),
`routes_wallet` (4), `routes_admin_betting` (4), `routes_markets` (2),
`routes_admin_risk` (1).

**Two defects the sweep introduced, both found and fixed:**

1. The rewriter inserted `import asyncio` *above* `from __future__ import annotations` in
   `api/routes_admin_live.py` → `SyntaxError`, cascading through `api/__init__.py` and
   `api/server.py`. Fixed by reordering.
2. Five wrapped calls sat inside open `with database.transaction()` blocks and deadlocked
   against the write lock (`sqlite3.OperationalError: database is locked`), turning market
   void and result correction into HTTP 500s. Caught by
   `tests/test_phase6_security.py::test_destructive_action_safety_and_audit`. Reverted to
   synchronous calls with an explanatory comment. **This is the origin of D-16** — the
   constraint was not known when the plan was written.

**Not done:** the ~30 `api/` calls inside `transaction()` blocks (need hand extraction per
D-16), P1-5 `handlers/topic_management.py` (20 calls), and the P1-6 remainder
(`handlers/admin.py` 6, `handlers/drafts.py` 2, `services/animation_sender.py` 2,
`handlers/__init__.py` 1).

### ⬜ Priorities 2–4 — not started

P2 (dead code), P3 (Season 2 migration completion), P4 (observability) are untouched.

### ✅ P0 regression tests — written

New file: `tests/test_p0_nameerror_regressions.py`, **9 tests**, closing the exact coverage
gap that let all four `NameError`s ship past 548 green tests.

| Class | Covers | Asserts |
|---|---|---|
| `TestAiChatContextAssembly` | P0-1 | `handle_ai_chat` assembles the full prompt and replies; the prompt contains the cup block **and** its six sibling sections (so a `cup_info_text = ""` stub would not satisfy it); `get_all_cup_series()` returns every field `chat.py` formats |
| `TestGuestReportPayloadBinding` | P0-2 | photo present → `send_photo` with the right `photo_id`, score and scorer names; photo absent → `send_message`; **never** falls through to `submit_report_to_admin`; the persisted pending report carries `photo_id` and both scores |
| `TestRiskEngineDeadlineBranch` | P0-3 | expired deadline → `MARKET_SUSPENDED`; future deadline → *not* suspended; malformed deadline string → tolerated |
| `TestClubSchedulePendingSort` | P0-4 | three pending rounds created out of order come back ascending, all `is_completed = False` |

**Each test was verified to actually fail against the original bug**, not merely to pass
against the fix — each of the four defects was mechanically reintroduced, the corresponding
test run, and the source restored:

```
handlers/chat.py             -> FAILS as expected   [1 failed]
handlers/cabinet.py          -> FAILS as expected   [3 failed]
services/risk_engine.py      -> FAILS as expected   [3 failed]
database.py                  -> FAILS as expected   [1 failed]
```

### Test status

**557 passed, 0 failed** — the 548-test baseline plus the 9 new regression tests. The single
failure introduced mid-execution (defect 2 above) is fixed.

### Outstanding follow-up

Beyond the unfinished P1 work listed above, one housekeeping item surfaced during
execution: `league.db.bak-20260907-203234` sits untracked in the repo root and is **not**
matched by `.gitignore`. Per the project's own secrets rule, `league.db` contains real user
data and must never be committed — its backups inherit that. Either add a `league.db.bak-*`
pattern to `.gitignore` or move the file out of the working tree.
