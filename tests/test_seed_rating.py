"""
tests/test_seed_rating.py

Предсезонный рейтинг участников и отбор четвёрки центральных матчей по нему.

Рейтинг живёт в `config.DIVISION_PLAYER_SEEDS` (сид сезона, как `DIVISION_CLUBS`),
читается через `services/preseason_seeds.py` и определяет статусность пары в
`services.betting_engine.select_top_round_matches`.

1. TestSeedRatingData — сам рейтинг: полнота, уникальность ключей, шкала силы,
   поиск по логину и по клубу (участники без телеграм-тега).
2. TestSeedDrivenSelection — отбор на туре, где таблицы ещё нет: раньше все пары
   получали одинаковый Score и в линию уходили первые четыре матча по id.
"""

import unittest

import config
import database
from services import preseason_seeds
from services.betting_engine import (
    CENTRAL_MATCHES_PER_ROUND,
    select_top_round_matches,
)
from services.preseason_seeds import (
    NEUTRAL_STRENGTH,
    get_seed_rank,
    get_seed_strength,
    get_seed_division_index,
    get_duplicate_seed_keys,
    get_unknown_seed_clubs,
)


SEEDS_PER_DIVISION = 16

# Дивизион 5 — на нём собран тур: его рейтинг целиком из логинов, без клубных записей.
DIV_CODE = "DIV_5"
DIV_ID = 5
SEASON_ID = 1

ROUND_LINE = 7
ROUND_NEUTRAL = 8          # отдельный тур на пару вне рейтинга
MATCH_ID_BASE = 99700
USER_ID_BASE = 997100

# Клубы, которых нет ни в DIVISION_CLUBS, ни в рейтинге.
NEUTRAL_CLUBS = ("Логово Тест Нейтрал А", "Логово Тест Нейтрал Б")

# Пары тура индексами в рейтинге DIV_5 (0 — слабейший, 15 — сильнейший), в том
# порядке, в каком они лягут в таблицу matches: id растёт сверху вниз.
#
# Порядок намеренно обратный статусности. Первые четыре по id — самая слабая
# четвёрка тура плюс «сильнейший против слабейшего»: если отбор перестанет
# считать рейтинг, он возьмёт именно их, и тест это поймает.
ROUND_PAIRS = [
    (15, 0),    # сумма 1.000, но разрыв максимальный — в линию не идёт
    (2, 1),
    (4, 3),
    (6, 5),
    (8, 7),     # сумма та же 1.000, соперники ровные — идёт
    (10, 9),
    (12, 11),
    (14, 13),
]
# Индексы ROUND_PAIRS, которые обязаны попасть в линию.
EXPECTED_LINE = (7, 6, 5, 4)


