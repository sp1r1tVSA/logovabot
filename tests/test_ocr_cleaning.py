"""
Unit tests for the deterministic post-processing layer of the match OCR
(`services/ai/ai_recognizer.py`).

Nothing here touches Gemini or the network: `clean_player_name`,
`clean_team_name`, `validate_and_sanitize_match_events` and
`clean_json_response` are pure functions over whatever the model returned.

The fixtures marked GROUND TRUTH come from a real EA FC Mobile end-of-match
screenshot (badbadnotgood 3 - 1 Chelsea) that was used to audit `PROMPT_TEXT`.
Its two traps are encoded as tests: the empty `ИС` column (which invites a
digit shift) and the bottom row, which carries a goal on one side and an assist
on the other right above the interface buttons.
"""

import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ai.ai_recognizer import (
    BADGE_WORDS,
    POS_TOKENS,
    clean_json_response,
    clean_player_name,
    clean_team_name,
    validate_and_sanitize_match_events,
)


class TestCleanPlayerName(unittest.TestCase):
    def test_strips_russian_positions(self):
        cases = {
            "ЦЗ Niakaté 76": "Niakaté",
            "ЦОП Grillitsch 114": "Grillitsch",
            "ЛП Leonardo Lelo 75": "Leonardo Lelo",
            "ПВ Addai 109": "Addai",
            "ЦАП Grønbæk 97": "Grønbæk",
            "ЛЗ Saracchi 72": "Saracchi",
            "ЦП Hatate 105": "Hatate",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(clean_player_name(raw), expected)

    def test_strips_frv_position(self):
        """ФРВ was missing from POS_TOKENS while ФРД was present."""
        self.assertIn("фрв", POS_TOKENS)
        self.assertEqual(clean_player_name("ФРВ Shomurodov 109"), "Shomurodov")
        self.assertEqual(clean_player_name("Shomurodov ФРВ"), "Shomurodov")

    def test_strips_english_positions(self):
        self.assertEqual(clean_player_name("ST Haaland 99"), "Haaland")
        self.assertEqual(clean_player_name("Haaland RWB"), "Haaland")
        self.assertEqual(clean_player_name("CDM Rodri"), "Rodri")

    def test_position_matching_is_case_insensitive_and_dot_tolerant(self):
        self.assertEqual(clean_player_name("цап Rafa"), "Rafa")
        self.assertEqual(clean_player_name("ЦАП. Rafa"), "Rafa")

    def test_strips_badge_glyphs(self):
        cases = {
            "ЦАП 👑Ricardo Horta 114": "Ricardo Horta",
            "Larsson👑 ПВ": "Larsson",
            "Ricardo Horta ★": "Ricardo Horta",
            "⭐ Larsson": "Larsson",
            "Pedro Gonçalves ⚽": "Pedro Gonçalves",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(clean_player_name(raw), expected)

    def test_strips_bare_badge_letters_and_words(self):
        self.assertEqual(clean_player_name("Larsson (C)"), "Larsson")
        self.assertEqual(clean_player_name("MVP Larsson"), "Larsson")
        self.assertEqual(clean_player_name("Larsson MOTM"), "Larsson")

    def test_keeps_initials_with_a_dot(self):
        """
        A trailing dot distinguishes an initial from a captain marker, so
        "C. Ronaldo" must not collapse to "Ronaldo" — otherwise "C. Silva" and
        "T. Silva" would both become "Silva".
        """
        self.assertIn("c", BADGE_WORDS)
        self.assertEqual(clean_player_name("C. Ronaldo"), "C. Ronaldo")
        self.assertEqual(clean_player_name("R. Lewandowski"), "R. Lewandowski")

    def test_peels_position_and_badge_together(self):
        self.assertEqual(clean_player_name("ЦАП C Ricardo Horta 114"), "Ricardo Horta")
        self.assertEqual(clean_player_name("👑 ЦАП Ricardo Horta"), "Ricardo Horta")

    def test_strips_ratings_from_either_end(self):
        self.assertEqual(clean_player_name("108 Bardghji"), "Bardghji")
        self.assertEqual(clean_player_name("Bardghji 108"), "Bardghji")

    def test_strips_minute_marks(self):
        self.assertEqual(clean_player_name("Addai 32'"), "Addai")
        self.assertEqual(clean_player_name("Addai 45’"), "Addai")

    def test_preserves_single_token_names(self):
        """
        The len(tokens) > 1 guard keeps an abbreviated one-word surname intact
        even when it contains dots and hyphens.
        """
        self.assertEqual(clean_player_name("Oxl.-Chamberlain"), "Oxl.-Chamberlain")
        self.assertEqual(clean_player_name("ЦП Oxl.-Chamberlain"), "Oxl.-Chamberlain")

    def test_preserves_non_ascii(self):
        self.assertEqual(clean_player_name("Grønbæk"), "Grønbæk")
        self.assertEqual(clean_player_name("Niakaté"), "Niakaté")
        self.assertEqual(clean_player_name("ФРВ João Pedro 116"), "João Pedro")

    def test_never_empties_a_name_made_only_of_tokens_it_would_peel(self):
        self.assertEqual(clean_player_name("ЦАП"), "ЦАП")

    def test_empty_and_none_input(self):
        self.assertEqual(clean_player_name(""), "")
        self.assertEqual(clean_player_name(None), "")
        self.assertEqual(clean_player_name("   "), "")
        self.assertEqual(clean_player_name("99"), "")


class TestCleanTeamName(unittest.TestCase):
    def test_strips_level_badge(self):
        self.assertEqual(clean_team_name("badbadnotgood 15 LV"), "badbadnotgood")
        self.assertEqual(clean_team_name("Chelsea 18 LV"), "Chelsea")
        self.assertEqual(clean_team_name("Chelsea 18 lvl"), "Chelsea")
        self.assertEqual(clean_team_name("Брага 7 ур"), "Брага")

    def test_rejects_league_captions(self):
        """
        The grey line under the gamertag is the league name, never the club.
        Returning "" lets handlers/drafts.py fall back to squad detection.
        """
        for noise in ("Логово фифарей", "ЛОГОВО ФИФАРЕЙ", "Champions", "НЕТ ЛИГИ"):
            with self.subTest(noise=noise):
                self.assertEqual(clean_team_name(noise), "")

    def test_keeps_real_names(self):
        self.assertEqual(clean_team_name("badbadnotgood"), "badbadnotgood")
        self.assertEqual(clean_team_name("Chelsea"), "Chelsea")
        self.assertEqual(clean_team_name("  Брага  "), "Брага")

    def test_keeps_digits_that_belong_to_the_club_name(self):
        """Only a digit followed by a level unit is a badge — "04" is part of the club."""
        self.assertEqual(clean_team_name("Шальке 04"), "Шальке 04")
        self.assertEqual(clean_team_name("Schalke 04"), "Schalke 04")

    def test_strips_badge_glyphs(self):
        self.assertEqual(clean_team_name("👑 Chelsea"), "Chelsea")

    def test_empty_and_none_input(self):
        self.assertEqual(clean_team_name(""), "")
        self.assertEqual(clean_team_name(None), "")
        self.assertEqual(clean_team_name("   "), "")


class TestValidateAndSanitizeMatchEvents(unittest.TestCase):
    def test_leaves_a_consistent_match_untouched(self):
        """GROUND TRUTH: badbadnotgood 3 - 1 Chelsea."""
        m = {
            "left_score": 3,
            "right_score": 1,
            "left_goals": ["Addai", "Ricardo Horta", "Ricardo Horta"],
            "right_goals": ["Larsson"],
            "left_assists": ["Grillitsch", "Vitor Carvalho", "Grønbæk"],
            "right_assists": [],
        }
        validate_and_sanitize_match_events(m)
        self.assertEqual(m["left_goals"], ["Addai", "Ricardo Horta", "Ricardo Horta"])
        self.assertEqual(m["right_goals"], ["Larsson"])
        self.assertEqual(m["left_assists"], ["Grillitsch", "Vitor Carvalho", "Grønbæk"])
        self.assertEqual(m["right_assists"], [])
        self.assertNotIn("ocr_needs_review", m)

    def test_trims_excess_assists_preferring_goal_scorers(self):
        m = {
            "left_score": 2,
            "right_score": 0,
            "left_goals": ["A", "A"],
            "left_assists": ["A", "B", "C"],
            "right_goals": [],
            "right_assists": [],
        }
        validate_and_sanitize_match_events(m)
        self.assertEqual(m["left_assists"], ["B", "C"])

    def test_trims_from_the_tail_when_no_dual_player_exists(self):
        m = {
            "left_score": 1,
            "right_score": 0,
            "left_goals": ["Z"],
            "left_assists": ["X", "Y"],
            "right_goals": [],
            "right_assists": [],
        }
        validate_and_sanitize_match_events(m)
        self.assertEqual(m["left_assists"], ["X"])

    def test_drops_all_assists_for_a_goalless_team(self):
        m = {
            "left_score": 0,
            "right_score": 2,
            "left_goals": [],
            "left_assists": ["Phantom"],
            "right_goals": ["R", "R"],
            "right_assists": ["Q"],
        }
        validate_and_sanitize_match_events(m)
        self.assertEqual(m["left_assists"], [])
        self.assertEqual(m["right_assists"], ["Q"])

    def test_sanitizes_the_right_side_too(self):
        m = {
            "left_score": 0,
            "right_score": 1,
            "left_goals": [],
            "left_assists": [],
            "right_goals": ["Rodrygo"],
            "right_assists": ["Rodrygo", "João Pedro"],
        }
        validate_and_sanitize_match_events(m)
        self.assertEqual(m["right_assists"], ["João Pedro"])

    def test_flags_a_cut_off_bottom_row(self):
        """
        The audited screenshot's bottom row holds Larsson's only goal. Losing it
        leaves right_score=1 with an empty right_goals, which no other guard in
        the pipeline catches for a 3-1 scoreline.
        """
        m = {
            "left_score": 3,
            "right_score": 1,
            "left_goals": ["Addai", "Ricardo Horta", "Ricardo Horta"],
            "right_goals": [],
            "left_assists": ["Grillitsch", "Vitor Carvalho"],
            "right_assists": [],
        }
        with self.assertLogs("services.ai.ai_recognizer", level=logging.WARNING) as cm:
            validate_and_sanitize_match_events(m)
        self.assertTrue(m["ocr_needs_review"])
        self.assertIn("right: 0 goal(s) vs score 1", "\n".join(cm.output))

    def test_flags_too_many_goals(self):
        m = {
            "left_score": 1,
            "right_score": 0,
            "left_goals": ["A", "B", "C"],
            "left_assists": [],
            "right_goals": [],
            "right_assists": [],
        }
        with self.assertLogs("services.ai.ai_recognizer", level=logging.WARNING):
            validate_and_sanitize_match_events(m)
        self.assertTrue(m["ocr_needs_review"])

    def test_does_not_flag_a_goalless_team(self):
        """A 0-0 or a clean sheet is not a mismatch, only score > 0 is checked."""
        m = {
            "left_score": 0,
            "right_score": 0,
            "left_goals": [],
            "right_goals": [],
            "left_assists": [],
            "right_assists": [],
        }
        validate_and_sanitize_match_events(m)
        self.assertNotIn("ocr_needs_review", m)

    def test_tolerates_missing_keys(self):
        m = {"left_score": 0, "right_score": 0}
        validate_and_sanitize_match_events(m)
        self.assertNotIn("ocr_needs_review", m)


class TestCleanJsonResponse(unittest.TestCase):
    def test_strips_markdown_fence(self):
        self.assertEqual(clean_json_response('```json\n{"a": 1}\n```'), '{"a": 1}')
        self.assertEqual(clean_json_response('```\n{"a": 1}\n```'), '{"a": 1}')

    def test_passes_plain_json_through(self):
        self.assertEqual(clean_json_response('{"a": 1}'), '{"a": 1}')

    def test_extracts_json_surrounded_by_prose(self):
        self.assertEqual(
            clean_json_response('Вот результат:\n{"a": 1}\nГотово.'), '{"a": 1}'
        )

    def test_keeps_nested_braces(self):
        raw = '```json\n{"matches": [{"team1": "X"}]}\n```'
        self.assertEqual(clean_json_response(raw), '{"matches": [{"team1": "X"}]}')


class TestGroundTruthScreenshotRows(unittest.TestCase):
    """
    End-to-end over the cleaning layer: feed the raw row text a model would emit
    for the audited screenshot and assert the ground-truth JSON comes out.
    """

    def test_full_screenshot_pipeline(self):
        raw = {
            "team1": "badbadnotgood 15 LV",
            "team2": "Chelsea 18 LV",
            "left_score": 3,
            "right_score": 1,
            "is_single_timeline": False,
            "left_goals": ["ПВ Addai 109", "ЦАП 👑Ricardo Horta 114", "ЦАП 👑Ricardo Horta 114"],
            "right_goals": ["Larsson👑 ПВ"],
            "left_assists": ["ЦОП Grillitsch 114", "ЦОП Vitor Carvalho 77", "ЦАП Grønbæk 97"],
            "right_assists": [],
        }

        m = dict(raw)
        for key in ("left_goals", "right_goals", "left_assists", "right_assists"):
            m[key] = [clean_player_name(p) for p in m[key] if clean_player_name(p)]
        m["team1"] = clean_team_name(m["team1"])
        m["team2"] = clean_team_name(m["team2"])
        validate_and_sanitize_match_events(m)

        self.assertEqual(
            {k: m[k] for k in (
                "team1", "team2", "left_score", "right_score", "is_single_timeline",
                "left_goals", "right_goals", "left_assists", "right_assists",
            )},
            {
                "team1": "badbadnotgood",
                "team2": "Chelsea",
                "left_score": 3,
                "right_score": 1,
                "is_single_timeline": False,
                "left_goals": ["Addai", "Ricardo Horta", "Ricardo Horta"],
                "right_goals": ["Larsson"],
                "left_assists": ["Grillitsch", "Vitor Carvalho", "Grønbæk"],
                "right_assists": [],
            },
        )
        self.assertNotIn("ocr_needs_review", m)

    def test_zero_rows_are_never_promoted(self):
        """
        Every player on the right except Larsson, plus three on the left, shows
        0/0. They must not reach any array — the cleaner keeps whatever the model
        sent, so this asserts the ground truth excludes them by construction.
        """
        zero_row_players = [
            "Niakaté", "Leonardo Lelo",
            "Saracchi", "Hatate", "Oxl.-Chamberlain", "Balikwisha", "Iwobi", "Shomurodov",
        ]
        m = {
            "left_score": 3,
            "right_score": 1,
            "left_goals": ["Addai", "Ricardo Horta", "Ricardo Horta"],
            "right_goals": ["Larsson"],
            "left_assists": ["Grillitsch", "Vitor Carvalho", "Grønbæk"],
            "right_assists": [],
        }
        validate_and_sanitize_match_events(m)
        everyone = (
            m["left_goals"] + m["right_goals"] + m["left_assists"] + m["right_assists"]
        )
        for player in zero_row_players:
            with self.subTest(player=player):
                self.assertNotIn(player, everyone)


if __name__ == "__main__":
    unittest.main()
