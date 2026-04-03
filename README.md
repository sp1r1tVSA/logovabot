# League Bot (Python)

Bot now runs directly from `league_module.py`.

## Run locally

```bash
pip install -r requirements.txt
python league_module.py
```

For OCR, install system Tesseract binary:

- Ubuntu/Debian: `sudo apt-get update && sudo apt-get install -y tesseract-ocr tesseract-ocr-rus`
- Windows: install Tesseract and optionally set `TESSERACT_CMD` in `.env`

## Environment

Create `.env` with:

```env
BOT_TOKEN=your_telegram_bot_token_here
# or
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
ADMIN_IDS=123456789,987654321
LEAGUE_SQLITE_PATH=league.db
# optional: if set, PostgreSQL is used instead of SQLite
DATABASE_URL=postgresql://...
# OCR provider settings
OCR_PROVIDER=ocrspace
OCRSPACE_API_KEY=your_ocrspace_key
OCR_TIMEOUT_SEC=20
```

## Railway

- `Procfile` is set to run: `worker: python league_module.py`
- Remove old Node service settings if they force `npm start`.
- `Dockerfile` installs system OCR packages: `tesseract-ocr`, `tesseract-ocr-rus`.
- In Railway, use Dockerfile builder (or let Railway auto-detect Dockerfile).

## Behavior

- Bot handles only league commands.
- In groups/supergroups: bot works for commands.
- In private chat: bot works only for users from `ADMIN_IDS`.
- For non-admin private users: bot is silent.

## Commands

- `/league_debts_show`
- `/league_debts_round [N]`
- `/league_map_bulk [list]`
- `/league_map_show`
- `/league_map_clear`
- `/league_sync_challenge [url] [N]`
- `/league_sync_now [N]`
- `/league_sync_off`
- `/league_reminder_on`
- `/league_reminder_off`
- `/league_reminder_now`
- `/league_reminder_hourly_on [text]`
- `/league_reminder_hourly_off`
- `/league_ocr_fix [id]` (players/admins)
- `/league_ocr_show [id]` (admins)
- `/league_ocr_approve [id]` (admins)
- `/league_ocr_reject [id] [reason]` (admins)
- `/league_apply_result [id] [--dry-run] [--force]` (admins)

Text command for players:

- `исправь [id]` (or reply to draft)

## Challenge session auth (Playwright)

One-time setup:

```bash
pip install -r requirements.txt
playwright install chromium
```

Save authenticated session:

```bash
python scripts/cp_auth_session.py
```

Output file: `state/challenge_storage_state.json`

Optional selector tuning (if Challenge UI changes):

- `CHALLENGE_EDIT_SELECTORS`
- `CHALLENGE_DATE_SELECTORS`
- `CHALLENGE_HOME_SCORE_SELECTORS`
- `CHALLENGE_AWAY_SCORE_SELECTORS`
- `CHALLENGE_HOME_GOAL_SELECTORS`
- `CHALLENGE_AWAY_GOAL_SELECTORS`
- `CHALLENGE_HOME_ASSIST_SELECTORS`
- `CHALLENGE_AWAY_ASSIST_SELECTORS`
- `CHALLENGE_SAVE_SELECTORS`
- `CHALLENGE_SUCCESS_SELECTORS`

Use `|` between selectors in env values.

## OCR drafts from screenshots

OCR now uses Tesseract (`pytesseract`) instead of EasyOCR/Torch.
If Tesseract binary is missing in the runtime, OCR commands will reply that OCR is unavailable.

- Send a screenshot as photo in group chat.
- Add caption with teams and optional assists, for example:

```text
Брюге - Селтик
Ассисты Брюге:
De Bruyne; Foden
Ассисты Селтика:
Maeda
```

- Bot creates draft `#N` per chat and status `pending_admin_review`.
- If goal side color is not detected, event is marked as unknown and should be fixed via `/league_ocr_fix` or `исправь`.
- Any OCR draft must be confirmed by admin via `/league_ocr_approve`.
