"""
sandbox/preview_fc_card.py

Standalone offline sandbox script to test and preview 3 distinct EA FC card designs.
Renders all 3 designs into sandbox/output/ for visual comparison.
"""

import os
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fc_card_generator import generate_ea_fc_card

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def run_tests():
    print("🧪 Rendering 3 Distinct Card Designs for Visual Review...")

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

    # 1. Design 1: Cyber Hybrid / Modern Broadcast
    buf_1 = generate_ea_fc_card(test_player, theme_name="design_1")
    path_1 = os.path.join(OUTPUT_DIR, "design_1_cyber.png")
    with open(path_1, "wb") as f:
        f.write(buf_1.getvalue())
    print(f"✅ Generated Design 1 [Cyber Broadcast]: {path_1}")

    # 2. Design 2: Authentic EA FC 25 FUT Shield
    buf_2 = generate_ea_fc_card(test_player, theme_name="design_2")
    path_2 = os.path.join(OUTPUT_DIR, "design_2_fut_shield.png")
    with open(path_2, "wb") as f:
        f.write(buf_2.getvalue())
    print(f"✅ Generated Design 2 [EA FC FUT Shield]: {path_2}")

    # 3. Design 3: Obsidian Luxury VIP / Editorial Poster
    buf_3 = generate_ea_fc_card(test_player, theme_name="design_3")
    path_3 = os.path.join(OUTPUT_DIR, "design_3_luxury_poster.png")
    with open(path_3, "wb") as f:
        f.write(buf_3.getvalue())
    print(f"✅ Generated Design 3 [Obsidian Luxury Poster]: {path_3}")

    print("\n🎉 All 3 designs generated successfully in sandbox/output/!")

if __name__ == "__main__":
    run_tests()
