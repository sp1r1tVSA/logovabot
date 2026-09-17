"""
services/betting_engine.py

Logovo.bet — Mathematical Odds Calculation & Market Generation Engine.
Calculates realistic sportsbook odds based on:
1. Elo / Current Tournament Standings (Wins, Goal Difference)
2. Average Goal Output (xG / Over 2.5 Total)
3. Both Teams to Score (BTTS)
4. Built-in 5% Bookmaker Margin (Vigorish)
"""

import math
import logging
import database

logger = logging.getLogger(__name__)

# Standard Bookmaker Margin (5.5%)
BOOKMAKER_MARGIN = 1.055

# Ровно столько центральных матчей тура попадает в линию БК.
CENTRAL_MATCHES_PER_ROUND = 4

# Пока сыграно меньше этого числа туров, таблицы фактически нет — статусность
# пары считается по базовой силе клубов, а не по очкам и местам.
TABLE_MODE_MIN_PLAYED_ROUNDS = 2

# Вес разрыва в местах в формуле привлекательности: чем дальше команды друг от
# друга в таблице, тем менее интересен матч.
_PLACE_GAP_PENALTY = 1.5

# Статусы матча, при которых ставить уже не на что.
_PLAYED_STATUSES = ("completed", "confirmed")


def _get_team_strength_score(standings: list[dict], team_name: str) -> float:
    """Calculate relative strength score based on tournament standings."""
    norm_team = database.normalize_team_name(team_name).lower()
    for row in standings:
        row_team = database.normalize_team_name(row.get("team_name", "")).lower()
        if row_team == norm_team:
            played = max(1, row.get("played", 0))
            pts = row.get("points", 0)
            # `get_standings` отдаёт goals_scored/goals_conceded; ключи gd/gf
            # поддержаны для вызовов с уже посчитанной разницей мячей.
            if "gd" in row:
                gd = row.get("gd") or 0
            else:
                gd = (row.get("goals_scored") or 0) - (row.get("goals_conceded") or 0)
            # Strength formula: Points per game (70%) + Goal diff per game (30%)
            ppg = pts / played
            gd_pg = gd / played
            return max(1.0, 10.0 + (ppg * 4.0) + (gd_pg * 1.5))

    # Default base strength for unranked / new teams
    return 10.0


def _match_team_names(m: dict) -> tuple[str, str]:
    """Имена клубов пары в том же порядке, в каком они уйдут в рынок."""
    t1 = m.get("player1_team") or m.get("team1") or "Команда 1"
    t2 = m.get("player2_team") or m.get("team2") or "Команда 2"
    return t1, t2


def _build_table_index(standings: list[dict]) -> dict[str, dict]:
    """Индекс «нормализованное имя клуба → {place, points}» по таблице.

    `get_standings` возвращает строки, уже отсортированные по месту, поэтому
    место — это позиция в списке + 1; отдельной колонки `place` в строках нет.
    """
    index: dict[str, dict] = {}
    for position, row in enumerate(standings, start=1):
        key = database.normalize_team_name(row.get("team_name", "")).lower()
        if key:
            index[key] = {"place": position, "points": row.get("points") or 0}
    return index


def select_top_round_matches(
    tour: int,
    division_id: int | None = None,
    season_id: int | None = None,
    limit: int = CENTRAL_MATCHES_PER_ROUND,
) -> list[dict]:
    """Отбирает ровно 4 самых статусных матча тура для выставления в линию БК.

    Правила ранжирования:
      * сыграно меньше двух туров (таблицы ещё нет) — статусность пары считается
        как сумма базовой силы клубов `S₁ + S₂`;
      * начиная с третьего тура — по привлекательности вершины таблицы:
        `Score = (Pts₁ + Pts₂) − |Place₁ − Place₂| × 1.5`.

    Уже сыгранные матчи в линию не попадают. При равенстве Score порядок
    определяется id матча, чтобы повторный вызов давал тот же набор.
    Возвращает строки матчей с добавленным ключом `line_score`.
    """
    matches = database.get_matches_by_round(tour, division_id=division_id, season_id=season_id)
    if season_id is not None:
        matches = [m for m in matches if m.get("season_id") in (season_id, None)]
    matches = [m for m in matches if m.get("status") not in _PLAYED_STATUSES]
    if not matches:
        return []

    standings: list[dict] = []
    try:
        standings = database.get_standings(division_id=division_id, season_id=season_id)
    except Exception as e:
        logger.debug(f"Could not load standings for round selection: {e}")

    played_rounds = max((row.get("played") or 0) for row in standings) if standings else 0
    use_table = played_rounds >= TABLE_MODE_MIN_PLAYED_ROUNDS
    table = _build_table_index(standings) if use_table else {}
    # Клуб без строки в таблице ставится за последнее место с нулём очков.
    fallback_place = len(standings) + 1

    scored: list[dict] = []
    for m in matches:
        t1, t2 = _match_team_names(m)
        if use_table:
            k1 = database.normalize_team_name(t1).lower()
            k2 = database.normalize_team_name(t2).lower()
            r1 = table.get(k1, {"place": fallback_place, "points": 0})
            r2 = table.get(k2, {"place": fallback_place, "points": 0})
            score = (r1["points"] + r2["points"]) - abs(r1["place"] - r2["place"]) * _PLACE_GAP_PENALTY
        else:
            score = _get_team_strength_score(standings, t1) + _get_team_strength_score(standings, t2)

        row = dict(m)
        row["line_score"] = round(float(score), 3)
        scored.append(row)

    scored.sort(key=lambda r: (-r["line_score"], r.get("id") or 0))
    return scored[:limit]


