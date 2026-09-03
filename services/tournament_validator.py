"""
services/tournament_validator.py

Double Round Robin tournament schedule validator.
Enforces the strict Logovobot tournament model:
- 16 teams
- 30 rounds
- 240 matches
- 8 matches per round
- 30 matches per team
- Exactly 2 meetings per pair (1 in Leg 1, 1 in Leg 2 with swapped Home/Away)
"""

import logging
from collections import Counter
from typing import Sequence, Tuple, Union, Dict, Any, List

logger = logging.getLogger(__name__)


class TournamentValidationError(Exception):
    """Raised when a tournament schedule violates integrity rules."""
    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class RoundRobinValidator:
    """
    Strict mathematical & integrity validator for Double Round Robin fixtures.
    """

    EXPECTED_TEAMS: int = 16
    EXPECTED_ROUNDS: int = 30
    EXPECTED_MATCHES: int = 240
    MATCHES_PER_ROUND: int = 8
    MATCHES_PER_TEAM: int = 30
    MEETINGS_PER_PAIR: int = 2
    LEG_1_MAX_ROUND: int = 15

    @classmethod
    def validate_fixtures(
        cls,
        fixtures: Sequence[Union[Tuple[int, int, int], Dict[str, Any]]],
        expected_teams: int = EXPECTED_TEAMS,
        expected_rounds: int = EXPECTED_ROUNDS,
        expected_matches: int = EXPECTED_MATCHES,
        division_id: int | None = None,
        season_id: int | None = None
    ) -> Tuple[bool, List[str]]:
        """
        Validate double round-robin fixtures.
        Accepts tuples (round_num, p1, p2) or dicts with 'round_number', 'player1_id', 'player2_id'.
        Returns (is_valid, list_of_errors).
        """
        errors: List[str] = []

        if not fixtures:
            return False, ["Список матчей пуст."]

        parsed_fixtures: List[Tuple[int, int, int]] = []
        for idx, f in enumerate(fixtures):
            if isinstance(f, (list, tuple)) and len(f) >= 3:
                r_num, p1, p2 = int(f[0]), int(f[1]), int(f[2])
            elif isinstance(f, dict):
                r_num = int(f.get("round_number", 0))
                p1 = int(f.get("player1_id", 0))
                p2 = int(f.get("player2_id", 0))
                if division_id is not None and "division_id" in f and f["division_id"] != division_id:
                    errors.append(f"Матч #{idx} имеет некорректный division_id={f['division_id']} (ожидался {division_id}).")
                if season_id is not None and "season_id" in f and f["season_id"] != season_id:
                    errors.append(f"Матч #{idx} имеет некорректный season_id={f['season_id']} (ожидался {season_id}).")
            else:
                errors.append(f"Неизвестный формат матча #{idx}: {f}")
                continue

            parsed_fixtures.append((r_num, p1, p2))

        # 1. Total matches count
        total_matches = len(parsed_fixtures)
        if total_matches != expected_matches:
            errors.append(f"Некорректное общее количество матчей: {total_matches} (ожидалось ровно {expected_matches}).")

        # 2. Extract unique teams & rounds
        teams = set()
        rounds = set()
        for r, p1, p2 in parsed_fixtures:
            teams.add(p1)
            teams.add(p2)
            rounds.add(r)

        if len(teams) != expected_teams:
            errors.append(f"Некорректное количество участников: {len(teams)} (ожидалось ровно {expected_teams}).")

        if len(rounds) != expected_rounds:
            errors.append(f"Некорректное количество туров: {len(rounds)} (ожидалось ровно {expected_rounds}).")

        if rounds and (min(rounds) != 1 or max(rounds) != expected_rounds):
            errors.append(f"Диапазон туров должен быть от 1 до {expected_rounds}, получено {min(rounds)}..{max(rounds)}.")

        # 3. Matches per round
        round_counts = Counter(r for r, _, _ in parsed_fixtures)
        for r_num in range(1, expected_rounds + 1):
            count = round_counts.get(r_num, 0)
            if count != cls.MATCHES_PER_ROUND:
                errors.append(f"Тур {r_num} содержит {count} матчей (ожидалось ровно {cls.MATCHES_PER_ROUND}).")

        # 4. Matches per team
        team_counts = Counter()
        for _, p1, p2 in parsed_fixtures:
            team_counts[p1] += 1
            team_counts[p2] += 1

        for t in teams:
            t_count = team_counts[t]
            if t_count != cls.MATCHES_PER_TEAM:
                errors.append(f"Команда #{t} играет {t_count} матчей (ожидалось ровно {cls.MATCHES_PER_TEAM}).")

        # 5. Pair analysis (no self-matches, exactly 2 meetings per pair, Leg 1 vs Leg 2)
        pair_meetings = Counter()
        leg1_pairs = {}
        leg2_pairs = {}

        for r, p1, p2 in parsed_fixtures:
            if p1 == p2:
                errors.append(f"Обнаружен матч команды с самой собой: Тур {r}, команда {p1} vs {p2}.")
                continue

            pair = tuple(sorted((p1, p2)))
            pair_meetings[pair] += 1

            if r <= cls.LEG_1_MAX_ROUND:
                if pair in leg1_pairs:
                    errors.append(f"Повторная встреча пары {pair} в 1-м круге (Туры {leg1_pairs[pair]} и {r}).")
                leg1_pairs[pair] = (r, p1, p2)
            else:
                if pair in leg2_pairs:
                    errors.append(f"Повторная встреча пары {pair} во 2-м круге (Туры {leg2_pairs[pair]} и {r}).")
                leg2_pairs[pair] = (r, p1, p2)

        # 6. Verify each pair meets in both legs with inverted Home/Away
        for pair, count in pair_meetings.items():
            if count != cls.MEETINGS_PER_PAIR:
                errors.append(f"Пара {pair} встречается {count} раз(а) (ожидалось ровно {cls.MEETINGS_PER_PAIR}).")

            if pair not in leg1_pairs:
                errors.append(f"Пара {pair} не имеет матча в 1-м круге (Туры 1–15).")
            if pair not in leg2_pairs:
                errors.append(f"Пара {pair} не имеет матча во 2-м круге (Туры 16–30).")

            if pair in leg1_pairs and pair in leg2_pairs:
                l1_r, l1_p1, l1_p2 = leg1_pairs[pair]
                l2_r, l2_p1, l2_p2 = leg2_pairs[pair]
                if l1_p1 == l2_p1 and l1_p2 == l2_p2:
                    errors.append(
                        f"Пара {pair} не поменялась сторонами Home/Away: "
                        f"Тур {l1_r} ({l1_p1} vs {l1_p2}) и Тур {l2_r} ({l2_p1} vs {l2_p2})."
                    )

        is_valid = len(errors) == 0
        if not is_valid:
            logger.warning(f"RoundRobin validation failed with {len(errors)} error(s): {errors[:5]}")

        return is_valid, errors

    @classmethod
    def assert_valid_fixtures(cls, fixtures: Sequence[Any], **kwargs) -> None:
        """Helper that raises TournamentValidationError if validation fails."""
        is_valid, errors = cls.validate_fixtures(fixtures, **kwargs)
        if not is_valid:
            raise TournamentValidationError(errors)
