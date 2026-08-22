# Logovobot — Agent Instructions & Architecture Guidelines

This repository contains **Logovobot** (Логово Фифарей / ИИ «Темшик») — a high-performance, asynchronous Telegram bot for managing FIFA/FC e-sports championships, cups, match drafts, automated Gemini AI OCR vision processing, SQLite statistics, and graphics generation.

**Project Path:** `c:\Users\Илез\Desktop\logovobot`  
**Stack:** Python 3.11+, `python-telegram-bot` v21 (async), SQLite (WAL mode, parameterized transactions), Google Gemini AI OCR (`google-genai` / `google-generativeai`), Pillow (Retina 2x/3x graphics rendering), APScheduler / JobQueue.

---

## Core Principles

1. **Agent-First** — Delegate complex domain tasks to specialized roles (`planner`, `python-reviewer`, `database-reviewer`, `security-reviewer`, `tdd-guide`).
2. **Deterministic Data Integrity** — Always use the `transaction()` context manager in `database.py`. All SQL queries MUST be parameterized. Never mutate database records outside verified repository functions.
3. **Pure OCR & Deterministic Enrichment** — Keep AI Vision OCR strictly perceptual (extracting coordinates, text, goals, and assists without hallucinating database squads). Perform team detection, side assignment, and squad enrichment deterministically in Python/SQLite (`detect_teams_from_players`, `match_and_enrich_squad`).
4. **Security-First** — Never commit `.env`, `league.db`, or expose Telegram tokens / Gemini API keys.
5. **High UI/UX Quality** — Telegram messages must use clean HTML formatting, concise keyboards, and stunning Pillow infographics.

---

## Project Architecture & Module Map

| Module | Responsibility & Rules |
|--------|------------------------|
| `main.py` | Bot entrypoint, ApplicationBuilder, Handler registration, `post_init` background jobs setup. |
| `config.py` | Environment variables, Telegram `TOKEN`, `GEMINI_API_KEY`, Admin IDs, chat/topic IDs, DB path. |
| `database.py` | Thread-safe SQLite repository layer, schema migrations, team resolution (`resolve_team_name`, `detect_teams_from_players`), debt tracking, tournament standings. |
| `ai_recognizer.py` | Gemini 2.5 Flash / 1.5 Flash Vision OCR. Strictly maps `team1` to screen left and `team2` to screen right. Handles multi-screenshot matching (timeline + table). |
| `ai_chat.py` | AI assistant «Темшик» persona for chat discussions, регламент, and match predictions. |
| `table_generator.py` | Pillow-based rendering of standings, retina 2x/3x graphics, cards, and top scorers/assisters. |
| `handlers/drafts.py` | Group topic match draft processing, photo debouncing, OCR triggering, squad matching, team/tour auto-detection, admin interactive approval. |
| `handlers/cabinet.py` | Private message player cabinet, match reporting, player registration, stats cards, squad photo upload. |
| `handlers/admin.py` | League & Cup administration, tech defeats/draws, round lifecycle (open/close), deadlines, squad management, broadcast. |
| `handlers/cup.py` | Cup series, playoff brackets, stage progression, series game tracking. |
| `handlers/common.py` | Shared command handlers, help, rules, and global error handlers. |

---

## Engineering Rules & Invariants

### 1. Database & Transactions (`database.py`)
- Always execute SQLite operations inside `with transaction() as conn:` blocks.
- Enable WAL mode (`PRAGMA journal_mode=WAL;`).
- Never perform string concatenation in SQL queries — always use `?` placeholders.
- When matching team names, use `resolve_team_name()` and `normalize_team_name()` to handle aliases, typos, and transliteration.

### 2. Vision OCR & Drafts Pipeline (`ai_recognizer.py` + `handlers/drafts.py`)
- **No Squad Hints in Gemini Prompt**: Gemini Vision must perform pure optical text extraction from screenshots. Do not pass DB squads into the AI prompt to prevent team hallucination.
- **Side Stability**: `team1` is always left side on-screen, `team2` is always right side.
- **Team & Round Detection**: Use `database.detect_teams_from_players()` to match extracted player names against `squad_players` in SQLite, determining left and right clubs and the target round automatically.
- **Preview Non-Mutation**: Previewing a match draft must NEVER insert unrecognized players into `squad_players` automatically.

### 3. Telegram Bot Handlers (`handlers/`)
- Always use asynchronous handlers with `async def` and `await asyncio.to_thread(...)` for CPU-bound or database operations.
- Handle Telegram API rate limits gracefully (`telegram.error.RetryAfter`).
- Escape user input in HTML parse mode using `html.escape()`.

### 4. Git & Commit Workflow
- Commit format: `<type>: <description>` (`feat`, `fix`, `refactor`, `docs`, `chore`, `perf`).
- Remote: `https://github.com/sp1r1tVSA/logovabot.git`.

---

## Workspace Project Isolation

- **Primary Project**: `c:\Users\Илез\Desktop\logovobot` (Python 3.11+, Telegram Bot, Gemini AI, SQLite).
- Confine all edits, test runs, and context reads strictly to this repository.
- Never modify or expose secrets in `.env` or `league.db`.
