#!/usr/bin/env python3
"""
scripts/cleanup_preseason_data.py

Предсезонная уборка базы: снести мусор, оставшийся от КПЛ и от демо-прогонов, и
дозаполнить экономику тем участникам, у кого её нет.

Четыре независимых блока:

  1. teams   — 16 демо-клубов (North Wolves, Red Falcons, …), засеянных прогоном
               Mini App 2026-09-06. Ни у одного нет владельца, ни один не входит
               в сезонный ростер `config.CLUB_REGISTRY`. Живой состав лиги живёт
               в `users.team_name`, таблица `teams` в текущем коде не читается —
               но при разборе инцидента она путает.

  2. config  — ключи `system_config`, оставшиеся от КПЛ:
               • league_table_msg_id  — мёртвый: таблица постится по ключу
                 `league_table_msg_id_div_{id}` (handlers/base.py);
               • results_topic_id, squad_topic_id — НЕ мёртвые: это работающие
                 фолбэки (handlers/admin.py, handlers/cabinet.py, handlers/drafts.py).
                 Они указывают на топики старой группы КПЛ, которых больше нет,
                 поэтому фолбэк сейчас отправляет сообщения в никуда. Без ключа
                 фолбэк даёт None — сообщение уходит в общий тред группы, что
                 заметно и чинится. Правильные id админ задаёт заново из новой
                 группы («Настройка топиков» в админке);
               • lab_test_user_id, lab_current_step — состояние тестовой
                 лаборатории с id 999999999.

  3. orphans — строки экономики, чей user_id больше не существует в `users`
               (кошельки, прогресс, транзакции монет, ставки). Остались от чистки
               ростера: коуч удалён, его кошелёк с монетами — нет. Ставки в
               статусе 'pending' скрипт НЕ трогает и показывает отдельно: их
               сначала должен закрыть расчётчик.

  4. backfill — участникам дивизионов (positive telegram_id + division_id) без
               кошелька / без прогресса создаёт их штатными
               `database.get_or_create_wallet` и `get_or_create_progression`,
               то есть ровно то же самое, что произойдёт при первом заходе в
               Mini App. Делается заранее, чтобы стартовые 677 монет и уровень 1
               уже были на месте к первому туру.

Режимы:

  1. Dry-run (по умолчанию) — только читает и печатает план:
       python scripts/cleanup_preseason_data.py

  2. Применение:
       python scripts/cleanup_preseason_data.py --apply

  3. Отдельные блоки:
       python scripts/cleanup_preseason_data.py --apply --only orphans --only backfill

Перед --apply на боевой базе имеет смысл остановить бота и снять копию:

    cp league.db "league.db.bak-$(date +%F-%H%M)"

Путь к БД берётся из config.DB_PATH (env LEAGUE_SQLITE_PATH). Dry-run базу не
открывает на запись и не запускает миграции — читает как есть.
"""

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
import database

# Демо-клубы засеяны одним прогоном 2026-09-06 09:29. Список задан явно, а не
# вычислен как «всё, чего нет в ростере»: правило «нет в ростере» снесло бы и
# клуб, который админ завёл вручную под товарищеский матч.
DEMO_TEAM_NAMES = [
    "North Wolves", "Red Falcons", "Iron Lions", "Black Eagles",
    "Golden Sharks", "Blue Titans", "Royal Bears", "Storm FC",
    "Silver Foxes", "Dark Knights", "Phoenix United", "Thunder City",
    "Atomic FC", "Victory Stars", "Capital Dragons", "United Kings",
]

STALE_CONFIG_KEYS = [
    "league_table_msg_id",
    "results_topic_id",
    "squad_topic_id",
    "lab_test_user_id",
    "lab_current_step",
]

BLOCKS = ("teams", "config", "orphans", "backfill")


@dataclass
class Plan:
    demo_teams: list[dict] = field(default_factory=list)
    kept_teams: list[str] = field(default_factory=list)
    stale_keys: list[tuple[str, str]] = field(default_factory=list)
    orphan_wallets: list[dict] = field(default_factory=list)
    orphan_progression: list[dict] = field(default_factory=list)
    orphan_tx_count: int = 0
    orphan_tx_sum: int = 0
    orphan_bets: list[dict] = field(default_factory=list)
    pending_orphan_bets: list[dict] = field(default_factory=list)
    missing_wallets: list[dict] = field(default_factory=list)
    missing_progression: list[dict] = field(default_factory=list)
    homeless_users: list[dict] = field(default_factory=list)


def _rows(cursor) -> list[dict]:
    return [dict(row) for row in cursor.fetchall()]


