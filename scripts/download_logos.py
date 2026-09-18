#!/usr/bin/env python3
"""
scripts/download_logos.py

Скачивает, чистит и проверяет эмблемы всех 80 клубов сезона в `assets/logos/`.

Имена файлов — те же, что в `TEAM_LOGO_MAP` (services/graphics/table_generator.py);
`tests/test_download_logos.py` следит, чтобы таблица CLUBS ниже не разошлась с картой.

Источники, по порядку:
  1. TheSportsDB — `searchteams.php?t=…`, поле `strBadge` (PNG ~500px с прозрачностью).
     Кандидат принимается только если это мужская футбольная команда из ожидаемой
     страны: `Arsenal` без фильтра легко отдаёт женскую команду или тёзку из Тулы.
  2. Wikipedia — `prop=pageimages` по точному заголовку статьи клуба: картинка из
     инфобокса, отрендеренная в PNG. `pilicense=any` обязателен — гербы клубов
     на en.wikipedia почти всегда несвободные файлы, и без него API их не отдаёт.

Постобработка: RGBA → белый фон, связанный с краями, делается прозрачным (заливкой
от границы, а не заменой *всех* белых пикселей — иначе в гербе появятся дыры) →
обрезка пустых полей → вписывание в квадрат SIZE×SIZE по центру прозрачного холста.
Квадрат важен: часть рендереров делает `resize((inner, inner))` без сохранения
пропорций, а прозрачные углы не дают `clean_and_prepare_logo` снова вырезать белое.

Режимы:
  python scripts/download_logos.py                 # все 80, существующие валидные пропускаются
  python scripts/download_logos.py --test          # первые 5 клубов (алиас: --dry-run)
  python scripts/download_logos.py --overwrite     # перекачать всё заново
  python scripts/download_logos.py --only psg,milan
  python scripts/download_logos.py --verify        # ничего не качать, только проверить файлы
  python scripts/download_logos.py --target-dir /tmp/logos --size 256

Код выхода: 0 — всё хорошо, 1 — есть ошибки скачивания или проверки.
"""

from __future__ import annotations

import argparse
import io
import json
import random
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET_DIR = PROJECT_ROOT / "assets" / "logos"

TIMEOUT_S = 10
RETRIES = 2
DELAY_RANGE_S = (0.2, 0.5)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 logovobot-logo-fetcher/1.0"
)

SPORTSDB_SEARCH = "https://www.thesportsdb.com/api/v1/json/3/searchteams.php?t={query}"
WIKI_API = "https://en.wikipedia.org/w/api.php"

# Пороги постобработки и проверки
WHITE_THRESHOLD = 245          # канал ≥ этого — «белый» для удаления фона
MIN_FILE_BYTES = 1024
MIN_SOURCE_SIDE = 100          # меньше — источник слишком мелкий, берём следующий
MIN_OPAQUE_RATIO = 0.05        # доля непрозрачных пикселей: меньше — почти пустая картинка
MAX_OPAQUE_RATIO = 0.98        # больше — фон так и не стал прозрачным


@dataclass(frozen=True)
class Club:
    ru: str          # каноническое имя из config.DIVISION_CLUBS / TEAM_LOGO_MAP
    filename: str    # файл в assets/logos/
    query: str       # поисковый запрос к TheSportsDB
    country: str     # strCountry в TheSportsDB — отсекает тёзок
    wiki: str        # заголовок статьи en.wikipedia для запасного источника


