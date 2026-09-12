"""
Разрешение русских имён футболистов в латинские (`services/graphics/player_photos.py`).

Ключевой риск: клуб участника — это его клуб в игре, а не реальный клуб футболиста,
поэтому поиск по «имя + клуб» легко возвращает постороннюю статью. Проверяем, что
фильтр по заголовку отсекает такие совпадения и что сетевые сбои не попадают в кэш.

Сеть не используется: Википедия замокана, карта имён уводится в tmp.
"""

import json
import os
import tempfile
import unittest
import urllib.parse
from unittest.mock import patch

from services.graphics import player_photos as pp


class TestTitleMatchesName(unittest.TestCase):
    def test_accepts_same_player(self):
        self.assertTrue(pp._title_matches_name("Килиан Мбаппе", "Мбаппе"))
        self.assertTrue(pp._title_matches_name("Диас, Луис Фернандо", "Диас"))
        self.assertTrue(pp._title_matches_name("Артур Гомес (футболист, 1997)", "Артур Гомес"))

    def test_accepts_different_transliteration(self):
        """В игре «Холанд», в Википедии «Холанн»."""
        self.assertTrue(pp._title_matches_name("Холанн, Эрлинг", "Холанд"))

    def test_ignores_initials(self):
        self.assertTrue(pp._title_matches_name("Холанн, Эрлинг", "К. Холанд"))

    def test_rejects_other_player(self):
        """«Мбаппе Порту футболист» возвращал статью про Месси."""
        self.assertFalse(pp._title_matches_name("Лионель Месси", "Мбаппе"))
        self.assertFalse(pp._title_matches_name("Гвардиола, Пеп", "Холанд"))
        self.assertFalse(pp._title_matches_name("Кокшаров, Александр Эдуардович", "Холанд"))

    def test_rejects_club_article(self):
        self.assertFalse(pp._title_matches_name("Реал Мадрид", "Мбаппе"))
        self.assertFalse(pp._title_matches_name("Порту", "Диас"))

    def test_rejects_when_name_has_no_significant_tokens(self):
        self.assertFalse(pp._title_matches_name("Холанн, Эрлинг", "К."))


class TestResolveLatinName(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp.close()
        self._patchers = [
            patch.object(pp, "NAME_MAP_PATH", self._tmp.name),
            patch.object(pp, "_name_map_cache", None),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        pp._name_map_cache = None
        os.unlink(self._tmp.name)

    def _map(self) -> dict:
        with open(self._tmp.name, encoding="utf-8") as f:
            raw = f.read().strip()
        return json.loads(raw) if raw else {}

    def test_latin_name_passes_through_without_lookup(self):
        with patch.object(pp, "_wiki_ru_to_en") as mock_wiki:
            self.assertEqual(pp._resolve_latin_name("Kylian Mbappé", "Порту"), "Kylian Mbappé")
        mock_wiki.assert_not_called()

    def test_successful_resolution_is_cached(self):
        with patch.object(pp, "_wiki_ru_to_en", return_value=("Kylian Mbappé", False)) as mock_wiki:
            self.assertEqual(pp._resolve_latin_name("Мбаппе", "Порту"), "Kylian Mbappé")
            self.assertEqual(pp._resolve_latin_name("Мбаппе", "Порту"), "Kylian Mbappé")
        mock_wiki.assert_called_once()
        self.assertEqual(self._map(), {"Мбаппе|Порту": "Kylian Mbappé"})

    def test_clean_miss_is_cached_as_empty(self):
        """Википедия ответила и ничего не нашла — повторно не спрашиваем."""
        with patch.object(pp, "_wiki_ru_to_en", return_value=(None, False)) as mock_wiki:
            self.assertEqual(pp._resolve_latin_name("Ноунейм", "Порту"), "Ноунейм")
            self.assertEqual(pp._resolve_latin_name("Ноунейм", "Порту"), "Ноунейм")
        mock_wiki.assert_called_once()
        self.assertEqual(self._map(), {"Ноунейм|Порту": ""})

    def test_network_failure_is_not_cached(self):
        """Сбой сети не должен навсегда прибивать игрока к кириллическому поиску."""
        with patch.object(pp, "_wiki_ru_to_en", return_value=(None, True)):
            self.assertEqual(pp._resolve_latin_name("Мбаппе", "Порту"), "Мбаппе")
        self.assertEqual(self._map(), {})

        with patch.object(pp, "_wiki_ru_to_en", return_value=("Kylian Mbappé", False)):
            self.assertEqual(pp._resolve_latin_name("Мбаппе", "Порту"), "Kylian Mbappé")

    def test_club_is_part_of_cache_key(self):
        """У одного имени в разных клубах могут быть разные однофамильцы."""
        with patch.object(pp, "_wiki_ru_to_en", return_value=("Luis Díaz", False)):
            pp._resolve_latin_name("Диас", "Порту")
        with patch.object(pp, "_wiki_ru_to_en", return_value=("Brahim Díaz", False)):
            pp._resolve_latin_name("Диас", "Аякс")
        self.assertEqual(self._map(), {"Диас|Порту": "Luis Díaz", "Диас|Аякс": "Brahim Díaz"})


class TestWikiQueryOrder(unittest.TestCase):
    def test_name_only_query_comes_before_club_query(self):
        """Клуб участника — ненадёжная подсказка, поэтому он только вторая попытка."""
        seen = []

        def fake_urlopen(req, timeout=None):
            seen.append(req.full_url)
            raise OSError("blocked in tests")

        with patch.object(pp.urllib.request, "urlopen", side_effect=fake_urlopen):
            latin, failed = pp._wiki_ru_to_en("Мбаппе", "Порту")

        self.assertIsNone(latin)
        self.assertTrue(failed)
        self.assertEqual(len(seen), 2)
        first, second = (urllib.parse.unquote(u) for u in seen)
        self.assertIn("Мбаппе футболист", first)
        self.assertNotIn("Порту", first)
        self.assertIn("Мбаппе Порту футболист", second)


if __name__ == "__main__":
    unittest.main()