def build_plan(blocks: set[str]) -> Plan:
    """Собрать весь план одним чтением — до первой записи.

    Читаем всё сразу и в одной транзакции, чтобы отчёт и применение видели одну
    и ту же базу: между ними может пройти минута, а бот, если его не остановили,
    успеет принять ставку.
    """
    plan = Plan()
    registry = {name.lower() for name in config.CLUB_REGISTRY}

    with database.transaction() as conn:
        cursor = conn.cursor()

        if "teams" in blocks:
            cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='teams'")
            if cursor.fetchone():
                placeholders = ",".join("?" for _ in DEMO_TEAM_NAMES)
                cursor.execute(
                    f"SELECT id, name, owner_id, created_at FROM teams WHERE name IN ({placeholders})",
                    DEMO_TEAM_NAMES,
                )
                for team in _rows(cursor):
                    # Демо-имя, внезапно получившее владельца или попавшее в ростер,
                    # демо-строкой уже не является — такую не трогаем.
                    if team["owner_id"] is not None or team["name"].lower() in registry:
                        plan.kept_teams.append(f"{team['name']} (id={team['id']}, owner_id={team['owner_id']})")
                        continue
                    cursor.execute("SELECT COUNT(*) AS n FROM users WHERE LOWER(TRIM(team_name)) = ?",
                                   (team["name"].strip().lower(),))
                    if cursor.fetchone()["n"]:
                        plan.kept_teams.append(f"{team['name']} (id={team['id']}, есть коуч в users)")
                        continue
                    plan.demo_teams.append(team)

                cursor.execute("SELECT name FROM teams ORDER BY id")
                for row in _rows(cursor):
                    if row["name"] in DEMO_TEAM_NAMES or row["name"].lower() in registry:
                        continue
                    plan.kept_teams.append(f"{row['name']} (не демо и не в ростере — не трогаем)")

        if "config" in blocks:
            for key in STALE_CONFIG_KEYS:
                value = database.get_config(key)
                if value is not None:
                    plan.stale_keys.append((key, value))

        if "orphans" in blocks:
            cursor.execute("""
                SELECT w.user_id, w.balance, w.bets_count
                FROM user_wallets w
                LEFT JOIN users u ON u.telegram_id = w.user_id
                WHERE u.telegram_id IS NULL
                ORDER BY w.user_id
            """)
            plan.orphan_wallets = _rows(cursor)

            cursor.execute("""
                SELECT p.user_id, p.level, p.total_xp_earned
                FROM user_progression p
                LEFT JOIN users u ON u.telegram_id = p.user_id
                WHERE u.telegram_id IS NULL
                ORDER BY p.user_id
            """)
            plan.orphan_progression = _rows(cursor)

            cursor.execute("""
                SELECT COUNT(*) AS n, COALESCE(SUM(t.amount), 0) AS total
                FROM coin_transactions t
                LEFT JOIN users u ON u.telegram_id = t.user_id
                WHERE u.telegram_id IS NULL
            """)
            row = cursor.fetchone()
            plan.orphan_tx_count, plan.orphan_tx_sum = row["n"], row["total"]

            cursor.execute("""
                SELECT b.id, b.user_id, b.amount, b.status
                FROM user_bets b
                LEFT JOIN users u ON u.telegram_id = b.user_id
                WHERE u.telegram_id IS NULL
                ORDER BY b.id
            """)
            for bet in _rows(cursor):
                # Незакрытая ставка — обязательство перед расчётчиком, а не мусор.
                if bet["status"] == "pending":
                    plan.pending_orphan_bets.append(bet)
                else:
                    plan.orphan_bets.append(bet)

        if "backfill" in blocks:
            cursor.execute("""
                SELECT u.telegram_id, u.username, u.team_name, u.division_id
                FROM users u
                LEFT JOIN user_wallets w ON w.user_id = u.telegram_id
                WHERE u.telegram_id > 0 AND u.division_id IS NOT NULL AND w.user_id IS NULL
                ORDER BY u.division_id, u.telegram_id
            """)
            plan.missing_wallets = _rows(cursor)

            cursor.execute("""
                SELECT u.telegram_id, u.username, u.team_name, u.division_id
                FROM users u
                LEFT JOIN user_progression p ON p.user_id = u.telegram_id
                WHERE u.telegram_id > 0 AND u.division_id IS NOT NULL AND p.user_id IS NULL
                ORDER BY u.division_id, u.telegram_id
            """)
            plan.missing_progression = _rows(cursor)

        # Справочно, ничего не делаем: люди нажали /start и остались без
        # дивизиона и без клуба. Удалять их нельзя — это живые зрители, у них
        # есть кошелёк и право ставить.
        cursor.execute("""
            SELECT telegram_id, username, role
            FROM users
            WHERE telegram_id > 0 AND division_id IS NULL
              AND (team_name IS NULL OR TRIM(team_name) = '')
            ORDER BY telegram_id
        """)
        plan.homeless_users = _rows(cursor)

    return plan


