import os
import io
from PIL import Image, ImageDraw, ImageFont
from table_generator import TEAM_LOGO_MAP, load_font

BASE_DIR = os.path.dirname(__file__)
LOGOS_DIR = os.path.join(BASE_DIR, "assets", "logos")

SCALE = 2

# Card dimensions (1x base)
CARD_WIDTH_1X   = 740
CARD_PADDING_1X = 32

CARD_WIDTH   = CARD_WIDTH_1X * SCALE
CARD_PADDING = CARD_PADDING_1X * SCALE

# Modern EA FC / Dark Premium UI Colors
BG_GRAD_TOP    = (14, 16, 22)        # #0E1016
BG_GRAD_BOT    = (20, 23, 32)        # #141720
SURFACE_COLOR  = (26, 30, 42)        # #1A1E2A
SURFACE_ALT    = (22, 25, 36)        # #161924
BORDER_COLOR   = (46, 52, 70)        # #2E3446
BORDER_LIGHT   = (65, 74, 98)        # #414A62

CUP_SURFACE    = (38, 32, 20)        # Gold tinted luxury surface
CUP_BORDER     = (110, 85, 32)
CUP_GOLD       = (251, 191, 36)      # #FBBF24

WHITE          = (255, 255, 255)
MUTED          = (148, 163, 184)     # #94A3B8
TEXT_SECONDARY = (203, 213, 225)     # #CBD5E1

WIN_COLOR      = (34, 197, 94)       # #22C55E  green
DRAW_COLOR     = (245, 158, 11)      # #F59E0B  amber
LOSS_COLOR     = (239, 68, 68)       # #EF4444  red
ACCENT_CYAN    = (56, 189, 248)      # #38BDF8  sky blue

GOAL_COLOR     = (34, 197, 94)       # #22C55E
ASSIST_COLOR   = (56, 189, 248)      # #38BDF8


def _draw_rounded_rect(draw: ImageDraw.ImageDraw, xy: tuple, radius: int, fill: tuple, outline: tuple | None = None, width: int = 1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _draw_vertical_gradient(img: Image.Image, top_color: tuple, bot_color: tuple):
    """Draw a smooth vertical gradient across the image background."""
    w, h = img.size
    draw = ImageDraw.Draw(img)
    for y in range(h):
        ratio = y / max(h - 1, 1)
        r = int(top_color[0] + (bot_color[0] - top_color[0]) * ratio)
        g = int(top_color[1] + (bot_color[1] - top_color[1]) * ratio)
        b = int(top_color[2] + (bot_color[2] - top_color[2]) * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b, 255))


