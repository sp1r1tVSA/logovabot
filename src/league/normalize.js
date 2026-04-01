function normalizeTeamName(input) {
  const value = String(input || "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase()
    .replace(/ё/g, "е");

  if (!value) {
    return "";
  }

  return value.replace(/глимпт/g, "глимт");
}

function canonicalPairKey(teamOneNorm, teamTwoNorm) {
  const [a, b] = [teamOneNorm, teamTwoNorm].sort((left, right) =>
    left.localeCompare(right, "ru")
  );
  return `${a}__${b}`;
}

module.exports = {
  normalizeTeamName,
  canonicalPairKey,
};
