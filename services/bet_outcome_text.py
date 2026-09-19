"""
services/bet_outcome_text.py

Человекочитаемое описание исхода ставки и разбор результата для админ-мониторинга
(/admin_bets). Чистые функции без обращения к БД.

Ключи исходов — те же, что создаёт services/odds_engine.py и понимает
services/market_settler.py, плюс устаревшие формы старых купонов (tb25, tb_2_5,
both_yes, draw…).
"""

import re

FINISHED_MATCH_STATUSES = ("confirmed", "completed", "finished")

_TOTAL_RE = re.compile(r"^(over|under|tb|tm)_?(\d+)[._]?(\d)?$")
_IND_TOTAL_RE = re.compile(r"^it([12])_(over|under)_(\d+(?:\.\d+)?)$")
_HANDICAP_RE = re.compile(r"^h([12])_(minus|plus)_(\d+(?:\.\d+)?)$")
_CORRECT_SCORE_RE = re.compile(r"^cs_(\d+)_(\d+)$")


def goals_word(n: int) -> str:
    """1 гол, 2 гола, 5 голов."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "гол"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "гола"
    return "голов"


def _fmt_line(value: float) -> str:
    return f"{value:g}"


def _parse_total(key: str) -> tuple[str, float] | None:
    """over_2.5 / under_1.5 / tb25 / tm_2_5 → ('over'|'under', 2.5)."""
    m = _TOTAL_RE.match(key)
    if not m:
        return None
    side = "over" if m.group(1) in ("over", "tb") else "under"
    whole, frac = m.group(2), m.group(3)
    if frac is None:
        # tb25 → 2.5; over_3 → 3
        line = float(f"{whole[:-1]}.{whole[-1]}") if len(whole) > 1 else float(whole)
    else:
        line = float(f"{whole}.{frac}")
    return side, line


def describe_selection(
    outcome_type: str | None,
    team1: str,
    team2: str,
    market_key: str | None = None,
    selection_name: str | None = None,
) -> str:
    """Понятное описание выбора игрока: «Победит Кельн (П1)», «Тотал меньше 2.5 гола (ТМ 2.5)»."""
    key = (outcome_type or "").strip().lower()
    mk = (market_key or "").strip().lower()

    if mk in ("ht_result", "first_half", "1st_half") or key.startswith("ht_"):
        base = key.removeprefix("ht_")
        if base in ("p1", "1"):
            return f"1-й тайм: победит {team1} (П1)"
        if base in ("x", "draw"):
            return "1-й тайм: ничья (X)"
        if base in ("p2", "2"):
            return f"1-й тайм: победит {team2} (П2)"

    if key in ("p1", "1", "home"):
        return f"Победит {team1} (П1)"
    if key in ("x", "draw"):
        return "Ничья (X)"
    if key in ("p2", "2", "away"):
        return f"Победит {team2} (П2)"
    if key in ("1x", "dc_1x"):
        return f"{team1} не проиграет (1X)"
    if key in ("x2", "dc_x2"):
        return f"{team2} не проиграет (X2)"
    if key in ("12", "dc_12"):
        return "Кто-то победит, без ничьей (12)"
    if key == "dnb_1":
        return f"Победит {team1}, ничья — возврат"
    if key == "dnb_2":
        return f"Победит {team2}, ничья — возврат"

    if key in ("btts_yes", "both_yes") or (key == "yes" and mk in ("btts", "both_teams_to_score")):
        return "Обе забьют — Да"
    if key in ("btts_no", "both_no") or (key == "no" and mk in ("btts", "both_teams_to_score")):
        return "Обе забьют — Нет"

    m = _IND_TOTAL_RE.match(key)
    if m:
        side, direction, line = m.groups()
        team = team1 if side == "1" else team2
        word = "больше" if direction == "over" else "меньше"
        short = "ИТБ" if direction == "over" else "ИТМ"
        return f"{team} забьёт {word} {line} (Инд. тотал {short}{side} {line})"

    m = _HANDICAP_RE.match(key)
    if m:
        side, sign, line = m.groups()
        team = team1 if side == "1" else team2
        signed = f"−{line}" if sign == "minus" else f"+{line}"
        if sign == "minus":
            hint = f"{team} победит с разницей 2+ мяча" if line == "1.5" else f"{team} с форой {signed}"
        else:
            hint = f"{team} не проиграет с разницей 2+ мяча" if line == "1.5" else f"{team} с форой {signed}"
        return f"{hint} (Ф{side} {signed})"

    m = _CORRECT_SCORE_RE.match(key)
    if m:
        return f"Точный счёт {m.group(1)}:{m.group(2)}"

    total = _parse_total(key)
    if total:
        side, line = total
        word, short = ("больше", "ТБ") if side == "over" else ("меньше", "ТМ")
        return f"Тотал {word} {_fmt_line(line)} гола ({short} {_fmt_line(line)})"

    if selection_name:
        return selection_name
    return outcome_type or "—"


def _winner_phrase(s1: int, s2: int, team1: str, team2: str) -> str:
    if s1 > s2:
        return f"победа {team1}"
    if s2 > s1:
        return f"победа {team2}"
    return "ничья"


def explain_result(
    outcome_type: str | None,
    team1: str,
    team2: str,
    score1: int | None,
    score2: int | None,
    market_key: str | None = None,
    ht_score1: int | None = None,
    ht_score2: int | None = None,
) -> str | None:
    """Факт, по которому рассчитан исход: «Счёт 1:3 — всего 4 гола, больше 2.5».

    None, если счёта нет — объяснять нечего.
    """
    if score1 is None or score2 is None:
        return None
    s1, s2 = int(score1), int(score2)
    key = (outcome_type or "").strip().lower()
    mk = (market_key or "").strip().lower()
    head = f"Счёт {s1}:{s2}"

    if mk in ("ht_result", "first_half", "1st_half") or key.startswith("ht_"):
        if ht_score1 is None or ht_score2 is None:
            return f"{head} — счёт 1-го тайма не записан"
        h1, h2 = int(ht_score1), int(ht_score2)
        return f"1-й тайм {h1}:{h2} — {_winner_phrase(h1, h2, team1, team2)}"

    total = None if _IND_TOTAL_RE.match(key) or _HANDICAP_RE.match(key) else _parse_total(key)
    if total:
        side, line = total
        tot = s1 + s2
        cmp_word = "больше" if tot > line else "меньше"
        return f"{head} — всего {tot} {goals_word(tot)}, это {cmp_word} {_fmt_line(line)}"

    m = _IND_TOTAL_RE.match(key)
    if m:
        side, _direction, line = m.groups()
        team, goals = (team1, s1) if side == "1" else (team2, s2)
        cmp_word = "больше" if goals > float(line) else "меньше"
        return f"{head} — у {team} {goals} {goals_word(goals)}, это {cmp_word} {line}"

    m = _HANDICAP_RE.match(key)
    if m:
        side, sign, line = m.groups()
        delta = float(line) if sign == "plus" else -float(line)
        if side == "1":
            adj1, adj2 = s1 + delta, float(s2)
        else:
            adj1, adj2 = float(s1), s2 + delta
        return f"{head} — с учётом форы {_fmt_line(adj1)}:{_fmt_line(adj2)}"

    if key in ("btts_yes", "btts_no", "both_yes", "both_no") or mk in ("btts", "both_teams_to_score"):
        if s1 > 0 and s2 > 0:
            return f"{head} — забили обе команды"
        if s1 == 0 and s2 == 0:
            return f"{head} — никто не забил"
        blank = team1 if s1 == 0 else team2
        return f"{head} — {blank} не забил"

    return f"{head} — {_winner_phrase(s1, s2, team1, team2)}"