def generate_club_card(data: dict, avatar_path: str | None = None) -> io.BytesIO:
    """
    Generate a high-res 2x supersampled Club Stats Card image.
    Includes Club Logo at top, key stats, form, leaders, and Owner Avatar/Tag at bottom.
    """
    team_name    = data.get("team_name", "Клуб")
    manager      = data.get("manager")
    mgr_name     = f"@{manager['username']}" if manager and manager.get("username") else ("Свободен" if not manager else "ID " + str(manager.get("telegram_id", "")))
    warn_count   = manager.get("warn_count", 0) if manager else 0
    l_stats      = data.get("league_stats") or {}
    form         = data.get("recent_form") or []
    cup          = data.get("cup_stats")
    top_scorers  = data.get("top_scorers") or []
    top_assists  = data.get("top_assists") or []
    squad_count  = data.get("squad_count", 0)
    debts_count  = data.get("debts_count", 0)

    # ── Fonts ──────────────────────────────────────────────────────────────
    font_title   = load_font(28 * SCALE, bold=True)
    font_sub     = load_font(13 * SCALE)
    font_badge   = load_font(14 * SCALE, bold=True)
    font_badge_sm= load_font(12 * SCALE, bold=True)
    font_big_num = load_font(30 * SCALE, bold=True)
    font_lbl     = load_font(11 * SCALE, bold=True)
    font_row_hd  = load_font(13 * SCALE, bold=True)
    font_row_val = load_font(13 * SCALE)
    font_pill    = load_font(12 * SCALE, bold=True)
    font_owner   = load_font(17 * SCALE, bold=True)
    font_sm      = load_font(11 * SCALE)

    # ── Dimensions ─────────────────────────────────────────────────────────
    HEADER_H     = 100 * SCALE
    STATS_BAR_H  = 84 * SCALE
    FORM_BAR_H   = 58 * SCALE
    CUP_BAR_H    = (76 * SCALE) if cup else 0
    LEADERS_H    = 156 * SCALE
    OWNER_CARD_H = 70 * SCALE
    FOOTER_H     = 32 * SCALE

    TOTAL_HEIGHT = CARD_PADDING * 2 + HEADER_H + STATS_BAR_H + FORM_BAR_H + CUP_BAR_H + LEADERS_H + OWNER_CARD_H + FOOTER_H + 54 * SCALE

    img = Image.new("RGBA", (CARD_WIDTH, TOTAL_HEIGHT))
    _draw_vertical_gradient(img, BG_GRAD_TOP, BG_GRAD_BOT)
    draw = ImageDraw.Draw(img)

    # Top accent line (Gold to Cyan gradient feel)
    draw.line([(CARD_PADDING, 4 * SCALE), (CARD_WIDTH - CARD_PADDING, 4 * SCALE)], fill=CUP_GOLD, width=3 * SCALE)

    curr_y = CARD_PADDING

    # ── 1. HEADER (Club Logo + Name + Rank Badge + Meta) ───────────────────
    logo_size = 78 * SCALE
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

    # Logo border circle / glow
    logo_circle_x = CARD_PADDING
    logo_circle_y = curr_y + 4 * SCALE
    _draw_rounded_rect(draw, (logo_circle_x - 4 * SCALE, logo_circle_y - 4 * SCALE,
                              logo_circle_x + logo_size + 4 * SCALE, logo_circle_y + logo_size + 4 * SCALE),
                       radius=(logo_size + 8 * SCALE) // 2, fill=(20, 24, 34), outline=BORDER_LIGHT, width=2)

    if logo_img:
        img.paste(logo_img, (logo_circle_x, logo_circle_y), logo_img)
    else:
        initials = (team_name[:2]).upper()
        bbox = draw.textbbox((0, 0), initials, font=font_title)
        draw.text((logo_circle_x + (logo_size - (bbox[2] - bbox[0])) // 2,
                   logo_circle_y + (logo_size - (bbox[3] - bbox[1])) // 2), initials, font=font_title, fill=WHITE)

    text_x = logo_circle_x + logo_size + 20 * SCALE

    # Club Name
    draw.text((text_x, curr_y + 4 * SCALE), team_name.upper(), font=font_title, fill=WHITE)

    # Squad & Debts subline in header
    debt_str = f"⚠️ Долги: {debts_count}" if debts_count > 0 else "🟢 Без долгов"
    meta_str = f"📋 Заявка: {squad_count} игр.  •  {debt_str}  •  КПЛ 2026"
    draw.text((text_x, curr_y + 48 * SCALE), meta_str, font=font_sub, fill=TEXT_SECONDARY)

    # Rank Badge on Right
    rank = l_stats.get("rank", 0)
    pts = l_stats.get("points", 0)
    rank_text = f"#{rank} МЕСТО" if rank > 0 else "ЛИГА КПЛ"
    pts_text  = f"{pts} PTS"

    badge_w = 124 * SCALE
    badge_h = 58 * SCALE
    badge_x = CARD_WIDTH - CARD_PADDING - badge_w
    _draw_rounded_rect(draw, (badge_x, curr_y + 8 * SCALE, badge_x + badge_w, curr_y + 8 * SCALE + badge_h),
                       radius=12 * SCALE, fill=(34, 30, 20), outline=CUP_BORDER, width=2)
    
    rb_bbox = draw.textbbox((0, 0), rank_text, font=font_badge)
    draw.text((badge_x + (badge_w - (rb_bbox[2] - rb_bbox[0])) // 2, curr_y + 14 * SCALE), rank_text, font=font_badge, fill=CUP_GOLD)
    
    pb_bbox = draw.textbbox((0, 0), pts_text, font=font_badge_sm)
    draw.text((badge_x + (badge_w - (pb_bbox[2] - pb_bbox[0])) // 2, curr_y + 37 * SCALE), pts_text, font=font_badge_sm, fill=WHITE)

    curr_y += HEADER_H + 10 * SCALE

    # ── 2. STATS TILES (6 Columns: ИГРЫ / В / Н / П / ГОЛЫ / РАЗНИЦА) ─────
    _draw_rounded_rect(draw, (CARD_PADDING, curr_y, CARD_WIDTH - CARD_PADDING, curr_y + STATS_BAR_H),
                       radius=14 * SCALE, fill=SURFACE_COLOR, outline=BORDER_COLOR, width=2)

    cols = [
        ("ИГРЫ", str(l_stats.get("played", 0)), WHITE),
        ("ПОБЕДЫ", str(l_stats.get("wins", 0)), WIN_COLOR),
        ("НИЧЬИ", str(l_stats.get("draws", 0)), DRAW_COLOR),
        ("ПОРАЖЕНИЯ", str(l_stats.get("losses", 0)), LOSS_COLOR),
        ("ГОЛЫ", f"{l_stats.get('goals_scored', 0)}:{l_stats.get('goals_conceded', 0)}", WHITE),
        ("РАЗНИЦА", f"{'+' if l_stats.get('goal_diff', 0) > 0 else ''}{l_stats.get('goal_diff', 0)}", ACCENT_CYAN),
    ]

    col_w = (CARD_WIDTH - CARD_PADDING * 2) / len(cols)
    for idx, (label, val, col_color) in enumerate(cols):
        cx = CARD_PADDING + idx * col_w + col_w / 2
        
        # Sub-divider line between cells (except last)
        if idx > 0:
            sep_x = CARD_PADDING + idx * col_w
            draw.line([(sep_x, curr_y + 16 * SCALE), (sep_x, curr_y + STATS_BAR_H - 16 * SCALE)], fill=BORDER_COLOR, width=1)

        # Label
        l_bbox = draw.textbbox((0, 0), label, font=font_lbl)
        draw.text((cx - (l_bbox[2] - l_bbox[0]) / 2, curr_y + 14 * SCALE), label, font=font_lbl, fill=MUTED)

        # Value
        v_bbox = draw.textbbox((0, 0), val, font=font_big_num)
        draw.text((cx - (v_bbox[2] - v_bbox[0]) / 2, curr_y + 36 * SCALE), val, font=font_big_num, fill=col_color)

    curr_y += STATS_BAR_H + 12 * SCALE

    # ── 3. FORM BAR (Last 5 matches) ───────────────────────────────────────
    _draw_rounded_rect(draw, (CARD_PADDING, curr_y, CARD_WIDTH - CARD_PADDING, curr_y + FORM_BAR_H),
                       radius=12 * SCALE, fill=SURFACE_COLOR, outline=BORDER_COLOR, width=2)

    draw.text((CARD_PADDING + 18 * SCALE, curr_y + 19 * SCALE), "ФОРМА (ПОСЛЕДНИЕ МАТЧИ В ЛИГЕ):", font=font_row_hd, fill=TEXT_SECONDARY)

    badge_start_x = CARD_WIDTH - CARD_PADDING - 16 * SCALE
    b_size = 30 * SCALE
    spacing = 8 * SCALE

    if not form:
        draw.text((badge_start_x - 120 * SCALE, curr_y + 19 * SCALE), "Матчей нет", font=font_sub, fill=MUTED)
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

            _draw_rounded_rect(draw, (bx, by, bx + b_size, by + b_size), radius=7 * SCALE, fill=b_fill)
            t_bbox = draw.textbbox((0, 0), b_text, font=font_badge)
            draw.text((bx + (b_size - (t_bbox[2] - t_bbox[0])) // 2, by + (b_size - (t_bbox[3] - t_bbox[1])) // 2),
                      b_text, font=font_badge, fill=WHITE if outcome != 'D' else (20, 20, 20))

    curr_y += FORM_BAR_H + 12 * SCALE

    # ── 4. CUP BLOCK (If played/active in Cup) ─────────────────────────────
    if cup:
        _draw_rounded_rect(draw, (CARD_PADDING, curr_y, CARD_WIDTH - CARD_PADDING, curr_y + CUP_BAR_H),
                           radius=12 * SCALE, fill=CUP_SURFACE, outline=CUP_BORDER, width=2)
        
        stage_name = cup.get("stage", "1/8")
        opp = cup.get("opponent", "Соперник")
        c_w = cup.get("club_wins", 0)
        o_w = cup.get("opp_wins", 0)
        status = cup.get("status", "active")
        
        title_text = f"🏆 КУБОК КПЛ 2026  •  {stage_name.upper()} ФИНАЛА"
        draw.text((CARD_PADDING + 18 * SCALE, curr_y + 14 * SCALE), title_text, font=font_row_hd, fill=CUP_GOLD)

        cup_desc = f"Серия против «{opp}»  |  Счёт серии: {c_w} : {o_w}  |  {'Завершена' if status == 'completed' else 'В процессе'}"
        draw.text((CARD_PADDING + 18 * SCALE, curr_y + 41 * SCALE), cup_desc, font=font_sub, fill=TEXT_SECONDARY)

        curr_y += CUP_BAR_H + 12 * SCALE

    # ── 5. TOP SCORERS & ASSISTS (Two parallel cards) ─────────────────────
    half_w = (CARD_WIDTH - CARD_PADDING * 2 - 12 * SCALE) // 2

    # Left: Top Scorers
    _draw_rounded_rect(draw, (CARD_PADDING, curr_y, CARD_PADDING + half_w, curr_y + LEADERS_H),
                       radius=14 * SCALE, fill=SURFACE_COLOR, outline=BORDER_COLOR, width=2)
    
    _draw_rounded_rect(draw, (CARD_PADDING + 4 * SCALE, curr_y + 4 * SCALE, CARD_PADDING + half_w - 4 * SCALE, curr_y + 36 * SCALE),
                       radius=10 * SCALE, fill=SURFACE_ALT)
    draw.text((CARD_PADDING + 16 * SCALE, curr_y + 10 * SCALE), "⚽ БОМБАРДИРЫ КЛУБА", font=font_row_hd, fill=GOAL_COLOR)

    if not top_scorers:
        draw.text((CARD_PADDING + 18 * SCALE, curr_y + 54 * SCALE), "Нет забитых голов", font=font_sub, fill=MUTED)
    else:
        for s_idx, sc in enumerate(top_scorers[:3]):
            sy = curr_y + 48 * SCALE + s_idx * 34 * SCALE
            p_n = sc["player_name"]
            p_g = sc["goals"]
            
            draw.text((CARD_PADDING + 16 * SCALE, sy + 2 * SCALE), f"{s_idx + 1}.", font=font_lbl, fill=MUTED)
            draw.text((CARD_PADDING + 36 * SCALE, sy + 2 * SCALE), p_n, font=font_row_val, fill=WHITE)
            
            g_str = f"{p_g} ⚽"
            g_bbox = draw.textbbox((0, 0), g_str, font=font_pill)
            pill_w = (g_bbox[2] - g_bbox[0]) + 16 * SCALE
            pill_h = 24 * SCALE
            pill_x = CARD_PADDING + half_w - 16 * SCALE - pill_w
            _draw_rounded_rect(draw, (pill_x, sy, pill_x + pill_w, sy + pill_h),
                               radius=6 * SCALE, fill=(20, 45, 30), outline=(34, 197, 94), width=1)
            draw.text((pill_x + 8 * SCALE, sy + 2 * SCALE), g_str, font=font_pill, fill=GOAL_COLOR)

    # Right: Top Assists
    right_x = CARD_PADDING + half_w + 12 * SCALE
    _draw_rounded_rect(draw, (right_x, curr_y, right_x + half_w, curr_y + LEADERS_H),
                       radius=14 * SCALE, fill=SURFACE_COLOR, outline=BORDER_COLOR, width=2)
    
    _draw_rounded_rect(draw, (right_x + 4 * SCALE, curr_y + 4 * SCALE, right_x + half_w - 4 * SCALE, curr_y + 36 * SCALE),
                       radius=10 * SCALE, fill=SURFACE_ALT)
    draw.text((right_x + 16 * SCALE, curr_y + 10 * SCALE), "🎯 АССИСТЕНТЫ КЛУБА", font=font_row_hd, fill=ASSIST_COLOR)

    if not top_assists:
        draw.text((right_x + 18 * SCALE, curr_y + 54 * SCALE), "Нет голевых передач", font=font_sub, fill=MUTED)
    else:
        for a_idx, ac in enumerate(top_assists[:3]):
            ay = curr_y + 48 * SCALE + a_idx * 34 * SCALE
            p_n = ac["player_name"]
            p_a = ac["assists"]
            
            draw.text((right_x + 16 * SCALE, ay + 2 * SCALE), f"{a_idx + 1}.", font=font_lbl, fill=MUTED)
            draw.text((right_x + 36 * SCALE, ay + 2 * SCALE), p_n, font=font_row_val, fill=WHITE)
            
            a_str = f"{p_a} 🎯"
            a_bbox = draw.textbbox((0, 0), a_str, font=font_pill)
            pill_w = (a_bbox[2] - a_bbox[0]) + 16 * SCALE
            pill_h = 24 * SCALE
            pill_x = right_x + half_w - 16 * SCALE - pill_w
            _draw_rounded_rect(draw, (pill_x, ay, pill_x + pill_w, ay + pill_h),
                               radius=6 * SCALE, fill=(18, 38, 55), outline=(56, 189, 248), width=1)
            draw.text((pill_x + 8 * SCALE, ay + 2 * SCALE), a_str, font=font_pill, fill=ASSIST_COLOR)

    curr_y += LEADERS_H + 14 * SCALE

    # ── 6. OWNER / MANAGER FOOTER CARD (Avatar + Tag) ─────────────────────
    _draw_rounded_rect(draw, (CARD_PADDING, curr_y, CARD_WIDTH - CARD_PADDING, curr_y + OWNER_CARD_H),
                       radius=14 * SCALE, fill=SURFACE_COLOR, outline=BORDER_LIGHT, width=2)

    av_size = 46 * SCALE
    av_x = CARD_PADDING + 14 * SCALE
    av_y = curr_y + (OWNER_CARD_H - av_size) // 2

    # Draw Owner Avatar (Telegram Profile Photo if available, or stylized initial badge)
    avatar_loaded = False
    if avatar_path and os.path.exists(avatar_path):
        try:
            av_raw = Image.open(avatar_path).convert("RGBA")
            av_raw = av_raw.resize((av_size, av_size), Image.Resampling.LANCZOS)
            mask = Image.new("L", (av_size, av_size), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, av_size, av_size), fill=255)
            
            img.paste(av_raw, (av_x, av_y), mask)
            draw.ellipse((av_x - 2 * SCALE, av_y - 2 * SCALE, av_x + av_size + 2 * SCALE, av_y + av_size + 2 * SCALE),
                         outline=ACCENT_CYAN, width=2 * SCALE)
            avatar_loaded = True
        except Exception:
            avatar_loaded = False

    if not avatar_loaded:
        draw.ellipse((av_x, av_y, av_x + av_size, av_y + av_size), fill=(35, 42, 60), outline=ACCENT_CYAN, width=2 * SCALE)
        initial = (mgr_name.replace("@", "")[:1] or "U").upper()
        ibbox = draw.textbbox((0, 0), initial, font=font_badge)
        draw.text((av_x + (av_size - (ibbox[2] - ibbox[0])) // 2,
                   av_y + (av_size - (ibbox[3] - ibbox[1])) // 2), initial, font=font_badge, fill=ACCENT_CYAN)

    ow_text_x = av_x + av_size + 16 * SCALE
    owner_title = f"{mgr_name}" if manager else "Клуб свободен"
    draw.text((ow_text_x, curr_y + 12 * SCALE), owner_title, font=font_owner, fill=WHITE if manager else MUTED)

    warn_badge_text = f"Нарушения: {warn_count}/4" if warn_count > 0 else "Без нарушений (0/4)"
    ow_subtitle = f"Владелец клуба  •  {warn_badge_text}"
    draw.text((ow_text_x, curr_y + 40 * SCALE), ow_subtitle, font=font_sm, fill=TEXT_SECONDARY)

    # Right side: Verified Owner Pill Badge
    pill_text = "ВЛАДЕЛЕЦ" if manager else "СВОБОДЕН"
    pill_color = ACCENT_CYAN if manager else MUTED
    op_bbox = draw.textbbox((0, 0), pill_text, font=font_badge_sm)
    op_w = (op_bbox[2] - op_bbox[0]) + 20 * SCALE
    op_h = 28 * SCALE
    op_x = CARD_WIDTH - CARD_PADDING - 16 * SCALE - op_w
    op_y = curr_y + (OWNER_CARD_H - op_h) // 2
    _draw_rounded_rect(draw, (op_x, op_y, op_x + op_w, op_y + op_h),
                       radius=8 * SCALE, fill=(18, 30, 48) if manager else (24, 28, 36),
                       outline=pill_color, width=1)
    draw.text((op_x + 10 * SCALE, op_y + 5 * SCALE), pill_text, font=font_badge_sm, fill=pill_color)

    curr_y += OWNER_CARD_H + 16 * SCALE

    # ── 7. FOOTER ──────────────────────────────────────────────────────────
    footer_text = "LOGOVOBOT • КИБЕРФУТБОЛЬНАЯ ПРЕМЬЕР-ЛИГА 2026"
    f_bbox = draw.textbbox((0, 0), footer_text, font=font_sm)
    draw.text(((CARD_WIDTH - (f_bbox[2] - f_bbox[0])) // 2, curr_y + 2 * SCALE), footer_text, font=font_sm, fill=MUTED)

    # ── Export to buffer ───────────────────────────────────────────────────
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
