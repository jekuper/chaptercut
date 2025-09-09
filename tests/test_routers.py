"""Intake and choice handlers, called directly with aiogram objects.

The point is the request/job bookkeeping and that several links can be in
flight at once, which the predecessor's FSM could not do.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, User

from chaptercut.bot import keyboards
from chaptercut.bot.callbacks import BackCb, QualityCb, TypeCb
from chaptercut.bot.routers.choices import on_back, on_quality, on_type
from chaptercut.bot.routers.intake import handle_text
from chaptercut.pipeline.formats import select_video_formats
from chaptercut.pipeline.ytdlp import VideoInfo, YtdlpError
from chaptercut.queue.models import ExtractType
from chaptercut.queue.repository import Repository

USER = 111
CHAT = 999
VIDEO_ID = "dQw4w9WgXcQ"

FORMATS = [
    {
        "format_id": "137",
        "height": 1080,
        "ext": "mp4",
        "vcodec": "avc1",
        "acodec": "none",
        "filesize": 210_000_000,
    },
    {
        "format_id": "136",
        "height": 720,
        "ext": "mp4",
        "vcodec": "avc1",
        "acodec": "none",
        "filesize": 90_000_000,
    },
    {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "filesize": 4_000_000},
]


class Sent:
    """Captures what a handler sent or edited, without a Bot."""

    def __init__(self) -> None:
        self.answers: list[tuple[str, InlineKeyboardMarkup | None]] = []
        self.edits: list[tuple[str, InlineKeyboardMarkup | None]] = []
        self.toasts: list[str] = []
        self.markup_cleared = 0


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Sent:
    record = Sent()

    async def answer(self: Message, text: str = "", **kwargs: Any) -> Message:
        record.answers.append((text, kwargs.get("reply_markup")))
        return self

    async def edit_text(self: Message, text: str = "", **kwargs: Any) -> Message:
        record.edits.append((text, kwargs.get("reply_markup")))
        return self

    async def edit_reply_markup(self: Message, **kwargs: Any) -> Message:
        record.markup_cleared += 1
        return self

    async def callback_answer(self: CallbackQuery, text: str = "", **kwargs: Any) -> bool:
        record.toasts.append(text)
        return True

    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(Message, "edit_text", edit_text)
    monkeypatch.setattr(Message, "edit_reply_markup", edit_reply_markup)
    monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
    return record


class FakeWorker:
    def __init__(self) -> None:
        self.wakes = 0

    def wake(self) -> None:
        self.wakes += 1


class FakeYtdlp:
    def __init__(self, formats: list[dict[str, Any]] | None = None, error: Exception | None = None):
        self.formats = formats
        self.error = error
        self.calls = 0

    async def info(self, url: str, timeout: float = 120.0) -> VideoInfo:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return VideoInfo({"id": VIDEO_ID, "title": "Test", "formats": self.formats or []})


def a_message(text: str) -> Message:
    return Message(
        message_id=7,
        date=datetime(2026, 1, 1),
        chat=Chat(id=CHAT, type="private"),
        from_user=User(id=USER, is_bot=False, first_name="Tester"),
        text=text,
    )


def a_callback() -> CallbackQuery:
    return CallbackQuery(
        id="cb1",
        from_user=User(id=USER, is_bot=False, first_name="Tester"),
        chat_instance="ci",
        message=a_message("keyboard"),
        data="x",
    )


async def test_a_link_creates_a_request_and_offers_the_type_keyboard(
    repo: Repository, sent: Sent
) -> None:
    await handle_text(a_message(f"https://youtu.be/{VIDEO_ID}"), repo)

    text, markup = sent.answers[0]
    assert "What do you want" in text
    assert isinstance(markup, InlineKeyboardMarkup)
    assert [button.text for row in markup.inline_keyboard for button in row] == ["Audio", "Video"]


async def test_non_link_text_gets_the_help_nudge(repo: Repository, sent: Sent) -> None:
    await handle_text(a_message("what is this"), repo)
    assert "not a YouTube link" in sent.answers[0][0]


async def test_commands_are_left_to_the_command_router(repo: Repository, sent: Sent) -> None:
    await handle_text(a_message("/status"), repo)
    assert sent.answers == []


async def test_several_links_processes_the_first_and_says_so(repo: Repository, sent: Sent) -> None:
    await handle_text(a_message(f"https://youtu.be/{VIDEO_ID} https://youtu.be/bbbbbbbbbbb"), repo)
    assert "several links" in sent.answers[0][0]
    assert len(sent.answers) == 2


async def test_two_links_in_flight_get_distinct_requests(repo: Repository, sent: Sent) -> None:
    # The predecessor's FSM state collided here and corrupted both dialogues.
    await handle_text(a_message(f"https://youtu.be/{VIDEO_ID}"), repo)
    await handle_text(a_message("https://youtu.be/bbbbbbbbbbb"), repo)

    req_ids = {
        TypeCb.unpack(button.callback_data).req_id  # pyright: ignore[reportArgumentType]
        for _text, markup in sent.answers
        if isinstance(markup, InlineKeyboardMarkup)
        for row in markup.inline_keyboard
        for button in row
    }
    assert len(req_ids) == 2


async def test_choosing_audio_enqueues_a_job(repo: Repository, sent: Sent) -> None:
    request = await repo.create_request(USER, CHAT, "https://youtu.be/x", VIDEO_ID)
    worker = FakeWorker()

    await on_type(
        a_callback(),
        TypeCb(req_id=request.req_id, kind=ExtractType.AUDIO),
        repo,
        worker,  # pyright: ignore[reportArgumentType]
        FakeYtdlp(),  # pyright: ignore[reportArgumentType]
    )

    assert await repo.queue_length() == 1
    assert worker.wakes == 1
    assert "Queued" in sent.edits[-1][0]


async def test_choosing_video_shows_the_quality_keyboard(repo: Repository, sent: Sent) -> None:
    request = await repo.create_request(USER, CHAT, "https://youtu.be/x", VIDEO_ID)
    ytdlp = FakeYtdlp(formats=FORMATS)

    await on_type(
        a_callback(),
        TypeCb(req_id=request.req_id, kind=ExtractType.VIDEO),
        repo,
        FakeWorker(),  # pyright: ignore[reportArgumentType]
        ytdlp,  # pyright: ignore[reportArgumentType]
    )

    _text, markup = sent.edits[-1]
    assert isinstance(markup, InlineKeyboardMarkup)
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert labels[0].startswith("1080p")
    assert labels[-1] == "Back"
    assert await repo.queue_length() == 0


async def test_the_format_list_is_cached_on_the_request(repo: Repository, sent: Sent) -> None:
    request = await repo.create_request(USER, CHAT, "https://youtu.be/x", VIDEO_ID)
    ytdlp = FakeYtdlp(formats=FORMATS)
    callback_data = TypeCb(req_id=request.req_id, kind=ExtractType.VIDEO)

    await on_type(a_callback(), callback_data, repo, FakeWorker(), ytdlp)  # pyright: ignore[reportArgumentType]
    await on_type(a_callback(), callback_data, repo, FakeWorker(), ytdlp)  # pyright: ignore[reportArgumentType]

    # The second pass reuses the stored list instead of shelling out again.
    assert ytdlp.calls == 1


async def test_a_bot_check_while_listing_formats_is_surfaced(repo: Repository, sent: Sent) -> None:
    request = await repo.create_request(USER, CHAT, "https://youtu.be/x", VIDEO_ID)
    ytdlp = FakeYtdlp(error=YtdlpError("blocked", bot_check=True))

    await on_type(
        a_callback(),
        TypeCb(req_id=request.req_id, kind=ExtractType.VIDEO),
        repo,
        FakeWorker(),  # pyright: ignore[reportArgumentType]
        ytdlp,  # pyright: ignore[reportArgumentType]
    )
    assert "signed-in session" in sent.edits[-1][0]


async def test_no_formats_says_so(repo: Repository, sent: Sent) -> None:
    request = await repo.create_request(USER, CHAT, "https://youtu.be/x", VIDEO_ID)

    await on_type(
        a_callback(),
        TypeCb(req_id=request.req_id, kind=ExtractType.VIDEO),
        repo,
        FakeWorker(),  # pyright: ignore[reportArgumentType]
        FakeYtdlp(formats=[]),  # pyright: ignore[reportArgumentType]
    )
    assert "No downloadable video formats" in sent.edits[-1][0]


async def test_choosing_a_quality_enqueues_a_video_job(repo: Repository, sent: Sent) -> None:
    request = await repo.create_request(USER, CHAT, "https://youtu.be/x", VIDEO_ID)
    from chaptercut.queue.models import Request

    await repo.set_request_formats(
        request.req_id, Request.encode_formats(select_video_formats({"formats": FORMATS}))
    )
    worker = FakeWorker()

    await on_quality(
        a_callback(),
        QualityCb(req_id=request.req_id, format_id="137"),
        repo,
        worker,  # pyright: ignore[reportArgumentType]
    )

    jobs = await repo.queued_jobs_for_user(USER)
    assert len(jobs) == 1
    assert jobs[0].kind is ExtractType.VIDEO
    assert jobs[0].format_id == "137"
    assert worker.wakes == 1


async def test_an_unknown_format_id_is_treated_as_expired(repo: Repository, sent: Sent) -> None:
    request = await repo.create_request(USER, CHAT, "https://youtu.be/x", VIDEO_ID)

    await on_quality(
        a_callback(),
        QualityCb(req_id=request.req_id, format_id="nope"),
        repo,
        FakeWorker(),  # pyright: ignore[reportArgumentType]
    )

    assert sent.toasts == ["Request expired, send the link again."]
    assert await repo.queue_length() == 0


async def test_back_returns_to_the_type_keyboard(repo: Repository, sent: Sent) -> None:
    request = await repo.create_request(USER, CHAT, "https://youtu.be/x", VIDEO_ID)

    await on_back(a_callback(), BackCb(req_id=request.req_id), repo)

    _text, markup = sent.edits[-1]
    assert isinstance(markup, InlineKeyboardMarkup)
    assert [button.text for row in markup.inline_keyboard for button in row] == ["Audio", "Video"]


async def test_a_stale_callback_after_a_restart_is_answered_and_disarmed(
    repo: Repository, sent: Sent
) -> None:
    await on_type(
        a_callback(),
        TypeCb(req_id="gone", kind=ExtractType.AUDIO),
        repo,
        FakeWorker(),  # pyright: ignore[reportArgumentType]
        FakeYtdlp(),  # pyright: ignore[reportArgumentType]
    )

    assert sent.toasts == ["Request expired, send the link again."]
    assert sent.markup_cleared == 1
    assert await repo.queue_length() == 0


def test_quality_labels_show_height_container_and_size() -> None:
    options = select_video_formats({"formats": FORMATS})
    markup = keyboards.quality_keyboard("req1", options)
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert labels[0] == "1080p - mp4 - ~204 MB"
    assert labels[1] == "720p - mp4 - ~89.6 MB"


def test_callback_data_round_trips() -> None:
    packed = TypeCb(req_id="abc123", kind=ExtractType.VIDEO).pack()
    unpacked = TypeCb.unpack(packed)
    assert unpacked.req_id == "abc123"
    assert unpacked.kind is ExtractType.VIDEO
