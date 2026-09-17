import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

# Load env variables from project root
load_dotenv(PROJECT_ROOT / ".env")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

def _get_admin_ids() -> list[int]:
    admins_raw = os.getenv("ADMIN_IDS", "")
    ids = []
    for x in admins_raw.split(","):
        x = x.strip()
        if x.isdigit():
            ids.append(int(x))
    return ids

ADMIN_IDS = _get_admin_ids()
_env_db_path = os.getenv("LEAGUE_SQLITE_PATH", "league.db")
DB_PATH = str(PROJECT_ROOT / _env_db_path) if not os.path.isabs(_env_db_path) else _env_db_path
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
def _get_gemini_chat_keys() -> list[str]:
    keys_raw = os.getenv("GEMINI_CHAT_API_KEY", "")
    return [k.strip() for k in keys_raw.split(",") if k.strip()]

GEMINI_CHAT_API_KEYS = _get_gemini_chat_keys()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()
# ─── Phase 8: Real Sports Provider Configuration ──────────────────────────────
SPORTS_PROVIDER = os.getenv("SPORTS_PROVIDER", "auto").strip()
SPORTS_API_KEY = os.getenv("SPORTS_API_KEY", os.getenv("APISPORTS_KEY", "")).strip()
APISPORTS_KEY = SPORTS_API_KEY  # Backward compatibility
SPORTS_API_BASE_URL = os.getenv("SPORTS_API_BASE_URL", "https://v3.football.api-sports.io").strip()
SPORTS_TIMEOUT_SECONDS = float(os.getenv("SPORTS_TIMEOUT_SECONDS", "10.0"))
SPORTS_CACHE_TTL_SECONDS = int(os.getenv("SPORTS_CACHE_TTL_SECONDS", "30"))
SPORTS_LIVE_POLL_SECONDS = int(os.getenv("SPORTS_LIVE_POLL_SECONDS", "15"))
SPORTS_MAX_RETRIES = int(os.getenv("SPORTS_MAX_RETRIES", "3"))
SPORTS_RATE_LIMIT_RPM = int(os.getenv("SPORTS_RATE_LIMIT_RPM", "60"))

# Stale data protection thresholds
LIVE_DATA_STALE_AFTER_SECONDS = int(os.getenv("LIVE_DATA_STALE_AFTER_SECONDS", "120"))
LIVE_DATA_EXPIRED_AFTER_SECONDS = int(os.getenv("LIVE_DATA_EXPIRED_AFTER_SECONDS", "300"))

def _get_group_id() -> int | None:
    group_raw = os.getenv("TELEGRAM_GROUP_ID", "").strip()
    if not group_raw:
        return None
    try:
        return int(group_raw)
    except ValueError:
        return None

GROUP_ID = _get_group_id()

MAX_WARNS_LIMIT = 4

# Сколько туров дивизиона могут быть открыты одновременно. «Одновременно» здесь
# считается строго по дедлайну: тур занимает слот, пока `deadline > now()`, и
# освобождает его сам, без ручного закрытия админом (`is_open` остаётся 1).
# Держит конвейер линии Logovo.bet «два через два»: на два текущих тура ставки
# закрыты, на два следующих выставляется ранняя линия.
MAX_OPEN_ROUNDS_PER_DIVISION: int = 2

# Logovo.bet: стартовый баланс нового кошелька (🪙). Единственный источник истины —
# схема user_wallets.balance, get_or_create_wallet() и приветственный бонус
# coin_transactions('welcome_bonus') берут сумму отсюда.
INITIAL_WALLET_BALANCE = 677

# Debt tracking and auto-warn activation threshold
# No auto-warns will be issued before this datetime.
# All previous round deadlines will be counted starting from this datetime.
DEBT_TRACKING_START_DATETIME = os.getenv("DEBT_TRACKING_START_DATETIME", "22.08.2026 00:00").strip()

KPL_TEAMS = [
    "Расинг", "Брага", "Бенфика", "АЕК", "Аякс", "ПСВ", "Фейеноорд", 
    "Будё Глимт", "Порту", "Спортинг", "Копенгаген", "Рейнджерс", 
    "Бока Хуниорс", "Селтик", "Брюгге", "Ривер Плейт"
]

CLUBS = [
    "Спортинг",
    "Ривер Плейт",
    "Бока Хуниорс",
    "Бенфика",
    "ПСВ",
    "Порту",
    "Будё Глимт",
    "Фейеноорд",
    "Селтик",
    "Расинг",
    "Аякс",
    "Брага",
    "Рейнджерс",
    "Брюгге",
    "Копенгаген",
    "АЕК"
]

