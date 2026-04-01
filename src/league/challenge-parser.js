function findInitialStateJson(html) {
  const marker = "window.__INITIAL_STATE__";
  const markerIndex = html.indexOf(marker);

  if (markerIndex === -1) {
    throw new Error("window.__INITIAL_STATE__ not found on challenge page.");
  }

  const equalIndex = html.indexOf("=", markerIndex);
  const firstBraceIndex = html.indexOf("{", equalIndex);

  if (equalIndex === -1 || firstBraceIndex === -1) {
    throw new Error("Cannot parse window.__INITIAL_STATE__ assignment.");
  }

  let depth = 0;
  let inString = false;
  let stringChar = "";
  let escaped = false;

  for (let i = firstBraceIndex; i < html.length; i += 1) {
    const char = html[i];

    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (char === "\\") {
        escaped = true;
      } else if (char === stringChar) {
        inString = false;
        stringChar = "";
      }
      continue;
    }

    if (char === '"' || char === "'") {
      inString = true;
      stringChar = char;
      continue;
    }

    if (char === "{") {
      depth += 1;
    } else if (char === "}") {
      depth -= 1;
      if (depth === 0) {
        return html.slice(firstBraceIndex, i + 1);
      }
    }
  }

  throw new Error("Cannot find complete JSON object for __INITIAL_STATE__.");
}

function deepFindRounds(value, visited = new WeakSet()) {
  if (!value || typeof value !== "object") {
    return null;
  }

  if (visited.has(value)) {
    return null;
  }

  visited.add(value);

  if (Array.isArray(value)) {
    for (const item of value) {
      const found = deepFindRounds(item, visited);
      if (found) {
        return found;
      }
    }
    return null;
  }

  if (Array.isArray(value.rounds) && value.rounds.length > 0) {
    return value.rounds;
  }

  for (const nested of Object.values(value)) {
    const found = deepFindRounds(nested, visited);
    if (found) {
      return found;
    }
  }

  return null;
}

function teamNameFromParticipant(participant) {
  if (!participant || typeof participant !== "object") {
    return "";
  }

  return (
    participant.name ||
    participant.teamName ||
    participant.title ||
    participant.displayName ||
    ""
  );
}

function extractTeamsFromMatch(match) {
  const slots =
    match?.slots ||
    match?.participants ||
    match?.entries ||
    match?.players ||
    [];

  const names = slots
    .map((slot) => {
      return (
        teamNameFromParticipant(slot?.participant) ||
        teamNameFromParticipant(slot?.entrant) ||
        teamNameFromParticipant(slot?.team) ||
        teamNameFromParticipant(slot?.entry) ||
        teamNameFromParticipant(slot)
      );
    })
    .filter(Boolean);

  return names.slice(0, 2);
}

function normalizeRoundNumber(round, index) {
  const candidates = [
    round?.roundNumber,
    round?.number,
    round?.order,
    round?.index,
    round?.position,
  ];

  for (const value of candidates) {
    const numeric = Number(value);
    if (Number.isInteger(numeric) && numeric > 0) {
      return numeric;
    }
  }

  return index + 1;
}

function extractRoundMatches(round) {
  return round?.matches || round?.pairs || round?.games || [];
}

async function parseChallengeRounds(sourceUrl) {
  const response = await fetch(sourceUrl);
  if (!response.ok) {
    throw new Error(`Cannot fetch challenge source: HTTP ${response.status}`);
  }

  const html = await response.text();
  const jsonText = findInitialStateJson(html);
  const initialState = JSON.parse(jsonText);
  const rounds = deepFindRounds(initialState);

  if (!rounds || rounds.length === 0) {
    throw new Error("Rounds were not found in challenge initial state.");
  }

  return rounds.map((round, index) => ({
    roundNumber: normalizeRoundNumber(round, index),
    matches: extractRoundMatches(round).map((match) => ({
      winnerSlot:
        match?.winnerSlot ??
        match?.winner_slot ??
        match?.winner?.slot ??
        match?.winnerSlotIndex ??
        null,
      teams: extractTeamsFromMatch(match),
    })),
  }));
}

module.exports = {
  parseChallengeRounds,
};