def apply_plan(plan: Plan, blocks: set[str]) -> dict[str, int]:
    """Применить план. Каждый блок — своя транзакция: сбой одного не рвёт остальные."""
    done: dict[str, int] = {}

    if "teams" in blocks and plan.demo_teams:
        with database.transaction() as conn:
            cursor = conn.cursor()
            ids = [team["id"] for team in plan.demo_teams]
            placeholders = ",".join("?" for _ in ids)
            cursor.execute(f"DELETE FROM teams WHERE id IN ({placeholders})", ids)
            done["teams"] = cursor.rowcount

    if "config" in blocks and plan.stale_keys:
        with database.transaction() as conn:
            cursor = conn.cursor()
            keys = [key for key, _ in plan.stale_keys]
            placeholders = ",".join("?" for _ in keys)
            cursor.execute(f"DELETE FROM system_config WHERE key IN ({placeholders})", keys)
            done["config"] = cursor.rowcount

    if "orphans" in blocks:
        with database.transaction() as conn:
            cursor = conn.cursor()
            removed = 0

            if plan.orphan_bets:
                ids = [bet["id"] for bet in plan.orphan_bets]
                placeholders = ",".join("?" for _ in ids)
                # bet_items висят на bet_id с ON DELETE CASCADE, а foreign_keys=ON
                # выставлен в get_connection — позиции уйдут сами.
                cursor.execute(f"DELETE FROM user_bets WHERE id IN ({placeholders})", ids)
                removed += cursor.rowcount

            # Транзакции сносим первыми: кошелёк ещё на месте, и если DELETE
            # упадёт, инвариант «баланс = сумма ленты» не разъедется.
            cursor.execute("""
                DELETE FROM coin_transactions
                WHERE user_id NOT IN (SELECT telegram_id FROM users)
            """)
            removed += cursor.rowcount

            if plan.orphan_wallets:
                ids = [row["user_id"] for row in plan.orphan_wallets]
                placeholders = ",".join("?" for _ in ids)
                cursor.execute(f"DELETE FROM user_wallets WHERE user_id IN ({placeholders})", ids)
                removed += cursor.rowcount

            if plan.orphan_progression:
                ids = [row["user_id"] for row in plan.orphan_progression]
                placeholders = ",".join("?" for _ in ids)
                cursor.execute(f"DELETE FROM user_progression WHERE user_id IN ({placeholders})", ids)
                removed += cursor.rowcount

            done["orphans"] = removed

    if "backfill" in blocks:
        created = 0
        # По одной транзакции на участника: `transaction()` реентрантен, так что
        # get_or_create_* пишут в неё же, а неудача одного не отменяет остальных.
        for user in plan.missing_wallets:
            with database.transaction():
                database.get_or_create_wallet(user["telegram_id"])
            created += 1
        for user in plan.missing_progression:
            with database.transaction():
                database.get_or_create_progression(user["telegram_id"])
            created += 1
        done["backfill"] = created

    return done


def _who(user: dict) -> str:
    name = user.get("username") or "—"
    return f"{user['telegram_id']} @{name}"


