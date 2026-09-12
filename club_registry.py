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
from dataclasses import dataclass
from enum import Enum

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
    "аякс амстердам": "Аякс", "ajax amsterdam": "Аякс",
    
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
    "селтик глазго": "Селтик", "celtic glasgow": "Селтик",
    
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
_alias_index: dict[str, str] = {}
_joined_index: dict[str, str] = {}
_dropped_aliases: tuple[tuple[str, str, str], ...] = ()
_orphan_aliases: tuple[str, ...] = ()


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


def _build_alias_index(index: dict[str, str]) -> tuple[dict[str, str], tuple[str, ...]]:
    """Map normalized alias -> canonical name, keeping only aliases that are safe.

    Two kinds are left out: aliases whose key is itself a registered club name (the
    EXACT tier owns those, and honouring the alias would rename a real club), and
    aliases pointing at a club that is not in the registry at all.
    """
    aliases: dict[str, str] = {}
    orphans: list[str] = []
    for alias, canonical in TEAM_ALIASES.items():
        a_norm = normalize_team_name(alias)
        if not a_norm or a_norm in index:
            continue
        owner = index.get(normalize_team_name(canonical))
        if owner is None:
            orphans.append(alias)
            continue
        aliases[a_norm] = owner
    return aliases, tuple(orphans)


def _build_joined_index(*sources: dict[str, str]) -> dict[str, str]:
    """Map space-free forms -> canonical name, for OCR that glues or splits words.

    Ambiguous keys are dropped: if two clubs collapse to the same space-free form,
    neither may win by accident.
    """
    joined: dict[str, str] = {}
    conflicting: set[str] = set()
    for source in sources:
        for key, canonical in source.items():
            glued = key.replace(" ", "")
            if not glued or glued == key:
                continue
            existing = joined.get(glued)
            if existing is not None and existing != canonical:
                conflicting.add(glued)
            else:
                joined[glued] = canonical
    for key in conflicting:
        joined.pop(key, None)
    return joined


