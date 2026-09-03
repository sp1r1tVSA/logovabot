"""
fc_card_generator.py

Ultimate AAA EA FC 25 & Esports Card Generator with 10 Top-Tier Motion Design Styles:

 1. toty_gold      — «TOTY Celestial Gold» (Божественное жидкое золото 24K)
 2. void_eclipse   — «Void Eclipse / Dark Matter» (Черная дыра, сингулярность)
 3. cyber_hud      — «Cyberpunk 2077 / Neo-Tokyo» (Лазерный HUD, глитч)
 4. hyper_glass    — «Liquid Crystal / Hyper-Glass» (Изумрудная призма, каустика)
 5. inferno_magma  — «Inferno Overdrive / Magma» (Раскаленная лава, горящие угли)
 6. glacial_frost  — «Glacial Frost / Diamond» (Арктический лед, алмазный иней)
 7. anime_sakuga   — «Anime Sakuga / Blue Lock» (Аура эгоиста, манга-молнии)
 8. royal_24k      — «Royal 24K Velvet & Ingot» (Банковский слиток, бархат)
 9. aero_carbon    — «Red Bull Velocity / Aero Carbon» (Кованый карбон, F1 телеметрия)
10. ucl_night      — «UEFA Champions Night» (Звездный купол, хром ЛЧ)
"""

import os
import io
import math
import logging
import random
import tempfile
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops
from pathlib import Path
from services.graphics import player_photos
from services.graphics.table_generator import get_team_logo_filename, clean_and_prepare_logo

try:
    import numpy as np
except ImportError:
    np = None

try:
    import cv2
except ImportError:
    cv2 = None

logger = logging.getLogger(__name__)

# Project root directory (services/graphics -> services -> root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = str(PROJECT_ROOT)
LOGOS_DIR = str(PROJECT_ROOT / "assets" / "logos")

SCALE = 2
WIDTH = 460 * SCALE
HEIGHT = 690 * SCALE

# ─────────────────────────────────────────────────────────────────────────────
# 10 DESIGN STYLES CONFIGURATION METADATA
# ─────────────────────────────────────────────────────────────────────────────

CARD_STYLES = {
    "kpl_standard": {
        "title": "КПЛ STANDARD",
        "bg_top": (16, 18, 26),             # Titanium Graphite
        "bg_bot": (8, 9, 14),               # Deep Matte Obsidian
        "border_primary": (210, 215, 225),  # Steel Silver
        "border_secondary": (239, 68, 68),  # KPL Ruby Red Accent
        "accent": (239, 68, 68),
        "text_primary": (255, 255, 255),
        "text_secondary": (210, 215, 225),
        "glow_rgb": (180, 190, 210),
        "desc": "Графитовый титан и рубиновый кант КПЛ (Рейтинг до 85)",
    },
    "kpl_star": {
        "title": "КПЛ STAR EDITION",
        "bg_top": (10, 18, 38),             # Midnight Sapphire
        "bg_bot": (5, 8, 18),               # Deep Indigo
        "border_primary": (0, 230, 255),    # Electric Laser Cyan
        "border_secondary": (239, 68, 68),  # KPL Ruby Red Neon
        "accent": (0, 230, 255),
        "text_primary": (255, 255, 255),
        "text_secondary": (0, 230, 255),
        "glow_rgb": (0, 200, 255),
        "desc": "Сапфирово-рубиновый неон КПЛ (Рейтинг 86-92)",
    },
    "kpl_prime": {
        "title": "КПЛ PRIME MVP",
        "bg_top": (26, 14, 18),             # Royal Obsidian & Crimson
        "bg_bot": (8, 5, 8),                # Deep Flame Void
        "border_primary": (255, 215, 0),    # 24K Polished Gold
        "border_secondary": (239, 68, 68),  # KPL Ruby Red Flame
        "accent": (255, 220, 90),
        "text_primary": (255, 255, 255),
        "text_secondary": (255, 215, 0),
        "glow_rgb": (255, 190, 0),
        "desc": "24K Золото и базальтовое пламя КПЛ (Рейтинг 93+)",
    },
    "toty_gold": {
        "title": "EA FC 24 SPECIAL ITEM",
        "bg_top": (8, 14, 28),             # Dark Navy Obsidian
        "bg_bot": (4, 6, 12),              # Deep Stadium Night
        "border_primary": (245, 208, 97),  # 24K Polished Gold Foil
        "border_secondary": (195, 155, 60),# Brushed Gold Bevel
        "accent": (255, 225, 120),
        "text_primary": (255, 255, 255),
        "text_secondary": (245, 208, 97),
        "glow_rgb": (255, 215, 0),
        "desc": "Dark navy obsidian & 24k polished gold special item",
    },
    "void_eclipse": {
        "title": "VOID ECLIPSE / DARK MATTER",
        "bg_top": (24, 12, 42),
        "bg_bot": (4, 4, 8),
        "border_primary": (138, 43, 226),
        "border_secondary": (0, 245, 255),
        "accent": (0, 245, 255),
        "text_primary": (255, 255, 255),
        "text_secondary": (180, 120, 255),
        "glow_rgb": (138, 43, 226),
        "desc": "Гравитационная сингулярность и аккреционный диск",
    },
    "cyber_hud": {
        "title": "CYBERPUNK 2077 / NEO-TOKYO",
        "bg_top": (18, 22, 32),
        "bg_bot": (9, 10, 15),
        "border_primary": (0, 255, 224),
        "border_secondary": (255, 0, 85),
        "accent": (255, 230, 0),
        "text_primary": (255, 255, 255),
        "text_secondary": (0, 255, 224),
        "glow_rgb": (0, 220, 255),
        "desc": "Неоновый лазерный интерфейс дополненной реальности",
    },
    "hyper_glass": {
        "title": "LIQUID CRYSTAL / HYPER-GLASS",
        "bg_top": (8, 38, 28),
        "bg_bot": (4, 16, 12),
        "border_primary": (0, 255, 136),
        "border_secondary": (0, 229, 255),
        "accent": (0, 255, 136),
        "text_primary": (255, 255, 255),
        "text_secondary": (180, 255, 220),
        "glow_rgb": (0, 255, 136),
        "desc": "Преломляющееся сапфирово-изумрудное стекло с каустикой",
    },
    "inferno_magma": {
        "title": "INFERNO OVERDRIVE / MAGMA",
        "bg_top": (52, 16, 6),
        "bg_bot": (10, 3, 2),
        "border_primary": (255, 59, 0),
        "border_secondary": (255, 174, 0),
        "accent": (255, 174, 0),
        "text_primary": (255, 250, 240),
        "text_secondary": (255, 140, 50),
        "glow_rgb": (255, 80, 0),
        "desc": "Раскаленная магма и искры вулканического базальта",
    },
    "glacial_frost": {
        "title": "GLACIAL FROST / DIAMOND",
        "bg_top": (14, 32, 54),
        "bg_bot": (6, 11, 20),
        "border_primary": (112, 214, 255),
        "border_secondary": (232, 247, 255),
        "accent": (112, 214, 255),
        "text_primary": (255, 255, 255),
        "text_secondary": (180, 230, 255),
        "glow_rgb": (100, 210, 255),
        "desc": "Вечный арктический лед с кристаллами алмазного инея",
    },
    "anime_sakuga": {
        "title": "ANIME SAKUGA / BLUE LOCK",
        "bg_top": (20, 22, 28),
        "bg_bot": (8, 9, 12),
        "border_primary": (0, 255, 240),
        "border_secondary": (255, 255, 255),
        "accent": (0, 255, 240),
        "text_primary": (255, 255, 255),
        "text_secondary": (0, 255, 240),
        "glow_rgb": (0, 255, 240),
        "desc": "Экспрессивная манга-тушь и молнии ауры эгоиста",
    },
    "royal_24k": {
        "title": "ROYAL 24K VELVET & INGOT",
        "bg_top": (46, 10, 24),
        "bg_bot": (13, 11, 9),
        "border_primary": (212, 175, 55),
        "border_secondary": (255, 237, 179),
        "accent": (212, 175, 55),
        "text_primary": (255, 252, 240),
        "text_secondary": (212, 175, 55),
        "glow_rgb": (212, 175, 55),
        "desc": "Лимитированный золотой слиток на королевском бархате",
    },
    "aero_carbon": {
        "title": "RED BULL VELOCITY / AERO CARBON",
        "bg_top": (28, 30, 36),
        "bg_bot": (14, 14, 18),
        "border_primary": (255, 24, 1),
        "border_secondary": (0, 229, 255),
        "accent": (255, 24, 1),
        "text_primary": (255, 255, 255),
        "text_secondary": (255, 80, 80),
        "glow_rgb": (255, 24, 1),
        "desc": "Кованый карбон F1 и телеметрия ветрового туннеля",
    },
    "ucl_night": {
        "title": "UEFA CHAMPIONS NIGHT",
        "bg_top": (6, 18, 48),
        "bg_bot": (2, 6, 23),
        "border_primary": (0, 212, 255),
        "border_secondary": (226, 232, 240),
        "accent": (0, 212, 255),
        "text_primary": (255, 255, 255),
        "text_secondary": (180, 230, 255),
        "glow_rgb": (0, 212, 255),
        "desc": "Звездная ночь Лиги Чемпионов и зеркальный хром",
    }
}


