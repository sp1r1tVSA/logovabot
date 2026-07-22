import os
from PIL import Image

img_path = r"C:\Users\Ислам\Desktop\Projects\logovobot\Туры\Снимок экрана 2026-07-22 163056.png"
img = Image.open(img_path)
os.makedirs("assets/tmp_crops", exist_ok=True)

header_crop = img.crop((50, 10, 260, 45))
header_crop.save("assets/tmp_crops/crop_header.png")

row0_left = img.crop((55, 110, 140, 150))
row0_left.save("assets/tmp_crops/crop_r0_left.png")

row0_right = img.crop((175, 110, 255, 150))
row0_right.save("assets/tmp_crops/crop_r0_right.png")

print("Saved cropped crops to assets/tmp_crops/")
