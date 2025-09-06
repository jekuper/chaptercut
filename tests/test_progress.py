"""Rate limiting of the status message. No Telegram involved: the bot is a stub."""

from __future__ import annotations

from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from chaptercut.bot.progress import StatusMessage
from chaptercut.queue.models import Phase


class FakeBot:
    def __init__(self, raises: Exception | None = None) -> None:
        self.edits: list[str] = []
        self.deleted = 0
        self.raises = raises

    async def edit_message_text(self, text: str, **_: Any) -> None:
        if self.raises is not None:
            raise self.raises
        self.edits.append(text)

    async def delete_message(self, **_: Any) -> None:
        self.deleted += 1


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def status_and_clock() -> tuple[StatusMessage, FakeBot, Clock]:
    bot = FakeBot()
    clock = Clock()
    status = StatusMessage(bot, chat_id=1, message_id=2, title="Album", min_interval=4.0)  # pyright: ignore[reportArgumentType]
    status._now = clock  # pyright: ignore[reportAttributeAccessIssue]
    return status, bot, clock


async def test_repeat_updates_in_the_same_phase_are_throttled(
    status_and_clock: tuple[StatusMessage, FakeBot, Clock],
) -> None:
    status, bot, clock = status_and_clock
    await status.update(Phase.DOWNLOAD, pct=1)
    for pct in range(2, 20):
        clock.advance(0.1)
        await status.update(Phase.DOWNLOAD, pct=pct)
    assert len(bot.edits) == 1


async def test_an_edit_goes_through_once_the_interval_passes(
    status_and_clock: tuple[StatusMessage, FakeBot, Clock],
) -> None:
    status, bot, clock = status_and_clock
    await status.update(Phase.DOWNLOAD, pct=1)
    clock.advance(5.0)
    await status.update(Phase.DOWNLOAD, pct=50)
    assert len(bot.edits) == 2


async def test_a_phase_change_always_edits(
    status_and_clock: tuple[StatusMessage, FakeBot, Clock],
) -> None:
    status, bot, clock = status_and_clock
    await status.update(Phase.DOWNLOAD, pct=1)
    clock.advance(0.1)
    await status.update(Phase.SPLIT, pct=1)
    clock.advance(0.1)
    await status.update(Phase.TAG, pct=1)
    assert len(bot.edits) == 3


async def test_force_bypasses_the_throttle(
    status_and_clock: tuple[StatusMessage, FakeBot, Clock],
) -> None:
    status, bot, clock = status_and_clock
    await status.update(Phase.DOWNLOAD, pct=1)
    clock.advance(0.1)
    await status.update(Phase.DOWNLOAD, pct=2, force=True)
    assert len(bot.edits) == 2


async def test_identical_text_is_not_re_sent(
    status_and_clock: tuple[StatusMessage, FakeBot, Clock],
) -> None:
    status, bot, clock = status_and_clock
    await status.update(Phase.DOWNLOAD, pct=10)
    clock.advance(10.0)
    await status.update(Phase.DOWNLOAD, pct=10)
    assert len(bot.edits) == 1


async def test_text_contains_the_title_phase_and_percentage(
    status_and_clock: tuple[StatusMessage, FakeBot, Clock],
) -> None:
    status, bot, _clock = status_and_clock
    await status.update(Phase.SPLIT, pct=52, detail="4/12 Intro")
    text = bot.edits[0]
    assert "Album" in text
    assert "Splitting" in text
    assert "52%" in text
    assert "4/12 Intro" in text


async def test_title_is_html_escaped() -> None:
    bot = FakeBot()
    status = StatusMessage(bot, chat_id=1, message_id=2, title="<b>x</b>")  # pyright: ignore[reportArgumentType]
    await status.update(Phase.DOWNLOAD, pct=1)
    assert "&lt;b&gt;x&lt;/b&gt;" in bot.edits[0]


async def test_a_flood_wait_backs_off_instead_of_retrying() -> None:
    bot = FakeBot(raises=TelegramRetryAfter(method=None, message="flood", retry_after=30))  # pyright: ignore[reportArgumentType]
    clock = Clock()
    status = StatusMessage(bot, chat_id=1, message_id=2, min_interval=4.0)  # pyright: ignore[reportArgumentType]
    status._now = clock  # pyright: ignore[reportAttributeAccessIssue]

    await status.update(Phase.DOWNLOAD, pct=1)
    clock.advance(5.0)
    # Still inside the flood wait, so no second attempt is made.
    assert not status._should_edit(Phase.DOWNLOAD, force=False)


async def test_an_api_error_does_not_propagate() -> None:
    bot = FakeBot(raises=TelegramBadRequest(method=None, message="not modified"))  # pyright: ignore[reportArgumentType]
    status = StatusMessage(bot, chat_id=1, message_id=2)  # pyright: ignore[reportArgumentType]
    await status.update(Phase.DOWNLOAD, pct=1, force=True)


async def test_delete_swallows_api_errors() -> None:
    bot = FakeBot(raises=TelegramBadRequest(method=None, message="gone"))
    status = StatusMessage(bot, chat_id=1, message_id=2)  # pyright: ignore[reportArgumentType]
    await status.delete()


async def test_delete_calls_the_api() -> None:
    bot = FakeBot()
    status = StatusMessage(bot, chat_id=1, message_id=2)  # pyright: ignore[reportArgumentType]
    await status.delete()
    assert bot.deleted == 1


async def test_finish_always_edits(
    status_and_clock: tuple[StatusMessage, FakeBot, Clock],
) -> None:
    status, bot, _clock = status_and_clock
    await status.update(Phase.DOWNLOAD, pct=1)
    await status.finish("Failed: nope")
    assert bot.edits[-1] == "Failed: nope"
