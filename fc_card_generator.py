"""
fc_card_generator.py

Multi-Design EA FC / Esports Card Generator with 3 fundamentally distinct design concepts:

1. DESIGN_1: "CYBER HYBRID / MODERN BROADCAST"
   - Neon/Graphite tech aesthetic, angular geometric cuts, glowing cyberpunk gradient accents.
   - Large vertical OVR pillar badge, angled name banner, stat cards with mini meter bars.

2. DESIGN_2: "AUTHENTIC EA FC 25 FUT SHIELD"
   - Official FIFA Ultimate Team shield shape, metallic gold 3D bevels, radial halo spotlight.
   - Left-column OVR & Position pill, centered player cutout, gold ribbon, classic 2x3 FIFA attribute grid.

3. DESIGN_3: "OBSIDIAN LUXURY VIP / EDITORIAL POSTER"
   - Luxury minimalist watch/poster aesthetic, deep onyx black with double gold hairline borders.
   - Centered luxury header with club crest, diamond OVR+POS emblem, clean 3x2 frosted stat capsules.
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

# ─────────────────────────────────────────────────────────────────────────────
# Font Helper
# ─────────────────────────────────────────────────────────────────────────────

def load_card_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load high quality system font with size scaling."""
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
    """Extract and normalize all player variables."""
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
    """Load player photo if cached/available."""
    try:
        photo_path = player_photos.get_player_photo(player_name, team_name)
        if photo_path and os.path.exists(photo_path):
            return Image.open(photo_path).convert("RGBA")
    except Exception as e:
        logger.warning(f"Error loading player photo for {player_name}: {e}")
    return None


# ═════════════════════════════════════════════════════════════════════════════
# 🎨 DESIGN 1: "CYBER HYBRID / MODERN BROADCAST"
# ═════════════════════════════════════════════════════════════════════════════

