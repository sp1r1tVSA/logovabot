#!/usr/bin/env python3
"""
scripts/bind_clubs_to_players.py

Массовая привязка участников к клубам сезона — стартовая расстановка одним прогоном
вместо ручного прохода по экрану «🔗 Привязка клубов».

Режимы:

  1. Dry-run (по умолчанию) — печатает план и ничего не пишет:
       python scripts/bind_clubs_to_players.py

  2. Применение:
       python scripts/bind_clubs_to_players.py --apply

  3. Только часть дивизионов:
       python scripts/bind_clubs_to_players.py --apply --divisions 4,5

  4. Дорегистрация участников, которых ещё нет в базе (временный отрицательный id,
     как в scripts/import_division_players.py — реальный id подставится, когда
     человек нажмёт /start):
       python scripts/bind_clubs_to_players.py --apply --create-missing

Важно: привязка снимает клуб с прежнего владельца и обнуляет варны — и новому
владельцу, и прежнему (см. `database.set_player_club`). Для предсезонной расстановки
это ровно то, что нужно, посреди сезона — нет.

Дивизион 1 расставлен вручную и в таблице ниже не значится.
"""

import argparse
import sys
from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
import database

# Клуб → @username, дивизион за дивизионом. Названия клубов — канонические, как в
# config.DIVISION_CLUBS: скрипт сверяет их с сезонным ростером и падает на любом
# незнакомом, а не угадывает. Сверка идёт по коду дивизиона (`DIV_2`…`DIV_5`),
# потому что ростер ключуется именно кодом, а не номером строки в таблице.
BINDINGS: dict[int, dict[str, str]] = {
    5: {
        "Арсенал": "Daot1",
        "Манчестер Сити": "joraknaz",
        "Манчестер Юнайтед": "agosv",
        "Тоттенхэм": "vitasmachiha",
        "Атлетико Мадрид": "zazz_33117",
        "Барселона": "Kurilril5",
        "Реал Мадрид": "Fede_15r",
        "Бавария": "MAGMDV_77",
        "Ливерпуль": "Rusasf",
        "Челси": "ArsenalSte",
        "Наполи": "ilia575",
        "Ювентус": "mms_op",
        "Рома": "lsmaksimmn",
        "ПСЖ": "tornike07",
        "Галатасарай": "Diktator_new",
        "Аль-Наср": "Kadyr_42",
    },
    4: {
        "Байя": "ReiZekk",
        "Милан": "Nixan23",
        "Боруссия Дортмунд": "MemoryYouSs",
        "Интер Милан": "Flasin5",
        "Брайтон": "k1nkyua",
        "Байер": "Leon_2515",
        "Лейпциг": "falIingapart",
        "Эвертон": "epl_l",
        "Аталанта": "pdsnvk",
        "Астон Вилла": "Lyubimov_Aleksandr",
        "Бешикташ": "sp1r1tVSA",
        "Интер Майами": "ARTIKggvp",  # в списке было «Интер Майми»
        "Бетис": "t3miy",
        "Аль-Хиляль": "Komarik97",
        "Ньюкасл": "tshmrrr",
        "Атлетик Бильбао": "kostya94petrik",
    },
    3: {
        "Сандерленд": "Shotik_UA",
        "Ноттингем Форест": "sergeynobody1",
        "Реал Сосьедад": "XTrent20",
        "Париж": "azs5652",
        "Фенербахче": "Artilawyer",
        "Комо": "kirillchuk_927",
        "Брентфорд": "Perdun_1337",
        "Кристал Пэлас": "Ghoust_tag",
        "Аль-Ахли": "Rodza20",
        "Лион": "sayvvel",
        "Борнмут": "TarEgiazaryan",
        "Аль-Иттихад": "Dr_Wh11te",
        "Трабзонспор": "aidarreezz",
        "Вильярреал": "Acidonchik_95",
        "Штутгарт": "Daimond_Highlight",
        "Болонья": "LazyMaxxAA",
    },
    2: {
        "Вулверхэмптон": "Davtyan_55",  # в списке было «Вулверхємтон»
        "Бурирам": "lvckri",  # в списке было «Буринам»
        "Валенсия": "sulassll",
        "Сельта": "saymino1",
        "Ривер Плейт": "Vladimir_5500",
        "Аякс": "virkilainen",
        "Спортинг": "govorigde",
        "Монако": "umbra_mind",
        "Бенфика": "Artem53824",
        "Фулхэм": "mitixfc",  # в списке было «Фулхєм»
        "Хоффенхайм": "Forzainternationale",
        "Ланс": "Turolen",
        "Аль-Кадисия": "GeorgiyKostenko",
        "Торино": "Tonyloki57",
        "Лос Анджелес": "Prizrakks",
        "ПСВ": "vtrrgyg",
    },
}


