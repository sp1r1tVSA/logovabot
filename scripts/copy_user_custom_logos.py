import os
from PIL import Image

SRC_DIR = r"C:\Users\Ислам\Desktop\Projects\logovobot\assets\Расинг, АЕК, Фейенорд"
DEST_DIR = r"C:\Users\Ислам\Desktop\Projects\logovobot\assets\logos"

mapping = {
    "АЕК.png": "aek.png",
    "Расинг.png": "racing.png",
    "Фейенорд.png": "feyenoord.png"
}

def copy_custom_logos():
    for src_file, dest_file in mapping.items():
        src_path = os.path.join(SRC_DIR, src_file)
        dest_path = os.path.join(DEST_DIR, dest_file)
        if os.path.exists(src_path):
            im = Image.open(src_path).convert("RGBA")
            im = im.resize((128, 128), Image.Resampling.LANCZOS)
            im.save(dest_path, "PNG")
            print(f"✓ Successfully copied and resized {src_file} -> {dest_file} in assets/logos/!")

if __name__ == "__main__":
    copy_custom_logos()
