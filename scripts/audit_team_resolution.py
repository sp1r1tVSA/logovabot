"""Аудит резолва имён клубов на живой базе (пункт P3-7).

Локальная league.db пустая, реальный ростер живёт только на VPS, поэтому проверить
реестр клубов тестами нельзя — нужен прогон по настоящим данным. Скрипт отвечает на
три вопроса:

  1. Есть ли клубы, которые резолвер сводит в одно имя? Это прямая потеря данных:
     `get_standings` индексирует таблицу каноническим именем, и второй клуб
     затирает первого — тренер пропадает из таблицы.
  2. Какие клубы из БД отсутствуют в `config.CLUB_REGISTRY`? Пока список неполон,
     `teams_match` для них намеренно строже и не склеивает опечатки OCR.
  3. Выдерживает ли порог фаззи-сравнения реальный ростер? Если два настоящих клуба
     похожи сильнее порога, порог придётся поднимать, а не подгонять данные.

Использование:

    python scripts/audit_team_resolution.py                 # отчёт, exit 1 при находках
    python scripts/audit_team_resolution.py --db /path/league.db
    python scripts/audit_team_resolution.py --emit-config   # блок для config.py в stdout

Скрипт **только читает**: соединение закрыто авторизатором SQLite, который пропускает
SELECT/READ и отклоняет любую запись на уровне драйвера. Ошибиться и что-то изменить
на боевой базе тут нельзя даже случайно.
"""

import argparse
import itertools
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# Запуск идёт из корня репозитория, но скрипт должны звать и как scripts/... с VPS.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import config  # noqa: E402
import club_registry  # noqa: E402
from club_registry import (  # noqa: E402
    FUZZY_THRESHOLD,
    ResolveMethod,
    normalize_team_name,
    resolve_team_name_ex,
)

# Авторизатор пускает только чтение. Всё, чего нет в списке — DENY, включая
# INSERT/UPDATE/DELETE, DDL и ATTACH.
_READ_ONLY_ACTIONS = frozenset({
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_PRAGMA,
    sqlite3.SQLITE_TRANSACTION,
})

# Имена клубов встречаются не только в ростере. Запросы записаны литералами, без
# подстановки имён таблиц: интерполяция в SQL в этом проекте запрещена.
NAME_SOURCES = (
    ("matches.player1_team", "SELECT DISTINCT player1_team FROM matches WHERE player1_team IS NOT NULL AND player1_team != ''"),
    ("matches.player2_team", "SELECT DISTINCT player2_team FROM matches WHERE player2_team IS NOT NULL AND player2_team != ''"),
    ("squad_players.team_name", "SELECT DISTINCT team_name FROM squad_players WHERE team_name IS NOT NULL AND team_name != ''"),
    ("match_events.team_name", "SELECT DISTINCT team_name FROM match_events WHERE team_name IS NOT NULL AND team_name != ''"),
    ("cup_series.team1_name", "SELECT DISTINCT team1_name FROM cup_series WHERE team1_name IS NOT NULL AND team1_name != ''"),
    ("cup_series.team2_name", "SELECT DISTINCT team2_name FROM cup_series WHERE team2_name IS NOT NULL AND team2_name != ''"),
    ("team_ratings.team_name", "SELECT DISTINCT team_name FROM team_ratings WHERE team_name IS NOT NULL AND team_name != ''"),
)


@dataclass
class Finding:
    """Находка аудита. Наличие хотя бы одной даёт exit code 1."""

    code: str
    title: str
    lines: list[str] = field(default_factory=list)


def open_read_only(db_path: str) -> sqlite3.Connection:
    """Открыть базу и запретить соединению запись.

    Открывать через `database.get_connection()` нельзя: он ставит PRAGMA и отдаёт
    соединение, пригодное для записи. Здесь нужен именно кастрированный доступ.
    """
    if not os.path.exists(db_path):
        raise SystemExit(f"Базы нет: {db_path}")

    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.set_authorizer(
        lambda action, *_: sqlite3.SQLITE_OK
        if action in _READ_ONLY_ACTIONS
        else sqlite3.SQLITE_DENY
    )
    return conn