CLUBS: tuple[Club, ...] = (
    # DIV_1
    Club("Лидс", "leeds.png", "Leeds United", "England", "Leeds United F.C."),
    Club("Ренн", "rennes.png", "Rennes", "France", "Stade Rennais F.C."),
    Club("Ницца", "nice.png", "Nice", "France", "OGC Nice"),
    Club("Нэшвилл", "nashville.png", "Nashville SC", "United States", "Nashville SC"),
    Club("Порту", "porto.png", "Porto", "Portugal", "FC Porto"),
    Club("Вест Хэм", "west_ham.png", "West Ham United", "England", "West Ham United F.C."),
    Club("Вольфсбург", "wolfsburg.png", "Wolfsburg", "Germany", "VfL Wolfsburg"),
    Club("Фиорентина", "fiorentina.png", "Fiorentina", "Italy", "ACF Fiorentina"),
    Club("Лацио", "lazio.png", "Lazio", "Italy", "SS Lazio"),
    Club("Марсель", "marseille.png", "Marseille", "France", "Olympique de Marseille"),
    Club("Лилль", "lille.png", "Lille OSC", "France", "Lille OSC"),
    Club("Айнтрахт", "eintracht.png", "Eintracht Frankfurt", "Germany", "Eintracht Frankfurt"),
    Club("Майнц", "mainz.png", "Mainz", "Germany", "1. FSV Mainz 05"),
    Club("Бернли", "burnley.png", "Burnley", "England", "Burnley F.C."),
    Club("Будё Глимт", "bodo_glimt.png", "Bodo Glimt", "Norway", "FK Bodø/Glimt"),
    Club("Кельн", "koln.png", "Koln", "Germany", "1. FC Köln"),
    # DIV_2
    Club("Вулверхэмптон", "wolverhampton.png", "Wolverhampton Wanderers", "England",
         "Wolverhampton Wanderers F.C."),
    Club("Бурирам", "buriram.png", "Buriram United", "Thailand", "Buriram United F.C."),
    Club("Валенсия", "valencia.png", "Valencia", "Spain", "Valencia CF"),
    Club("Сельта", "celta.png", "Celta Vigo", "Spain", "RC Celta de Vigo"),
    Club("Ривер Плейт", "river_plate.png", "River Plate", "Argentina", "Club Atlético River Plate"),
    Club("Аякс", "ajax.png", "Ajax", "Netherlands", "AFC Ajax"),
    Club("Спортинг", "sporting.png", "Sporting CP", "Portugal", "Sporting CP"),
    Club("Монако", "monaco.png", "Monaco", "Monaco", "AS Monaco FC"),
    Club("Бенфика", "benfica.png", "Benfica", "Portugal", "S.L. Benfica"),
    Club("Фулхэм", "fulham.png", "Fulham", "England", "Fulham F.C."),
    Club("Хоффенхайм", "hoffenheim.png", "Hoffenheim", "Germany", "TSG 1899 Hoffenheim"),
    Club("Ланс", "lens.png", "Lens", "France", "RC Lens"),
    Club("Аль-Кадисия", "al_qadsiah.png", "Al Qadsiah", "Saudi Arabia", "Al-Qadsiah FC"),
    Club("Торино", "torino.png", "Torino", "Italy", "Torino FC"),
    Club("Лос Анджелес", "los_angeles.png", "Los Angeles FC", "United States", "Los Angeles FC"),
    Club("ПСВ", "psv.png", "PSV Eindhoven", "Netherlands", "PSV Eindhoven"),
    # DIV_3
    Club("Сандерленд", "sunderland.png", "Sunderland", "England", "Sunderland A.F.C."),
    Club("Ноттингем Форест", "nottingham_forest.png", "Nottingham Forest", "England",
         "Nottingham Forest F.C."),
    Club("Реал Сосьедад", "real_sociedad.png", "Real Sociedad", "Spain", "Real Sociedad"),
    Club("Париж", "paris_fc.png", "Paris FC", "France", "Paris FC"),
    Club("Фенербахче", "fenerbahce.png", "Fenerbahce", "Turkey", "Fenerbahçe S.K. (football)"),
    Club("Комо", "como.png", "Como", "Italy", "Como 1907"),
    Club("Брентфорд", "brentford.png", "Brentford", "England", "Brentford F.C."),
    Club("Кристал Пэлас", "crystal_palace.png", "Crystal Palace", "England", "Crystal Palace F.C."),
    Club("Аль-Ахли", "al_ahli.png", "Al Ahli Saudi", "Saudi Arabia", "Al-Ahli Saudi FC"),
    Club("Лион", "lyon.png", "Lyon", "France", "Olympique Lyonnais"),
    Club("Борнмут", "bournemouth.png", "Bournemouth", "England", "AFC Bournemouth"),
    Club("Аль-Иттихад", "al_ittihad.png", "Al Ittihad", "Saudi Arabia", "Al-Ittihad Club (Jeddah)"),
    Club("Трабзонспор", "trabzonspor.png", "Trabzonspor", "Turkey", "Trabzonspor"),
    Club("Вильярреал", "villarreal.png", "Villarreal", "Spain", "Villarreal CF"),
    Club("Штутгарт", "stuttgart.png", "Stuttgart", "Germany", "VfB Stuttgart"),
    Club("Болонья", "bologna.png", "Bologna", "Italy", "Bologna FC 1909"),
    # DIV_4
    Club("Байя", "bahia.png", "Bahia", "Brazil", "Esporte Clube Bahia"),
    Club("Милан", "milan.png", "AC Milan", "Italy", "AC Milan"),
    Club("Боруссия Дортмунд", "borussia_dortmund.png", "Borussia Dortmund", "Germany",
         "Borussia Dortmund"),
    Club("Интер Милан", "inter_milan.png", "Inter Milan", "Italy", "Inter Milan"),
    Club("Брайтон", "brighton.png", "Brighton and Hove Albion", "England", "Brighton & Hove Albion F.C."),
    Club("Байер", "bayer_leverkusen.png", "Bayer Leverkusen", "Germany", "Bayer 04 Leverkusen"),
    Club("Лейпциг", "leipzig.png", "RB Leipzig", "Germany", "RB Leipzig"),
    Club("Эвертон", "everton.png", "Everton", "England", "Everton F.C."),
    Club("Аталанта", "atalanta.png", "Atalanta", "Italy", "Atalanta BC"),
    Club("Астон Вилла", "aston_villa.png", "Aston Villa", "England", "Aston Villa F.C."),
    Club("Бешикташ", "besiktas.png", "Besiktas", "Turkey", "Beşiktaş J.K."),
    Club("Интер Майами", "inter_miami.png", "Inter Miami", "United States", "Inter Miami CF"),
    Club("Бетис", "betis.png", "Real Betis", "Spain", "Real Betis"),
    Club("Аль-Хиляль", "al_hilal.png", "Al Hilal", "Saudi Arabia", "Al Hilal SFC"),
    Club("Ньюкасл", "newcastle.png", "Newcastle United", "England", "Newcastle United F.C."),
    Club("Атлетик Бильбао", "athletic_bilbao.png", "Athletic Bilbao", "Spain", "Athletic Bilbao"),
    # DIV_5
    Club("Арсенал", "arsenal.png", "Arsenal", "England", "Arsenal F.C."),
    Club("Манчестер Сити", "manchester_city.png", "Manchester City", "England",
         "Manchester City F.C."),
    Club("Манчестер Юнайтед", "manchester_united.png", "Manchester United", "England",
         "Manchester United F.C."),
    Club("Тоттенхэм", "tottenham.png", "Tottenham Hotspur", "England", "Tottenham Hotspur F.C."),
    Club("Атлетико Мадрид", "atletico_madrid.png", "Atletico Madrid", "Spain", "Atlético Madrid"),
    Club("Барселона", "barcelona.png", "Barcelona", "Spain", "FC Barcelona"),
    Club("Реал Мадрид", "real_madrid.png", "Real Madrid", "Spain", "Real Madrid CF"),
    Club("Бавария", "bayern.png", "Bayern Munich", "Germany", "FC Bayern Munich"),
    Club("Ливерпуль", "liverpool.png", "Liverpool", "England", "Liverpool F.C."),
    Club("Челси", "chelsea.png", "Chelsea", "England", "Chelsea F.C."),
    Club("Наполи", "napoli.png", "Napoli", "Italy", "SSC Napoli"),
    Club("Ювентус", "juventus.png", "Juventus", "Italy", "Juventus FC"),
    Club("Рома", "roma.png", "Roma", "Italy", "AS Roma"),
    Club("ПСЖ", "psg.png", "Paris Saint Germain", "France", "Paris Saint-Germain FC"),
    Club("Галатасарай", "galatasaray.png", "Galatasaray", "Turkey", "Galatasaray S.K. (football)"),
    Club("Аль-Наср", "al_nassr.png", "Al Nassr", "Saudi Arabia", "Al Nassr FC"),
)