class PlanError(Exception):
    """План не сходится — до записи в БД дело не доходит."""


@dataclass
class Binding:
    """Одна строка плана: кому какой клуб и что с этим делать."""

    club: str
    username: str
    user: dict | None = None
    action: str = "bind"  # bind | rebind | keep | create | missing


@dataclass
class DivisionPlan:
    number: int
    division: dict
    bindings: list[Binding] = field(default_factory=list)
    unclaimed: list[str] = field(default_factory=list)


ACTION_LABELS = {
    "keep": "= уже привязан",
    "bind": "+ привязать",
    "rebind": "~ переставить",
    "create": "* дорегистрировать и привязать",
    "missing": "! нет в базе",
}


def resolve_division(number: int) -> dict:
    """Строка дивизиона по каноническому коду `DIV_{number}`."""
    division = database.get_division_by_code(f"DIV_{number}")
    if not division:
        raise PlanError(
            f"Дивизион с кодом DIV_{number} не найден. "
            f"Коды 1–5 чинит миграция 012_canonical_division_codes — запустите бота "
            f"или database.init_db() на этой базе."
        )
    return division


def resolve_club(name: str, roster: list[str]) -> str:
    """Каноническое имя клуба внутри ростера дивизиона.

    Сверяем точно и по `normalize_team_name` (он сворачивает «ё»/«э» и дефисы),
    больше ничем. Похожее имя скрипт только предлагает в тексте ошибки, но не
    подставляет: массовая привязка, угадавшая клуб, отдаёт чужую команду молча, и
    заметно это станет уже по расписанию.
    """
    name_clean = name.strip()
    for club in roster:
        if club.lower() == name_clean.lower():
            return club

    normalized = database.normalize_team_name(name_clean)
    matches = [club for club in roster if database.normalize_team_name(club) == normalized]
    if len(matches) == 1:
        return matches[0]

    hint = get_close_matches(name_clean, roster, n=1, cutoff=0.6)
    suggestion = f" Похоже на «{hint[0]}»." if hint else ""
    raise PlanError(f"Клуб «{name_clean}» не входит в сезонный состав дивизиона.{suggestion}")


def find_user(username: str) -> dict | None:
    """Участник по @username.

    `find_user_by_ref` умеет искать ещё и по названию клуба — отсекаем этот путь,
    иначе username, совпавший с именем команды, привяжет не того человека.
    """
    username_clean = username.strip().lstrip("@")
    user = database.find_user_by_ref(username_clean)
    if not user:
        return None
    if (user.get("username") or "").lower() != username_clean.lower():
        return None
    return user


def build_plan(numbers: list[int], create_missing: bool) -> list[DivisionPlan]:
    """Собрать и проверить план целиком до первой записи в БД.

    Проверяем всё сразу, потому что наполовину применённая расстановка хуже
    неприменённой: часть клубов уже отобрана у прежних владельцев, а по какой
    строке прогон споткнулся — видно только в логе.
    """
    seen_usernames: dict[str, str] = {}
    plans: list[DivisionPlan] = []

    for number in numbers:
        division = resolve_division(number)
        roster = list(config.DIVISION_CLUBS.get((division["code"] or "").upper(), []))
        if not roster:
            raise PlanError(f"Дивизион {number} ({division['code']}) без сезонного состава клубов.")

        plan = DivisionPlan(number=number, division=division)
        claimed: dict[str, str] = {}

        for raw_club, raw_username in BINDINGS[number].items():
            club = resolve_club(raw_club, roster)
            username = raw_username.strip().lstrip("@")

            if club in claimed:
                raise PlanError(f"Клуб «{club}» назначен дважды: @{claimed[club]} и @{username}.")
            claimed[club] = username

            owner_of = seen_usernames.get(username.lower())
            if owner_of:
                raise PlanError(f"@{username} назначен на два клуба: «{owner_of}» и «{club}».")
            seen_usernames[username.lower()] = club

            user = find_user(username)
            if not user:
                action = "create" if create_missing else "missing"
            elif (user.get("team_name") or "").strip().lower() == club.lower() \
                    and user.get("division_id") == division["id"]:
                action = "keep"
            elif (user.get("team_name") or "").strip():
                action = "rebind"
            else:
                action = "bind"

            plan.bindings.append(Binding(club=club, username=username, user=user, action=action))

        plan.unclaimed = [club for club in roster if club not in claimed]
        plans.append(plan)

    return plans


