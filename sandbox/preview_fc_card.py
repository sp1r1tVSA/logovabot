"""
sandbox/preview_fc_card.py

Оффлайн-превью всей матрицы дизайнов карточки: 3 тира по диапазону OVR
× 5 дивизионов = 15 вариантов. Складывает PNG и MP4 в sandbox/output/.

Дивизион задаётся через division_name — resolve_theme разбирает номер из
названия, так что запускать можно без базы.
"""

import os
import sys
import time

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.graphics.fc_card_generator import (
    CARD_STYLES,
    generate_animated_ea_fc_card,
    generate_ea_fc_card,
)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# OVR берём из середины каждого диапазона, чтобы тир совпадал с ожидаемым.
TIER_OVR = {"kpl_standard": 82, "kpl_star": 89, "kpl_prime": 95}
DIVISIONS = [1, 2, 3, 4, 5]


def run_tests():
    total = len(CARD_STYLES) * len(DIVISIONS)
    print(f"🧪 RENDERING {total} CARD DESIGNS (OVR TIER × DIVISION)...")

    total_t0 = time.time()
    idx = 0

    for style_id, cfg in CARD_STYLES.items():
        for div in DIVISIONS:
            idx += 1
            test_player = {
                "player_name": "ROONY BARDGHJI",
                "team_name": "АЕК",
                "position": "CAM",
                "total_goals": 18,
                "total_assists": 9,
                "matches_played": 12,
                "ovr": TIER_OVR[style_id],
                "division_name": f"Дивизион {div}",
                "custom_stats": {
                    "PAC": 96, "SHO": 98, "PAS": 99,
                    "DRI": 86, "DEF": 80, "PHY": 98,
                },
            }
            tag = f"{idx:02d}_{style_id}_div{div}"
            print(f"\n[{idx}/{total}] 🎨 {cfg['title']} · Дивизион {div}")

            t0 = time.time()
            buf_png = generate_ea_fc_card(test_player, theme_name=style_id)
            t_png = time.time() - t0
            png_path = os.path.join(OUTPUT_DIR, f"{tag}_static.png")
            with open(png_path, "wb") as f:
                f.write(buf_png.getvalue())
            print(f"  └─ 🖼️ PNG: {len(buf_png.getvalue()) / 1024.0:.1f} KB in {t_png:.2f}s -> {png_path}")

            t0 = time.time()
            buf_mp4 = generate_animated_ea_fc_card(test_player, anim_style=style_id)
            t_mp4 = time.time() - t0
            mp4_path = os.path.join(OUTPUT_DIR, f"{tag}_animated.mp4")
            with open(mp4_path, "wb") as f:
                f.write(buf_mp4.getvalue())
            print(f"  └─ 🎬 MP4: {len(buf_mp4.getvalue()) / 1024.0:.1f} KB in {t_mp4:.2f}s -> {mp4_path}")

    print(f"\n🎉 ALL {total} DESIGNS RENDERED IN {time.time() - total_t0:.2f}s!")


if __name__ == "__main__":
    run_tests()
