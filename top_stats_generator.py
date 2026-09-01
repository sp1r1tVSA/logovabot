import os
import io
import database
import player_photos
from PIL import Image, ImageDraw, ImageFont
from table_generator import TEAM_LOGO_MAP, load_font

BASE_DIR = os.path.dirname(__file__)
LOGOS_DIR = os.path.join(BASE_DIR, "assets", "logos")

# Base 2x Supersampled Canvas Dimensions
# All coordinate math is done at SCALE = 2 for ultra-sharp Retina rendering
SCALE = 2

WIDTH_1X   = 800
CARD_PAD_1X = 32

WIDTH   = WIDTH_1X * SCALE
PAD     = CARD_PAD_1X * SCALE

# ── Color Palette ──────────────────────────────────────────────────────────
BG_COLOR        = (18, 18, 20)        # #121214
SURFACE_COLOR   = (28, 28, 34)        # #1C1C22
BORDER_COLOR    = (50, 50, 60)        # #32323C
WHITE           = (255, 255, 255)
MUTED           = (160, 168, 180)     # #A0A8B4
TEXT_SECONDARY  = (210, 215, 222)

# Goal theme colors
GOAL_COLOR      = (34, 197, 94)       # #22C55E
GOAL_BG         = (18, 48, 30)        # #12301E
GOAL_BORDER     = (34, 197, 94)

# Assist theme colors
ASSIST_COLOR    = (59, 130, 246)      # #3B82F6
ASSIST_BG       = (18, 38, 70)        # #122646
ASSIST_BORDER   = (59, 130, 246)

# Medal colors (Solid high-contrast)
GOLD_BG         = (245, 190, 11)      # #F5BE0B
GOLD_TEXT       = (20, 20, 20)
SILVER_BG       = (200, 210, 225)     # #C8D2E1
SILVER_TEXT     = (20, 20, 20)
BRONZE_BG       = (210, 125, 45)      # #D27D2D
BRONZE_TEXT     = (255, 255, 255)
OTHER_RANK_BG   = (38, 38, 48)
OTHER_RANK_TEXT = (160, 168, 180)


