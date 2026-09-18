"""
services/poisson_odds.py

Logovo.bet — Bivariate Poisson Odds Engine & Margin Normalizer.
Provides:
1. Joint score probability grid calculation: P(i, j) = Poisson(i; lambda1) * Poisson(j; lambda2).
2. Continuous expected goal modeling (lambda1, lambda2) calibrated for FIFA esports.
3. Derivation of all 7 market types from the exact same probability space.
4. Unified 7.5% bookmaker margin (vigorish) application without arbitrage holes.
5. Monotonic nesting guards (ITM <= TM, ITB >= TB, Over/Under hierarchy).
"""

import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Target unified bookmaker margin (7.5% vigorish / 107.5% overround)
TARGET_MARGIN: float = 1.075

# Base FIFA goals per match (~3.1 - 3.4 goals per game)
BASE_GOALS_PER_TEAM: float = 1.55

# Maximum score per side evaluated in the probability matrix (0..9)
MAX_GOALS_GRID: int = 10


def calculate_match_lambdas(
    s1: float,
    s2: float,
    home_advantage: float = 0.05
) -> tuple[float, float]:
    """
    Calculate expected goals (lambda1, lambda2) from team strength scores.
    Strength score neutral point is 10.0, typical range [2.0, 25.0].
    """
    delta = (s1 - s2) / 10.0
    l1 = BASE_GOALS_PER_TEAM * math.exp(0.32 * delta + home_advantage)
    l2 = BASE_GOALS_PER_TEAM * math.exp(-0.32 * delta)
    # Bound to realistic FIFA match bounds
    return max(0.40, min(4.50, l1)), max(0.35, min(4.50, l2))


def generate_poisson_score_grid(
    lambda1: float,
    lambda2: float,
    max_goals: int = MAX_GOALS_GRID
) -> list[list[float]]:
    """
    Generate joint probability grid matrix P(i, j) for exact scores (i goals team 1, j goals team 2).
    Re-normalizes over the total sum to ensure sum(grid) == 1.000.
    """
    p1_dist = [(math.exp(-lambda1) * (lambda1 ** i)) / math.factorial(i) for i in range(max_goals)]
    p2_dist = [(math.exp(-lambda2) * (lambda2 ** j)) / math.factorial(j) for j in range(max_goals)]

    grid = [[p1_dist[i] * p2_dist[j] for j in range(max_goals)] for i in range(max_goals)]
    total_prob = sum(sum(row) for row in grid)
    if total_prob > 0:
        for i in range(max_goals):
            for j in range(max_goals):
                grid[i][j] /= total_prob
    return grid


