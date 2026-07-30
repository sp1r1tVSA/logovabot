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


def generate_cup_bracket_image(stage: str = "1/8") -> io.BytesIO:
    """
    Generate a 2x supersampled high-res graphic image of the KPL Cup stage bracket.
    Returns io.BytesIO PNG buffer.
    """
    series_list = database.get_cup_series_list(stage)

    stage_title_map = {
        '1/8': '1/8 ФИНАЛА',
        '1/4': '1/4 ФИНАЛА',
        '1/2': '1/2 ФИНАЛА',
        'final': '🏆 ФИНАЛ КУБКА'
    }
    stage_name = stage_title_map.get(stage, f"{stage.upper()} ФИНАЛА")

    num_series = len(series_list) if series_list else 1

    # Base Dimensions
    width_1x = 1120
    card_height_1x = 115
    header_height_1x = 110
    card_gap_1x = 15

    # 2 Columns for 1/8 and 1/4; 1 Column for 1/2 and final
    cols = 2 if num_series > 1 else 1
    rows = (num_series + cols - 1) // cols

    content_height_1x = rows * card_height_1x + (rows - 1) * card_gap_1x
    height_1x = header_height_1x + content_height_1x + 70

    # 2x Scaled Canvas
    width = width_1x * SCALE
    height = height_1x * SCALE

    # Colors
    bg_color = (20, 20, 22)           # #141416
    card_bg = (26, 26, 30)            # #1A1A1E
    card_border = (42, 42, 48)        # #2A2A30
    winner_gold = (245, 158, 11)      # #F59E0B
    red_accent = (239, 68, 68)        # #EF4444
    text_primary = (255, 255, 255)
    text_muted = (156, 163, 175)     # #9CA3AF

    img = Image.new("RGBA", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    font_title = load_font(24 * SCALE, bold=True)
    font_subtitle = load_font(13 * SCALE)
    font_card_title = load_font(12 * SCALE, bold=True)
    font_team = load_font(15 * SCALE, bold=True)
    font_score = load_font(18 * SCALE, bold=True)
    font_subtext = load_font(11 * SCALE)

    # Draw Header
    draw.text((35 * SCALE, 22 * SCALE), "КУБОК КПЛ 2026", fill=red_accent, font=font_title)
    draw.text((35 * SCALE, 58 * SCALE), f"{stage_name}  •  СЕРИИ ДО 2-Х ПОБЕД (BEST-OF-3)", fill=text_muted, font=font_subtitle)

    # Draw Header Accent Line
    draw.rectangle([35 * SCALE, 92 * SCALE, (width_1x - 35) * SCALE, 94 * SCALE], fill=card_border)

    # Draw Cards
    start_y = 115 * SCALE
    card_w_1x = (width_1x - (70) - (card_gap_1x if cols > 1 else 0)) // cols
    card_w = card_w_1x * SCALE
    card_h = card_height_1x * SCALE

    for i, s in enumerate(series_list):
        r = i // cols
        c = i % cols

        cx = (35 * SCALE) + c * (card_w + (card_gap_1x * SCALE))
        cy = start_y + r * (card_h + (card_gap_1x * SCALE))

        is_completed = s.get("status") == "completed"
        border_col = winner_gold if is_completed else card_border

        # Card Box
        draw.rectangle([cx, cy, cx + card_w, cy + card_h], fill=card_bg, outline=border_col, width=2 * SCALE)

        # Card Header: Series Num & Winner status
        s_num = s.get("series_num", i + 1)
        t1_name = s.get("team1_name", "TBD")
        t2_name = s.get("team2_name", "TBD")
        w1 = s.get("team1_wins", 0)
        w2 = s.get("team2_wins", 0)
        winner_name = s.get("winner_name", "")

        header_text = f"СЕРИЯ {s_num}" if stage != "final" else "🏆 ФИНАЛЬНАЯ СЕРИЯ"
        draw.text((cx + 15 * SCALE, cy + 12 * SCALE), header_text, fill=text_muted, font=font_card_title)

        if is_completed and winner_name:
            draw.text((cx + card_w - 15 * SCALE, cy + 12 * SCALE), f"🏆 {winner_name}", fill=winner_gold, font=font_card_title, anchor="ra")

        # Teams & Series Score
        mid_y = cy + 50 * SCALE

        # Team 1 (Left)
        draw.text((cx + 20 * SCALE, mid_y), t1_name, fill=text_primary if w1 >= w2 else text_muted, font=font_team, anchor="lm")

        # Score (Center)
        score_text = f"{w1}  :  {w2}"
        draw.text((cx + card_w // 2, mid_y), score_text, fill=winner_gold if is_completed else text_primary, font=font_score, anchor="mm")

        # Team 2 (Right)
        draw.text((cx + card_w - 20 * SCALE, mid_y), t2_name, fill=text_primary if w2 >= w1 else text_muted, font=font_team, anchor="rm")

        # Individual Matches Breakdown
        matches = s.get("matches", [])
        match_str_list = []
        for m in matches:
            g_n = m.get("game_num_in_series", 1)
            p1_s = m.get("player1_score", 0)
            p2_s = m.get("player2_score", 0)
            st = m.get("status")
            if st == "confirmed":
                match_str_list.append(f"И{g_n}: {p1_s}-{p2_s}")
            else:
                match_str_list.append(f"И{g_n}: ⏳")

        matches_line = "   •   ".join(match_str_list) if match_str_list else "Ожидается начало серии"
        draw.text((cx + card_w // 2, cy + card_h - 16 * SCALE), matches_line, fill=text_muted, font=font_subtext, anchor="mm")

    # Footer
    footer_y = height - 35 * SCALE
    draw.text((35 * SCALE, footer_y), "ОФИЦИАЛЬНЫЙ БОТ КПЛ • @LOGOVABOT", fill=text_muted, font=font_subtitle)

    # Resample LANCZOS to 1x
    resampled = img.resize((width_1x, height_1x), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    resampled.save(buf, format="PNG", quality=95)
    buf.seek(0)
    return buf


if __name__ == "__main__":
    buf = generate_league_table_image()
    with open("test_league_table.png", "wb") as f:
        f.write(buf.getvalue())
    print("✓ test_league_table.png generated successfully with 2x supersampling!")