class TestSeedRatingData(unittest.TestCase):
    """Чистые проверки рейтинга: БД не нужна."""

    def test_every_division_has_a_full_rating(self):
        self.assertEqual(
            sorted(config.DIVISION_PLAYER_SEEDS), sorted(config.DIVISION_CLUBS),
            "Рейтинг должен покрывать те же дивизионы, что и роспись клубов"
        )
        for code, entries in config.DIVISION_PLAYER_SEEDS.items():
            with self.subTest(division=code):
                self.assertEqual(len(entries), SEEDS_PER_DIVISION)
        total = sum(len(e) for e in config.DIVISION_PLAYER_SEEDS.values())
        self.assertEqual(total, SEEDS_PER_DIVISION * len(config.DIVISION_CLUBS))

    def test_keys_are_unique_across_the_league(self):
        self.assertEqual(get_duplicate_seed_keys(), (), "Участник не может стоять в рейтинге дважды")

    def test_club_entries_belong_to_their_own_division(self):
        """Зеркало инварианта TestLogoMapCoversTheRoster: запись без тега — реальный клуб."""
        self.assertEqual(get_unknown_seed_clubs(), ())

    def test_division_index_keeps_the_source_order(self):
        index = get_seed_division_index()
        self.assertEqual(index[DIV_CODE], list(config.DIVISION_PLAYER_SEEDS[DIV_CODE]))

    def test_strength_spans_the_whole_range(self):
        weakest, strongest = config.DIVISION_PLAYER_SEEDS[DIV_CODE][0], config.DIVISION_PLAYER_SEEDS[DIV_CODE][-1]
        self.assertEqual(get_seed_strength(weakest), 0.0)
        self.assertEqual(get_seed_strength(strongest), 1.0)
        self.assertEqual(get_seed_rank(weakest), 1)
        self.assertEqual(get_seed_rank(strongest), SEEDS_PER_DIVISION)

    def test_login_lookup_ignores_case_and_the_at_sign(self):
        for form in ("Fede_15r", "@Fede_15r", "fede_15r", "  @FEDE_15R  "):
            with self.subTest(form=form):
                self.assertEqual(get_seed_strength(form), 0.0)

    def test_rating_rises_monotonically_with_position(self):
        strengths = [get_seed_strength(e) for e in config.DIVISION_PLAYER_SEEDS[DIV_CODE]]
        self.assertEqual(strengths, sorted(strengths))
        self.assertEqual(len(set(strengths)), SEEDS_PER_DIVISION)

    def test_untagged_participants_are_found_by_their_club(self):
        """убиватор и Мандарин заведены клубом — тега у них нет."""
        marseille = get_seed_strength(None, "Марсель")
        koln = get_seed_strength(None, "Кельн")
        self.assertIsNotNone(marseille)
        self.assertIsNotNone(koln)
        self.assertEqual(get_seed_rank(None, "Марсель"), 14)
        self.assertEqual(get_seed_rank(None, "Кельн"), 15)
        self.assertLess(marseille, koln)

    def test_club_lookup_goes_through_the_registry(self):
        """«Кёльн» с ё и алиасы приходят к тому же ключу, что каноническое имя."""
        self.assertEqual(get_seed_rank(None, "Кёльн"), 15)
        self.assertEqual(get_seed_rank(None, "  кельн "), 15)

    def test_login_wins_over_club(self):
        """Логин переживает смену клуба: он проверяется первым."""
        self.assertEqual(get_seed_strength("Fede_15r", "Марсель"), 0.0)

    def test_unknown_participant_has_no_rating(self):
        self.assertIsNone(get_seed_strength("no_such_login_at_all"))
        self.assertIsNone(get_seed_strength(None, "Клуб Которого Нет"))
        self.assertIsNone(get_seed_strength(None))
        self.assertIsNone(get_seed_rank("no_such_login_at_all"))

    def test_reload_counts_every_entry(self):
        loaded = preseason_seeds.reload_seeds()
        self.assertEqual(loaded, SEEDS_PER_DIVISION * len(config.DIVISION_CLUBS))


