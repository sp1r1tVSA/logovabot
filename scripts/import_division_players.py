"""
Import and pre-register tournament players across divisions into SQLite database.

Usage:
    python scripts/import_division_players.py
    python scripts/import_division_players.py --dry-run
"""

import sys
import os
import argparse
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import database

# Map of divisions and players
PLAYERS_BY_DIVISION: dict[int, list[str]] = {
    5: [
        "Fede_15r",
        "tornike07",
        "Kurilril5",
        "MAGMDV_77",
        "Rusasf",
        "joraknaz",
        "ArsenalSte",
        "Daot1",
        "agosv",
        "Diktator_new",
        "ilia575",
        "zazz_33117",
        "vitasmachiha",
        "lsmaksimmn",
        "mms_op",
        "Kadyr_42",
    ],
    4: [
        "Nixan23",
        "Flasin5",
        "epl_l",
        "k1nkyua",
        "MemoryYouSs",
        "Komarik97",
        "kostya94petrik",
        "falIingapart",
        "Leon_2515",
        "Lyubimov_Aleksandr",
        "ARTIKggvp",
        "t3miy",
        "sp1r1tVSA",
        "pdsnvk",
        "tshmrrr",
        "ReiZekk",
    ],
    3: [
        "XTrent20",
        "TarEgiazaryan",
        "Shotik_UA",
        "perdun_1337",
        "sergeynobody1",
        "Artilawyer",
        "kirillchuk_927",
        "azs5652",
        "Rodza20",
        "Ghoust_tag",
        "Daimond_Highlight",
        "Acidonchik_95",
        "sayvvel",
        "Dr_Wh11te",
        "aidarreezz",
        "LazyMaxxAA",
    ],
    2: [
        "Vladimir_5500",
        "govorigde",
        "Davtyan_55",
        "sulassll",
        "Artem53824",
        "saymino1",
        "mitixfc",
        "lvckri",
        "Turolen",
        "Tonyloki57",
        "umbra_mind",
        "Forzainternationale",
        "GeorgiyKostenko",
        "virkilainen",
        "Prizrakks",
        "vtrrgyg",
    ],
    1: [
        "Saharokk8830",
        "curseedoeleo",
        "TheFlakeSo",
        "typeuw",
        "ArtemPalagin",
        "Snikers2121",
        "Nukolaich",
        "Rostyslav07",
        "Maximilian4",
        "Doakkk",
        "qweasdzxc22819",
        "brando055",
        "Serghe1KO",
        "ch1lyx",
    ],
}

DIVISION_NAMES = {
    1: "Дивизион 1",
    2: "Дивизион 2",
    3: "Дивизион 3",
    4: "Дивизион 4",
    5: "Дивизион 5",
}

SELF_REGISTER_NOTES = {
    1: [
        "Участник без тега @ (зарегистрируется самостоятельно через бота)",
        "Участник без тега @ (зарегистрируется самостоятельно через бота)",
    ]
}


def import_players(dry_run: bool = False) -> dict:
    """Import players by division into the database."""
    database.init_db()

    results = {
        "created": 0,
        "updated": 0,
        "total": 0,
        "by_division": {},
    }

    print("=" * 65)
    print(" 🚀 ИМПОРТ ИГРОКОВ В БАЗУ ДАННЫХ ПО ДИВИЗИОНАМ")
    if dry_run:
        print(" ⚠️  РЕЖИМ ТЕСТИРОВАНИЯ (DRY-RUN): изменения в БД НЕ сохраняются")
    print("=" * 65)

    for div_id in sorted(PLAYERS_BY_DIVISION.keys(), reverse=True):
        div_name = DIVISION_NAMES.get(div_id, f"Дивизион {div_id}")
        players = PLAYERS_BY_DIVISION[div_id]
        print(f"\n📂 {div_name} (Всего игроков: {len(players)}):")

        div_stats = {"created": 0, "updated": 0, "players": []}

        for idx, raw_username in enumerate(players, start=1):
            username = raw_username.strip().lstrip("@")
            
            if dry_run:
                # Check current status in DB
                user = database.find_user_by_ref(username)
                if user:
                    action = f"Будет обновлён (ID: {user['telegram_id']}, текущий дивизион: {user.get('division_id')})"
                    div_stats["updated"] += 1
                else:
                    action = "Будет создан (временный отрицательный ID)"
                    div_stats["created"] += 1
                print(f"  {idx:2d}. @{username:<22} -> {action}")
                div_stats["players"].append({"username": username, "status": action})
            else:
                # Check before insert/update
                existed_before = database.find_user_by_ref(username)
                user_id = database.pre_register_player_to_division(username, div_id)
                if existed_before and existed_before["telegram_id"] == user_id:
                    status_text = f"Обновлён (ID: {user_id})"
                    div_stats["updated"] += 1
                else:
                    status_text = f"Создан (Временный ID: {user_id})"
                    div_stats["created"] += 1

                print(f"  {idx:2d}. @{username:<22} -> ✅ {status_text}")
                div_stats["players"].append({"username": username, "user_id": user_id, "status": status_text})

        # Notes for self-registering users
        if div_id in SELF_REGISTER_NOTES:
            for note in SELF_REGISTER_NOTES[div_id]:
                print(f"   *  ℹ️ {note}")

        results["created"] += div_stats["created"]
        results["updated"] += div_stats["updated"]
        results["total"] += len(players)
        results["by_division"][div_id] = div_stats

    print("\n" + "=" * 65)
    print(" 📊 ИТОГИ ИМПОРТА:")
    print(f"  • Всего обработано игроков: {results['total']}")
    print(f"  • Новых создано:           {results['created']}")
    print(f"  • Существующих обновлено:   {results['updated']}")
    for div_id in sorted(PLAYERS_BY_DIVISION.keys()):
        count = len(PLAYERS_BY_DIVISION[div_id])
        notes_count = len(SELF_REGISTER_NOTES.get(div_id, []))
        extra_info = f" (+{notes_count} без @)" if notes_count else ""
        print(f"  • {DIVISION_NAMES[div_id]}: {count} игроков{extra_info}")
    print("=" * 65 + "\n")

    return results


def main():
    parser = argparse.ArgumentParser(description="Импорт игроков по дивизионам в базу данных Logovobot")
    parser.add_argument("--dry-run", action="store_true", help="Проверить данные без внесения изменений в БД")
    args = parser.parse_args()

    import_players(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
