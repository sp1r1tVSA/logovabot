import asyncio
import os
from PIL import Image, ImageOps, ImageEnhance
import winocr

async def test():
    img_path = r"C:\Users\Ислам\Desktop\Projects\logovobot\Туры\Снимок экрана 2026-07-22 163056.png"
    img = Image.open(img_path).convert("L")
    img = ImageOps.invert(img) # invert black background to white background
    img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
    
    res = await winocr.recognize_pil(img, lang="en-US")
    print("Recognized en-US:")
    print(res.text)

asyncio.run(test())