def _draw_rounded_rect(draw: ImageDraw.ImageDraw, xy: tuple, radius: int, fill: tuple, outline: tuple | None = None, width: int = 1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def generate_top_stats_image(mode: str = "goals", limit: int = 10, tournament_type: str = "league") -> io.BytesIO:
    """
    Generate a high-res graphic image for Top Scorers ("goals") or Top Assisters ("assists")
    with 2x supersampling for razor-sharp typography and borders.

    Returns io.BytesIO PNG buffer.
    """
    is_cup = (tournament_type == "cup")
    sub_suffix = "КУБОК КПЛ 2026" if is_cup else "СЕЗОН 2026"

    if mode == "goals":
        title_text = "ТОП БОМБАРДИРОВ КУБКА" if is_cup else "ТОП БОМБАРДИРОВ"
        subtitle_text = f"ГОНКА БОМБАРДИРОВ  •  {sub_suffix}"
        stat_label_str = "ГОЛОВ"
        stat_color = GOAL_COLOR
        badge_bg = GOAL_BG
        badge_border = GOAL_BORDER
        raw_data = database.get_cup_top_scorers(limit) if is_cup else database.get_top_scorers(limit)
    else:
        title_text = "ТОП АССИСТЕНТОВ КУБКА" if is_cup else "ТОП АССИСТЕНТОВ"
        subtitle_text = f"ЛУЧШИЕ АССИСТЕНТЫ  •  {sub_suffix}"
        stat_label_str = "ПАСОВ"
        stat_color = ASSIST_COLOR
        badge_bg = ASSIST_BG
        badge_border = ASSIST_BORDER
        raw_data = database.get_cup_top_assists(limit) if is_cup else database.get_top_assists(limit)

    # ── 2x Scaled Fonts ────────────────────────────────────────────────────
    font_title    = load_font(26 * SCALE, bold=True)
    font_subtitle = load_font(12 * SCALE, bold=True)
    font_rank     = load_font(16 * SCALE, bold=True)
    font_player   = load_font(18 * SCALE, bold=True)
    font_team     = load_font(13 * SCALE)
    font_stat_val = load_font(22 * SCALE, bold=True)
    font_stat_lbl = load_font(10 * SCALE, bold=True)
    font_footer   = load_font(11 * SCALE)

    # ── 2x Scaled Geometry ─────────────────────────────────────────────────
    HEADER_H   = 90 * SCALE
    ROW_H      = 68 * SCALE
    ROW_GAP    = 10 * SCALE
    FOOTER_H   = 44 * SCALE
    PHOTO_D    = 52 * SCALE
    BADGE_D    = 24 * SCALE

    num_rows = len(raw_data)
    rows_h = num_rows * (ROW_H + ROW_GAP) if num_rows > 0 else 70 * SCALE

    total_h = PAD + HEADER_H + rows_h + FOOTER_H + PAD

    # Create 2x canvas
    img  = Image.new("RGBA", (WIDTH, total_h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    y = PAD

    # ══════════════════════════════════════════════════════════════════════
    # 1. HEADER
    # ══════════════════════════════════════════════════════════════════════
    # Left vertical accent bar
    bar_w = 6 * SCALE
    bar_h = 52 * SCALE
    _draw_rounded_rect(draw, (PAD, y, PAD + bar_w, y + bar_h), radius=3 * SCALE, fill=stat_color)

    # Title & Subtitle
    draw.text((PAD + 18 * SCALE, y + 2 * SCALE), title_text, fill=WHITE, font=font_title)
    draw.text((PAD + 18 * SCALE, y + 34 * SCALE), subtitle_text, fill=MUTED, font=font_subtitle)

    # Top right season badge pill
    pill_text = "КПЛ 2026"
    pw = int(draw.textlength(pill_text, font=font_subtitle))
    px = WIDTH - PAD - pw - 24 * SCALE
    _draw_rounded_rect(
        draw,
        (px - 12 * SCALE, y + 8 * SCALE, px + pw + 12 * SCALE, y + 36 * SCALE),
        radius=8 * SCALE, fill=SURFACE_COLOR, outline=BORDER_COLOR, width=1 * SCALE
    )
    draw.text((px, y + 14 * SCALE), pill_text, fill=WHITE, font=font_subtitle)

    y += HEADER_H

    # ══════════════════════════════════════════════════════════════════════
    # 2. PLAYER ROWS
    # ══════════════════════════════════════════════════════════════════════
    if num_rows == 0:
        empty_text = "Пока нет данных по турниру"
        ew = int(draw.textlength(empty_text, font=font_player))
        draw.text(((WIDTH - ew) // 2, y + 20 * SCALE), empty_text, fill=MUTED, font=font_player)
        y += 70 * SCALE
    else:
        for idx, row_data in enumerate(raw_data, start=1):
            player_name = row_data.get("player_name", "—")
            team_name   = row_data.get("team_name", "—")
            stat_value  = row_data.get("total_goals" if mode == "goals" else "total_assists", 0)

            row_x0 = PAD
            row_x1 = WIDTH - PAD
            row_y0 = y
            row_y1 = y + ROW_H

            # Row card background
            _draw_rounded_rect(
                draw, (row_x0, row_y0, row_x1, row_y1),
                radius=12 * SCALE, fill=SURFACE_COLOR, outline=BORDER_COLOR, width=1 * SCALE
            )

            # ── Rank Badge (Left) ──────────────────────────────────────────
            rank_w = 48 * SCALE
            rank_h = 36 * SCALE
            rank_x = row_x0 + 14 * SCALE
            rank_y = row_y0 + (ROW_H - rank_h) // 2

            if idx == 1:
                r_fill, r_text_col, r_str = GOLD_BG, GOLD_TEXT, "#1"
            elif idx == 2:
                r_fill, r_text_col, r_str = SILVER_BG, SILVER_TEXT, "#2"
            elif idx == 3:
                r_fill, r_text_col, r_str = BRONZE_BG, BRONZE_TEXT, "#3"
            else:
                r_fill, r_text_col, r_str = OTHER_RANK_BG, OTHER_RANK_TEXT, f"#{idx}"

            _draw_rounded_rect(draw, (rank_x, rank_y, rank_x + rank_w, rank_y + rank_h), radius=8 * SCALE, fill=r_fill)
            rw = int(draw.textlength(r_str, font=font_rank))
            draw.text((rank_x + (rank_w - rw) // 2, rank_y + (rank_h - 22 * SCALE) // 2), r_str, fill=r_text_col, font=font_rank)

            # ── Player Photo Portrait ──────────────────────────────────────
            photo_x = rank_x + rank_w + 16 * SCALE
            photo_y = row_y0 + (ROW_H - PHOTO_D) // 2

            photo_path = player_photos.get_player_photo(player_name, team_name)

            mask = Image.new("L", (PHOTO_D, PHOTO_D), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse([0, 0, PHOTO_D, PHOTO_D], fill=255)

            if photo_path:
                try:
                    portrait = Image.open(photo_path).convert("RGBA")
                    portrait = portrait.resize((PHOTO_D, PHOTO_D), Image.Resampling.LANCZOS)
                    img.paste(portrait, (photo_x, photo_y), mask)
                except Exception:
                    photo_path = None

            if not photo_path:
                placeholder = Image.new("RGBA", (PHOTO_D, PHOTO_D), (55, 65, 81, 255))
                ph_draw = ImageDraw.Draw(placeholder)
                initials = "".join(w[0].upper() for w in player_name.split()[:2]) if player_name else "?"
                font_init = load_font(20 * SCALE, bold=True)
                init_w = int(ph_draw.textlength(initials, font=font_init))
                ph_draw.text(((PHOTO_D - init_w) // 2, (PHOTO_D - 26 * SCALE) // 2), initials, fill=WHITE, font=font_init)
                img.paste(placeholder, (photo_x, photo_y), mask)

            # Outer ring around photo
            draw.ellipse([photo_x - 1, photo_y - 1, photo_x + PHOTO_D + 1, photo_y + PHOTO_D + 1], outline=BORDER_COLOR, width=2 * SCALE)

            # ── Club Badge (Overlay) ───────────────────────────────────────
            badge_x = photo_x + PHOTO_D - BADGE_D // 2 - 2 * SCALE
            badge_y = photo_y + PHOTO_D - BADGE_D // 2 - 2 * SCALE
            draw.ellipse([badge_x, badge_y, badge_x + BADGE_D, badge_y + BADGE_D], fill=WHITE)

            logo_filename = TEAM_LOGO_MAP.get(team_name, "default.png")
            logo_path = os.path.join(LOGOS_DIR, logo_filename)
            if os.path.exists(logo_path):
                try:
                    logo_img = Image.open(logo_path).convert("RGBA")
                    inner = BADGE_D - 4 * SCALE
                    logo_img = logo_img.resize((inner, inner), Image.Resampling.LANCZOS)
                    off_x = badge_x + (BADGE_D - inner) // 2
                    off_y = badge_y + (BADGE_D - inner) // 2
                    img.paste(logo_img, (off_x, off_y), logo_img)
                except Exception:
                    pass

            # ── Player Name & Team Label ───────────────────────────────────
            name_x = photo_x + PHOTO_D + 18 * SCALE
            p_name_y = row_y0 + 12 * SCALE

            # Dynamic font scaling for long names
            f_pname = font_player
            max_nw = row_x1 - name_x - 140 * SCALE
            if draw.textlength(player_name, font=f_pname) > max_nw:
                f_pname = load_font(15 * SCALE, bold=True)

            draw.text((name_x, p_name_y), player_name, fill=WHITE, font=f_pname)
            draw.text((name_x, p_name_y + 26 * SCALE), team_name, fill=MUTED, font=font_team)

            # ── Stat Count Pill Badge (Right) ──────────────────────────────
            val_str = f"{stat_value}"
            val_w = int(draw.textlength(val_str, font=font_stat_val))

            pill_w = max(60 * SCALE, val_w + 30 * SCALE)
            pill_h = 40 * SCALE
            pill_x = row_x1 - 16 * SCALE - pill_w
            pill_y = row_y0 + (ROW_H - pill_h) // 2

            _draw_rounded_rect(
                draw,
                (pill_x, pill_y, pill_x + pill_w, pill_y + pill_h),
                radius=8 * SCALE, fill=badge_bg, outline=badge_border, width=1 * SCALE
            )

            # Stat number centered
            text_x = pill_x + (pill_w - val_w) // 2
            draw.text((text_x, pill_y + (pill_h - 26 * SCALE) // 2), val_str, fill=WHITE, font=font_stat_val)
            y += ROW_H + ROW_GAP

    # ══════════════════════════════════════════════════════════════════════
    # 3. FOOTER
    # ══════════════════════════════════════════════════════════════════════
    y += 8 * SCALE
    draw.line([(PAD, y), (WIDTH - PAD, y)], fill=BORDER_COLOR, width=1 * SCALE)
    footer_str = "КПЛ 2026  •  Официальная статистика турнира"
    fw = int(draw.textlength(footer_str, font=font_footer))
    draw.text(((WIDTH - fw) // 2, y + 12 * SCALE), footer_str, fill=MUTED, font=font_footer)

    # Downsample from 2x scale to 1x scale using LANCZOS for 4K Retina antialiasing
    final_w = WIDTH_1X
    final_h = total_h // SCALE
    resampled_img = img.resize((final_w, final_h), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    resampled_img.save(buf, format="PNG")
    buf.seek(0)
    return buf


if __name__ == "__main__":
    buf1 = generate_top_stats_image("goals", 10)
    with open("test_top_scorers.png", "wb") as f:
        f.write(buf1.getvalue())
    print("Saved high-res test_top_scorers.png")

    buf2 = generate_top_stats_image("assists", 10)
    with open("test_top_assisters.png", "wb") as f:
        f.write(buf2.getvalue())
    print("Saved high-res test_top_assisters.png")
