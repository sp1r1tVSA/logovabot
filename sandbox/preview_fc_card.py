"""
sandbox/preview_fc_card.py

Standalone offline preview runner to render and benchmark ALL 10 MOTION DESIGN STYLES.
Generates high-res PNG and animated GIF for all 10 styles into sandbox/output/.
"""

import os
import sys
import time

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.graphics.fc_card_generator import CARD_STYLES, generate_ea_fc_card, generate_animated_ea_fc_card

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def run_tests():
    print("🧪 RENDERING ALL 10 ULTIMATE MOTION DESIGN CARD STYLES...")

    test_player = {
        "player_name": "ROONY BARDGHJI",
        "team_name": "АЕК",
        "position": "CAM",
        "total_goals": 18,
        "total_assists": 9,
        "matches_played": 12,
        "ovr": 95,
        "custom_stats": {
            "PAC": 96,
            "SHO": 98,
            "PAS": 99,
            "DRI": 86,
            "DEF": 80,
            "PHY": 98
        }
    }

    total_t0 = time.time()

    for idx, (style_id, cfg) in enumerate(CARD_STYLES.items(), 1):
        print(f"\n[{idx}/10] 🎨 Rendering Style: {cfg['title']} ({style_id})")

        # 1. Static PNG
        t0 = time.time()
        buf_png = generate_ea_fc_card(test_player, theme_name=style_id)
        t_png = time.time() - t0
        png_path = os.path.join(OUTPUT_DIR, f"{idx:02d}_{style_id}_static.png")
        with open(png_path, "wb") as f:
            f.write(buf_png.getvalue())
        sz_png = len(buf_png.getvalue()) / 1024.0
        print(f"  └─ 🖼️ PNG: {sz_png:.1f} KB in {t_png:.2f}s -> {png_path}")

        # 2. Animated GIF (20 FPS seamless loop)
        t0 = time.time()
        buf_gif = generate_animated_ea_fc_card(test_player, anim_style=style_id)
        t_gif = time.time() - t0
        gif_path = os.path.join(OUTPUT_DIR, f"{idx:02d}_{style_id}_animated.gif")
        with open(gif_path, "wb") as f:
            f.write(buf_gif.getvalue())
        sz_gif = len(buf_gif.getvalue()) / 1024.0
        print(f"  └─ 🎬 GIF: {sz_gif:.1f} KB in {t_gif:.2f}s -> {gif_path}")

    total_elapsed = time.time() - total_t0
    print(f"\n🎉 ALL 10 STYLES RENDERED SUCCESSFULLY IN {total_elapsed:.2f}s!")

if __name__ == "__main__":
    run_tests()
