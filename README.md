# League Bot (Python)

A Telegram Bot for tournament league management, rewritten from scratch.

## Project Structure

- `main.py` — Telegram Bot entry point and handler registration.
- `database.py` — Database connection initialization and transactional scope helpers.
- `requirements.txt` — Project dependencies.

## Local Development

### 1. Installation

Create a virtual environment and install the required dependencies:

```bash
python -m venv venv
venv\Scripts\activate   # Windows
source venv/bin/activate # Unix/macOS
pip install -r requirements.txt
```

### 2. Environment Configuration

Create a `.env` file in the root directory (see `.env.example`):

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
ADMIN_IDS=123456789,987654321
LEAGUE_SQLITE_PATH=league.db
```

### 3. Run

Start the bot locally:

```bash
python main.py
```

## Production Deployment

This project contains a `Dockerfile` and `Procfile` configured for easy deployment to container hosting platforms (e.g. Railway).