def calculate_poisson_market_odds(
    s1: float,
    s2: float,
    margin: float = TARGET_MARGIN,
    home_advantage: float = 0.05
) -> dict:
    """
    Calculate complete, realistic European decimal odds for all standard sportsbook markets
    derived from the joint Poisson distribution matrix.
    """
    l1, l2 = calculate_match_lambdas(s1, s2, home_advantage=home_advantage)
    grid = generate_poisson_score_grid(l1, l2, max_goals=MAX_GOALS_GRID)
    max_g = MAX_GOALS_GRID

    # 1. 1X2 Probabilities
    p1 = sum(grid[i][j] for i in range(max_g) for j in range(max_g) if i > j)
    px = sum(grid[i][i] for i in range(max_g))
    p2 = sum(grid[i][j] for i in range(max_g) for j in range(max_g) if i < j)

    # 2. Double Chance Probabilities
    p_1x = p1 + px
    p_12 = p1 + p2
    p_x2 = px + p2

    # 3. Match Totals Probabilities (1.5, 2.5, 3.5)
    p_tb15 = sum(grid[i][j] for i in range(max_g) for j in range(max_g) if i + j >= 2)
    p_tm15 = 1.0 - p_tb15

    p_tb25 = sum(grid[i][j] for i in range(max_g) for j in range(max_g) if i + j >= 3)
    p_tm25 = 1.0 - p_tb25

    p_tb35 = sum(grid[i][j] for i in range(max_g) for j in range(max_g) if i + j >= 4)
    p_tm35 = 1.0 - p_tb35

    # 4. Both Teams to Score (BTTS)
    p_btts_yes = sum(grid[i][j] for i in range(1, max_g) for j in range(1, max_g))
    p_btts_no = 1.0 - p_btts_yes

    # 5. Individual Totals (1.5)
    p_itb1 = sum(grid[i][j] for i in range(2, max_g) for j in range(max_g))
    p_itm1 = 1.0 - p_itb1

    p_itb2 = sum(grid[i][j] for i in range(max_g) for j in range(2, max_g))
    p_itm2 = 1.0 - p_itb2

    # 6. Handicap (+-1.5)
    p_h1_minus = sum(grid[i][j] for i in range(max_g) for j in range(max_g) if i - j >= 2)
    p_h2_plus = 1.0 - p_h1_minus

    p_h2_minus = sum(grid[i][j] for i in range(max_g) for j in range(max_g) if j - i >= 2)
    p_h1_plus = 1.0 - p_h2_minus

    def _to_odd(prob: float, min_val: float = 1.04, max_val: float = 25.0) -> float:
        safe_prob = max(1e-4, min(0.999, prob))
        val = 1.0 / (safe_prob * margin)
        return round(max(min_val, min(max_val, val)), 2)

    # Convert to decimal odds
    odd_p1 = _to_odd(p1, min_val=1.08, max_val=20.0)
    odd_x = _to_odd(px, min_val=2.10, max_val=12.0)
    odd_p2 = _to_odd(p2, min_val=1.08, max_val=20.0)

    odd_1x = _to_odd(p_1x, min_val=1.04, max_val=10.0)
    odd_12 = _to_odd(p_12, min_val=1.04, max_val=10.0)
    odd_x2 = _to_odd(p_x2, min_val=1.04, max_val=10.0)

    odd_tb15 = _to_odd(p_tb15)
    odd_tm15 = _to_odd(p_tm15)
    odd_tb25 = _to_odd(p_tb25)
    odd_tm25 = _to_odd(p_tm25)
    odd_tb35 = _to_odd(p_tb35)
    odd_tm35 = _to_odd(p_tm35)

    odd_btts_yes = _to_odd(p_btts_yes)
    odd_btts_no = _to_odd(p_btts_no)

    odd_itb1 = _to_odd(p_itb1)
    odd_itm1 = _to_odd(p_itm1)
    odd_itb2 = _to_odd(p_itb2)
    odd_itm2 = _to_odd(p_itm2)

    odd_h1_minus = _to_odd(p_h1_minus, min_val=1.05)
    odd_h2_plus = _to_odd(p_h2_plus, min_val=1.05)
    odd_h2_minus = _to_odd(p_h2_minus, min_val=1.05)
    odd_h1_plus = _to_odd(p_h1_plus, min_val=1.05)

    # Defensive Monotonic Nesting Enforcement (LB-10)
    # 1. Individual Under cannot be more expensive than Match Under
    if odd_itm1 > odd_tm15:
        odd_itm1 = odd_tm15
    if odd_itm2 > odd_tm15:
        odd_itm2 = odd_tm15

    # 2. Individual Over cannot be cheaper than Match Over
    if odd_itb1 < odd_tb15:
        odd_itb1 = odd_tb15
    if odd_itb2 < odd_tb15:
        odd_itb2 = odd_tb15

    # 3. Match totals monotonicity
    if odd_tb15 > odd_tb25:
        odd_tb25 = odd_tb15
    if odd_tb25 > odd_tb35:
        odd_tb35 = odd_tb25

    if odd_tm35 > odd_tm25:
        odd_tm25 = odd_tm35
    if odd_tm25 > odd_tm15:
        odd_tm15 = odd_tm25

    # 4. Double chance vs match winner monotonicity
    if odd_1x > odd_p1:
        odd_1x = odd_p1
    if odd_x2 > odd_p2:
        odd_x2 = odd_p2

    return {
        "lambda1": round(l1, 2),
        "lambda2": round(l2, 2),
        "odd_p1": odd_p1,
        "odd_x": odd_x,
        "odd_p2": odd_p2,
        "odd_1x": odd_1x,
        "odd_12": odd_12,
        "odd_x2": odd_x2,
        "odd_tb15": odd_tb15,
        "odd_tm15": odd_tm15,
        "odd_tb25": odd_tb25,
        "odd_tm25": odd_tm25,
        "odd_tb35": odd_tb35,
        "odd_tm35": odd_tm35,
        "odd_btts_yes": odd_btts_yes,
        "odd_btts_no": odd_btts_no,
        "odd_itb1": odd_itb1,
        "odd_itm1": odd_itm1,
        "odd_itb2": odd_itb2,
        "odd_itm2": odd_itm2,
        "odd_h1_minus_1.5": odd_h1_minus,
        "odd_h2_plus_1.5": odd_h2_plus,
        "odd_h1_plus_1.5": odd_h1_plus,
        "odd_h2_minus_1.5": odd_h2_minus
    }
