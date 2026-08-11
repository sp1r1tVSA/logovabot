import os
import io
from PIL import Image, ImageDraw, ImageFont

from table_generator import TEAM_LOGO_MAP, load_font
import database

BASE_DIR = os.path.dirname(__file__)
ASSETS_DIR = os.path.join(BASE_DIR, '..', 'assets')

def get_logo_path(team_name: str) -> str | None:
    filename = TEAM_LOGO_MAP.get(team_name)
    if not filename:
        return None
    path = os.path.join(ASSETS_DIR, 'logos', filename)
    return path if os.path.exists(path) else None

# Colors
BG_COLOR = (20, 20, 22)
BOX_BG = (26, 26, 30)
BOX_BORDER = (45, 45, 50)
TEXT_COLOR = (255, 255, 255)
MUTED_TEXT = (156, 163, 175)
LINE_COLOR = (75, 85, 99)
ACCENT_COLOR = (239, 68, 68)

SCALE = 2
BOX_WIDTH = 180 * SCALE
BOX_HEIGHT = 50 * SCALE
LOGO_SIZE = 24 * SCALE
SCORE_BOX_W = 24 * SCALE
PADDING = 8 * SCALE

def draw_match_box(draw: ImageDraw.Draw, img: Image.Image, x: int, y: int, series):
    # Draw background
    draw.rectangle([x, y, x + BOX_WIDTH, y + BOX_HEIGHT], fill=BOX_BG, outline=BOX_BORDER, width=2)
    
    font = load_font(12 * SCALE)
    font_bold = load_font(12 * SCALE, bold=True)
    
    if not series:
        # TBD Box
        draw.text((x + BOX_WIDTH//2, y + BOX_HEIGHT//2), "TBD", fill=MUTED_TEXT, font=font, anchor="mm")
        return
        
    # Convert series to dict if it's an object for easier access
    t1_name = series.team1_name if hasattr(series, 'team1_name') else series.get('team1_name', 'TBD')
    t2_name = series.team2_name if hasattr(series, 'team2_name') else series.get('team2_name', 'TBD')
    s1 = str(series.team1_wins if hasattr(series, 'team1_wins') else series.get('team1_wins', 0))
    s2 = str(series.team2_wins if hasattr(series, 'team2_wins') else series.get('team2_wins', 0))
    
    # Team 1 (Top half)
    t1_y = y + PADDING
    logo1_path = get_logo_path(t1_name)
    if logo1_path and os.path.exists(logo1_path):
        try:
            l1 = Image.open(logo1_path).convert("RGBA").resize((LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
            img.paste(l1, (x + PADDING, t1_y), l1)
        except Exception: pass
    
    draw.text((x + PADDING * 2 + LOGO_SIZE, t1_y + LOGO_SIZE//2), t1_name[:12], fill=TEXT_COLOR, font=font_bold, anchor="lm")
    draw.text((x + BOX_WIDTH - PADDING - SCORE_BOX_W//2, t1_y + LOGO_SIZE//2), s1, fill=TEXT_COLOR, font=font_bold, anchor="mm")
    
    # Separation line
    mid_y = y + BOX_HEIGHT//2
    draw.line([x, mid_y, x + BOX_WIDTH, mid_y], fill=BOX_BORDER, width=1)
    
    # Team 2 (Bottom half)
    t2_y = mid_y + (BOX_HEIGHT//2 - LOGO_SIZE)//2
    logo2_path = get_logo_path(t2_name)
    if logo2_path and os.path.exists(logo2_path):
        try:
            l2 = Image.open(logo2_path).convert("RGBA").resize((LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
            img.paste(l2, (x + PADDING, t2_y), l2)
        except Exception: pass
        
    draw.text((x + PADDING * 2 + LOGO_SIZE, t2_y + LOGO_SIZE//2), t2_name[:12], fill=TEXT_COLOR, font=font_bold, anchor="lm")
    draw.text((x + BOX_WIDTH - PADDING - SCORE_BOX_W//2, t2_y + LOGO_SIZE//2), s2, fill=TEXT_COLOR, font=font_bold, anchor="mm")

def draw_bracket_connection(draw, start_box, end_box, direction):
    # direction: 1 for left-to-right, -1 for right-to-left
    start_x = start_box[0] + BOX_WIDTH if direction == 1 else start_box[0]
    start_y = start_box[1] + BOX_HEIGHT // 2
    
    end_x = end_box[0] if direction == 1 else end_box[0] + BOX_WIDTH
    end_y = end_box[1] + BOX_HEIGHT // 2
    
    mid_x = start_x + (end_x - start_x) // 2
    
    # Draw elbow line
    draw.line([start_x, start_y, mid_x, start_y], fill=LINE_COLOR, width=2*SCALE)
    draw.line([mid_x, start_y, mid_x, end_y], fill=LINE_COLOR, width=2*SCALE)
    draw.line([mid_x, end_y, end_x, end_y], fill=LINE_COLOR, width=2*SCALE)

def generate_bracket_image() -> io.BytesIO:
    # 1/8 -> 8 matches
    # 1/4 -> 4 matches
    # 1/2 -> 2 matches
    # Final -> 1 match
    
    # Fetch all data
    cup_series_list = []
    for stage in ['1/8', '1/4', '1/2', 'final']:
        cup_series_list.extend(database.get_cup_series_list(stage))
        
    # Dimensions
    CANVAS_W = 1200 * SCALE
    CANVAS_H = 700 * SCALE
    
    img = Image.new("RGBA", (CANVAS_W, CANVAS_H), BG_COLOR)
    draw = ImageDraw.Draw(img)
    
    # Draw Title
    font_title = load_font(28 * SCALE, bold=True)
    draw.text((CANVAS_W//2, 40 * SCALE), "КУБОК КПЛ", fill=ACCENT_COLOR, font=font_title, anchor="mm")
    
    # Organize data by stage
    stages = {'1/8': [], '1/4': [], '1/2': [], 'Final': []}
    for series in cup_series_list:
        stage = series.stage if hasattr(series, 'stage') else series.get('stage')
        if stage in stages:
            stages[stage].append(series)
            
    # Sort each stage by series_num
    for k in stages:
        if len(stages[k]) > 0:
            if hasattr(stages[k][0], 'series_num'):
                stages[k].sort(key=lambda x: x.series_num)
            else:
                stages[k].sort(key=lambda x: x.get('series_num', 0))
                
    # Pad to ensure correct length
    while len(stages['1/8']) < 8: stages['1/8'].append(None)
    while len(stages['1/4']) < 4: stages['1/4'].append(None)
    while len(stages['1/2']) < 2: stages['1/2'].append(None)
    while len(stages['Final']) < 1: stages['Final'].append(None)
    
    boxes = {} # Store coordinates for drawing lines: {(stage, idx): (x,y)}
    
    # Helper to calculate positions
    def get_y_positions(count, canvas_h, offset_y):
        spacing = (canvas_h - offset_y * 2) / (count if count > 1 else 2)
        if count == 1:
            return [canvas_h // 2 - BOX_HEIGHT//2]
        return [int(offset_y + i * spacing) for i in range(count)]

    MARGIN_X = 40 * SCALE
    COLUMN_SPACING = (CANVAS_W - MARGIN_X*2 - BOX_WIDTH) // 6 # 3 gaps each side
    
    # LEFT SIDE
    left_y_8 = get_y_positions(4, CANVAS_H, 120 * SCALE)
    for i in range(4):
        x = MARGIN_X
        y = left_y_8[i]
        boxes[('1/8', i)] = (x, y)
        draw_match_box(draw, img, x, y, stages['1/8'][i])
        
    left_y_4 = [ (left_y_8[0]+left_y_8[1])//2, (left_y_8[2]+left_y_8[3])//2 ]
    for i in range(2):
        x = MARGIN_X + COLUMN_SPACING
        y = left_y_4[i]
        boxes[('1/4', i)] = (x, y)
        draw_match_box(draw, img, x, y, stages['1/4'][i])
        # connections
        draw_bracket_connection(draw, boxes[('1/8', i*2)], boxes[('1/4', i)], 1)
        draw_bracket_connection(draw, boxes[('1/8', i*2+1)], boxes[('1/4', i)], 1)
        
    left_y_2 = [ (left_y_4[0]+left_y_4[1])//2 ]
    x = MARGIN_X + COLUMN_SPACING*2
    y = left_y_2[0]
    boxes[('1/2', 0)] = (x, y)
    draw_match_box(draw, img, x, y, stages['1/2'][0])
    draw_bracket_connection(draw, boxes[('1/4', 0)], boxes[('1/2', 0)], 1)
    draw_bracket_connection(draw, boxes[('1/4', 1)], boxes[('1/2', 0)], 1)
    
    # RIGHT SIDE
    right_y_8 = get_y_positions(4, CANVAS_H, 120 * SCALE)
    for i in range(4):
        x = CANVAS_W - MARGIN_X - BOX_WIDTH
        y = right_y_8[i]
        idx = i + 4
        boxes[('1/8', idx)] = (x, y)
        draw_match_box(draw, img, x, y, stages['1/8'][idx])
        
    right_y_4 = [ (right_y_8[0]+right_y_8[1])//2, (right_y_8[2]+right_y_8[3])//2 ]
    for i in range(2):
        x = CANVAS_W - MARGIN_X - BOX_WIDTH - COLUMN_SPACING
        y = right_y_4[i]
        idx = i + 2
        boxes[('1/4', idx)] = (x, y)
        draw_match_box(draw, img, x, y, stages['1/4'][idx])
        # connections
        draw_bracket_connection(draw, boxes[('1/8', (i+2)*2)], boxes[('1/4', idx)], -1)
        draw_bracket_connection(draw, boxes[('1/8', (i+2)*2+1)], boxes[('1/4', idx)], -1)
        
    right_y_2 = [ (right_y_4[0]+right_y_4[1])//2 ]
    x = CANVAS_W - MARGIN_X - BOX_WIDTH - COLUMN_SPACING*2
    y = right_y_2[0]
    boxes[('1/2', 1)] = (x, y)
    draw_match_box(draw, img, x, y, stages['1/2'][1])
    draw_bracket_connection(draw, boxes[('1/4', 2)], boxes[('1/2', 1)], -1)
    draw_bracket_connection(draw, boxes[('1/4', 3)], boxes[('1/2', 1)], -1)
    
    # FINAL
    x = CANVAS_W//2 - BOX_WIDTH//2
    y = CANVAS_H//2 - BOX_HEIGHT//2
    boxes[('Final', 0)] = (x, y)
    draw_match_box(draw, img, x, y, stages['Final'][0])
    draw_bracket_connection(draw, boxes[('1/2', 0)], boxes[('Final', 0)], 1)
    draw_bracket_connection(draw, boxes[('1/2', 1)], boxes[('Final', 0)], -1)
    
    # Label Stages
    font_stage = load_font(14 * SCALE, bold=True)
    draw.text((MARGIN_X + BOX_WIDTH//2, 80 * SCALE), "1/8 ФИНАЛА", fill=MUTED_TEXT, font=font_stage, anchor="mm")
    draw.text((CANVAS_W - MARGIN_X - BOX_WIDTH//2, 80 * SCALE), "1/8 ФИНАЛА", fill=MUTED_TEXT, font=font_stage, anchor="mm")
    
    draw.text((MARGIN_X + COLUMN_SPACING + BOX_WIDTH//2, 80 * SCALE), "1/4 ФИНАЛА", fill=MUTED_TEXT, font=font_stage, anchor="mm")
    draw.text((CANVAS_W - MARGIN_X - COLUMN_SPACING - BOX_WIDTH//2, 80 * SCALE), "1/4 ФИНАЛА", fill=MUTED_TEXT, font=font_stage, anchor="mm")
    
    draw.text((MARGIN_X + COLUMN_SPACING*2 + BOX_WIDTH//2, 80 * SCALE), "ПОЛУФИНАЛ", fill=MUTED_TEXT, font=font_stage, anchor="mm")
    draw.text((CANVAS_W - MARGIN_X - COLUMN_SPACING*2 - BOX_WIDTH//2, 80 * SCALE), "ПОЛУФИНАЛ", fill=MUTED_TEXT, font=font_stage, anchor="mm")
    
    draw.text((CANVAS_W//2, y - 20*SCALE), "ФИНАЛ", fill=ACCENT_COLOR, font=font_stage, anchor="mm")
    
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio
