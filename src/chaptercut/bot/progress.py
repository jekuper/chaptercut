"""The one status message per job, edited in place.

Telegram rate-limits edits, and a download emits progress several times a
second, so edits are throttled. A phase change forces an edit through, because
that is the update the user is actually waiting to see.
"""

from __future__ import annotations

import asyncio
import time

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

from chaptercut.bot import texts
from chaptercut.logging import get_logger
from chaptercut.queue.models import Phase

log = get_logger(__name__)

MIN_EDIT_INTERVAL = 4.0


class StatusMessage:
    """A `ProgressSink` backed by one editable Telegram message."""

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        message_id: int,
        title: str = "",
        min_interval: float = MIN_EDIT_INTERVAL,
    ) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.title = title
        self.min_interval = min_interval
        self._last_edit = 0.0
        self._last_phase: Phase | None = None
        self._last_text = ""
        self._lock = asyncio.Lock()

    def _now(self) -> float:
        return time.monotonic()

    def _should_edit(self, phase: Phase, force: bool) -> bool:
        if force or phase is not self._last_phase:
            return True
        return self._now() - self._last_edit >= self.min_interval

    async def update(
        self,
        phase: Phase,
        pct: float | None = None,
        detail: str | None = None,
        force: bool = False,
    ) -> None:
        async with self._lock:
            if not self._should_edit(phase, force):
                return
            label = texts.PHASE_LABELS.get(phase.value, phase.value)
            text = texts.progress_text(self.title, label, pct, detail)
            self._last_phase = phase
            await self._edit(text)

    async def finish(self, text: str) -> None:
        """Replace the status message with a final one, e.g. a failure notice."""
        async with self._lock:
            await self._edit(text, force=True)

    async def delete(self) -> None:
        try:
            await self.bot.delete_message(chat_id=self.chat_id, message_id=self.message_id)
        except TelegramAPIError as exc:
            log.debug("progress.delete_failed", error=type(exc).__name__)

    async def _edit(self, text: str, force: bool = False) -> None:
        if text == self._last_text and not force:
            return
        try:
            await self.bot.edit_message_text(
                text=text,
                chat_id=self.chat_id,
                message_id=self.message_id,
                parse_mode="HTML",
            )
        except TelegramRetryAfter as exc:
            # Back off past the flood wait rather than hammering the same edit.
            self._last_edit = self._now() + exc.retry_after
            return
        except TelegramAPIError as exc:
            # "message is not modified" and friends are not worth failing a job over.
            log.debug("progress.edit_failed", error=type(exc).__name__)
            self._last_edit = self._now()
            return
        self._last_text = text
        self._last_edit = self._now()
