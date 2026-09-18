"""Массовая привязка участников к клубам обязана быть предсказуемой.

`scripts/bind_clubs_to_players.py` за один прогон переписывает `users.team_name` у
всей лиги и попутно обнуляет варны — ошибка в нём стоит дороже, чем в экране
привязки, потому что заметна только по расписанию через несколько дней.

Проверяем три вещи: таблица привязок сходится с сезонным ростером, план
собирается и валидируется до первой записи, а применение атомарно и идемпотентно.
"""
import unittest
from unittest.mock import patch

import config
import database
from scripts.bind_clubs_to_players import (
    BINDINGS,
    PlanError,
    apply_division,
    build_plan,
    find_user,
    resolve_club,
)
from scripts.import_division_players import PLAYERS_BY_DIVISION


class TestTheBindingTable(unittest.TestCase):
    """Данные в таблице — тоже код, и ломаются они молча."""

    def test_every_division_covers_its_whole_roster(self):
        """Неполный дивизион оставит клубы без владельцев, лишнее имя — упадёт
        на resolve_club уже в проде."""
        for number, pairs in BINDINGS.items():
            with self.subTest(division=number):
                roster = config.DIVISION_CLUBS[f"DIV_{number}"]
                self.assertEqual(sorted(pairs), sorted(roster))

    def test_division_one_is_not_in_the_table(self):
        """Первый дивизион расставлен вручную; прогон по нему сбросил бы варны."""
        self.assertNotIn(1, BINDINGS)

    def test_nobody_holds_two_clubs(self):
        usernames = [u.lower() for pairs in BINDINGS.values() for u in pairs.values()]

        self.assertEqual(len(usernames), len(set(usernames)))

    def test_the_same_people_are_in_the_import_script(self):
        """Два списка участников живут в разных файлах, и разъезжаются они молча:
        опечатка в одном заводит второй аккаунт тому же человеку — так в дивизионе
        2 появился призрак @Davtyan рядом с живым @Davtyan_55."""
        for number, pairs in BINDINGS.items():
            with self.subTest(division=number):
                imported = {u.lower() for u in PLAYERS_BY_DIVISION[number]}
                bound = {u.lower() for u in pairs.values()}
                self.assertEqual(imported, bound)


class TestResolvingAClub(unittest.TestCase):
    roster = config.DIVISION_CLUBS["DIV_2"]

    def test_a_spelling_variant_resolves_through_normalization(self):
        """«е» вместо «э» — самый частый способ написать клуб мимо ростера."""
        self.assertEqual(resolve_club("Фулхем", self.roster), "Фулхэм")

    def test_case_does_not_matter(self):
        self.assertEqual(resolve_club("аякс", self.roster), "Аякс")

    def test_an_unknown_club_fails_with_a_suggestion(self):
        """Подсказываем, но не подставляем: угаданный клуб уедет чужому тренеру."""
        with self.assertRaises(PlanError) as ctx:
            resolve_club("Бурнам", self.roster)

        self.assertIn("Бурирам", str(ctx.exception))

    def test_a_club_from_another_division_is_not_accepted(self):
        with self.assertRaises(PlanError):
            resolve_club("Арсенал", self.roster)


class TestFindingAUser(unittest.TestCase):
    def setUp(self):
        database.handle_user_startup(9100, "binder_one")
        database.set_player_club("binder_one", "Аякс")

    def tearDown(self):
        database.clear_player_club(9100)

    def test_a_username_is_found_case_insensitively(self):
        self.assertEqual(find_user("BINDER_ONE")["telegram_id"], 9100)

    def test_a_club_name_is_not_a_username(self):
        """`find_user_by_ref` ищет ещё и по названию клуба — этот путь нам вреден:
        совпадение отдало бы клуб человеку, которого в списке нет."""
        self.assertIsNone(find_user("Аякс"))


