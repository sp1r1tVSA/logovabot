import os
import io
import database
from PIL import Image, ImageDraw, ImageFont

from pathlib import Path

from services.graphics.division_theme import draw_division_badge, resolve_theme

# Project root directory (services/graphics -> services -> root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = str(PROJECT_ROOT)
LOGOS_DIR = str(PROJECT_ROOT / "assets" / "logos")

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
    "Будë Глимпт": "bodo_glimt.png",
    "Будё Глимпт": "bodo_glimt.png",
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

# Also ensure lowercase keys are directly present
for _k, _v in list(TEAM_LOGO_MAP.items()):
    TEAM_LOGO_MAP[_k.lower()] = _v


def get_team_logo_filename(team_name: str) -> str | None:
    """Case-insensitive and alias-aware club logo filename lookup."""
    if not team_name:
        return None
    canon = database.resolve_team_name(team_name) or team_name
    t_clean = canon.strip().lower()
    for k, v in TEAM_LOGO_MAP.items():
        if k.lower() == t_clean:
            return v
    if "расинг" in t_clean or "racing" in t_clean:
        return "racing.png"
    if "аек" in t_clean or "aek" in t_clean:
        return "aek.png"
    if "фейено" in t_clean or "feyen" in t_clean:
        return "feyenoord.png"
    if "буд" in t_clean or "bodo" in t_clean:
        return "bodo_glimt.png"
    if "бока" in t_clean or "boca" in t_clean:
        return "boca_juniors.png"
    if "ривер" in t_clean or "river" in t_clean:
        return "river_plate.png"
    if "копен" in t_clean or "copen" in t_clean:
        return "copenhagen.png"
    if "спортинг" in t_clean or "sporting" in t_clean:
        return "sporting.png"
    if "рейнджер" in t_clean or "ranger" in t_clean:
        return "rangers.png"
    if "брюг" in t_clean or "brugg" in t_clean:
        return "brugge.png"
    if "браг" in t_clean or "braga" in t_clean:
        return "braga.png"
    if "аякс" in t_clean or "ajax" in t_clean:
        return "ajax.png"
    if "псв" in t_clean or "psv" in t_clean:
        return "psv.png"
    if "порт" in t_clean or "porto" in t_clean:
        return "porto.png"
    if "селтик" in t_clean or "celtic" in t_clean:
        return "celtic.png"
    if "бенфик" in t_clean or "benfica" in t_clean:
        return "benfica.png"
    return None


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


def clean_and_prepare_logo(img: Image.Image) -> Image.Image:
    """Ensure logo is RGBA, transparent, and auto-cropped of empty margins."""
    img = img.convert("RGBA")
    w, h = img.size
    if w == 0 or h == 0:
        return img

    corners = [img.getpixel((0, 0)), img.getpixel((w - 1, 0)), img.getpixel((0, h - 1)), img.getpixel((w - 1, h - 1))]
    has_white_corner = any(c[0] > 240 and c[1] > 240 and c[2] > 240 and c[3] > 200 for c in corners)

    if has_white_corner:
        datas = img.getdata()
        new_data = []
        for item in datas:
            if item[0] > 245 and item[1] > 245 and item[2] > 245:
                new_data.append((255, 255, 255, 0))
            else:
                new_data.append(item)
        img.putdata(new_data)

    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)

    return img


def resize_logo_proportional(img: Image.Image, max_w: int, max_h: int) -> tuple[Image.Image, int, int]:
    """Proportionally resize logo to fit within max_w x max_h preserving aspect ratio without distortion."""
    w, h = img.size
    if w <= 0 or h <= 0:
        return img, max_w, max_h
    ratio = min(max_w / w, max_h / h)
    new_w = max(1, int(round(w * ratio)))
    new_h = max(1, int(round(h * ratio)))
    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    return resized, new_w, new_h


def generate_league_table_image(
    standings: list[dict] | None = None,
    form_map: dict[str, list[str]] | None = None,
    division_name: str | None = None,
    division_id: int | None = None,
) -> io.BytesIO:
    """
    Generate a 2x supersampled, high-res graphic image of the league table.
    Returns io.BytesIO PNG buffer.

    division_id selects the division accent colour; without it the theme falls
    back to division_name and then to the neutral default.
    """
    # division_id здесь не только про цвет: если данные не передали, тянуть их надо
    # тем же срезом, иначе получается таблица в цветах дивизиона с чужими строками.
    if standings is None:
        standings = database.get_standings(division_id=division_id)
    if form_map is None:
        form_map = database.get_teams_recent_form(limit=5, division_id=division_id)

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

    # Акцент дивизиона. Позиционные цвета строк (зона вылета, лидер, форма)
    # остаются семантическими и теме не подчиняются.
    theme = resolve_theme(division_id=division_id, division_name=division_name)
    accent_color = theme.accent

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
    font_badge      = load_font(12 * SCALE, bold=True)

    # Header
    title_str = f"СЕЗОН 2 • {division_name.upper()}" if division_name else "ТУРНИРНАЯ ТАБЛИЦА"
    subtitle_str = f"Турнирная таблица дивизиона {division_name}" if division_name else "Standings"
    draw.text((35 * SCALE, 25 * SCALE), title_str, fill=accent_color, font=font_title)
    draw.text((35 * SCALE, 58 * SCALE), subtitle_str, fill=header_text_color, font=font_subtitle)
    draw_division_badge(draw, theme, width - 35 * SCALE, 25 * SCALE, font_badge, SCALE)

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
        logo_filename = get_team_logo_filename(team_name) or "default.png"
        logo_path = os.path.join(LOGOS_DIR, logo_filename)

        badge_diameter = 30 * SCALE
        badge_x = col_x["team"]
        badge_y = y_center - (badge_diameter // 2)

        # White circle background
        draw.ellipse([badge_x, badge_y, badge_x + badge_diameter, badge_y + badge_diameter], fill=(255, 255, 255))

        # Fit emblem centered proportionally without distortion
        if os.path.exists(logo_path):
            try:
                raw_logo = Image.open(logo_path)
                clean_logo = clean_and_prepare_logo(raw_logo)
                inner_size = 24 * SCALE
                logo_img, lw, lh = resize_logo_proportional(clean_logo, inner_size, inner_size)
                offset_x = badge_x + ((badge_diameter - lw) // 2)
                offset_y = badge_y + ((badge_diameter - lh) // 2)
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
        team_n = s.get("team_name", "").lower().strip()
        user_form = form_map.get(team_n, []) if team_n else []
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




generate_table = generate_league_table_image


if __name__ == "__main__":
    buf = generate_league_table_image()
    with open("test_league_table.png", "wb") as f:
        f.write(buf.getvalue())
    print("✓ test_league_table.png generated successfully with 2x supersampling!")