def load_card_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load high-DPI system font supporting Linux, macOS, and Windows."""
    size_scaled = int(size * SCALE)
    if bold:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
            "DejaVuSans-Bold.ttf", "impact.ttf", "arialbd.ttf", "trebucbd.ttf", "seguiemj.ttf"
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
            "DejaVuSans.ttf", "arial.ttf", "trebuc.ttf"
        ]

    for fn in candidates:
        try:
            return ImageFont.truetype(fn, size_scaled)
        except (IOError, OSError):
            continue
    try:
        return ImageFont.load_default(size=size_scaled)
    except Exception:
        return ImageFont.load_default()


def calculate_fut_attributes(stats: dict) -> dict:
    """Calculate 6 FUT attributes & OVR based on stats."""
    from services.player_positions import detect_player_position, normalize_position

    goals = int(stats.get("total_goals", 0) or 0)
    assists = int(stats.get("total_assists", 0) or 0)
    matches = int(stats.get("matches_played", 0) or max(1, math.ceil((goals + assists) / 2)))

    pos_raw = stats.get("position")
    if not pos_raw and stats.get("player_name"):
        pos_raw = detect_player_position(stats["player_name"], stats.get("team_name"), goals, assists)
    position = normalize_position(pos_raw or "ST")

    pac_base, sho_base, pas_base, dri_base, def_base, phy_base = 78, 70, 68, 72, 40, 70

    if position in ["CB", "LB", "RB", "RWB", "LWB", "ЦЗ", "ЛЗ", "ПЗ"]:
        def_base, phy_base, sho_base = 82, 80, 45
    elif position in ["CDM", "CM", "CAM", "ЦП", "ЦОП", "ЦАП"]:
        pas_base, dri_base, def_base, phy_base = 78, 76, 65, 74
    elif position in ["GK", "ВРТ"]:
        def_base, phy_base, sho_base, pac_base = 88, 82, 30, 65

    goals_per_game = goals / max(1, matches)
    assists_per_game = assists / max(1, matches)
    prod_per_game = (goals + assists) / max(1, matches)

    pac = min(99, int(pac_base + min(18, matches * 1.2)))
    sho = min(99, int(sho_base + min(28, goals * 3.5 + goals_per_game * 8)))
    pas = min(99, int(pas_base + min(28, assists * 4.0 + assists_per_game * 8)))
    dri = min(99, int(dri_base + min(25, prod_per_game * 10)))
    def_stat = min(99, int(def_base + min(15, matches * 0.8)))
    phy = min(99, int(phy_base + min(24, matches * 1.5 + goals * 0.5)))

    if position in ["ST", "CF", "LW", "RW", "НАП", "ЛВ", "ПВ"]:
        ovr = int(0.35 * sho + 0.25 * dri + 0.20 * pac + 0.12 * pas + 0.08 * phy)
    elif position in ["CAM", "CM", "ЦАП", "ЦП"]:
        ovr = int(0.30 * pas + 0.25 * dri + 0.20 * sho + 0.15 * pac + 0.10 * phy)
    elif position in ["CB", "LB", "RB", "ЦЗ", "ЛЗ", "ПЗ"]:
        ovr = int(0.35 * def_stat + 0.30 * phy + 0.15 * pac + 0.12 * pas + 0.08 * dri)
    else:
        ovr = int((pac + sho + pas + dri + def_stat + phy) / 6.0)

    return {
        "ovr": max(75, min(99, ovr)),
        "position": position,
        "pac": max(50, pac),
        "sho": max(40, sho),
        "pas": max(45, pas),
        "dri": max(50, dri),
        "def": max(30, def_stat),
        "phy": max(50, phy),
    }


def _extract_card_data(player_data: dict) -> tuple:
    calc_stats = calculate_fut_attributes(player_data)
    ovr = player_data.get("ovr") or calc_stats["ovr"]
    position = player_data.get("position") or calc_stats["position"]
    player_name = (player_data.get("player_name") or "ИГРОК").strip().upper()
    team_name = player_data.get("team_name") or "—"

    custom_stats = player_data.get("custom_stats") or {}
    pac = custom_stats.get("PAC", calc_stats["pac"])
    sho = custom_stats.get("SHO", calc_stats["sho"])
    pas = custom_stats.get("PAS", calc_stats["pas"])
    dri = custom_stats.get("DRI", calc_stats["dri"])
    def_stat = custom_stats.get("DEF", calc_stats["def"])
    phy = custom_stats.get("PHY", calc_stats["phy"])

    return ovr, position, player_name, team_name, pac, sho, pas, dri, def_stat, phy


def _get_player_photo_image(player_name: str, team_name: str) -> Image.Image | None:
    try:
        photo_path = player_photos.get_player_photo(player_name, team_name)
        if photo_path and os.path.exists(photo_path):
            return Image.open(photo_path).convert("RGBA")
    except Exception as e:
        logger.warning(f"Error loading photo for {player_name}: {e}")
    return None


def get_kpl_tier_by_ovr(ovr: int | float | str) -> str:
    """Return the official KPL card tier style key based on OVR rating bracket."""
    try:
        ovr_val = int(ovr)
    except (ValueError, TypeError):
        ovr_val = 80
    if ovr_val >= 93:
        return "kpl_prime"
    elif ovr_val >= 86:
        return "kpl_star"
    else:
        return "kpl_standard"


def _normalize_style_key(style_name: str) -> str:
    s = str(style_name).lower().strip()
    alias_map = {
        # KPL League Official Formats
        "standard": "kpl_standard", "kpl_standard": "kpl_standard", "base": "kpl_standard", "tier1": "kpl_standard", "bronze": "kpl_standard", "silver": "kpl_standard",
        "star": "kpl_star", "kpl_star": "kpl_star", "tier2": "kpl_star", "rare": "kpl_star", "elite": "kpl_star", "inform": "kpl_star", "totw": "kpl_star",
        "prime": "kpl_prime", "kpl_prime": "kpl_prime", "tier3": "kpl_prime", "mvp": "kpl_prime", "legend": "kpl_prime", "toty": "kpl_prime", "toty_gold": "kpl_prime",
        # Legacy & Specialized Themes
        "void": "void_eclipse", "void_eclipse": "void_eclipse", "eclipse": "void_eclipse", "dark_matter": "void_eclipse",
        "cyber": "cyber_hud", "cyber_hud": "cyber_hud", "cyberpunk": "cyber_hud",
        "glass": "hyper_glass", "hyper_glass": "hyper_glass", "crystal": "hyper_glass", "emerald": "hyper_glass",
        "inferno": "inferno_magma", "inferno_magma": "inferno_magma", "magma": "inferno_magma", "fire": "inferno_magma",
        "frost": "glacial_frost", "glacial_frost": "glacial_frost", "ice": "glacial_frost",
        "anime": "anime_sakuga", "anime_sakuga": "anime_sakuga", "sakuga": "anime_sakuga", "blue_lock": "anime_sakuga",
        "royal": "royal_24k", "royal_24k": "royal_24k", "gold_bar": "royal_24k", "luxury": "royal_24k",
        "aero": "aero_carbon", "aero_carbon": "aero_carbon", "carbon": "aero_carbon", "velocity": "aero_carbon",
        "ucl": "ucl_night", "ucl_night": "ucl_night", "champions": "ucl_night"
    }
    return alias_map.get(s, s if s in CARD_STYLES else "kpl_prime")


def _generate_jersey_silhouette(width: int, height: int, collar_y: int, plaque_y: int, cfg: dict) -> Image.Image:
    """
    Render a sleek, authentic athletic esports/football jersey silhouette
    underneath players where only a headshot photo is available.
    Eliminates the 'severed floating head' effect by grounding the head in an authentic kit.
    """
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cx = width // 2

    sh_w = int(175 * SCALE)
    collar_w = int(42 * SCALE)
    collar_depth = int(28 * SCALE)
    shoulder_y = collar_y + int(24 * SCALE)
    chest_bottom_y = plaque_y + int(12 * SCALE)

    # 1. Torso & Shoulders Geometry
    body_poly = [
        (cx - collar_w, collar_y + int(6 * SCALE)),
        (cx, collar_y + collar_depth),
        (cx + collar_w, collar_y + int(6 * SCALE)),
        (cx + sh_w, shoulder_y + int(32 * SCALE)),
        (cx + sh_w - int(12 * SCALE), chest_bottom_y),
        (cx - sh_w + int(12 * SCALE), chest_bottom_y),
        (cx - sh_w, shoulder_y + int(32 * SCALE)),
    ]

    body_mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(body_mask).polygon(body_poly, fill=255)
    body_mask = body_mask.filter(ImageFilter.GaussianBlur(1.5))

    tr, tg, tb = cfg.get("bg_top", (18, 20, 28))
    br, bg_c, bb = cfg.get("bg_bot", (8, 9, 14))

    # 2. Deep Matte Obsidian & Carbon Jersey Gradient
    grad = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    g_draw = ImageDraw.Draw(grad)
    for y in range(collar_y, chest_bottom_y + 1):
        t = (y - collar_y) / float(max(1, chest_bottom_y - collar_y))
        r = int((tr + 18) * (1 - t) + (br + 10) * t)
        g = int((tg + 18) * (1 - t) + (bg_c + 10) * t)
        b = int((tb + 24) * (1 - t) + (bb + 12) * t)
        a = int(240 * (1.0 - t * 0.15))
        g_draw.line([(cx - sh_w - 20, y), (cx + sh_w + 20, y)], fill=(r, g, b, a))

    layer.paste(grad, (0, 0), body_mask)

    # 3. Collar Trim & Seam Accents in Card Theme Border / Accent Colors
    accent = cfg.get("border_primary", (255, 215, 0))
    # Elegant V-neck collar trim
    draw.line([
        (cx - collar_w, collar_y + int(6 * SCALE)),
        (cx, collar_y + collar_depth),
        (cx + collar_w, collar_y + int(6 * SCALE))
    ], fill=accent + (190,), width=int(2.5 * SCALE))

    # Subtle athletic shoulder seams
    draw.line([(cx - collar_w, collar_y + int(10 * SCALE)), (cx - sh_w + int(10 * SCALE), shoulder_y + int(24 * SCALE))], fill=(255, 255, 255, 38), width=int(1.5 * SCALE))
    draw.line([(cx + collar_w, collar_y + int(10 * SCALE)), (cx + sh_w - int(10 * SCALE), shoulder_y + int(24 * SCALE))], fill=(255, 255, 255, 38), width=int(1.5 * SCALE))

    # Trapezius contour lines
    draw.line([(cx - int(collar_w * 0.7), collar_y + int(14 * SCALE)), (cx - int(sh_w * 0.6), shoulder_y + int(30 * SCALE))], fill=accent + (60,), width=int(1 * SCALE))
    draw.line([(cx + int(collar_w * 0.7), collar_y + int(14 * SCALE)), (cx + int(sh_w * 0.6), shoulder_y + int(30 * SCALE))], fill=accent + (60,), width=int(1 * SCALE))

    # 4. Soft Bottom Dissolve into Name Plaque
    fade_start = chest_bottom_y - int(32 * SCALE)
    for y in range(fade_start, chest_bottom_y + 1):
        ratio = (y - fade_start) / float(max(1, chest_bottom_y - fade_start))
        factor = 1.0 - ratio
        # Multiply row alpha
        row_crop = layer.crop((0, y, width, y + 1))
        r, g, b, a = row_crop.split()
        a = a.point(lambda p: int(p * factor))
        layer.paste(Image.merge("RGBA", (r, g, b, a)), (0, y))

    return layer


# ═════════════════════════════════════════════════════════════════════════════
# 🎨 MASTER STATIC CARD GENERATOR (Authentic EA Sports FC FUT Shield Engine)
# ═════════════════════════════════════════════════════════════════════════════

def render_master_static_card(player_data: dict, style_id: str = "toty_gold") -> Image.Image:
    """Render authentic high resolution EA FC 25 Ultimate Team Card Shield."""
    style_id = _normalize_style_key(style_id)
    cfg = CARD_STYLES[style_id]
    ovr, position, player_name, team_name, pac, sho, pas, dri, def_stat, phy = _extract_card_data(player_data)

    img = Image.new("RGBA", (WIDTH, HEIGHT), (6, 8, 14, 255))
    draw = ImageDraw.Draw(img)

    # 1. Outer Stadium Atmospheric Ambient Spotlight (Centered on Player)
    cx, cy = WIDTH // 2, int(HEIGHT * 0.28)
    glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    g_draw = ImageDraw.Draw(glow)
    gr, gg, gb = cfg["glow_rgb"]
    for r in range(280, 0, -5):
        a = int(75 * (1.0 - (r / 280.0) ** 1.4))
        g_draw.ellipse([(cx - r * SCALE, cy - r * SCALE), (cx + r * SCALE, cy + r * SCALE)], fill=(gr, gg, gb, a))
    glow = glow.filter(ImageFilter.GaussianBlur(30))
    img.paste(glow, (0, 0), glow)

    # 2. FUT Shield Geometry
    inset = int(28 * SCALE)
    cut_top = int(42 * SCALE)
    top_y = int(34 * SCALE)
    bot_y = HEIGHT - int(34 * SCALE)
    left_x = inset
    right_x = WIDTH - inset
    mid_y = int(HEIGHT * 0.70)
    bot_mid_y = int(HEIGHT * 0.86)

    shield_poly = [
        (left_x + cut_top, top_y),
        (right_x - cut_top, top_y),
        (right_x, top_y + cut_top),
        (right_x, mid_y),
        (right_x - int(42 * SCALE), bot_mid_y),
        (WIDTH // 2, bot_y),
        (left_x + int(42 * SCALE), bot_mid_y),
        (left_x, mid_y),
        (left_x, top_y + cut_top)
    ]

    shield_mask = Image.new("L", (WIDTH, HEIGHT), 0)
    ImageDraw.Draw(shield_mask).polygon(shield_poly, fill=255)

    # 3. Clean Rich Obsidian & Metallic Shield Body
    tr, tg, tb = cfg["bg_top"]
    br, bg, bb = cfg["bg_bot"]
    shield_bg = Image.new("RGBA", (WIDTH, HEIGHT), (br, bg, bb, 255))
    s_draw = ImageDraw.Draw(shield_bg)

    for y in range(top_y, bot_y):
        t = (y - top_y) / float(bot_y - top_y)
        r = int(tr + (br - tr) * t)
        g = int(tg + (bg - tg) * t)
        b = int(tb + (bb - tb) * t)
        s_draw.line([(left_x, y), (right_x, y)], fill=(r, g, b, 255))

    img.paste(shield_bg, (0, 0), shield_mask)

    # 4. Metallic Shield 3D Bevel Borders
    draw = ImageDraw.Draw(img)
    draw.polygon(shield_poly, outline=cfg["border_primary"], width=int(5 * SCALE))
    draw.polygon(shield_poly, outline=cfg["border_secondary"] + (190,), width=int(2 * SCALE))

    # Name plaque position reference
    ry = int(HEIGHT * 0.515)

    # 5. Heroic Player Cutout & Headshot Handling
    player_img = _get_player_photo_image(player_name, team_name)
    if player_img:
        # 1. Trim transparent borders to get actual player bounds
        bbox = player_img.getbbox()
        if bbox:
            player_img = player_img.crop(bbox)

        orig_w, orig_h = player_img.size
        aspect = orig_w / float(orig_h) if orig_h > 0 else 1.0

        # Intelligent Headshot Detection:
        # Full cutouts include torso/shoulders (aspect ~0.50-0.75, orig_h >= 300).
        # Headshots are compact face crops (aspect >= 0.78 or small resolution orig_h <= 260).
        is_headshot = (aspect >= 0.78) or (orig_h <= 260)

        if is_headshot:
            # Anatomically proportionate head scale (~210px in 1x, not oversized 360px)
            target_h = int(215 * SCALE)
            ph = target_h
            pw = int(ph * aspect)
            max_pw = int(205 * SCALE)
            if pw > max_pw:
                pw = max_pw
                ph = int(pw / aspect)

            player_img = player_img.resize((pw, ph), Image.Resampling.LANCZOS)
            px = (WIDTH - pw) // 2
            py = top_y + int(24 * SCALE)

            collar_y = py + int(ph * 0.68)

            # A. Render sleek athletic esports jersey silhouette underlay
            jersey_silhouette = _generate_jersey_silhouette(WIDTH, HEIGHT, collar_y, ry, cfg)

            # Jersey drop shadow
            j_mask = jersey_silhouette.split()[3]
            j_shadow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
            j_shadow.paste(Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 180)), (0, int(6 * SCALE)), j_mask)
            j_shadow = j_shadow.filter(ImageFilter.GaussianBlur(16))
            img.paste(j_shadow, (0, 0), j_shadow)
            img.paste(jersey_silhouette, (0, 0), jersey_silhouette)

            # B. Soft 3D drop shadow around head
            shadow = Image.new("RGBA", (pw + 40, ph + 40), (0, 0, 0, 0))
            s_mask = player_img.split()[3] if "A" in player_img.getbands() else Image.new("L", (pw, ph), 255)
            shadow.paste(Image.new("RGBA", (pw, ph), (0, 0, 0, 200)), (14, 14), s_mask)
            shadow = shadow.filter(ImageFilter.GaussianBlur(12))
            img.paste(shadow, (px - 7, py - 7), shadow)

            # C. Parabolic soft neck dissolve into collar (no horizontal cut)
            fade = Image.new("L", (pw, ph), 255)
            f_draw = ImageDraw.Draw(fade)
            f_start = int(ph * 0.60)
            for y in range(f_start, ph):
                val = int(255 * (1.0 - ((y - f_start) / float(max(1, ph - f_start))) ** 1.8))
                f_draw.line([(0, y), (pw, y)], fill=val)
            if "A" in player_img.getbands():
                fade = ImageChops.multiply(s_mask, fade)

            img.paste(player_img, (px, py), fade)

        else:
            # Full cutout with torso & jersey
            target_h = int(360 * SCALE)
            ph = target_h
            pw = int(ph * aspect)
            max_pw = int(320 * SCALE)
            if pw > max_pw:
                pw = max_pw
                ph = int(pw / aspect)

            player_img = player_img.resize((pw, ph), Image.Resampling.LANCZOS)

            # Soft 3D drop shadow
            shadow = Image.new("RGBA", (pw + 40, ph + 40), (0, 0, 0, 0))
            s_mask = player_img.split()[3] if "A" in player_img.getbands() else Image.new("L", (pw, ph), 255)
            shadow.paste(Image.new("RGBA", (pw, ph), (0, 0, 0, 220)), (14, 14), s_mask)
            shadow = shadow.filter(ImageFilter.GaussianBlur(14))

            # Centered horizontally on the card
            px = (WIDTH - pw) // 2
            py = top_y + int(10 * SCALE)

            img.paste(shadow, (px - 7, py - 7), shadow)

            # Baseline fade into name plaque
            fade = Image.new("L", (pw, ph), 255)
            f_draw = ImageDraw.Draw(fade)
            f_start = int(ph * 0.70)
            for y in range(f_start, ph):
                val = int(255 * (1.0 - ((y - f_start) / float(max(1, ph - f_start))) ** 1.6))
                f_draw.line([(0, y), (pw, y)], fill=val)
            if "A" in player_img.getbands():
                fade = ImageChops.multiply(s_mask, fade)

            img.paste(player_img, (px, py), fade)

    # 6. Authentic Left HUD (OVR, Position, Divider, Club Logo)
    col_x = left_x + int(52 * SCALE)
    ovr_y = top_y + int(24 * SCALE)

    font_ovr = load_card_font(56, bold=True)
    draw.text((col_x + int(2 * SCALE), ovr_y + int(2 * SCALE)), str(ovr), font=font_ovr, fill=(0, 0, 0, 220), anchor="mt")
    draw.text((col_x, ovr_y), str(ovr), font=font_ovr, fill=cfg["border_primary"], anchor="mt")

    pos_y = ovr_y + int(60 * SCALE)
    font_pos = load_card_font(24, bold=True)
    draw.text((col_x, pos_y), position, font=font_pos, fill=cfg["text_primary"], anchor="mt")

    # Separator bar
    sep_y = pos_y + int(32 * SCALE)
    draw.line([(col_x - int(24 * SCALE), sep_y), (col_x + int(24 * SCALE), sep_y)], fill=cfg["border_primary"] + (220,), width=int(2.5 * SCALE))

    # Club Crest Logo
    logo_fn = get_team_logo_filename(team_name)
    if logo_fn:
        logo_path = os.path.join(LOGOS_DIR, logo_fn)
        if os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path)
                logo_img = clean_and_prepare_logo(logo_img)
                l_size = int(52 * SCALE)
                logo_img.thumbnail((l_size, l_size), Image.Resampling.LANCZOS)
                lx = col_x - (logo_img.width // 2)
                ly = sep_y + int(16 * SCALE)
                img.paste(logo_img, (lx, ly), logo_img)
            except Exception:
                pass

    # 7. Player Name Ribbon (Embossed Metallic Plaque)
    ry = int(HEIGHT * 0.515)
    rw = right_x - left_x - int(24 * SCALE)
    rh = int(48 * SCALE)
    rx = (WIDTH - rw) // 2

    # Draw gradient plaque background
    draw.rounded_rectangle([(rx, ry), (rx + rw, ry + rh)], radius=int(10 * SCALE), fill=(14, 18, 28, 245), outline=cfg["border_primary"], width=int(2.5 * SCALE))
    
    font_size_name = 25 if len(player_name) <= 13 else (21 if len(player_name) <= 17 else 17)
    font_name = load_card_font(font_size_name, bold=True)
    draw.text((WIDTH // 2, ry + (rh // 2)), player_name, font=font_name, fill=cfg["text_primary"], anchor="mm")

    # 8. 6-Attribute Stat Grid (2 Columns, 3 Rows with Vertical Separator)
    grid_y = ry + rh + int(18 * SCALE)
    row_h = int(44 * SCALE)
    sep_x = WIDTH // 2

    # Glowing vertical divider line
    draw.line([(sep_x, grid_y + int(4 * SCALE)), (sep_x, grid_y + int(130 * SCALE))], fill=cfg["border_primary"] + (140,), width=int(2 * SCALE))

    font_s_val = load_card_font(30, bold=True)
    font_s_lbl = load_card_font(21, bold=True)

    stats_pairs = [
        (pac, "PAC", dri, "DRI"),
        (sho, "SHO", def_stat, "DEF"),
        (pas, "PAS", phy, "PHY"),
    ]

    # Left Column: Value right-aligned before label, Label left-aligned
    c1_num_x = sep_x - int(76 * SCALE)
    c1_lbl_x = sep_x - int(58 * SCALE)

    # Right Column: Value right-aligned before label, Label left-aligned
    c2_num_x = sep_x + int(64 * SCALE)
    c2_lbl_x = sep_x + int(82 * SCALE)

    for idx, (lv, ll, rv, rl) in enumerate(stats_pairs):
        cur_y = grid_y + idx * row_h
        
        # Left Side Stat (e.g. 96 PAC)
        draw.text((c1_num_x, cur_y), str(lv), font=font_s_val, fill=cfg["text_primary"], anchor="rt")
        draw.text((c1_lbl_x, cur_y + int(3 * SCALE)), ll, font=font_s_lbl, fill=cfg["border_primary"], anchor="lt")

        # Right Side Stat (e.g. 86 DRI)
        draw.text((c2_num_x, cur_y), str(rv), font=font_s_val, fill=cfg["text_primary"], anchor="rt")
        draw.text((c2_lbl_x, cur_y + int(3 * SCALE)), rl, font=font_s_lbl, fill=cfg["border_primary"], anchor="lt")

    # 9. Bottom Finial & Edition Badge (Positioned safely above tapering shield walls)
    foot_y = grid_y + int(140 * SCALE)
    title_short = cfg['title'].split(' / ')[0].strip()
    foot_text = f"★ {title_short} • КПЛ 2026 ★"

    # Clamp font and width to always maintain comfortable padding from shield borders
    max_text_w = int(220 * SCALE)
    font_sz = 12
    font_foot = load_card_font(font_sz, bold=True)
    bbox = draw.textbbox((0, 0), foot_text, font=font_foot)
    text_w = bbox[2] - bbox[0]

    while text_w > max_text_w and font_sz > 8:
        font_sz -= 1
        font_foot = load_card_font(font_sz, bold=True)
        bbox = draw.textbbox((0, 0), foot_text, font=font_foot)
        text_w = bbox[2] - bbox[0]

    foot_w = min(int(260 * SCALE), text_w + int(24 * SCALE))
    foot_h = int(24 * SCALE)
    foot_x = (WIDTH - foot_w) // 2

    draw.rounded_rectangle(
        [(foot_x, foot_y), (foot_x + foot_w, foot_y + foot_h)],
        radius=int(7 * SCALE),
        fill=(10, 12, 18, 240),
        outline=cfg["border_secondary"] + (180,),
        width=int(1.5 * SCALE)
    )

    draw.text((WIDTH // 2, foot_y + (foot_h // 2)), foot_text, font=font_foot, fill=cfg["border_primary"], anchor="mm")

    return img


def generate_ea_fc_card(player_data: dict, theme_name: str = "toty_gold") -> io.BytesIO:
    """Generate static PNG player card for any of the 10 styles."""
    style_id = _normalize_style_key(theme_name)
    img = render_master_static_card(player_data, style_id=style_id)
    buf = io.BytesIO()
    buf.name = f"{style_id}.png"
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


# ═════════════════════════════════════════════════════════════════════════════
# 🎬 MASTER ANIMATED CARD GENERATOR (All 10 Dedicated Loop Shaders)
# ═════════════════════════════════════════════════════════════════════════════

def _create_shimmer_streak(w: int, h: int, progress: float, color=(255, 245, 210), alpha=65) -> Image.Image:
    """Delicate holographic light beam sweep across card surface."""
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    beam_x = -w * 0.6 + (w * 2.2) * progress
    beam_w = int(45 * (w / 400.0))

    p = [
        (beam_x, 0),
        (beam_x + beam_w, 0),
        (beam_x + beam_w - int(h * 0.50), h),
        (beam_x - int(h * 0.50), h)
    ]
    draw.polygon(p, fill=color + (alpha,))
    return overlay.filter(ImageFilter.GaussianBlur(6))


def _draw_laser_perimeter_runner(fx_draw, pts: list[tuple[int, int]], progress: float, color: tuple[int, int, int], trail_len: float = 0.22):
    """Draw a smooth high-tech laser comet travelling around the card's outer shield perimeter."""
    # Compute total perimeter length
    n = len(pts)
    total_len = 0.0
    seg_lens = []
    for i in range(n):
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        sl = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        seg_lens.append(sl)
        total_len += sl

    if total_len <= 0:
        return

    def get_pt(d):
        d = d % total_len
        accum = 0.0
        for i in range(n):
            sl = seg_lens[i]
            if accum + sl >= d:
                st = (d - accum) / sl if sl > 0 else 0
                p1 = pts[i]
                p2 = pts[(i + 1) % n]
                return (p1[0] + (p2[0] - p1[0]) * st, p1[1] + (p2[1] - p1[1]) * st)
            accum += sl
        return pts[0]

    head_dist = (progress * total_len) % total_len
    num_samples = 14
    trail_dist = total_len * trail_len

    for s in range(num_samples):
        ratio = (s + 1) / float(num_samples)
        d1 = head_dist - (1.0 - ratio) * trail_dist
        d2 = head_dist - (1.0 - (s / float(num_samples))) * trail_dist
        pt1 = get_pt(d1)
        pt2 = get_pt(d2)
        alpha = int(220 * (ratio ** 2))
        width = 2 if ratio < 0.6 else 3
        fx_draw.line([pt1, pt2], fill=color + (alpha,), width=width)

    # Core spark at the head of the laser
    head_pt = get_pt(head_dist)
    hx, hy = int(head_pt[0]), int(head_pt[1])
    fx_draw.ellipse([(hx - 3, hy - 3), (hx + 3, hy + 3)], fill=(255, 255, 255, 250))


