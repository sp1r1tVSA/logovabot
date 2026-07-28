import os
import io
from PIL import Image, ImageDraw, ImageFont
from table_generator import TEAM_LOGO_MAP, load_font
import player_photos

BASE_DIR = os.path.dirname(__file__)
LOGOS_DIR = os.path.join(BASE_DIR, "assets", "logos")

SCALE = 2

# Card dimensions (1x base)
CARD_WIDTH_1X   = 680
CARD_PADDING_1X = 36

CARD_WIDTH   = CARD_WIDTH_1X * SCALE
CARD_PADDING = CARD_PADDING_1X * SCALE

# Colors (same dark theme)
BG_COLOR       = (20, 20, 22)        # #141416
SURFACE_COLOR  = (26, 26, 30)        # #1A1A1E
BORDER_COLOR   = (45, 45, 52)        # #2D2D34
RED_ACCENT     = (239, 68, 68)       # #EF4444
WHITE          = (255, 255, 255)
MUTED          = (156, 163, 175)     # #9CA3AF
GOAL_COLOR     = (34, 197, 94)       # #22C55E  green
ASSIST_COLOR   = (59, 130, 246)      # #3B82F6  blue
TEXT_SECONDARY = (209, 213, 219)     # #D1D5DB


def _draw_rounded_rect(draw: ImageDraw.ImageDraw, xy: tuple, radius: int, fill: tuple, outline: tuple | None = None, width: int = 1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def generate_player_card(stats: dict) -> io.BytesIO:
    """
    Generate a high-res 2x supersampled player stats card image.

    stats dict expected:
      {
        "player_name": str,
        "team_name":   str,
        "total_goals": int,
        "total_assists": int,
        "rounds": {round_number: {"goals": int, "assists": int}, ...}
      }

    Returns io.BytesIO PNG buffer.
    """
    player_name   = stats.get("player_name", "—")
    team_name     = stats.get("team_name", "—")
    total_goals   = stats.get("total_goals", 0)
    total_assists = stats.get("total_assists", 0)
    rounds: dict  = stats.get("rounds", {})

    # ── 2x Scaled Fonts ────────────────────────────────────────────────────
    font_player   = load_font(24 * SCALE, bold=True)
    font_team     = load_font(14 * SCALE)
    font_label    = load_font(12 * SCALE)
    font_big_num  = load_font(42 * SCALE, bold=True)
    font_stat_lbl = load_font(13 * SCALE, bold=True)
    font_round_hd = load_font(14 * SCALE, bold=True)
    font_round    = load_font(14 * SCALE)
    font_season   = load_font(13 * SCALE, bold=True)

    # ── Dynamic height ─────────────────────────────────────────────────────
    HEADER_H      = 100 * SCALE
    BIG_STATS_H   = 100 * SCALE
    ROUNDS_HEADER = 38 * SCALE
    ROW_H         = 40 * SCALE
    FOOTER_H      = 32 * SCALE
    SECTION_GAP   = 16 * SCALE

    num_rounds = len(rounds)
    rounds_section_h = (ROUNDS_HEADER + num_rounds * ROW_H) if num_rounds > 0 else 0
    no_rounds_h      = 44 * SCALE if num_rounds == 0 else 0

    total_h = (
        CARD_PADDING
        + HEADER_H
        + SECTION_GAP
        + BIG_STATS_H
        + SECTION_GAP
        + (rounds_section_h if num_rounds > 0 else no_rounds_h)
        + SECTION_GAP
        + FOOTER_H
        + CARD_PADDING
    )

    # ── Canvas ─────────────────────────────────────────────────────────────
    img  = Image.new("RGBA", (CARD_WIDTH, total_h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    y = CARD_PADDING

    # ══════════════════════════════════════════════════════════════════════
    # 1. HEADER  — player photo  |  name + team + club logo
    # ══════════════════════════════════════════════════════════════════════
    PHOTO_D = 80 * SCALE   # player portrait diameter
    BADGE_D = 32 * SCALE   # club logo badge diameter

    photo_x = CARD_PADDING
    photo_y = y + (HEADER_H - PHOTO_D) // 2

    # ── Player portrait ────────────────────────────────────────────────
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
        font_init = load_font(28 * SCALE, bold=True)
        init_w = int(ph_draw.textlength(initials, font=font_init))
        ph_draw.text(
            ((PHOTO_D - init_w) // 2, (PHOTO_D - 32 * SCALE) // 2),
            initials, fill=WHITE, font=font_init
        )
        img.paste(placeholder, (photo_x, photo_y), mask)

    draw.ellipse(
        [photo_x - 2 * SCALE, photo_y - 2 * SCALE, photo_x + PHOTO_D + 2 * SCALE, photo_y + PHOTO_D + 2 * SCALE],
        outline=BORDER_COLOR, width=2 * SCALE
    )

    # ── Club logo badge ────────────────────────────────────────────────
    badge_x = photo_x + PHOTO_D - BADGE_D // 2
    badge_y = photo_y + PHOTO_D - BADGE_D // 2

    draw.ellipse([badge_x, badge_y, badge_x + BADGE_D, badge_y + BADGE_D], fill=WHITE)

    logo_filename = TEAM_LOGO_MAP.get(team_name, "default.png")
    logo_path     = os.path.join(LOGOS_DIR, logo_filename)
    if os.path.exists(logo_path):
        try:
            logo_img  = Image.open(logo_path).convert("RGBA")
            inner     = BADGE_D - 6 * SCALE
            logo_img  = logo_img.resize((inner, inner), Image.Resampling.LANCZOS)
            off_x     = badge_x + (BADGE_D - inner) // 2
            off_y     = badge_y + (BADGE_D - inner) // 2
            img.paste(logo_img, (off_x, off_y), logo_img)
        except Exception:
            pass

    # ── Player name + team label ───────────────────────────────────────
    text_x = photo_x + PHOTO_D + 20 * SCALE
    name_y = y + 22 * SCALE
    max_name_w = CARD_WIDTH - CARD_PADDING - text_x - 120 * SCALE
    if draw.textlength(player_name, font=font_player) > max_name_w:
        font_player = load_font(20 * SCALE, bold=True)
        if draw.textlength(player_name, font=font_player) > max_name_w:
            font_player = load_font(17 * SCALE, bold=True)

    draw.text((text_x, name_y), player_name, fill=WHITE, font=font_player)

    team_y = name_y + 34 * SCALE
    draw.text((text_x, team_y), team_name, fill=MUTED, font=font_team)

    # "Сезон 2026" badge
    season_label = "Сезон 2026"
    sl_w = int(draw.textlength(season_label, font=font_season))
    sl_x = CARD_WIDTH - CARD_PADDING - sl_w - 16 * SCALE
    sl_y = y + 6 * SCALE
    _draw_rounded_rect(draw, (sl_x - 10 * SCALE, sl_y - 4 * SCALE, sl_x + sl_w + 10 * SCALE, sl_y + 24 * SCALE), radius=6 * SCALE, fill=SURFACE_COLOR, outline=BORDER_COLOR)
    draw.text((sl_x, sl_y), season_label, fill=MUTED, font=font_season)

    y += HEADER_H + SECTION_GAP

    draw.line([(CARD_PADDING, y), (CARD_WIDTH - CARD_PADDING, y)], fill=BORDER_COLOR, width=1 * SCALE)
    y += SECTION_GAP

    # ══════════════════════════════════════════════════════════════════════
    # 2. BIG STATS — Goals  |  Assists
    # ══════════════════════════════════════════════════════════════════════
    col_w    = (CARD_WIDTH - CARD_PADDING * 2) // 2
    stats_y0 = y
    stats_h  = BIG_STATS_H

    # Goals block
    goal_bg_x0 = CARD_PADDING
    goal_bg_x1 = CARD_PADDING + col_w - 8 * SCALE
    _draw_rounded_rect(draw, (goal_bg_x0, stats_y0, goal_bg_x1, stats_y0 + stats_h), radius=12 * SCALE, fill=SURFACE_COLOR, outline=BORDER_COLOR)

    g_num_str = str(total_goals)
    g_num_w   = int(draw.textlength(g_num_str, font=font_big_num))
    g_center_x = (goal_bg_x0 + goal_bg_x1) // 2

    draw.text((g_center_x - g_num_w // 2, stats_y0 + 10 * SCALE), g_num_str, fill=GOAL_COLOR, font=font_big_num)
    g_lbl = "ГОЛОВ"
    g_lbl_w = int(draw.textlength(g_lbl, font=font_stat_lbl))
    draw.text((g_center_x - g_lbl_w // 2, stats_y0 + 62 * SCALE), g_lbl, fill=MUTED, font=font_stat_lbl)

    # Assists block
    ast_bg_x0 = CARD_PADDING + col_w + 8 * SCALE
    ast_bg_x1 = CARD_WIDTH - CARD_PADDING
    _draw_rounded_rect(draw, (ast_bg_x0, stats_y0, ast_bg_x1, stats_y0 + stats_h), radius=12 * SCALE, fill=SURFACE_COLOR, outline=BORDER_COLOR)

    a_num_str = str(total_assists)
    a_num_w   = int(draw.textlength(a_num_str, font=font_big_num))
    a_center_x = (ast_bg_x0 + ast_bg_x1) // 2

    draw.text((a_center_x - a_num_w // 2, stats_y0 + 10 * SCALE), a_num_str, fill=ASSIST_COLOR, font=font_big_num)
    a_lbl = "АССИСТОВ"
    a_lbl_w = int(draw.textlength(a_lbl, font=font_stat_lbl))
    draw.text((a_center_x - a_lbl_w // 2, stats_y0 + 62 * SCALE), a_lbl, fill=MUTED, font=font_stat_lbl)

    y += stats_h + SECTION_GAP

    # ══════════════════════════════════════════════════════════════════════
    # 3. PER-ROUND TABLE
    # ══════════════════════════════════════════════════════════════════════
    if num_rounds == 0:
        no_data = "Нет статистики по турам"
        nd_w = int(draw.textlength(no_data, font=font_stat_lbl))
        draw.text(((CARD_WIDTH - nd_w) // 2, y + 10 * SCALE), no_data, fill=MUTED, font=font_stat_lbl)
        y += no_rounds_h
    else:
        draw.text((CARD_PADDING, y + 8 * SCALE), "Статистика по турам", fill=WHITE, font=font_round_hd)

        col_round_x   = CARD_PADDING
        col_goals_x   = CARD_WIDTH - CARD_PADDING - 220 * SCALE
        col_assists_x = CARD_WIDTH - CARD_PADDING - 100 * SCALE
        col_total_x   = CARD_WIDTH - CARD_PADDING - 10 * SCALE

        draw.text((col_goals_x,   y + 8 * SCALE), "Голы",     fill=MUTED, font=font_label, anchor="ra")
        draw.text((col_assists_x, y + 8 * SCALE), "Ассисты",  fill=MUTED, font=font_label, anchor="ra")
        draw.text((col_total_x,   y + 8 * SCALE), "Всего",    fill=MUTED, font=font_label, anchor="ra")

        y += ROUNDS_HEADER
        draw.line([(CARD_PADDING, y - 4 * SCALE), (CARD_WIDTH - CARD_PADDING, y - 4 * SCALE)], fill=BORDER_COLOR, width=1 * SCALE)

        for idx, (rn, rd) in enumerate(sorted(rounds.items(), key=lambda x: int(x[0]))):
            row_bg = SURFACE_COLOR if idx % 2 == 0 else BG_COLOR
            draw.rectangle(
                [(CARD_PADDING - 4 * SCALE, y), (CARD_WIDTH - CARD_PADDING + 4 * SCALE, y + ROW_H - 2 * SCALE)],
                fill=row_bg
            )

            row_center_y = y + ROW_H // 2

            draw.text((col_round_x, row_center_y), f"Тур {rn}", fill=TEXT_SECONDARY, font=font_round, anchor="lm")

            goals   = rd.get("goals", 0)
            assists = rd.get("assists", 0)
            total   = goals + assists

            g_str = str(goals) if goals > 0 else "—"
            g_col = GOAL_COLOR if goals > 0 else MUTED
            draw.text((col_goals_x, row_center_y), g_str, fill=g_col, font=font_round, anchor="rm")

            a_str = str(assists) if assists > 0 else "—"
            a_col = ASSIST_COLOR if assists > 0 else MUTED
            draw.text((col_assists_x, row_center_y), a_str, fill=a_col, font=font_round, anchor="rm")

            t_str = str(total)
            draw.text((col_total_x, row_center_y), t_str, fill=WHITE, font=font_round, anchor="rm")

            y += ROW_H

    y += SECTION_GAP

    # ══════════════════════════════════════════════════════════════════════
    # 4. FOOTER
    # ══════════════════════════════════════════════════════════════════════
    draw.line([(CARD_PADDING, y), (CARD_WIDTH - CARD_PADDING, y)], fill=BORDER_COLOR, width=1 * SCALE)
    footer_text = "КПЛ 2026  •  Player Card"
    ft_w = int(draw.textlength(footer_text, font=font_label))
    draw.text(
        ((CARD_WIDTH - ft_w) // 2, y + 10 * SCALE),
        footer_text, fill=MUTED, font=font_label
    )

    final_w = CARD_WIDTH_1X
    final_h = total_h // SCALE
    resampled_img = img.resize((final_w, final_h), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    resampled_img.save(buf, format="PNG")
    buf.seek(0)
    return buf


if __name__ == "__main__":
    test_stats = {
        "player_name": "Криштиану Роналду",
        "team_name": "Спортинг",
        "total_goals": 12,
        "total_assists": 5,
        "rounds": {
            1: {"goals": 2, "assists": 1},
            3: {"goals": 1, "assists": 0},
            5: {"goals": 0, "assists": 2},
            7: {"goals": 3, "assists": 1},
        },
    }
    buf = generate_player_card(test_stats)
    with open("test_player_card.png", "wb") as f:
        f.write(buf.getvalue())
    print("test_player_card.png saved")
