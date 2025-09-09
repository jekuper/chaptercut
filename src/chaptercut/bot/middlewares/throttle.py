"""Per-user token bucket.

In-memory on purpose: the allowlist is a handful of people, so a restart
forgetting their recent rate is not worth a table.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from chaptercut.bot import texts
from chaptercut.bot.middlewares.common import inner_event, user_of
from chaptercut.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class Bucket:
    tokens: float
    updated: float


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, per_minute: int, burst: int | None = None) -> None:
        self.rate = per_minute / 60.0
        self.capacity = float(burst if burst is not None else max(3, per_minute // 2))
        self._buckets: dict[int, Bucket] = {}

    def _allow(self, user_id: int, now: float) -> bool:
        bucket = self._buckets.get(user_id)
        if bucket is None:
            self._buckets[user_id] = Bucket(tokens=self.capacity - 1, updated=now)
            return True
        bucket.tokens = min(self.capacity, bucket.tokens + (now - bucket.updated) * self.rate)
        bucket.updated = now
        if bucket.tokens < 1:
            return False
        bucket.tokens -= 1
        return True

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        inner = inner_event(event)
        user = user_of(event)
        if user is None:
            return await handler(event, data)

        if self._allow(user.id, time.monotonic()):
            return await handler(event, data)

        log.info("throttle.blocked", user_id=user.id)
        if isinstance(inner, Message):
            await inner.answer(texts.RATE_LIMITED)
        elif isinstance(inner, CallbackQuery):
            await inner.answer(texts.RATE_LIMITED, show_alert=False)
        return None