def render_animated_card_frames(player_data: dict, anim_style: str = "toty_gold") -> tuple[list[Image.Image], float, int, int]:
    """
    Render raw high-resolution frames of the animated FUT card without palette loss.
    Returns: (frames, fps, anim_w, anim_h)
    """
    style_id = _normalize_style_key(anim_style)
    cfg = CARD_STYLES[style_id]

    # 1. Base High-Res Static Render & Resize to 480x680 (Aspect Ratio Preserved)
    base_img = render_master_static_card(player_data, style_id=style_id)
    anim_w, anim_h = 480, 680
    base_img = base_img.resize((anim_w, anim_h), Image.Resampling.LANCZOS)

    # 2. Scaled shield perimeter polygon for laser border runner
    scale_x = anim_w / float(WIDTH)
    scale_y = anim_h / float(HEIGHT)
    inset = int(28 * SCALE)
    cut_top = int(42 * SCALE)
    top_y = int(34 * SCALE)
    bot_y = HEIGHT - int(34 * SCALE)
    left_x = inset
    right_x = WIDTH - inset
    mid_y = int(HEIGHT * 0.70)
    bot_mid_y = int(HEIGHT * 0.86)

    shield_poly_raw = [
        (left_x + cut_top, top_y),
        (right_x - cut_top, top_y),
        (right_x, top_y + cut_top),
        (right_x, mid_y),
        (right_x - int(42 * SCALE), bot_mid_y),
        (WIDTH // 2, bot_y),
        (left_x + int(42 * SCALE), bot_mid_y),
        (left_x, mid_y),
        (left_x, top_y + cut_top)
    ]
    shield_pts = [(int(x * scale_x), int(y * scale_y)) for (x, y) in shield_poly_raw]

    num_frames = 24
    fps = 24.0
    frames = []

    # Deterministic particle seeds for styles requiring particle physics
    particles = []
    for p in range(28):
        seed_x = ((p * 73 + 19) % 360) / 360.0
        seed_y = ((p * 47 + 11) % 100) / 100.0
        speed = 0.4 + ((p * 31) % 40) / 100.0
        rad = 1 + (p % 3)
        phase = (p * 1.3)
        particles.append((seed_x, seed_y, speed, rad, phase))

    gr, gg, gb = cfg["glow_rgb"]

    for f_idx in range(num_frames):
        t = f_idx / float(num_frames)
        frame = base_img.copy()
        fx_layer = Image.new("RGBA", (anim_w, anim_h), (0, 0, 0, 0))
        fx_draw = ImageDraw.Draw(fx_layer)

        # ─── 0. KPL STANDARD (OVR <= 85): Clean Steel Sheen & Ruby Perimeter Laser ───
        if style_id == "kpl_standard":
            shimmer = _create_shimmer_streak(anim_w, anim_h, t, color=(220, 228, 240), alpha=50)
            frame = Image.alpha_composite(frame, shimmer)
            _draw_laser_perimeter_runner(fx_draw, shield_pts, t, color=(239, 68, 68), trail_len=0.18)
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(1))

        # ─── 0. KPL STAR (OVR 86-92): Prismatic Cyan Sheen, Laser Tracer & Stardust ───
        elif style_id == "kpl_star":
            shimmer = _create_shimmer_streak(anim_w, anim_h, t, color=(0, 230, 255), alpha=55)
            frame = Image.alpha_composite(frame, shimmer)
            _draw_laser_perimeter_runner(fx_draw, shield_pts, t, color=(0, 230, 255), trail_len=0.22)
            # Subtle floating micro-stars
            for (px_rel, py_rel, spd, rad, phase) in particles[:12]:
                cur_y_pct = (py_rel - spd * t) % 1.0
                star_x = int(px_rel * (anim_w - 60) + 30 + math.sin(phase + 2 * math.pi * t) * 8)
                star_y = int(cur_y_pct * (anim_h - 100) + 40)
                star_a = int(220 * math.sin(math.pi * cur_y_pct))
                s_len = int(rad * 2.5)
                fx_draw.line([(star_x - s_len, star_y), (star_x + s_len, star_y)], fill=(255, 255, 255, star_a), width=1)
                fx_draw.line([(star_x, star_y - s_len), (star_x, star_y + s_len)], fill=(0, 230, 255, star_a), width=1)
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(1))

        # ─── 0. KPL PRIME MVP (OVR 93+): 24K Gold Specular Foil, Gold Border Laser & Micro Embers ─────
        elif style_id in ["kpl_prime", "toty_gold"]:
            shimmer = _create_shimmer_streak(anim_w, anim_h, t, color=(255, 225, 120), alpha=60)
            frame = Image.alpha_composite(frame, shimmer)
            # High-tech gold laser runner along shield borders
            _draw_laser_perimeter_runner(fx_draw, shield_pts, t, color=(255, 215, 0), trail_len=0.25)
            # Subtle micro embers rising gracefully (no giant circles)
            for (px_rel, py_rel, spd, rad, phase) in particles[:16]:
                cur_y_pct = (py_rel - spd * t) % 1.0
                cur_x = int(px_rel * (anim_w - 70) + 35 + math.sin(phase + 2 * math.pi * t) * 10)
                cur_y = int(cur_y_pct * (anim_h - 100) + 40)
                p_alpha = int(210 * math.sin(math.pi * cur_y_pct))
                is_gold = (rad % 2 == 0)
                p_col = (255, 215, 0, p_alpha) if is_gold else (239, 68, 68, p_alpha)
                fx_draw.ellipse([(cur_x - rad, cur_y - rad), (cur_x + rad, cur_y + rad)], fill=p_col)
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(1))

        # ─── 2. VOID ECLIPSE: Accretion Disk & Subtle Gravitational Stardust ───
        elif style_id == "void_eclipse":
            _draw_laser_perimeter_runner(fx_draw, shield_pts, t, color=(138, 43, 226), trail_len=0.20)
            cx, cy = anim_w // 2, int(anim_h * 0.27)
            for (px_rel, py_rel, spd, rad, phase) in particles[:14]:
                dist = (1.0 - (t * spd + py_rel) % 1.0) * 200
                ang = phase + (2 * math.pi * t)
                sx = cx + int(dist * math.cos(ang))
                sy = cy + int(dist * math.sin(ang))
                p_alpha = int(200 * (dist / 200.0))
                fx_draw.ellipse([(sx - rad, sy - rad), (sx + rad, sy + rad)], fill=(0, 245, 255, p_alpha))
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(1))

        # ─── 3. CYBER HUD: Scanning Laser & Tech Corner Accents ───────────────
        elif style_id == "cyber_hud":
            laser_y = int((t * anim_h * 1.2) % anim_h)
            fx_draw.line([(16, laser_y), (anim_w - 16, laser_y)], fill=(0, 255, 224, 160), width=2)
            fx_draw.line([(16, laser_y - 2), (anim_w - 16, laser_y - 2)], fill=(255, 0, 85, 100), width=1)
            _draw_laser_perimeter_runner(fx_draw, shield_pts, t, color=(0, 255, 224), trail_len=0.18)
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(1))

        # ─── 4. HYPER GLASS: Fluid Caustics & Prismatic Shimmer ──────────────
        elif style_id == "hyper_glass":
            shimmer = _create_shimmer_streak(anim_w, anim_h, t, color=(0, 255, 136), alpha=55)
            frame = Image.alpha_composite(frame, shimmer)
            _draw_laser_perimeter_runner(fx_draw, shield_pts, t, color=(0, 255, 136), trail_len=0.20)
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(1))

        # ─── 5. INFERNO MAGMA: Molten Border Tracer & Rising Sparks ──────────
        elif style_id == "inferno_magma":
            _draw_laser_perimeter_runner(fx_draw, shield_pts, t, color=(255, 80, 0), trail_len=0.24)
            for (px_rel, py_rel, spd, rad, phase) in particles[:16]:
                cur_y_pct = (py_rel - spd * t) % 1.0
                cur_x = int(px_rel * (anim_w - 60) + 30 + math.sin(phase + 2 * math.pi * t) * 12)
                cur_y = int(cur_y_pct * (anim_h - 100) + 40)
                p_alpha = int(220 * math.sin(math.pi * cur_y_pct))
                fx_draw.ellipse([(cur_x - rad, cur_y - rad), (cur_x + rad, cur_y + rad)], fill=(255, 200, 50, p_alpha))
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(1))

        # ─── 6. GLACIAL FROST: Sub-Zero Diamond Star Glitter ─────────────────
        elif style_id == "glacial_frost":
            shimmer = _create_shimmer_streak(anim_w, anim_h, t, color=(160, 230, 255), alpha=50)
            frame = Image.alpha_composite(frame, shimmer)
            _draw_laser_perimeter_runner(fx_draw, shield_pts, t, color=(112, 214, 255), trail_len=0.20)
            for (px_rel, py_rel, spd, rad, phase) in particles[:12]:
                cur_t = (t * spd + py_rel) % 1.0
                star_a = int(230 * math.sin(math.pi * cur_t))
                star_x = int(px_rel * (anim_w - 60) + 30)
                star_y = int(py_rel * (anim_h - 120) + 50)
                s_len = int(rad * 3)
                fx_draw.line([(star_x - s_len, star_y), (star_x + s_len, star_y)], fill=(255, 255, 255, star_a), width=1)
                fx_draw.line([(star_x, star_y - s_len), (star_x, star_y + s_len)], fill=(180, 235, 255, star_a), width=1)
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(1))

        # ─── 7. ANIME SAKUGA: Lightning Laser Runner & Speed Sparks ──────────
        elif style_id == "anime_sakuga":
            _draw_laser_perimeter_runner(fx_draw, shield_pts, t, color=(0, 255, 240), trail_len=0.25)
            if f_idx % 4 == 0:
                cx, cy = anim_w // 2, int(anim_h * 0.28)
                l_points = [(cx - 90, cy - 40)]
                for step_i in range(4):
                    prev_x, prev_y = l_points[-1]
                    next_x = prev_x + random.randint(25, 50)
                    next_y = prev_y + random.randint(-25, 25)
                    l_points.append((next_x, next_y))
                for pt_idx in range(len(l_points) - 1):
                    fx_draw.line([l_points[pt_idx], l_points[pt_idx + 1]], fill=(255, 255, 255, 180), width=2)
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(1))

        # ─── 8. ROYAL 24K: Clean Velvet Gold Sheen & Border Glide ────────────
        elif style_id == "royal_24k":
            shimmer = _create_shimmer_streak(anim_w, anim_h, t, color=(255, 237, 179), alpha=55)
            frame = Image.alpha_composite(frame, shimmer)
            _draw_laser_perimeter_runner(fx_draw, shield_pts, t, color=(212, 175, 55), trail_len=0.20)
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(1))

        # ─── 9. AERO CARBON: F1 Telemetry Laser & Speed Streamlines ──────────
        elif style_id == "aero_carbon":
            _draw_laser_perimeter_runner(fx_draw, shield_pts, t, color=(255, 24, 1), trail_len=0.22)
            for s_idx in range(4):
                stream_y = int((anim_h * 0.25) + s_idx * 90 + math.sin(t * 2 * math.pi + s_idx) * 10)
                s_prog = (t + s_idx * 0.22) % 1.0
                stream_x = int(s_prog * anim_w)
                fx_draw.line([(stream_x, stream_y), (stream_x + 50, stream_y)], fill=(0, 229, 255, 140), width=2)
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(1))

        # ─── 10. UCL NIGHT: Cosmic Cyan Laser & Constellation Stardust ───────
        else:
            shimmer = _create_shimmer_streak(anim_w, anim_h, t, color=(180, 230, 255), alpha=55)
            frame = Image.alpha_composite(frame, shimmer)
            _draw_laser_perimeter_runner(fx_draw, shield_pts, t, color=(0, 212, 255), trail_len=0.22)
            for (px_rel, py_rel, spd, rad, phase) in particles[:12]:
                cur_t = (t * spd + py_rel) % 1.0
                star_a = int(220 * math.sin(math.pi * cur_t))
                star_x = int(px_rel * (anim_w - 60) + 30)
                star_y = int(py_rel * (anim_h - 120) + 50)
                s_len = int(rad * 2.5)
                fx_draw.line([(star_x - s_len, star_y), (star_x + s_len, star_y)], fill=(255, 255, 255, star_a), width=1)
                fx_draw.line([(star_x, star_y - s_len), (star_x, star_y + s_len)], fill=(0, 212, 255, star_a), width=1)
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(1))

        frame = Image.alpha_composite(frame, fx_layer)
        frames.append(frame.convert("RGB"))

    return frames, fps, anim_w, anim_h


def generate_animated_ea_fc_card(player_data: dict, anim_style: str = "toty_gold") -> io.BytesIO:
    """
    Generate high-definition animated card directly as H.264 MP4 without palette loss.
    """
    from services.animation_sender import convert_to_high_quality_mp4

    style_id = _normalize_style_key(anim_style)
    frames, fps, anim_w, anim_h = render_animated_card_frames(player_data, anim_style=style_id)

    mp4_path, meta, is_temp = convert_to_high_quality_mp4(
        input_source=frames,
        fps=fps,
        initial_crf=16
    )

    try:
        with open(mp4_path, "rb") as f_in:
            buf = io.BytesIO(f_in.read())
        buf.name = f"{style_id}.mp4"
        buf.seek(0)
        return buf
    finally:
        if is_temp and os.path.exists(mp4_path):
            try:
                os.remove(mp4_path)
            except Exception:
                pass
