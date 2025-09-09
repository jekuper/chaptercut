"""The allowlist is the whole auth model, so it gets tested directly."""

from __future__ import annotations

from typing import Any

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from chaptercut.bot.middlewares.auth import AuthMiddleware
from chaptercut.bot.middlewares.throttle import ThrottleMiddleware
from chaptercut.settings import Settings

ALLOWED = 111
ADMIN = 111
STRANGER = 999


class Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, event: Any, data: dict[str, Any]) -> str:
        self.calls.append(dict(data))
        return "handled"


def a_user(user_id: int) -> User:
    return User(id=user_id, is_bot=False, first_name="Tester")


def a_message(user_id: int, text: str = "hello") -> Message:
    return Message(
        message_id=1,
        date=__import__("datetime").datetime(2026, 1, 1),
        chat=Chat(id=user_id, type="private"),
        from_user=a_user(user_id),
        text=text,
    )


def a_callback(user_id: int) -> CallbackQuery:
    return CallbackQuery(
        id="cb1",
        from_user=a_user(user_id),
        chat_instance="ci",
        data="t:req:audio",
    )


def an_update(inner: Message | CallbackQuery) -> Update:
    if isinstance(inner, Message):
        return Update(update_id=1, message=inner)
    return Update(update_id=1, callback_query=inner)


@pytest.fixture
def replies(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []

    async def fake_answer(self: Any, text: str = "", **_: Any) -> None:
        sent.append(text)

    monkeypatch.setattr(Message, "answer", fake_answer)
    monkeypatch.setattr(CallbackQuery, "answer", fake_answer)
    return sent


async def test_an_allowed_user_reaches_the_handler(settings: Settings, replies: list[str]) -> None:
    handler = Recorder()
    result = await AuthMiddleware(settings)(handler, an_update(a_message(ALLOWED)), {})
    assert result == "handled"
    assert len(handler.calls) == 1
    assert replies == []


async def test_a_stranger_is_stopped_with_one_terse_reply(
    settings: Settings, replies: list[str]
) -> None:
    handler = Recorder()
    result = await AuthMiddleware(settings)(handler, an_update(a_message(STRANGER)), {})
    assert result is None
    assert handler.calls == []
    assert replies == ["This bot is private."]


async def test_a_stranger_pressing_a_button_gets_nothing_back(
    settings: Settings, replies: list[str]
) -> None:
    handler = Recorder()
    result = await AuthMiddleware(settings)(handler, an_update(a_callback(STRANGER)), {})
    assert result is None
    assert handler.calls == []
    assert replies == [""]


async def test_admin_status_is_injected(settings: Settings, replies: list[str]) -> None:
    handler = Recorder()
    await AuthMiddleware(settings)(handler, an_update(a_message(ADMIN)), {})
    assert handler.calls[0]["is_admin"] is True


async def test_a_non_admin_allowed_user_is_not_admin(
    settings: Settings, replies: list[str]
) -> None:
    handler = Recorder()
    await AuthMiddleware(settings)(handler, an_update(a_message(222)), {})
    assert handler.calls[0]["is_admin"] is False


async def test_an_update_type_we_do_not_handle_is_dropped_not_crashed(
    settings: Settings,
) -> None:
    # Telegram keeps adding update types; Update.event raises on unknown ones.
    handler = Recorder()
    result = await AuthMiddleware(settings)(handler, Update(update_id=1), {})
    assert result is None
    assert handler.calls == []


async def test_the_middleware_also_accepts_a_bare_event(
    settings: Settings, replies: list[str]
) -> None:
    handler = Recorder()
    assert await AuthMiddleware(settings)(handler, a_message(ALLOWED), {}) == "handled"


async def test_requests_under_the_limit_all_pass(replies: list[str]) -> None:
    middleware = ThrottleMiddleware(per_minute=60)
    handler = Recorder()
    for _ in range(3):
        assert await middleware(handler, an_update(a_message(ALLOWED)), {}) == "handled"
    assert replies == []


async def test_a_burst_past_the_capacity_is_blocked(replies: list[str]) -> None:
    middleware = ThrottleMiddleware(per_minute=60, burst=3)
    handler = Recorder()
    results = [await middleware(handler, an_update(a_message(ALLOWED)), {}) for _ in range(6)]
    assert results.count("handled") == 3
    assert results.count(None) == 3
    assert replies == ["Slow down a moment."] * 3


async def test_users_have_separate_buckets(replies: list[str]) -> None:
    middleware = ThrottleMiddleware(per_minute=60, burst=2)
    handler = Recorder()
    for _ in range(3):
        await middleware(handler, an_update(a_message(ALLOWED)), {})
    assert await middleware(handler, an_update(a_message(222)), {}) == "handled"


async def test_tokens_refill_over_time() -> None:
    middleware = ThrottleMiddleware(per_minute=60, burst=1)
    now = 1000.0
    assert middleware._allow(ALLOWED, now) is True
    assert middleware._allow(ALLOWED, now) is False
    # One token per second at 60/minute.
    assert middleware._allow(ALLOWED, now + 2) is True


async def test_an_unknown_update_type_bypasses_the_throttle_without_crashing() -> None:
    middleware = ThrottleMiddleware(per_minute=60, burst=1)
    handler = Recorder()
    assert await middleware(handler, Update(update_id=1), {}) == "handled"


async def test_a_throttled_callback_gets_a_toast(replies: list[str]) -> None:
    middleware = ThrottleMiddleware(per_minute=60, burst=1)
    handler = Recorder()
    await middleware(handler, an_update(a_callback(ALLOWED)), {})
    await middleware(handler, an_update(a_callback(ALLOWED)), {})
    assert replies == ["Slow down a moment."]
