"""Таблица дивизиона не теряет тренеров с похожими именами клубов.

Исходная жалоба по P3-7 звучала так: в дивизионе три клуба, а в таблице две строки —
один тренер исчезает. Причина не в самой таблице, а в резолве имени: `get_standings`
индексирует аккумулятор каноническим именем (`teams[canon]`), поэтому два разных клуба,
схлопнутых резолвером в одно имя, делят одну строку и второй перезаписывает первого.

Проверено на коде до фикса (коммит 1c4fb41): `Расинг Сантандер`, `Расинг Ланс` и
`Расинг` давали `Расинг` все три, в том числе с uuid-суффиксом, — то есть этот файл
на прежнем резолвере красный, а не «зелёный по случайности».

Тесты в `test_team_name_resolution.py` проверяют резолвер в изоляции. Здесь — сквозной
путь от `users.team_name` до строк таблицы, то, что видит тренер.
"""
import unittest
import uuid

import database


class TestStandingsNameCollision(unittest.TestCase):
    """Один дивизион, три клуба с коллизионными именами, полный круг матчей."""

    def setUp(self):
        database.init_db()

        # Файлы тестов бегут параллельно против одной league.db, поэтому всё, что
        # уникально в схеме (код дивизиона, имя клуба, telegram_id), получает суффикс.
        self.uid = uuid.uuid4().hex[:6].upper()
        base = 960_000_000 + uuid.uuid4().int % 1_000_000

        self.division_id = database.create_division(
            name=f"COL Дивизион {self.uid}", code=f"COL_{self.uid}"
        )

        # Имена из репро: прежний резолвер сводил все три к «Расинг».
        self.club_a = f"Расинг Сантандер {self.uid}"
        self.club_b = f"Расинг Ланс {self.uid}"
        self.club_c = f"Расинг {self.uid}"

        self.coach_a, self.coach_b, self.coach_c = base, base + 1, base + 2
        self.clubs = {
            self.coach_a: self.club_a,
            self.coach_b: self.club_b,
            self.coach_c: self.club_c,
        }

        for tg_id, club in self.clubs.items():
            database.register_user(tg_id, f"col_{tg_id}_{self.uid}", team_name=club)
            database.assign_user_division(tg_id, self.division_id)

        season = database.get_active_season()
        self.season_id = season["id"] if season else 1

        # Полный круг: каждый клуб играет дважды. A 3:1 B, B 2:2 C, C 0:1 A.
        self.results = (
            (self.coach_a, self.club_a, self.coach_b, self.club_b, 3, 1),
            (self.coach_b, self.club_b, self.coach_c, self.club_c, 2, 2),
            (self.coach_c, self.club_c, self.coach_a, self.club_a, 0, 1),
        )
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO rounds (round_number, is_open, deadline, division_id) "
                "VALUES (1, 0, ?, ?)",
                ("01.01.2030 00:00", self.division_id),
            )
            for p1_id, p1_team, p2_id, p2_team, s1, s2 in self.results:
                c.execute(
                    "INSERT INTO matches (round_number, player1_id, player2_id, "
                    "player1_team, player2_team, player1_score, player2_score, "
                    "status, division_id, season_id, tournament_type) "
                    "VALUES (1, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, 'league')",
                    (p1_id, p2_id, p1_team, p2_team, s1, s2,
                     self.division_id, self.season_id),
                )

    def tearDown(self):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM matches WHERE division_id = ?", (self.division_id,))
            c.execute("DELETE FROM rounds WHERE division_id = ?", (self.division_id,))
            c.execute(
                "DELETE FROM users WHERE telegram_id IN (?, ?, ?)",
                (self.coach_a, self.coach_b, self.coach_c),
            )
            c.execute("DELETE FROM divisions WHERE id = ?", (self.division_id,))

    def test_three_clubs_give_three_rows(self):
        """Прямое опровержение жалобы: три клуба — три строки, а не две."""
        standings = database.get_standings(division_id=self.division_id)

        self.assertEqual(
            len(standings),
            3,
            "Строк в таблице меньше, чем клубов в дивизионе: "
            f"{[row['team_name'] for row in standings]}",
        )

    def test_no_coach_disappears_from_the_table(self):
        """У каждой строки свой тренер, и ни один из трёх не пропал."""
        standings = database.get_standings(division_id=self.division_id)

        ids = [row["telegram_id"] for row in standings]
        self.assertEqual(len(ids), len(set(ids)), f"Тренер повторяется в таблице: {ids}")
        self.assertEqual(set(ids), set(self.clubs), "Состав тренеров в таблице не совпадает")

    def test_each_club_keeps_its_own_name(self):
        """Имя клуба в таблице — то, что ввёл тренер, без подмены каноном КПЛ."""
        standings = database.get_standings(division_id=self.division_id)

        by_id = {row["telegram_id"]: row["team_name"] for row in standings}
        for tg_id, club in self.clubs.items():
            self.assertEqual(by_id.get(tg_id), club)

    def test_results_are_not_double_counted(self):
        """Суммы сходятся: схлопывание имён раньше приписывало матчи чужой строке."""
        standings = database.get_standings(division_id=self.division_id)
        by_id = {row["telegram_id"]: row for row in standings}

        expected = {
            # played, wins, draws, losses, points, goals_scored, goals_conceded
            self.coach_a: (2, 2, 0, 0, 6, 4, 1),
            self.coach_b: (2, 0, 1, 1, 1, 3, 5),
            self.coach_c: (2, 0, 1, 1, 1, 2, 3),
        }
        for tg_id, (played, wins, draws, losses, points, gs, gc) in expected.items():
            row = by_id[tg_id]
            with self.subTest(club=self.clubs[tg_id]):
                self.assertEqual(row["played"], played)
                self.assertEqual(row["wins"], wins)
                self.assertEqual(row["draws"], draws)
                self.assertEqual(row["losses"], losses)
                self.assertEqual(row["points"], points)
                self.assertEqual(row["goals_scored"], gs)
                self.assertEqual(row["goals_conceded"], gc)

        # 3 матча: два результативных по 3 очка плюс ничья по 1 очку.
        self.assertEqual(sum(row["points"] for row in standings), 8)

    def test_similar_club_from_another_division_stays_out(self):
        """Скоуп по дивизиону держится и на похожих именах — строка не подхватывает чужого."""
        other_uid = uuid.uuid4().hex[:6].upper()
        other_division = database.create_division(
            name=f"COL Сосед {other_uid}", code=f"COLN_{other_uid}"
        )
        other_coach = 961_000_000 + uuid.uuid4().int % 1_000_000
        other_club = f"Расинг Сантандер {other_uid}"
        database.register_user(other_coach, f"col_n_{other_uid}", team_name=other_club)
        database.assign_user_division(other_coach, other_division)
        try:
            standings = database.get_standings(division_id=self.division_id)

            self.assertEqual(len(standings), 3)
            self.assertNotIn(other_coach, [row["telegram_id"] for row in standings])
            self.assertNotIn(other_club, [row["team_name"] for row in standings])
        finally:
            with database.transaction() as conn:
                c = conn.cursor()
                c.execute("DELETE FROM users WHERE telegram_id = ?", (other_coach,))
                c.execute("DELETE FROM divisions WHERE id = ?", (other_division,))


if __name__ == "__main__":
    unittest.main()
