"""Резолв имени клуба: коллизии канонов при ростере в ~80 клубов (аудит, P3-7).

`resolve_team_name` матчит против 16 имён `config.KPL_TEAMS` и словаря `TEAM_ALIASES`.
На пяти дивизионах это схлопывает разные клубы в один канон, а `get_standings` ключует
аккумулятор этим каноном (`teams[canon]`) — клуб пропадает из таблицы вместе с тренером.

Файл характеризационный: часть тестов красная намеренно (описывает дефект), часть зелёная
и фиксирует поведение, которое чинить нельзя. Красное чинится в T3/T4, зелёное обязано
оставаться зелёным на каждом шаге.

Тесты чистые: обращений к БД нет, только резолвер и словарь алиасов.
"""
import unittest

import club_registry
import config
import database
from database import (
    TEAM_ALIASES,
    normalize_team_name,
    resolve_team_name,
    teams_match,
)


# Клубы вне 16 канонических имён КПЛ. Каждый обязан резолвиться в себя: чужого канона
# у него нет, и молча стать другим клубом он не должен.
# Справа — что функция возвращает сегодня.
COLLIDING_CLUBS = [
    ("Расинг Сантандер", "Расинг"),      # тир 4, подстрока алиаса 'расинг'
    ("Расинг Ланс", "Расинг"),           # тир 4
    ("Расинг Авельянеда", "Расинг"),     # тир 4
    ("Спортинг Хихон", "Порту"),         # тир 4, алиас 'порт' лежит внутри 'спортинг'
    ("ПСЖ", "ПСВ"),                      # тир 5, SequenceMatcher = 0.667 >= 0.65
    ("Спарта Прага", "Брага"),           # тир 5
    ("Спартак", "Спортинг"),             # тир 5
    ("Бешикташ", "Бенфика"),             # тир 5
]

# Многословные формы, которые схлопываться в канон ОБЯЗАНЫ: это тот же клуб, а не другой.
# Часть держится на алиасах, часть — на подстрочном тире, который удаляется в T3.
# Тем, кто помечен alias_backed=False, в T3 нужен явный алиас, иначе распознавание упадёт.
VERBOSE_FORMS = [
    ("Спортинг Лиссабон", "Спортинг", True),
    ("Бенфика Лиссабон", "Бенфика", True),
    ("Рейнджерс Глазго", "Рейнджерс", True),
    ("АЕК Афины", "АЕК", True),
    ("Аякс Амстердам", "Аякс", False),
    ("Селтик Глазго", "Селтик", False),
]


class TestCollidingClubsResolveToThemselves(unittest.TestCase):
    """КРАСНЫЕ до T3. Клуб вне 16 канонических имён не должен становиться другим клубом."""

    def test_each_colliding_club_resolves_to_itself(self):
        for name, current in COLLIDING_CLUBS:
            with self.subTest(club=name):
                self.assertEqual(
                    resolve_team_name(name),
                    name,
                    f"{name!r} резолвится в {current!r} — тренер выпадет из таблицы",
                )

    def test_two_racing_clubs_do_not_share_a_canonical_name(self):
        self.assertNotEqual(
            resolve_team_name("Расинг Сантандер"),
            resolve_team_name("Расинг Ланс"),
            "Два разных клуба дают один канон: get_standings отдаст одну строку вместо двух",
        )

    def test_short_name_is_never_fuzzy_matched(self):
        # 'псж' против 'псв' — 0.667 при пороге 0.65. На трёх буквах похожесть
        # неотличима от другого клуба, фаззи здесь применяться не должен вообще.
        self.assertEqual(resolve_team_name("ПСЖ"), "ПСЖ")

    def test_unknown_club_is_returned_unchanged(self):
        # Контракт вызывающих мест: resolve_team_name(x) or x. На непустом входе
        # функция обязана вернуть непустую строку и никогда не угадывать.
        for name in ("Црвена Звезда", "Лудогорец", "Шериф"):
            with self.subTest(club=name):
                self.assertEqual(resolve_team_name(name), name)


class TestTeamsMatchDoesNotConflateClubs(unittest.TestCase):
    """КРАСНЫЕ до T4. teams_match страдает тем же дефектом: подстрока плюс словесный матч."""

    def test_racing_variants_are_not_the_same_club(self):
        self.assertFalse(teams_match("Расинг", "Расинг Ланс"))
        self.assertFalse(teams_match("Расинг Сантандер", "Расинг Ланс"))

    def test_sporting_variants_are_not_the_same_club(self):
        self.assertFalse(teams_match("Спортинг", "Спортинг Хихон"))


class TestAliasContractPreserved(unittest.TestCase):
    """ЗЕЛЁНЫЕ. Словарь OCR-опечаток и транслита — ронять его нельзя ни на одном шаге."""

    def test_every_alias_key_resolves_to_its_canonical_value(self):
        self.assertGreater(len(TEAM_ALIASES), 100, "словарь алиасов подозрительно похудел")
        for alias, canonical in TEAM_ALIASES.items():
            with self.subTest(alias=alias):
                self.assertEqual(resolve_team_name(alias), canonical)

    def test_every_kpl_team_resolves_to_itself(self):
        for team in config.KPL_TEAMS:
            with self.subTest(team=team):
                self.assertEqual(resolve_team_name(team), team)

    def test_verbose_forms_still_collapse_to_their_club(self):
        # Ловушка для T3: 'Аякс Амстердам' и 'Селтик Глазго' сегодня держатся на
        # подстрочном тире. Удалите тир без добавления алиасов — этот тест покраснеет,
        # и это будет правильным сигналом, а не поводом вернуть тир.
        for name, canonical, _alias_backed in VERBOSE_FORMS:
            with self.subTest(form=name):
                self.assertEqual(resolve_team_name(name), canonical)


