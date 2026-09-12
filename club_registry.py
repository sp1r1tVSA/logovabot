"""Club name registry and name resolution.

Canonical club names, the alias dictionary and fuzzy resolution live here rather
than in database.py: none of this touches SQL, and keeping it in the storage layer
forced every consumer of the registry to drag the database module along with it
(audit item P3-7).

Layering rule: this module sits BELOW database.py and must never import it.
It depends on config only, so importing it can never create a cycle.
"""
import difflib
import logging
import re

logger = logging.getLogger(__name__)


TEAM_ALIASES = {
    # Расинг
    "расинг": "Расинг", "расинг клаб": "Расинг", "расинг клуб": "Расинг", "расинга": "Расинг",
    "racing": "Расинг", "racing club": "Расинг", "rcing": "Расинг",
    
    # Брага
    "брага": "Брага", "брагу": "Брага", "браге": "Брага", "браги": "Брага",
    "braga": "Брага", "sc braga": "Брага", "сп брага": "Брага", "сц брага": "Брага",
    
    # Бенфика
    "бенфика": "Бенфика", "бенфику": "Бенфика", "бенфике": "Бенфика", "бенфики": "Бенфика", "бенфа": "Бенфика",
    "benfica": "Бенфика", "sl benfica": "Бенфика", "бенфика лиссабон": "Бенфика",
    
    # АЕК
    "аек": "АЕК", "аека": "АЕК", "аеку": "АЕК", "аек афины": "АЕК",
    "aek": "АЕК", "aek athens": "АЕК",
    
    # Аякс
    "аякс": "Аякс", "аякса": "Аякс", "аяксу": "Аякс", "аяксе": "Аякс",
    "ajax": "Аякс", "afc ajax": "Аякс",
    
    # ПСВ
    "псв": "ПСВ", "псв эйндховен": "ПСВ",
    "psv": "ПСВ", "psv eindhoven": "ПСВ",
    
    # Фейеноорд
    "фейеноорд": "Фейеноорд", "фейенорд": "Фейеноорд", "фейноорд": "Фейеноорд", "фейнорд": "Фейеноорд",
    "фейе": "Фейеноорд", "фейеноорда": "Фейеноорд", "фейенорда": "Фейеноорд",
    "feyenoord": "Фейеноорд", "feyenoor": "Фейеноорд", "feyenord": "Фейеноорд",
    
    # Будё Глимт
    "будё глимт": "Будё Глимт", "буде глимт": "Будё Глимт", "будë глимт": "Будё Глимт",
    "буде-глимт": "Будё Глимт", "будё-глимт": "Будё Глимт", "будеглимт": "Будё Глимт", "будёглимт": "Будё Глимт",
    "буде": "Будё Глимт", "будё": "Будё Глимт", "будë": "Будё Глимт", "глимт": "Будё Глимт",
    "bodo glimt": "Будё Глимт", "bodø glimt": "Будё Глимт", "bodo/glimt": "Будё Глимт", "bodø/glimt": "Будё Глимт",
    "bodo": "Будё Глимт", "glimt": "Будё Глимт", "bodoe glimt": "Будё Глимт",
    
    # Порту
    "порту": "Порту", "порто": "Порту", "порт": "Порту", "португал": "Порту",
    "porto": "Порту", "portu": "Порту", "fc porto": "Порту", "фк порту": "Порту", "фк порто": "Порту",
    
    # Спортинг
    "спортинг": "Спортинг", "спортнг": "Спортинг", "спортинга": "Спортинг", "спорт": "Спортинг",
    "sporting": "Спортинг", "sporting cp": "Спортинг", "спортинг лиссабон": "Спортинг",
    
    # Копенгаген
    "копенгаген": "Копенгаген", "копен": "Копенгаген", "копенгагн": "Копенгаген", "копенгагена": "Копенгаген",
    "copenhagen": "Копенгаген", "kobenhavn": "Копенгаген", "fc kobenhavn": "Копенгаген", "фк копенгаген": "Копенгаген",
    
    # Рейнджерс
    "рейнджерс": "Рейнджерс", "рейнджер": "Рейнджерс", "рейнджерсы": "Рейнджерс", "ренджерс": "Рейнджерс", "ренджер": "Рейнджерс",
    "рейнджерса": "Рейнджерс", "rangers": "Рейнджерс", "glasgow rangers": "Рейнджерс", "рейнджерс глазго": "Рейнджерс",
    
    # Бока Хуниорс
    "бока хуниорс": "Бока Хуниорс", "бока": "Бока Хуниорс", "боку": "Бока Хуниорс", "боке": "Бока Хуниорс", "хуниорс": "Бока Хуниорс",
    "boca juniors": "Бока Хуниорс", "boca": "Бока Хуниорс", "boca jrs": "Бока Хуниорс",
    
    # Селтик
    "селтик": "Селтик", "кельтик": "Селтик", "селтика": "Селтик", "селтику": "Селтик",
    "celtic": "Селтик", "celtic fc": "Селтик",
    
    # Брюгге
    "брюгге": "Брюгге", "брюге": "Брюгге", "брюгг": "Брюгге", "брюг": "Брюгге", "брюгге фк": "Брюгге",
    "brugge": "Брюгге", "club brugge": "Брюгге", "клуб брюгге": "Брюгге",
    
    # Ривер Плейт
    "ривер плейт": "Ривер Плейт", "ривер": "Ривер Плейт", "плейт": "Ривер Плейт", "ривера": "Ривер Плейт",
    "river plate": "Ривер Плейт", "river": "Ривер Плейт",
}

