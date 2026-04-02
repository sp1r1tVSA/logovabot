# League Bot (Python)

Bot now runs directly from `league_module.py`.

## Run locally

```bash
pip install -r requirements.txt
python league_module.py
```

Optional OCR dependencies (heavy):

```bash
pip install -r requirements-ocr.txt
```

## Environment

Create `.env` with:

```env
BOT_TOKEN=your_telegram_bot_token_here
# or
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
ADMIN_IDS=123456789,987654321
LEAGUE_SQLITE_PATH=league.db
```

## Railway

- `Procfile` is set to run: `worker: python league_module.py`
- Remove old Node service settings if they force `npm start`.

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

## OCR drafts from screenshots

Note: OCR packages are intentionally moved to `requirements-ocr.txt` because they significantly increase container size.
Without these packages, OCR commands will reply that OCR is unavailable.

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
