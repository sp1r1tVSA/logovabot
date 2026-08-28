"""
fc_card_generator.py

High-performance, retina-grade EA FC / FIFA Ultimate Team (FUT) style card generator using Pillow.
Renders cards in multiple styles (Gold Rare, TOTW Inform, Icon / Legend, Champions) with:
- Dynamic Overall Rating (OVR: 75-99)
- Position, Club Crest, Country/League Flag
- High-res Player Portrait (with automatic alpha gradient blending)
- Dynamic 6-Attribute FIFA Grid (PAC, SHO, PAS, DRI, DEF, PHY)
- Metallic gradients, hexagon facets, and sleek modern typography.
"""

import os
import io
import math
import logging
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops
import database
from table_generator import get_team_logo_filename, clean_and_prepare_logo
import player_photos

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(__file__)
LOGOS_DIR = os.path.join(BASE_DIR, "assets", "logos")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

# Dimensions for base render (Supersampled x2 for Retina broadcast crispness)
SCALE = 2
WIDTH_1X = 460
HEIGHT_1X = 680

WIDTH = WIDTH_1X * SCALE
HEIGHT = HEIGHT_1X * SCALE

# ─────────────────────────────────────────────────────────────────────────────
# Color Palettes & Card Themes
# ─────────────────────────────────────────────────────────────────────────────

THEMES = {
    "gold_rare": {
        "bg_top": (42, 33, 14),           # Deep bronze-gold
        "bg_mid": (28, 22, 10),
        "bg_bottom": (18, 14, 7),
        "border_outer": (224, 185, 96),   # Shining gold
        "border_inner": (148, 115, 45),   # Muted dark gold
        "accent": (255, 215, 0),          # Bright gold
        "text_primary": (255, 248, 220),  # Cornsilk gold-white
        "text_secondary": (212, 175, 55),
        "text_accent": (255, 223, 128),
        "divider": (180, 145, 60, 180),
        "stat_num": (255, 248, 230),
        "stat_lbl": (212, 185, 120),
        "badge_text": "GOLD RARE",
    },
    "totw": {
        "bg_top": (24, 24, 28),           # Pitch black / graphite
        "bg_mid": (14, 14, 16),
        "bg_bottom": (8, 8, 10),
        "border_outer": (245, 197, 24),   # Electric neon gold
        "border_inner": (80, 70, 30),
        "accent": (255, 215, 0),
        "text_primary": (255, 255, 255),
        "text_secondary": (245, 197, 24),
        "text_accent": (245, 197, 24),
        "divider": (245, 197, 24, 160),
        "stat_num": (255, 255, 255),
        "stat_lbl": (245, 197, 24),
        "badge_text": "TEAM OF THE WEEK",
    },
    "icon": {
        "bg_top": (240, 238, 230),        # Pearl white / marble
        "bg_mid": (218, 214, 200),
        "bg_bottom": (190, 185, 170),
        "border_outer": (218, 165, 32),   # Goldenrod
        "border_inner": (160, 130, 60),
        "accent": (184, 134, 11),
        "text_primary": (30, 25, 20),
        "text_secondary": (120, 90, 30),
        "text_accent": (140, 105, 35),
        "divider": (184, 134, 11, 150),
        "stat_num": (30, 25, 20),
        "stat_lbl": (110, 85, 30),
        "badge_text": "ICON",
    }
}