def render_design_1_cyber(player_data: dict) -> io.BytesIO:
    """
    Design 1: High-tech Cyberpunk Esports Broadcast Card.
    - Dark graphite background with dynamic angled tech lines and glowing neon cyan/gold accents.
    - Left tech-pillar displaying OVR & Position in a futuristic HUD frame.
    - Centered player with glowing backdrop aura.
    - Angled parallelogram name plate with neon glow.
    - 6 modern stat capsules with mini percentage power-meters.
    """
    ovr, position, player_name, team_name, pac, sho, pas, dri, def_stat, phy = _extract_card_data(player_data)

    W = 460 * SCALE
    H = 690 * SCALE

    # 1. Base Canvas (Dark Solid Obsidian)
    img = Image.new("RGBA", (W, H), (11, 12, 16, 255))
    draw = ImageDraw.Draw(img)

    # 2. Gradient Background
    for y in range(H):
        ratio = y / H
        r = int(18 - 8 * ratio)
        g = int(20 - 10 * ratio)
        b = int(28 - 14 * ratio)
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

    # 3. Cyber Grid Lines & Diagonal Slashes
    for x in range(0, W, int(30 * SCALE)):
        draw.line([(x, 0), (x, H)], fill=(0, 240, 255, 6), width=1)
    for diag in range(-H, W + H, int(45 * SCALE)):
        draw.line([(diag, 0), (diag + H, H)], fill=(255, 190, 0, 10), width=int(1.5 * SCALE))

    # 4. Outer Cyber Frame with Angled Chamfers
    outer_poly = [
        (int(15 * SCALE), int(35 * SCALE)),
        (int(35 * SCALE), int(15 * SCALE)),
        (W - int(35 * SCALE), int(15 * SCALE)),
        (W - int(15 * SCALE), int(35 * SCALE)),
        (W - int(15 * SCALE), H - int(35 * SCALE)),
        (W - int(35 * SCALE), H - int(15 * SCALE)),
        (int(35 * SCALE), H - int(15 * SCALE)),
        (int(15 * SCALE), H - int(35 * SCALE)),
    ]
    draw.polygon(outer_poly, outline=(0, 225, 255, 180), width=int(2.5 * SCALE))

    # Corner Neon Accent Brackets
    c_len = int(22 * SCALE)
    c_col = (255, 200, 0, 255)
    draw.line([(int(35 * SCALE), int(15 * SCALE)), (int(35 * SCALE) + c_len, int(15 * SCALE))], fill=c_col, width=int(3 * SCALE))
    draw.line([(W - int(35 * SCALE) - c_len, int(15 * SCALE)), (W - int(35 * SCALE), int(15 * SCALE))], fill=c_col, width=int(3 * SCALE))
    draw.line([(int(35 * SCALE), H - int(15 * SCALE)), (int(35 * SCALE) + c_len, H - int(15 * SCALE))], fill=c_col, width=int(3 * SCALE))
    draw.line([(W - int(35 * SCALE) - c_len, H - int(15 * SCALE)), (W - int(35 * SCALE), H - int(15 * SCALE))], fill=c_col, width=int(3 * SCALE))

    # 5. Top Header Banner: Club Logo & Esports Badge
    logo_fn = get_team_logo_filename(team_name)
    if logo_fn:
        logo_path = os.path.join(LOGOS_DIR, logo_fn)
        if os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path)
                logo_img = clean_and_prepare_logo(logo_img)
                l_size = int(38 * SCALE)
                logo_img.thumbnail((l_size, l_size), Image.Resampling.LANCZOS)
                img.paste(logo_img, (W - int(55 * SCALE), int(26 * SCALE)), logo_img)
            except Exception:
                pass

    # Top League Tag
    font_tag = load_card_font(11, bold=True)
    draw.text((W - int(65 * SCALE), int(34 * SCALE)), "КПЛ PRO 2026", font=font_tag, fill=(0, 240, 255, 220), anchor="rt")

    # 6. Radial Neon Spotlight behind player
    cx, cy = W // 2, int(H * 0.28)
    spotlight = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(spotlight)
    for r in range(int(220 * SCALE), 0, -int(12 * SCALE)):
        alpha = int(70 * (1.0 - (r / (220 * SCALE)) ** 1.3))
        s_draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=(0, 180, 255, alpha))
    spotlight = spotlight.filter(ImageFilter.GaussianBlur(int(14 * SCALE)))
    img = Image.alpha_composite(img, spotlight)
    draw = ImageDraw.Draw(img)

    # 7. Player Cutout (Centered & Large)
    photo_w = int(360 * SCALE)
    photo_h = int(340 * SCALE)
    player_img = _get_player_photo_image(player_name, team_name)

    if player_img:
        player_img.thumbnail((photo_w, photo_h), Image.Resampling.LANCZOS)
        pw, ph = player_img.size

        # Shadow
        shadow = Image.new("RGBA", (pw + int(20 * SCALE), ph + int(20 * SCALE)), (0, 0, 0, 0))
        s_mask = player_img.split()[3] if "A" in player_img.getbands() else Image.new("L", (pw, ph), 255)
        shadow.paste(Image.new("RGBA", (pw, ph), (0, 0, 0, 180)), (int(8 * SCALE), int(8 * SCALE)), s_mask)
        shadow = shadow.filter(ImageFilter.GaussianBlur(int(7 * SCALE)))

        px = (W - pw) // 2
        py = int(25 * SCALE) + (photo_h - ph)
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
    else:
        # Silhouette placeholder
        sc_x = W // 2
        head_r = int(40 * SCALE)
        draw.ellipse([(sc_x - head_r, int(40 * SCALE)), (sc_x + head_r, int(40 * SCALE) + 2 * head_r)], fill=(0, 200, 255, 60))

    # 8. Left Tech-Pillar (OVR & Position HUD)
    pillar_w = int(72 * SCALE)
    pillar_h = int(120 * SCALE)
    pil_x = int(28 * SCALE)
    pil_y = int(45 * SCALE)

    # Glowing Pillar Plate
    pil_poly = [
        (pil_x, pil_y),
        (pil_x + pillar_w, pil_y),
        (pil_x + pillar_w, pil_y + pillar_h - int(15 * SCALE)),
        (pil_x + pillar_w - int(15 * SCALE), pil_y + pillar_h),
        (pil_x, pil_y + pillar_h),
    ]
    draw.polygon(pil_poly, fill=(15, 18, 25, 235), outline=(0, 240, 255, 220), width=int(2 * SCALE))

    font_ovr = load_card_font(46, bold=True)
    draw.text((pil_x + pillar_w // 2, pil_y + int(12 * SCALE)), str(ovr), font=font_ovr, fill=(255, 255, 255), anchor="mt")

    # Divider
    draw.line([(pil_x + int(10 * SCALE), pil_y + int(64 * SCALE)), (pil_x + pillar_w - int(10 * SCALE), pil_y + int(64 * SCALE))], fill=(0, 240, 255, 160), width=int(1.5 * SCALE))

    font_pos = load_card_font(18, bold=True)
    draw.text((pil_x + pillar_w // 2, pil_y + int(76 * SCALE)), position, font=font_pos, fill=(255, 205, 35), anchor="mt")

    # 9. Angled Cyber Name Plate
    name_w = int(380 * SCALE)
    name_h = int(46 * SCALE)
    nx1 = (W - name_w) // 2
    ny1 = int(360 * SCALE)

    name_poly = [
        (nx1 + int(20 * SCALE), ny1),
        (nx1 + name_w, ny1),
        (nx1 + name_w - int(20 * SCALE), ny1 + name_h),
        (nx1, ny1 + name_h),
    ]
    draw.polygon(name_poly, fill=(16, 20, 28, 245), outline=(255, 205, 35, 220), width=int(2 * SCALE))

    font_name = load_card_font(23, bold=True)
    draw.text((W // 2, ny1 + int(10 * SCALE)), player_name, font=font_name, fill=(255, 255, 255), anchor="mt")

    # 10. Cyber Stat HUD (6 Horizontal Capsules with glowing meters)
    grid_y = ny1 + name_h + int(18 * SCALE)
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
    c2_x = W - int(36 * SCALE) - col_w

    for idx, (lbl, val) in enumerate(stats_list):
        row = idx // 2
        col = idx % 2
        bx = c1_x if col == 0 else c2_x
        by = grid_y + row * (row_h + int(10 * SCALE))

        # Stat Capsule Backing
        draw.rounded_rectangle(
            [(bx, by), (bx + col_w, by + row_h)],
            radius=int(8 * SCALE),
            fill=(14, 18, 26, 235),
            outline=(0, 220, 255, 120),
            width=int(1.5 * SCALE)
        )

        # Mini Progress Meter Bar at bottom of capsule
        bar_pad = int(6 * SCALE)
        bar_w = col_w - 2 * bar_pad
        bar_h = int(4 * SCALE)
        bar_y = by + row_h - bar_pad - bar_h

        # Background track
        draw.rounded_rectangle([(bx + bar_pad, bar_y), (bx + bar_pad + bar_w, bar_y + bar_h)], radius=int(2 * SCALE), fill=(30, 36, 48))
        # Filled meter
        fill_w = int(bar_w * (val / 99.0))
        draw.rounded_rectangle([(bx + bar_pad, bar_y), (bx + bar_pad + fill_w, bar_y + bar_h)], radius=int(2 * SCALE), fill=(255, 205, 35))

        # Text
        draw.text((bx + int(12 * SCALE), by + int(6 * SCALE)), lbl, font=font_s_lbl, fill=(0, 240, 255), anchor="lt")
        draw.text((bx + col_w - int(12 * SCALE), by + int(4 * SCALE)), str(val), font=font_s_num, fill=(255, 255, 255), anchor="rt")

    # 11. Bottom Cyber Footer Badge
    foot_y = H - int(48 * SCALE)
    font_foot = load_card_font(10, bold=True)
    draw.text((W // 2, foot_y), "LOGOVOBOT CYBER SERIES • 2026", font=font_foot, fill=(0, 220, 255, 180), anchor="mt")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


# ═════════════════════════════════════════════════════════════════════════════
# 🎨 DESIGN 2: "AUTHENTIC EA FC 25 FUT SHIELD"
# ═════════════════════════════════════════════════════════════════════════════

def render_design_2_fut_shield(player_data: dict) -> io.BytesIO:
    """
    Design 2: Authentic EA FC 25 Ultimate Team Shield.
    - Classic FUT Shield contour with metallic gold beveled 3D frame.
    - Left-hand OVR & position pill, club crest directly underneath.
    - Centered player with warm golden halo lighting.
    - Curving gold-trimmed player name plate.
    - Classic 2x3 FIFA stats matrix with center gold separator bar.
    """
    ovr, position, player_name, team_name, pac, sho, pas, dri, def_stat, phy = _extract_card_data(player_data)

    W = 460 * SCALE
    H = 690 * SCALE

    # 1. Base Canvas (Solid Dark Bronze-Gold)
    img = Image.new("RGBA", (W, H), (12, 10, 6, 255))
    draw = ImageDraw.Draw(img)

    # 2. Gradient Background
    for y in range(H):
        ratio = y / H
        r = int(48 - 36 * ratio)
        g = int(38 - 28 * ratio)
        b = int(16 - 12 * ratio)
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

    # 3. Gold Carbon Weave Pattern
    for diag in range(-H, W + H, int(8 * SCALE)):
        draw.line([(diag, 0), (diag + H, H)], fill=(255, 215, 0, 10), width=int(1.5 * SCALE))

    # 4. FUT Shield Mask & Borders
    top_cut = int(32 * SCALE)
    bot_y1 = int(H * 0.82)
    bot_mid_x = int(44 * SCALE)
    bot_mid_y = int(H * 0.92)

    shield_poly = [
        (int(12 * SCALE) + top_cut, int(12 * SCALE)),
        (W - int(12 * SCALE) - top_cut, int(12 * SCALE)),
        (W - int(12 * SCALE), int(12 * SCALE) + top_cut),
        (W - int(12 * SCALE), bot_y1),
        (W - int(12 * SCALE) - bot_mid_x, bot_mid_y),
        (W // 2, H - int(14 * SCALE)),
        (int(12 * SCALE) + bot_mid_x, bot_mid_y),
        (int(12 * SCALE), bot_y1),
        (int(12 * SCALE), int(12 * SCALE) + top_cut),
    ]

    # Metallic Multi-Layer Border
    draw.polygon(shield_poly, outline=(245, 206, 112, 255), width=int(4 * SCALE))
    draw.polygon(shield_poly, outline=(180, 140, 50, 200), width=int(1.5 * SCALE))

    # 5. Golden Halo Spotlight
    cx, cy = W // 2, int(H * 0.26)
    spotlight = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(spotlight)
    for r in range(int(230 * SCALE), 0, -int(12 * SCALE)):
        alpha = int(85 * (1.0 - (r / (230 * SCALE)) ** 1.4))
        s_draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=(255, 215, 0, alpha))
    spotlight = spotlight.filter(ImageFilter.GaussianBlur(int(14 * SCALE)))
    img = Image.alpha_composite(img, spotlight)
    draw = ImageDraw.Draw(img)

    # 6. Centered Player Cutout
    photo_w = int(370 * SCALE)
    photo_h = int(350 * SCALE)
    player_img = _get_player_photo_image(player_name, team_name)

    if player_img:
        player_img.thumbnail((photo_w, photo_h), Image.Resampling.LANCZOS)
        pw, ph = player_img.size

        # Drop shadow
        shadow = Image.new("RGBA", (pw + int(24 * SCALE), ph + int(24 * SCALE)), (0, 0, 0, 0))
        s_mask = player_img.split()[3] if "A" in player_img.getbands() else Image.new("L", (pw, ph), 255)
        shadow.paste(Image.new("RGBA", (pw, ph), (0, 0, 0, 180)), (int(10 * SCALE), int(10 * SCALE)), s_mask)
        shadow = shadow.filter(ImageFilter.GaussianBlur(int(8 * SCALE)))

        px = (W - pw) // 2
        py = int(18 * SCALE) + (photo_h - ph)
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

    # 7. Frosted Glass Stat Backplate
    plate_w = int(390 * SCALE)
    plate_h = int(230 * SCALE)
    plate_x = (W - plate_w) // 2
    plate_y = int(345 * SCALE)

    draw.rounded_rectangle(
        [(plate_x, plate_y), (plate_x + plate_w, plate_y + plate_h)],
        radius=int(18 * SCALE),
        fill=(18, 14, 8, 245),
        outline=(245, 206, 112, 180),
        width=int(1.5 * SCALE)
    )

    # 8. Top-Left OVR, Position, and Club Crest
    col_x = int(60 * SCALE)
    ovr_y = int(50 * SCALE)

    font_ovr = load_card_font(52, bold=True)
    draw.text((col_x + int(2 * SCALE), ovr_y + int(2 * SCALE)), str(ovr), font=font_ovr, fill=(0, 0, 0, 180), anchor="mt")
    draw.text((col_x, ovr_y), str(ovr), font=font_ovr, fill=(255, 252, 240), anchor="mt")

    # Position Pill
    pos_y = ovr_y + int(56 * SCALE)
    pos_w = int(52 * SCALE)
    pos_h = int(26 * SCALE)
    draw.rounded_rectangle(
        [(col_x - pos_w // 2, pos_y), (col_x + pos_w // 2, pos_y + pos_h)],
        radius=int(6 * SCALE),
        fill=(22, 18, 10, 245),
        outline=(245, 206, 112, 200),
        width=int(1.5 * SCALE)
    )
    font_pos = load_card_font(21, bold=True)
    draw.text((col_x, pos_y + int(3 * SCALE)), position, font=font_pos, fill=(245, 206, 112), anchor="mt")

    # Club Crest
    logo_fn = get_team_logo_filename(team_name)
    if logo_fn:
        logo_path = os.path.join(LOGOS_DIR, logo_fn)
        if os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path)
                logo_img = clean_and_prepare_logo(logo_img)
                l_size = int(50 * SCALE)
                logo_img.thumbnail((l_size, l_size), Image.Resampling.LANCZOS)
                lx = col_x - (logo_img.width // 2)
                ly = pos_y + int(36 * SCALE)
                img.paste(logo_img, (lx, ly), logo_img)
            except Exception:
                pass

    # 9. Player Name Ribbon
    ribbon_w = int(360 * SCALE)
    ribbon_h = int(42 * SCALE)
    rx = (W - ribbon_w) // 2
    ry = int(355 * SCALE)

    draw.rounded_rectangle(
        [(rx, ry), (rx + ribbon_w, ry + ribbon_h)],
        radius=int(10 * SCALE),
        fill=(32, 24, 10, 245),
        outline=(245, 206, 112, 200),
        width=int(1.5 * SCALE)
    )

    name_size = 26 if len(player_name) <= 13 else (21 if len(player_name) <= 18 else 17)
    font_name = load_card_font(name_size, bold=True)
    draw.text((W // 2, ry + int(7 * SCALE)), player_name, font=font_name, fill=(255, 252, 240), anchor="mt")

    # 10. Classic 2x3 FUT Stats Grid
    grid_y = ry + ribbon_h + int(16 * SCALE)
    row_h = int(38 * SCALE)
    sep_x = W // 2

    # Vertical gold separator
    draw.line([(sep_x, grid_y - int(4 * SCALE)), (sep_x, grid_y + int(112 * SCALE))], fill=(245, 206, 112, 140), width=int(1.5 * SCALE))

    font_s_val = load_card_font(25, bold=True)
    font_s_lbl = load_card_font(18, bold=True)

    stats_pairs = [
        (pac, "PAC", dri, "DRI"),
        (sho, "SHO", def_stat, "DEF"),
        (pas, "PAS", phy, "PHY"),
    ]

    c1_v = plate_x + int(42 * SCALE)
    c1_l = plate_x + int(88 * SCALE)
    c2_v = plate_x + int(218 * SCALE)
    c2_l = plate_x + int(264 * SCALE)

    for idx, (lv, ll, rv, rl) in enumerate(stats_pairs):
        cur_y = grid_y + idx * row_h
        draw.text((c1_v, cur_y), f"{lv:>2}", font=font_s_val, fill=(255, 255, 255), anchor="lt")
        draw.text((c1_l, cur_y + int(4 * SCALE)), ll, font=font_s_lbl, fill=(230, 190, 100), anchor="lt")

        draw.text((c2_v, cur_y), f"{rv:>2}", font=font_s_val, fill=(255, 255, 255), anchor="lt")
        draw.text((c2_l, cur_y + int(4 * SCALE)), rl, font=font_s_lbl, fill=(230, 190, 100), anchor="lt")

    # 11. Bottom Badge
    foot_w = int(280 * SCALE)
    foot_h = int(24 * SCALE)
    foot_x = (W - foot_w) // 2
    foot_y = H - int(82 * SCALE)

    draw.rounded_rectangle(
        [(foot_x, foot_y), (foot_x + foot_w, foot_y + foot_h)],
        radius=int(6 * SCALE),
        fill=(22, 18, 10, 245),
        outline=(245, 206, 112, 180),
        width=int(1.5 * SCALE)
    )
    font_foot = load_card_font(11, bold=True)
    draw.text((W // 2, foot_y + int(4 * SCALE)), "★ ULTIMATE TEAM • КПЛ 2026 ★", font=font_foot, fill=(245, 206, 112), anchor="mt")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


# ═════════════════════════════════════════════════════════════════════════════
# 🎨 DESIGN 3: "OBSIDIAN LUXURY VIP / EDITORIAL POSTER"
# ═════════════════════════════════════════════════════════════════════════════

def render_design_3_luxury_poster(player_data: dict) -> io.BytesIO:
    """
    Design 3: Minimalist Obsidian & Fine Gold Luxury Card.
    - Clean rectangular format with smooth rounded corners (32px radius) and double gold hairline border.
    - Centered top header with club logo, golden tournament badge, and season year.
    - Centered large player cutout with subtle golden backlight halo.
    - Centered diamond/hexagon OVR badge: [ 95 • CAM ].
    - 6 modern stat capsules arranged in a clean 3x2 horizontal grid with frosted dark glass.
    """
    ovr, position, player_name, team_name, pac, sho, pas, dri, def_stat, phy = _extract_card_data(player_data)

    W = 460 * SCALE
    H = 690 * SCALE

    # 1. Base Canvas (Pure Deep Obsidian Black)
    img = Image.new("RGBA", (W, H), (8, 8, 10, 255))
    draw = ImageDraw.Draw(img)

    # 2. Subtle Luxury Vertical Gradient
    for y in range(H):
        ratio = y / H
        val = int(16 - 8 * ratio)
        draw.line([(0, y), (W, y)], fill=(val, val, int(val * 1.2), 255))

    # 3. Double Gold Hairline Border (Rounded Rectangle)
    card_inset_1 = int(14 * SCALE)
    card_inset_2 = int(20 * SCALE)
    corner_r = int(26 * SCALE)

    draw.rounded_rectangle(
        [(card_inset_1, card_inset_1), (W - card_inset_1, H - card_inset_1)],
        radius=corner_r,
        outline=(212, 175, 55, 255),
        width=int(2.5 * SCALE)
    )
    draw.rounded_rectangle(
        [(card_inset_2, card_inset_2), (W - card_inset_2, H - card_inset_2)],
        radius=corner_r - int(4 * SCALE),
        outline=(140, 115, 45, 140),
        width=int(1 * SCALE)
    )

    # 4. Top Header: Club Logo & Luxury Tournament Badge
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
                lx = int(36 * SCALE)
                ly = header_y
                img.paste(logo_img, (lx, ly), logo_img)
            except Exception:
                pass

    font_hdr = load_card_font(12, bold=True)
    draw.text((W // 2, header_y + int(10 * SCALE)), "КПЛ • LUXURY EDITION 2026", font=font_hdr, fill=(212, 175, 55), anchor="mt")

    # 5. Golden Halo Glow
    cx, cy = W // 2, int(H * 0.30)
    spotlight = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(spotlight)
    for r in range(int(210 * SCALE), 0, -int(10 * SCALE)):
        alpha = int(70 * (1.0 - (r / (210 * SCALE)) ** 1.5))
        s_draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=(212, 175, 55, alpha))
    spotlight = spotlight.filter(ImageFilter.GaussianBlur(int(14 * SCALE)))
    img = Image.alpha_composite(img, spotlight)
    draw = ImageDraw.Draw(img)

    # 6. Centered Large Player Cutout
    photo_w = int(360 * SCALE)
    photo_h = int(330 * SCALE)
    player_img = _get_player_photo_image(player_name, team_name)

    if player_img:
        player_img.thumbnail((photo_w, photo_h), Image.Resampling.LANCZOS)
        pw, ph = player_img.size

        # Shadow
        shadow = Image.new("RGBA", (pw + int(20 * SCALE), ph + int(20 * SCALE)), (0, 0, 0, 0))
        s_mask = player_img.split()[3] if "A" in player_img.getbands() else Image.new("L", (pw, ph), 255)
        shadow.paste(Image.new("RGBA", (pw, ph), (0, 0, 0, 180)), (int(8 * SCALE), int(8 * SCALE)), s_mask)
        shadow = shadow.filter(ImageFilter.GaussianBlur(int(7 * SCALE)))

        px = (W - pw) // 2
        py = int(45 * SCALE) + (photo_h - ph)
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

    # 7. Player Name (Clean, Elegant, Bold)
    name_y = int(380 * SCALE)
    font_name = load_card_font(26, bold=True)
    draw.text((W // 2, name_y), player_name, font=font_name, fill=(255, 255, 255), anchor="mt")

    # 8. Central Luxury Emblem Badge: [ 95 • CAM ]
    badge_w = int(140 * SCALE)
    badge_h = int(32 * SCALE)
    bx1 = (W - badge_w) // 2
    by1 = name_y + int(36 * SCALE)

    draw.rounded_rectangle(
        [(bx1, by1), (bx1 + badge_w, by1 + badge_h)],
        radius=int(16 * SCALE),
        fill=(20, 18, 14, 245),
        outline=(212, 175, 55, 220),
        width=int(1.5 * SCALE)
    )

    font_badge = load_card_font(18, bold=True)
    draw.text((W // 2, by1 + int(6 * SCALE)), f"{ovr}  •  {position}", font=font_badge, fill=(212, 175, 55), anchor="mt")

    # 9. Clean 3x2 Modern Horizontal Grid (6 Stat Pills)
    grid_y = by1 + badge_h + int(20 * SCALE)
    stat_w = int(120 * SCALE)
    stat_h = int(58 * SCALE)
    gap_x = int(12 * SCALE)
    gap_y = int(10 * SCALE)

    total_w = 3 * stat_w + 2 * gap_x
    start_x = (W - total_w) // 2

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

        # Stat Box
        draw.rounded_rectangle(
            [(sx, sy), (sx + stat_w, sy + stat_h)],
            radius=int(10 * SCALE),
            fill=(16, 15, 18, 245),
            outline=(212, 175, 55, 120),
            width=int(1 * SCALE)
        )

        # Value on Top
        draw.text((sx + stat_w // 2, sy + int(8 * SCALE)), str(val), font=font_st_val, fill=(255, 255, 255), anchor="mt")
        # Label below
        draw.text((sx + stat_w // 2, sy + int(34 * SCALE)), lbl, font=font_st_lbl, fill=(212, 175, 55), anchor="mt")

    # 10. Bottom Luxury Serial / Stamp
    foot_y = H - int(38 * SCALE)
    font_foot = load_card_font(10, bold=False)
    draw.text((W // 2, foot_y), f"AUTHENTIC COLLECTOR CARD • NO. {ovr * 107 % 999:03d}", font=font_foot, fill=(140, 115, 45), anchor="mt")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher: generate_ea_fc_card
# ─────────────────────────────────────────────────────────────────────────────

def generate_ea_fc_card(player_data: dict, theme_name: str = "design_2") -> io.BytesIO:
    """
    Generate player card with selectable design concept:
    - 'design_1' / 'cyber': Cyber Hybrid / Modern Broadcast
    - 'design_2' / 'fut_shield' / 'gold_rare' / 'totw' / 'icon': Authentic EA FC 25 FUT Shield
    - 'design_3' / 'luxury': Obsidian Luxury VIP / Editorial Poster
    """
    mode = str(theme_name).lower()
    if mode in ["design_1", "cyber", "broadcast"]:
        return render_design_1_cyber(player_data)
    elif mode in ["design_3", "luxury", "poster", "minimal"]:
        return render_design_3_luxury_poster(player_data)
    else:
        # Default: Design 2 (FUT Shield)
        return render_design_2_fut_shield(player_data)
