# CLAUDE.md — logovobot

Guidance for Claude Code when working in this repository.

**Logovobot** (Логово Фифарей / ИИ «Темшик») is an async Telegram bot that runs FIFA/FC
e-sports championships: divisions and rounds, match result intake via AI screenshot OCR,
standings and Pillow-rendered infographics, a debt/warn discipline system, and a virtual
prediction market ("Logovo.bet") exposed through a Telegram Mini App.

The project is well past MVP — 246 Python files (117 application modules + 129 pytest
files), 59 SQLite tables, and ten completed development phases documented in the
`PHASE_*.md` reports at the repo root. Post-phase work is logged in the numbered
`FIX_*.md` notes and the `*_AUDIT.md` reports beside them.

---

## Stack

- Python 3.11+, `python-telegram-bot[job-queue]` v21 (fully async)
- SQLite in WAL mode — single file, no ORM, hand-written SQL
- Google Gemini via **raw REST over `aiohttp`** — there is no Google SDK dependency
- Pillow + `pillow-heif` + `opencv-python-headless` + `numpy` for graphics and image prep
- `aiohttp` also serves the Mini App API; deployed as a single `worker: python main.py`

`requirements.txt` is runtime-only — the worker installs just that file, so no test tooling
may be added to it. `requirements-dev.txt` pulls it in via `-r` and adds `pytest` and
`pytest-xdist`. Deployment artefacts at the root: `Procfile` (`worker: python main.py`),
`Dockerfile`, `.dockerignore`.

---

## Commands

Run tests:

```bash
python -m pytest tests/ -q
```

Run a single test file:

```bash
python -m pytest tests/test_divisions_schema.py -v
```

Run the bot (needs a populated `.env`):

```bash
python main.py
```

Run serially when debugging shared state:

```bash
python -m pytest tests/ -n0
```

Install the dev dependencies first:

```bash
pip install -r requirements-dev.txt
```

`pytest.ini` is the only config (there is no `pyproject.toml` / `setup.cfg`). It sets
`testpaths = tests` and `addopts = -q -n auto --dist loadfile`, so runs are parallel across
cores by default, with each file pinned to one worker. Tests that share module or class
state within a file therefore keep their order. `markers` declares `slow`, excluded via
`-m "not slow"`.

The repo-root `conftest.py` is the test bootstrap and does the DB isolation, because
`config.py` resolves `DB_PATH` at import time:

- it sets `LEAGUE_SQLITE_PATH` to a per-process temp file before anything imports `config`,
  and provides placeholder `TELEGRAM_BOT_TOKEN` / `ADMIN_IDS` via `setdefault` for the
  module-level `from config import …` in many test files;
- a module-scoped autouse fixture then gives **every test module its own** `league.db` and
  calls `init_db()` on it, so files are not order-dependent and the repo-root `league.db`
  is never touched;
- further autouse fixtures disable API rate limiting, keep `config.ADMIN_IDS` the same list
  object across the process, and drop the cached per-thread SQLite connection around each
  test (needed on Windows, where an open handle silently blocks file deletion).

CI is `.github/workflows/tests.yml` — Python 3.11, `pip install -r requirements-dev.txt`,
`python -m pytest tests/` on pushes to `main` and on every PR. It passes deliberately fake
secrets as env vars; real values live only in the server `.env` and must never become
repository secrets.

---

## Architecture

`main.py` is thin: configure logging → `init_db()` → build `ApplicationBuilder` →
`register_all_handlers()` → `register_jobs()` → `run_polling()`. The Mini App API server
is started from `post_init` as a background task, wrapped in try/except so an API failure
never prevents the bot itself from starting. Preserve that isolation.

