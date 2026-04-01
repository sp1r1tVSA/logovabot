# Telegram League Bot

Node.js Telegram bot on Telegraf with league debt sync from challenge.place and reminders.

## 1) Install dependencies

```bash
npm install
```

## 2) Configure environment

Create `.env`:

```env
BOT_TOKEN=your_telegram_bot_token_here
# or
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
ADMIN_IDS=123456789,987654321
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

## 3) Run bot

```bash
npm start
```

## League commands

- `/league_map_bulk [список]` - full replace by lines `Команда - @username`
- `/league_map_show` - show team map
- `/league_map_clear` - clear team map
- `/league_sync_challenge [url] [N]` - sync debts from challenge.place to round N
- `/league_sync_now [N]` - resync from saved source
- `/league_sync_off` - disable saved source
- `/league_debts_show` - debts summary by players
- `/league_debts_round [N]` - debts for one round
- `/league_reminder_on` - enable daily reminders (09:00, 15:00, 20:00 MSK)
- `/league_reminder_off` - disable daily reminders
- `/league_reminder_now` - send reminder immediately
- `/league_reminder_hourly_on [text]` - enable hourly reminder at `:00`
- `/league_reminder_hourly_off` - disable hourly reminder

## Notes

- Reminder and auto-post chat is saved from the chat where admin runs `/league_reminder_on` and sync commands.
- Database tables are created automatically on startup.
