import os
import io
import database
import player_photos
from PIL import Image, ImageDraw, ImageFont
from table_generator import TEAM_LOGO_MAP, load_font

BASE_DIR = os.path.dirname(__file__)
LOGOS_DIR = os.path.join(BASE_DIR, "assets", "logos")

# Card dimensions
CARD_WIDTH  = 750
CARD_PAD    = 32

# Colors
BG_COLOR       = (20, 20, 22)        # #141416
SURFACE_COLOR  = (26, 26, 30)        # #1A1A1E
BORDER_COLOR   = (45, 45, 52)        # #2D2D34
WHITE          = (255, 255, 255)
MUTED          = (156, 163, 175)     # #9CA3AF
GOAL_COLOR     = (34, 197, 94)       # #22C55E  green
ASSIST_COLOR   = (59, 130, 246)      # #3B82F6  blue
TEXT_SECONDARY = (209, 213, 219)     # #D1D5DB

# Medal / Rank colors
GOLD_BG    = (234, 179, 8, 40)       # #EAB308
GOLD_TEXT  = (250, 204, 21)
SILVER_BG  = (148, 163, 184, 40)    # #94A3B8
SILVER_TEXT= (226, 232, 240)
BRONZE_BG  = (217, 119, 6, 40)      # #D97706
BRONZE_TEXT= (251, 146, 60)


def _draw_rounded_rect(draw: ImageDraw.ImageDraw, xy: tuple, radius: int, fill: tuple, outline: tuple | None = None):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline)


