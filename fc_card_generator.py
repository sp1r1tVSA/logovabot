"""
fc_card_generator.py

Ultra-premium, retina-grade EA FC 25 / FIFA Ultimate Team (FUT) style card generator using Pillow.
Renders broadcast-quality cards (Gold Rare, TOTW Inform, Icon / Legend) with:
- Authentic 3D beveled FUT shield geometry with corner chamfers and tapered shield base
- Multi-layer metallic gold borders with specular lighting and ambient drop shadows
- High-tech carbon-weave & holographic geometric crystal facets
- Dynamic radial burst spotlight behind player portraits for 3D depth
- Frosted glass stat plates, athletic typography, and sleek attribute badges
- 100% deterministic, high-speed Pillow rendering (no external dependencies required).
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

# Scale factor for Retina 2x rendering
SCALE = 2
WIDTH_1X = 460
HEIGHT_1X = 690

WIDTH = WIDTH_1X * SCALE
HEIGHT = HEIGHT_1X * SCALE

# ─────────────────────────────────────────────────────────────────────────────
# 🎨 Themes & Color Grading
# ─────────────────────────────────────────────────────────────────────────────

THEMES = {
    "gold_rare": {
        "canvas_bg": (14, 11, 6, 255),    # Solid dark gold background (no white Telegram corners)
        "bg_top": (46, 36, 16),           # Deep bronze-gold
        "bg_mid": (24, 18, 8),
        "bg_bottom": (12, 9, 4),
        "spotlight": (255, 215, 0, 85),   # Golden glow burst
        "border_outer": (245, 206, 112),  # Polished bright gold
        "border_mid": (195, 152, 60),     # Brushed gold
        "border_inner": (95, 72, 28),     # Dark antique gold
        "accent": (255, 215, 0),          # Vivid gold
        "plate_bg": (18, 14, 7, 245),     # Dark frosted glass plate
        "plate_border": (195, 152, 60, 180),
        "text_primary": (255, 252, 240),
        "text_secondary": (245, 206, 112),
        "stat_val": (255, 255, 255),
        "stat_lbl": (230, 190, 100),
        "ribbon_bg": (32, 24, 10, 245),
        "badge_text": "GOLD RARE",
        "badge_bg": (22, 18, 10, 245),
    },
    "totw": {
        "canvas_bg": (10, 9, 12, 255),    # Solid dark obsidian background (no white Telegram corners)
        "bg_top": (28, 26, 32),           # Obsidian / Charcoal
        "bg_mid": (14, 13, 16),
        "bg_bottom": (7, 6, 8),
        "spotlight": (255, 205, 35, 90),  # Electric gold spotlight
        "border_outer": (255, 205, 35),   # Neon electric gold
        "border_mid": (200, 155, 20),     # Rich gold
        "border_inner": (65, 52, 18),     # Deep shadow gold
        "accent": (255, 215, 0),
        "plate_bg": (14, 13, 16, 250),    # Deep carbon plate
        "plate_border": (255, 205, 35, 190),
        "text_primary": (255, 255, 255),
        "text_secondary": (255, 205, 35),
        "stat_val": (255, 255, 255),
        "stat_lbl": (255, 205, 35),
        "ribbon_bg": (18, 16, 20, 250),
        "badge_text": "TEAM OF THE WEEK",
        "badge_bg": (18, 16, 20, 250),
    },
    "icon": {
        "canvas_bg": (18, 16, 14, 255),   # Solid dark luxury backdrop
        "bg_top": (248, 245, 235),        # Pearl marble white
        "bg_mid": (225, 218, 205),
        "bg_bottom": (195, 188, 172),
        "spotlight": (255, 255, 255, 120),# Bright celestial aura
        "border_outer": (225, 175, 45),   # Royal goldenrod
        "border_mid": (180, 140, 35),
        "border_inner": (130, 100, 30),
        "accent": (180, 135, 25),
        "plate_bg": (235, 230, 218, 245), # Warm pearl plate
        "plate_border": (180, 140, 35, 180),
        "text_primary": (25, 20, 15),
        "text_secondary": (140, 100, 25),
        "stat_val": (20, 16, 12),
        "stat_lbl": (130, 95, 25),
        "ribbon_bg": (245, 240, 230, 250),
        "badge_text": "ICON LEGEND",
        "badge_bg": (230, 222, 208, 245),
    }
}


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
    """
    Calculate 6 classic FUT attributes (PAC, SHO, PAS, DRI, DEF, PHY) & overall OVR rating
    based on actual tournament statistics.
    """
    goals = int(stats.get("total_goals", 0) or 0)
    assists = int(stats.get("total_assists", 0) or 0)
    matches = int(stats.get("matches_played", 0) or max(1, math.ceil((goals + assists) / 2)))
    position = (stats.get("position") or "ST").strip().upper()

    # Base attributes
    pac_base = 78
    sho_base = 70
    pas_base = 68
    dri_base = 72
    def_base = 40
    phy_base = 70

    # Position modifiers
    if position in ["CB", "LB", "RB", "RWB", "LWB", "ЦЗ", "ЛЗ", "ПЗ"]:
        def_base = 82
        phy_base = 80
        sho_base = 45
    elif position in ["CDM", "CM", "CAM", "ЦП", "ЦОП", "ЦАП"]:
        pas_base = 78
        dri_base = 76
        def_base = 65
        phy_base = 74
    elif position in ["GK", "ВРТ"]:
        def_base = 88
        phy_base = 82
        sho_base = 30
        pac_base = 65

    # Incremental performance boosts
    goals_per_game = goals / max(1, matches)
    assists_per_game = assists / max(1, matches)
    prod_per_game = (goals + assists) / max(1, matches)

    pac = min(99, int(pac_base + min(18, matches * 1.2)))
    sho = min(99, int(sho_base + min(28, goals * 3.5 + goals_per_game * 8)))
    pas = min(99, int(pas_base + min(28, assists * 4.0 + assists_per_game * 8)))
    dri = min(99, int(dri_base + min(25, prod_per_game * 10)))
    def_stat = min(99, int(def_base + min(15, matches * 0.8)))
    phy = min(99, int(phy_base + min(24, matches * 1.5 + goals * 0.5)))

    # Overall OVR Calculation
    if position in ["ST", "CF", "LW", "RW", "НАП", "ЛВ", "ПВ"]:
        ovr = int(0.35 * sho + 0.25 * dri + 0.20 * pac + 0.12 * pas + 0.08 * phy)
    elif position in ["CAM", "CM", "ЦАП", "ЦП"]:
        ovr = int(0.30 * pas + 0.25 * dri + 0.20 * sho + 0.15 * pac + 0.10 * phy)
    elif position in ["CB", "LB", "RB", "ЦЗ", "ЛЗ", "ПЗ"]:
        ovr = int(0.35 * def_stat + 0.30 * phy + 0.15 * pac + 0.12 * pas + 0.08 * dri)
    else:
        ovr = int((pac + sho + pas + dri + def_stat + phy) / 6.0)

    ovr = max(75, min(99, ovr))

    return {
        "ovr": ovr,
        "position": position,
        "pac": max(50, pac),
        "sho": max(40, sho),
        "pas": max(45, pas),
        "dri": max(50, dri),
        "def": max(30, def_stat),
        "phy": max(50, phy),
    }


def get_fut_shield_polygon(size: tuple[int, int], inset: int = 0) -> list[tuple[int, int]]:
    """
    Returns authentic EA FC 25 FUT shield polygon vertices with:
    - Top chamfered corner cuts (45-degree angle)
    - Straight side edges
    - Deep double-angled tapered bottom shield point
    """
    w, h = size
    top_cut = int((30 - inset * 0.5) * SCALE)
    bot_shoulder = int(h * 0.81)
    bot_mid_y = int(h * 0.92)
    bot_mid_x = int((45 - inset * 0.6) * SCALE)

    x1 = inset
    x2 = w - inset
    y1 = inset
    y2 = h - inset

    return [
        (x1 + top_cut, y1),               # Top left chamfer end
        (x2 - top_cut, y1),               # Top right chamfer start
        (x2, y1 + top_cut),               # Top right chamfer end
        (x2, bot_shoulder),               # Right side bottom shoulder
        (x2 - bot_mid_x, bot_mid_y),      # Right lower taper
        (w // 2, y2),                     # Bottom center point
        (x1 + bot_mid_x, bot_mid_y),      # Left lower taper
        (x1, bot_shoulder),               # Left side bottom shoulder
        (x1, y1 + top_cut),               # Top left chamfer start
    ]


def create_fut_shield_mask(size: tuple[int, int], inset: int = 0) -> Image.Image:
    """Create a crisp binary mask for the FUT shield."""
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    poly = get_fut_shield_polygon(size, inset)
    draw.polygon(poly, fill=255)
    return mask


def draw_radial_spotlight(size: tuple[int, int], center: tuple[int, int], radius: int, color_rgba: tuple) -> Image.Image:
    """Renders a soft radial glow burst for dramatic depth behind portraits."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = center
    r_max = radius
    cr, cg, cb, ca = color_rgba

    # Draw concentric soft circles with quadratic falloff
    steps = 40
    for i in range(steps, 0, -1):
        curr_r = int(r_max * (i / steps))
        factor = 1.0 - (i / steps) ** 1.5
        alpha = int(ca * factor)
        draw.ellipse([(cx - curr_r, cy - curr_r), (cx + curr_r, cy + curr_r)], fill=(cr, cg, cb, alpha))

    return img.filter(ImageFilter.GaussianBlur(int(12 * SCALE)))


