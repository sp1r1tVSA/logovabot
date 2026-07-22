import os
import glob
from PIL import Image

# Map short code to canonical club name
CLUB_MAP = {
    "БУДГ": "Будё Глимт",
    "БРЮГ": "Брюгге",
    "БЕНФ": "Бенфика",
    "РАСИ": "Расинг",
    "ФЕЙЕ": "Фейеноорд",
    "ПОРТ": "Порту",
    "СЕЛТ": "Селтик",
    "АЯКС": "Аякс",
    "ПСВ":  "ПСВ",
    "РЕЙН": "Рейнджерс",
    "РИВП": "Ривер Плейт",
    "АЕК":  "АЕК",
    "СПОР": "Спортинг",
    "БОКХ": "Бока Хуниорс",
    "БРАГ": "Брага",
    "КОПЕ": "Копенгаген"
}

# The 8 fixed match row y-centers in screenshot (size 312x562)
ROW_Y_CENTERS = [130, 190, 250, 310, 370, 430, 490, 550]

def get_row_crops(img: Image.Image):
    crops = []
    for yc in ROW_Y_CENTERS:
        y1, y2 = yc - 14, yc + 14
        # Crop home emblem (x: 12..42)
        home = img.crop((12, y1, 42, y2)).convert("RGB").resize((24, 24))
        # Crop away emblem (x: 270..300)
        away = img.crop((270, y1, 300, y2)).convert("RGB").resize((24, 24))
        crops.append((home, away))
    return crops

def build_references(r1_filepath: str):
    r1_pairs = [
        ("БУДГ", "БРЮГ"),
        ("БЕНФ", "РАСИ"),
        ("ФЕЙЕ", "ПОРТ"),
        ("СЕЛТ", "АЯКС"),
        ("ПСВ",  "РЕЙН"),
        ("РИВП", "АЕК"),
        ("СПОР", "БОКХ"),
        ("БРАГ", "КОПЕ"),
    ]
    img = Image.open(r1_filepath)
    crops = get_row_crops(img)
    
    ref = {}
    for (h_code, a_code), (h_crop, a_crop) in zip(r1_pairs, crops):
        ref[h_code] = h_crop.tobytes()
        ref[a_code] = a_crop.tobytes()
    return ref

def match_code(crop: Image.Image, ref: dict) -> str:
    cb = crop.tobytes()
    best_code = None
    min_diff = float("inf")
    for code, ref_b in ref.items():
        diff = sum((a - b) * (a - b) for a, b in zip(cb, ref_b))
        if diff < min_diff:
            min_diff = diff
            best_code = code
    return best_code

def main():
    folder = r"C:\Users\Ислам\Desktop\Projects\logovobot\Туры"
    files = sorted(glob.glob(os.path.join(folder, "*.png")), key=lambda f: os.path.getmtime(f))
    
    ref = build_references(files[0])
    
    output_lines = []
    
    for r_num, filepath in enumerate(files, 1):
        img = Image.open(filepath)
        crops = get_row_crops(img)
        
        output_lines.append(f"{r_num} Тур")
        for home_crop, away_crop in crops:
            h_code = match_code(home_crop, ref)
            a_code = match_code(away_crop, ref)
            
            h_team = CLUB_MAP[h_code]
            a_team = CLUB_MAP[a_code]
            output_lines.append(f"{h_team} - {a_team}")
        output_lines.append("") # Empty line between rounds
        
    out_path = r"C:\Users\Ислам\Desktop\Projects\logovobot\schedule_30_rounds.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))
        
    print(f"Generated {out_path} with all 30 rounds!")

if __name__ == "__main__":
    main()
