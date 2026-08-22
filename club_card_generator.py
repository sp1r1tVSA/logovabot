import os
import io
from PIL import Image, ImageDraw, ImageFont
from table_generator import TEAM_LOGO_MAP, load_font

BASE_DIR = os.path.dirname(__file__)
LOGOS_DIR = os.path.join(BASE_DIR, "assets", "logos")

SCALE = 2

# Card dimensions (1x base)
CARD_WIDTH_1X   = 720
CARD_PADDING_1X = 36

CARD_WIDTH   = CARD_WIDTH_1X * SCALE
CARD_PADDING = CARD_PADDING_1X * SCALE

# Colors (EA FC / Dark Premium UI)
BG_COLOR       = (20, 20, 24)        # #141418
SURFACE_COLOR  = (28, 28, 34)        # #1C1C22
BORDER_COLOR   = (48, 48, 58)        # #30303A

CUP_SURFACE    = (36, 30, 20)        # subtle warm gold tint
CUP_BORDER     = (90, 70, 28)
CUP_GOLD       = (251, 191, 36)      # #FBBF24

WHITE          = (255, 255, 255)
MUTED          = (156, 163, 175)     # #9CA3AF
TEXT_SECONDARY = (209, 213, 219)     # #D1D5DB

WIN_COLOR      = (34, 197, 94)       # #22C55E  green
DRAW_COLOR     = (234, 179, 8)       # #EAB308  yellow
LOSS_COLOR     = (239, 68, 68)       # #EF4444  red

GOAL_COLOR     = (34, 197, 94)       # #22C55E
ASSIST_COLOR   = (59, 130, 246)      # #3B82F6