def generate_top_stats_image(mode: str = "goals", limit: int = 10) -> io.BytesIO:
    """
    Generate a high-res graphic image for Top Scorers ("goals") or Top Assisters ("assists").

    Returns io.BytesIO PNG buffer.
    """
    if mode == "goals":
        title_text = "ТОП БОМБАРДИРОВ"
        subtitle_text = "ГОНКА БОМБАРДИРОВ  •  СЕЗОН 2026"
        stat_color = GOAL_COLOR
        raw_data = database.get_top_scorers(limit)
    else:
        title_text = "ТОП АССИСТЕНТОВ"
        subtitle_text = "ЛУЧШИЕ АССИСТЕНТЫ  •  СЕЗОН 2026"
        stat_color = ASSIST_COLOR
        raw_data = database.get_top_assists(limit)

    # ── Fonts ──────────────────────────────────────────────────────────────
    font_title    = load_font(28, bold=True)
    font_subtitle = load_font(12, bold=True)
    font_rank     = load_font(16, bold=True)
    font_player   = load_font(18, bold=True)
    font_team     = load_font(13)
    font_stat_val = load_font(22, bold=True)
    font_footer   = load_font(12)

    # ── Geometry ───────────────────────────────────────────────────────────
    HEADER_H   = 90
    ROW_H      = 64
    ROW_GAP    = 10
    FOOTER_H   = 40
    PHOTO_D    = 52
    BADGE_D    = 22

    num_rows = len(raw_data)
    rows_h = num_rows * (ROW_H + ROW_GAP) if num_rows > 0 else 60

    total_h = CARD_PAD + HEADER_H + rows_h + FOOTER_H + CARD_PAD

    img  = Image.new("RGBA", (CARD_WIDTH, total_h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    y = CARD_PAD

    # ══════════════════════════════════════════════════════════════════════
    # 1. HEADER
    # ══════════════════════════════════════════════════════════════════════
    # Accent top border bar
    _draw_rounded_rect(draw, (CARD_PAD, y, CARD_PAD + 6, y + 54), radius=3, fill=stat_color)

    # Title & Subtitle
    draw.text((CARD_PAD + 20, y + 2), title_text, fill=WHITE, font=font_title)
    draw.text((CARD_PAD + 20, y + 36), subtitle_text, fill=MUTED, font=font_subtitle)

    # Top right season pill
    pill_text = "КПЛ 2026"
    pw = int(draw.textlength(pill_text, font=font_subtitle))
    px = CARD_WIDTH - CARD_PAD - pw - 20
    _draw_rounded_rect(draw, (px - 10, y + 10, px + pw + 10, y + 36), radius=8, fill=SURFACE_COLOR, outline=BORDER_COLOR)
    draw.text((px, y + 15), pill_text, fill=WHITE, font=font_subtitle)

    y += HEADER_H

    # ══════════════════════════════════════════════════════════════════════
    # 2. PLAYER ROWS
    # ══════════════════════════════════════════════════════════════════════
    if num_rows == 0:
        empty_text = "Пока нет данных по турниру"
        ew = int(draw.textlength(empty_text, font=font_player))
        draw.text(((CARD_WIDTH - ew) // 2, y + 20), empty_text, fill=MUTED, font=font_player)
        y += 60
    else:
        for idx, row_data in enumerate(raw_data, start=1):
            player_name = row_data.get("player_name", "—")
            team_name   = row_data.get("team_name", "—")
            stat_value  = row_data.get("total_goals" if mode == "goals" else "total_assists", 0)

            row_x0 = CARD_PAD
            row_x1 = CARD_WIDTH - CARD_PAD
            row_y0 = y
            row_y1 = y + ROW_H

            # Row container card
            _draw_rounded_rect(draw, (row_x0, row_y0, row_x1, row_y1), radius=12, fill=SURFACE_COLOR, outline=BORDER_COLOR)

            # ── Rank Badge (Left) ──────────────────────────────────────────
            rank_w = 44
            rank_h = 34
            rank_x = row_x0 + 14
            rank_y = row_y0 + (ROW_H - rank_h) // 2

            if idx == 1:
                r_fill, r_text_col, r_str = GOLD_BG, GOLD_TEXT, "#1"
            elif idx == 2:
                r_fill, r_text_col, r_str = SILVER_BG, SILVER_TEXT, "#2"
            elif idx == 3:
                r_fill, r_text_col, r_str = BRONZE_BG, BRONZE_TEXT, "#3"
            else:
                r_fill, r_text_col, r_str = (35, 35, 42), MUTED, f"#{idx}"

            _draw_rounded_rect(draw, (rank_x, rank_y, rank_x + rank_w, rank_y + rank_h), radius=8, fill=r_fill)
            rw = int(draw.textlength(r_str, font=font_rank))
            draw.text((rank_x + (rank_w - rw) // 2, rank_y + (rank_h - 20) // 2), r_str, fill=r_text_col, font=font_rank)

            # ── Player Photo Portrait ──────────────────────────────────────
            photo_x = rank_x + rank_w + 16
            photo_y = row_y0 + (ROW_H - PHOTO_D) // 2

            photo_path = player_photos.get_photo_path(player_name, team_name)

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
                font_init = load_font(20, bold=True)
                init_w = int(ph_draw.textlength(initials, font=font_init))
                ph_draw.text(((PHOTO_D - init_w) // 2, (PHOTO_D - 24) // 2), initials, fill=WHITE, font=font_init)
                img.paste(placeholder, (photo_x, photo_y), mask)

            draw.ellipse([photo_x - 1, photo_y - 1, photo_x + PHOTO_D + 1, photo_y + PHOTO_D + 1], outline=BORDER_COLOR, width=1)

            # ── Club Badge (Overlay on portrait) ───────────────────────────
            badge_x = photo_x + PHOTO_D - BADGE_D // 2 - 2
            badge_y = photo_y + PHOTO_D - BADGE_D // 2 - 2
            draw.ellipse([badge_x, badge_y, badge_x + BADGE_D, badge_y + BADGE_D], fill=WHITE)

            logo_filename = TEAM_LOGO_MAP.get(team_name, "default.png")
            logo_path = os.path.join(LOGOS_DIR, logo_filename)
            if os.path.exists(logo_path):
                try:
                    logo_img = Image.open(logo_path).convert("RGBA")
                    inner = BADGE_D - 4
                    logo_img = logo_img.resize((inner, inner), Image.Resampling.LANCZOS)
                    off_x = badge_x + (BADGE_D - inner) // 2
                    off_y = badge_y + (BADGE_D - inner) // 2
                    img.paste(logo_img, (off_x, off_y), logo_img)
                except Exception:
                    pass

            # ── Player Name & Team Label ───────────────────────────────────
            name_x = photo_x + PHOTO_D + 16
            p_name_y = row_y0 + 12

            # Dynamic font scaling for long names
            f_pname = font_player
            max_nw = row_x1 - name_x - 110
            if draw.textlength(player_name, font=f_pname) > max_nw:
                f_pname = load_font(15, bold=True)

            draw.text((name_x, p_name_y), player_name, fill=WHITE, font=f_pname)
            draw.text((name_x, p_name_y + 24), team_name, fill=MUTED, font=font_team)

            # ── Stat Count Badge (Right Pill) ─────────────────────────────
            val_str = f"{stat_value}"
            val_w = int(draw.textlength(val_str, font=font_stat_val))

            pill_w = max(50, val_w + 24)
            pill_h = 36
            pill_x = row_x1 - 16 - pill_w
            pill_y = row_y0 + (ROW_H - pill_h) // 2

            bg_pill_col = (stat_color[0], stat_color[1], stat_color[2], 30)
            _draw_rounded_rect(draw, (pill_x, pill_y, pill_x + pill_w, pill_y + pill_h), radius=8, fill=bg_pill_col, outline=stat_color)

            sw = int(draw.textlength(val_str, font=font_stat_val))
            draw.text((pill_x + (pill_w - sw) // 2, pill_y + (pill_h - 26) // 2), val_str, fill=stat_color, font=font_stat_val)

            y += ROW_H + ROW_GAP

    # ══════════════════════════════════════════════════════════════════════
    # 3. FOOTER
    # ══════════════════════════════════════════════════════════════════════
    y += 10
    draw.line([(CARD_PAD, y), (CARD_WIDTH - CARD_PAD, y)], fill=BORDER_COLOR, width=1)
    footer_str = "КПЛ 2026  •  Официальная статистика турнира"
    fw = int(draw.textlength(footer_str, font=font_footer))
    draw.text(((CARD_WIDTH - fw) // 2, y + 10), footer_str, fill=MUTED, font=font_footer)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


if __name__ == "__main__":
    buf1 = generate_top_stats_image("goals", 10)
    with open("test_top_scorers.png", "wb") as f:
        f.write(buf1.getvalue())
    print("Saved test_top_scorers.png")

    buf2 = generate_top_stats_image("assists", 10)
    with open("test_top_assisters.png", "wb") as f:
        f.write(buf2.getvalue())
    print("Saved test_top_assisters.png")