class TestSeedDrivenSelection(unittest.TestCase):
    """Отбор четвёрки на туре без таблицы — тест на исходный баг."""

    def setUp(self):
        database.init_db()
        self._cleanup()
        self._add_coaches()
        self._add_round_matches()

    def tearDown(self):
        self._cleanup()

    # ─── фикстуры ────────────────────────────────────────────────────────

    def _cleanup(self):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute(
                "DELETE FROM market_selections WHERE market_id IN "
                "(SELECT id FROM markets WHERE match_id >= ?)", (MATCH_ID_BASE,)
            )
            c.execute("DELETE FROM markets WHERE match_id >= ?", (MATCH_ID_BASE,))
            c.execute("DELETE FROM bet_markets WHERE match_id >= ?", (MATCH_ID_BASE,))
            c.execute("DELETE FROM matches WHERE id >= ?", (MATCH_ID_BASE,))
            c.execute(
                "DELETE FROM rounds WHERE round_number IN (?, ?) AND division_id = ?",
                (ROUND_LINE, ROUND_NEUTRAL, DIV_ID)
            )
            c.execute("DELETE FROM users WHERE telegram_id >= ?", (USER_ID_BASE,))

    def _add_coaches(self):
        """16 тренеров дивизиона: логин из рейтинга, клуб — из DIVISION_CLUBS.

        Привязка клуба к логину для отбора неважна — рейтинг ищется по логину, —
        но без клуба тренер не попадёт ни в таблицу, ни в матч.
        """
        logins = [e.lstrip("@") for e in config.DIVISION_PLAYER_SEEDS[DIV_CODE]]
        with database.transaction() as conn:
            c = conn.cursor()
            for idx, (login, club) in enumerate(zip(logins, self.clubs())):
                c.execute(
                    "INSERT OR REPLACE INTO users (telegram_id, username, team_name, division_id, role) "
                    "VALUES (?, ?, ?, ?, 'player')",
                    (USER_ID_BASE + idx, login, club, DIV_ID)
                )

    @staticmethod
    def clubs() -> list[str]:
        return list(config.DIVISION_CLUBS[DIV_CODE])

    def _add_round_matches(self):
        clubs = self.clubs()
        with database.transaction() as conn:
            c = conn.cursor()
            for n, (i1, i2) in enumerate(ROUND_PAIRS):
                c.execute(
                    "INSERT INTO matches (id, round_number, division_id, season_id, "
                    "player1_id, player2_id, player1_team, player2_team, status) "
                    "VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, 'pending')",
                    (MATCH_ID_BASE + n, ROUND_LINE, DIV_ID, SEASON_ID, clubs[i1], clubs[i2])
                )

    def _match_id(self, pair_index: int) -> int:
        return MATCH_ID_BASE + pair_index

    # ─── проверки ────────────────────────────────────────────────────────

    def test_no_table_yet(self):
        """Предпосылка теста: ни одного сыгранного тура, таблица пустая."""
        standings = database.get_standings(division_id=DIV_ID, season_id=SEASON_ID)
        self.assertEqual(len(standings), SEEDS_PER_DIVISION)
        self.assertEqual(max(r["played"] for r in standings), 0)

    def test_line_takes_the_top_rated_pairs_not_the_first_by_id(self):
        matches = database.get_matches_by_round(ROUND_LINE, division_id=DIV_ID, season_id=SEASON_ID)
        self.assertEqual(len(matches), len(ROUND_PAIRS))

        selected = select_top_round_matches(ROUND_LINE, division_id=DIV_ID, season_id=SEASON_ID)
        self.assertEqual(len(selected), CENTRAL_MATCHES_PER_ROUND)
        self.assertEqual(
            sorted(m["id"] for m in selected),
            sorted(self._match_id(i) for i in EXPECTED_LINE),
            "В линию должны уйти четыре самые статусные пары рейтинга"
        )

        first_four_by_id = [self._match_id(i) for i in range(CENTRAL_MATCHES_PER_ROUND)]
        self.assertFalse(
            set(first_four_by_id) & {m["id"] for m in selected},
            "Отбор не должен вырождаться в «первые четыре матча тура по id»"
        )

    def test_mismatch_loses_to_an_even_pair_of_the_same_sum(self):
        """«Сильнейший против слабейшего» — не топ-матч: сумма та же, разрыв больше."""
        scored = select_top_round_matches(
            ROUND_LINE, division_id=DIV_ID, season_id=SEASON_ID, limit=len(ROUND_PAIRS)
        )
        by_id = {m["id"]: m for m in scored}

        mismatch = by_id[self._match_id(0)]      # 16-й против 1-го
        even_pair = by_id[self._match_id(4)]     # 9-й против 8-го, сумма рейтингов та же

        self.assertLess(mismatch["line_score"], even_pair["line_score"])
        self.assertEqual(
            mismatch["line_score"], min(m["line_score"] for m in scored),
            "Самый неравный матч тура должен быть последним в отборе"
        )

    def test_selection_is_stable(self):
        first = [m["id"] for m in select_top_round_matches(ROUND_LINE, division_id=DIV_ID, season_id=SEASON_ID)]
        second = [m["id"] for m in select_top_round_matches(ROUND_LINE, division_id=DIV_ID, season_id=SEASON_ID)]
        self.assertEqual(first, second)

    def test_opening_the_line_creates_four_markets(self):
        """Сквозная проверка до bet_markets: линия тура — ровно эти четыре матча."""
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO rounds (round_number, is_open, bets_open, deadline, division_id, season_id) "
                "VALUES (?, 0, 0, NULL, ?, ?)",
                (ROUND_LINE, DIV_ID, SEASON_ID)
            )

        self.assertTrue(
            database.set_round_bets_open(ROUND_LINE, True, division_id=DIV_ID, season_id=SEASON_ID)
        )
        markets = database.get_active_bet_markets(ROUND_LINE, division_id=DIV_ID, season_id=SEASON_ID)
        self.assertEqual(len(markets), CENTRAL_MATCHES_PER_ROUND)
        self.assertEqual(
            sorted(m["match_id"] for m in markets),
            sorted(self._match_id(i) for i in EXPECTED_LINE)
        )

    def test_a_pair_outside_the_rating_stays_neutral(self):
        """Клубы без записи в рейтинге не поднимаются и не тонут: сила 0.5 каждому."""
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO matches (id, round_number, division_id, season_id, "
                "player1_id, player2_id, player1_team, player2_team, status) "
                "VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, 'pending')",
                (MATCH_ID_BASE + 90, ROUND_NEUTRAL, DIV_ID, SEASON_ID,
                 NEUTRAL_CLUBS[0], NEUTRAL_CLUBS[1])
            )

        selected = select_top_round_matches(ROUND_NEUTRAL, division_id=DIV_ID, season_id=SEASON_ID)
        self.assertEqual(len(selected), 1)
        self.assertEqual(
            selected[0]["line_score"], round(NEUTRAL_STRENGTH * 2, 3),
            "Пара вне рейтинга — две нейтральные силы без разрыва"
        )


if __name__ == "__main__":
    unittest.main()
