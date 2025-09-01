"""Narrowing helpers for the loosely typed JSON that yt-dlp hands back."""

from __future__ import annotations

from typing import Any, cast


def dict_list(value: object) -> list[dict[str, Any]]:
    """Every dict in `value`, or an empty list if it is not a list at all."""
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for item in cast(list[Any], value):
        if isinstance(item, dict):
            items.append(cast(dict[str, Any], item))
    return items


def as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def as_int(value: object) -> int | None:
    number = as_float(value)
    return int(number) if number is not None else None


def as_str(value: object, default: str = "") -> str:
    if value is None:
        return default
    return str(value)
