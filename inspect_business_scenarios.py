"""Script to trace match reporting logic for QA audit"""
import re

def trace_file(filepath, patterns):
    print(f"=== {filepath} ===")
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for i, line in enumerate(lines, 1):
        for pat in patterns:
            if re.search(pat, line, re.IGNORECASE):
                print(f"L{i}: {line.strip()[:120]}")
                break

print("1. Perspective Inversion & Player ID checks:")
trace_file("handlers/cabinet.py", ["player1_id", "player2_id", "submit_report_to_guest", "handle_confirm_score", "report_home_goals"])

print("\n2. Status transitions & button authorization:")
trace_file("handlers/cabinet.py", ["confirm_score_", "dispute_score_", "reported", "disputed"])

print("\n3. Goalscorers count validation:")
trace_file("handlers/cabinet.py", ["cb_pick_goal", "cb_skip_goals", "goals_count"])
