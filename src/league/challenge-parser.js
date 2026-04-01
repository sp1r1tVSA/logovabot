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

function challengeIdFromUrl(url) {
  const match = String(url).match(/\/c\/([a-z0-9]+)/i);
  return match ? match[1] : null;
}

function stageIdFromUrl(url) {
  const match = String(url).match(/\/stage\/([a-z0-9]+)/i);
  return match ? match[1] : null;
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

function extractTeamsFromMatch(match, competitorsById) {
  const homeRef = match?.homeCompetitor ?? match?.homeParticipant;
  const awayRef = match?.awayCompetitor ?? match?.awayParticipant;

  const homeName =
    typeof homeRef === "string"
      ? teamNameFromParticipant(competitorsById.get(homeRef))
      : teamNameFromParticipant(homeRef);
  const awayName =
    typeof awayRef === "string"
      ? teamNameFromParticipant(competitorsById.get(awayRef))
      : teamNameFromParticipant(awayRef);

  if (homeName && awayName) {
    return [homeName, awayName];
  }

  const fallbackHomeName =
    teamNameFromParticipant(match?.homeCompetitor) ||
    teamNameFromParticipant(match?.homeParticipant);
  const fallbackAwayName =
    teamNameFromParticipant(match?.awayCompetitor) ||
    teamNameFromParticipant(match?.awayParticipant);

  if (fallbackHomeName && fallbackAwayName) {
    return [fallbackHomeName, fallbackAwayName];
  }

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

function normalizeRoundNumber(round, index = 0) {
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

function parseInitialState(html) {
  const jsonText = findInitialStateJson(html);
  return JSON.parse(jsonText);
}

async function fetchInitialState(sourceUrl) {
  const response = await fetch(sourceUrl);
  if (!response.ok) {
    throw new Error(`Cannot fetch challenge source: HTTP ${response.status} (${sourceUrl})`);
  }

  const html = await response.text();
  return parseInitialState(html);
}

function extractRoundOrderMap(initialState) {
  const map = new Map();
  const rooms = initialState?.rooms;

  if (!rooms || typeof rooms !== "object") {
    return map;
  }

  for (const room of Object.values(rooms)) {
    if (!room || typeof room !== "object") {
      continue;
    }

    const rounds = room.rounds;
    if (!rounds || typeof rounds !== "object") {
      continue;
    }

    for (const [roundId, round] of Object.entries(rounds)) {
      const order = normalizeRoundNumber(round);
      if (Number.isInteger(order) && order > 0) {
        map.set(roundId, order);
      }
    }
  }

  return map;
}

function arrayFromMaybeObject(value) {
  if (!value) {
    return [];
  }
  if (Array.isArray(value)) {
    return value;
  }
  if (typeof value === "object") {
    return Object.values(value);
  }
  return [];
}

function extractMatches(initialState) {
  const rooms = initialState?.rooms;
  if (!rooms || typeof rooms !== "object") {
    return [];
  }

  const result = [];

  for (const room of Object.values(rooms)) {
    if (!room || typeof room !== "object") {
      continue;
    }

    result.push(...arrayFromMaybeObject(room.matches));
    result.push(...arrayFromMaybeObject(room.latestMatches));
    result.push(...arrayFromMaybeObject(room.upcomingMatches));
    result.push(...arrayFromMaybeObject(room.liveMatches));
  }

  return result.filter((item) => item && typeof item === "object");
}

function extractCompetitorsMap(initialState) {
  const map = new Map();
  const rooms = initialState?.rooms;

  if (!rooms || typeof rooms !== "object") {
    return map;
  }

  for (const room of Object.values(rooms)) {
    if (!room || typeof room !== "object") {
      continue;
    }

    const competitors = room.competitors;
    if (!competitors || typeof competitors !== "object" || Array.isArray(competitors)) {
      continue;
    }

    for (const [id, competitor] of Object.entries(competitors)) {
      map.set(id, competitor);
    }
  }

  return map;
}

function roundNumberFromMatch(match, roundOrderMap) {
  const fromMap = roundOrderMap.get(String(match?.roundId || ""));
  if (fromMap) {
    return fromMap;
  }

  return normalizeRoundNumber(match, -1);
}

function buildRoundModel(matches, roundOrderMap, competitorsById) {
  const grouped = new Map();

  for (const match of matches) {
    const roundNumber = roundNumberFromMatch(match, roundOrderMap);
    if (!Number.isInteger(roundNumber) || roundNumber <= 0) {
      continue;
    }

    if (!grouped.has(roundNumber)) {
      grouped.set(roundNumber, []);
    }

    grouped.get(roundNumber).push({
      winnerSlot:
        match?.winnerSlot ??
        match?.winner_slot ??
        match?.winner?.slot ??
        match?.winnerSlotIndex ??
        null,
      teams: extractTeamsFromMatch(match, competitorsById),
    });
  }

  return [...grouped.entries()]
    .sort((left, right) => left[0] - right[0])
    .map(([roundNumber, roundMatches]) => ({
      roundNumber,
      matches: roundMatches,
    }));
}

async function parseChallengeRounds(sourceUrl) {
  const initial = await fetchInitialState(sourceUrl);
  const states = [initial];

  const challengeId = challengeIdFromUrl(sourceUrl) || initial?.settings?.id || null;
  let stageId = stageIdFromUrl(sourceUrl);

  const primaryMatches = extractMatches(initial);
  if (!stageId) {
    const firstWithStage = primaryMatches.find((match) => match?.stageId);
    stageId = firstWithStage ? String(firstWithStage.stageId) : null;
  }

  if (challengeId && stageId) {
    const stageUrl = `https://challenge.place/c/${challengeId}/stage/${stageId}`;
    if (stageUrl !== sourceUrl) {
      try {
        states.push(await fetchInitialState(stageUrl));
      } catch (_) {}
    }
  }

  if (challengeId) {
    const dashboardUrl = `https://challenge.place/c/${challengeId}`;
    if (dashboardUrl !== sourceUrl) {
      try {
        states.push(await fetchInitialState(dashboardUrl));
      } catch (_) {}
    }
  }

  const roundOrderMap = new Map();
  const competitorsById = new Map();
  const mergedMatches = [];
  const seenMatchIds = new Set();

  for (const state of states) {
    const map = extractRoundOrderMap(state);
    for (const [roundId, order] of map.entries()) {
      roundOrderMap.set(roundId, order);
    }

    const competitorsMap = extractCompetitorsMap(state);
    for (const [competitorId, competitor] of competitorsMap.entries()) {
      if (!competitorsById.has(competitorId)) {
        competitorsById.set(competitorId, competitor);
      }
    }

    for (const match of extractMatches(state)) {
      if (stageId && match?.stageId && String(match.stageId) !== String(stageId)) {
        continue;
      }

      const matchId = String(match?.id || "");
      if (matchId && seenMatchIds.has(matchId)) {
        continue;
      }

      if (matchId) {
        seenMatchIds.add(matchId);
      }

      mergedMatches.push(match);
    }
  }

  const rounds = buildRoundModel(mergedMatches, roundOrderMap, competitorsById);

  if (!rounds || rounds.length === 0) {
    throw new Error(
      "Rounds were not found in challenge data. Use a stage URL or ensure the challenge has available matches."
    );
  }

  return rounds;
}

module.exports = {
  parseChallengeRounds,
};
