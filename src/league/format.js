const { canonicalPairKey } = require("./normalize");

function buildRoundLines(entries) {
  const seen = new Set();
  const lines = [];

  for (const entry of entries) {
    const pairKey =
      entry.pair_key || canonicalPairKey(entry.team_a_norm, entry.team_b_norm);

    if (seen.has(pairKey)) {
      continue;
    }

    seen.add(pairKey);
    lines.push(`- ${entry.team_a_raw} - ${entry.team_b_raw}`);
  }

  return lines;
}

function buildDebtsPost(entries) {
  if (!entries.length) {
    return "Общие долги\n\nДолгов нет.";
  }

  const grouped = new Map();

  for (const entry of entries) {
    if (!grouped.has(entry.round_no)) {
      grouped.set(entry.round_no, []);
    }
    grouped.get(entry.round_no).push(entry);
  }

  const roundNumbers = [...grouped.keys()].sort((a, b) => a - b);
  const sections = roundNumbers.map((roundNo) => {
    const lines = buildRoundLines(grouped.get(roundNo));
    return `Тур ${roundNo}\n${lines.join("\n")}`;
  });

  return `Общие долги\n\n${sections.join("\n\n")}`;
}

function buildDebtsRoundPost(entries, roundNo) {
  if (!entries.length) {
    return `Тур ${roundNo}\n\nДолгов нет.`;
  }

  const lines = buildRoundLines(entries);
  return `Тур ${roundNo}\n\n${lines.join("\n")}`;
}

function buildDebtorSummary(entries, mapRows) {
  const mapByNorm = new Map(
    mapRows.map((row) => [row.team_name_norm, row.telegram_username])
  );

  const countByTeam = new Map();

  for (const entry of entries) {
    countByTeam.set(
      entry.team_a_norm,
      (countByTeam.get(entry.team_a_norm) || 0) + 1
    );
    countByTeam.set(
      entry.team_b_norm,
      (countByTeam.get(entry.team_b_norm) || 0) + 1
    );
  }

  const rows = [...countByTeam.entries()]
    .map(([teamNorm, debts]) => ({
      teamNorm,
      debts,
      username: mapByNorm.get(teamNorm) || null,
    }))
    .sort((left, right) => right.debts - left.debts || left.teamNorm.localeCompare(right.teamNorm, "ru"));

  return rows;
}

function buildReminderMessage(summaryRows, hourlyText) {
  const debtors = summaryRows.filter((row) => row.debts > 2 && row.username);

  if (!debtors.length) {
    return null;
  }

  const tags = debtors.map((row) => `@${row.username} (${row.debts})`);
  const text = hourlyText && hourlyText.trim() ? hourlyText.trim() : "Напоминание по долгам лиги";

  return `${text}\n\n${tags.join("\n")}`;
}

function buildDebtorSummaryMessage(summaryRows) {
  if (!summaryRows.length) {
    return "Сводка долгов\n\nДолгов нет.";
  }

  const lines = summaryRows.map((row) => {
    const user = row.username ? `@${row.username}` : "(без маппинга)";
    return `- ${user}: ${row.debts}`;
  });

  return `Сводка долгов\n\n${lines.join("\n")}`;
}

module.exports = {
  buildDebtsPost,
  buildDebtsRoundPost,
  buildDebtorSummary,
  buildReminderMessage,
  buildDebtorSummaryMessage,
};
