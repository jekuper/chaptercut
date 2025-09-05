"""The allowlist. It is the entire auth model, so it runs as an outer
middleware: an unauthorized update never reaches a filter or a handler.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update, User

from chaptercut.bot import texts
from chaptercut.logging import get_logger
from chaptercut.settings import Settings

log = get_logger(__name__)


def _user_of(event: TelegramObject) -> User | None:
    inner: TelegramObject = event.event if isinstance(event, Update) else event
    user = getattr(inner, "from_user", None)
    return user if isinstance(user, User) else None


class AuthMiddleware(BaseMiddleware):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = _user_of(event)
        if user is None:
            return None
        if not self.settings.is_allowed(user.id):
            log.info("auth.rejected", user_id=user.id)
            await self._reject(event)
            return None
        data["is_admin"] = self.settings.is_admin(user.id)
        return await handler(event, data)

    async def _reject(self, event: TelegramObject) -> None:
        inner = event.event if isinstance(event, Update) else event
        if isinstance(inner, Message):
            await inner.answer(texts.PRIVATE)
        elif isinstance(inner, CallbackQuery):
            # Silent: an unauthorized user gets no signal that anything exists.
            await inner.answer()