class TestBuildingThePlan(unittest.TestCase):
    """План собирается целиком до записи: половина применённой расстановки хуже
    неприменённой."""

    def setUp(self):
        self.div = database.get_division_by_code("DIV_2")
        database.handle_user_startup(9201, "settled")
        database.handle_user_startup(9202, "misplaced")
        database.handle_user_startup(9203, "clubless")
        database.set_player_club("settled", "Аякс")
        database.assign_user_division(9201, self.div["id"])
        database.set_player_club("misplaced", "Монако")

    def tearDown(self):
        for telegram_id in (9201, 9202, 9203):
            database.clear_player_club(telegram_id)
            database.assign_user_division(telegram_id, None)

    def _plan(self, pairs: dict[str, str], create_missing: bool = False):
        with patch.dict(BINDINGS, {2: pairs}, clear=False):
            return build_plan([2], create_missing=create_missing)[0]

    def test_actions_are_classified_by_the_current_state(self):
        plan = self._plan({
            "Аякс": "settled",
            "Спортинг": "misplaced",
            "Валенсия": "clubless",
            "Сельта": "never_seen",
        })

        self.assertEqual([b.action for b in plan.bindings], ["keep", "rebind", "bind", "missing"])

    def test_create_missing_turns_an_unknown_user_into_a_registration(self):
        plan = self._plan({"Сельта": "never_seen"}, create_missing=True)

        self.assertEqual(plan.bindings[0].action, "create")

    def test_clubs_left_without_an_owner_are_reported(self):
        plan = self._plan({"Аякс": "settled"})

        self.assertEqual(len(plan.unclaimed), len(config.DIVISION_CLUBS["DIV_2"]) - 1)
        self.assertNotIn("Аякс", plan.unclaimed)

    def test_one_user_on_two_clubs_is_rejected(self):
        with self.assertRaises(PlanError) as ctx:
            self._plan({"Аякс": "settled", "Сельта": "settled"})

        self.assertIn("settled", str(ctx.exception))


class TestApplyingThePlan(unittest.TestCase):
    def setUp(self):
        self.div = database.get_division_by_code("DIV_3")
        database.handle_user_startup(9301, "runner_one")
        database.handle_user_startup(9302, "runner_two")

    def tearDown(self):
        for telegram_id in (9301, 9302):
            database.clear_player_club(telegram_id)
            database.assign_user_division(telegram_id, None)

    def _apply(self, pairs: dict[str, str]):
        with patch.dict(BINDINGS, {3: pairs}, clear=False):
            plan = build_plan([3], create_missing=False)[0]
            return plan, apply_division(plan)

    def test_binding_sets_both_the_club_and_the_division(self):
        """Участник мог прийти из другого дивизиона или вообще ниоткуда — без
        division_id он не попадёт ни в таблицу, ни в расписание."""
        _, (changed, problems) = self._apply({"Лион": "runner_one", "Комо": "runner_two"})

        self.assertEqual((changed, problems), (2, []))
        self.assertEqual(database.get_user(9301)["team_name"], "Лион")
        self.assertEqual(database.get_user(9301)["division_id"], self.div["id"])

    def test_a_second_run_writes_nothing(self):
        self._apply({"Лион": "runner_one"})

        plan, (changed, problems) = self._apply({"Лион": "runner_one"})

        self.assertEqual([b.action for b in plan.bindings], ["keep"])
        self.assertEqual((changed, problems), (0, []))

    def test_a_failure_rolls_the_whole_division_back(self):
        """transaction() реентрантен, поэтому весь дивизион пишется одной
        транзакцией: упавший на середине прогон не оставляет клубы отобранными
        у прежних владельцев."""
        with patch("scripts.bind_clubs_to_players.database.assign_user_division",
                   side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self._apply({"Лион": "runner_one", "Комо": "runner_two"})

        self.assertIsNone(database.get_user(9301)["team_name"])
        self.assertIsNone(database.get_user(9302)["team_name"])


if __name__ == "__main__":
    unittest.main()
