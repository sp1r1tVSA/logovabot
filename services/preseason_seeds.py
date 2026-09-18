"""
services/preseason_seeds.py

Предсезонный рейтинг участников — сид-сила тренера до того, как появится таблица.

Источник истины — `config.DIVISION_PLAYER_SEEDS`: по 16 записей на дивизион, от самого
слабого к самому сильному. Отсюда берётся «сила» участника в диапазоне 0.0..1.0, на
которой `services.betting_engine.select_top_round_matches` строит статусность пары и
отбирает четвёрку центральных матчей тура.

Модуль намеренно чистый: импортирует `config` и `club_registry` и больше ничего. В
частности, он **не** импортирует `database` — как и `club_registry`, он сидит ниже слоя
хранения, поэтому его можно звать из любого движка без риска циклического импорта.

Это не `services/player_rating.py`: тот считает навык прогнозиста по расчитанным ставкам,
а здесь — заданная руками стартовая сила тренера в его дивизионе.
"""

import logging

import config
from club_registry import normalize_team_name, resolve_team_name

logger = logging.getLogger(__name__)

# Участник, которого нет в рейтинге, встаёт ровно посередине: ни вверх, ни вниз.
NEUTRAL_STRENGTH = 0.5

# Логин в рейтинге пишется с «@», имя клуба — без. Разделение полное, поэтому
# гадать по содержимому строки не нужно.
_USERNAME_MARK = "@"


# Индексы строятся при импорте; `reload_seeds()` — единственное, что их меняет.
_username_strength: dict[str, float] = {}
_club_strength: dict[str, float] = {}
_username_rank: dict[str, int] = {}
_club_rank: dict[str, int] = {}
_division_index: dict[str, list[str]] = {}
_unknown_clubs: tuple[str, ...] = ()
_duplicate_keys: tuple[str, ...] = ()


def normalize_username(name: str | None) -> str:
    """Логин в форме, пригодной для сравнения: без «@», в нижнем регистре.

    Telegram отдаёт `user.username` без «@» и считает логины регистронезависимыми,
    поэтому обе формы — и из конфига, и из `users.username` — сводятся сюда.
    """
    if not name:
        return ""
    return name.strip().lstrip(_USERNAME_MARK).lower()


def _club_key(name: str | None) -> str:
    """Ключ клуба: канон через резолвер, затем нормализация реестра.

    Через `resolve_team_name` — чтобы алиас или форма из OCR («Кёльн», «Koln»)
    приходили к тому же ключу, что и каноническое имя из `DIVISION_CLUBS`.
    """
    if not name:
        return ""
    return normalize_team_name(resolve_team_name(name))


def _division_club_keys(division_code: str) -> set[str]:
    """Нормализованные имена клубов дивизиона — для проверки клубных записей."""
    return {
        _club_key(club)
        for club in config.DIVISION_CLUBS.get(division_code, [])
        if club
    }


def reload_seeds() -> int:
    """Пересобрать индексы рейтинга из конфига. Возвращает число загруженных записей.

    Вызывается явно после правки `DIVISION_PLAYER_SEEDS` — как
    `club_registry.reload_registry()`, чтобы поиск оставался без I/O и без
    внезапных пересборок внутри горячих циклов.
    """
    global _username_strength, _club_strength, _username_rank, _club_rank
    global _division_index, _unknown_clubs, _duplicate_keys

    username_strength: dict[str, float] = {}
    club_strength: dict[str, float] = {}
    username_rank: dict[str, int] = {}
    club_rank: dict[str, int] = {}
    division_index: dict[str, list[str]] = {}
    unknown: list[str] = []
    duplicates: list[str] = []
    loaded = 0

    seeds = getattr(config, "DIVISION_PLAYER_SEEDS", None) or {}
    for division_code, entries in seeds.items():
        code = (division_code or "").strip().upper()
        entries = [e for e in (entries or []) if isinstance(e, str) and e.strip()]
        division_index[code] = list(entries)
        if not entries:
            continue

        # Сила растянута на весь диапазон: слабейший 0.0, сильнейший 1.0. Шкала
        # нормирована именно поэтому — её смешивают с местом в таблице, а число
        # участников в дивизионе может отличаться от шестнадцати.
        span = len(entries) - 1
        division_clubs = _division_club_keys(code)

        for position, entry in enumerate(entries):
            rank = position + 1
            strength = (position / span) if span else NEUTRAL_STRENGTH
            raw = entry.strip()

            if raw.startswith(_USERNAME_MARK):
                key = normalize_username(raw)
                if not key:
                    continue
                if key in username_strength:
                    duplicates.append(raw)
                    continue
                username_strength[key] = strength
                username_rank[key] = rank
            else:
                key = _club_key(raw)
                if not key:
                    continue
                # Клубная запись обязана указывать на клуб СВОЕГО дивизиона: иначе
                # это опечатка, и молча дать ей силу — значит поднять или утопить
                # чужого участника.
                if key not in division_clubs:
                    unknown.append(f"{code}: {raw}")
                    continue
                if key in club_strength:
                    duplicates.append(raw)
                    continue
                club_strength[key] = strength
                club_rank[key] = rank
            loaded += 1

    _username_strength = username_strength
    _club_strength = club_strength
    _username_rank = username_rank
    _club_rank = club_rank
    _division_index = division_index
    _unknown_clubs = tuple(unknown)
    _duplicate_keys = tuple(duplicates)

    for entry in _unknown_clubs:
        logger.warning("Seed entry %r is not a club of that division and is ignored", entry)
    for entry in _duplicate_keys:
        logger.warning("Seed entry %r is a duplicate and is ignored", entry)

    return loaded


def get_seed_strength(username: str | None, team_name: str | None = None) -> float | None:
    """Сила участника в 0.0..1.0 по предсезонному рейтингу; None — записи нет.

    Логин — основной ключ: он переживает смену клуба. Имя клуба — запасной, им
    опознаются участники без телеграм-тега.
    """
    key = normalize_username(username)
    if key and key in _username_strength:
        return _username_strength[key]

    club = _club_key(team_name)
    if club and club in _club_strength:
        return _club_strength[club]

    return None


def get_seed_rank(username: str | None, team_name: str | None = None) -> int | None:
    """Место участника в рейтинге дивизиона (1 — слабейший); None — записи нет."""
    key = normalize_username(username)
    if key and key in _username_rank:
        return _username_rank[key]

    club = _club_key(team_name)
    if club and club in _club_rank:
        return _club_rank[club]

    return None


def get_seed_division_index() -> dict[str, list[str]]:
    """Код дивизиона -> список записей рейтинга в исходном порядке."""
    return _division_index


def get_seed_usernames() -> tuple[str, ...]:
    """Все логины рейтинга в нормализованной форме — для сверки с users.username."""
    return tuple(_username_strength)


def get_unknown_seed_clubs() -> tuple[str, ...]:
    """Клубные записи, не совпавшие ни с одним клубом своего дивизиона."""
    return _unknown_clubs


def get_duplicate_seed_keys() -> tuple[str, ...]:
    """Записи, встретившиеся в рейтинге дважды: вторая игнорируется."""
    return _duplicate_keys


reload_seeds()
