import os
from PIL import Image

ARTIFACTS_DIR = r"C:\Users\Ислам\.gemini\antigravity-ide\brain\6bc6161b-e0b0-43e3-9c0d-f6bc78177237"
TARGET_DIR = r"c:\Users\Ислам\Desktop\Projects\logovobot\assets\logos"

files_map = {
    "feyenoord.png": "media__1784712216498.png",
    "aek.png": "media__1784712230689.png",
    "racing.png": "media__1784712238544.png"
}

def crop_and_extract():
    for target_name, src_name in files_map.items():
        src_path = os.path.join(ARTIFACTS_DIR, src_name)
        dst_path = os.path.join(TARGET_DIR, target_name)
        
        if os.path.exists(src_path):
            im = Image.open(src_path).convert("RGBA")
            w, h = im.size
            # Crop the logo circle on the left (square crop based on height h)
            logo_crop = im.crop((0, 0, min(w, h), h))
            logo_crop = logo_crop.resize((128, 128), Image.Resampling.LANCZOS)
            logo_crop.save(dst_path, "PNG")
            print(f"✓ Successfully extracted {target_name} from user attached image {src_name} ({w}x{h})!")

if __name__ == "__main__":
    crop_and_extract()