class TestNormalizationPreserved(unittest.TestCase):
    """ЗЕЛЁНЫЕ. Нормализация переехала в club_registry.py в T2 без изменений."""

    def test_yo_and_latin_variants_collapse(self):
        self.assertEqual(normalize_team_name("Будё Глимт"), "буде глимт")
        self.assertEqual(normalize_team_name("Будë-Глимт"), "буде глимт")
        self.assertEqual(normalize_team_name("Bodø/Glimt"), "bodo glimt")

    def test_separators_and_spacing_collapse(self):
        self.assertEqual(normalize_team_name("  Ривер   Плейт  "), "ривер плейт")
        self.assertEqual(normalize_team_name("Бока-Хуниорс"), "бока хуниорс")

    def test_empty_input(self):
        self.assertEqual(normalize_team_name(None), "")
        self.assertEqual(normalize_team_name(""), "")
        self.assertEqual(resolve_team_name(None), "")
        self.assertEqual(resolve_team_name(""), "")


class TestTotalityInvariant(unittest.TestCase):
    """ЗЕЛЁНЫЙ. Главный страж: два разных клуба реестра не имеют общего канона.

    Сейчас идёт по 16 именам КПЛ. Когда в T8 приедут реальные ~80, инвариант
    расширится автоматически — именно он не даст дефекту вернуться с новым клубом.
    """

    def _registry(self):
        registry = getattr(config, "CLUB_REGISTRY", None)
        if registry:
            return sorted(set(registry))
        return sorted(set(config.KPL_TEAMS) | set(config.CLUBS))

    def test_distinct_clubs_never_share_a_canonical_name(self):
        collisions = {}
        for club in self._registry():
            collisions.setdefault(resolve_team_name(club), []).append(club)
        conflicts = {canon: names for canon, names in collisions.items() if len(names) > 1}
        self.assertEqual(conflicts, {}, f"клубы схлопнулись в один канон: {conflicts}")

    def test_registry_clubs_resolve_to_themselves(self):
        for club in self._registry():
            with self.subTest(club=club):
                self.assertEqual(resolve_team_name(club), club)


class TestResolverIsPureCpu(unittest.TestCase):
    """ЗЕЛЁНЫЙ. Резолвер зовут из async-кода без to_thread — он обязан остаться без I/O."""

    def test_resolve_does_not_open_a_database_connection(self):
        calls = []
        original = database.get_connection

        def tracking_get_connection(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        database.get_connection = tracking_get_connection
        try:
            for name, _ in COLLIDING_CLUBS:
                resolve_team_name(name)
            teams_match("Расинг", "Брага")
        finally:
            database.get_connection = original

        self.assertEqual(calls, [], "резолв полез в БД — он вызывается из event loop")


class TestClubRegistry(unittest.TestCase):
    """ЗЕЛЁНЫЕ. Реестр: индекс, перезагрузка, отбраковка алиасов-теней."""

    def tearDown(self):
        club_registry.reload_registry()

    def test_registry_falls_back_to_legacy_seed_while_empty(self):
        # Пока config.CLUB_REGISTRY не заполнен (T8), реестр = KPL_TEAMS ∪ CLUBS,
        # то есть поведение не хуже прежнего.
        registry = club_registry.get_registry()
        self.assertTrue(registry)
        for team in config.KPL_TEAMS:
            with self.subTest(team=team):
                self.assertIn(team, registry)

    def test_index_maps_normalized_name_to_canonical(self):
        index = club_registry.get_registry_index()
        self.assertEqual(index.get("буде глимт"), "Будё Глимт")
        self.assertEqual(index.get("ривер плейт"), "Ривер Плейт")

    def test_is_registered_ignores_case_and_separators(self):
        self.assertTrue(club_registry.is_registered("будё-глимт"))
        self.assertTrue(club_registry.is_registered("  РИВЕР ПЛЕЙТ  "))
        self.assertFalse(club_registry.is_registered("Расинг Сантандер"))

    def test_no_alias_currently_shadows_a_registered_club(self):
        self.assertEqual(club_registry.get_dropped_aliases(), ())

    def test_reload_picks_up_a_changed_club_list(self):
        original = getattr(config, "CLUB_REGISTRY", [])
        try:
            config.CLUB_REGISTRY = ["Расинг Сантандер", "Расинг Ланс", "Шериф"]
            count = club_registry.reload_registry()
            self.assertEqual(count, 3)
            self.assertTrue(club_registry.is_registered("Расинг Ланс"))
            self.assertFalse(club_registry.is_registered("Брага"))
        finally:
            config.CLUB_REGISTRY = original

    def test_duplicate_and_blank_entries_are_ignored(self):
        original = getattr(config, "CLUB_REGISTRY", [])
        try:
            config.CLUB_REGISTRY = ["Брага", "брага", "  ", "", "Порту"]
            self.assertEqual(club_registry.reload_registry(), 2)
        finally:
            config.CLUB_REGISTRY = original

    def test_registry_module_does_not_import_database(self):
        # Слоение: club_registry лежит ниже database.py. Импорт наверх вернул бы
        # цикл, ради ухода от которого модуль и вынесен.
        import inspect

        source = inspect.getsource(club_registry)
        self.assertNotIn("import database", source)


if __name__ == "__main__":
    unittest.main()
