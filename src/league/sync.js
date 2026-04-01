const { parseChallengeRounds } = require("./challenge-parser");
const { canonicalPairKey, normalizeTeamName } = require("./normalize");

function toDebtEntries(rounds, maxRound) {
  const entries = [];

  for (const round of rounds) {
    if (round.roundNumber > maxRound) {
      continue;
    }

    for (const match of round.matches) {
      const [teamA, teamB] = match.teams;
      if (!teamA || !teamB) {
        continue;
      }

      if (match.winnerSlot != null) {
        continue;
      }

      const teamANorm = normalizeTeamName(teamA);
      const teamBNorm = normalizeTeamName(teamB);

      if (!teamANorm || !teamBNorm || teamANorm === teamBNorm) {
        continue;
      }

      entries.push({
        roundNo: round.roundNumber,
        pairKey: canonicalPairKey(teamANorm, teamBNorm),
        teamANorm,
        teamARaw: teamA.trim(),
        teamBNorm,
        teamBRaw: teamB.trim(),
      });
    }
  }

  const unique = new Map();
  for (const entry of entries) {
    const key = `${entry.roundNo}__${entry.pairKey}`;
    if (!unique.has(key)) {
      unique.set(key, entry);
    }
  }

  return [...unique.values()].sort(
    (left, right) => left.roundNo - right.roundNo || left.pairKey.localeCompare(right.pairKey, "ru")
  );
}

async function replaceDebts(client, entries) {
  await client.query("DELETE FROM league_debt_entries");

  for (const entry of entries) {
    await client.query(
      `
        INSERT INTO league_debt_entries
          (round_no, pair_key, team_a_norm, team_a_raw, team_b_norm, team_b_raw)
        VALUES ($1, $2, $3, $4, $5, $6)
      `,
      [
        entry.roundNo,
        entry.pairKey,
        entry.teamANorm,
        entry.teamARaw,
        entry.teamBNorm,
        entry.teamBRaw,
      ]
    );
  }
}

async function loadCurrentDebts(query) {
  const result = await query(
    `
      SELECT round_no, pair_key, team_a_norm, team_a_raw, team_b_norm, team_b_raw
      FROM league_debt_entries
      ORDER BY round_no ASC, pair_key ASC
    `
  );

  return result.rows;
}

async function loadRoundDebts(query, roundNo) {
  const result = await query(
    `
      SELECT round_no, pair_key, team_a_norm, team_a_raw, team_b_norm, team_b_raw
      FROM league_debt_entries
      WHERE round_no = $1
      ORDER BY pair_key ASC
    `,
    [roundNo]
  );

  return result.rows;
}

async function findUnmappedTeams(query) {
  const result = await query(`
    WITH teams AS (
      SELECT team_a_norm AS team_norm, team_a_raw AS team_raw FROM league_debt_entries
      UNION
      SELECT team_b_norm AS team_norm, team_b_raw AS team_raw FROM league_debt_entries
    )
    SELECT DISTINCT t.team_norm, t.team_raw
    FROM teams t
    LEFT JOIN league_team_map m ON m.team_name_norm = t.team_norm
    WHERE m.team_name_norm IS NULL
    ORDER BY t.team_raw ASC
  `);

  return result.rows;
}

async function saveChallengeSource(query, sourceUrl, maxRound, enabled, chatId) {
  await query(
    `
      INSERT INTO league_challenge_sources (id, source_url, max_round, enabled, chat_id, updated_at)
      VALUES (1, $1, $2, $3, $4, NOW())
      ON CONFLICT (id)
      DO UPDATE SET
        source_url = EXCLUDED.source_url,
        max_round = EXCLUDED.max_round,
        enabled = EXCLUDED.enabled,
        chat_id = EXCLUDED.chat_id,
        updated_at = NOW()
    `,
    [sourceUrl, maxRound, enabled, chatId]
  );
}

async function loadChallengeSource(query) {
  const result = await query(
    "SELECT source_url, max_round, enabled, chat_id FROM league_challenge_sources WHERE id = 1"
  );
  return result.rows[0] || null;
}

async function setSyncEnabled(query, enabled) {
  await query(
    "UPDATE league_challenge_sources SET enabled = $1, updated_at = NOW() WHERE id = 1",
    [enabled]
  );
}

async function syncChallenge(sourceUrl, maxRound) {
  const rounds = await parseChallengeRounds(sourceUrl);
  const entries = toDebtEntries(rounds, maxRound);
  return entries;
}

module.exports = {
  syncChallenge,
  replaceDebts,
  loadCurrentDebts,
  loadRoundDebts,
  findUnmappedTeams,
  saveChallengeSource,
  loadChallengeSource,
  setSyncEnabled,
};
