"""
Фирменный акцент дивизиона (services.graphics.division_theme).

Проверяем три вещи: тема определяется по любому доступному признаку,
незнакомый дивизион не ломает рендер (fail-soft → DEFAULT_THEME),
и плашка рисуется только там, где дивизион известен.
"""

import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from services.graphics import division_theme as dt


class TestThemeByCode(unittest.TestCase):
    def test_known_codes_have_distinct_accents(self):
        accents = [t.accent for t in dt.THEMES.values()]
        self.assertEqual(len(accents), len(set(accents)), "акценты дивизионов должны различаться")

    def test_code_lookup_is_case_and_space_insensitive(self):
        self.assertIs(dt.get_theme_by_code("div_2"), dt.THEMES["DIV_2"])
        self.assertIs(dt.get_theme_by_code("  DIV_2 "), dt.THEMES["DIV_2"])

    def test_unknown_and_empty_codes_fall_back(self):
        for value in (None, "", "DIV_99", "мусор"):
            self.assertIs(dt.get_theme_by_code(value), dt.DEFAULT_THEME, value)

    def test_default_theme_draws_no_badge_label(self):
        self.assertEqual(dt.DEFAULT_THEME.label, "")


class TestContrast(unittest.TestCase):
    def test_dark_accent_gets_white_text(self):
        self.assertEqual(dt._best_text_on((20, 30, 90)), (255, 255, 255))

    def test_light_accent_gets_dark_text(self):
        self.assertEqual(dt._best_text_on((250, 240, 120)), (18, 18, 20))

    def test_every_theme_text_matches_its_accent(self):
        for theme in dt.THEMES.values():
            self.assertEqual(theme.on_accent, dt._best_text_on(theme.accent), theme.code)


class TestResolveTheme(unittest.TestCase):
    def test_explicit_code_wins(self):
        theme = dt.resolve_theme(division_code="DIV_4", division_name="Дивизион 1")
        self.assertEqual(theme.code, "DIV_4")

    def test_division_id_is_resolved_through_database(self):
        with patch.object(dt.database, "get_division", return_value={"id": 7, "code": "DIV_5"}) as get_division:
            theme = dt.resolve_theme(division_id=7)
        get_division.assert_called_once_with(7)
        self.assertEqual(theme.code, "DIV_5")

    def test_division_name_is_a_fallback(self):
        for name in ("Дивизион 3", "ДИВИЗИОН 3 · осень", "3-й дивизион"):
            self.assertEqual(dt.resolve_theme(division_name=name).code, "DIV_3", name)

    def test_name_without_digits_falls_back_to_default(self):
        self.assertIs(dt.resolve_theme(division_name="Основная Лига"), dt.DEFAULT_THEME)

    def test_team_name_is_the_last_resort(self):
        with patch.object(dt.database, "get_team_division_id", return_value=2) as by_team, \
             patch.object(dt.database, "get_division", return_value={"id": 2, "code": "DIV_2"}):
            theme = dt.resolve_theme(team_name="Порту")
        by_team.assert_called_once_with("Порту")
        self.assertEqual(theme.code, "DIV_2")

    def test_database_failure_does_not_propagate(self):
        with patch.object(dt.database, "get_division", side_effect=RuntimeError("db down")), \
             patch.object(dt.database, "get_team_division_id", side_effect=RuntimeError("db down")):
            self.assertIs(dt.resolve_theme(division_id=1, team_name="Порту"), dt.DEFAULT_THEME)

    def test_no_hints_means_default(self):
        self.assertIs(dt.resolve_theme(), dt.DEFAULT_THEME)


class TestDrawBadge(unittest.TestCase):
    def _canvas(self):
        img = Image.new("RGB", (400, 80), (20, 20, 22))
        return img, ImageDraw.Draw(img)

    def test_badge_is_drawn_right_aligned_and_returns_width(self):
        img, draw = self._canvas()
        width = dt.draw_division_badge(draw, dt.THEMES["DIV_3"], 380, 20, None)
        self.assertGreater(width, 0)
        # Правый край плашки прижат к right_x, левее её начала фон нетронут.
        self.assertNotEqual(img.getpixel((375, 32)), (20, 20, 22))
        self.assertEqual(img.getpixel((380 - width - 5, 32)), (20, 20, 22))

    def test_badge_is_skipped_without_a_label(self):
        img, draw = self._canvas()
        before = list(img.getdata())
        self.assertEqual(dt.draw_division_badge(draw, dt.DEFAULT_THEME, 380, 20, None), 0)
        self.assertEqual(list(img.getdata()), before, "тема без метки не должна ничего рисовать")


class TestRenderersUseTheTheme(unittest.TestCase):
    """Тема должна доходить до картинок и при этом оставаться необязательной."""

    def _standings(self):
        return [
            {"position": 1, "team_name": "Порту", "played": 7, "wins": 5, "draws": 2, "losses": 0,
             "goals_scored": 20, "goals_conceded": 6, "goal_difference": 14, "points": 17},
            {"position": 2, "team_name": "Аякс", "played": 7, "wins": 4, "draws": 3, "losses": 0,
             "goals_scored": 14, "goals_conceded": 9, "goal_difference": 5, "points": 15},
        ]

    def _digest(self, division_name):
        return {
            "kind": "digest",
            "division_name": division_name,
            "round_number": 7,
            "results": [{"match_id": 1, "team1": "Порту", "team2": "Аякс", "score1": 2, "score2": 1}],
            "matches_total": 1, "matches_played": 1, "goals_total": 3,
            "rout": None, "player_of_the_round": None, "table": [], "movers": [], "leader": None,
        }

    def test_league_table_differs_between_divisions(self):
        from services.graphics.table_generator import generate_league_table_image

        div1 = generate_league_table_image(self._standings(), None, "Дивизион 1").getvalue()
        div3 = generate_league_table_image(self._standings(), None, "Дивизион 3").getvalue()
        self.assertNotEqual(div1, div3)

    def test_league_table_without_a_division_still_renders(self):
        from services.graphics.table_generator import generate_league_table_image

        self.assertGreater(len(generate_league_table_image(self._standings()).getvalue()), 0)

    def test_round_digest_differs_between_divisions(self):
        from services.graphics.round_digest_generator import generate_round_digest_image

        div1 = generate_round_digest_image(self._digest("Дивизион 1")).getvalue()
        div5 = generate_round_digest_image(self._digest("Дивизион 5")).getvalue()
        self.assertNotEqual(div1, div5)


if __name__ == "__main__":
    unittest.main()
