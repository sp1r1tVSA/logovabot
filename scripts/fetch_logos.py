import os
import urllib.request
from PIL import Image, ImageDraw, ImageFont

LOGOS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "logos")
os.makedirs(LOGOS_DIR, exist_ok=True)

# High-resolution Wikipedia/Wikimedia PNG logos matching official club emblems
CLUBS_INFO = {
    "Спортинг": {
        "filename": "sporting.png",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3e/Sporting_Clube_de_Portugal.png/300px-Sporting_Clube_de_Portugal.png",
        "alt_url": "https://media.api-sports.io/football/teams/228.png",
        "color": "#008053",
        "initials": "SCP"
    },
    "Ривер Плейт": {
        "filename": "river_plate.png",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ac/Escudo_del_C_A_River_Plate.png/300px-Escudo_del_C_A_River_Plate.png",
        "alt_url": "https://media.api-sports.io/football/teams/435.png",
        "color": "#D32F2F",
        "initials": "CARP"
    },
    "Бока Хуниорс": {
        "filename": "boca_juniors.png",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/41/Boca_Juniors_logo13.png/300px-Boca_Juniors_logo13.png",
        "alt_url": "https://media.api-sports.io/football/teams/451.png",
        "color": "#0D47A1",
        "initials": "CABJ"
    },
    "Бенфика": {
        "filename": "benfica.png",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a2/SL_Benfica_logo.svg/300px-SL_Benfica_logo.svg.png",
        "alt_url": "https://media.api-sports.io/football/teams/211.png",
        "color": "#E53935",
        "initials": "SLB"
    },
    "ПСВ": {
        "filename": "psv.png",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/05/PSV_Eindhoven.svg/300px-PSV_Eindhoven.svg.png",
        "alt_url": "https://media.api-sports.io/football/teams/197.png",
        "color": "#D32F2F",
        "initials": "PSV"
    },
    "Порту": {
        "filename": "porto.png",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f1/FC_Porto.svg/300px-FC_Porto.svg.png",
        "alt_url": "https://media.api-sports.io/football/teams/212.png",
        "color": "#1976D2",
        "initials": "FCP"
    },
    "Будё Глимт": {
        "filename": "bodo_glimt.png",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/bf/FK_Bod%C3%B8_Glimt.svg/300px-FK_Bod%C3%B8_Glimt.svg.png",
        "alt_url": "https://media.api-sports.io/football/teams/327.png",
        "color": "#FBC02D",
        "initials": "B/G"
    },
    "Фейеноорд": {
        "filename": "feyenoord.png",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e3/Feyenoord_logo.svg/300px-Feyenoord_logo.svg.png",
        "alt_url": "https://media.api-sports.io/football/teams/198.png",
        "color": "#D32F2F",
        "initials": "FEY"
    },
    "Селтик": {
        "filename": "celtic.png",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/35/Celtic_FC.svg/300px-Celtic_FC.svg.png",
        "alt_url": "https://media.api-sports.io/football/teams/247.png",
        "color": "#2E7D32",
        "initials": "CFC"
    },
    "Расинг": {
        "filename": "racing.png",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/56/Escudo_de_Racing_Club_%282014%29.svg/300px-Escudo_de_Racing_Club_%282014%29.svg.png",
        "alt_url": "https://media.api-sports.io/football/teams/458.png",
        "color": "#0288D1",
        "initials": "RAC"
    },
    "Аякс": {
        "filename": "ajax.png",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/79/Ajax_Amsterdam.svg/300px-Ajax_Amsterdam.svg.png",
        "alt_url": "https://media.api-sports.io/football/teams/194.png",
        "color": "#C62828",
        "initials": "AJX"
    },
    "Брага": {
        "filename": "braga.png",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/79/Sporting_Clube_de_Braga.svg/300px-Sporting_Clube_de_Braga.svg.png",
        "alt_url": "https://media.api-sports.io/football/teams/217.png",
        "color": "#C62828",
        "initials": "SCB"
    },
    "Рейнджерс": {
        "filename": "rangers.png",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/43/Rangers_FC.svg/300px-Rangers_FC.svg.png",
        "alt_url": "https://media.api-sports.io/football/teams/257.png",
        "color": "#1565C0",
        "initials": "RFC"
    },
    "Брюгге": {
        "filename": "brugge.png",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d0/Club_Brugge_KV_logo.svg/300px-Club_Brugge_KV_logo.svg.png",
        "alt_url": "https://media.api-sports.io/football/teams/569.png",
        "color": "#0288D1",
        "initials": "CLUB"
    },
    "Копенгаген": {
        "filename": "copenhagen.png",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/93/FC_Copenhagen_logo.svg/300px-FC_Copenhagen_logo.svg.png",
        "alt_url": "https://media.api-sports.io/football/teams/400.png",
        "color": "#1976D2",
        "initials": "FCK"
    },
    "АЕК": {
        "filename": "aek.png",
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/04/AEK_Athens_FC_logo.svg/300px-AEK_Athens_FC_logo.svg.png",
        "alt_url": "https://media.api-sports.io/football/teams/589.png",
        "color": "#FBC02D",
        "initials": "AEK"
    }
}

def create_circular_badge(text: str, color: str, size: tuple[int, int] = (128, 128)) -> Image.Image:
    """Generate a clean circular badge with team initials and primary color."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    margin = 4
    draw.ellipse([margin, margin, size[0] - margin, size[1] - margin], fill=color, outline="#FFFFFF", width=3)
    
    font_size = int(size[0] * 0.32)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except IOError:
        font = ImageFont.load_default()
        
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (size[0] - tw) / 2 - bbox[0]
    ty = (size[1] - th) / 2 - bbox[1]
    draw.text((tx, ty), text, fill="#FFFFFF", font=font)
    
    return img

def download_and_process_logos():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    for name, info in CLUBS_INFO.items():
        filepath = os.path.join(LOGOS_DIR, info["filename"])
        print(f"Processing logo for {name} ({info['filename']})...")
        
        urls_to_try = []
        if "url" in info: urls_to_try.append(info["url"])
        if "alt_url" in info: urls_to_try.append(info["alt_url"])
        
        success = False
        for url in urls_to_try:
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=6) as response:
                    data = response.read()
                    
                temp_path = filepath + ".tmp"
                with open(temp_path, "wb") as f:
                    f.write(data)
                    
                with Image.open(temp_path) as im:
                    im = im.convert("RGBA")
                    im = im.resize((128, 128), Image.Resampling.LANCZOS)
                    im.save(filepath, "PNG")
                    
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                success = True
                print(f"  ✓ Downloaded successfully from {url}")
                break
            except Exception as e:
                print(f"  ⚠️ URL download failed ({url}): {e}")

        if not success:
            badge = create_circular_badge(info["initials"], info["color"])
            badge.save(filepath, "PNG")
            print(f"  ✓ Created clean circular badge for {name}.")

if __name__ == "__main__":
    download_and_process_logos()