def reload_registry() -> int:
    """Rebuild the registry index from config. Returns the number of clubs loaded.

    Call this after the club list changes. Kept explicit (rather than lazy) so the
    resolver stays free of I/O and of surprise rebuilds inside hot loops.
    """
    global _registry, _registry_index, _alias_index, _joined_index
    global _dropped_aliases, _orphan_aliases

    _registry = _load_canonical_names()
    _registry_index = {normalize_team_name(name): name for name in _registry}
    _dropped_aliases = _validate_aliases(_registry_index)
    _alias_index, _orphan_aliases = _build_alias_index(_registry_index)
    _joined_index = _build_joined_index(_registry_index, _alias_index)

    for alias, canonical, owner in _dropped_aliases:
        logger.warning(
            "Alias %r -> %r shadows registered club %r and will be ignored",
            alias, canonical, owner,
        )
    if _orphan_aliases:
        logger.debug(
            "%d aliases point at clubs outside the registry and are inactive",
            len(_orphan_aliases),
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


def get_alias_index() -> dict[str, str]:
    """Normalized alias -> canonical name, for aliases active against this registry."""
    return _alias_index


def get_orphan_aliases() -> tuple[str, ...]:
    """Aliases inactive because their target club is not in the registry."""
    return _orphan_aliases


# ---------------------------------------------------------------------------
# Name resolution
#
# Tiers run in order and the first *unambiguous* one wins. Ambiguity anywhere
# stops resolution with method=NONE: letting a weaker tier settle what a stronger
# one called a tie is exactly how «Расинг Ланс» used to become «Расинг».
# ---------------------------------------------------------------------------

# Фаззи-тир нужен против опечаток OCR, а не против коротких похожих имён.
FUZZY_MIN_LEN = 5       # 'псж' против 'псв' даёт 0.667 — на трёх буквах фаззи бессмысленен
FUZZY_THRESHOLD = 0.87  # было 0.65: слишком низко, склеивало разные клубы
FUZZY_MARGIN = 0.07     # отрыв от второго кандидата; без него побеждал просто «наименее плохой»
PREFIX_MIN_LEN = 3      # префикс короче трёх букв подходит слишком многим

# Юридические формы и приставки: шум, а не часть имени. Отбрасываются, чтобы
# «Спортинг CP» и «ФК Порту» дошли до точного совпадения. Географические
# уточнения сюда не входят и входить не должны — именно они отличают
# «Расинг Сантандер» от «Расинг Ланс».
_NOISE_TOKENS = frozenset({
    "фк", "фс", "сп", "сц", "кф", "клуб",
    "fc", "sc", "sl", "cf", "ac", "afc", "cp", "club", "jrs",
})


class ResolveMethod(str, Enum):
    """Which tier produced the answer."""
    EXACT = "exact"      # совпадение с каноном реестра
    ALIAS = "alias"      # словарь TEAM_ALIASES
    JOINED = "joined"    # склейка токенов / отброшенный шум
    PREFIX = "prefix"    # единственный канон с таким префиксом
    FUZZY = "fuzzy"      # difflib, с запасом над вторым кандидатом
    NONE = "none"        # не разрешено — вход возвращается как есть


@dataclass(frozen=True, slots=True)
class TeamResolution:
    """Outcome of resolving a raw team name against the club registry."""
    raw: str
    canonical: str | None
    method: ResolveMethod
    confidence: float
    candidates: tuple[str, ...] = ()

    @property
    def is_confident(self) -> bool:
        return self.canonical is not None


def _alternate_forms(norm: str) -> list[str]:
    """Rewrites of the input worth a second lookup, in order of trustworthiness."""
    forms: list[str] = []
    tokens = norm.split()

    stripped = [t for t in tokens if t not in _NOISE_TOKENS]
    if stripped and len(stripped) != len(tokens):
        forms.append(" ".join(stripped))

    if len(tokens) > 1:
        forms.append("".join(tokens))
    if len(stripped) > 1 and len(stripped) != len(tokens):
        forms.append("".join(stripped))

    return [f for f in forms if f and f != norm]


def _fuzzy_scores(norm: str) -> list[tuple[str, float]]:
    """Best difflib ratio per canonical club, highest first."""
    scores: dict[str, float] = {}
    for candidate_norm, canonical in list(_registry_index.items()) + list(_alias_index.items()):
        ratio = difflib.SequenceMatcher(None, norm, candidate_norm).ratio()
        if ratio > scores.get(canonical, 0.0):
            scores[canonical] = ratio
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def resolve_team_name_ex(name: str | None) -> TeamResolution:
    """Resolve a raw team name, reporting how confident the answer is.

    Pure CPU: no SQL, no network. Callers run inside the event loop.
    """
    raw = str(name).strip() if name else ""
    norm = normalize_team_name(raw)
    if not norm:
        return TeamResolution(raw, None, ResolveMethod.NONE, 0.0)

    # 1. Точное совпадение с каноном. idx_users_team_name_unique гарантирует,
    #    что канон один, поэтому спорить тут не с чем.
    canonical = _registry_index.get(norm)
    if canonical is not None:
        return TeamResolution(raw, canonical, ResolveMethod.EXACT, 1.0)

    # 2. Словарь алиасов: транслит, склонения, опечатки OCR.
    canonical = _alias_index.get(norm)
    if canonical is not None:
        return TeamResolution(raw, canonical, ResolveMethod.ALIAS, 1.0)

    # 3. Те же справочники, но по переписанным формам: отброшенные «ФК»/«CP»
    #    и склейка токенов в обе стороны (OCR и слепляет слова, и рвёт их).
    for form in _alternate_forms(norm):
        canonical = _registry_index.get(form) or _alias_index.get(form)
        if canonical is not None:
            return TeamResolution(raw, canonical, ResolveMethod.JOINED, 1.0)

    for form in [norm] + _alternate_forms(norm):
        canonical = _joined_index.get(form.replace(" ", ""))
        if canonical is not None:
            return TeamResolution(raw, canonical, ResolveMethod.JOINED, 1.0)

    # 4. Префикс — но только если он ведёт ровно к одному клубу. Именно этот
    #    предохранитель не даёт «Расинг» угадаться при живых «Расинг Сантандер»
    #    и «Расинг Ланс».
    if len(norm) >= PREFIX_MIN_LEN:
        prefixed = sorted({
            canon for canon_norm, canon in _registry_index.items()
            if canon_norm.startswith(norm)
        })
        if len(prefixed) == 1:
            return TeamResolution(raw, prefixed[0], ResolveMethod.PREFIX, 1.0)
        if len(prefixed) > 1:
            return TeamResolution(raw, None, ResolveMethod.NONE, 0.0, tuple(prefixed))

    # 5. Фаззи — последний и самый слабый тир, под тремя предохранителями.
    if len(norm) < FUZZY_MIN_LEN:
        return TeamResolution(raw, None, ResolveMethod.NONE, 0.0)

    ranked = _fuzzy_scores(norm)
    if not ranked:
        return TeamResolution(raw, None, ResolveMethod.NONE, 0.0)

    best_canon, best_score = ranked[0]
    if best_score < FUZZY_THRESHOLD:
        return TeamResolution(raw, None, ResolveMethod.NONE, 0.0)

    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score - second_score < FUZZY_MARGIN:
        tied = tuple(canon for canon, score in ranked if best_score - score < FUZZY_MARGIN)
        return TeamResolution(raw, None, ResolveMethod.NONE, 0.0, tied)

    return TeamResolution(raw, best_canon, ResolveMethod.FUZZY, best_score)


def resolve_team_name(name: str | None) -> str:
    """Resolve a raw team name to its canonical form, or return it unchanged.

    Backwards-compatible wrapper: never returns an empty string for a non-empty
    input, so the existing `resolve_team_name(x) or x` call sites keep working.
    Use resolve_team_name_ex when you need to know whether it actually resolved.
    """
    if not name:
        return ""
    resolved = resolve_team_name_ex(name)
    return resolved.canonical if resolved.canonical is not None else str(name).strip()


reload_registry()