def normalize_team_name(name: str | None) -> str:
    """Normalize team name for fuzzy matching (handles ё/е, latin ë, hyphens, slashes, extra spaces)."""
    if not name:
        return ""
    s = str(name).lower()
    # Replace variants of 'ё', latin 'ë' (\u00eb), 'ø', 'ö'
    s = s.replace("ё", "е").replace("\u00eb", "е").replace("ø", "o").replace("ö", "o")
    # Replace punctuation and separators
    s = re.sub(r"[\-_/\\.,]", " ", s)
    # Collapse multiple spaces
    s = re.sub(r"\s+", " ", s).strip()
    return s


def resolve_team_name(name: str | None) -> str:
    """Intelligently resolve any user-entered team name, typo, alias, or transliteration to canonical KPL team name."""
    if not name:
        return ""
    
    raw = str(name).strip()
    norm = normalize_team_name(raw)
    if not norm:
        return raw

    # 1. Direct alias dictionary lookup
    if norm in TEAM_ALIASES:
        return TEAM_ALIASES[norm]

    # 2. Check tokens / joined words
    tokens = norm.split()
    if len(tokens) > 1:
        joined = "".join(tokens)
        if joined in TEAM_ALIASES:
            return TEAM_ALIASES[joined]

    # 3. Check exact match against canonical KPL_TEAMS in config
    import config
    all_canon = getattr(config, "KPL_TEAMS", [])
    for canon in all_canon:
        c_norm = normalize_team_name(canon)
        if norm == c_norm:
            return canon

    # 4. Prefix / Substring match against aliases
    for alias, canon in TEAM_ALIASES.items():
        a_norm = normalize_team_name(alias)
        if len(norm) >= 3 and (norm == a_norm or (len(a_norm) >= 4 and (norm in a_norm or a_norm in norm))):
            return canon

    # 5. Fuzzy string similarity using difflib
    best_match = None
    best_score = 0.0

    for alias, canon in TEAM_ALIASES.items():
        score = difflib.SequenceMatcher(None, norm, normalize_team_name(alias)).ratio()
        if score > best_score:
            best_score = score
            best_match = canon

    for canon in all_canon:
        score = difflib.SequenceMatcher(None, norm, normalize_team_name(canon)).ratio()
        if score > best_score:
            best_score = score
            best_match = canon

    if best_match and best_score >= 0.65:
        return best_match

    return raw