def draw_carbon_and_facets(size: tuple[int, int], theme: dict) -> Image.Image:
    """Draws high-tech carbon weave and dynamic geometric crystal facets."""
    w, h = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # 1. Diagonal Carbon Micro-Lines
    line_step = int(8 * SCALE)
    line_col = theme["accent"] + (10,)
    for diag in range(-h, w + h, line_step):
        draw.line([(diag, 0), (diag + h, h)], fill=line_col, width=int(1.5 * SCALE))

    # 2. Futuristic Geometric Crystal Facets (Triangles / Hexagons)
    step_y = int(55 * SCALE)
    step_x = int(65 * SCALE)
    for row, y in enumerate(range(0, h, step_y)):
        x_offset = (row % 2) * (step_x // 2)
        for col, x in enumerate(range(-step_x, w + step_x, step_x)):
            cx = x + x_offset
            # Upper Triangle
            t1 = [(cx, y), (cx + step_x // 2, y - step_y // 2), (cx + step_x, y)]
            alpha1 = 18 if (row + col) % 3 == 0 else 8
            draw.polygon(t1, fill=theme["accent"] + (alpha1,))

            # Lower Triangle
            t2 = [(cx, y), (cx + step_x, y), (cx + step_x // 2, y + step_y // 2)]
            alpha2 = 14 if (row * 2 + col) % 4 == 0 else 6
            draw.polygon(t2, fill=theme["accent"] + (alpha2,))

    return layer


def draw_metallic_frame(size: tuple[int, int], theme: dict) -> Image.Image:
    """Draws multi-layered 3D beveled metallic borders with specular corners."""
    w, h = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # 1. Main outer border (Brushed & Polished Gold)
    poly_outer = get_fut_shield_polygon(size, inset=int(10 * SCALE))
    draw.polygon(poly_outer, outline=theme["border_outer"] + (255,), width=int(4 * SCALE))

    # 2. Mid specular highlight line
    poly_mid = get_fut_shield_polygon(size, inset=int(15 * SCALE))
    draw.polygon(poly_mid, outline=theme["border_mid"] + (220,), width=int(1.5 * SCALE))

    # 3. Inner shadow bevel
    poly_inner = get_fut_shield_polygon(size, inset=int(20 * SCALE))
    draw.polygon(poly_inner, outline=theme["border_inner"] + (180,), width=int(1.5 * SCALE))

    # 4. Specular Corner Bracket Pins
    pin_len = int(18 * SCALE)
    pin_col = theme["border_outer"] + (255,)
    # Top Left Corner Bracket
    draw.line([(int(28 * SCALE), int(10 * SCALE)), (int(28 * SCALE) + pin_len, int(10 * SCALE))], fill=pin_col, width=int(2.5 * SCALE))
    draw.line([(int(10 * SCALE), int(28 * SCALE)), (int(10 * SCALE), int(28 * SCALE) + pin_len)], fill=pin_col, width=int(2.5 * SCALE))
    # Top Right Corner Bracket
    draw.line([(w - int(28 * SCALE) - pin_len, int(10 * SCALE)), (w - int(28 * SCALE), int(10 * SCALE))], fill=pin_col, width=int(2.5 * SCALE))
    draw.line([(w - int(10 * SCALE), int(28 * SCALE)), (w - int(10 * SCALE), int(28 * SCALE) + pin_len)], fill=pin_col, width=int(2.5 * SCALE))

    return layer


def generate_ea_fc_card(player_data: dict, theme_name: str = "gold_rare") -> io.BytesIO:
    """
    Generate an authentic broadcast-grade EA FC 25 / FUT player card as high-res PNG.
    """
    theme = THEMES.get(theme_name, THEMES["gold_rare"])
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

    # 1. Base Canvas with solid dark theme backdrop (prevents Telegram JPEG white corners completely!)
    card = Image.new("RGBA", (WIDTH, HEIGHT), theme["canvas_bg"])

    # 2. Gradient Background Canvas
    bg_gradient = Image.new("RGBA", (WIDTH, HEIGHT))
    bg_draw = ImageDraw.Draw(bg_gradient)
    top_r, top_g, top_b = theme["bg_top"]
    bot_r, bot_g, bot_b = theme["bg_bottom"]

    for y in range(HEIGHT):
        ratio = y / max(1, HEIGHT)
        r = int(top_r + (bot_r - top_r) * ratio)
        g = int(top_g + (bot_g - top_g) * ratio)
        b = int(top_b + (bot_b - top_b) * ratio)
        bg_draw.line([(0, y), (WIDTH, y)], fill=(r, g, b, 255))

    # 3. Add Dynamic Radial Burst Glow behind player (depth & illumination)
    spotlight_x = int(WIDTH * 0.54)
    spotlight_y = int(HEIGHT * 0.25)
    spotlight = draw_radial_spotlight((WIDTH, HEIGHT), (spotlight_x, spotlight_y), int(230 * SCALE), theme["spotlight"])
    bg_gradient = Image.alpha_composite(bg_gradient, spotlight)

    # 4. Add Carbon Weave & Crystal Facets
    facets = draw_carbon_and_facets((WIDTH, HEIGHT), theme)
    bg_gradient = Image.alpha_composite(bg_gradient, facets)

    # 5. Mask Background to FUT Shield Shape
    shield_mask = create_fut_shield_mask((WIDTH, HEIGHT), inset=0)
    card.paste(bg_gradient, (0, 0), shield_mask)

    # 6. Player Photo Rendering with Dynamic Drop Shadow & Smooth Base Fade
    # Scaled up for majestic presence and centered across the card
    photo_box_w = int(350 * SCALE)
    photo_box_h = int(350 * SCALE)
    photo_x = int(75 * SCALE)
    photo_y = int(18 * SCALE)

    player_img = None
    try:
        photo_path = player_photos.get_player_photo(player_name, team_name)
        if photo_path and os.path.exists(photo_path):
            player_img = Image.open(photo_path).convert("RGBA")
    except Exception as e:
        logger.warning(f"Failed to fetch photo for {player_name}: {e}")

    if player_img:
        try:
            # Resize with Lanczos filtering
            player_img.thumbnail((photo_box_w, photo_box_h), Image.Resampling.LANCZOS)
            pw, ph = player_img.size

            # Soft drop shadow behind player
            shadow = Image.new("RGBA", (pw + int(24 * SCALE), ph + int(24 * SCALE)), (0, 0, 0, 0))
            shadow_mask = player_img.split()[3] if "A" in player_img.getbands() else Image.new("L", (pw, ph), 255)
            shadow_fill = Image.new("RGBA", (pw, ph), (0, 0, 0, 180))
            shadow.paste(shadow_fill, (int(10 * SCALE), int(10 * SCALE)), shadow_mask)
            shadow = shadow.filter(ImageFilter.GaussianBlur(int(8 * SCALE)))

            px = photo_x + (photo_box_w - pw) // 2
            py = photo_y + (photo_box_h - ph)
            card.paste(shadow, (px - int(4 * SCALE), py - int(4 * SCALE)), shadow)

            # Smooth vertical gradient alpha fade at the torso baseline
            fade_mask = Image.new("L", (pw, ph), 255)
            f_mask_draw = ImageDraw.Draw(fade_mask)
            fade_start_y = int(ph * 0.68)
            for fy in range(fade_start_y, ph):
                fade_ratio = (fy - fade_start_y) / max(1, (ph - fade_start_y))
                alpha_val = int(255 * (1.0 - (fade_ratio ** 1.6)))
                f_mask_draw.line([(0, fy), (pw, fy)], fill=alpha_val)

            if "A" in player_img.getbands():
                fade_mask = ImageChops.multiply(player_img.split()[3], fade_mask)

            card.paste(player_img, (px, py), fade_mask)
        except Exception as e:
            logger.warning(f"Error drawing player photo: {e}")
    else:
        # High quality geometric athlete silhouette placeholder
        sil_layer = Image.new("RGBA", (photo_box_w, photo_box_h), (0, 0, 0, 0))
        sil_draw = ImageDraw.Draw(sil_layer)
        sc_x = photo_box_w // 2
        head_r = int(42 * SCALE)
        sil_draw.ellipse([(sc_x - head_r, int(22 * SCALE)), (sc_x + head_r, int(22 * SCALE) + 2 * head_r)], fill=theme["border_outer"] + (65,))
        torso_poly = [
            (sc_x - int(100 * SCALE), photo_box_h),
            (sc_x - int(75 * SCALE), int(115 * SCALE)),
            (sc_x + int(75 * SCALE), int(115 * SCALE)),
            (sc_x + int(100 * SCALE), photo_box_h),
        ]
        sil_draw.polygon(torso_poly, fill=theme["border_outer"] + (55,))
        card.paste(sil_layer, (photo_x, photo_y), sil_layer)

    # 7. Frosted Glass Stat Plate for bottom half
    plate_w = int(390 * SCALE)
    plate_h = int(230 * SCALE)
    plate_x = (WIDTH - plate_w) // 2
    plate_y = int(345 * SCALE)

    plate = Image.new("RGBA", (plate_w, plate_h), (0, 0, 0, 0))
    p_draw = ImageDraw.Draw(plate)
    p_draw.rounded_rectangle(
        [(0, 0), (plate_w, plate_h)],
        radius=int(18 * SCALE),
        fill=theme["plate_bg"],
        outline=theme["plate_border"],
        width=int(1.5 * SCALE)
    )
    card.paste(plate, (plate_x, plate_y), plate)

    # 8. Metallic Frame Overlay (Borders, Specular Pins)
    frame = draw_metallic_frame((WIDTH, HEIGHT), theme)
    card = Image.alpha_composite(card, frame)
    draw = ImageDraw.Draw(card)

    # ─────────────────────────────────────────────────────────────────────────
    # 9. Top-Left Column: OVR Rating, Position Badge, Club Crest
    # ─────────────────────────────────────────────────────────────────────────
    font_ovr = load_card_font(52, bold=True)
    font_pos = load_card_font(21, bold=True)

    col_x = int(62 * SCALE)
    ovr_y = int(52 * SCALE)

    # OVR with subtle 3D drop shadow
    ovr_str = str(ovr)
    draw.text((col_x + int(2 * SCALE), ovr_y + int(2 * SCALE)), ovr_str, font=font_ovr, fill=(0, 0, 0, 180), anchor="mt")
    draw.text((col_x, ovr_y), ovr_str, font=font_ovr, fill=theme["text_primary"], anchor="mt")

    # Position Tag (with sleek frosted pill backing)
    pos_y = ovr_y + int(56 * SCALE)
    pos_w = int(52 * SCALE)
    pos_h = int(26 * SCALE)
    pos_rect = [(col_x - pos_w // 2, pos_y), (col_x + pos_w // 2, pos_y + pos_h)]
    draw.rounded_rectangle(pos_rect, radius=int(6 * SCALE), fill=theme["badge_bg"], outline=theme["border_outer"] + (200,), width=int(1.5 * SCALE))
    draw.text((col_x, pos_y + int(3 * SCALE)), position, font=font_pos, fill=theme["text_secondary"], anchor="mt")

    # Club Crest
    crest_y = pos_y + int(38 * SCALE)
    logo_fn = get_team_logo_filename(team_name)
    if logo_fn:
        logo_path = os.path.join(LOGOS_DIR, logo_fn)
        if os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path)
                logo_img = clean_and_prepare_logo(logo_img)
                logo_size = int(50 * SCALE)
                logo_img.thumbnail((logo_size, logo_size), Image.Resampling.LANCZOS)
                lx = col_x - (logo_img.width // 2)
                ly = crest_y
                card.paste(logo_img, (lx, ly), logo_img)
            except Exception as e:
                logger.warning(f"Error rendering crest: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # 10. Player Name Ribbon
    # ─────────────────────────────────────────────────────────────────────────
    ribbon_w = int(360 * SCALE)
    ribbon_h = int(42 * SCALE)
    ribbon_x = (WIDTH - ribbon_w) // 2
    ribbon_y = int(355 * SCALE)

    # Frosted Ribbon Bar
    draw.rounded_rectangle(
        [(ribbon_x, ribbon_y), (ribbon_x + ribbon_w, ribbon_y + ribbon_h)],
        radius=int(10 * SCALE),
        fill=theme["ribbon_bg"],
        outline=theme["border_outer"] + (180,),
        width=int(1.5 * SCALE)
    )

    # Player Name Font scaling
    name_size = 26
    if len(player_name) > 13:
        name_size = 21
    if len(player_name) > 18:
        name_size = 17

    font_name = load_card_font(name_size, bold=True)
    draw.text((WIDTH // 2, ribbon_y + int(7 * SCALE)), player_name, font=font_name, fill=theme["text_primary"], anchor="mt")

    # ─────────────────────────────────────────────────────────────────────────
    # 11. 6-Attribute FIFA Grid (PAC, SHO, PAS | DRI, DEF, PHY)
    # ─────────────────────────────────────────────────────────────────────────
    font_stat_val = load_card_font(25, bold=True)
    font_stat_name = load_card_font(18, bold=True)

    grid_y_start = ribbon_y + ribbon_h + int(16 * SCALE)
    row_height = int(38 * SCALE)

    left_col_val_x = plate_x + int(42 * SCALE)
    left_col_lbl_x = plate_x + int(88 * SCALE)

    right_col_val_x = plate_x + int(218 * SCALE)
    right_col_lbl_x = plate_x + int(264 * SCALE)

    # Center Vertical Separator
    sep_x = WIDTH // 2
    draw.line(
        [(sep_x, grid_y_start - int(4 * SCALE)), (sep_x, grid_y_start + int(112 * SCALE))],
        fill=theme["border_mid"] + (140,),
        width=int(1.5 * SCALE)
    )

    stats_matrix = [
        (pac, "PAC", dri, "DRI"),
        (sho, "SHO", def_stat, "DEF"),
        (pas, "PAS", phy, "PHY"),
    ]

    for idx, (l_val, l_lbl, r_val, r_lbl) in enumerate(stats_matrix):
        cur_y = grid_y_start + idx * row_height

        # Left stat
        draw.text((left_col_val_x, cur_y), f"{l_val:>2}", font=font_stat_val, fill=theme["stat_val"], anchor="lt")
        draw.text((left_col_lbl_x, cur_y + int(4 * SCALE)), l_lbl, font=font_stat_name, fill=theme["stat_lbl"], anchor="lt")

        # Right stat
        draw.text((right_col_val_x, cur_y), f"{r_val:>2}", font=font_stat_val, fill=theme["stat_val"], anchor="lt")
        draw.text((right_col_lbl_x, cur_y + int(4 * SCALE)), r_lbl, font=font_stat_name, fill=theme["stat_lbl"], anchor="lt")

    # ─────────────────────────────────────────────────────────────────────────
    # 12. Bottom Tournament Footer Badge
    # ─────────────────────────────────────────────────────────────────────────
    footer_w = int(280 * SCALE)
    footer_h = int(24 * SCALE)
    footer_x = (WIDTH - footer_w) // 2
    footer_y = HEIGHT - int(82 * SCALE)

    draw.rounded_rectangle(
        [(footer_x, footer_y), (footer_x + footer_w, footer_y + footer_h)],
        radius=int(6 * SCALE),
        fill=theme["badge_bg"],
        outline=theme["border_outer"] + (180,),
        width=int(1.5 * SCALE)
    )

    font_footer = load_card_font(11, bold=True)
    badge_label = f"★ {theme['badge_text']} • КПЛ 2026 ★"
    draw.text((WIDTH // 2, footer_y + int(4 * SCALE)), badge_label, font=font_footer, fill=theme["text_secondary"], anchor="mt")

    # 13. Output PNG Buffer
    buf = io.BytesIO()
    card.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
