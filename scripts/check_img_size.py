from PIL import Image

img_path = r"C:\Users\Ислам\Desktop\Projects\logovobot\Туры\Снимок экрана 2026-07-22 163056.png"
img = Image.open(img_path)
print("Image size:", img.size) # (width, height)
