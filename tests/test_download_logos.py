"""Офлайн-тесты scripts/download_logos.py — сеть не трогается."""

import importlib.util
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "download_logos.py"
_spec = importlib.util.spec_from_file_location("download_logos", _SCRIPT)
dl = importlib.util.module_from_spec(_spec)
sys.modules["download_logos"] = dl
_spec.loader.exec_module(dl)


def _team(name, country="England", sport="Soccer", gender="Male", alt="", badge="https://x/b.png"):
    return {"strTeam": name, "strTeamAlternate": alt, "strCountry": country,
            "strSport": sport, "strGender": gender, "strBadge": badge}


def _club(filename):
    return next(c for c in dl.CLUBS if c.filename == filename)


class TestClubTableMatchesLogoMap:
    def test_same_clubs_and_filenames_as_team_logo_map(self):
        from services.graphics.table_generator import TEAM_LOGO_MAP
        # table_generator дублирует каждый ключ в нижнем регистре — берём исходные
        canonical = {k: v for k, v in TEAM_LOGO_MAP.items() if k != k.lower()}
        assert {c.ru: c.filename for c in dl.CLUBS} == canonical

    def test_no_duplicates(self):
        assert len({c.filename for c in dl.CLUBS}) == len(dl.CLUBS) == 80
        assert len({c.ru for c in dl.CLUBS}) == 80


class TestPickSportsdbTeam:
    def test_accepts_exact_club(self):
        team = _team("Arsenal")
        assert dl.pick_sportsdb_team([team], _club("arsenal.png")) is team

    @pytest.mark.parametrize("team", [
        _team("Arsenal", gender="Female"),
        _team("Arsenal Women"),
        _team("Arsenal", sport="Basketball"),
        _team("Arsenal", country="Russia"),
        _team("Arsenal", badge=None),
    ])
    def test_rejects_wrong_kind_of_team(self, team):
        assert dl.pick_sportsdb_team([team], _club("arsenal.png")) is None

    def test_rejects_same_country_namesake(self):
        # Реальные ответы бесплатного ключа на неудачные запросы
        torcy = _team("Torcy", country="France", alt="Union Sportive Torcy-Paris Vallée de la Marne")
        assert dl.pick_sportsdb_team([torcy], _club("psg.png")) is None
        wolves = _team("Wolverhampton City")
        assert dl.pick_sportsdb_team([wolves], _club("wolverhampton.png")) is None

    def test_folds_diacritics_prefixes_and_the_country_article(self):
        assert dl.pick_sportsdb_team([_team("Köln", country="Germany")], _club("koln.png"))
        assert dl.pick_sportsdb_team([_team("Bodø/Glimt", country="Norway")], _club("bodo_glimt.png"))
        assert dl.pick_sportsdb_team([_team("Ajax", country="The Netherlands")], _club("ajax.png"))

    def test_matches_on_alternate_name(self):
        team = _team("Lille", country="France", alt="Lille OSC")
        assert dl.pick_sportsdb_team([team], _club("lille.png")) is team


def _crest_on_white(size=300):
    """Красный круг с белой полосой внутри на сплошном белом фоне."""
    img = Image.new("RGB", (size, size), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((50, 50, size - 50, size - 50), fill=(200, 0, 0))
    d.rectangle((size // 2 - 10, 80, size // 2 + 10, size - 80), fill="white")
    return img


class TestProcessing:
    def test_edge_white_removed_interior_white_kept(self):
        out = dl.remove_edge_white(_crest_on_white())
        assert out.getpixel((5, 5))[3] == 0            # фон
        assert out.getpixel((150, 150)) == (255, 255, 255, 255)  # белая полоса герба
        assert out.getpixel((100, 150))[3] == 255      # красное тело

    def test_process_logo_gives_centered_square(self):
        out = dl.process_logo(_crest_on_white(), 256)
        assert out.size == (256, 256) and out.mode == "RGBA"
        assert out.getpixel((0, 0))[3] == 0
        bbox = out.getchannel("A").getbbox()
        assert bbox[2] - bbox[0] == 256 or bbox[3] - bbox[1] == 256  # вписан по длинной стороне

    def test_existing_transparency_left_alone(self):
        img = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
        ImageDraw.Draw(img).rectangle((20, 20, 180, 80), fill=(255, 255, 255, 255))
        out = dl.process_logo(img, 256)
        # белый логотип на прозрачном фоне не должен исчезнуть
        assert dl.opaque_ratio(out) > 0.3

    def test_blank_image_rejected(self):
        with pytest.raises(ValueError):
            dl.process_logo(Image.new("RGB", (200, 200), "white"), 256)

    def test_load_image_rejects_garbage_and_tiny(self):
        with pytest.raises(ValueError):
            dl.load_image(b"<html>not an image</html>")
        import io
        buf = io.BytesIO()
        Image.new("RGB", (40, 40), "red").save(buf, format="PNG")
        with pytest.raises(ValueError):
            dl.load_image(buf.getvalue())


class TestValidateFile:
    def test_good_file(self, tmp_path):
        path = tmp_path / "ok.png"
        dl.process_logo(_crest_on_white(), 512).save(path, format="PNG")
        assert dl.validate_file(path) == []

    def test_missing_file(self, tmp_path):
        assert dl.validate_file(tmp_path / "nope.png") == ["файла нет"]

    def test_opaque_rgb_jpeg_flagged(self, tmp_path):
        path = tmp_path / "bad.png"
        Image.effect_noise((256, 256), 64).convert("RGB").save(path, format="JPEG")
        problems = " ".join(dl.validate_file(path))
        assert "JPEG" in problems and "RGB" in problems

    def test_non_transparent_rgba_flagged(self, tmp_path):
        path = tmp_path / "flat.png"
        Image.effect_noise((256, 256), 64).convert("RGBA").save(path, format="PNG")
        assert any("прозрачного фона" in p for p in dl.validate_file(path))
