import glob
import os
from PIL import Image

logos_dir = r"C:\Users\Ислам\Desktop\Projects\logovobot\assets\logos"
files = glob.glob(os.path.join(logos_dir, "*.png"))

print(f"Found {len(files)} official logos in assets/logos/:")
for f in files:
    name = os.path.splitext(os.path.basename(f))[0]
    img = Image.open(f)
    print(f" - {name} ({img.size})")