def calculate_match_odds(team1: str, team2: str, division_id: int | None = None, season_id: int | None = None) -> dict:
    """
    Calculate realistic European decimal odds for a fixture.
    Returns:
    {
        'odd_p1': float, 'odd_x': float, 'odd_p2': float,
        'odd_tb25': float, 'odd_tm25': float,
        'odd_btts_yes': float, 'odd_btts_no': float
    }
    """
    standings = []
    try:
        standings = database.get_standings(division_id=division_id, season_id=season_id)
    except Exception as e:
        logger.debug(f"Could not load standings for odds: {e}")

    s1 = _get_team_strength_score(standings, team1)
    s2 = _get_team_strength_score(standings, team2)

    # 1. Base win probabilities using logistic scale
    # Home advantage slight boost (1.05x)
    s1_adjusted = s1 * 1.05
    prob_p1_raw = s1_adjusted / (s1_adjusted + s2)
    prob_p2_raw = s2 / (s1_adjusted + s2)

    # Calculate draw probability based on closeness of teams
    closeness = 1.0 - abs(prob_p1_raw - prob_p2_raw)
    prob_x_raw = 0.26 * closeness

    # Normalize probabilities to sum to 1.0
    total_raw = prob_p1_raw + prob_x_raw + prob_p2_raw
    p1 = prob_p1_raw / total_raw
    px = prob_x_raw / total_raw
    p2 = prob_p2_raw / total_raw

    # 2. Apply Bookmaker Margin (5.5%)
    odd_p1 = round(max(1.10, min(12.0, (1.0 / (p1 * BOOKMAKER_MARGIN)))), 2)
    odd_x = round(max(2.10, min(8.0, (1.0 / (px * BOOKMAKER_MARGIN)))), 2)
    odd_p2 = round(max(1.10, min(12.0, (1.0 / (p2 * BOOKMAKER_MARGIN)))), 2)

    # 3. Totals & BTTS Calculation
    # FIFA esports typically has high goal average (3.2 - 4.5 goals per match)
    total_strength = (s1 + s2) / 2.0
    if total_strength > 12.0:
        # High scoring teams
        odd_tb25 = 1.55
        odd_tm25 = 2.30
        odd_btts_yes = 1.60
        odd_btts_no = 2.20
    elif total_strength < 8.0:
        # Lower scoring teams
        odd_tb25 = 2.05
        odd_tm25 = 1.70
        odd_btts_yes = 1.85
        odd_btts_no = 1.85
    else:
        # Balanced
        odd_tb25 = 1.75
        odd_tm25 = 1.95
        odd_btts_yes = 1.68
        odd_btts_no = 2.05

    return {
        "odd_p1": odd_p1,
        "odd_x": odd_x,
        "odd_p2": odd_p2,
        "odd_tb25": odd_tb25,
        "odd_tm25": odd_tm25,
        "odd_btts_yes": odd_btts_yes,
        "odd_btts_no": odd_btts_no
    }


def generate_round_markets(tour: int, division_id: int | None = None, season_id: int | None = None) -> list[dict]:
    """
    Generate or update odds markets for the central matches of a tour, optionally filtered by division and season.

    В линию выставляются ровно `CENTRAL_MATCHES_PER_ROUND` самых статусных
    матчей тура (см. `select_top_round_matches`). Рынки остальных матчей тура
    гасятся, чтобы после пересчёта в линии не оставалось лишних пар.
    """
    if season_id is None:
        act = database.get_active_season()
        season_id = act["id"] if act else 1

    selected = select_top_round_matches(tour, division_id=division_id, season_id=season_id)
    keep_ids = [m.get("id") for m in selected if m.get("id")]

    # Сначала убираем неактуальные рынки тура, потом выставляем новые:
    # матчи с уже принятыми ставками из линии не выпадают (см. prune_round_markets).
    try:
        database.prune_round_markets(tour, keep_ids, division_id=division_id, season_id=season_id)
    except Exception as e:
        logger.warning(f"Could not prune stale markets for Tour #{tour}: {e}")

    markets = []

    for m in selected:
        m_id = m.get("id")
        t1, t2 = _match_team_names(m)

        odds = calculate_match_odds(t1, t2, division_id=division_id, season_id=season_id)
        database.save_bet_market(
            match_id=m_id,
            tour=tour,
            team1_name=t1,
            team2_name=t2,
            odd_p1=odds["odd_p1"],
            odd_x=odds["odd_x"],
            odd_p2=odds["odd_p2"],
            odd_tb25=odds["odd_tb25"],
            odd_tm25=odds["odd_tm25"],
            odd_btts_yes=odds["odd_btts_yes"],
            odd_btts_no=odds["odd_btts_no"]
        )
        markets.append({
            "match_id": m_id,
            "tour": tour,
            "team1_name": t1,
            "team2_name": t2,
            **odds
        })

    logger.info(f"✅ Generated Logovo.bet markets for {len(markets)} central matches in Tour #{tour} (division={division_id}, season={season_id})")
    return markets