def _draw_rounded_rect(draw: ImageDraw.ImageDraw, xy: tuple, radius: int, fill: tuple, outline: tuple | None = None, width: int = 1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def generate_club_card(data: dict) -> io.BytesIO:
    """
    Generate a high-res 2x supersampled Club Stats Card image.
    
    data expected:
      {
        "team_name": str,
        "manager": {"username": str, "warn_count": int} or None,
        "league_stats": {
            "rank": int, "played": int, "wins": int, "draws": int, "losses": int,
            "goals_scored": int, "goals_conceded": int, "goal_diff": int, "points": int
        },
        "recent_form": ["W", "D", "L", ...],
        "cup_stats": {
            "stage": str, "opponent": str, "club_wins": int, "opp_wins": int, "status": str
        } or None,
        "top_scorers": [{"player_name": str, "goals": int}],
        "top_assists": [{"player_name": str, "assists": int}],
        "squad_count": int,
        "debts_count": int
      }
    """
    team_name    = data.get("team_name", "Клуб")
    manager      = data.get("manager")
    mgr_name     = f"@{manager['username']}" if manager and manager.get("username") else ("Свободен" if not manager else "ID " + str(manager.get("telegram_id", "")))
    l_stats      = data.get("league_stats") or {}
    form         = data.get("recent_form") or []
    cup          = data.get("cup_stats")
    top_scorers  = data.get("top_scorers") or []
    top_assists  = data.get("top_assists") or []
    squad_count  = data.get("squad_count", 0)

    # ── Fonts ──────────────────────────────────────────────────────────────
    font_title   = load_font(26 * SCALE, bold=True)
    font_sub     = load_font(13 * SCALE)
    font_badge   = load_font(14 * SCALE, bold=True)
    font_big_num = load_font(32 * SCALE, bold=True)
    font_lbl     = load_font(11 * SCALE)
    font_row_hd  = load_font(13 * SCALE, bold=True)
    font_row_val = load_font(13 * SCALE)
    font_sm      = load_font(11 * SCALE)

    # ── Dimensions ─────────────────────────────────────────────────────────
    HEADER_H     = 100 * SCALE
    STATS_BAR_H  = 80 * SCALE
    FORM_BAR_H   = 56 * SCALE
    CUP_BAR_H    = (70 * SCALE) if cup else 0
    LEADERS_H    = 150 * SCALE
    FOOTER_H     = 36 * SCALE

    TOTAL_HEIGHT = CARD_PADDING * 2 + HEADER_H + STATS_BAR_H + FORM_BAR_H + CUP_BAR_H + LEADERS_H + FOOTER_H + 50 * SCALE

    img = Image.new("RGBA", (CARD_WIDTH, TOTAL_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    curr_y = CARD_PADDING

    # ── 1. HEADER (Club Logo + Name + Manager + Rank Badge) ─────────────────
    logo_size = 72 * SCALE
    logo_file = TEAM_LOGO_MAP.get(team_name.lower())
    logo_img = None
    if logo_file:
        full_logo_path = os.path.join(LOGOS_DIR, logo_file)
        if os.path.exists(full_logo_path):
            try:
                logo_img = Image.open(full_logo_path).convert("RGBA")
                logo_img = logo_img.resize((logo_size, logo_size), Image.Resampling.LANCZOS)
            except Exception:
                logo_img = None

    if logo_img:
        img.paste(logo_img, (CARD_PADDING, curr_y), logo_img)
    else:
        # Fallback circle
        _draw_rounded_rect(draw, (CARD_PADDING, curr_y, CARD_PADDING + logo_size, curr_y + logo_size),
                           radius=logo_size // 2, fill=SURFACE_COLOR, outline=BORDER_COLOR, width=2)
        initials = (team_name[:2]).upper()
        bbox = draw.textbbox((0, 0), initials, font=font_title)
        draw.text((CARD_PADDING + (logo_size - (bbox[2] - bbox[0])) // 2,
                   curr_y + (logo_size - (bbox[3] - bbox[1])) // 2), initials, font=font_title, fill=WHITE)

    text_x = CARD_PADDING + logo_size + 18 * SCALE
    draw.text((text_x, curr_y + 4 * SCALE), team_name, font=font_title, fill=WHITE)

    mgr_label = f"Тренер: {mgr_name}  •  Заявка: {squad_count} игр."
    draw.text((text_x, curr_y + 42 * SCALE), mgr_label, font=font_sub, fill=MUTED)

    # Rank Badge on Right
    rank = l_stats.get("rank", 0)
    pts = l_stats.get("points", 0)
    rank_text = f"#{rank} МЕСТО" if rank > 0 else "ЛИГА КПЛ"
    pts_text  = f"{pts} PTS"

    badge_w = 120 * SCALE
    badge_h = 56 * SCALE
    badge_x = CARD_WIDTH - CARD_PADDING - badge_w
    _draw_rounded_rect(draw, (badge_x, curr_y + 8 * SCALE, badge_x + badge_w, curr_y + 8 * SCALE + badge_h),
                       radius=10 * SCALE, fill=(38, 38, 48), outline=(68, 68, 88), width=2)
    
    rb_bbox = draw.textbbox((0, 0), rank_text, font=font_badge)
    draw.text((badge_x + (badge_w - (rb_bbox[2] - rb_bbox[0])) // 2, curr_y + 14 * SCALE), rank_text, font=font_badge, fill=WHITE)
    
    pb_bbox = draw.textbbox((0, 0), pts_text, font=font_sub)
    draw.text((badge_x + (badge_w - (pb_bbox[2] - pb_bbox[0])) // 2, curr_y + 36 * SCALE), pts_text, font=font_sub, fill=CUP_GOLD)

    curr_y += HEADER_H + 10 * SCALE

    # ── 2. STATS TILES (Played / Wins / Draws / Losses / Goals / GD) ────────
    _draw_rounded_rect(draw, (CARD_PADDING, curr_y, CARD_WIDTH - CARD_PADDING, curr_y + STATS_BAR_H),
                       radius=12 * SCALE, fill=SURFACE_COLOR, outline=BORDER_COLOR, width=2)

    cols = [
        ("ИГРЫ", str(l_stats.get("played", 0)), WHITE),
        ("ПОБЕДЫ", str(l_stats.get("wins", 0)), WIN_COLOR),
        ("НИЧЬИ", str(l_stats.get("draws", 0)), DRAW_COLOR),
        ("ПОРАЖЕНИЯ", str(l_stats.get("losses", 0)), LOSS_COLOR),
        ("ГОЛЫ", f"{l_stats.get('goals_scored', 0)}:{l_stats.get('goals_conceded', 0)}", WHITE),
        ("РАЗНИЦА", f"{'+' if l_stats.get('goal_diff', 0) > 0 else ''}{l_stats.get('goal_diff', 0)}", CUP_GOLD),
    ]

    col_w = (CARD_WIDTH - CARD_PADDING * 2) / len(cols)
    for idx, (label, val, col_color) in enumerate(cols):
        cx = CARD_PADDING + idx * col_w + col_w / 2
        
        # Label
        l_bbox = draw.textbbox((0, 0), label, font=font_lbl)
        draw.text((cx - (l_bbox[2] - l_bbox[0]) / 2, curr_y + 14 * SCALE), label, font=font_lbl, fill=MUTED)

        # Value
        v_bbox = draw.textbbox((0, 0), val, font=font_big_num)
        draw.text((cx - (v_bbox[2] - v_bbox[0]) / 2, curr_y + 34 * SCALE), val, font=font_big_num, fill=col_color)

    curr_y += STATS_BAR_H + 14 * SCALE

    # ── 3. FORM BAR (Last 5 matches) ───────────────────────────────────────
    _draw_rounded_rect(draw, (CARD_PADDING, curr_y, CARD_WIDTH - CARD_PADDING, curr_y + FORM_BAR_H),
                       radius=10 * SCALE, fill=SURFACE_COLOR, outline=BORDER_COLOR, width=2)

    draw.text((CARD_PADDING + 16 * SCALE, curr_y + 18 * SCALE), "ФОРМА (ПОСЛЕДНИЕ МАТЧИ):", font=font_row_hd, fill=TEXT_SECONDARY)

    badge_start_x = CARD_WIDTH - CARD_PADDING - 16 * SCALE
    b_size = 28 * SCALE
    spacing = 8 * SCALE

    if not form:
        draw.text((badge_start_x - 120 * SCALE, curr_y + 18 * SCALE), "Матчей нет", font=font_sub, fill=MUTED)
    else:
        for idx, outcome in enumerate(reversed(form[-5:])):
            bx = badge_start_x - (idx + 1) * (b_size + spacing)
            by = curr_y + (FORM_BAR_H - b_size) // 2

            if outcome == 'W':
                b_fill, b_text = WIN_COLOR, 'В'
            elif outcome == 'D':
                b_fill, b_text = DRAW_COLOR, 'Н'
            else:
                b_fill, b_text = LOSS_COLOR, 'П'

            _draw_rounded_rect(draw, (bx, by, bx + b_size, by + b_size), radius=6 * SCALE, fill=b_fill)
            t_bbox = draw.textbbox((0, 0), b_text, font=font_badge)
            draw.text((bx + (b_size - (t_bbox[2] - t_bbox[0])) // 2, by + (b_size - (t_bbox[3] - t_bbox[1])) // 2),
                      b_text, font=font_badge, fill=WHITE if outcome != 'D' else (20, 20, 20))

    curr_y += FORM_BAR_H + 14 * SCALE

    # ── 4. CUP BLOCK (Optional if series exists) ───────────────────────────
    if cup:
        _draw_rounded_rect(draw, (CARD_PADDING, curr_y, CARD_WIDTH - CARD_PADDING, curr_y + CUP_BAR_H),
                           radius=10 * SCALE, fill=CUP_SURFACE, outline=CUP_BORDER, width=2)
        
        stage_name = cup.get("stage", "1/8")
        opp = cup.get("opponent", "Соперник")
        c_w = cup.get("club_wins", 0)
        o_w = cup.get("opp_wins", 0)
        status = cup.get("status", "active")
        
        title_text = f"🏆 КУБОК КПЛ 2026 | СТАДИЯ: {stage_name.upper()} ФИНАЛА"
        draw.text((CARD_PADDING + 16 * SCALE, curr_y + 14 * SCALE), title_text, font=font_row_hd, fill=CUP_GOLD)

        cup_desc = f"Серия против «{opp}»  •  Счёт серии: {c_w} : {o_w}  •  Статус: {'Завершена' if status == 'completed' else 'В процессе'}"
        draw.text((CARD_PADDING + 16 * SCALE, curr_y + 38 * SCALE), cup_desc, font=font_sub, fill=TEXT_SECONDARY)

        curr_y += CUP_BAR_H + 14 * SCALE

    # ── 5. TOP SCORERS & ASSISTS (Two columns) ─────────────────────────────
    half_w = (CARD_WIDTH - CARD_PADDING * 2 - 14 * SCALE) // 2

    # Left: Top Scorers
    _draw_rounded_rect(draw, (CARD_PADDING, curr_y, CARD_PADDING + half_w, curr_y + LEADERS_H),
                       radius=12 * SCALE, fill=SURFACE_COLOR, outline=BORDER_COLOR, width=2)
    draw.text((CARD_PADDING + 16 * SCALE, curr_y + 14 * SCALE), "⚽ БОМБАРДИРЫ КЛУБА", font=font_row_hd, fill=GOAL_COLOR)

    if not top_scorers:
        draw.text((CARD_PADDING + 16 * SCALE, curr_y + 45 * SCALE), "Нет забитых голов", font=font_sub, fill=MUTED)
    else:
        for s_idx, sc in enumerate(top_scorers[:3]):
            sy = curr_y + 44 * SCALE + s_idx * 30 * SCALE
            p_n = sc["player_name"]
            p_g = sc["goals"]
            draw.text((CARD_PADDING + 16 * SCALE, sy), f"{s_idx + 1}. {p_n}", font=font_row_val, fill=WHITE)
            g_str = f"{p_g} гол."
            g_bbox = draw.textbbox((0, 0), g_str, font=font_badge)
            draw.text((CARD_PADDING + half_w - 16 * SCALE - (g_bbox[2] - g_bbox[0]), sy), g_str, font=font_badge, fill=GOAL_COLOR)

    # Right: Top Assists
    right_x = CARD_PADDING + half_w + 14 * SCALE
    _draw_rounded_rect(draw, (right_x, curr_y, right_x + half_w, curr_y + LEADERS_H),
                       radius=12 * SCALE, fill=SURFACE_COLOR, outline=BORDER_COLOR, width=2)
    draw.text((right_x + 16 * SCALE, curr_y + 14 * SCALE), "🎯 АССИСТЕНТЫ КЛУБА", font=font_row_hd, fill=ASSIST_COLOR)

    if not top_assists:
        draw.text((right_x + 16 * SCALE, curr_y + 45 * SCALE), "Нет голевых передач", font=font_sub, fill=MUTED)
    else:
        for a_idx, ac in enumerate(top_assists[:3]):
            ay = curr_y + 44 * SCALE + a_idx * 30 * SCALE
            p_n = ac["player_name"]
            p_a = ac["assists"]
            draw.text((right_x + 16 * SCALE, ay), f"{a_idx + 1}. {p_n}", font=font_row_val, fill=WHITE)
            a_str = f"{p_a} пас."
            a_bbox = draw.textbbox((0, 0), a_str, font=font_badge)
            draw.text((right_x + half_w - 16 * SCALE - (a_bbox[2] - a_bbox[0]), ay), a_str, font=font_badge, fill=ASSIST_COLOR)

    curr_y += LEADERS_H + 16 * SCALE

    # ── 6. FOOTER ──────────────────────────────────────────────────────────
    footer_text = "LOGOVOBOT • КИБЕРФУТБОЛЬНАЯ ПРЕМЬЕР-ЛИГА 2026"
    f_bbox = draw.textbbox((0, 0), footer_text, font=font_sm)
    draw.text(((CARD_WIDTH - (f_bbox[2] - f_bbox[0])) // 2, curr_y + 4 * SCALE), footer_text, font=font_sm, fill=MUTED)

    # ── Export to buffer ───────────────────────────────────────────────────
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
