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
    """Load high-DPI system font."""
    size_scaled = size * SCALE
    if bold:
        candidates = ["impact.ttf", "arialbd.ttf", "trebucbd.ttf", "DejaVuSans-Bold.ttf", "seguiemj.ttf"]
    else:
        candidates = ["arial.ttf", "trebuc.ttf", "DejaVuSans.ttf"]

    for fn in candidates:
        try:
            return ImageFont.truetype(fn, size_scaled)
        except (IOError, OSError):
            continue
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
# 🎨 MASTER STATIC CARD GENERATOR (Supports All 10 Styles)
# ═════════════════════════════════════════════════════════════════════════════

def render_master_static_card(player_data: dict, style_id: str = "toty_gold") -> Image.Image:
    """Render high resolution static card for any of the 10 styles."""
    style_id = _normalize_style_key(style_id)
    cfg = CARD_STYLES[style_id]
    ovr, position, player_name, team_name, pac, sho, pas, dri, def_stat, phy = _extract_card_data(player_data)

    img = Image.new("RGBA", (WIDTH, HEIGHT), (cfg["bg_bot"][0], cfg["bg_bot"][1], cfg["bg_bot"][2], 255))
    draw = ImageDraw.Draw(img)

    # 1. Multi-Layer Background Gradient
    tr, tg, tb = cfg["bg_top"]
    br, bg, bb = cfg["bg_bot"]
    for y in range(HEIGHT):
        ratio = y / float(HEIGHT)
        r = int(tr + (br - tr) * ratio)
        g = int(tg + (bg - tg) * ratio)
        b = int(tb + (bb - tb) * ratio)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b, 255))

    # 2. Smooth Radial Ambient Glow (Zero Banding / Zero Concentric Rings)
    cx, cy = WIDTH // 2, int(HEIGHT * 0.28)
    spot_size = int(320 * SCALE)
    spot_img = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(spot_img)
    gr, gg, gb = cfg["glow_rgb"]
    for r in range(60, 0, -2):
        a = int(60 * (1.0 - (r / 60.0) ** 1.5))
        s_draw.ellipse([(64 - r, 64 - r), (64 + r, 64 + r)], fill=(gr, gg, gb, a))
    spot_img = spot_img.resize((spot_size, spot_size), Image.Resampling.BICUBIC)
    img.paste(spot_img, (cx - spot_size // 2, cy - spot_size // 2), spot_img)
    draw = ImageDraw.Draw(img)

    # 3. Outer Frame Geometry (Generous Inset so card floats cleanly inside Telegram bubble)
    inset = int(28 * SCALE)
    cut = int(36 * SCALE)
    bot_y1 = int(HEIGHT * 0.81)
    bot_mid_x = int(48 * SCALE)
    bot_mid_y = int(HEIGHT * 0.90)

    if style_id in ["cyber_hud", "aero_carbon"]:
        # Hexagon/Mech Chamfered Frame
        poly = [
            (inset + cut, inset), (WIDTH - inset - cut, inset),
            (WIDTH - inset, inset + cut), (WIDTH - inset, HEIGHT - inset - cut),
            (WIDTH - inset - cut, HEIGHT - inset), (inset + cut, HEIGHT - inset),
            (inset, HEIGHT - inset - cut), (inset, inset + cut)
        ]
    elif style_id in ["royal_24k", "hyper_glass"]:
        # Clean Rounded Rectangle Ingot
        poly = None
        draw.rounded_rectangle(
            [(inset, inset), (WIDTH - inset, HEIGHT - inset)],
            radius=int(26 * SCALE),
            outline=cfg["border_primary"],
            width=int(4 * SCALE)
        )
        draw.rounded_rectangle(
            [(inset + int(6 * SCALE), inset + int(6 * SCALE)), (WIDTH - inset - int(6 * SCALE), HEIGHT - inset - int(6 * SCALE))],
            radius=int(20 * SCALE),
            outline=cfg["border_secondary"] + (140,),
            width=int(1.5 * SCALE)
        )
    else:
        # Classic & Modern FUT Shield
        poly = [
            (inset + cut, inset), (WIDTH - inset - cut, inset),
            (WIDTH - inset, inset + cut), (WIDTH - inset, bot_y1),
            (WIDTH - inset - bot_mid_x, bot_mid_y), (WIDTH // 2, HEIGHT - inset),
            (inset + bot_mid_x, bot_mid_y), (inset, bot_y1),
            (inset, inset + cut)
        ]

    if poly:
        draw.polygon(poly, outline=cfg["border_primary"], width=int(4.5 * SCALE))
        draw.polygon(poly, outline=cfg["border_secondary"] + (180,), width=int(1.5 * SCALE))

    # 4. Large & Razor-Sharp Player Cutout (Centered with Clean Drop Shadow)
    photo_w = int(440 * SCALE)
    photo_h = int(410 * SCALE)
    player_img = _get_player_photo_image(player_name, team_name)

    if player_img:
        player_img.thumbnail((photo_w, photo_h), Image.Resampling.LANCZOS)
        pw, ph = player_img.size

        # Clean soft drop shadow (No muddy halos)
        shadow = Image.new("RGBA", (pw + int(24 * SCALE), ph + int(24 * SCALE)), (0, 0, 0, 0))
        s_mask = player_img.split()[3] if "A" in player_img.getbands() else Image.new("L", (pw, ph), 255)
        shadow.paste(Image.new("RGBA", (pw, ph), (0, 0, 0, 195)), (int(8 * SCALE), int(8 * SCALE)), s_mask)
        shadow = shadow.filter(ImageFilter.GaussianBlur(int(8 * SCALE)))

        px = (WIDTH - pw) // 2
        py = inset + int(12 * SCALE) + max(0, int((photo_h - ph) * 0.4))

        img.paste(shadow, (px - int(4 * SCALE), py - int(4 * SCALE)), shadow)

        # Smooth baseline alpha fade
        fade = Image.new("L", (pw, ph), 255)
        f_draw = ImageDraw.Draw(fade)
        f_start = int(ph * 0.72)
        for y in range(f_start, ph):
            val = int(255 * (1.0 - ((y - f_start) / (ph - f_start)) ** 1.6))
            f_draw.line([(0, y), (pw, y)], fill=val)
        if "A" in player_img.getbands():
            fade = ImageChops.multiply(player_img.split()[3], fade)

        img.paste(player_img, (px, py), fade)

    # 5. Top-Left OVR / Position Column (Generously offset inside the padded shield)
    col_x = inset + int(48 * SCALE)
    ovr_y = inset + int(36 * SCALE)

    font_ovr = load_card_font(44, bold=True)
    draw.text((col_x + int(2 * SCALE), ovr_y + int(2 * SCALE)), str(ovr), font=font_ovr, fill=(0, 0, 0, 180), anchor="mt")
    draw.text((col_x, ovr_y), str(ovr), font=font_ovr, fill=cfg["text_primary"], anchor="mt")

    # Position Pill
    pos_y = ovr_y + int(48 * SCALE)
    pos_w = int(50 * SCALE)
    pos_h = int(24 * SCALE)
    draw.rounded_rectangle(
        [(col_x - pos_w // 2, pos_y), (col_x + pos_w // 2, pos_y + pos_h)],
        radius=int(6 * SCALE),
        fill=(16, 14, 18, 245),
        outline=cfg["border_primary"],
        width=int(1.5 * SCALE)
    )
    font_pos = load_card_font(18, bold=True)
    draw.text((col_x, pos_y + int(2 * SCALE)), position, font=font_pos, fill=cfg["accent"], anchor="mt")

    # Club Crest
    logo_fn = get_team_logo_filename(team_name)
    if logo_fn:
        logo_path = os.path.join(LOGOS_DIR, logo_fn)
        if os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path)
                logo_img = clean_and_prepare_logo(logo_img)
                l_size = int(44 * SCALE)
                logo_img.thumbnail((l_size, l_size), Image.Resampling.LANCZOS)
                lx = col_x - (logo_img.width // 2)
                ly = pos_y + int(30 * SCALE)
                img.paste(logo_img, (lx, ly), logo_img)
            except Exception:
                pass

    # 6. Player Name Ribbon
    ribbon_w = int(350 * SCALE)
    ribbon_h = int(42 * SCALE)
    rx = (WIDTH - ribbon_w) // 2
    ry = int(356 * SCALE)

    draw.rounded_rectangle(
        [(rx, ry), (rx + ribbon_w, ry + ribbon_h)],
        radius=int(10 * SCALE),
        fill=(18, 16, 22, 245),
        outline=cfg["border_primary"],
        width=int(1.5 * SCALE)
    )

    name_size = 24 if len(player_name) <= 13 else (20 if len(player_name) <= 18 else 16)
    font_name = load_card_font(name_size, bold=True)
    draw.text((WIDTH // 2, ry + int(7 * SCALE)), player_name, font=font_name, fill=cfg["text_primary"], anchor="mt")

    # 7. Frosted Glass Bottom Plate for Stats
    plate_w = int(370 * SCALE)
    plate_h = int(230 * SCALE)
    plate_x = (WIDTH - plate_w) // 2
    plate_y = int(346 * SCALE)

    # 8. 6-Attribute Stat Grid (2x3 with Vertical Separator)
    grid_y = ry + ribbon_h + int(14 * SCALE)
    row_h = int(38 * SCALE)
    sep_x = WIDTH // 2

    draw.line([(sep_x, grid_y - int(2 * SCALE)), (sep_x, grid_y + int(112 * SCALE))], fill=cfg["border_secondary"] + (140,), width=int(1.5 * SCALE))

    font_s_val = load_card_font(26, bold=True)
    font_s_lbl = load_card_font(18, bold=True)

    stats_pairs = [
        (pac, "PAC", dri, "DRI"),
        (sho, "SHO", def_stat, "DEF"),
        (pas, "PAS", phy, "PHY"),
    ]

    c1_v = plate_x + int(42 * SCALE)
    c1_l = plate_x + int(94 * SCALE)
    c2_v = plate_x + int(222 * SCALE)
    c2_l = plate_x + int(274 * SCALE)

    for idx, (lv, ll, rv, rl) in enumerate(stats_pairs):
        cur_y = grid_y + idx * row_h
        draw.text((c1_v, cur_y), f"{lv:>2}", font=font_s_val, fill=cfg["text_primary"], anchor="lt")
        draw.text((c1_l, cur_y + int(4 * SCALE)), ll, font=font_s_lbl, fill=cfg["accent"], anchor="lt")

        draw.text((c2_v, cur_y), f"{rv:>2}", font=font_s_val, fill=cfg["text_primary"], anchor="lt")
        draw.text((c2_l, cur_y + int(4 * SCALE)), rl, font=font_s_lbl, fill=cfg["accent"], anchor="lt")

    # 9. Bottom Footer Badge
    foot_w = int(290 * SCALE)
    foot_h = int(24 * SCALE)
    foot_x = (WIDTH - foot_w) // 2
    foot_y = HEIGHT - int(80 * SCALE)

    draw.rounded_rectangle(
        [(foot_x, foot_y), (foot_x + foot_w, foot_y + foot_h)],
        radius=int(6 * SCALE),
        fill=(16, 14, 18, 245),
        outline=cfg["border_secondary"] + (180,),
        width=int(1.5 * SCALE)
    )
    font_foot = load_card_font(11, bold=True)
    draw.text((WIDTH // 2, foot_y + int(4 * SCALE)), f"★ {cfg['title']} • КПЛ 2026 ★", font=font_foot, fill=cfg["accent"], anchor="mt")

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

    # 1. Base High-Res Static Render & Resize to 600x900 for True HD 1080p Telegram Card Animation
    base_img = render_master_static_card(player_data, style_id=style_id)
    anim_w, anim_h = 600, 900
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

        # ─── 10. UCL NIGHT: Starlight Constellations & Chrome Shimmer ────────
        else:
            shimmer = _create_shimmer_streak(anim_w, anim_h, t, color=(180, 230, 255))
            frame = Image.alpha_composite(frame, shimmer)

            # Star constellation laser lines
            cx, cy = anim_w // 2, int(anim_h * 0.28)
            star_pts = [
                (cx, cy - 110),
                (cx + 95, cy - 30),
                (cx + 60, cy + 90),
                (cx - 60, cy + 90),
                (cx - 95, cy - 30)
            ]
            for s_i in range(len(star_pts)):
                p1 = star_pts[s_i]
                p2 = star_pts[(s_i + 1) % len(star_pts)]
                s_a = int(120 + 80 * math.sin(2 * math.pi * t + s_i))
                fx_draw.line([p1, p2], fill=(0, 212, 255, s_a), width=2)
                fx_draw.ellipse([(p1[0] - 4, p1[1] - 4), (p1[0] + 4, p1[1] + 4)], fill=(255, 255, 255, s_a + 40))

            fx_layer = fx_layer.filter(ImageFilter.GaussianBlur(3))

        frame = Image.alpha_composite(frame, fx_layer)
        frames.append(frame.convert("RGB"))

    # 1. Preferred Telegram Ultra-HD 1080p MP4 Video Encoder (TrueColor 24-bit, 0% banding, 0% noise)
    if cv2 is not None and np is not None:
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp_path = tmp.name

            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(tmp_path, fourcc, fps, (anim_w, anim_h))
            for f in frames:
                arr = np.array(f)
                bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
                writer.write(bgr)
            writer.release()

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
