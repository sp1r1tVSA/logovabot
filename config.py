import os
from dotenv import load_dotenv

# Load env variables
load_dotenv()

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
DB_PATH = os.getenv("LEAGUE_SQLITE_PATH", "league.db")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
def _get_gemini_chat_keys() -> list[str]:
    keys_raw = os.getenv("GEMINI_CHAT_API_KEY", "")
    return [k.strip() for k in keys_raw.split(",") if k.strip()]

GEMINI_CHAT_API_KEYS = _get_gemini_chat_keys()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "").strip()

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

MAX_MATCH_GOALS = 50


