"""
Очистка кэша фотографий в `scripts/refresh_all_player_cards.py`.

Проход по БД перезаписывает только слаг «имя + клуб», поэтому в кэше остаются
файлы, до которых он не доходит: слаги от прежнего написания имени и плохие
варианты без клуба. Удаление необратимо (каталог не под git), так что проверяем
и то, что удаляется, и то, что удаляться не должно.

Сеть и реальный кэш не используются: `PHOTOS_DIR` уводится в tmp.
"""

import importlib.util
import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from services.graphics import player_photos

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(BASE_DIR, "scripts", "refresh_all_player_cards.py")


def _load_script():
    spec = importlib.util.spec_from_file_location("refresh_all_player_cards", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


refresh = _load_script()


class PhotoCacheTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache_dir = self._tmp.name
        self._patcher = patch.object(player_photos, "PHOTOS_DIR", self.cache_dir)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmp.cleanup()

    def _write_good(self, filename: str) -> str:
        """Полноценная вырезка: 500x500 RGBA."""
        path = os.path.join(self.cache_dir, filename)
        Image.new("RGBA", (500, 500), (10, 120, 200, 255)).save(path)
        return path

    def _write_bad(self, filename: str) -> str:
        """Палитровое превью 192x192 — то, что отдавал FotMob."""
        path = os.path.join(self.cache_dir, filename)
        Image.new("RGB", (192, 192), (90, 90, 90)).convert("P").save(path)
        return path

    def _listing(self) -> list[str]:
        return sorted(os.listdir(self.cache_dir))


class TestIsLowQuality(PhotoCacheTestBase):
    def test_flags_small_image(self):
        self.assertTrue(refresh.is_low_quality_or_headshot(self._write_bad("small.png")))

    def test_accepts_large_image_without_alpha(self):
        """
        Плоская картинка — не брак: фетчер пишет её только когда вырезки нет ни
        у одного провайдера, так что перекачивать её бессмысленно.
        """
        path = os.path.join(self.cache_dir, "flat.png")
        Image.new("RGB", (500, 500), (40, 40, 40)).save(path)
        self.assertFalse(refresh.is_low_quality_or_headshot(path))

    def test_accepts_large_cutout(self):
        self.assertFalse(refresh.is_low_quality_or_headshot(self._write_good("big.png")))

    def test_flags_missing_file(self):
        self.assertTrue(refresh.is_low_quality_or_headshot(os.path.join(self.cache_dir, "nope.png")))


class TestCleanPhotoCache(PhotoCacheTestBase):
    def test_removes_orphan_and_keeps_expected(self):
        self._write_good("rodrygo_бенфика.png")
        self._write_good("gyokeres_спортинг.png")  # слаг от прежнего написания имени

        removed = refresh.clean_photo_cache([("Rodrygo", "Бенфика")])

        self.assertEqual(removed, 1)
        self.assertEqual(self._listing(), ["rodrygo_бенфика.png"])

    def test_keeps_no_team_variant_of_known_player(self):
        """Вызовы без клуба читают запасной слаг — он не сирота."""
        self._write_good("rodrygo_бенфика.png")
        self._write_good("rodrygo.png")

        removed = refresh.clean_photo_cache([("Rodrygo", "Бенфика")])

        self.assertEqual(removed, 0)
        self.assertEqual(self._listing(), ["rodrygo.png", "rodrygo_бенфика.png"])

    def test_removes_low_quality_shadowed_variant(self):
        """Плохой файл без клуба при хорошем файле с клубом должен уйти."""
        self._write_good("rodrygo_бенфика.png")
        self._write_bad("rodrygo.png")

        removed = refresh.clean_photo_cache([("Rodrygo", "Бенфика")])

        self.assertEqual(removed, 1)
        self.assertEqual(self._listing(), ["rodrygo_бенфика.png"])

    def test_keeps_shadowed_variant_when_both_are_bad(self):
        """Лучше плохое фото, чем никакого: единственный источник не удаляем."""
        self._write_bad("rodrygo_бенфика.png")
        self._write_bad("rodrygo.png")

        removed = refresh.clean_photo_cache([("Rodrygo", "Бенфика")])

        self.assertEqual(removed, 0)
        self.assertEqual(self._listing(), ["rodrygo.png", "rodrygo_бенфика.png"])

    def test_empty_player_list_removes_nothing(self):
        """Сверять не с чем — иначе сиротами оказался бы весь кэш."""
        self._write_good("rodrygo_бенфика.png")
        self._write_bad("мбаппе_реал_мадрид.png")

        removed = refresh.clean_photo_cache([])

        self.assertEqual(removed, 0)
        self.assertEqual(len(self._listing()), 2)

    def test_never_touches_service_files(self):
        """`_name_map.json` лежит в том же каталоге и обязан выжить."""
        with open(os.path.join(self.cache_dir, "_name_map.json"), "w", encoding="utf-8") as f:
            f.write('{"Мбаппе|Порту": "Kylian Mbappé"}')
        self._write_good("orphan_клуб.png")

        removed = refresh.clean_photo_cache([("Rodrygo", "Бенфика")])

        self.assertEqual(removed, 1)
        self.assertEqual(self._listing(), ["_name_map.json"])

    def test_dry_run_changes_nothing(self):
        self._write_good("orphan_клуб.png")
        self._write_good("rodrygo_бенфика.png")
        before = self._listing()

        removed = refresh.clean_photo_cache([("Rodrygo", "Бенфика")], dry_run=True)

        self.assertEqual(removed, 1)
        self.assertEqual(self._listing(), before)

    def test_missing_cache_dir_is_not_an_error(self):
        self._tmp.cleanup()
        self.assertEqual(refresh.clean_photo_cache([("Rodrygo", "Бенфика")]), 0)
        self._tmp = tempfile.TemporaryDirectory()  # чтобы tearDown не упал


class TestDropGuard(PhotoCacheTestBase):
    def test_refuses_to_delete_outside_cache_dir(self):
        outside = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        outside.close()
        try:
            dropped = refresh._drop_cached_photo(outside.name, "test", dry_run=False)
            self.assertEqual(dropped, 0)
            self.assertTrue(os.path.exists(outside.name))
        finally:
            os.unlink(outside.name)


if __name__ == "__main__":
    unittest.main()
