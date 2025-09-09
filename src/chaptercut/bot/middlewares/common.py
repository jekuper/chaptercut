"""Shared helpers for the outer middlewares.

Outer middlewares run before aiogram resolves the update type, and Telegram
adds new update types over time. `Update.event` raises on anything it does not
recognise, so it is never called unguarded.
"""

from __future__ import annotations

from aiogram.types import TelegramObject, Update, User
from aiogram.types.update import UpdateTypeLookupError


def inner_event(event: TelegramObject) -> TelegramObject | None:
    """The message or callback inside an Update, or None if it is a type we
    do not handle."""
    if not isinstance(event, Update):
        return event
    try:
        return event.event
    except UpdateTypeLookupError:
        return None


def user_of(event: TelegramObject) -> User | None:
    inner = inner_event(event)
    if inner is None:
        return None
    user = getattr(inner, "from_user", None)
    return user if isinstance(user, User) else None
