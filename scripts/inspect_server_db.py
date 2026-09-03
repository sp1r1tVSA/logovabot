"""Inspect the server DB snapshot: schema state and data relevant to debt backfill."""
import sqlite3
import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB = str(PROJECT_ROOT / "server_league.db")
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print("integrity:", conn.execute("PRAGMA integrity_check").fetchone()[0])

tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print("\ntables:", tables)

# Check whether new migrations are already present on this snapshot
cols_users = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
cols_matches = [r[1] for r in conn.execute("PRAGMA table_info(matches)")]
print("\nusers has pending_notification:", "pending_notification" in cols_users)
print("matches has frozen_seconds:", "frozen_seconds" in cols_matches)
print("matches has frozen_at:", "frozen_at" in cols_matches)

idx = [r[1] for r in conn.execute("PRAGMA index_list(users)")]
print("users unique team index:", idx)

# Warns overview
print("\n=== Игроки с варнами ===")
for r in conn.execute("SELECT telegram_id, username, team_name, warn_count FROM users WHERE warn_count > 0 ORDER BY warn_count DESC"):
    print(f"  {r['telegram_id']} @{r['username']} [{r['team_name']}] — {r['warn_count']}")

# Duplicate club check (dedup will act on these at next bot start)
print("\n=== Дубликаты клубов ===")
dups = list(conn.execute(
    "SELECT LOWER(TRIM(team_name)) t, COUNT(*) n, GROUP_CONCAT(telegram_id) ids FROM users "
    "WHERE team_name IS NOT NULL GROUP BY t HAVING n > 1"
))
if not dups:
    print("  нет")
for d in dups:
    print(f"  {d['t']}: {d['n']} шт (ids: {d['ids']})")

# Matches confirmed since 24.08 00:00 local
since = "2026-08-24 00:00:00"
rows = list(conn.execute("""
    SELECT m.id, m.round_number, m.status, m.played_at, r.deadline,
           m.player1_id, m.player2_id, m.player1_team, m.player2_team,
           m.is_extended
    FROM matches m LEFT JOIN rounds r ON r.round_number = m.round_number
    WHERE m.status = 'confirmed' AND m.played_at >= ?
    ORDER BY m.played_at ASC
""", (since,)))
print(f"\n=== Матчей confirmed с 24.08.2026 00:00: {len(rows)} ===")
for r in rows:
    dl = r["deadline"]
    played = r["played_at"]
    late = "?"
    if dl:
        # parse flexible-ish: try common formats
        for fmt in ("%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                dl_dt = datetime.datetime.strptime(dl, fmt)
                p_dt = datetime.datetime.strptime(played[:19], "%Y-%m-%d %H:%M:%S")
                late = f"{'ПОЗЖЕ дедлайна +' if p_dt > dl_dt else 'вовремя'}{(p_dt-dl_dt).total_seconds()/3600:+.1f}h".replace("+", "", 1) if False else ("ПОЗДНО" if p_dt > dl_dt else "вовремя")
                break
            except ValueError:
                continue
    else:
        late = "нет дедлайна"
    ext = " ⏸заморожен" if r["is_extended"] else ""
    print(f"  #{r['id']} тур {r['round_number']} | {r['player1_team']} vs {r['player2_team']} | {played} | {late}{ext}")

# Rounds deadlines around now
print("\n=== Туры (открытые/дедлайны) ===")
for r in conn.execute("SELECT round_number, is_open, deadline FROM rounds WHERE round_number >= 20 ORDER BY round_number"):
    print(f"  тур {r['round_number']}: open={r['is_open']}, deadline={r['deadline']}")

conn.close()
