"""
fc_card_generator.py

Professional AAA EA FC 25 & Esports Card Generator.
Provides 3 polished, hyper-realistic, high-end card design concepts:

1. DESIGN 1: «CYBER PRO BROADCAST» (Неоновый киберспортивный HUD)
2. DESIGN 2: «AUTHENTIC EA FC 25 GOLD» (Премиальный золотой щит EA Sports FC)
3. DESIGN 3: «OBSIDIAN LUXURY VIP» (Минималистичный люксовый постер)
"""

import os
import io
import math
import logging
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops, ImageEnhance
import database
from table_generator import get_team_logo_filename, clean_and_prepare_logo
import player_photos

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(__file__)
LOGOS_DIR = os.path.join(BASE_DIR, "assets", "logos")

SCALE = 2
WIDTH = 460 * SCALE
HEIGHT = 690 * SCALE


def load_card_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load system font with high DPI scaling."""
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

    pac_base = 78
    sho_base = 70
    pas_base = 68
    dri_base = 72
    def_base = 40
    phy_base = 70

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
        logger.warning(f"Error loading player photo for {player_name}: {e}")
    return None


# ═════════════════════════════════════════════════════════════════════════════
# 🎨 1. CYBER PRO BROADCAST DESIGN
# ═════════════════════════════════════════════════════════════════════════════

def render_design_1_cyber(player_data: dict) -> io.BytesIO:
    """Cyberpunk / Neon Esports Broadcast Card with tech HUD and power meter bars."""
    ovr, position, player_name, team_name, pac, sho, pas, dri, def_stat, phy = _extract_card_data(player_data)

    img = Image.new("RGBA", (WIDTH, HEIGHT), (10, 11, 15, 255))
    draw = ImageDraw.Draw(img)

    # Vertical Dark Sci-Fi Gradient
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        r = int(18 - 10 * ratio)
        g = int(22 - 12 * ratio)
        b = int(32 - 18 * ratio)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b, 255))

    # Futuristic Hexagon Cut Frame
    inset = int(14 * SCALE)
    cut = int(30 * SCALE)
    poly = [
        (inset + cut, inset), (WIDTH - inset - cut, inset),
        (WIDTH - inset, inset + cut), (WIDTH - inset, HEIGHT - inset - cut),
        (WIDTH - inset - cut, HEIGHT - inset), (inset + cut, HEIGHT - inset),
        (inset, HEIGHT - inset - cut), (inset, inset + cut)
    ]
    draw.polygon(poly, outline=(0, 225, 255, 180), width=int(2.5 * SCALE))

    # Cyan Radial Glow in center
    cx, cy = WIDTH // 2, int(HEIGHT * 0.28)
    spotlight = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(spotlight)
    for r in range(int(240 * SCALE), 0, -int(15 * SCALE)):
        alpha = int(75 * (1.0 - (r / (240 * SCALE)) ** 1.3))
        s_draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=(0, 200, 255, alpha))
    spotlight = spotlight.filter(ImageFilter.GaussianBlur(int(16 * SCALE)))
    img = Image.alpha_composite(img, spotlight)
    draw = ImageDraw.Draw(img)

    # Big Centered Player Photo
    photo_w = int(410 * SCALE)
    photo_h = int(380 * SCALE)
    player_img = _get_player_photo_image(player_name, team_name)

    if player_img:
        player_img.thumbnail((photo_w, photo_h), Image.Resampling.LANCZOS)
        pw, ph = player_img.size

        # Shadow
        shadow = Image.new("RGBA", (pw + int(24 * SCALE), ph + int(24 * SCALE)), (0, 0, 0, 0))
        s_mask = player_img.split()[3] if "A" in player_img.getbands() else Image.new("L", (pw, ph), 255)
        shadow.paste(Image.new("RGBA", (pw, ph), (0, 0, 0, 190)), (int(10 * SCALE), int(10 * SCALE)), s_mask)
        shadow = shadow.filter(ImageFilter.GaussianBlur(int(9 * SCALE)))

        px = (WIDTH - pw) // 2
        py = int(24 * SCALE) + (photo_h - ph)
        img.paste(shadow, (px - int(4 * SCALE), py - int(4 * SCALE)), shadow)

        # Base fade
        fade = Image.new("L", (pw, ph), 255)
        f_draw = ImageDraw.Draw(fade)
        f_start = int(ph * 0.68)
        for y in range(f_start, ph):
            val = int(255 * (1.0 - ((y - f_start) / (ph - f_start)) ** 1.6))
            f_draw.line([(0, y), (pw, y)], fill=val)
        if "A" in player_img.getbands():
            fade = ImageChops.multiply(player_img.split()[3], fade)

        img.paste(player_img, (px, py), fade)

    # Top-Left HUD OVR / POS Plate
    pil_w = int(76 * SCALE)
    pil_h = int(122 * SCALE)
    pil_x = int(26 * SCALE)
    pil_y = int(38 * SCALE)
    pil_poly = [
        (pil_x, pil_y),
        (pil_x + pil_w, pil_y),
        (pil_x + pil_w, pil_y + pil_h - int(16 * SCALE)),
        (pil_x + pil_w - int(16 * SCALE), pil_y + pil_h),
        (pil_x, pil_y + pil_h),
    ]
    draw.polygon(pil_poly, fill=(15, 18, 26, 235), outline=(0, 240, 255, 220), width=int(2 * SCALE))

    font_ovr = load_card_font(46, bold=True)
    draw.text((pil_x + pil_w // 2, pil_y + int(10 * SCALE)), str(ovr), font=font_ovr, fill=(255, 255, 255), anchor="mt")
    draw.line([(pil_x + int(10 * SCALE), pil_y + int(64 * SCALE)), (pil_x + pil_w - int(10 * SCALE), pil_y + int(64 * SCALE))], fill=(0, 240, 255, 160), width=int(1.5 * SCALE))

    font_pos = load_card_font(19, bold=True)
    draw.text((pil_x + pil_w // 2, pil_y + int(76 * SCALE)), position, font=font_pos, fill=(255, 205, 35), anchor="mt")

    # Top-Right Club Logo
    logo_fn = get_team_logo_filename(team_name)
    if logo_fn:
        logo_path = os.path.join(LOGOS_DIR, logo_fn)
        if os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path)
                logo_img = clean_and_prepare_logo(logo_img)
                l_size = int(44 * SCALE)
                logo_img.thumbnail((l_size, l_size), Image.Resampling.LANCZOS)
                img.paste(logo_img, (WIDTH - int(66 * SCALE), int(36 * SCALE)), logo_img)
            except Exception:
                pass

    # Angled Name Plate
    nw = int(390 * SCALE)
    nh = int(46 * SCALE)
    nx = (WIDTH - nw) // 2
    ny = int(360 * SCALE)
    name_poly = [
        (nx + int(20 * SCALE), ny),
        (nx + nw, ny),
        (nx + nw - int(20 * SCALE), ny + nh),
        (nx, ny + nh),
    ]
    draw.polygon(name_poly, fill=(16, 20, 28, 245), outline=(255, 205, 35, 220), width=int(2 * SCALE))
    font_name = load_card_font(23, bold=True)
    draw.text((WIDTH // 2, ny + int(10 * SCALE)), player_name, font=font_name, fill=(255, 255, 255), anchor="mt")

    # 6 Stat Capsules with Meters
    grid_y = ny + nh + int(18 * SCALE)
    stats_list = [
        ("PAC", pac), ("DRI", dri),
        ("SHO", sho), ("DEF", def_stat),
        ("PAS", pas), ("PHY", phy),
    ]
    font_s_num = load_card_font(20, bold=True)
    font_s_lbl = load_card_font(13, bold=True)

    col_w = int(185 * SCALE)
    row_h = int(38 * SCALE)
    c1_x = int(36 * SCALE)
    c2_x = WIDTH - int(36 * SCALE) - col_w

    for idx, (lbl, val) in enumerate(stats_list):
        row = idx // 2
        col = idx % 2
        bx = c1_x if col == 0 else c2_x
        by = grid_y + row * (row_h + int(10 * SCALE))

        draw.rounded_rectangle(
            [(bx, by), (bx + col_w, by + row_h)],
            radius=int(8 * SCALE),
            fill=(14, 18, 26, 235),
            outline=(0, 220, 255, 120),
            width=int(1.5 * SCALE)
        )

        bar_pad = int(6 * SCALE)
        bar_w = col_w - 2 * bar_pad
        bar_h = int(4 * SCALE)
        bar_y = by + row_h - bar_pad - bar_h

        draw.rounded_rectangle([(bx + bar_pad, bar_y), (bx + bar_pad + bar_w, bar_y + bar_h)], radius=int(2 * SCALE), fill=(30, 36, 48))
        fill_w = int(bar_w * (val / 99.0))
        draw.rounded_rectangle([(bx + bar_pad, bar_y), (bx + bar_pad + fill_w, bar_y + bar_h)], radius=int(2 * SCALE), fill=(255, 205, 35))

        draw.text((bx + int(12 * SCALE), by + int(6 * SCALE)), lbl, font=font_s_lbl, fill=(0, 240, 255), anchor="lt")
        draw.text((bx + col_w - int(12 * SCALE), by + int(4 * SCALE)), str(val), font=font_s_num, fill=(255, 255, 255), anchor="rt")

    # Bottom Tag
    foot_y = HEIGHT - int(46 * SCALE)
    font_foot = load_card_font(10, bold=True)
    draw.text((WIDTH // 2, foot_y), "LOGOVOBOT CYBER SERIES • 2026", font=font_foot, fill=(0, 220, 255, 180), anchor="mt")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


# ═════════════════════════════════════════════════════════════════════════════
# 🎨 2. AUTHENTIC EA FC 25 GOLD DESIGN (Fixed & Hyper-Polished)
# ═════════════════════════════════════════════════════════════════════════════

def render_design_2_fut_shield(player_data: dict) -> io.BytesIO:
    """
    Authentic EA FC 25 Gold Ultimate Team Card.
    - Smooth deep golden gradient and clean metallic 3D shield frame.
    - Giant centered player cutout with warm golden spotlight.
    - Left-hand column for OVR, position badge, and club logo.
    - Curved gold name ribbon.
    - 6 stats cleanly presented in a premium frosted dark glass plate.
    """
    ovr, position, player_name, team_name, pac, sho, pas, dri, def_stat, phy = _extract_card_data(player_data)

    img = Image.new("RGBA", (WIDTH, HEIGHT), (12, 10, 8, 255))
    draw = ImageDraw.Draw(img)

    # 1. Smooth Dark Bronze & Gold Vertical Gradient (NO harsh stripes!)
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        r = int(52 - 38 * ratio)
        g = int(40 - 30 * ratio)
        b = int(18 - 14 * ratio)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b, 255))

    # 2. FUT Shield Geometry (Beveled 3D Outline)
    top_cut = int(36 * SCALE)
    bot_y1 = int(HEIGHT * 0.82)
    bot_mid_x = int(44 * SCALE)
    bot_mid_y = int(HEIGHT * 0.92)

    shield_poly = [
        (int(14 * SCALE) + top_cut, int(14 * SCALE)),
        (WIDTH - int(14 * SCALE) - top_cut, int(14 * SCALE)),
        (WIDTH - int(14 * SCALE), int(14 * SCALE) + top_cut),
        (WIDTH - int(14 * SCALE), bot_y1),
        (WIDTH - int(14 * SCALE) - bot_mid_x, bot_mid_y),
        (WIDTH // 2, HEIGHT - int(14 * SCALE)),
        (int(14 * SCALE) + bot_mid_x, bot_mid_y),
        (int(14 * SCALE), bot_y1),
        (int(14 * SCALE), int(14 * SCALE) + top_cut),
    ]

    # Metallic Multi-Layer Border
    draw.polygon(shield_poly, outline=(245, 206, 112, 255), width=int(4.5 * SCALE))
    draw.polygon(shield_poly, outline=(180, 140, 50, 200), width=int(1.5 * SCALE))

    # 3. Dynamic Golden Halo Spotlight in center
    cx, cy = WIDTH // 2, int(HEIGHT * 0.26)
    spotlight = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(spotlight)
    for r in range(int(240 * SCALE), 0, -int(14 * SCALE)):
        alpha = int(95 * (1.0 - (r / (240 * SCALE)) ** 1.3))
        s_draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=(255, 215, 0, alpha))
    spotlight = spotlight.filter(ImageFilter.GaussianBlur(int(16 * SCALE)))
    img = Image.alpha_composite(img, spotlight)
    draw = ImageDraw.Draw(img)

    # 4. Giant Player Cutout (Centered & Dominant)
    photo_w = int(420 * SCALE)
    photo_h = int(390 * SCALE)
    player_img = _get_player_photo_image(player_name, team_name)

    if player_img:
        player_img.thumbnail((photo_w, photo_h), Image.Resampling.LANCZOS)
        pw, ph = player_img.size

        # Drop shadow
        shadow = Image.new("RGBA", (pw + int(24 * SCALE), ph + int(24 * SCALE)), (0, 0, 0, 0))
        s_mask = player_img.split()[3] if "A" in player_img.getbands() else Image.new("L", (pw, ph), 255)
        shadow.paste(Image.new("RGBA", (pw, ph), (0, 0, 0, 190)), (int(10 * SCALE), int(10 * SCALE)), s_mask)
        shadow = shadow.filter(ImageFilter.GaussianBlur(int(9 * SCALE)))

        px = (WIDTH - pw) // 2
        py = int(14 * SCALE) + (photo_h - ph)
        img.paste(shadow, (px - int(4 * SCALE), py - int(4 * SCALE)), shadow)

        # Base fade
        fade = Image.new("L", (pw, ph), 255)
        f_draw = ImageDraw.Draw(fade)
        f_start = int(ph * 0.70)
        for y in range(f_start, ph):
            val = int(255 * (1.0 - ((y - f_start) / (ph - f_start)) ** 1.6))
            f_draw.line([(0, y), (pw, y)], fill=val)
        if "A" in player_img.getbands():
            fade = ImageChops.multiply(player_img.split()[3], fade)

        img.paste(player_img, (px, py), fade)

    # 5. Frosted Glass Stat Backplate (Dark Carbon Glass)
    plate_w = int(396 * SCALE)
    plate_h = int(236 * SCALE)
    plate_x = (WIDTH - plate_w) // 2
    plate_y = int(342 * SCALE)

    draw.rounded_rectangle(
        [(plate_x, plate_y), (plate_x + plate_w, plate_y + plate_h)],
        radius=int(18 * SCALE),
        fill=(16, 14, 10, 245),
        outline=(245, 206, 112, 200),
        width=int(2 * SCALE)
    )

    # 6. Top-Left OVR, Position, and Club Crest
    col_x = int(58 * SCALE)
    ovr_y = int(46 * SCALE)

    font_ovr = load_card_font(52, bold=True)
    draw.text((col_x + int(2 * SCALE), ovr_y + int(2 * SCALE)), str(ovr), font=font_ovr, fill=(0, 0, 0, 180), anchor="mt")
    draw.text((col_x, ovr_y), str(ovr), font=font_ovr, fill=(255, 252, 240), anchor="mt")

    # Position Pill
    pos_y = ovr_y + int(56 * SCALE)
    pos_w = int(54 * SCALE)
    pos_h = int(26 * SCALE)
    draw.rounded_rectangle(
        [(col_x - pos_w // 2, pos_y), (col_x + pos_w // 2, pos_y + pos_h)],
        radius=int(6 * SCALE),
        fill=(24, 20, 12, 245),
        outline=(245, 206, 112, 200),
        width=int(1.5 * SCALE)
    )
    font_pos = load_card_font(20, bold=True)
    draw.text((col_x, pos_y + int(3 * SCALE)), position, font=font_pos, fill=(245, 206, 112), anchor="mt")

    # Club Crest
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
                ly = pos_y + int(36 * SCALE)
                img.paste(logo_img, (lx, ly), logo_img)
            except Exception:
                pass

    # 7. Player Name Ribbon
    ribbon_w = int(368 * SCALE)
    ribbon_h = int(44 * SCALE)
    rx = (WIDTH - ribbon_w) // 2
    ry = int(352 * SCALE)

    draw.rounded_rectangle(
        [(rx, ry), (rx + ribbon_w, ry + ribbon_h)],
        radius=int(10 * SCALE),
        fill=(28, 22, 12, 245),
        outline=(245, 206, 112, 220),
        width=int(1.5 * SCALE)
    )

    name_size = 25 if len(player_name) <= 13 else (21 if len(player_name) <= 18 else 17)
    font_name = load_card_font(name_size, bold=True)
    draw.text((WIDTH // 2, ry + int(8 * SCALE)), player_name, font=font_name, fill=(255, 252, 240), anchor="mt")

    # 8. Classic 2x3 FUT Stats Grid with Subtle Stat Cells
    grid_y = ry + ribbon_h + int(14 * SCALE)
    row_h = int(40 * SCALE)
    sep_x = WIDTH // 2

    # Vertical gold separator
    draw.line([(sep_x, grid_y - int(2 * SCALE)), (sep_x, grid_y + int(116 * SCALE))], fill=(245, 206, 112, 140), width=int(1.5 * SCALE))

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
        # Left stat
        draw.text((c1_v, cur_y), f"{lv:>2}", font=font_s_val, fill=(255, 255, 255), anchor="lt")
        draw.text((c1_l, cur_y + int(4 * SCALE)), ll, font=font_s_lbl, fill=(230, 190, 100), anchor="lt")

        # Right stat
        draw.text((c2_v, cur_y), f"{rv:>2}", font=font_s_val, fill=(255, 255, 255), anchor="lt")
        draw.text((c2_l, cur_y + int(4 * SCALE)), rl, font=font_s_lbl, fill=(230, 190, 100), anchor="lt")

    # 9. Bottom Tournament Badge
    foot_w = int(280 * SCALE)
    foot_h = int(24 * SCALE)
    foot_x = (WIDTH - foot_w) // 2
    foot_y = HEIGHT - int(82 * SCALE)

    draw.rounded_rectangle(
        [(foot_x, foot_y), (foot_x + foot_w, foot_y + foot_h)],
        radius=int(6 * SCALE),
        fill=(22, 18, 10, 245),
        outline=(245, 206, 112, 180),
        width=int(1.5 * SCALE)
    )
    font_foot = load_card_font(11, bold=True)
    draw.text((WIDTH // 2, foot_y + int(4 * SCALE)), "★ ULTIMATE TEAM • КПЛ 2026 ★", font=font_foot, fill=(245, 206, 112), anchor="mt")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


# ═════════════════════════════════════════════════════════════════════════════
# 🎨 3. OBSIDIAN LUXURY VIP DESIGN (Minimalist Luxury)
# ═════════════════════════════════════════════════════════════════════════════

def render_design_3_luxury_poster(player_data: dict) -> io.BytesIO:
    """Minimalist Obsidian & Fine Gold Luxury Card with 3x2 stat capsules."""
    ovr, position, player_name, team_name, pac, sho, pas, dri, def_stat, phy = _extract_card_data(player_data)

    img = Image.new("RGBA", (WIDTH, HEIGHT), (8, 8, 10, 255))
    draw = ImageDraw.Draw(img)

    for y in range(HEIGHT):
        ratio = y / HEIGHT
        val = int(16 - 8 * ratio)
        draw.line([(0, y), (WIDTH, y)], fill=(val, val, int(val * 1.2), 255))

    # Double Gold Hairline Border
    card_inset_1 = int(14 * SCALE)
    card_inset_2 = int(20 * SCALE)
    corner_r = int(26 * SCALE)

    draw.rounded_rectangle(
        [(card_inset_1, card_inset_1), (WIDTH - card_inset_1, HEIGHT - card_inset_1)],
        radius=corner_r,
        outline=(212, 175, 55, 255),
        width=int(2.5 * SCALE)
    )
    draw.rounded_rectangle(
        [(card_inset_2, card_inset_2), (WIDTH - card_inset_2, HEIGHT - card_inset_2)],
        radius=corner_r - int(4 * SCALE),
        outline=(140, 115, 45, 140),
        width=int(1 * SCALE)
    )

    # Top Header
    header_y = int(32 * SCALE)
    logo_fn = get_team_logo_filename(team_name)
    if logo_fn:
        logo_path = os.path.join(LOGOS_DIR, logo_fn)
        if os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path)
                logo_img = clean_and_prepare_logo(logo_img)
                l_size = int(36 * SCALE)
                logo_img.thumbnail((l_size, l_size), Image.Resampling.LANCZOS)
                img.paste(logo_img, (int(36 * SCALE), header_y), logo_img)
            except Exception:
                pass

    font_hdr = load_card_font(12, bold=True)
    draw.text((WIDTH // 2, header_y + int(10 * SCALE)), "КПЛ • LUXURY EDITION 2026", font=font_hdr, fill=(212, 175, 55), anchor="mt")

    # Golden Halo Glow
    cx, cy = WIDTH // 2, int(HEIGHT * 0.30)
    spotlight = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(spotlight)
    for r in range(int(220 * SCALE), 0, -int(12 * SCALE)):
        alpha = int(75 * (1.0 - (r / (220 * SCALE)) ** 1.5))
        s_draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=(212, 175, 55, alpha))
    spotlight = spotlight.filter(ImageFilter.GaussianBlur(int(14 * SCALE)))
    img = Image.alpha_composite(img, spotlight)
    draw = ImageDraw.Draw(img)

    # Player Cutout
    photo_w = int(400 * SCALE)
    photo_h = int(360 * SCALE)
    player_img = _get_player_photo_image(player_name, team_name)

    if player_img:
        player_img.thumbnail((photo_w, photo_h), Image.Resampling.LANCZOS)
        pw, ph = player_img.size

        shadow = Image.new("RGBA", (pw + int(24 * SCALE), ph + int(24 * SCALE)), (0, 0, 0, 0))
        s_mask = player_img.split()[3] if "A" in player_img.getbands() else Image.new("L", (pw, ph), 255)
        shadow.paste(Image.new("RGBA", (pw, ph), (0, 0, 0, 190)), (int(10 * SCALE), int(10 * SCALE)), s_mask)
        shadow = shadow.filter(ImageFilter.GaussianBlur(int(8 * SCALE)))

        px = (WIDTH - pw) // 2
        py = int(45 * SCALE) + (photo_h - ph)
        img.paste(shadow, (px - int(4 * SCALE), py - int(4 * SCALE)), shadow)

        fade = Image.new("L", (pw, ph), 255)
        f_draw = ImageDraw.Draw(fade)
        f_start = int(ph * 0.68)
        for y in range(f_start, ph):
            val = int(255 * (1.0 - ((y - f_start) / (ph - f_start)) ** 1.6))
            f_draw.line([(0, y), (pw, y)], fill=val)
        if "A" in player_img.getbands():
            fade = ImageChops.multiply(player_img.split()[3], fade)

        img.paste(player_img, (px, py), fade)

    # Player Name
    name_y = int(380 * SCALE)
    font_name = load_card_font(26, bold=True)
    draw.text((WIDTH // 2, name_y), player_name, font=font_name, fill=(255, 255, 255), anchor="mt")

    # Emblem Badge: [ 95 • CAM ]
    badge_w = int(140 * SCALE)
    badge_h = int(32 * SCALE)
    bx1 = (WIDTH - badge_w) // 2
    by1 = name_y + int(36 * SCALE)

    draw.rounded_rectangle(
        [(bx1, by1), (bx1 + badge_w, by1 + badge_h)],
        radius=int(16 * SCALE),
        fill=(20, 18, 14, 245),
        outline=(212, 175, 55, 220),
        width=int(1.5 * SCALE)
    )

    font_badge = load_card_font(18, bold=True)
    draw.text((WIDTH // 2, by1 + int(6 * SCALE)), f"{ovr}  •  {position}", font=font_badge, fill=(212, 175, 55), anchor="mt")

    # 3x2 Stat Grid
    grid_y = by1 + badge_h + int(20 * SCALE)
    stat_w = int(120 * SCALE)
    stat_h = int(58 * SCALE)
    gap_x = int(12 * SCALE)
    gap_y = int(10 * SCALE)

    total_w = 3 * stat_w + 2 * gap_x
    start_x = (WIDTH - total_w) // 2

    stats_3x2 = [
        ("PAC", pac), ("SHO", sho), ("PAS", pas),
        ("DRI", dri), ("DEF", def_stat), ("PHY", phy),
    ]

    font_st_val = load_card_font(22, bold=True)
    font_st_lbl = load_card_font(12, bold=True)

    for i, (lbl, val) in enumerate(stats_3x2):
        row = i // 3
        col = i % 3

        sx = start_x + col * (stat_w + gap_x)
        sy = grid_y + row * (stat_h + gap_y)

        draw.rounded_rectangle(
            [(sx, sy), (sx + stat_w, sy + stat_h)],
            radius=int(10 * SCALE),
            fill=(16, 15, 18, 245),
            outline=(212, 175, 55, 120),
            width=int(1 * SCALE)
        )

        draw.text((sx + stat_w // 2, sy + int(8 * SCALE)), str(val), font=font_st_val, fill=(255, 255, 255), anchor="mt")
        draw.text((sx + stat_w // 2, sy + int(34 * SCALE)), lbl, font=font_st_lbl, fill=(212, 175, 55), anchor="mt")

    # Bottom Serial
    foot_y = HEIGHT - int(38 * SCALE)
    font_foot = load_card_font(10, bold=False)
    draw.text((WIDTH // 2, foot_y), f"AUTHENTIC COLLECTOR CARD • NO. {ovr * 107 % 999:03d}", font=font_foot, fill=(140, 115, 45), anchor="mt")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


def generate_ea_fc_card(player_data: dict, theme_name: str = "design_2") -> io.BytesIO:
    """Main card generation router."""
    mode = str(theme_name).lower()
    if mode in ["design_1", "cyber", "broadcast"]:
        return render_design_1_cyber(player_data)
    elif mode in ["design_3", "luxury", "poster", "minimal"]:
        return render_design_3_luxury_poster(player_data)
    else:
        return render_design_2_fut_shield(player_data)
