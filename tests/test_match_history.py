"""get_match_history resolves both sides' telegram ids from team names."""
import database


def _seed():
    with database.transaction() as conn:
        conn.execute("DELETE FROM matches")
        conn.execute("DELETE FROM users WHERE telegram_id IN (9001, 9002)")
        conn.execute("INSERT INTO users (telegram_id, username, team_name) VALUES (9001, 'home', 'Хомтим')")
        conn.execute("INSERT INTO users (telegram_id, username, team_name) VALUES (9002, 'away', 'Эвэйтим')")
        conn.execute(
            "INSERT INTO matches (round_number, player1_team, player2_team, player1_score, player2_score, status, played_at) "
            "VALUES (1, 'Хомтим', 'Эвэйтим', 3, 1, 'confirmed', CURRENT_TIMESTAMP)"
        )


def test_history_returns_side_ids_and_opponent():
    _seed()
    rows = database.get_match_history(9002)
    assert len(rows) == 1
    row = rows[0]
    assert row["player1_id"] == 9001
    assert row["player2_id"] == 9002
    assert row["opponent_id"] == 9001
    assert row["opponent_team"] == "Хомтим"


def test_history_empty_for_user_without_team():
    assert database.get_match_history(123456789) == []
