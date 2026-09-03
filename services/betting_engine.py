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


def _get_team_strength_score(standings: list[dict], team_name: str) -> float:
    """Calculate relative strength score based on tournament standings."""
    norm_team = database.normalize_team_name(team_name).lower()
    for row in standings:
        row_team = database.normalize_team_name(row.get("team_name", "")).lower()
        if row_team == norm_team:
            played = max(1, row.get("played", 0))
            pts = row.get("points", 0)
            gd = row.get("gd", 0)
            gf = row.get("gf", 0)
            # Strength formula: Points per game (70%) + Goal diff per game (30%)
            ppg = pts / played
            gd_pg = gd / played
            return max(1.0, 10.0 + (ppg * 4.0) + (gd_pg * 1.5))
            
    # Default base strength for unranked / new teams
    return 10.0


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
    Generate or update odds markets for all unplayed matches of a tour, optionally filtered by division and season.
    """
    matches = database.get_matches_by_round(tour, division_id=division_id)
    if season_id is not None:
        matches = [m for m in matches if m.get("season_id") in (season_id, None)]
    markets = []

    for m in matches:
        if m.get("status") in ("completed", "confirmed"):
            continue

        m_id = m.get("id")
        t1 = m.get("player1_team") or m.get("team1") or "Команда 1"
        t2 = m.get("player2_team") or m.get("team2") or "Команда 2"

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

    logger.info(f"✅ Generated Logovo.bet markets for {len(markets)} matches in Tour #{tour} (division={division_id}, season={season_id})")
    return markets
