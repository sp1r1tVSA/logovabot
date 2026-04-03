# League Bot (Python)

Bot now runs directly from `league_module.py`.

## Run locally

```bash
pip install -r requirements.txt
python league_module.py
```

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
```

## Railway

- `Procfile` is set to run: `worker: python league_module.py`
- Remove old Node service settings if they force `npm start`.
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
- `/league_reminder_on`
- `/league_reminder_off`
- `/league_reminder_now`
- `/league_reminder_hourly_on [text]`
- `/league_reminder_hourly_off`

## Challenge.place sync

`/league_sync_challenge` выполняет разовый синк долгов из `stage_url` до указанного тура `N`.