| Path | Responsibility |
|---|---|
| `main.py` | Entrypoint, `post_init`, background job registration |
| `config.py` | All env parsing. Every setting must be read here, never via `os.getenv` at a call site |
| `database.py` | ~10.5k lines: schema, migrations, and every repository function |
| `constants.py` | Shared enums and literals |
| `club_registry.py` | Canonical club names, aliases, and the tiered name resolver. Imports `config` only |
| `conftest.py` | Test bootstrap: per-module temp SQLite DB and the autouse fixtures (see Commands) |
| `handlers/` (11 modules) | Telegram entrypoints — `admin`, `cabinet`, `drafts`, `betting`, `chat`, `topic_management`, `text_commands`, `squad_ai`, `tracker`, `base` |
| `services/ai/` | `ai_recognizer.py` (match-result Gemini Vision OCR), `squad_recognizer.py` (lineup OCR), `ai_chat.py` («Темшик» persona), `persona_base.py` |
| `services/graphics/` (9 modules) | Pillow renderers: standings tables, club/player/FC cards, club schedules, round digests, top-stats, `division_theme.py`, `player_photos.py` |
| `services/sports/` + `sports_provider.py` | External live-football provider adapters plus `cache`, `circuit`, `limiter`, `freshness`, `health`, `odds_sync` |
| `services/` (root, ~39 modules) | Betting/market engines, ELO, Poisson, risk, settlement, gamification, seasons, `topic_cache.py` |
| `api/` (18 modules) | `aiohttp` Mini App API — `server.py`, `auth.py`, `rate_limiter.py`, and 15 `routes_*.py` modules |
| `web/` | Mini App frontend (static `index.html`, `css/`, `js/` — `api`, `app`, `effects`, `store`, `tg`, `ui`) |
| `utils/` | `media_utils.py`, a thin re-export wrapper over `services/animation_sender.py` |
| `scripts/` (10 scripts) | One-off operational scripts (DB audit, backfills, imports, cache refresh, season reset) |
| `tests/` | 129 `test_*.py` files, one per feature area; no `__init__.py`, no local `conftest.py` |
| `assets/`, `players_cache/` | Club logos, OCR crop scratch space, cached generated player cards |
| `tasks/`, `docs/`, `sandbox/` | Working plan/todo notes, `PURGE_SEASON_GUIDE.md`, and throwaway preview scripts |
| `.github/workflows/` | `tests.yml` — the pytest CI job |

`handlers/base.py` holds shared helpers, including the role checks described below.
`handlers/squad_ai.py` exists as its own module because `handlers/admin.py` already imports
from `handlers/cabinet.py` and both need it. `handlers/tracker.py` + `api/routes_tracker.py`
serve the Logovo Tracker mobile app: `/tracker` and `/app` issue a single-use 4-digit PIN,
valid 10 minutes, that the app exchanges for a session token.

### Handler registration order matters

`register_all_handlers()` in `handlers/__init__.py` registers in a deliberate sequence:

1. `global_lockdown_guard` as a `TypeHandler` at **group=-1** — runs before everything else.
2. Group-chat tracking at group=1.
3. User handlers → cabinet/admin FSM conversations → betting handlers.
4. **Last:** the catch-all text/voice `MessageHandler` that routes to the AI chat, then a
   catch-all `CallbackQueryHandler`.

Anything registered after the AI catch-all will never fire. Add new handlers *before* it.

---

## Storage rules

- `database.py` owns all SQL. Do not open connections or write queries elsewhere.
- Always go through the `transaction()` context manager. It is **re-entrant**: a nested
  call on the same thread joins the outer transaction and commits only when the outermost
  scope exits. This makes composite operations (e.g. confirm match + advance cup series)
  atomic — rely on it rather than hand-rolling multi-step commits.
- `get_connection()` sets `journal_mode=WAL`, `busy_timeout=10000`, `foreign_keys=ON`,
  and `row_factory = sqlite3.Row`. Rows are accessed by column name.
- **Every** query must be parameterized. The one intentional exception is the migration
  helper that interpolates column names from the hardcoded `SAFE_COLUMNS` tuple; it is
  annotated as such. Do not add new interpolation.
- Schema changes are additive migrations guarded by `CREATE TABLE IF NOT EXISTS` and
  `schema_migrations`. Never rewrite or drop an existing table.
