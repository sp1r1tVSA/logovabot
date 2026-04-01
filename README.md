# Telegram Bot Starter

Minimal Node.js Telegram bot template based on Telegraf.

## 1) Install dependencies

```bash
npm install
```

## 2) Configure environment

Copy `.env.example` to `.env` and add your bot token:

```env
BOT_TOKEN=your_telegram_bot_token_here
```

## 3) Run bot

```bash
npm start
```

For development with auto-reload:

```bash
npm run dev
```

## Basic behavior

- `/start` - greet user
- `/help` - show commands
- `/ping` - reply with `pong`
- Any text message - echo back to user