def load_card_font(size: int, bold: bool = False, condensed: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load appropriate system font for athletic card layout."""
    size_scaled = size * SCALE
    font_candidates = []
    if bold:
        font_candidates.extend(["impact.ttf", "arialbd.ttf", "trebucbd.ttf", "DejaVuSans-Bold.ttf", "seguiemj.ttf"])
    else:
        font_candidates.extend(["arial.ttf", "trebuc.ttf", "DejaVuSans.ttf"])

    for fn in font_candidates:
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

    # Ensure competitive range
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


def create_fut_card_mask(size: tuple[int, int], radius: int = 36) -> Image.Image:
    """Create a FUT shield shaped mask with rounded corners and bottom tapered bevel."""
    w, h = size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)

    # Main rounded body
    draw.rounded_rectangle([(0, 0), (w, int(h * 0.88))], radius=radius * SCALE, fill=255)

    # Bottom tapered shield point
    poly_points = [
        (0, int(h * 0.82)),
        (w, int(h * 0.82)),
        (w - int(30 * SCALE), int(h * 0.94)),
        (int(w * 0.5), h - int(5 * SCALE)),
        (int(30 * SCALE), int(h * 0.94)),
    ]
    draw.polygon(poly_points, fill=255)
    return mask


def draw_linear_gradient(draw: ImageDraw.ImageDraw, size: tuple[int, int], top_color: tuple, bot_color: tuple) -> Image.Image:
    """Generate a vertical linear gradient canvas."""
    w, h = size
    base = Image.new("RGBA", (w, h), top_color)
    top_r, top_g, top_b = top_color[:3]
    bot_r, bot_g, bot_b = bot_color[:3]

    gradient = Image.new("RGBA", (w, h))
    g_draw = ImageDraw.Draw(gradient)
    for y in range(h):
        ratio = y / max(1, h)
        r = int(top_r + (bot_r - top_r) * ratio)
        g = int(top_g + (bot_g - top_g) * ratio)
        b = int(top_b + (bot_b - top_b) * ratio)
        g_draw.line([(0, y), (w, y)], fill=(r, g, b, 255))
    return gradient


def generate_ea_fc_card(player_data: dict, theme_name: str = "gold_rare") -> io.BytesIO:
    """
    Generate an EA FC / FUT player card as high-resolution PNG buffer.

    player_data dict:
    {
        "player_name": "VINICIUS JR.",
        "team_name": "Спортинг",
        "position": "LW",       # optional
        "ovr": 92,               # optional override
        "total_goals": 14,
        "total_assists": 7,
        "matches_played": 8,
        "theme": "gold_rare" / "totw" / "icon",
        "custom_stats": {"PAC": 95, "SHO": 88, ...} # optional override
    }
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

    # 1. Base Canvas
    card = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))

    # 2. Gradient Background
    bg_gradient = draw_linear_gradient(
        ImageDraw.Draw(card),
        (WIDTH, HEIGHT),
        theme["bg_top"],
        theme["bg_bottom"]
    )

    # 3. Add subtle hexagonal/facet texture pattern on background
    facet_overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    f_draw = ImageDraw.Draw(facet_overlay)
    step_y = int(45 * SCALE)
    for row, y in enumerate(range(0, HEIGHT, step_y)):
        offset_x = int((row % 2) * 35 * SCALE)
        for x in range(-offset_x, WIDTH + int(50 * SCALE), int(70 * SCALE)):
            poly = [
                (x, y),
                (x + int(25 * SCALE), y - int(12 * SCALE)),
                (x + int(50 * SCALE), y),
                (x + int(50 * SCALE), y + int(25 * SCALE)),
                (x + int(25 * SCALE), y + int(37 * SCALE)),
                (x, y + int(25 * SCALE)),
            ]
            fill_alpha = 15 if (x + y) % 3 == 0 else 7
            accent_col = theme["accent"] + (fill_alpha,)
            f_draw.polygon(poly, fill=accent_col)

    bg_combined = Image.alpha_composite(bg_gradient, facet_overlay)

    # Mask background into FUT Shield
    card_mask = create_fut_card_mask((WIDTH, HEIGHT), radius=32)
    card.paste(bg_combined, (0, 0), card_mask)

    # 4. Outer & Inner Glowing Shield Borders
    border_layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    b_draw = ImageDraw.Draw(border_layer)

    # Outer border
    outer_poly = [
        (int(14 * SCALE), int(14 * SCALE)),
        (WIDTH - int(14 * SCALE), int(14 * SCALE)),
        (WIDTH - int(14 * SCALE), int(HEIGHT * 0.82)),
        (WIDTH - int(38 * SCALE), int(HEIGHT * 0.93)),
        (int(WIDTH * 0.5), HEIGHT - int(16 * SCALE)),
        (int(38 * SCALE), int(HEIGHT * 0.93)),
        (int(14 * SCALE), int(HEIGHT * 0.82)),
    ]
    b_draw.polygon(outer_poly, outline=theme["border_outer"] + (255,), width=int(3.5 * SCALE))

    # Inner decorative border
    inner_poly = [
        (int(22 * SCALE), int(22 * SCALE)),
        (WIDTH - int(22 * SCALE), int(22 * SCALE)),
        (WIDTH - int(22 * SCALE), int(HEIGHT * 0.81)),
        (WIDTH - int(44 * SCALE), int(HEIGHT * 0.92)),
        (int(WIDTH * 0.5), HEIGHT - int(25 * SCALE)),
        (int(44 * SCALE), int(HEIGHT * 0.92)),
        (int(22 * SCALE), int(HEIGHT * 0.81)),
    ]
    b_draw.polygon(inner_poly, outline=theme["border_inner"] + (180,), width=int(1.5 * SCALE))

    card = Image.alpha_composite(card, border_layer)
    draw = ImageDraw.Draw(card)

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Top Left Column: OVR, Position, Club Logo, Badge
    # ─────────────────────────────────────────────────────────────────────────
    font_ovr = load_card_font(48, bold=True)
    font_pos = load_card_font(20, bold=True)

    col_x = int(58 * SCALE)
    ovr_y = int(52 * SCALE)

    # Draw OVR
    ovr_str = str(ovr)
    draw.text((col_x, ovr_y), ovr_str, font=font_ovr, fill=theme["text_primary"], anchor="mt")

    # Draw Position
    pos_y = ovr_y + int(52 * SCALE)
    draw.text((col_x, pos_y), position, font=font_pos, fill=theme["text_secondary"], anchor="mt")

    # Divider bar below pos
    div_y = pos_y + int(28 * SCALE)
    draw.line([(col_x - int(18 * SCALE), div_y), (col_x + int(18 * SCALE), div_y)], fill=theme["border_outer"] + (200,), width=int(2 * SCALE))

    # Club Logo
    logo_fn = get_team_logo_filename(team_name)
    if logo_fn:
        logo_path = os.path.join(LOGOS_DIR, logo_fn)
        if os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path)
                logo_img = clean_and_prepare_logo(logo_img)
                logo_size = int(46 * SCALE)
                logo_img.thumbnail((logo_size, logo_size), Image.Resampling.LANCZOS)
                logo_x = col_x - (logo_img.width // 2)
                logo_y = div_y + int(16 * SCALE)
                card.paste(logo_img, (logo_x, logo_y), logo_img)
            except Exception as e:
                logger.warning(f"Error loading club logo {logo_fn}: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # 6. Player Photo (Center / Right with smooth alpha fade)
    # ─────────────────────────────────────────────────────────────────────────
    photo_box_w = int(270 * SCALE)
    photo_box_h = int(270 * SCALE)
    photo_x = int(145 * SCALE)
    photo_y = int(45 * SCALE)

    # Try fetching / loading player photo
    player_img = None
    try:
        photo_path = player_photos.get_player_photo(player_name, team_name)
        if photo_path and os.path.exists(photo_path):
            player_img = Image.open(photo_path).convert("RGBA")
    except Exception as e:
        logger.warning(f"Failed to fetch photo for {player_name}: {e}")

    if player_img:
        try:
            # Resize preserving aspect ratio to fill photo area
            player_img.thumbnail((photo_box_w, photo_box_h), Image.Resampling.LANCZOS)
            pw, ph = player_img.size

            # Create vertical alpha gradient mask to fade the bottom of the photo smoothly into the card
            fade_mask = Image.new("L", (pw, ph), 255)
            f_mask_draw = ImageDraw.Draw(fade_mask)
            fade_start_y = int(ph * 0.65)
            for fy in range(fade_start_y, ph):
                fade_ratio = (fy - fade_start_y) / max(1, (ph - fade_start_y))
                alpha_val = int(255 * (1.0 - (fade_ratio ** 1.5)))
                f_mask_draw.line([(0, fy), (pw, fy)], fill=alpha_val)

            # Combine with existing image alpha if any
            if "A" in player_img.getbands():
                fade_mask = ImageChops.multiply(player_img.split()[3], fade_mask)

            px = photo_x + (photo_box_w - pw) // 2
            py = photo_y + (photo_box_h - ph)
            card.paste(player_img, (px, py), fade_mask)
        except Exception as e:
            logger.warning(f"Error drawing player photo: {e}")
    else:
        # High quality geometric athlete silhouette placeholder
        sil_layer = Image.new("RGBA", (photo_box_w, photo_box_h), (0, 0, 0, 0))
        sil_draw = ImageDraw.Draw(sil_layer)
        sc_x = photo_box_w // 2
        # Head
        head_r = int(32 * SCALE)
        sil_draw.ellipse([(sc_x - head_r, int(20 * SCALE)), (sc_x + head_r, int(20 * SCALE) + 2 * head_r)], fill=theme["border_outer"] + (60,))
        # Shoulders / Torso
        torso_poly = [
            (sc_x - int(75 * SCALE), photo_box_h),
            (sc_x - int(55 * SCALE), int(95 * SCALE)),
            (sc_x + int(55 * SCALE), int(95 * SCALE)),
            (sc_x + int(75 * SCALE), photo_box_h),
        ]
        sil_draw.polygon(torso_poly, fill=theme["border_outer"] + (50,))
        card.paste(sil_layer, (photo_x, photo_y), sil_layer)

    # ─────────────────────────────────────────────────────────────────────────
    # 7. Player Name Banner & Divider Ribbon
    # ─────────────────────────────────────────────────────────────────────────
    name_banner_y = int(330 * SCALE)

    # Golden ribbon separator
    ribbon_w = int(380 * SCALE)
    rx1 = (WIDTH - ribbon_w) // 2
    rx2 = rx1 + ribbon_w
    draw.line([(rx1, name_banner_y), (rx2, name_banner_y)], fill=theme["divider"], width=int(2.5 * SCALE))

    # Player Name Text (dynamic font scaling to avoid overflow)
    name_font_size = 28
    if len(player_name) > 14:
        name_font_size = 22
    if len(player_name) > 20:
        name_font_size = 18

    font_name = load_card_font(name_font_size, bold=True)
    draw.text((WIDTH // 2, name_banner_y + int(10 * SCALE)), player_name, font=font_name, fill=theme["text_primary"], anchor="mt")

    # Lower separator below name
    name_bottom_y = name_banner_y + int(48 * SCALE)
    draw.line([(rx1 + int(40 * SCALE), name_bottom_y), (rx2 - int(40 * SCALE), name_bottom_y)], fill=theme["divider"], width=int(1.5 * SCALE))

    # ─────────────────────────────────────────────────────────────────────────
    # 8. 6-Attribute FIFA Grid (PAC, SHO, PAS, DRI, DEF, PHY)
    # ─────────────────────────────────────────────────────────────────────────
    font_stat_val = load_card_font(23, bold=True)
    font_stat_name = load_card_font(18, bold=True)

    grid_y_start = name_bottom_y + int(20 * SCALE)
    row_height = int(38 * SCALE)

    # Left Column (PAC, SHO, PAS) | Right Column (DRI, DEF, PHY)
    left_col_x = int(88 * SCALE)
    right_col_x = int(250 * SCALE)

    stats_matrix = [
        # (left_val, left_label, right_val, right_label)
        (pac, "PAC", dri, "DRI"),
        (sho, "SHO", def_stat, "DEF"),
        (pas, "PAS", phy, "PHY"),
    ]

    for idx, (l_val, l_lbl, r_val, r_lbl) in enumerate(stats_matrix):
        current_y = grid_y_start + idx * row_height

        # Left Stat
        draw.text((left_col_x, current_y), f"{l_val:>2}", font=font_stat_val, fill=theme["stat_num"], anchor="lt")
        draw.text((left_col_x + int(42 * SCALE), current_y + int(3 * SCALE)), l_lbl, font=font_stat_name, fill=theme["stat_lbl"], anchor="lt")

        # Vertical separator line in center
        sep_x = WIDTH // 2
        draw.line([(sep_x, grid_y_start - int(5 * SCALE)), (sep_x, grid_y_start + int(105 * SCALE))], fill=theme["border_inner"] + (130,), width=int(1.5 * SCALE))

        # Right Stat
        draw.text((right_col_x, current_y), f"{r_val:>2}", font=font_stat_val, fill=theme["stat_num"], anchor="lt")
        draw.text((right_col_x + int(42 * SCALE), current_y + int(3 * SCALE)), r_lbl, font=font_stat_name, fill=theme["stat_lbl"], anchor="lt")

    # ─────────────────────────────────────────────────────────────────────────
    # 9. Bottom Footer Badge / Shield Crest
    # ─────────────────────────────────────────────────────────────────────────
    footer_y = HEIGHT - int(80 * SCALE)
    font_footer = load_card_font(12, bold=True)
    badge_label = f"★ {theme['badge_text']} • КПЛ 2026 ★"
    draw.text((WIDTH // 2, footer_y), badge_label, font=font_footer, fill=theme["text_secondary"], anchor="mt")

    # 10. Output buffer
    buf = io.BytesIO()
    card.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