def apply_division(plan: DivisionPlan) -> tuple[int, list[str]]:
    """Применить план одного дивизиона одной транзакцией.

    `transaction()` реентрантен, поэтому вложенные `set_player_club` /
    `assign_user_division` пишут в неё же: дивизион ложится целиком или никак.
    """
    changed = 0
    problems: list[str] = []
    div_id = plan.division["id"]

    with database.transaction():
        for binding in plan.bindings:
            if binding.action in ("keep", "missing"):
                continue

            if binding.action == "create":
                database.pre_register_player_to_division(binding.username, div_id)

            ok, message = database.set_player_club(binding.username, binding.club)
            if not ok:
                problems.append(f"@{binding.username} → «{binding.club}»: {message}")
                continue

            user = find_user(binding.username)
            if user and user.get("division_id") != div_id:
                database.assign_user_division(user["telegram_id"], div_id)
            changed += 1

    return changed, problems


def report_plan(plans: list[DivisionPlan], apply: bool) -> None:
    print("=" * 72)
    print(" 🔗 ПРИВЯЗКА УЧАСТНИКОВ К КЛУБАМ СЕЗОНА")
    if apply:
        print(" ⚠️  Привязка снимает клуб с прежнего владельца и обнуляет варны обоим.")
    else:
        print(" ℹ️  DRY-RUN: база не меняется. Применить — флаг --apply.")
    print("=" * 72)

    for plan in plans:
        div = plan.division
        print(f"\n📂 {div['name']} (id={div['id']}, code={div['code']}) — {len(plan.bindings)} привязок:")
        for idx, binding in enumerate(plan.bindings, start=1):
            label = ACTION_LABELS[binding.action]
            current = (binding.user or {}).get("team_name") or ""
            extra = f" (сейчас «{current}»)" if binding.action == "rebind" else ""
            print(f"  {idx:2d}. {binding.club:<22} ← @{binding.username:<22} {label}{extra}")

        if plan.unclaimed:
            print(f"   ⚪ Без владельца останутся: {', '.join(plan.unclaimed)}")

        orphans = [
            f"@{u['username']}" if u.get("username") else f"ID {u['telegram_id']}"
            for u in database.get_division_users(div["id"])
            if not (u.get("team_name") or "").strip()
            and (u.get("username") or "").lower() not in {b.username.lower() for b in plan.bindings}
        ]
        if orphans:
            print(f"   ⚪ Участники дивизиона без клуба и вне списка: {', '.join(orphans)}")


def report_totals(plans: list[DivisionPlan], applied: dict[int, int], problems: list[str], apply: bool) -> int:
    counts: dict[str, int] = {}
    for plan in plans:
        for binding in plan.bindings:
            counts[binding.action] = counts.get(binding.action, 0) + 1

    print("\n" + "=" * 72)
    print(" 📊 ИТОГИ:")
    for action, label in ACTION_LABELS.items():
        if counts.get(action):
            print(f"  • {label:<32} {counts[action]}")
    if apply:
        for plan in plans:
            print(f"  • {plan.division['name']}: записано {applied.get(plan.number, 0)}")
    if problems:
        print("\n ❌ Не применено:")
        for problem in problems:
            print(f"  • {problem}")
    if counts.get("missing"):
        print("\n ℹ️  Участников нет в базе — они не привязаны. Дорегистрировать их "
              "временными id: добавьте --create-missing.")
    print("=" * 72 + "\n")

    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Массовая привязка участников к клубам сезона")
    parser.add_argument("--apply", action="store_true", help="Применить изменения (по умолчанию dry-run)")
    parser.add_argument("--create-missing", action="store_true",
                        help="Дорегистрировать участников, которых нет в базе (временный отрицательный id)")
    parser.add_argument("--divisions", default=None,
                        help="Только эти дивизионы, через запятую (например 4,5). По умолчанию все из таблицы")

    args = parser.parse_args()

    if args.divisions:
        try:
            numbers = [int(part) for part in args.divisions.split(",") if part.strip()]
        except ValueError:
            return _fail(f"Не разобрать --divisions: {args.divisions}")
        unknown = [n for n in numbers if n not in BINDINGS]
        if unknown:
            return _fail(f"В таблице привязок нет дивизионов: {unknown}. Есть: {sorted(BINDINGS)}")
    else:
        numbers = sorted(BINDINGS, reverse=True)

    database.init_db()

    try:
        plans = build_plan(numbers, create_missing=args.create_missing)
    except PlanError as exc:
        return _fail(str(exc))

    report_plan(plans, apply=args.apply)

    applied: dict[int, int] = {}
    problems: list[str] = []
    if args.apply:
        for plan in plans:
            changed, plan_problems = apply_division(plan)
            applied[plan.number] = changed
            problems.extend(plan_problems)

    return report_totals(plans, applied, problems, apply=args.apply)


def _fail(message: str) -> int:
    print(f"❌ {message}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
