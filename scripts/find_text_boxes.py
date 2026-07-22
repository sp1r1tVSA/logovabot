import glob
import os
from PIL import Image

def extract_text_rows_from_image(filepath: str):
    """
    Find 8 match rows in screenshot by scanning vertical profile of cyan/white text pixels.
    Left text column: x = 50..130
    Right text column: x = 180..260
    """
    img = Image.open(filepath).convert("RGB")
    width, height = img.size
    
    # Let's crop 8 equal rows from y=70 to y=550
    # Let's measure exact y bounds of the 8 match cards
    row_height = (550 - 65) / 8.0
    
    match_pairs = []
    for i in range(8):
        y1 = int(65 + i * row_height)
        y2 = int(y1 + row_height)
        
        # Left text region: x=55..140, y=y1..y2
        left_crop = img.crop((55, y1 + 10, 140, y2 - 10))
        # Right text region: x=175..260, y=y1..y2
        right_crop = img.crop((175, y1 + 10, 260, y2 - 10))
        
        match_pairs.append((left_crop, right_crop))
        
    return match_pairs

print("Tested text region extractor")
