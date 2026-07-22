import os
import glob
from PIL import Image

# Map short codes/filenames to canonical club names in DB
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

def get_emblem_crops(img: Image.Image):
    """
    Given a screenshot (312x562), return a list of 8 pairs:
    [ (home_emblem_crop, away_emblem_crop), ... ]
    """
    rows = []
    y_start = 68
    row_h = (540 - 68) / 8.0
    
    for i in range(8):
        y1 = int(y_start + i * row_h + 12)
        y2 = int(y1 + 28)
        
        # Left emblem: x = 12..42
        home_crop = img.crop((12, y1, 42, y2)).convert("RGB").resize((30, 30))
        # Right emblem: x = 270..300
        away_crop = img.crop((270, y1, 300, y2)).convert("RGB").resize((30, 30))
        
        rows.append((home_crop, away_crop))
        
    return rows

def build_reference_emblems(r1_img_path: str):
    """
    Build reference PIL images for the 16 clubs using Round 1 screenshot.
    """
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
    img = Image.open(r1_img_path)
    crops = get_emblem_crops(img)
    
    ref_dict = {}
    for (home_code, away_code), (home_crop, away_crop) in zip(r1_pairs, crops):
        ref_dict[home_code] = home_crop.tobytes()
        ref_dict[away_code] = away_crop.tobytes()
        
    return ref_dict

def image_diff(bytes1: bytes, bytes2: bytes) -> float:
    return sum((a - b) * (a - b) for a, b in zip(bytes1, bytes2))

def match_emblem(crop: Image.Image, ref_dict: dict) -> str:
    crop_bytes = crop.tobytes()
    best_code = None
    min_diff = float("inf")
    
    for code, ref_bytes in ref_dict.items():
        diff = image_diff(crop_bytes, ref_bytes)
        if diff < min_diff:
            min_diff = diff
            best_code = code
            
    return best_code

def main():
    folder = r"C:\Users\Ислам\Desktop\Projects\logovobot\Туры"
    files = sorted(glob.glob(os.path.join(folder, "*.png")), key=lambda f: os.path.getmtime(f))
    
    if not files:
        print("No screenshots found!")
        return

    print(f"Found {len(files)} screenshots.")
    ref_dict = build_reference_emblems(files[0])
    
    schedule_out = []
    
    for r_idx, filepath in enumerate(files, 1):
        img = Image.open(filepath)
        crops = get_emblem_crops(img)
        
        schedule_out.append(f"{r_idx} Тур")
        for home_crop, away_crop in crops:
            home_code = match_emblem(home_crop, ref_dict)
            away_code = match_emblem(away_crop, ref_dict)
            
            home_name = CLUB_MAP[home_code]
            away_name = CLUB_MAP[away_code]
            schedule_out.append(f"{home_name} - {away_name}")
            
        schedule_out.append("") # blank line between rounds
        
    result_text = "\n".join(schedule_out)
    out_file = r"C:\Users\Ислам\Desktop\Projects\logovobot\schedule_30_rounds.txt"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(result_text)
        
    print(f"Successfully extracted {len(files)} rounds into {out_file}!")
    print("\n--- SAMPLE FIRST 3 ROUNDS ---")
    print("\n".join(schedule_out[:27]))

if __name__ == "__main__":
    main()
