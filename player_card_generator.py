import os
import io
from PIL import Image, ImageDraw, ImageFont
from table_generator import TEAM_LOGO_MAP, load_font
import player_photos

BASE_DIR = os.path.dirname(__file__)
LOGOS_DIR = os.path.join(BASE_DIR, "assets", "logos")

SCALE = 2

# Card dimensions (1x base)
CARD_WIDTH_1X   = 720
CARD_PADDING_1X = 36

CARD_WIDTH   = CARD_WIDTH_1X * SCALE
CARD_PADDING = CARD_PADDING_1X * SCALE

# Colors (Apple / EA FC Premium Dark UI)
BG_COLOR       = (18, 18, 22)        # #121216
SURFACE_COLOR  = (26, 26, 32)        # #1A1A20
SURFACE_ALT    = (21, 21, 26)        # #15151A
BORDER_COLOR   = (42, 42, 50)        # #2A2A32

CUP_SURFACE    = (35, 28, 18)        # #231C12 warm gold tint
CUP_BORDER     = (85, 65, 25)        # #554119
CUP_GOLD       = (245, 158, 11)      # #F59E0B
CUP_TEXT       = (251, 191, 36)      # #FBBF24

WHITE          = (255, 255, 255)
MUTED          = (156, 163, 175)     # #9CA3AF
GOAL_COLOR     = (34, 197, 94)       # #22C55E  green
GOAL_HIGH      = (74, 222, 128)      # #4ADE80  bright green for hat-tricks+
ASSIST_COLOR   = (59, 130, 246)      # #3B82F6  blue
POINT_COLOR    = (168, 85, 247)      # #A855F7  purple
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
        "total_points": int,
        "league_goals": int,
        "league_assists": int,
        "cup_goals": int,
        "cup_assists": int,
        "matches_count": int,
        "items": [
           {"title": str, "opponent": str, "score_str": str, "goals": int, "assists": int, "total": int, "is_cup": bool},
           ...
        ],
        "rounds": dict (fallback)
      }

    Returns io.BytesIO PNG buffer.
    """
    player_name   = stats.get("player_name", "—")
    team_name     = stats.get("team_name", "—")
    total_goals   = stats.get("total_goals", 0)
    total_assists = stats.get("total_assists", 0)
    total_points  = stats.get("total_points", total_goals + total_assists)
    
    league_goals   = stats.get("league_goals", 0)
    league_assists = stats.get("league_assists", 0)
    cup_goals      = stats.get("cup_goals", 0)
    cup_assists    = stats.get("cup_assists", 0)
    
    items: list[dict] = stats.get("items", [])
    # Fallback if items not provided
    if not items and stats.get("rounds"):
        for rn, rd in sorted(stats["rounds"].items(), key=lambda x: int(x[0])):
            is_c = (int(rn) == -1)
            items.append({
                "title": "🏆 Кубок КПЛ" if is_c else f"Тур {rn}",
                "opponent": "",
                "score_str": "",
                "goals": rd.get("goals", 0),
                "assists": rd.get("assists", 0),
                "total": rd.get("goals", 0) + rd.get("assists", 0),
                "is_cup": is_c
            })

    matches_count = stats.get("matches_count", len(items))

    # ── 2x Scaled Fonts ────────────────────────────────────────────────────
    font_player   = load_font(25 * SCALE, bold=True)
    font_team     = load_font(15 * SCALE)
    font_label    = load_font(12 * SCALE)
    font_sub_lbl  = load_font(11 * SCALE)
    font_big_num  = load_font(38 * SCALE, bold=True)
    font_stat_lbl = load_font(13 * SCALE, bold=True)
    font_round_hd = load_font(15 * SCALE, bold=True)
    font_round    = load_font(14 * SCALE)
    font_round_b  = load_font(14 * SCALE, bold=True)
    font_season   = load_font(12 * SCALE, bold=True)

    # ── Dynamic height ─────────────────────────────────────────────────────
    HEADER_H      = 100 * SCALE
    BIG_STATS_H   = 106 * SCALE
    ROUNDS_HEADER = 40 * SCALE
    ROW_H         = 44 * SCALE
    FOOTER_H      = 32 * SCALE
    SECTION_GAP   = 16 * SCALE

    num_rows = len(items)
    rounds_section_h = (ROUNDS_HEADER + num_rows * ROW_H) if num_rows > 0 else 0
    no_rounds_h      = 48 * SCALE if num_rows == 0 else 0

    total_h = (
        CARD_PADDING
        + HEADER_H
        + SECTION_GAP
        + BIG_STATS_H
        + SECTION_GAP
        + (rounds_section_h if num_rows > 0 else no_rounds_h)
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
    PHOTO_D = 84 * SCALE   # player portrait diameter
    BADGE_D = 34 * SCALE   # club logo badge diameter

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
        placeholder = Image.new("RGBA", (PHOTO_D, PHOTO_D), (45, 55, 72, 255))
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
    text_x = photo_x + PHOTO_D + 22 * SCALE
    name_y = y + 20 * SCALE
    max_name_w = CARD_WIDTH - CARD_PADDING - text_x - 140 * SCALE
    if draw.textlength(player_name, font=font_player) > max_name_w:
        font_player = load_font(21 * SCALE, bold=True)
        if draw.textlength(player_name, font=font_player) > max_name_w:
            font_player = load_font(18 * SCALE, bold=True)

    draw.text((text_x, name_y), player_name, fill=WHITE, font=font_player)

    team_y = name_y + 36 * SCALE
    draw.text((text_x, team_y), team_name, fill=MUTED, font=font_team)

    # "Сезон 2026" badge
    season_label = "Сезон 2026"
    sl_w = int(draw.textlength(season_label, font=font_season))
    sl_x = CARD_WIDTH - CARD_PADDING - sl_w - 16 * SCALE
    sl_y = y + 10 * SCALE
    _draw_rounded_rect(draw, (sl_x - 10 * SCALE, sl_y - 4 * SCALE, sl_x + sl_w + 10 * SCALE, sl_y + 24 * SCALE), radius=6 * SCALE, fill=SURFACE_COLOR, outline=BORDER_COLOR)
    draw.text((sl_x, sl_y), season_label, fill=MUTED, font=font_season)

    y += HEADER_H + SECTION_GAP

    draw.line([(CARD_PADDING, y), (CARD_WIDTH - CARD_PADDING, y)], fill=BORDER_COLOR, width=1 * SCALE)
    y += SECTION_GAP

    # ══════════════════════════════════════════════════════════════════════
    # 2. 3-CARD STATS OVERVIEW — Goals  |  Assists  |  Goal + Pass (G+A)
    # ══════════════════════════════════════════════════════════════════════
    gap_between = 12 * SCALE
    total_avail_w = CARD_WIDTH - CARD_PADDING * 2
    card_w = (total_avail_w - gap_between * 2) // 3
    stats_y0 = y
    stats_h  = BIG_STATS_H

    # Card 1: GOALS
    c1_x0 = CARD_PADDING
    c1_x1 = c1_x0 + card_w
    _draw_rounded_rect(draw, (c1_x0, stats_y0, c1_x1, stats_y0 + stats_h), radius=12 * SCALE, fill=SURFACE_COLOR, outline=BORDER_COLOR)
    c1_cx = (c1_x0 + c1_x1) // 2

    g_num_str = str(total_goals)
    g_num_w   = int(draw.textlength(g_num_str, font=font_big_num))
    draw.text((c1_cx - g_num_w // 2, stats_y0 + 10 * SCALE), g_num_str, fill=GOAL_COLOR, font=font_big_num)
    
    g_lbl = "ГОЛОВ"
    g_lbl_w = int(draw.textlength(g_lbl, font=font_stat_lbl))
    draw.text((c1_cx - g_lbl_w // 2, stats_y0 + 56 * SCALE), g_lbl, fill=TEXT_SECONDARY, font=font_stat_lbl)

    g_sub = f"Лига: {league_goals} • Кубок: {cup_goals}"
    g_sub_w = int(draw.textlength(g_sub, font=font_sub_lbl))
    draw.text((c1_cx - g_sub_w // 2, stats_y0 + 78 * SCALE), g_sub, fill=MUTED, font=font_sub_lbl)

    # Card 2: ASSISTS
    c2_x0 = c1_x1 + gap_between
    c2_x1 = c2_x0 + card_w
    _draw_rounded_rect(draw, (c2_x0, stats_y0, c2_x1, stats_y0 + stats_h), radius=12 * SCALE, fill=SURFACE_COLOR, outline=BORDER_COLOR)
    c2_cx = (c2_x0 + c2_x1) // 2

    a_num_str = str(total_assists)
    a_num_w   = int(draw.textlength(a_num_str, font=font_big_num))
    draw.text((c2_cx - a_num_w // 2, stats_y0 + 10 * SCALE), a_num_str, fill=ASSIST_COLOR, font=font_big_num)

    a_lbl = "АССИСТОВ"
    a_lbl_w = int(draw.textlength(a_lbl, font=font_stat_lbl))
    draw.text((c2_cx - a_lbl_w // 2, stats_y0 + 56 * SCALE), a_lbl, fill=TEXT_SECONDARY, font=font_stat_lbl)

    a_sub = f"Лига: {league_assists} • Кубок: {cup_assists}"
    a_sub_w = int(draw.textlength(a_sub, font=font_sub_lbl))
    draw.text((c2_cx - a_sub_w // 2, stats_y0 + 78 * SCALE), a_sub, fill=MUTED, font=font_sub_lbl)

    # Card 3: GOAL + PASS (POINTS)
    c3_x0 = c2_x1 + gap_between
    c3_x1 = CARD_WIDTH - CARD_PADDING
    _draw_rounded_rect(draw, (c3_x0, stats_y0, c3_x1, stats_y0 + stats_h), radius=12 * SCALE, fill=SURFACE_COLOR, outline=BORDER_COLOR)
    c3_cx = (c3_x0 + c3_x1) // 2

    p_num_str = str(total_points)
    p_num_w   = int(draw.textlength(p_num_str, font=font_big_num))
    draw.text((c3_cx - p_num_w // 2, stats_y0 + 10 * SCALE), p_num_str, fill=POINT_COLOR, font=font_big_num)

    p_lbl = "ОЧКОВ (Г+П)"
    p_lbl_w = int(draw.textlength(p_lbl, font=font_stat_lbl))
    draw.text((c3_cx - p_lbl_w // 2, stats_y0 + 56 * SCALE), p_lbl, fill=TEXT_SECONDARY, font=font_stat_lbl)

    avg_str = f"В {matches_count} матчах"
    p_sub_w = int(draw.textlength(avg_str, font=font_sub_lbl))
    draw.text((c3_cx - p_sub_w // 2, stats_y0 + 78 * SCALE), avg_str, fill=MUTED, font=font_sub_lbl)

    y += stats_h + SECTION_GAP

    # ══════════════════════════════════════════════════════════════════════
    # 3. DETAILED PER-MATCH & ROUND TABLE
    # ══════════════════════════════════════════════════════════════════════
    if num_rows == 0:
        no_data = "Нет подтверждённых результативных матчей"
        nd_w = int(draw.textlength(no_data, font=font_stat_lbl))
        draw.text(((CARD_WIDTH - nd_w) // 2, y + 12 * SCALE), no_data, fill=MUTED, font=font_stat_lbl)
        y += no_rounds_h
    else:
        draw.text((CARD_PADDING, y + 8 * SCALE), "Результативность по матчам", fill=WHITE, font=font_round_hd)

        col_title_x   = CARD_PADDING + 8 * SCALE
        col_match_x   = CARD_PADDING + 210 * SCALE
        col_goals_x   = CARD_WIDTH - CARD_PADDING - 210 * SCALE
        col_assists_x = CARD_WIDTH - CARD_PADDING - 110 * SCALE
        col_total_x   = CARD_WIDTH - CARD_PADDING - 16 * SCALE

        draw.text((col_match_x,   y + 8 * SCALE), "Матч",      fill=MUTED, font=font_label, anchor="la")
        draw.text((col_goals_x,   y + 8 * SCALE), "Голы",     fill=MUTED, font=font_label, anchor="ra")
        draw.text((col_assists_x, y + 8 * SCALE), "Ассисты",  fill=MUTED, font=font_label, anchor="ra")
        draw.text((col_total_x,   y + 8 * SCALE), "Г+П",      fill=MUTED, font=font_label, anchor="ra")

        y += ROUNDS_HEADER
        draw.line([(CARD_PADDING, y - 4 * SCALE), (CARD_WIDTH - CARD_PADDING, y - 4 * SCALE)], fill=BORDER_COLOR, width=1 * SCALE)

        for idx, item in enumerate(items):
            is_cup = item.get("is_cup", False)
            
            if is_cup:
                row_bg = CUP_SURFACE
                row_outline = CUP_BORDER
            else:
                row_bg = SURFACE_COLOR if idx % 2 == 0 else BG_COLOR
                row_outline = None

            draw.rectangle(
                [(CARD_PADDING, y), (CARD_WIDTH - CARD_PADDING, y + ROW_H - 2 * SCALE)],
                fill=row_bg,
                outline=row_outline,
                width=1 * SCALE if is_cup else 0
            )

            row_center_y = y + ROW_H // 2

            # Title (e.g. "🏆 Кубок КПЛ (1/8)" or "Тур 1")
            title = item.get("title", "")
            title_col = CUP_TEXT if is_cup else TEXT_SECONDARY
            title_font = font_round_b if is_cup else font_round
            draw.text((col_title_x, row_center_y), title, fill=title_col, font=title_font, anchor="lm")

            # Match info (e.g. "vs Брюгге (4:1)")
            opp = item.get("opponent")
            score = item.get("score_str")
            if opp and score and score != "—":
                match_str = f"vs {opp} ({score})"
            elif opp:
                match_str = f"vs {opp}"
            else:
                match_str = "—"
            
            # Truncate match_str if too long
            max_m_w = col_goals_x - col_match_x - 50 * SCALE
            if draw.textlength(match_str, font=font_round) > max_m_w:
                match_str = match_str[:22] + "…"

            draw.text((col_match_x, row_center_y), match_str, fill=MUTED if not is_cup else TEXT_SECONDARY, font=font_round, anchor="lm")

            # Goals
            goals   = item.get("goals", 0)
            assists = item.get("assists", 0)
            total   = item.get("total", goals + assists)

            g_str = str(goals) if goals > 0 else "—"
            if goals >= 3:
                g_col = GOAL_HIGH
                g_fnt = font_round_b
            elif goals > 0:
                g_col = GOAL_COLOR
                g_fnt = font_round
            else:
                g_col = MUTED
                g_fnt = font_round
                
            draw.text((col_goals_x, row_center_y), g_str, fill=g_col, font=g_fnt, anchor="rm")

            # Assists
            a_str = str(assists) if assists > 0 else "—"
            a_col = ASSIST_COLOR if assists > 0 else MUTED
            a_fnt = font_round_b if assists >= 2 else font_round
            draw.text((col_assists_x, row_center_y), a_str, fill=a_col, font=a_fnt, anchor="rm")

            # Total (G+A)
            t_str = str(total) if total > 0 else "—"
            t_col = WHITE if total > 0 else MUTED
            draw.text((col_total_x, row_center_y), t_str, fill=t_col, font=font_round_b, anchor="rm")

            y += ROW_H

    y += SECTION_GAP

    # ══════════════════════════════════════════════════════════════════════
    # 4. FOOTER
    # ══════════════════════════════════════════════════════════════════════
    draw.line([(CARD_PADDING, y), (CARD_WIDTH - CARD_PADDING, y)], fill=BORDER_COLOR, width=1 * SCALE)
    footer_text = "КПЛ 2026  •  Логово Фифарей  •  Player Card"
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