def fetch_roster(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Ростер — источник истины по клубам: один клуб на тренера."""
    return conn.execute(
        "SELECT telegram_id, username, team_name, division_id FROM users "
        "WHERE team_name IS NOT NULL AND team_name != '' ORDER BY division_id, team_name"
    ).fetchall()


def fetch_division_count(conn: sqlite3.Connection) -> int | None:
    """Сколько дивизионов заведено в базе — независимо от того, есть ли в них клубы.

    Нужно именно как отдельный счётчик: пустой ростер и указанная не та база дают
    одинаковый «0 клубов», и различить их можно только по остальному содержимому.
    None — таблицы нет, база явно не та.
    """
    try:
        return conn.execute("SELECT COUNT(*) FROM divisions").fetchone()[0]
    except sqlite3.OperationalError:
        return None


def fetch_other_names(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Имена клубов из остальных таблиц: name -> где встретилось.

    Таблица могла не доехать миграцией на конкретной базе — такой источник
    пропускаем, а не роняем весь аудит.
    """
    found: dict[str, set[str]] = defaultdict(set)
    for label, query in NAME_SOURCES:
        try:
            rows = conn.execute(query).fetchall()
        except sqlite3.OperationalError as exc:
            print(f"  ! источник {label} пропущен: {exc}", file=sys.stderr)
            continue
        for row in rows:
            found[str(row[0]).strip()].add(label)
    return found


def check_resolution_collisions(roster: list[sqlite3.Row]) -> Finding | None:
    """Два и больше клубов ростера, схлопнутых резолвером в одно каноническое имя."""
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in roster:
        res = resolve_team_name_ex(row["team_name"])
        if res.is_confident:
            groups[res.canonical].append(row)

    lines = []
    for canonical, rows in sorted(groups.items()):
        if len(rows) < 2:
            continue
        names = {normalize_team_name(r["team_name"]) for r in rows}
        if len(names) < 2:
            continue  # одно и то же имя у двух тренеров — это уже другая проблема
        lines.append(f"  «{canonical}» ← " + ", ".join(
            f"{r['team_name']} (id {r['telegram_id']}, дивизион {r['division_id']})"
            for r in rows
        ))

    if not lines:
        return None
    return Finding(
        "COLLISION",
        "Разные клубы резолвятся в одно имя — в таблице дивизиона они делят строку",
        lines,
    )


def check_normalization_duplicates(roster: list[sqlite3.Row]) -> Finding | None:
    """Разные строки в БД с одинаковой нормализованной формой.

    Уникальный индекс держит только LOWER(TRIM(...)), а нормализация ещё убирает
    пунктуацию и ё/е — такая пара пройдёт индекс, но склеится в любом резолве.
    """
    groups: dict[str, set[str]] = defaultdict(set)
    for row in roster:
        groups[normalize_team_name(row["team_name"])].add(row["team_name"])

    lines = [
        f"  {norm!r} ← " + ", ".join(sorted(names))
        for norm, names in sorted(groups.items())
        if len(names) > 1
    ]
    if not lines:
        return None
    return Finding("NORMALIZE_DUP", "Имена различаются только тем, что съедает нормализация", lines)


def check_unregistered(roster: list[sqlite3.Row]) -> Finding | None:
    """Клубы ростера, которых нет в CLUB_REGISTRY.

    Та же сверка есть в `database.verify_registry_against_db()` — её зовёт код бота,
    по своей базе. Здесь считаем по уже прочитанному ростеру, потому что аудит
    умеет смотреть в произвольный файл через --db, в том числе в копию с VPS.
    """
    missing = sorted({
        row["team_name"] for row in roster
        if not club_registry.is_registered(row["team_name"])
    })
    if not missing:
        return None
    return Finding(
        "UNREGISTERED",
        f"Клубов вне CLUB_REGISTRY: {len(missing)} — для них teams_match не прощает опечатки OCR",
        [f"  {name}" for name in missing],
    )


def check_ambiguous(roster: list[sqlite3.Row]) -> Finding | None:
    """Имена, на которых резолвер остановился из-за неоднозначности."""
    lines = []
    for row in roster:
        res = resolve_team_name_ex(row["team_name"])
        if res.method is ResolveMethod.NONE and res.candidates:
            lines.append(f"  {row['team_name']} → кандидаты: " + ", ".join(res.candidates))
    if not lines:
        return None
    return Finding("AMBIGUOUS", "Резолвер не выбрал между кандидатами", lines)


def check_fuzzy_threshold(roster: list[sqlite3.Row], top: int = 10) -> tuple[Finding | None, list[str]]:
    """Насколько близко реальные клубы подходят к порогу фаззи.

    Возвращает находку (если пара клубов похожа сильнее порога — тогда порог низкий)
    и справочные строки с самыми похожими парами для глазами-проверки.
    """
    names = sorted({normalize_team_name(row["team_name"]) for row in roster})
    scored = sorted(
        (
            (SequenceMatcher(None, a, b).ratio(), a, b)
            for a, b in itertools.combinations(names, 2)
        ),
        reverse=True,
    )[:top]

    report = [f"  {ratio:.3f}  {a!r} ~ {b!r}" for ratio, a, b in scored]
    over = [line for (ratio, _, _), line in zip(scored, report) if ratio >= FUZZY_THRESHOLD]
    if not over:
        return None, report
    return (
        Finding(
            "FUZZY_THRESHOLD",
            f"Разные клубы похожи не меньше порога {FUZZY_THRESHOLD} — порог надо поднимать",
            over,
        ),
        report,
    )


def check_registry_drift(roster: list[sqlite3.Row], other: dict[str, set[str]]) -> Finding | None:
    """Имена вне ростера, которые ни во что не резолвятся.

    Это следы OCR и старых сезонов: строка в matches есть, а клуба под неё нет.
    Не ошибка сама по себе, но именно такие имена ломают сведение статистики.
    """
    roster_norms = {normalize_team_name(row["team_name"]) for row in roster}
    orphans = sorted(
        (name, sorted(sources))
        for name, sources in other.items()
        if normalize_team_name(name) not in roster_norms
        and not resolve_team_name_ex(name).is_confident
    )
    if not orphans:
        return None
    return Finding(
        "ORPHAN_NAME",
        f"Имён в данных без клуба и без реестра: {len(orphans)}",
        [f"  {name}  ({', '.join(sources)})" for name, sources in orphans],
    )


def emit_config(roster: list[sqlite3.Row]) -> str:
    """Готовый блок CLUB_REGISTRY: по одному имени на клуб, дубли по нормализации сняты."""
    seen: dict[str, str] = {}
    for row in roster:
        seen.setdefault(normalize_team_name(row["team_name"]), row["team_name"].strip())

    body = "\n".join(f'    "{name}",' for name in sorted(seen.values()))
    return (
        "# Канонические имена всех клубов турнира (пункт P3-7).\n"
        "# Сгенерировано scripts/audit_team_resolution.py --emit-config по users.team_name.\n"
        f"CLUB_REGISTRY: list[str] = [\n{body}\n]\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Аудит резолва имён клубов (read-only)")
    parser.add_argument("--db", default=config.DB_PATH, help="путь к league.db")
    parser.add_argument(
        "--emit-config",
        action="store_true",
        help="напечатать блок CLUB_REGISTRY в stdout (отчёт при этом уходит в stderr)",
    )
    parser.add_argument("--limit", type=int, default=40, help="сколько строк печатать в находке")
    args = parser.parse_args()

    # При --emit-config stdout должен остаться пастабельным, поэтому отчёт — в stderr.
    out = sys.stderr if args.emit_config else sys.stdout

    conn = open_read_only(args.db)
    try:
        roster = fetch_roster(conn)
        division_count = fetch_division_count(conn)
        other = fetch_other_names(conn)
    finally:
        conn.close()

    print(f"База: {args.db}", file=out)
    registry = club_registry.get_registry()
    stub = not config.CLUB_REGISTRY
    print(
        f"Реестр: {len(registry)} имён"
        + (" (заглушка: KPL_TEAMS ∪ CLUBS, см. TODO(P3-7) в config.py)" if stub else ""),
        file=out,
    )
    dropped = club_registry.get_dropped_aliases()
    orphan_aliases = club_registry.get_orphan_aliases()
    if dropped:
        print(f"Отброшено алиасов (конфликт с каноном): {len(dropped)} — {sorted(dropped)}", file=out)
    if orphan_aliases:
        print(f"Алиасов на клуб вне реестра: {len(orphan_aliases)}", file=out)

    divisions_with_clubs = len({row["division_id"] for row in roster} - {None})
    if division_count is None:
        print("Дивизионов: таблицы divisions нет — это не база бота", file=out)
    else:
        print(f"Дивизионов заведено: {division_count}, из них с клубами: {divisions_with_clubs}", file=out)
    print(f"Клубов в ростере: {len(roster)}", file=out)
    print(f"Имён клубов в остальных таблицах: {len(other)}", file=out)

    if not roster:
        # Без этой подсказки пустой ростер выглядит как ошибка в пути к базе.
        print(
            "\nРостер пуст: ни у одного пользователя не заполнен team_name.\n"
            "Если база та самая — значит тренеров ещё не регистрировали (например, после\n"
            "purge сезона), и собирать CLUB_REGISTRY пока не из чего. Если ожидались клубы —\n"
            "проверьте путь: LEAGUE_SQLITE_PATH в .env и WorkingDirectory у systemd-юнита.",
            file=out,
        )

    fuzzy_finding, fuzzy_report = check_fuzzy_threshold(roster)
    findings = [
        f for f in (
            check_resolution_collisions(roster),
            check_normalization_duplicates(roster),
            check_ambiguous(roster),
            check_unregistered(roster),
            check_registry_drift(roster, other),
            fuzzy_finding,
        ) if f is not None
    ]

    if fuzzy_report:
        print("\nСамые похожие пары клубов (порог фаззи "
              f"{FUZZY_THRESHOLD}):", file=out)
        for line in fuzzy_report:
            print(line, file=out)

    if not findings:
        print("\nНаходок нет.", file=out)
    else:
        print(f"\nНаходок: {len(findings)}", file=out)
        for finding in findings:
            print(f"\n[{finding.code}] {finding.title}", file=out)
            for line in finding.lines[:args.limit]:
                print(line, file=out)
            if len(finding.lines) > args.limit:
                print(f"  … ещё {len(finding.lines) - args.limit}", file=out)

    if args.emit_config:
        sys.stdout.write(emit_config(roster))

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