- The DB file is gitignored. `league.db` is local state, never a fixture.

---

## Domain model

**Tournaments** (`tournaments`) have `type IN ('league', 'cup', 'friendly')`. Row id 1 is
seeded as the main league, «Логово Фифарей (Основная Лига)».

**Divisions** (`divisions`) partition a tournament. Each carries a unique `code` and
optional `group_chat_id` + `topic_id`, binding it to one forum topic in one Telegram
group. `services/topic_cache.py` caches this routing and is reloaded during handler
registration — call `topic_cache.reload_cache()` after mutating division topic bindings.

**League play** runs through `rounds` / `rounds_v` and `matches`. **Cup play** uses
`cup_series` (stage, series number, per-side win counts, winner, status).

**Teams** live in `users.team_name`, one club per coach. Club names are globally unique —
`idx_users_team_name_unique` enforces `UNIQUE(LOWER(TRIM(team_name)))` across all divisions,
so a name identifies a club on its own and name-keyed lookups are safe. The roster is
growing toward ~16 clubs per division across 5 divisions (~80 total).

`config.CLUBS` / `config.KPL_TEAMS` hold only the 16 clubs of the pre-division КПЛ era —
roughly one division's worth. Treat them as legacy seed data, **not** as the list of
participants; query `users` scoped by `division_id` instead.

Team-name resolution from OCR output goes through `resolve_team_name` and
`detect_teams_from_players`, both backed by **`club_registry.py`** — a pure-CPU module at
the repo root that imports `config` and nothing else. It deliberately sits *below*
`database.py`, which re-exports `resolve_team_name`, `teams_match`, `normalize_team_name`
and `TEAM_ALIASES`, so existing `database.…` call sites keep working. Never import
`database` from it.

Resolution walks EXACT → ALIAS → JOINED → PREFIX → FUZZY and stops at the first
*unambiguous* tier. A tie at any tier ends the walk with no match instead of falling
through to a weaker one. Fuzzy is guarded by `FUZZY_MIN_LEN = 5`,
`FUZZY_THRESHOLD = 0.87` and `FUZZY_MARGIN = 0.07` (the gap to the runner-up). There is no
substring tier: `Расинг` is both a club and a substring of `Расинг Ланс`, so that tier had
no safe version. `resolve_team_name` returns its input unchanged when nothing resolves;
`resolve_team_name_ex` returns `(canonical, method, confidence, candidates)` when the
caller needs to know *how* confident the answer is. Results are memoised —
`reload_registry()` is the only thing that invalidates them.

⚠️ The canonical list is `config.CLUB_REGISTRY`, and it is **still an empty stub** falling
back to `KPL_TEAMS ∪ CLUBS`. It cannot be bulk-filled yet: the live database was audited on
2026-09-12 and its roster is empty — the five divisions exist, but the previous season was
purged and no coach has registered a club since, so there are no names to harvest. The
registry therefore fills in one name at a time, as coaches join. Two consequences while it
is short: a club outside the list resolves to itself, which is safe — it is no longer coerced into a КПЛ name — but
`teams_match` will not merge an OCR typo of such a club, because a typo cannot be told
apart from a genuinely similar club without knowing the club list. Refusing to merge is
recoverable; silently merging two coaches' clubs is not.

**Adding a club to the tournament means adding its name to `CLUB_REGISTRY`.**
`python scripts/audit_team_resolution.py` reports registry↔`users.team_name` drift, name
collisions and clubs that sit too close to the fuzzy threshold; it is read-only (the
connection is closed by a SQLite authorizer) and `--emit-config` prints a ready block.

**Discipline:** unplayed matches accrue debts, tracked from `DEBT_TRACKING_START_DATETIME`.
Three job-queue tasks drive it — deadline reminders and the debt lifecycle tracker every
30 min, a debts digest to the ПРЕДЫ thread every 12 h. `MAX_WARNS_LIMIT = 4`.

