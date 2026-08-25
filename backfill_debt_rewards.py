"""
Backfill: re-process debt rewards (-1 warn per played debt match) for matches
confirmed since a given date. Fixes missed rewards caused by the sqlite3.Row bug.

Dry-run by default — prints what WOULD be done. Pass --apply to execute.

Usage (from bot directory):
    python3 backfill_debt_rewards.py --since "24.08.2026 00:00"
    python3 backfill_debt_rewards.py --since "24.08.2026 00:00" --apply
"""
import argparse
import datetime
import sys

import database


def parse_since(s: str) -> datetime.datetime:
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise SystemExit(f"Не удалось распознать дату: {s}")


def user_already_unwarned_for_round(p_id: int, round_number: int, played_at_str: str) -> bool:
    """True if the player already received a DEBT_UNWARN ledger entry for this
    round after the match was played (covers rewards granted by the old code
    that crashed mid-way and never recorded the reward_given stage)."""
    with database.transaction() as conn:
        row = conn.execute(
            "SELECT 1 FROM user_warns "
            "WHERE user_id = ? AND type = 'DEBT_UNWARN' AND reason LIKE ? "
            "AND created_at >= ? LIMIT 1",
            (p_id, f"%{round_number} тур%", played_at_str)
        ).fetchone()
        return row is not None


def main() -> None:
    parser = argparse.ArgumentParser(description="Retroactive debt-reward backfill")
    parser.add_argument("--since", required=True, help="Дата начала проверки, напр. \"24.08.2026 00:00\"")
    parser.add_argument("--apply", action="store_true", help="Реально применить изменения (по умолчанию dry-run)")
    args = parser.parse_args()

    since = parse_since(args.since)

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT m.id, m.round_number, m.player1_id, m.player2_id,
                   m.player1_team, m.player2_team, m.played_at, r.deadline
            FROM matches m
            LEFT JOIN rounds r ON r.round_number = m.round_number
            WHERE m.status = 'confirmed'
              AND m.played_at IS NOT NULL
              AND m.played_at >= ?
              AND (m.tournament_type IS NULL OR m.tournament_type = 'league')
            ORDER BY m.played_at ASC
        """, (since.strftime("%Y-%m-%d %H:%M:%S"),))
        matches = [dict(r) for r in cursor.fetchall()]

    print(f"Матчей, подтверждённых с {since.strftime('%d.%m.%Y %H:%M')}: {len(matches)}")

    processed = 0
    skipped_ontime = 0
    skipped_marked = 0
    skipped_nodeadline = 0
    plans = []

    for m in matches:
        match_id = m["id"]
        dl = database.parse_flexible_datetime(m["deadline"]) if m["deadline"] else None
        if not dl:
            skipped_nodeadline += 1
            continue

        played_at = datetime.datetime.strptime(m["played_at"], "%Y-%m-%d %H:%M:%S")

        # Result entered before the deadline — not a debt, nothing to do
        if played_at <= dl:
            skipped_ontime += 1
            continue

        # Already processed under the fixed system
        if database.has_debt_stage(match_id, "reward_given"):
            skipped_marked += 1
            continue

        hours_late = (played_at - dl).total_seconds() / 3600.0

        players = []
        for p_id, team in ((m["player1_id"], m["player1_team"]), (m["player2_id"], m["player2_team"])):
            u = None
            if p_id:
                u = database.get_user(p_id)
            if not u and team:
                u = database.find_user_by_team(team)
            pid = dict(u)["telegram_id"] if u else None
            # Skip players already rewarded through the old (crashing) path:
            # their unwarn was applied but the marker stage was never written.
            if pid and user_already_unwarned_for_round(pid, m["round_number"], m["played_at"]):
                print(f"  Матч #{match_id}: игрок {pid} уже получал списание за {m['round_number']} тур — пропущен")
                pid = None
            players.append((pid, team))

        plans.append((match_id, m["round_number"], hours_late, players))

    mode = "ПРИМЕНЕНИЕ" if args.apply else "DRY-RUN (ничего не изменено)"
    print(f"\n=== {mode} ===")
    for match_id, rn, hours_late, players in plans:
        print(f"\nМатч #{match_id} | {rn}-й тур | просрочка на момент внесения: ~{int(hours_late)}ч")
        for p_id, team in players:
            if not p_id:
                print(f"  • [{team}] — игрок не найден в базе, пропущен")
                continue
            u = dict(database.get_user(p_id) or {})
            cur_warns = u.get("warn_count") or 0
            print(f"  • @{u.get('username') or p_id} [{team}] — варнов сейчас: {cur_warns}/{4} → будет: {max(0, cur_warns - 1)}")

        if args.apply:
            for p_id, _team in players:
                if p_id:
                    database.apply_debt_played_reward(p_id, rn)
                    database.record_debt_stage(match_id, "reward_given")
            processed += 1

    print("\n=== ИТОГИ ===")
    print(f"Матчей-долгов к обработке: {len(plans)}")
    print(f"Пропущено (сыграны вовремя): {skipped_ontime}")
    print(f"Пропущены (уже обработаны): {skipped_marked}")
    print(f"Пропущено (нет дедлайна): {skipped_nodeadline}")
    if args.apply:
        print(f"Обработано: {processed}")
    else:
        print("\nЭто был dry-run. Для применения запустите с флагом --apply")


if __name__ == "__main__":
    sys.exit(main())