def teams_match(team_a: str | None, team_b: str | None) -> bool:
    """Check if two team names refer to the same team (smart fuzzy/normalized match)."""
    if not team_a or not team_b:
        return False

    res_a = resolve_team_name(team_a)
    res_b = resolve_team_name(team_b)
    if res_a and res_b and res_a.lower() == res_b.lower():
        return True

    # Two distinct canonical clubs must never be conflated by fuzzy matching
    # (e.g. "Атлетико" vs "Атлетик" score ~0.93 on plain string similarity).
    try:
        from config import CLUBS as _KPL_CLUBS
        canon = {normalize_team_name(c) for c in (_KPL_CLUBS or []) if isinstance(c, str)}
        a_c = normalize_team_name(team_a)
        b_c = normalize_team_name(team_b)
        if canon and a_c in canon and b_c in canon and a_c != b_c:
            return False
    except Exception:
        pass

    a_norm = normalize_team_name(team_a)
    b_norm = normalize_team_name(team_b)
    if not a_norm or not b_norm:
        return False
    if a_norm == b_norm or a_norm in b_norm or b_norm in a_norm:
        return True

    # Fuzzy ratio check (raised to 0.85 so similar-but-distinct clubs like
    # "Атлетик"/"Атлетико" are not matched).
    if difflib.SequenceMatcher(None, a_norm, b_norm).ratio() >= 0.85:
        return True
        
    # Word-level match
    a_words = [w for w in a_norm.split() if len(w) > 2]
    b_words = [w for w in b_norm.split() if len(w) > 2]
    if a_words and b_words:
        if all(any(aw in bw or bw in aw for bw in b_words) for aw in a_words):
            return True
        if all(any(bw in aw or aw in bw for aw in a_words) for bw in b_words):
            return True
    return False


# ---------------------------------------------------------------------------
# Club registry
#
# The canonical set of club names. Historically this was config.KPL_TEAMS — the
# 16 clubs of the pre-division era — which is why clubs outside those 16 used to
# collapse into each other. The registry is the single source of truth now.
# ---------------------------------------------------------------------------

_registry: tuple[str, ...] = ()
_registry_index: dict[str, str] = {}
_dropped_aliases: tuple[tuple[str, str, str], ...] = ()


def _load_canonical_names() -> tuple[str, ...]:
    """Read the canonical club list from config, newest setting first."""
    import config

    names = getattr(config, "CLUB_REGISTRY", None)
    if not names:
        # Legacy seed data: the 16 pre-division clubs.
        names = list(getattr(config, "KPL_TEAMS", [])) + list(getattr(config, "CLUBS", []))

    seen: dict[str, str] = {}
    for name in names:
        if not isinstance(name, str) or not name.strip():
            continue
        norm = normalize_team_name(name)
        if norm and norm not in seen:
            seen[norm] = name.strip()
    return tuple(seen.values())


def _validate_aliases(index: dict[str, str]) -> tuple[tuple[str, str, str], ...]:
    """Find aliases that point away from a club registered under that exact name.

    An alias like "расинг" -> "Расинг" is fine. An alias whose key IS the canonical
    name of a *different* registered club would silently rename that club, so it is
    reported here and ignored by the resolver.
    """
    dropped: list[tuple[str, str, str]] = []
    for alias, canonical in TEAM_ALIASES.items():
        owner = index.get(normalize_team_name(alias))
        if owner is None:
            continue
        if normalize_team_name(owner) != normalize_team_name(canonical):
            dropped.append((alias, canonical, owner))
    return tuple(dropped)


def reload_registry() -> int:
    """Rebuild the registry index from config. Returns the number of clubs loaded.

    Call this after the club list changes. Kept explicit (rather than lazy) so the
    resolver stays free of I/O and of surprise rebuilds inside hot loops.
    """
    global _registry, _registry_index, _dropped_aliases

    _registry = _load_canonical_names()
    _registry_index = {normalize_team_name(name): name for name in _registry}
    _dropped_aliases = _validate_aliases(_registry_index)

    for alias, canonical, owner in _dropped_aliases:
        logger.warning(
            "Alias %r -> %r shadows registered club %r and will be ignored",
            alias, canonical, owner,
        )
    return len(_registry)


def get_registry() -> tuple[str, ...]:
    """All canonical club names, in registration order."""
    return _registry


def get_registry_index() -> dict[str, str]:
    """Normalized name -> canonical name, precomputed for the resolver."""
    return _registry_index


def get_dropped_aliases() -> tuple[tuple[str, str, str], ...]:
    """Aliases ignored because they shadow a registered club: (alias, target, owner)."""
    return _dropped_aliases


def is_registered(name: str | None) -> bool:
    """True when the name matches a registered club exactly (after normalization)."""
    return normalize_team_name(name) in _registry_index


reload_registry()