`register_jobs()` in `main.py` schedules six more beyond those three, each in its own
try/except block: live provider sync (45 s), intelligence cache (5 min), the notification
queue (15 s), bet settlement (60 s), and the round preview / round digest posts to the
АНАЛИТИКА topic (10 / 15 min). Settlement in particular used to run inline on Mini App
requests — keep it off the request path.

**Betting** ("Logovo.bet") is a closed virtual-currency system: `user_wallets`,
`coin_transactions`, `markets`/`market_selections`, `user_bets`/`bet_items`, plus risk,
exposure, cashout and settlement engines. No real money is involved anywhere.

---

## Roles and access

Three distinct levels, all resolved in `handlers/base.py` — use these helpers, never
compare against `config.ADMIN_IDS` inline:

- `is_global_admin(telegram_id)` — full access, includes the `ADMIN_IDS` env list.
- `is_admin(telegram_id)` — global admins plus per-division admins (`division_admins`).
- `is_admin_user(user_id)` — the general check used by most handlers.

`config.py` re-reads `config.ADMIN_IDS` dynamically inside these helpers, so admin changes
take effect without a restart. Keep that behaviour.

**Lockdown:** `LOGOVO_LOCKDOWN=true` restricts the bot to global admins via the group=-1
guard. It is the only rollout gate — Mini App access (`api/auth.py::check_user_access`) and
the betting handlers defer to it and nothing else. That check is deliberately FAIL-CLOSED:
any internal error must reject, never allow.

---

## Conventions

- Async throughout. Never block the event loop — SQLite calls are short and synchronous by
  design, but image generation and network I/O must not stall handlers.
- Telegram messages use HTML parse mode, concise keyboards, and Retina 2x/3x Pillow output.
- User-facing strings are Russian; code, identifiers, and docstrings are English. Comments
  in existing files are mixed — match the file you are editing.
- Background jobs register in `register_jobs()` and are wrapped in try/except so one
  failing subsystem cannot take down the others.
- Commits follow Conventional Commits (`feat:`, `fix(web):`, `refactor:`, `chore(agents):`).
- OCR must stay **perceptual**: Gemini extracts what is visibly on screen — coordinates,
  text, goals, assists — and `team1`/`team2` map to screen left/right. Team identification,
  side assignment, and squad enrichment are done deterministically afterwards in Python and
  SQLite (`detect_teams_from_players`, `match_and_enrich_squad`). Never let the model infer
  squads from the database.

---

## Secrets

`.env` is gitignored and must stay that way. `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`,
`GEMINI_CHAT_API_KEY`, and `SPORTS_API_KEY` must never appear in code, logs, tests, or
commits. `.env.example` documents the variable names only. `league.db` contains real user
data and is likewise never committed.

---

## Related files

- `.agents/AGENTS.md` — the ECC agent framework's project instructions. Its project path is
  now correct, but its module map is still **stale**: it lists `ai_recognizer.py`,
  `ai_chat.py`, and `table_generator.py` at the repo root, but they now live under
  `services/ai/` and `services/graphics/`. It also names a `google-genai` dependency that
  the project does not use. Prefer this file when the two disagree.
- `.claude/prds/logovobot.prd.md` — original product brief. Its milestones still read
  "pending" although the corresponding features shipped; treat it as historical intent,
  not current status.
- `SPEC.md`, `SPEC-team-name-resolution.md` — current specs; the second is the authority on
  the resolver tiers and thresholds described above.
- `PHASE_*.md`, `FIX_0*.md`, `PRODUCTION_AUDIT.md`, `FULL_BOT_AUDIT.md`, `BUTTON_AUDIT.md`,
  `MINIAPP_ROUTE_AUDIT.md`, `Project_Audit_Report.md` — per-phase plans, test matrices,
  fix notes and final reports. Useful history for why a subsystem looks the way it does.
- `.claude/` (agents, commands, skills, prds) and `.apm/` hold agent tooling, not runtime
  code. `.claude/worktrees/` contains throwaway git worktrees.
