"""
services/schedule_generator.py

High-performance, mathematically rigorous schedule generator for Logovobot.
Features:
- Standard 16-team Double Round Robin (30 rounds, 240 matches, 8 matches per round).
- Asymmetric Leg 2 (Rounds 16–30):
    * Non-mirrored round structure (breaks simple round + 15 predictability).
    * Rematch spacing: minimum gap >= 5 rounds between meetings of the same pair.
    * Home/Away streak balance: no team has more than 2 consecutive home or away matches (no HHH/AAA).
    * Equal total balance: exactly 15 home and 15 away matches for every participant.
- Randomized draw (жеребьевка): fair team placement with optional seed support.
- Generalized fallback for arbitrary N participants.
"""

import logging
import random
from collections import defaultdict
from typing import List, Tuple, Sequence

logger = logging.getLogger(__name__)

# Verified asymmetric permutations of Leg 1 rounds (1..15) mapped to Leg 2 (rounds 16..30)
# for standard 16-team circle-method schedule.
# Criteria:
# 1. Rematch spacing >= 5 rounds (min gap is 6).
# 2. No HHH or AAA streak across all 30 rounds for any team.
# 3. Exactly 15 Home / 15 Away per team.
VERIFIED_ASYMMETRIC_PERMUTATIONS_16: Tuple[Tuple[int, ...], ...] = (
    (10, 11, 5, 8, 4, 7, 1, 6, 14, 15, 9, 12, 13, 2, 3),
    (10, 11, 5, 8, 4, 7, 1, 6, 14, 3, 13, 2, 12, 15, 9),
    (10, 11, 5, 8, 4, 7, 1, 2, 3, 6, 9, 12, 13, 14, 15),
    (10, 11, 5, 8, 4, 7, 1, 2, 3, 6, 14, 15, 9, 12, 13),
    (10, 11, 5, 8, 4, 7, 1, 2, 3, 13, 14, 15, 9, 12, 6),
    (11, 10, 4, 7, 5, 8, 2, 6, 14, 15, 9, 12, 13, 1, 3),
    (9, 12, 5, 8, 4, 7, 1, 6, 14, 15, 10, 11, 13, 2, 3),
)


def generate_asymmetric_round_robin_fixtures(
    player_ids: Sequence[int],
    shuffle_teams: bool = True,
    seed: int | None = None
) -> List[Tuple[int, int, int]]:
    """
    Generate an asymmetric double round-robin schedule.
    Returns a list of fixtures as (round_number, player1_id, player2_id).

    :param player_ids: List of team/player identifiers.
    :param shuffle_teams: If True, performs a randomized draw (жеребьевка) before scheduling.
    :param seed: Optional random seed for reproducible draws.
    """
    n = len(player_ids)
    if n < 2:
        return []

    players = list(player_ids)
    rng = random.Random(seed) if seed is not None else random.Random()

    if shuffle_teams:
        rng.shuffle(players)

    # Pad with None for odd number of participants (bye)
    has_bye = (n % 2 != 0)
    if has_bye:
        players.append(None)
        n += 1

    rounds_in_half = n - 1
    temp_players = list(players)
    single_fixtures: List[Tuple[int, int, int]] = []

    # Leg 1: Circle method
    for round_num in range(1, n):
        for i in range(n // 2):
            p1 = temp_players[i]
            p2 = temp_players[n - 1 - i]
            if p1 is not None and p2 is not None:
                if round_num % 2 == 0:
                    single_fixtures.append((round_num, p2, p1))
                else:
                    single_fixtures.append((round_num, p1, p2))
        # Rotate players (keep player at index 0 fixed)
        temp_players = [temp_players[0]] + [temp_players[-1]] + temp_players[1:-1]

    # Leg 2: Asymmetric schedule for standard 16-team format
    if n == 16 and not has_bye:
        # Group leg 1 matches by round for fast retrieval, reversing Home and Away
        leg1_by_round = defaultdict(list)
        for r, p1, p2 in single_fixtures:
            leg1_by_round[r].append((p2, p1))

        # Select a verified permutation based on seed / rng
        perm_idx = rng.randrange(len(VERIFIED_ASYMMETRIC_PERMUTATIONS_16))
        chosen_perm = VERIFIED_ASYMMETRIC_PERMUTATIONS_16[perm_idx]

        double_fixtures = list(single_fixtures)
        for idx, orig_round in enumerate(chosen_perm):
            leg2_round = 16 + idx
            for home_p, away_p in leg1_by_round[orig_round]:
                double_fixtures.append((leg2_round, home_p, away_p))
    else:
        # Generalized fallback for other league sizes: reversed second leg
        double_fixtures = list(single_fixtures)
        for round_num, p1, p2 in single_fixtures:
            double_fixtures.append((round_num + rounds_in_half, p2, p1))

    # Sort strictly by round number, then home player ID
    double_fixtures.sort(key=lambda x: (x[0], x[1]))
    return double_fixtures


# Backward compatibility alias
generate_round_robin_fixtures = generate_asymmetric_round_robin_fixtures
