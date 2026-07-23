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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash-lite").strip()

def _get_group_id() -> int | None:
    group_raw = os.getenv("TELEGRAM_GROUP_ID", "").strip()
    if not group_raw:
        return None
    try:
        return int(group_raw)
    except ValueError:
        return None

GROUP_ID = _get_group_id()

CLUBS = [
    "Спортинг",
    "Ривер Плейт",
    "Бока Хуниорс",
    "Бенфика",
    "ПСВ",
    "Порту",
    "Будë Глимт",
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