# Канонические имена всех клубов лиги — источник истины для club_registry.resolve_team_name.
# KPL_TEAMS/CLUBS выше остаются легаси-сидом эпохи до дивизионов (16 клубов) и списком
# участников НЕ являются.
#
# TODO(P3-7): заполнить реальными ~80 клубами. Список печатает
#   python scripts/audit_team_resolution.py --emit-config
# на боевой БД: локальная league.db пуста, взять имена неоткуда. Пока список пуст,
# реестр падает обратно на KPL_TEAMS ∪ CLUBS, то есть работает как прежде.
# Каждый новый клуб обязан попадать сюда, иначе он схлопнется в чужой канон.
CLUB_REGISTRY: list[str] = []

MAX_MATCH_GOALS = 50

# Telegram Mini App Configuration
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8080").strip()
API_PORT = int(os.getenv("API_PORT", "8080"))
API_HOST = os.getenv("API_HOST", "0.0.0.0").strip()

# Global Lockdown Mode: true = accessible only to Global Admins; false = regular operation
def is_global_lockdown_enabled() -> bool:
    """Return True if global lockdown mode is enabled via LOGOVO_LOCKDOWN environment variable."""
    return os.getenv("LOGOVO_LOCKDOWN", "false").strip().lower() in ("true", "1", "yes")

is_lockdown_enabled = is_global_lockdown_enabled
LOGOVO_LOCKDOWN = is_global_lockdown_enabled()


def is_dev_auth_bypass_enabled() -> bool:
    """
    Разрешён ли обход валидации initData («mock_admin_<id>») для локальной отладки.

    Читается динамически, как и lockdown: тесты и локальный запуск меняют флаг
    без перезапуска процесса. В продакшене переменная не выставляется никогда.
    """
    return os.getenv("ALLOW_DEV_AUTH_BYPASS", "").strip().lower() in ("1", "true", "yes")


# Mini App API: защита от флуда и спам-атак.
# Идентификация по user_id из валидированного initData, для анонимных — по IP.
API_RATE_LIMIT_ENABLED = os.getenv("API_RATE_LIMIT_ENABLED", "true").strip().lower() in ("true", "1", "yes")
API_RATE_LIMIT_READ_RPM = int(os.getenv("API_RATE_LIMIT_READ_RPM", "60"))
API_RATE_LIMIT_WRITE_RPM = int(os.getenv("API_RATE_LIMIT_WRITE_RPM", "20"))
API_RATE_LIMIT_ADMIN_RPM = int(os.getenv("API_RATE_LIMIT_ADMIN_RPM", "120"))
API_RATE_LIMIT_ANON_RPM = int(os.getenv("API_RATE_LIMIT_ANON_RPM", "30"))
# Минимальный интервал между двумя чувствительными мутациями одного пользователя.
API_SENSITIVE_MIN_INTERVAL = float(os.getenv("API_SENSITIVE_MIN_INTERVAL", "2.0"))

# X-Forwarded-For подделывается кем угодно, если сервер смотрит в интернет напрямую,
# поэтому доверяем заголовку только при явном включении (за nginx/Cloudflare).
API_TRUST_PROXY_HEADERS = os.getenv("API_TRUST_PROXY_HEADERS", "false").strip().lower() in ("true", "1", "yes")


# Logovo Tracker: мобильное приложение live-трансляции матчей (api/routes_tracker.py).
# Одноразовый ПИН из бота живёт 10 минут — столько нужно, чтобы дойти до телефона.
TRACKER_PIN_TTL_SECONDS = int(os.getenv("TRACKER_PIN_TTL_SECONDS", "600"))
# Сессия устройства протухает после суток без запросов: матч длится минуты,
# а забытый на чужом телефоне токен — нет.
TRACKER_SESSION_TTL_SECONDS = int(os.getenv("TRACKER_SESSION_TTL_SECONDS", "86400"))
# Приложение шлёт тики каждые несколько секунд, поэтому обычный write-лимит
# (API_RATE_LIMIT_WRITE_RPM) ему не подходит — у трекера свой бюджет.
API_RATE_LIMIT_TRACKER_RPM = int(os.getenv("API_RATE_LIMIT_TRACKER_RPM", "180"))
# Кадр плашки события: 2 МБ с запасом хватает на скриншот телефона в JPEG.
TRACKER_MAX_SCREENSHOT_BYTES = int(os.getenv("TRACKER_MAX_SCREENSHOT_BYTES", str(2 * 1024 * 1024)))
# Распознавание фамилии с кадра — необязательный шаг, его можно выключить.
TRACKER_OCR_ENABLED = os.getenv("TRACKER_OCR_ENABLED", "true").strip().lower() in ("true", "1", "yes")

