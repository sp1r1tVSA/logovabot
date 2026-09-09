"""
Дизайн EA FC карточки = диапазон OVR × дивизион (5 × 3 = 15 вариантов).

Ось редкости (тир по OVR) владеет «металлом» — border_primary, свечение,
цвет текста. Дивизион владеет фирменным слоем — вторичный кант, подтон
подложки, узор фона. Проверяем, что оси не съедают друг друга и что
неизвестный дивизион ничего не ломает.
"""

import unittest
from unittest.mock import patch

from services.graphics import fc_card_generator as fc
from services.graphics.division_theme import DEFAULT_THEME, THEMES

TIERS = ("kpl_standard", "kpl_star", "kpl_prime")
CODES = ("DIV_1", "DIV_2", "DIV_3", "DIV_4", "DIV_5")


def _player(ovr):
    return {
        "player_name": "Диас", "team_name": "Порту", "position": "ST",
        "total_goals": 14, "total_assists": 6, "ovr": ovr,
    }


class TestTierByOvrRange(unittest.TestCase):
    def test_every_range_maps_to_its_own_tier(self):
        self.assertEqual(fc.get_kpl_tier_by_ovr(75), "kpl_standard")
        self.assertEqual(fc.get_kpl_tier_by_ovr(85), "kpl_standard")
        self.assertEqual(fc.get_kpl_tier_by_ovr(86), "kpl_star")
        self.assertEqual(fc.get_kpl_tier_by_ovr(92), "kpl_star")
        self.assertEqual(fc.get_kpl_tier_by_ovr(93), "kpl_prime")
        self.assertEqual(fc.get_kpl_tier_by_ovr(99), "kpl_prime")

    def test_tiers_keep_distinct_metals(self):
        primaries = [fc.CARD_STYLES[t]["border_primary"] for t in TIERS]
        self.assertEqual(len(primaries), len(set(primaries)))


class TestBuildDivisionStyle(unittest.TestCase):
    def test_division_owns_the_secondary_accent(self):
        for code in CODES:
            cfg = fc.build_division_style("kpl_prime", THEMES[code])
            self.assertEqual(cfg["border_secondary"], THEMES[code].accent, code)
            self.assertEqual(cfg["division_accent"], THEMES[code].accent, code)

    def test_tier_metal_survives_the_division_layer(self):
        """Дивизион не имеет права перекрасить ось редкости."""
        for tier in TIERS:
            base = fc.CARD_STYLES[tier]
            for code in CODES:
                cfg = fc.build_division_style(tier, THEMES[code])
                self.assertEqual(cfg["border_primary"], base["border_primary"], (tier, code))
                self.assertEqual(cfg["glow_rgb"], base["glow_rgb"], (tier, code))
                self.assertEqual(cfg["title"], base["title"], (tier, code))

    def test_backgrounds_differ_across_divisions(self):
        for tier in TIERS:
            tops = {fc.build_division_style(tier, THEMES[c])["bg_top"] for c in CODES}
            self.assertEqual(len(tops), len(CODES), tier)

    def test_every_division_gets_its_own_pattern(self):
        patterns = [fc.build_division_style("kpl_star", THEMES[c])["division_pattern"] for c in CODES]
        self.assertEqual(len(set(patterns)), len(CODES))
        self.assertNotIn(None, patterns)

    def test_base_style_table_is_never_mutated(self):
        before = dict(fc.CARD_STYLES["kpl_prime"])
        fc.build_division_style("kpl_prime", THEMES["DIV_5"])
        self.assertEqual(fc.CARD_STYLES["kpl_prime"], before)

    def test_unknown_division_keeps_the_plain_tier_palette(self):
        for tier in TIERS:
            cfg = fc.build_division_style(tier, DEFAULT_THEME)
            base = fc.CARD_STYLES[tier]
            self.assertEqual(cfg["bg_top"], base["bg_top"], tier)
            self.assertEqual(cfg["border_secondary"], base["border_secondary"], tier)
            self.assertIsNone(cfg["division_pattern"], tier)

    def test_legendary_styles_are_not_touched_by_divisions(self):
        """Спецкарточки — отдельные коллекционные предметы, а не тиры."""
        for style in ("toty_gold", "void_eclipse", "ucl_night"):
            cfg = fc.build_division_style(style, THEMES["DIV_4"])
            self.assertEqual(cfg["bg_top"], fc.CARD_STYLES[style]["bg_top"], style)
            self.assertEqual(cfg["border_secondary"], fc.CARD_STYLES[style]["border_secondary"], style)
            self.assertIsNone(cfg["division_pattern"], style)


class TestPatternLayer(unittest.TestCase):
    def test_no_pattern_means_no_layer(self):
        self.assertIsNone(fc._draw_division_pattern(200, 200, None, (255, 0, 0)))

    def test_each_pattern_paints_something_and_stays_subtle(self):
        for pattern in set(fc.DIVISION_PATTERNS.values()):
            layer = fc._draw_division_pattern(300, 300, pattern, (255, 255, 255), alpha=26)
            alphas = layer.split()[3].getextrema()
            self.assertEqual(alphas[1], 26, f"{pattern}: узор должен рисоваться ровно заданной альфой")
            self.assertEqual(alphas[0], 0, f"{pattern}: между линиями должна оставаться прозрачность")


class TestRenderedMatrixIsDistinct(unittest.TestCase):
    """15 реальных рендеров: ни одна пара не должна совпасть."""

    def _render(self, code, tier, ovr):
        with patch("services.graphics.division_theme.database.get_division",
                   return_value={"id": 1, "code": code}):
            img = fc.render_master_static_card({**_player(ovr), "division_id": 1}, style_id=tier)
        return img.tobytes()

    def test_all_fifteen_designs_differ(self):
        seen = {}
        for code in CODES:
            for tier, ovr in zip(TIERS, (82, 89, 95)):
                key = self._render(code, tier, ovr)
                self.assertNotIn(key, seen, f"{code}/{tier} совпал с {seen.get(key)}")
                seen[key] = f"{code}/{tier}"
        self.assertEqual(len(seen), 15)

    def test_card_without_a_division_still_renders(self):
        img = fc.render_master_static_card(_player(89), style_id="kpl_star")
        self.assertEqual(img.size, (fc.WIDTH, fc.HEIGHT))


class TestAnimationUsesTheDivisionAccent(unittest.TestCase):
    def test_frames_differ_between_divisions(self):
        rendered = {}
        for code in ("DIV_1", "DIV_5"):
            with patch("services.graphics.division_theme.database.get_division",
                       return_value={"id": 1, "code": code}):
                frames, _, _, _ = fc.render_animated_card_frames(
                    {**_player(95), "division_id": 1}, anim_style="kpl_prime"
                )
            rendered[code] = frames
        self.assertNotEqual(rendered["DIV_1"][5].tobytes(), rendered["DIV_5"][5].tobytes())


if __name__ == "__main__":
    unittest.main()
