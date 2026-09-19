"""Integer parsing for request input that answers 400 instead of crashing into 500.

A bare ``int(request.query["limit"])`` raises ``ValueError`` on ``?limit=abc``,
which aiohttp turns into a 500 with a traceback in the log. These helpers raise
``web.HTTPBadRequest`` with the API's usual JSON error body instead;
``cors_middleware`` returns HTTP exceptions as regular responses.

Call them outside any broad ``try/except Exception`` block, or the 400 is
swallowed and reported as a 500.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from aiohttp import web

_MISSING = object()


def parse_int(raw: Any, name: str, default: Any = _MISSING,
              minimum: int | None = None, maximum: int | None = None) -> int | None:
    """``raw`` as an int; ``default`` when it is absent or empty, otherwise 400."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if default is _MISSING:
            raise _bad_request(name, f"Параметр «{name}» обязателен.")
        return default
    if isinstance(raw, bool):
        raise _bad_request(name, f"Параметр «{name}» должен быть целым числом.")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise _bad_request(name, f"Параметр «{name}» должен быть целым числом.") from None
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise _bad_request(name, f"Параметр «{name}» вне допустимого диапазона.")
    return value


def query_int(request: web.Request, name: str, default: Any = _MISSING, **bounds) -> int | None:
    return parse_int(request.query.get(name), name, default, **bounds)


def body_int(data: Mapping[str, Any], name: str, default: Any = _MISSING, **bounds) -> int | None:
    return parse_int(data.get(name), name, default, **bounds)


def path_int(request: web.Request, name: str = "id") -> int:
    return parse_int(request.match_info.get(name), name)


def _bad_request(name: str, message: str) -> web.HTTPBadRequest:
    return web.HTTPBadRequest(
        text=json.dumps({"status": "error", "error": f"invalid_{name}", "message": message}, ensure_ascii=False),
        content_type="application/json",
    )
