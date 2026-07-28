import os
import io
import database
from PIL import Image, ImageDraw, ImageFont

# Path to club logos directory
BASE_DIR = os.path.dirname(__file__)
LOGOS_DIR = os.path.join(BASE_DIR, "assets", "logos")

# Map of Russian club names to PNG logo filenames
TEAM_LOGO_MAP = {
    "Спортинг": "sporting.png",
    "Ривер Плейт": "river_plate.png",
    "Бока Хуниорс": "boca_juniors.png",
    "Бенфика": "benfica.png",
    "ПСВ": "psv.png",
    "Порту": "porto.png",
    "Будë Глимт": "bodo_glimt.png",
    "Будё Глимт": "bodo_glimt.png",
    "Фейеноорд": "feyenoord.png",
    "Селтик": "celtic.png",
    "Расинг": "racing.png",
    "Аякс": "ajax.png",
    "Брага": "braga.png",
    "Рейнджерс": "rangers.png",
    "Брюгге": "brugge.png",
    "Копенгаген": "copenhagen.png",
    "АЕК": "aek.png"
}

SCALE = 2  # 2x Supersampling for Retina broadcast sharpness


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load Arial or fallback font."""
    font_names = ["arialbd.ttf" if bold else "arial.ttf", "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", "seguiemj.ttf"]
    for font_name in font_names:
        try:
            return ImageFont.truetype(font_name, size)
        except IOError:
            continue
    return ImageFont.load_default()


def generate_league_table_image(standings: list[dict] = None, form_map: dict[int, list[str]] = None) -> io.BytesIO:
    """
    Generate a 2x supersampled, high-res graphic image of the league table.
    Returns io.BytesIO PNG buffer.
    """
    if standings is None:
        standings = database.get_standings()
    if form_map is None:
        form_map = database.get_teams_recent_form(limit=5)

    # 1x Base Dimensions
    width_1x = 1120
    row_height_1x = 48
    table_top_1x = 130
    num_rows = len(standings) if standings else 16
    footer_height_1x = 90
    height_1x = table_top_1x + (num_rows * row_height_1x) + footer_height_1x

    # 2x Scaled Canvas Dimensions
    width = width_1x * SCALE
    height = height_1x * SCALE
    row_height = row_height_1x * SCALE
    table_top = table_top_1x * SCALE

    # Colors
    bg_color           = (20, 20, 22)         # #141416
    row_bg_1           = (26, 26, 30)         # #1A1A1E
    row_bg_2           = (20, 20, 22)         # #141416
    header_text_color  = (156, 163, 175)   # #9CA3AF
    primary_text_color = (255, 255, 255)
    muted_text_color   = (209, 213, 219)
    red_accent_color   = (239, 68, 68)

    # Canvas
    img = Image.new("RGBA", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    # 2x Fonts
    font_title      = load_font(22 * SCALE, bold=True)
    font_subtitle   = load_font(14 * SCALE)
    font_col_header = load_font(13 * SCALE, bold=True)
    font_row_text   = load_font(15 * SCALE, bold=False)
    font_row_bold   = load_font(15 * SCALE, bold=True)
    font_footer     = load_font(13 * SCALE)

    # Header
    draw.text((35 * SCALE, 25 * SCALE), "КПЛ 2026", fill=red_accent_color, font=font_title)
    draw.text((35 * SCALE, 58 * SCALE), "Standings", fill=header_text_color, font=font_subtitle)

    # Column X offsets (scaled)
    col_x = {
        "place": 35 * SCALE,
        "team": 95 * SCALE,
        "P": 390 * SCALE,
        "M": 460 * SCALE,
        "W": 530 * SCALE,
        "T": 600 * SCALE,
        "L": 670 * SCALE,
        "GF": 740 * SCALE,
        "GA": 810 * SCALE,
        "GD": 880 * SCALE,
        "%": 950 * SCALE,
        "form": 1020 * SCALE
    }

    # Column Headers
    y_hdr = table_top - 28 * SCALE
    draw.text((col_x["place"], y_hdr), "Standings", fill=header_text_color, font=font_col_header)
    for col in ["P", "M", "W", "T", "L", "GF", "GA", "GD", "%"]:
        draw.text((col_x[col], y_hdr), col, fill=header_text_color, font=font_col_header, anchor="mm")
    draw.text((col_x["form"] + 30 * SCALE, y_hdr), "Latest Results", fill=header_text_color, font=font_col_header, anchor="mm")

    # Separator line
    draw.line([(30 * SCALE, table_top - 10 * SCALE), (width - 30 * SCALE, table_top - 10 * SCALE)], fill=(45, 45, 52), width=1 * SCALE)

    # Rows
    y_curr = table_top
    for i, s in enumerate(standings, 1):
        bg = row_bg_1 if i % 2 == 1 else row_bg_2
        draw.rectangle([(30 * SCALE, y_curr), (width - 30 * SCALE, y_curr + row_height - 2 * SCALE)], fill=bg)

        y_center = y_curr + (row_height // 2)

        # Place number
        place_str = str(i)
        draw.text((col_x["place"] + 10 * SCALE, y_center), place_str, fill=primary_text_color, font=font_row_bold, anchor="mm")

        # Team Logo with White Circular Container Badge
        team_name = s.get("team_name") or f"Команда {i}"
        logo_filename = TEAM_LOGO_MAP.get(team_name, "default.png")
        logo_path = os.path.join(LOGOS_DIR, logo_filename)

        badge_diameter = 30 * SCALE
        badge_x = col_x["team"]
        badge_y = y_center - (badge_diameter // 2)

        # White circle background
        draw.ellipse([badge_x, badge_y, badge_x + badge_diameter, badge_y + badge_diameter], fill=(255, 255, 255))

        # Fit emblem centered
        if os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path).convert("RGBA")
                inner_size = 24 * SCALE
                logo_img = logo_img.resize((inner_size, inner_size), Image.Resampling.LANCZOS)
                offset_x = badge_x + ((badge_diameter - inner_size) // 2)
                offset_y = badge_y + ((badge_diameter - inner_size) // 2)
                img.paste(logo_img, (offset_x, offset_y), logo_img)
            except Exception:
                pass

        # Team Name
        draw.text((col_x["team"] + 42 * SCALE, y_center), team_name, fill=primary_text_color, font=font_row_bold, anchor="lm")

        # Stat Values
        p = s.get("points", 0)
        w = s.get("wins", 0)
        t = s.get("draws", 0)
        l = s.get("losses", 0)
        m = s.get("played", w + t + l)
        gf = s.get("goals_scored", 0)
        ga = s.get("goals_conceded", 0)
        gd = gf - ga
        rating = (p / (m * 3) * 100.0) if m > 0 else 0.0
        rating_str = f"{rating:.1f}"

        draw.text((col_x["P"], y_center), str(p), fill=primary_text_color, font=font_row_bold, anchor="mm")
        draw.text((col_x["M"], y_center), str(m), fill=muted_text_color, font=font_row_text, anchor="mm")
        draw.text((col_x["W"], y_center), str(w), fill=muted_text_color, font=font_row_text, anchor="mm")
        draw.text((col_x["T"], y_center), str(t), fill=muted_text_color, font=font_row_text, anchor="mm")
        draw.text((col_x["L"], y_center), str(l), fill=muted_text_color, font=font_row_text, anchor="mm")
        draw.text((col_x["GF"], y_center), str(gf), fill=muted_text_color, font=font_row_text, anchor="mm")
        draw.text((col_x["GA"], y_center), str(ga), fill=muted_text_color, font=font_row_text, anchor="mm")

        gd_str = f"+{gd}" if gd > 0 else str(gd)
        draw.text((col_x["GD"], y_center), gd_str, fill=muted_text_color, font=font_row_text, anchor="mm")
        draw.text((col_x["%"], y_center), rating_str, fill=muted_text_color, font=font_row_text, anchor="mm")

        # Form Dots (5 dots)
        uid = s.get("telegram_id")
        user_form = form_map.get(uid, []) if uid else []
        dots = (['E'] * (5 - len(user_form))) + user_form[-5:]

        dot_radius = 5 * SCALE
        start_x = col_x["form"]
        for d_idx, res in enumerate(dots):
            dx = start_x + (d_idx * 16 * SCALE)
            dy = y_center
            if res == 'W':
                fill_color = (34, 197, 94)   # Green #22C55E
            elif res == 'L':
                fill_color = (239, 68, 68)   # Red #EF4444
            elif res == 'D':
                fill_color = (156, 163, 175) # Gray #9CA3AF
            else:
                fill_color = (55, 65, 81)    # Muted dark #374151

            draw.ellipse([dx - dot_radius, dy - dot_radius, dx + dot_radius, dy + dot_radius], fill=fill_color)

        y_curr += row_height

    # Footer Legend
    y_footer = y_curr + 25 * SCALE
    legend_parts = [
        ("P", "Points"), ("M", "Matches"), ("W", "Wins"), ("T", "Ties"),
        ("L", "Losses"), ("GF", "Goals for"), ("GA", "Goals against"),
        ("GD", "Goals difference"), ("%", "Rating")
    ]

    x_leg = 35 * SCALE
    for code, desc in legend_parts:
        draw.text((x_leg, y_footer), code, fill=primary_text_color, font=font_row_bold)
        x_leg += draw.textlength(code, font=font_row_bold) + 4 * SCALE
        draw.text((x_leg, y_footer), desc, fill=header_text_color, font=font_footer)
        x_leg += draw.textlength(desc, font=font_footer) + 20 * SCALE

    # Resample down from 2x scale to 1x scale using LANCZOS
    resampled_img = img.resize((width_1x, height_1x), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    resampled_img.save(buffer, format="PNG", quality=95)
    buffer.seek(0)
    return buffer


if __name__ == "__main__":
    buf = generate_league_table_image()
    with open("test_league_table.png", "wb") as f:
        f.write(buf.getvalue())
    print("✓ test_league_table.png generated successfully with 2x supersampling!")
