"""
services/graphics/division_theme.py

Фирменный акцент дивизиона для всей графики бота.

Один дивизион = один акцентный цвет + плашка «ДИВИЗИОН N». Фон, карточки,
сетка и типографика у всех рендереров остаются общими: различать турниры
должен акцент, а не пять разных вёрсток.

Что темой НЕ управляется — семантические цвета. Зелёный «победа», красный
«поражение», золото «первое место», синий «пас» значат одно и то же во всех
дивизионах, и перекрашивать их под дивизион нельзя: цвет там несёт данные,
а не бренд.

Палитра живёт в коде и привязана к divisions.code, а не к id: id зависит от
порядка вставки, code — стабильный ключ («DIV_1»…«DIV_5»). Схему БД модуль
не трогает и SQL сам не пишет — только вызывает репозиторные функции
database.py.

Все резолверы fail-soft: если дивизион не найден, БД недоступна или код
незнакомый, возвращается DEFAULT_THEME. Рендерер не должен падать из-за темы.
"""

import logging
from dataclasses import dataclass

import database

logger = logging.getLogger(__name__)

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class DivisionTheme:
    """Акцент одного дивизиона.

    label пустой — плашка не рисуется (общая таблица без привязки к дивизиону).
    """
    code: str
    label: str
    accent: RGB
    on_accent: RGB


def _best_text_on(rgb: RGB) -> RGB:
    """Чёрный или белый поверх акцента — по относительной яркости (WCAG)."""
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2])
    contrast_with_black = (luminance + 0.05) / 0.05
    contrast_with_white = 1.05 / (luminance + 0.05)
    return (18, 18, 20) if contrast_with_black >= contrast_with_white else (255, 255, 255)


def _theme(code: str, label: str, accent: RGB) -> DivisionTheme:
    return DivisionTheme(code=code, label=label, accent=accent, on_accent=_best_text_on(accent))


# Оттенки разведены по кругу (0° / 38° / 174° / 258° / 330°), чтобы дивизионы
# не путались между собой на превью в ленте Telegram. DIV_1 сохраняет текущий
# красный: главный дивизион выглядит ровно так же, как до появления тем.
THEMES: dict[str, DivisionTheme] = {
    "DIV_1": _theme("DIV_1", "ДИВИЗИОН 1", (239, 68, 68)),    # #EF4444 красный
    "DIV_2": _theme("DIV_2", "ДИВИЗИОН 2", (245, 158, 11)),   # #F59E0B янтарь
    "DIV_3": _theme("DIV_3", "ДИВИЗИОН 3", (45, 212, 191)),   # #2DD4BF бирюза
    "DIV_4": _theme("DIV_4", "ДИВИЗИОН 4", (167, 139, 250)),  # #A78BFA лаванда
    "DIV_5": _theme("DIV_5", "ДИВИЗИОН 5", (244, 114, 182)),  # #F472B6 розовый
}

# Дивизион неизвестен: сегодняшний красный акцент и никакой плашки.
DEFAULT_THEME = _theme("", "", (239, 68, 68))


def get_theme_by_code(code: str | None) -> DivisionTheme:
    """Тема по divisions.code. Незнакомый код — DEFAULT_THEME."""
    if not code:
        return DEFAULT_THEME
    return THEMES.get(str(code).strip().upper(), DEFAULT_THEME)


def resolve_theme(
    division_id: int | None = None,
    division_code: str | None = None,
    division_name: str | None = None,
    team_name: str | None = None,
) -> DivisionTheme:
    """
    Определить тему по любому из доступных признаков.

    Порядок: явный code → division_id → название дивизиона → клуб.
    Ни одна ветка не выбрасывает исключение наружу: графика важнее темы.
    """
    if division_code:
        theme = get_theme_by_code(division_code)
        if theme is not DEFAULT_THEME:
            return theme

    if division_id is not None:
        try:
            division = database.get_division(int(division_id))
            if division and division.get("code"):
                theme = get_theme_by_code(division["code"])
                if theme is not DEFAULT_THEME:
                    return theme
        except Exception:
            logger.debug("Division theme: could not resolve division_id=%r", division_id, exc_info=True)

    if division_name:
        theme = _theme_from_name(division_name)
        if theme is not DEFAULT_THEME:
            return theme

    if team_name:
        try:
            resolved_id = database.get_team_division_id(team_name)
            if resolved_id is not None:
                return resolve_theme(division_id=resolved_id)
        except Exception:
            logger.debug("Division theme: could not resolve team_name=%r", team_name, exc_info=True)

    return DEFAULT_THEME


def _theme_from_name(division_name: str) -> DivisionTheme:
    """«Дивизион 3», «ДИВИЗИОН 3 · осень» → DIV_3. Только как запасной путь."""
    digits = ""
    for char in str(division_name):
        if char.isdigit():
            digits += char
        elif digits:
            break
    if not digits:
        return DEFAULT_THEME
    return get_theme_by_code(f"DIV_{digits}")


def draw_division_badge(draw, theme: DivisionTheme, right_x: int, top_y: int, font, scale: int = 1) -> int:
    """
    Нарисовать плашку «ДИВИЗИОН N», прижатую правым краем к right_x.

    Возвращает ширину плашки (0, если тема без метки — тогда ничего не рисуется
    и вызывающий код может не проверять условие заранее).
    """
    if not theme.label:
        return 0

    pad_x = 12 * scale
    height = 24 * scale
    try:
        text_w = int(draw.textlength(theme.label, font=font))
    except Exception:
        text_w = 9 * scale * len(theme.label)

    width = text_w + pad_x * 2
    x0 = right_x - width
    draw.rounded_rectangle(
        [x0, top_y, right_x, top_y + height],
        radius=height // 2,
        fill=theme.accent,
    )
    draw.text(
        (x0 + pad_x, top_y + height // 2),
        theme.label,
        fill=theme.on_accent,
        font=font,
        anchor="lm",
    )
    return width
