# League Bot (Python)

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
# optional: use PostgreSQL instead of SQLite
# DATABASE_URL=postgresql://...
```

## Railway

- `Procfile` is set to run: `worker: python league_module.py`
- In Railway, use Dockerfile builder (or let Railway auto-detect Dockerfile).

## Commands

- `/admin` — список команд
- `+ долги <url> <тур>` — загрузить долги из challenge.place (автоматически включает напоминания в 08, 12, 18 МСК)
- `+ команды\nКоманда - @username\n...` — задать привязки команд

## Challenge.place sync

`+ долги` удаляет старые долги и загружает новые. После загрузки автоматически включаются напоминания в 08:00, 12:00 и 18:00 по Москве (тег игроков с ≥2 долгами).
