"""
sandbox/preview_fc_card.py

Standalone offline sandbox script to test and preview EA FC player card generation.
Saves rendered PNG cards directly into sandbox/output/ for rapid visual testing.
"""

import os
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fc_card_generator import generate_ea_fc_card

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def run_tests():
    print("🧪 Running EA FC Card Sandbox Previews...")

    # 1. Test Gold Rare Card (Striker)
    player_gold = {
        "player_name": "VINICIUS JR.",
        "team_name": "Спортинг",
        "position": "LW",
        "total_goals": 15,
        "total_assists": 8,
        "matches_played": 10,
        "ovr": 92,
        "custom_stats": {
            "PAC": 96,
            "SHO": 87,
            "PAS": 83,
            "DRI": 93,
            "DEF": 36,
            "PHY": 78
        }
    }
    buf_gold = generate_ea_fc_card(player_gold, theme_name="gold_rare")
    gold_path = os.path.join(OUTPUT_DIR, "preview_gold_card.png")
    with open(gold_path, "wb") as f:
        f.write(buf_gold.getvalue())
    print(f"✅ Generated Gold Rare Preview: {gold_path}")

    # 2. Test TOTW Inform Card (Midfielder)
    player_totw = {
        "player_name": "GYÖKERES",
        "team_name": "Спортинг",
        "position": "ST",
        "total_goals": 22,
        "total_assists": 5,
        "matches_played": 12,
        "ovr": 90,
    }
    buf_totw = generate_ea_fc_card(player_totw, theme_name="totw")
    totw_path = os.path.join(OUTPUT_DIR, "preview_totw_card.png")
    with open(totw_path, "wb") as f:
        f.write(buf_totw.getvalue())
    print(f"✅ Generated TOTW Inform Preview: {totw_path}")

    # 3. Test Icon Card (Defender)
    player_icon = {
        "player_name": "MALINI",
        "team_name": "Бенфика",
        "position": "CB",
        "total_goals": 3,
        "total_assists": 2,
        "matches_played": 14,
        "ovr": 94,
    }
    buf_icon = generate_ea_fc_card(player_icon, theme_name="icon")
    icon_path = os.path.join(OUTPUT_DIR, "preview_icon_card.png")
    with open(icon_path, "wb") as f:
        f.write(buf_icon.getvalue())
    print(f"✅ Generated Icon Legend Preview: {icon_path}")

    print("\n🎉 All preview cards rendered successfully in sandbox/output/!")

if __name__ == "__main__":
    run_tests()
