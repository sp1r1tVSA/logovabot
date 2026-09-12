"""Дрейф между config.CLUB_REGISTRY и клубами в users.

Реестр правится руками, клубы заводят тренеры — списки расходятся молча, и узнать
об этом хочется не из жалобы «меня нет в таблице». `verify_registry_against_db`
показывает расхождение в обе стороны.

Проверяем на своих фикстурах со своим дивизионом, а не на живом ростере: файлы
тестов бегут параллельно против одной league.db и подсаживают туда собственных
пользователей, так что проверка «весь ростер лежит в реестре» на общей базе была бы
не стражем, а генератором случайных падений.
"""
import unittest
import uuid

import club_registry
import config
import database


class TestRegistryDrift(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self.uid = uuid.uuid4().hex[:6].upper()
        base = 962_000_000 + uuid.uuid4().int % 1_000_000

        self.division_id = database.create_division(
            name=f"DRF Дивизион {self.uid}", code=f"DRF_{self.uid}"
        )
        self.club_known = f"Дрейф Известный {self.uid}"
        self.club_unknown = f"Дрейф Неизвестный {self.uid}"
        self.coaches = (base, base + 1)

        for tg_id, club in zip(self.coaches, (self.club_known, self.club_unknown)):
            database.register_user(tg_id, f"drf_{tg_id}", team_name=club)
            database.assign_user_division(tg_id, self.division_id)

    def tearDown(self):
        # Порядок важен: сначала гасим подставной реестр, потом перезагружаем,
        # иначе кэш резолва останется с чужими именами.
        config.CLUB_REGISTRY = []
        club_registry.reload_registry()
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute(
                "DELETE FROM users WHERE telegram_id IN (?, ?)", self.coaches
            )
            c.execute("DELETE FROM divisions WHERE id = ?", (self.division_id,))

    def _with_registry(self, names):
        config.CLUB_REGISTRY = list(names)
        club_registry.reload_registry()

    def test_club_outside_the_registry_is_reported(self):
        """Главный случай: тренер завёл клуб, в реестр его добавить забыли."""
        self._with_registry([self.club_known])

        drift = database.verify_registry_against_db(division_id=self.division_id)

        self.assertEqual(drift["missing_in_registry"], [self.club_unknown])

    def test_full_registry_gives_no_drift(self):
        """Оба клуба в реестре — расхождения нет."""
        self._with_registry([self.club_known, self.club_unknown])

        drift = database.verify_registry_against_db(division_id=self.division_id)

        self.assertEqual(drift["missing_in_registry"], [])

    def test_registry_entry_without_a_coach_is_reported(self):
        """Обратная сторона: имя в реестре есть, а клуба под ним нет.

        Это либо опечатка в самом реестре, либо ушедший клуб — и то и другое
        стоит увидеть, потому что такое имя перетягивает на себя фаззи-резолв.
        """
        ghost = f"Дрейф Призрак {self.uid}"
        self._with_registry([self.club_known, self.club_unknown, ghost])

        drift = database.verify_registry_against_db()

        self.assertIn(ghost, drift["unused_in_registry"])
        self.assertNotIn(self.club_known, drift["unused_in_registry"])

    def test_division_scope_does_not_claim_missing_clubs(self):
        """В срезе дивизиона «неиспользованным» был бы весь остальной реестр — не отдаём."""
        self._with_registry([self.club_known, self.club_unknown, f"Дрейф Чужой {self.uid}"])

        drift = database.verify_registry_against_db(division_id=self.division_id)

        self.assertEqual(drift["unused_in_registry"], [])

    def test_case_and_spacing_do_not_count_as_drift(self):
        """Сверка идёт по нормализованной форме, а не по строке символ в символ."""
        self._with_registry([self.club_known.upper(), f"  {self.club_unknown}  "])

        drift = database.verify_registry_against_db(division_id=self.division_id)

        self.assertEqual(drift["missing_in_registry"], [])


if __name__ == "__main__":
    unittest.main()