# Названия, которые выдают не основную мужскую команду.
_REJECT_NAME_TOKENS = ("women", "femen", "frauen", "ladies", "u19", "u21", "u23", " ii", " b ")
# Организационные приставки, которые не различают клубы при сравнении имён.
_NOISE_TOKENS = frozenset({"fc", "cf", "afc", "sc", "ac", "as", "ss", "ssc", "sl", "cp", "bc",
                           "sk", "jk", "sfc", "fk", "vfl", "vfb", "rc", "club", "the"})
MIN_NAME_SIMILARITY = 0.8


# ==============================================================================
# Сеть
# ==============================================================================

class FetchError(Exception):
    pass


def _polite_pause() -> None:
    time.sleep(random.uniform(*DELAY_RANGE_S))


def http_get(url: str, *, expect_json: bool = False) -> bytes | dict:
    """GET с таймаутом, браузерным User-Agent и парой повторов на временных ошибках."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    last_exc: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                data = resp.read()
            return json.loads(data.decode("utf-8")) if expect_json else data
        except urllib.error.HTTPError as exc:
            last_exc = exc
            # 4xx кроме 429 повторять бессмысленно
            if exc.code != 429 and 400 <= exc.code < 500:
                break
            time.sleep(1.5 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
            last_exc = exc
            time.sleep(1.0 * (attempt + 1))
    raise FetchError(f"{url.split('?')[0]}: {last_exc}")


def _norm_name(name: str) -> str:
    """`FC Köln` → `koln`, `Bodø/Glimt` → `bodo glimt`: диакритика, пунктуация, FC/AFC прочь."""
    folded = unicodedata.normalize("NFKD", name.lower().replace("ø", "o"))
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    tokens = re.sub(r"[^a-z0-9]+", " ", folded).split()
    return " ".join(t for t in tokens if t not in _NOISE_TOKENS)


def _norm_country(country: str) -> str:
    # TheSportsDB пишет «The Netherlands»
    return country.lower().removeprefix("the ").strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm_name(a), _norm_name(b)).ratio()


def pick_sportsdb_team(teams: list[dict], club: Club) -> dict | None:
    """Лучший кандидат: футбол, мужская команда, ожидаемая страна, есть бейдж, имя похоже.

    Бесплатный ключ отдаёт ровно один результат на запрос, и это не всегда тот клуб:
    `Paris SG` возвращает «Torcy», `Wolverhampton` — «Wolverhampton City». Поэтому
    без порога схожести имени ответ не принимается — лучше уйти на Википедию.
    """
    best, best_score = None, MIN_NAME_SIMILARITY
    for team in teams or []:
        name = (team.get("strTeam") or "").strip()
        padded = f" {name.lower()} "
        if (team.get("strSport") or "") != "Soccer" or not team.get("strBadge"):
            continue
        if (team.get("strGender") or "Male") != "Male":
            continue
        if any(tok in padded for tok in _REJECT_NAME_TOKENS):
            continue
        if _norm_country(team.get("strCountry") or "") != _norm_country(club.country):
            continue
        names = [name] + [
            n.strip() for n in (team.get("strTeamAlternate") or "").split(",") if n.strip()
        ]
        score = max(_similarity(club.query, n) for n in names)
        if score >= best_score:
            best, best_score = team, score
    return best


def source_sportsdb(club: Club) -> tuple[str, bytes]:
    url = SPORTSDB_SEARCH.format(query=urllib.parse.quote(club.query))
    payload = http_get(url, expect_json=True)
    team = pick_sportsdb_team((payload or {}).get("teams") or [], club)
    if not team:
        raise FetchError("TheSportsDB: подходящей команды не найдено")
    _polite_pause()
    badge_url = team["strBadge"]
    return f"TheSportsDB «{team['strTeam']}»", http_get(badge_url)


def source_wikipedia(club: Club) -> tuple[str, bytes]:
    params = {
        "action": "query",
        "titles": club.wiki,
        "prop": "pageimages",
        "piprop": "thumbnail",
        "pithumbsize": "600",
        "pilicense": "any",
        "redirects": "1",
        "format": "json",
    }
    payload = http_get(f"{WIKI_API}?{urllib.parse.urlencode(params)}", expect_json=True)
    pages = ((payload or {}).get("query") or {}).get("pages") or {}
    for page in pages.values():
        thumb = (page.get("thumbnail") or {}).get("source")
        if thumb:
            _polite_pause()
            return f"Wikipedia «{page.get('title')}»", http_get(thumb)
    raise FetchError(f"Wikipedia: у статьи «{club.wiki}» нет картинки")


SOURCES = (source_sportsdb, source_wikipedia)


# ==============================================================================
# Обработка изображений
# ==============================================================================

def load_image(data: bytes) -> Image.Image:
    """Открывает байты как картинку; битый или не-графический ответ — исключение."""
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:  # PIL бросает разнородные исключения на мусорный ввод
        raise ValueError(f"не картинка: {exc}") from exc
    if min(img.size) < MIN_SOURCE_SIDE:
        raise ValueError(f"слишком мелкая картинка {img.size[0]}×{img.size[1]}")
    return img


def _has_transparency(img: Image.Image) -> bool:
    return img.getchannel("A").getextrema()[0] < 250


def remove_edge_white(img: Image.Image, threshold: int = WHITE_THRESHOLD) -> Image.Image:
    """Делает прозрачным белый фон, *связанный с краем* картинки.

    Белые детали внутри герба (полосы, буквы) с краем не связаны и остаются как есть —
    в отличие от наивной замены всех белых пикселей.
    """
    img = img.convert("RGBA")
    w, h = img.size
    # 255 — «белый» пиксель (все три канала ≥ threshold), 0 — всё остальное
    r, g, b = (ch.point(lambda v: 255 if v >= threshold else 0) for ch in img.split()[:3])
    mask = ImageChops.multiply(ImageChops.multiply(r, g), b)

    border = (
        [(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)]
        + [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)]
    )
    for xy in border:
        if mask.getpixel(xy) == 255:
            ImageDraw.floodfill(mask, xy, 128)

    background = mask.point(lambda v: 255 if v == 128 else 0)
    alpha = img.getchannel("A")
    alpha.paste(0, mask=background)
    img.putalpha(alpha)
    return img


def process_logo(img: Image.Image, size: int) -> Image.Image:
    """RGBA → прозрачный фон → обрезка полей → по центру квадрата size×size."""
    img = img.convert("RGBA")
    if not _has_transparency(img):
        img = remove_edge_white(img)

    bbox = img.getchannel("A").getbbox()
    if not bbox:
        raise ValueError("после удаления фона картинка пустая")
    img = img.crop(bbox)

    ratio = min(size / img.width, size / img.height)
    new_size = (max(1, round(img.width * ratio)), max(1, round(img.height * ratio)))
    img = img.resize(new_size, Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(img, ((size - new_size[0]) // 2, (size - new_size[1]) // 2), img)
    return canvas


def opaque_ratio(img: Image.Image) -> float:
    hist = img.getchannel("A").histogram()
    total = img.width * img.height
    return sum(hist[128:]) / total if total else 0.0


def validate_file(path: Path) -> list[str]:
    """Список проблем с файлом; пустой список — файл годен."""
    if not path.is_file():
        return ["файла нет"]
    problems = []
    if path.stat().st_size <= MIN_FILE_BYTES:
        problems.append(f"размер {path.stat().st_size} Б ≤ {MIN_FILE_BYTES} Б")
    try:
        with Image.open(path) as probe:
            probe.verify()
        with Image.open(path) as img:
            img.load()
            if img.format != "PNG":
                problems.append(f"формат {img.format}, нужен PNG")
            if img.mode != "RGBA":
                problems.append(f"режим {img.mode}, нужен RGBA")
            else:
                ratio = opaque_ratio(img)
                if ratio < MIN_OPAQUE_RATIO:
                    problems.append(f"почти пустая: непрозрачно {ratio:.1%}")
                elif ratio > MAX_OPAQUE_RATIO:
                    problems.append(f"нет прозрачного фона: непрозрачно {ratio:.1%}")
    except Exception as exc:
        problems.append(f"Pillow не открывает: {exc}")
    return problems


# ==============================================================================
# Прогоны
# ==============================================================================

def fetch_club(club: Club, dest: Path, size: int) -> str:
    """Пробует источники по очереди; возвращает описание успешного источника."""
    errors = []
    for source in SOURCES:
        try:
            label, data = source(club)
            logo = process_logo(load_image(data), size)
            tmp = dest.with_suffix(".png.part")
            logo.save(tmp, format="PNG", optimize=True)
            problems = validate_file(tmp)
            if problems:
                tmp.unlink(missing_ok=True)
                raise ValueError("; ".join(problems))
            tmp.replace(dest)  # атомарно: недокачанный файл не подменит хороший
            return label
        except (FetchError, ValueError, OSError) as exc:
            errors.append(f"{source.__name__.removeprefix('source_')}: {exc}")
        finally:
            _polite_pause()
    raise FetchError(" | ".join(errors))


def run_download(clubs: list[Club], target: Path, size: int, overwrite: bool) -> int:
    target.mkdir(parents=True, exist_ok=True)
    total = len(clubs)
    downloaded, skipped, failed = [], [], []
    width = len(str(total))

    for i, club in enumerate(clubs, 1):
        dest = target / club.filename
        prefix = f"[{i:>{width}}/{total}] {club.ru:<20} → {club.filename:<24}"
        if not overwrite and dest.exists() and not validate_file(dest):
            skipped.append(club)
            print(f"{prefix} ⏭  пропущен (уже есть и валиден)")
            continue
        try:
            label = fetch_club(club, dest, size)
        except FetchError as exc:
            failed.append((club, str(exc)))
            print(f"{prefix} ❌ ошибка: {exc}")
        else:
            downloaded.append(club)
            print(f"{prefix} ✅ {label}")

    print_summary(total, downloaded, skipped, failed)
    if downloaded:
        print_sync_commands(target)
    return 1 if failed else 0


def run_verify(clubs: list[Club], target: Path) -> int:
    bad = []
    for i, club in enumerate(clubs, 1):
        problems = validate_file(target / club.filename)
        status = "✅" if not problems else "❌ " + "; ".join(problems)
        print(f"[{i:>2}/{len(clubs)}] {club.ru:<20} {club.filename:<24} {status}")
        if problems:
            bad.append(club)

    extra = sorted(
        p.name for p in target.glob("*.png") if p.name not in {c.filename for c in CLUBS}
    ) if target.is_dir() else []

    print("\n" + "=" * 60)
    print(f"Проверено: {len(clubs)}   годных: {len(clubs) - len(bad)}   с проблемами: {len(bad)}")
    if bad:
        print("С проблемами: " + ", ".join(c.filename for c in bad))
    if extra:
        print("Лишние файлы вне TEAM_LOGO_MAP: " + ", ".join(extra))
    return 1 if bad else 0


def print_summary(total, downloaded, skipped, failed) -> None:
    print("\n" + "=" * 60)
    print(f"{'Всего клубов':<16}{total:>5}")
    print(f"{'Скачано':<16}{len(downloaded):>5}")
    print(f"{'Пропущено':<16}{len(skipped):>5}")
    print(f"{'Ошибок':<16}{len(failed):>5}")
    if failed:
        print("\nНе удалось скачать:")
        for club, err in failed:
            print(f"  • {club.ru} ({club.filename}): {err}")
        print("\nЭти файлы можно положить в папку вручную и проверить через --verify.")


def print_sync_commands(target: Path) -> None:
    try:
        local = target.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        local = target.resolve().as_posix()
    print("\nПеренос на сервер (подставьте пользователя, хост и путь):")
    print(f"  rsync -avz --progress {local}/ user@server:/home/logovobot/assets/logos/")
    print(f"  scp -r {local}/* user@server:/home/logovobot/assets/logos/")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Скачивание и проверка эмблем 80 клубов в assets/logos/."
    )
    parser.add_argument(
        "--test", "--dry-run", dest="test", nargs="?", const=5, type=int, metavar="N",
        help="скачать только первые N клубов (по умолчанию 5) для проверки качества",
    )
    parser.add_argument("--overwrite", action="store_true",
                        help="перезаписывать уже существующие валидные файлы")
    parser.add_argument("--verify", action="store_true",
                        help="ничего не скачивать, только проверить файлы в папке")
    parser.add_argument("--target-dir", type=Path, default=DEFAULT_TARGET_DIR,
                        help=f"папка для логотипов (по умолчанию {DEFAULT_TARGET_DIR})")
    parser.add_argument("--size", type=int, choices=(256, 512), default=512,
                        help="сторона итогового квадрата в пикселях")
    parser.add_argument("--only", metavar="LIST",
                        help="через запятую: имена файлов (psg, milan.png) или русские названия")
    return parser.parse_args(argv)


def select_clubs(args: argparse.Namespace) -> list[Club]:
    clubs = list(CLUBS)
    if args.only:
        wanted = {w.strip().lower().removesuffix(".png") for w in args.only.split(",") if w.strip()}
        clubs = [
            c for c in clubs
            if c.filename.removesuffix(".png") in wanted or c.ru.lower() in wanted
        ]
        if not clubs:
            raise SystemExit(f"--only: ни один клуб не совпал с {sorted(wanted)}")
    if args.test:
        clubs = clubs[: args.test]
    return clubs


def main(argv: list[str] | None = None) -> int:
    # Кириллица и эмодзи в консоли Windows без UTF-8 падают с UnicodeEncodeError
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args(argv)
    clubs = select_clubs(args)
    target = args.target_dir if args.target_dir.is_absolute() else (Path.cwd() / args.target_dir)

    if args.verify:
        return run_verify(clubs, target)
    print(f"Папка: {target}\nКлубов: {len(clubs)}   размер: {args.size}×{args.size}\n")
    return run_download(clubs, target, args.size, args.overwrite)


if __name__ == "__main__":
    sys.exit(main())
