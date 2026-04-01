const { normalizeTeamName } = require("./normalize");

function cleanUsername(input) {
  return String(input || "")
    .trim()
    .replace(/^@+/, "")
    .replace(/\s+/g, "");
}

function parseBulkMapText(text) {
  const lines = String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  const items = [];

  for (const line of lines) {
    const parts = line.split("-");
    if (parts.length < 2) {
      continue;
    }

    const teamRaw = parts.slice(0, -1).join("-").trim();
    const username = cleanUsername(parts[parts.length - 1]);
    const teamNorm = normalizeTeamName(teamRaw);

    if (!teamRaw || !teamNorm || !username) {
      continue;
    }

    items.push({
      teamNorm,
      teamRaw,
      username,
    });
  }

  const deduped = new Map();
  for (const item of items) {
    deduped.set(item.teamNorm, item);
  }

  return [...deduped.values()].sort((a, b) => a.teamRaw.localeCompare(b.teamRaw, "ru"));
}

async function replaceTeamMap(client, rows) {
  await client.query("DELETE FROM league_team_map");

  for (const row of rows) {
    await client.query(
      `
        INSERT INTO league_team_map (team_name_norm, team_name_raw, telegram_username, updated_at)
        VALUES ($1, $2, $3, NOW())
      `,
      [row.teamNorm, row.teamRaw, row.username]
    );
  }
}

async function clearTeamMap(query) {
  await query("DELETE FROM league_team_map");
}

async function loadTeamMap(query) {
  const result = await query(
    `
      SELECT team_name_norm, team_name_raw, telegram_username
      FROM league_team_map
      ORDER BY team_name_raw ASC
    `
  );
  return result.rows;
}

async function upsertReminderSettings(query, patch) {
  const current = await query(
    `
      SELECT daily_enabled, hourly_enabled, hourly_text, chat_id
      FROM league_reminder_settings
      WHERE id = 1
    `
  );

  const row = current.rows[0] || {
    daily_enabled: false,
    hourly_enabled: false,
    hourly_text: null,
    chat_id: null,
  };

  const dailyEnabled =
    patch.dailyEnabled === undefined ? row.daily_enabled : patch.dailyEnabled;
  const hourlyEnabled =
    patch.hourlyEnabled === undefined ? row.hourly_enabled : patch.hourlyEnabled;
  const hourlyText = patch.hourlyText === undefined ? row.hourly_text : patch.hourlyText;
  const chatId = patch.chatId === undefined ? row.chat_id : patch.chatId;

  await query(
    `
      INSERT INTO league_reminder_settings
        (id, daily_enabled, hourly_enabled, hourly_text, chat_id, updated_at)
      VALUES (1, $1, $2, $3, $4, NOW())
      ON CONFLICT (id)
      DO UPDATE SET
        daily_enabled = EXCLUDED.daily_enabled,
        hourly_enabled = EXCLUDED.hourly_enabled,
        hourly_text = EXCLUDED.hourly_text,
        chat_id = EXCLUDED.chat_id,
        updated_at = NOW()
    `,
    [dailyEnabled, hourlyEnabled, hourlyText, chatId]
  );
}

async function loadReminderSettings(query) {
  const result = await query(
    "SELECT daily_enabled, hourly_enabled, hourly_text, chat_id FROM league_reminder_settings WHERE id = 1"
  );

  return (
    result.rows[0] || {
      daily_enabled: false,
      hourly_enabled: false,
      hourly_text: null,
      chat_id: null,
    }
  );
}

async function registerReminderRun(query, slotKey, slotType) {
  const result = await query(
    `
      INSERT INTO league_reminder_runs (slot_key, slot_type, run_at)
      VALUES ($1, $2, NOW())
      ON CONFLICT (slot_key) DO NOTHING
    `,
    [slotKey, slotType]
  );

  return result.rowCount > 0;
}

module.exports = {
  parseBulkMapText,
  replaceTeamMap,
  clearTeamMap,
  loadTeamMap,
  upsertReminderSettings,
  loadReminderSettings,
  registerReminderRun,
};
