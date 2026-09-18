#!/usr/bin/env python3
"""
scripts/recalculate_odds.py

Standalone CLI utility to recalculate and update betting odds in SQLite
for all unplayed matches across bet_markets and relational match_markets.
Uses the calibrated Bivariate Poisson distribution engine.
"""

import sys
import os

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from services.betting_engine import regenerate_all_active_markets

def main():
    print("🔄 Initializing database and calculating fresh Poisson odds...")
    database.init_db()
    count = regenerate_all_active_markets()
    print(f"✅ Successfully refreshed betting markets for {count} active fixtures!")

if __name__ == "__main__":
    main()
