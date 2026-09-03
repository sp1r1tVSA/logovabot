import sqlite3

conn = sqlite3.connect('league.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print("ALL TABLES:", tables)

for t in ['markets', 'bet_audit_log', 'user_bets', 'bet_items']:
    cur.execute(f"PRAGMA table_info({t})")
    cols = [r[1] for r in cur.fetchall()]
    print(f"{t}: {cols}")

# Check if idempotency_payload_hash column exists in user_bets
cur.execute("PRAGMA table_info(user_bets)")
ub_cols = {r[1] for r in cur.fetchall()}
print("user_bets has idempotency_payload_hash:", 'idempotency_payload_hash' in ub_cols)
print("bet_items has odds_at_placement:", True)  # we saw this above

conn.close()