def report_plan(plan: Plan, blocks: set[str], apply: bool) -> None:
    print("=" * 72)
    print(" 🧹 ПРЕДСЕЗОННАЯ УБОРКА БАЗЫ")
    print(f" 📂 {config.DB_PATH}")
    if apply:
        print(" ⚠️  Режим записи. Бота лучше остановить, базу — скопировать.")
    else:
        print(" ℹ️  DRY-RUN: база не меняется. Применить — флаг --apply.")
    print(f" 🔧 Блоки: {', '.join(sorted(blocks))}")
    print("=" * 72)

    if "teams" in blocks:
        print(f"\n1️⃣  teams — демо-клубы к удалению: {len(plan.demo_teams)}")
        for team in plan.demo_teams:
            print(f"     - id={team['id']:<4} {team['name']:<20} создан {team['created_at']}")
        for kept in plan.kept_teams:
            print(f"     = оставляем: {kept}")

    if "config" in blocks:
        print(f"\n2️⃣  system_config — ключей к удалению: {len(plan.stale_keys)}")
        for key, value in plan.stale_keys:
            print(f"     - {key:<24} = {value!r}")
        if plan.stale_keys:
            print("     ℹ️  results_topic_id / squad_topic_id — рабочие фолбэки на топики "
                  "старой группы. После удаления сообщения уйдут в общий тред; "
                  "новые id задаются из админки «Настройка топиков».")

    if "orphans" in blocks:
        total = (len(plan.orphan_wallets) + len(plan.orphan_progression)
                 + plan.orphan_tx_count + len(plan.orphan_bets))
        print(f"\n3️⃣  Сироты экономики (user_id, которого нет в users) — строк: {total}")
        for row in plan.orphan_wallets:
            print(f"     - кошелёк  {row['user_id']:<12} баланс {row['balance']}, ставок {row['bets_count']}")
        for row in plan.orphan_progression:
            print(f"     - прогресс {row['user_id']:<12} уровень {row['level']}, XP {row['total_xp_earned']}")
        if plan.orphan_tx_count:
            print(f"     - транзакций монет: {plan.orphan_tx_count} на сумму {plan.orphan_tx_sum}")
        for bet in plan.orphan_bets:
            print(f"     - ставка id={bet['id']} ({bet['status']}) {bet['user_id']}, {bet['amount']}")
        for bet in plan.pending_orphan_bets:
            print(f"     ! ставка id={bet['id']} в статусе pending у {bet['user_id']} — "
                  f"НЕ трогаем, сначала расчёт")

    if "backfill" in blocks:
        print(f"\n4️⃣  Дозаполнение экономики участникам дивизионов")
        print(f"     кошельков создать: {len(plan.missing_wallets)} "
              f"(по {config.INITIAL_WALLET_BALANCE} монет)")
        for user in plan.missing_wallets:
            print(f"     + {_who(user):<28} див.{user['division_id']} {user['team_name'] or '—'}")
        print(f"     прогресса создать: {len(plan.missing_progression)} (уровень 1, 0 XP)")
        for user in plan.missing_progression:
            print(f"     + {_who(user):<28} див.{user['division_id']} {user['team_name'] or '—'}")

    if plan.homeless_users:
        print(f"\nℹ️  Справочно: {len(plan.homeless_users)} пользователей без дивизиона и без клуба "
              f"— скрипт их не трогает:")
        for user in plan.homeless_users:
            print(f"     · {_who(user):<28} role={user['role']}")


def report_totals(plan: Plan, done: dict[str, int], apply: bool) -> int:
    print("\n" + "=" * 72)
    print(" 📊 ИТОГИ:")
    if apply:
        print(f"  • удалено демо-клубов:        {done.get('teams', 0)}")
        print(f"  • удалено ключей конфига:     {done.get('config', 0)}")
        print(f"  • удалено строк-сирот:        {done.get('orphans', 0)}")
        print(f"  • создано кошельков/прогресса: {done.get('backfill', 0)}")
    else:
        print(f"  • демо-клубов к удалению:     {len(plan.demo_teams)}")
        print(f"  • ключей конфига к удалению:  {len(plan.stale_keys)}")
        print(f"  • строк-сирот к удалению:     "
              f"{len(plan.orphan_wallets) + len(plan.orphan_progression) + plan.orphan_tx_count + len(plan.orphan_bets)}")
        print(f"  • кошельков/прогресса создать: "
              f"{len(plan.missing_wallets) + len(plan.missing_progression)}")
    if plan.pending_orphan_bets:
        print(f"  ⚠️  ставок pending у несуществующих id: {len(plan.pending_orphan_bets)} — разобрать вручную")
    print("=" * 72 + "\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Предсезонная уборка базы")
    parser.add_argument("--db", type=str, default=None, help="Путь к SQLite базе (по умолчанию config.DB_PATH)")
    parser.add_argument("--apply", action="store_true", help="Применить изменения (по умолчанию dry-run)")
    parser.add_argument("--only", action="append", choices=BLOCKS, default=None,
                        help="Только эти блоки (можно повторять). По умолчанию все")

    args = parser.parse_args()
    blocks = set(args.only) if args.only else set(BLOCKS)

    if args.db:
        import os
        os.environ["LEAGUE_SQLITE_PATH"] = args.db
        config.DB_PATH = args.db
        database.DB_PATH = args.db

    # init_db() пишет (миграции), поэтому в dry-run его нет: отчёт обязан быть
    # читающим. При --apply схема должна быть актуальной — тогда запускаем.
    if args.apply:
        database.init_db()

    plan = build_plan(blocks)
    report_plan(plan, blocks, apply=args.apply)

    done: dict[str, int] = {}
    if args.apply:
        done = apply_plan(plan, blocks)

    return report_totals(plan, done, apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
