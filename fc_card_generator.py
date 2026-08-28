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
import player_photos
from table_generator import get_team_logo_filename, clean_and_prepare_logo

try:
    import numpy as np
except ImportError:
    np = None

try:
    import cv2
except ImportError:
    cv2 = None

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(__file__)
LOGOS_DIR = os.path.join(BASE_DIR, "assets", "logos")

SCALE = 2
WIDTH = 460 * SCALE
HEIGHT = 690 * SCALE

# ─────────────────────────────────────────────────────────────────────────────
# 10 DESIGN STYLES CONFIGURATION METADATA
# ─────────────────────────────────────────────────────────────────────────────

CARD_STYLES = {
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
    goals = int(stats.get("total_goals", 0) or 0)
    assists = int(stats.get("total_assists", 0) or 0)
    matches = int(stats.get("matches_played", 0) or max(1, math.ceil((goals + assists) / 2)))
    position = (stats.get("position") or "ST").strip().upper()

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


def _normalize_style_key(style_name: str) -> str:
    s = str(style_name).lower().strip()
    alias_map = {
        "1": "toty_gold", "toty": "toty_gold", "gold": "toty_gold", "celestial": "toty_gold", "design_2": "toty_gold",
        "2": "void_eclipse", "void": "void_eclipse", "eclipse": "void_eclipse", "dark_matter": "void_eclipse",
        "3": "cyber_hud", "cyber": "cyber_hud", "cyberpunk": "cyber_hud", "design_1": "cyber_hud",
        "4": "hyper_glass", "glass": "hyper_glass", "crystal": "hyper_glass", "emerald": "hyper_glass",
        "5": "inferno_magma", "inferno": "inferno_magma", "magma": "inferno_magma", "fire": "inferno_magma",
        "6": "glacial_frost", "glacial": "glacial_frost", "frost": "glacial_frost", "ice": "glacial_frost",
        "7": "anime_sakuga", "anime": "anime_sakuga", "sakuga": "anime_sakuga", "blue_lock": "anime_sakuga",
        "8": "royal_24k", "royal": "royal_24k", "gold_bar": "royal_24k", "design_3": "royal_24k", "luxury": "royal_24k",
        "9": "aero_carbon", "aero": "aero_carbon", "carbon": "aero_carbon", "velocity": "aero_carbon", "red_bull": "aero_carbon",
        "10": "ucl_night", "ucl": "ucl_night", "champions": "ucl_night", "constellation": "ucl_night"
    }
    return alias_map.get(s, "toty_gold" if s not in CARD_STYLES else s)


# ═════════════════════════════════════════════════════════════════════════════
# 🎨 MASTER STATIC CARD GENERATOR (Authentic EA Sports FC FUT Shield Engine)
# ═════════════════════════════════════════════════════════════════════════════

def render_master_static_card(player_data: dict, style_id: str = "toty_gold") -> Image.Image:
    """Render authentic high resolution EA FC 25 Ultimate Team Card Shield."""
    style_id = _normalize_style_key(style_id)
    cfg = CARD_STYLES[style_id]
    ovr, position, player_name, team_name, pac, sho, pas, dri, def_stat, phy = _extract_card_data(player_data)

    img = Image.new("RGBA", (WIDTH, HEIGHT), (8, 10, 15, 255))
    draw = ImageDraw.Draw(img)

    # 1. Outer Stadium Atmospheric Ambient Spotlight
    cx, cy = WIDTH // 2, int(HEIGHT * 0.32)
    glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    g_draw = ImageDraw.Draw(glow)
    gr, gg, gb = cfg["glow_rgb"]
    for r in range(240, 0, -4):
        a = int(65 * (1.0 - (r / 240.0) ** 1.5))
        g_draw.ellipse([(cx - r * SCALE, cy - r * SCALE), (cx + r * SCALE, cy + r * SCALE)], fill=(gr, gg, gb, a))
    glow = glow.filter(ImageFilter.GaussianBlur(25))
    img.paste(glow, (0, 0), glow)

    # 2. FUT Shield Geometry
    inset = int(24 * SCALE)
    cut_top = int(38 * SCALE)
    top_y = int(32 * SCALE)
    bot_y = HEIGHT - int(32 * SCALE)
    left_x = inset
    right_x = WIDTH - inset
    mid_y = int(HEIGHT * 0.71)
    bot_mid_y = int(HEIGHT * 0.87)

    shield_poly = [
        (left_x + cut_top, top_y),
        (right_x - cut_top, top_y),
        (right_x, top_y + cut_top),
        (right_x, mid_y),
        (right_x - int(38 * SCALE), bot_mid_y),
        (WIDTH // 2, bot_y),
        (left_x + int(38 * SCALE), bot_mid_y),
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

    # 5. Large Heroic Player Cutout (Dominant Upper Half, Offset to Right)
    player_img = _get_player_photo_image(player_name, team_name)
    if player_img:
        player_img.thumbnail((int(400 * SCALE), int(400 * SCALE)), Image.Resampling.LANCZOS)
        pw, ph = player_img.size

        # Soft drop shadow
        shadow = Image.new("RGBA", (pw + 30, ph + 30), (0, 0, 0, 0))
        s_mask = player_img.split()[3] if "A" in player_img.getbands() else Image.new("L", (pw, ph), 255)
        shadow.paste(Image.new("RGBA", (pw, ph), (0, 0, 0, 195)), (10, 10), s_mask)
        shadow = shadow.filter(ImageFilter.GaussianBlur(10))

        px = (WIDTH // 2) - (pw // 2) + int(36 * SCALE)
        py = top_y + int(2 * SCALE)

        img.paste(shadow, (px - 5, py - 5), shadow)

        # Baseline fade into name plaque
        fade = Image.new("L", (pw, ph), 255)
        f_draw = ImageDraw.Draw(fade)
        f_start = int(ph * 0.70)
        for y in range(f_start, ph):
            val = int(255 * (1.0 - ((y - f_start) / (ph - f_start)) ** 1.6))
            f_draw.line([(0, y), (pw, y)], fill=val)
        if "A" in player_img.getbands():
            fade = ImageChops.multiply(s_mask, fade)

        img.paste(player_img, (px, py), fade)

    # 6. Authentic Left HUD (OVR, Position, Divider, Club Logo)
    col_x = left_x + int(48 * SCALE)
    ovr_y = top_y + int(24 * SCALE)

    font_ovr = load_card_font(52, bold=True)
    draw.text((col_x + int(2 * SCALE), ovr_y + int(2 * SCALE)), str(ovr), font=font_ovr, fill=(0, 0, 0, 200), anchor="mt")
    draw.text((col_x, ovr_y), str(ovr), font=font_ovr, fill=cfg["border_primary"], anchor="mt")

    pos_y = ovr_y + int(56 * SCALE)
    font_pos = load_card_font(22, bold=True)
    draw.text((col_x, pos_y), position, font=font_pos, fill=cfg["text_primary"], anchor="mt")

    # Separator bar
    sep_y = pos_y + int(28 * SCALE)
    draw.line([(col_x - int(18 * SCALE), sep_y), (col_x + int(18 * SCALE), sep_y)], fill=cfg["border_primary"] + (200,), width=int(2 * SCALE))

    # Club Crest Logo
    logo_fn = get_team_logo_filename(team_name)
    if logo_fn:
        logo_path = os.path.join(LOGOS_DIR, logo_fn)
        if os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path)
                logo_img = clean_and_prepare_logo(logo_img)
                l_size = int(46 * SCALE)
                logo_img.thumbnail((l_size, l_size), Image.Resampling.LANCZOS)
                lx = col_x - (logo_img.width // 2)
                ly = sep_y + int(12 * SCALE)
                img.paste(logo_img, (lx, ly), logo_img)
            except Exception:
                pass

    # 7. Player Name Ribbon (Embossed Metallic Plaque)
    ry = int(HEIGHT * 0.54)
    rw = right_x - left_x - int(24 * SCALE)
    rh = int(44 * SCALE)
    rx = (WIDTH - rw) // 2

    draw.rounded_rectangle([(rx, ry), (rx + rw, ry + rh)], radius=int(8 * SCALE), fill=(16, 18, 26, 245), outline=cfg["border_primary"], width=int(2 * SCALE))
    font_name = load_card_font(24 if len(player_name) <= 14 else (20 if len(player_name) <= 18 else 16), bold=True)
    draw.text((WIDTH // 2, ry + int(7 * SCALE)), player_name, font=font_name, fill=cfg["text_primary"], anchor="mt")

    # 8. 6-Attribute Stat Grid (2x3 with Vertical Separator)
    grid_y = ry + rh + int(14 * SCALE)
    row_h = int(38 * SCALE)
    sep_x = WIDTH // 2

    draw.line([(sep_x, grid_y), (sep_x, grid_y + int(114 * SCALE))], fill=cfg["border_primary"] + (100,), width=int(1.5 * SCALE))

    font_s_val = load_card_font(28, bold=True)
    font_s_lbl = load_card_font(18, bold=True)

    stats_pairs = [
        (pac, "PAC", dri, "DRI"),
        (sho, "SHO", def_stat, "DEF"),
        (pas, "PAS", phy, "PHY"),
    ]

    c1_v = left_x + int(55 * SCALE)
    c1_l = left_x + int(115 * SCALE)
    c2_v = sep_x + int(45 * SCALE)
    c2_l = sep_x + int(105 * SCALE)

    for idx, (lv, ll, rv, rl) in enumerate(stats_pairs):
        cur_y = grid_y + idx * row_h
        draw.text((c1_v, cur_y), f"{lv}", font=font_s_val, fill=cfg["text_primary"], anchor="lt")
        draw.text((c1_l, cur_y + int(4 * SCALE)), ll, font=font_s_lbl, fill=cfg["border_primary"], anchor="lt")

        draw.text((c2_v, cur_y), f"{rv}", font=font_s_val, fill=cfg["text_primary"], anchor="lt")
        draw.text((c2_l, cur_y + int(4 * SCALE)), rl, font=font_s_lbl, fill=cfg["border_primary"], anchor="lt")

    # 9. Bottom Finial & Edition Badge
    foot_y = grid_y + int(120 * SCALE)
    foot_w = int(240 * SCALE)
    foot_h = int(22 * SCALE)
    foot_x = (WIDTH - foot_w) // 2

    draw.rounded_rectangle([(foot_x, foot_y), (foot_x + foot_w, foot_y + foot_h)], radius=int(6 * SCALE), fill=(12, 14, 20, 240), outline=cfg["border_secondary"] + (160,), width=int(1.5 * SCALE))
    font_foot = load_card_font(11, bold=True)
    draw.text((WIDTH // 2, foot_y + int(4 * SCALE)), f"★ {cfg['title']} • КПЛ 2026 ★", font=font_foot, fill=cfg["border_primary"], anchor="mt")

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

def _create_shimmer_streak(w: int, h: int, progress: float, color=(255, 245, 210)) -> Image.Image:
    """Holographic light beam sweep."""
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    beam_x = -w * 0.6 + (w * 2.4) * progress
    beam_w = int(60 * (w / 400.0))

    p = [
        (beam_x, 0),
        (beam_x + beam_w, 0),
        (beam_x + beam_w - int(h * 0.55), h),
        (beam_x - int(h * 0.55), h)
    ]
    draw.polygon(p, fill=color + (95,))
    return overlay.filter(ImageFilter.GaussianBlur(8))


def generate_animated_ea_fc_card(player_data: dict, anim_style: str = "toty_gold") -> io.BytesIO:
    """
    Generate ultra-smooth looping animated GIF in any of the 10 distinct motion design styles.
    """
    style_id = _normalize_style_key(anim_style)
    cfg = CARD_STYLES[style_id]

    # 1. Base High-Res Static Render & Resize to 480x680 (Aspect Ratio Preserved)
    base_img = render_master_static_card(player_data, style_id=style_id)
    anim_w, anim_h = 480, 680
    base_img = base_img.resize((anim_w, anim_h), Image.Resampling.LANCZOS)

    num_frames = 24
    fps = 24.0
    frame_duration_ms = int(1000.0 / fps)
    frames = []

    # Deterministic particle seeds for styles requiring particle physics
    particles = []
    for p in range(32):
        seed_x = ((p * 73 + 19) % 360) / 360.0
        seed_y = ((p * 47 + 11) % 100) / 100.0
        speed = 0.5 + ((p * 31) % 50) / 100.0
        rad = 2 + (p % 4)
        phase = (p * 1.3)
        particles.append((seed_x, seed_y, speed, rad, phase))

    gr, gg, gb = cfg["glow_rgb"]

    for f_idx in range(num_frames):
        t = f_idx / float(num_frames)
        frame = base_img.copy()
        fx_layer = Image.new("RGBA", (anim_w, anim_h), (0, 0, 0, 0))
        fx_draw = ImageDraw.Draw(fx_layer)

        # ─── 1. TOTY GOLD: Liquid God-Rays & Shimmer Beam ────────────────────
        if style_id == "toty_gold":
            shimmer = _create_shimmer_streak(anim_w, anim_h, t, color=(255, 225, 120))
            frame = Image.alpha_composite(frame, shimmer)

            spot_alpha = int(45 + 30 * math.sin(2 * math.pi * t))
            cx, cy = anim_w // 2, int(anim_h * 0.27)
            fx_draw.ellipse([(cx - 200, cy - 200), (cx + 200, cy + 200)], fill=(255, 215, 0, spot_alpha))
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(20))

        # ─── 2. VOID ECLIPSE: Accretion Disk & Inward Gravitational Pull ──────
        elif style_id == "void_eclipse":
            cx, cy = anim_w // 2, int(anim_h * 0.27)
            # Rotating accretion rings
            for r_ring in [120, 180, 240]:
                angle = (2 * math.pi * t) + (r_ring * 0.05)
                arc_x = cx + int(20 * math.cos(angle))
                arc_y = cy + int(20 * math.sin(angle))
                fx_draw.ellipse([(arc_x - r_ring, arc_y - r_ring), (arc_x + r_ring, arc_y + r_ring)], outline=(138, 43, 226, 40), width=4)

            # Inward gravitationally pulled stardust
            for (px_rel, py_rel, spd, rad, phase) in particles:
                dist = (1.0 - (t * spd + py_rel) % 1.0) * 240
                ang = phase + (2 * math.pi * t)
                sx = cx + int(dist * math.cos(ang))
                sy = cy + int(dist * math.sin(ang))
                p_alpha = int(220 * (dist / 240.0))
                fx_draw.ellipse([(sx - rad * 2, sy - rad * 2), (sx + rad * 2, sy + rad * 2)], fill=(0, 245, 255, p_alpha))

            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(3))

        # ─── 3. CYBER HUD: Scanning Laser & Glitch Energy ────────────────────
        elif style_id == "cyber_hud":
            laser_y = int((t * anim_h * 1.2) % anim_h)
            fx_draw.line([(16, laser_y), (anim_w - 16, laser_y)], fill=(0, 255, 224, 180), width=3)
            fx_draw.line([(16, laser_y - 3), (anim_w - 16, laser_y - 3)], fill=(255, 0, 85, 120), width=2)

            pulse_a = int(60 + 50 * math.sin(2 * math.pi * t))
            fx_draw.rounded_rectangle([(20, 20), (anim_w - 20, anim_h - 20)], radius=24, outline=(0, 255, 224, pulse_a), width=3)
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(2))

        # ─── 4. HYPER GLASS: Fluid Caustics & Prismatic Shimmer ──────────────
        elif style_id == "hyper_glass":
            shimmer = _create_shimmer_streak(anim_w, anim_h, t, color=(0, 255, 136))
            frame = Image.alpha_composite(frame, shimmer)

            cx, cy = anim_w // 2, int(anim_h * 0.30)
            glow_a = int(40 + 25 * math.sin(2 * math.pi * t))
            fx_draw.ellipse([(cx - 190, cy - 190), (cx + 190, cy + 190)], fill=(0, 255, 136, glow_a))
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(18))

        # ─── 5. INFERNO MAGMA: Molten Lava Pulse & 35 Rising Embers ──────────
        elif style_id == "inferno_magma":
            cx, cy = anim_w // 2, int(anim_h * 0.28)
            lava_a = int(50 + 35 * math.sin(2 * math.pi * t))
            fx_draw.ellipse([(cx - 180, cy - 180), (cx + 180, cy + 180)], fill=(255, 60, 0, lava_a))

            for (px_rel, py_rel, spd, rad, phase) in particles:
                cur_y_pct = (py_rel - spd * t) % 1.0
                cur_x = int(px_rel * (anim_w - 80) + 40 + math.sin(phase + 2 * math.pi * t) * 18)
                cur_y = int(cur_y_pct * (anim_h - 120) + 40)
                y_norm = cur_y / float(anim_h)
                p_alpha = int(230 * math.sin(math.pi * y_norm))
                fx_draw.ellipse([(cur_x - rad * 2 - 2, cur_y - rad * 2 - 2), (cur_x + rad * 2 + 2, cur_y + rad * 2 + 2)], fill=(255, 120, 0, p_alpha // 2))
                fx_draw.ellipse([(cur_x - rad * 2, cur_y - rad * 2), (cur_x + rad * 2, cur_y + rad * 2)], fill=(255, 230, 80, p_alpha))

            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(2))

        # ─── 6. GLACIAL FROST: Sub-Zero Blizzard & Diamond Star Glitter ──────
        elif style_id == "glacial_frost":
            shimmer = _create_shimmer_streak(anim_w, anim_h, t, color=(160, 230, 255))
            frame = Image.alpha_composite(frame, shimmer)

            cx, cy = anim_w // 2, int(anim_h * 0.28)
            frost_a = int(45 + 30 * math.sin(2 * math.pi * t))
            fx_draw.ellipse([(cx - 190, cy - 190), (cx + 190, cy + 190)], fill=(112, 214, 255, frost_a))

            for (px_rel, py_rel, spd, rad, phase) in particles[:18]:
                cur_t = (t * spd + py_rel) % 1.0
                star_a = int(240 * math.sin(math.pi * cur_t))
                star_x = int(px_rel * (anim_w - 70) + 35)
                star_y = int(py_rel * (anim_h - 140) + 60)
                s_len = int(rad * 4)
                fx_draw.line([(star_x - s_len, star_y), (star_x + s_len, star_y)], fill=(255, 255, 255, star_a), width=2)
                fx_draw.line([(star_x, star_y - s_len), (star_x, star_y + s_len)], fill=(255, 255, 255, star_a), width=2)

            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(2))

        # ─── 7. ANIME SAKUGA: Blue Lock Ego Flame & Lightning Arcs ───────────
        elif style_id == "anime_sakuga":
            cx, cy = anim_w // 2, int(anim_h * 0.28)
            flame_a = int(55 + 35 * math.sin(2 * math.pi * t))
            fx_draw.ellipse([(cx - 180, cy - 200), (cx + 180, cy + 180)], fill=(0, 255, 240, flame_a))

            if f_idx % 3 == 0:
                l_points = [(cx - 120, cy - 60)]
                for step_i in range(5):
                    prev_x, prev_y = l_points[-1]
                    next_x = prev_x + random.randint(30, 70)
                    next_y = prev_y + random.randint(-35, 35)
                    l_points.append((next_x, next_y))
                for pt_idx in range(len(l_points) - 1):
                    fx_draw.line([l_points[pt_idx], l_points[pt_idx + 1]], fill=(255, 255, 255, 220), width=3)

            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(3))

        # ─── 8. ROYAL 24K: Velvet Sheen & Bullion Specular Glide ─────────────
        elif style_id == "royal_24k":
            shimmer = _create_shimmer_streak(anim_w, anim_h, t, color=(255, 237, 179))
            frame = Image.alpha_composite(frame, shimmer)

            cx, cy = anim_w // 2, int(anim_h * 0.30)
            v_a = int(35 + 20 * math.sin(2 * math.pi * t))
            fx_draw.ellipse([(cx - 180, cy - 180), (cx + 180, cy + 180)], fill=(212, 175, 55, v_a))
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(4))

        # ─── 9. AERO CARBON: F1 Streamlines & Telemetry Blink ────────────────
        elif style_id == "aero_carbon":
            for s_idx in range(6):
                stream_y = int((anim_h * 0.2) + s_idx * 75 + math.sin(t * 2 * math.pi + s_idx) * 15)
                s_prog = (t + s_idx * 0.18) % 1.0
                stream_x = int(s_prog * anim_w)
                fx_draw.line([(stream_x, stream_y), (stream_x + 65, stream_y)], fill=(0, 229, 255, 170), width=3)

            pulse_a = int(70 + 40 * math.sin(2 * math.pi * t))
            fx_draw.rounded_rectangle([(20, 20), (anim_w - 20, anim_h - 20)], radius=24, outline=(255, 24, 1, pulse_a), width=3)
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(2))

        # ─── 10. UCL NIGHT: Midnight Cosmic Glow & Chrome Shimmer ───────────
        else:
            shimmer = _create_shimmer_streak(anim_w, anim_h, t, color=(180, 230, 255))
            frame = Image.alpha_composite(frame, shimmer)

            cx, cy = anim_w // 2, int(anim_h * 0.28)
            ucl_a = int(45 + 30 * math.sin(2 * math.pi * t))
            fx_draw.ellipse([(cx - 180, cy - 180), (cx + 180, cy + 180)], fill=(0, 180, 255, ucl_a))
            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(16))

        frame = Image.alpha_composite(frame, fx_layer)
        frames.append(frame.convert("RGB"))

    # 1. Preferred Telegram Ultra-HD 1080p MP4 Video Encoder (TrueColor 24-bit, 0% banding, 0% noise)
    if cv2 is not None and np is not None:
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp_path = tmp.name

            writer = None
            for codec in ["mp4v", "avc1", "H264", "XVID"]:
                try:
                    fourcc = cv2.VideoWriter_fourcc(*codec)
                    w = cv2.VideoWriter(tmp_path, fourcc, fps, (anim_w, anim_h))
                    if w.isOpened():
                        writer = w
                        break
                except Exception:
                    continue

            if writer is not None:
                for f in frames:
                    arr = np.array(f)
                    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
                    writer.write(bgr)
                writer.release()

                if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 1000:
                    with open(tmp_path, "rb") as f_in:
                        buf = io.BytesIO(f_in.read())
                    buf.name = f"{style_id}.mp4"
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                    buf.seek(0)
                    return buf
        except Exception as e:
            logger.warning(f"Failed to encode MP4 video with OpenCV: {e}")

    # 2. High-DPI GIF Fallback with Floyd-Steinberg Dithering
    quantized_frames = [
        f.convert("P", palette=Image.Palette.ADAPTIVE, colors=256, dither=Image.Dither.FLOYDSTEINBERG)
        for f in frames
    ]

    buf = io.BytesIO()
    buf.name = f"{style_id}.gif"
    quantized_frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=quantized_frames[1:],
        duration=frame_duration_ms,
        loop=0,
        optimize=True
    )
    buf.seek(0)
    return buf
