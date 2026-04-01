const { Pool } = require("pg");

const connectionString = process.env.DATABASE_URL;

if (!connectionString) {
  throw new Error("DATABASE_URL is missing.");
}

const pool = new Pool({
  connectionString,
  ssl: connectionString.includes("railway")
    ? { rejectUnauthorized: false }
    : false,
});

async function query(text, params = []) {
  return pool.query(text, params);
}

async function withTransaction(work) {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const result = await work(client);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}

async function initDb() {
  await query(`
    CREATE TABLE IF NOT EXISTS league_team_map (
      team_name_norm TEXT PRIMARY KEY,
      team_name_raw TEXT NOT NULL,
      telegram_username TEXT NOT NULL,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
  `);

  await query(`
    CREATE TABLE IF NOT EXISTS league_debt_entries (
      id BIGSERIAL PRIMARY KEY,
      round_no INTEGER NOT NULL,
      pair_key TEXT NOT NULL,
      team_a_norm TEXT NOT NULL,
      team_a_raw TEXT NOT NULL,
      team_b_norm TEXT NOT NULL,
      team_b_raw TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (round_no, pair_key)
    )
  `);

  await query(
    "CREATE INDEX IF NOT EXISTS idx_league_debt_entries_round ON league_debt_entries (round_no)"
  );

  await query(`
    CREATE TABLE IF NOT EXISTS league_challenge_sources (
      id INTEGER PRIMARY KEY CHECK (id = 1),
      source_url TEXT,
      max_round INTEGER,
      enabled BOOLEAN NOT NULL DEFAULT FALSE,
      chat_id BIGINT,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
  `);

  await query(`
    INSERT INTO league_challenge_sources (id, enabled)
    VALUES (1, FALSE)
    ON CONFLICT (id) DO NOTHING
  `);

  await query(`
    CREATE TABLE IF NOT EXISTS league_reminder_settings (
      id INTEGER PRIMARY KEY CHECK (id = 1),
      daily_enabled BOOLEAN NOT NULL DEFAULT FALSE,
      hourly_enabled BOOLEAN NOT NULL DEFAULT FALSE,
      hourly_text TEXT,
      chat_id BIGINT,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
  `);

  await query(`
    INSERT INTO league_reminder_settings (id, daily_enabled, hourly_enabled)
    VALUES (1, FALSE, FALSE)
    ON CONFLICT (id) DO NOTHING
  `);

  await query(`
    CREATE TABLE IF NOT EXISTS league_reminder_runs (
      slot_key TEXT PRIMARY KEY,
      slot_type TEXT NOT NULL,
      run_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
  `);
}

module.exports = {
  pool,
  query,
  withTransaction,
  initDb,
};
