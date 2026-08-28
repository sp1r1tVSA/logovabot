"""
sandbox/preview_fc_card.py

Standalone offline sandbox script to test and preview EA FC card generation:
- 3 Distinct Static Card Designs (PNG)
- 3 Distinct Animated Dynamic Card Styles (GIF)
"""

import os
import sys
import time

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fc_card_generator import generate_ea_fc_card, generate_animated_ea_fc_card

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def run_tests():
    print("🧪 Rendering Static & Animated Card Previews...")

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

    # 1. Static Designs
    print("\n--- 🖼️ STATIC DESIGNS ---")
    for name, theme in [
        ("design_1_cyber.png", "design_1"),
        ("design_2_fut_shield.png", "design_2"),
        ("design_3_luxury_poster.png", "design_3"),
    ]:
        buf = generate_ea_fc_card(test_player, theme_name=theme)
        out_path = os.path.join(OUTPUT_DIR, name)
        with open(out_path, "wb") as f:
            f.write(buf.getvalue())
        print(f"✅ Generated Static [{theme}]: {out_path}")

    # 2. Animated Looping Cards (GIF)
    print("\n--- 🎬 ANIMATED DYNAMIC CARDS ---")
    anim_styles = [
        ("anim_1_holo_shimmer.gif", "holo_shimmer", "1. Голографический блик (Holo Shimmer)"),
        ("anim_2_golden_sparks.gif", "golden_sparks", "2. Парящие золотые искры (Golden Sparks)"),
        ("anim_3_cyber_pulse.gif", "cyber_pulse", "3. Неоновый кибер-пульс (Cyber Pulse)"),
    ]

    for filename, style_id, desc in anim_styles:
        t0 = time.time()
        buf = generate_animated_ea_fc_card(test_player, anim_style=style_id)
        elapsed = time.time() - t0
        out_path = os.path.join(OUTPUT_DIR, filename)
        with open(out_path, "wb") as f:
            f.write(buf.getvalue())
        sz_kb = len(buf.getvalue()) / 1024.0
        print(f"✅ Generated Animated [{desc}]: {out_path} ({sz_kb:.1f} KB in {elapsed:.2f}s)")

    print("\n🎉 All static and animated preview cards rendered successfully!")

if __name__ == "__main__":
    run_tests()
