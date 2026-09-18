"""
tests/test_asymmetric_schedule.py

Comprehensive tests for the asymmetric 30-round schedule generator and validator.
"""

import unittest
from collections import defaultdict, Counter
import database
from services.schedule_generator import (
    generate_asymmetric_round_robin_fixtures,
    generate_round_robin_fixtures,
)
from services.tournament_validator import RoundRobinValidator


class TestAsymmetricSchedule(unittest.TestCase):
    def setUp(self):
        self.teams = list(range(101, 117))  # 16 teams

    def test_01_mathematics_16_teams_30_rounds(self):
        """Verify strict mathematics: 16 teams, 30 rounds, 240 matches, 8 per round, 30 per team."""
        fixtures = generate_asymmetric_round_robin_fixtures(self.teams, shuffle_teams=False)

        # 1. Total matches
        self.assertEqual(len(fixtures), 240)

        # 2. Total rounds
        rounds = set(f[0] for f in fixtures)
        self.assertEqual(len(rounds), 30)
        self.assertEqual(min(rounds), 1)
        self.assertEqual(max(rounds), 30)

        # 3. 8 matches per round with each team playing once
        for r in range(1, 31):
            r_matches = [f for f in fixtures if f[0] == r]
            self.assertEqual(len(r_matches), 8, f"Round {r} has {len(r_matches)} matches instead of 8")
            teams_in_r = []
            for _, p1, p2 in r_matches:
                self.assertNotEqual(p1, p2, "Self-match detected")
                teams_in_r.extend([p1, p2])
            self.assertEqual(len(set(teams_in_r)), 16, f"Not all teams played in round {r}")

        # 4. Each team plays 30 matches
        team_counts = Counter()
        for _, p1, p2 in fixtures:
            team_counts[p1] += 1
            team_counts[p2] += 1
        for t in self.teams:
            self.assertEqual(team_counts[t], 30)

    def test_02_asymmetric_properties_and_rematch_spacing(self):
        """Verify that Leg 2 is truly asymmetric with rematch spacing >= 5 rounds."""
        fixtures = generate_asymmetric_round_robin_fixtures(self.teams, shuffle_teams=False)

        leg1 = [f for f in fixtures if 1 <= f[0] <= 15]
        leg2 = [f for f in fixtures if 16 <= f[0] <= 30]

        self.assertEqual(len(leg1), 120)
        self.assertEqual(len(leg2), 120)

        # Pair meetings
        pair_rounds = defaultdict(list)
        for r, p1, p2 in fixtures:
            pair = tuple(sorted((p1, p2)))
            pair_rounds[pair].append(r)

        # Each pair meets exactly twice: once in Leg 1 and once in Leg 2
        for pair, meeting_rounds in pair_rounds.items():
            self.assertEqual(len(meeting_rounds), 2, f"Pair {pair} has {len(meeting_rounds)} meetings")
            r1, r2 = sorted(meeting_rounds)
            self.assertTrue(1 <= r1 <= 15, f"Leg 1 meeting out of range: {r1}")
            self.assertTrue(16 <= r2 <= 30, f"Leg 2 meeting out of range: {r2}")
            gap = r2 - r1
            self.assertGreaterEqual(gap, 5, f"Rematch spacing too small for {pair}: round {r1} vs {r2} (gap {gap})")

        # Verify asymmetry: Leg 2 is NOT a simple +15 mirror
        is_mirror = all(
            any(f2[0] == f1[0] + 15 and f2[1] == f1[2] and f2[2] == f1[1] for f2 in leg2)
            for f1 in leg1
        )
        self.assertFalse(is_mirror, "Leg 2 should be asymmetric, not a naive +15 mirror!")

    def test_03_home_away_balance_and_streaks(self):
        """Verify strict Home/Away balance: 15 H / 15 A and no streak > 2 consecutive H or A."""
        for seed in (42, 123, 999):
            fixtures = generate_asymmetric_round_robin_fixtures(self.teams, shuffle_teams=True, seed=seed)

            team_ha = defaultdict(list)
            for r in range(1, 31):
                r_matches = [f for f in fixtures if f[0] == r]
                home_teams = set(f[1] for f in r_matches)
                away_teams = set(f[2] for f in r_matches)
                for t in self.teams:
                    team_ha[t].append('H' if t in home_teams else 'A')

            for t in self.teams:
                seq = "".join(team_ha[t])
                # Exactly 15 home and 15 away
                self.assertEqual(seq.count('H'), 15, f"Team {t} does not have 15 home games")
                self.assertEqual(seq.count('A'), 15, f"Team {t} does not have 15 away games")
                # No HHH or AAA
                self.assertNotIn("HHH", seq, f"Team {t} has 3 consecutive home games: {seq}")
                self.assertNotIn("AAA", seq, f"Team {t} has 3 consecutive away games: {seq}")

    def test_04_validator_with_asymmetric_check(self):
        """Verify that RoundRobinValidator validates the asymmetric schedule without errors."""
        fixtures = generate_asymmetric_round_robin_fixtures(self.teams, shuffle_teams=True, seed=777)
        is_valid, errors = RoundRobinValidator.validate_fixtures(
            fixtures,
            expected_teams=16,
            expected_rounds=30,
            expected_matches=240,
            check_asymmetric=True,
            min_rematch_gap=5,
            max_streak=2,
        )
        self.assertTrue(is_valid, f"Validator failed on asymmetric fixtures: {errors}")
        self.assertEqual(len(errors), 0)

    def test_05_shuffling_and_reproducibility(self):
        """Verify that seed ensures reproducibility, while different seeds yield different schedules."""
        fixtures_a1 = generate_asymmetric_round_robin_fixtures(self.teams, shuffle_teams=True, seed=10)
        fixtures_a2 = generate_asymmetric_round_robin_fixtures(self.teams, shuffle_teams=True, seed=10)
        fixtures_b = generate_asymmetric_round_robin_fixtures(self.teams, shuffle_teams=True, seed=20)

        self.assertEqual(fixtures_a1, fixtures_a2, "Same seed must produce identical schedules")
        self.assertNotEqual(fixtures_a1, fixtures_b, "Different seeds must produce different schedules")

    def test_06_fallback_for_arbitrary_teams(self):
        """Verify that generator gracefully handles non-16 team sizes (e.g. 4 teams)."""
        small_teams = [1, 2, 3, 4]
        fixtures = generate_asymmetric_round_robin_fixtures(small_teams, shuffle_teams=False)
        # 4 teams -> 3 rounds per leg = 6 rounds total, 4 * 3 = 12 matches
        self.assertEqual(len(fixtures), 12)
        rounds = set(f[0] for f in fixtures)
        self.assertEqual(len(rounds), 6)


if __name__ == "__main__":
    unittest.main()
