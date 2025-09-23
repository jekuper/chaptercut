"""Intake and choice handlers, called directly with aiogram objects.

The point is the request/job bookkeeping and that several links can be in
flight at once, which the predecessor's FSM could not do.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, User
from pydantic import SecretStr

from chaptercut.bot import keyboards
from chaptercut.bot.callbacks import BackCb, DestCb, QualityCb, TypeCb
from chaptercut.bot.routers.choices import on_back, on_destination, on_quality, on_type
from chaptercut.bot.routers.intake import handle_text
from chaptercut.pipeline.formats import select_video_formats
from chaptercut.pipeline.ytdlp import VideoInfo, YtdlpError
from chaptercut.providers.registry import ProviderRegistry
from chaptercut.providers.tiktok import TikTokProvider
from chaptercut.queue.models import Destination, ExtractType, Request
from chaptercut.queue.repository import Repository
from chaptercut.settings import Settings
from tests.conftest import youtube_ref

USER = 111
CHAT = 999
VIDEO_ID = "dQw4w9WgXcQ"
TIKTOK_ID = "7123456789012345678"

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
    """Stands in for the factory and for the client it hands back."""

    def __init__(
        self, formats: list[dict[str, Any]] | None = None, error: Exception | None = None
    ) -> None:
        self.formats = formats
        self.error = error
        self.calls = 0
        self.providers_seen: list[str] = []

    def for_provider(self, provider: Any) -> FakeYtdlp:
        self.providers_seen.append(provider.name)
        return self

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


def buttons(markup: InlineKeyboardMarkup | None) -> list[str]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [button.text for row in markup.inline_keyboard for button in row]


def first_req_id(markup: InlineKeyboardMarkup | None) -> str:
    assert isinstance(markup, InlineKeyboardMarkup)
    data = markup.inline_keyboard[0][0].callback_data
    assert data is not None
    return TypeCb.unpack(data).req_id


# --- intake ------------------------------------------------------------------


async def test_a_youtube_link_creates_a_request_and_offers_the_type_keyboard(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    await handle_text(a_message(f"https://youtu.be/{VIDEO_ID}"), repo, registry)

    text, markup = sent.answers[0]
    assert "What do you want" in text
    assert buttons(markup) == ["Audio", "Video"]


async def test_a_tiktok_link_creates_a_tiktok_request(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    await handle_text(
        a_message(f"https://www.tiktok.com/@someone/video/{TIKTOK_ID}"), repo, registry
    )

    assert buttons(sent.answers[0][1]) == ["Audio", "Video"]
    request = await repo.get_request(first_req_id(sent.answers[0][1]))
    assert request is not None
    assert request.provider == "tiktok"
    assert request.video_id == TIKTOK_ID


async def test_a_tiktok_short_link_stores_the_code_for_later_resolution(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    await handle_text(a_message("https://vm.tiktok.com/ZMhqAbCdE/"), repo, registry)

    request = await repo.get_request(first_req_id(sent.answers[0][1]))
    assert request is not None
    assert request.provider == "tiktok"
    assert request.video_id == "ZMhqAbCdE"
    assert request.url == "https://vm.tiktok.com/ZMhqAbCdE"


async def test_tracking_parameters_never_reach_the_stored_url(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    await handle_text(
        a_message(f"https://www.tiktok.com/@u/video/{TIKTOK_ID}?is_from_webapp=1&sender_device=pc"),
        repo,
        registry,
    )
    request = await repo.get_request(first_req_id(sent.answers[0][1]))
    assert request is not None
    assert request.url == f"https://www.tiktok.com/@u/video/{TIKTOK_ID}"


async def test_non_link_text_lists_the_sites_it_accepts(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    await handle_text(a_message("what is this"), repo, registry)
    assert "not a link I recognise" in sent.answers[0][0]
    assert "YouTube" in sent.answers[0][0]
    assert "TikTok" in sent.answers[0][0]


async def test_commands_are_left_to_the_command_router(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    await handle_text(a_message("/status"), repo, registry)
    assert sent.answers == []


async def test_several_links_processes_the_first_and_says_so(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    await handle_text(
        a_message(f"https://youtu.be/{VIDEO_ID} https://youtu.be/bbbbbbbbbbb"), repo, registry
    )
    assert "several links" in sent.answers[0][0]
    assert len(sent.answers) == 2


async def test_a_mixed_message_takes_the_first_link_whichever_site(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    await handle_text(
        a_message(f"https://www.tiktok.com/@u/video/{TIKTOK_ID} then https://youtu.be/{VIDEO_ID}"),
        repo,
        registry,
    )
    request = await repo.get_request(first_req_id(sent.answers[1][1]))
    assert request is not None
    assert request.provider == "tiktok"


async def test_two_links_in_flight_get_distinct_requests(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    # The predecessor's FSM state collided here and corrupted both dialogues.
    await handle_text(a_message(f"https://youtu.be/{VIDEO_ID}"), repo, registry)
    await handle_text(a_message(f"https://www.tiktok.com/@u/video/{TIKTOK_ID}"), repo, registry)

    req_ids = {
        TypeCb.unpack(button.callback_data).req_id  # pyright: ignore[reportArgumentType]
        for _text, markup in sent.answers
        if isinstance(markup, InlineKeyboardMarkup)
        for row in markup.inline_keyboard
        for button in row
    }
    assert len(req_ids) == 2


async def test_a_disabled_provider_is_not_recognised(repo: Repository, sent: Sent) -> None:
    youtube_only = ProviderRegistry.enabled(["youtube"])
    await handle_text(a_message(f"https://www.tiktok.com/@u/video/{TIKTOK_ID}"), repo, youtube_only)
    assert "not a link I recognise" in sent.answers[0][0]
    assert "TikTok" not in sent.answers[0][0]


# --- choices -----------------------------------------------------------------


async def test_choosing_audio_enqueues_a_job(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    request = await repo.create_request(youtube_ref(VIDEO_ID), user_id=USER, chat_id=CHAT)
    worker = FakeWorker()

    await on_type(
        a_callback(),
        TypeCb(req_id=request.req_id, kind=ExtractType.AUDIO),
        repo,
        worker,  # pyright: ignore[reportArgumentType]
        FakeYtdlp(),  # pyright: ignore[reportArgumentType]
        registry,
        settings,
    )

    assert await repo.queue_length() == 1
    assert worker.wakes == 1
    assert "Queued" in sent.edits[-1][0]


async def test_an_enqueued_job_carries_the_provider_and_canonical_url(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    ref = TikTokProvider().match(f"https://www.tiktok.com/@u/video/{TIKTOK_ID}")
    assert ref is not None
    request = await repo.create_request(ref, user_id=USER, chat_id=CHAT)

    await on_type(
        a_callback(),
        TypeCb(req_id=request.req_id, kind=ExtractType.AUDIO),
        repo,
        FakeWorker(),  # pyright: ignore[reportArgumentType]
        FakeYtdlp(),  # pyright: ignore[reportArgumentType]
        registry,
        settings,
    )

    jobs = await repo.queued_jobs_for_user(USER)
    assert [job.provider for job in jobs] == ["tiktok"]
    assert jobs[0].url == f"https://www.tiktok.com/@u/video/{TIKTOK_ID}"


async def test_choosing_video_shows_the_quality_keyboard(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    request = await repo.create_request(youtube_ref(VIDEO_ID), user_id=USER, chat_id=CHAT)
    ytdlp = FakeYtdlp(formats=FORMATS)

    await on_type(
        a_callback(),
        TypeCb(req_id=request.req_id, kind=ExtractType.VIDEO),
        repo,
        FakeWorker(),  # pyright: ignore[reportArgumentType]
        ytdlp,  # pyright: ignore[reportArgumentType]
        registry,
        settings,
    )

    labels = buttons(sent.edits[-1][1])
    assert labels[0].startswith("1080p")
    assert labels[-1] == "Back"
    assert ytdlp.providers_seen == ["youtube"]
    assert await repo.queue_length() == 0


async def test_format_listing_uses_the_requests_own_provider(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    ref = TikTokProvider().match(f"https://www.tiktok.com/@u/video/{TIKTOK_ID}")
    assert ref is not None
    request = await repo.create_request(ref, user_id=USER, chat_id=CHAT)
    ytdlp = FakeYtdlp(formats=FORMATS)

    await on_type(
        a_callback(),
        TypeCb(req_id=request.req_id, kind=ExtractType.VIDEO),
        repo,
        FakeWorker(),  # pyright: ignore[reportArgumentType]
        ytdlp,  # pyright: ignore[reportArgumentType]
        registry,
        settings,
    )

    assert ytdlp.providers_seen == ["tiktok"]


async def test_the_format_list_is_cached_on_the_request(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    request = await repo.create_request(youtube_ref(VIDEO_ID), user_id=USER, chat_id=CHAT)
    ytdlp = FakeYtdlp(formats=FORMATS)
    callback_data = TypeCb(req_id=request.req_id, kind=ExtractType.VIDEO)

    await on_type(a_callback(), callback_data, repo, FakeWorker(), ytdlp, registry, settings)  # pyright: ignore[reportArgumentType]
    await on_type(a_callback(), callback_data, repo, FakeWorker(), ytdlp, registry, settings)  # pyright: ignore[reportArgumentType]

    # The second pass reuses the stored list instead of shelling out again.
    assert ytdlp.calls == 1


async def test_a_bot_check_while_listing_formats_is_surfaced(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    request = await repo.create_request(youtube_ref(VIDEO_ID), user_id=USER, chat_id=CHAT)
    ytdlp = FakeYtdlp(error=YtdlpError("blocked", bot_check=True))

    await on_type(
        a_callback(),
        TypeCb(req_id=request.req_id, kind=ExtractType.VIDEO),
        repo,
        FakeWorker(),  # pyright: ignore[reportArgumentType]
        ytdlp,  # pyright: ignore[reportArgumentType]
        registry,
        settings,
    )
    assert "signed-in session" in sent.edits[-1][0]


async def test_no_formats_says_so(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    request = await repo.create_request(youtube_ref(VIDEO_ID), user_id=USER, chat_id=CHAT)

    await on_type(
        a_callback(),
        TypeCb(req_id=request.req_id, kind=ExtractType.VIDEO),
        repo,
        FakeWorker(),  # pyright: ignore[reportArgumentType]
        FakeYtdlp(formats=[]),  # pyright: ignore[reportArgumentType]
        registry,
        settings,
    )
    assert "No downloadable video formats" in sent.edits[-1][0]


async def test_choosing_a_quality_enqueues_a_video_job(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    request = await repo.create_request(youtube_ref(VIDEO_ID), user_id=USER, chat_id=CHAT)
    await repo.set_request_formats(
        request.req_id, Request.encode_formats(select_video_formats({"formats": FORMATS}))
    )
    worker = FakeWorker()

    await on_quality(
        a_callback(),
        QualityCb(req_id=request.req_id, format_id="137"),
        repo,
        worker,  # pyright: ignore[reportArgumentType]
        settings,
    )

    jobs = await repo.queued_jobs_for_user(USER)
    assert len(jobs) == 1
    assert jobs[0].kind is ExtractType.VIDEO
    assert jobs[0].format_id == "137"
    assert worker.wakes == 1


async def test_an_unknown_format_id_is_treated_as_expired(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    request = await repo.create_request(youtube_ref(VIDEO_ID), user_id=USER, chat_id=CHAT)

    await on_quality(
        a_callback(),
        QualityCb(req_id=request.req_id, format_id="nope"),
        repo,
        FakeWorker(),  # pyright: ignore[reportArgumentType]
        settings,
    )

    assert sent.toasts == ["Request expired, send the link again."]
    assert await repo.queue_length() == 0


async def test_back_returns_to_the_type_keyboard(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    request = await repo.create_request(youtube_ref(VIDEO_ID), user_id=USER, chat_id=CHAT)

    await on_back(a_callback(), BackCb(req_id=request.req_id), repo)

    assert buttons(sent.edits[-1][1]) == ["Audio", "Video"]


async def test_a_stale_callback_after_a_restart_is_answered_and_disarmed(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    await on_type(
        a_callback(),
        TypeCb(req_id="gone", kind=ExtractType.AUDIO),
        repo,
        FakeWorker(),  # pyright: ignore[reportArgumentType]
        FakeYtdlp(),  # pyright: ignore[reportArgumentType]
        registry,
        settings,
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


# --- destination step ---------------------------------------------------------


@pytest.fixture
def with_server(settings: Settings) -> Settings:
    settings.fileserver_url = "https://files.invalid:8443"
    settings.fileserver_token = SecretStr("a-token-long-enough-for-the-check-here")
    return settings


async def test_the_destination_step_is_skipped_without_a_server(
    repo: Repository, sent: Sent, registry: ProviderRegistry, settings: Settings
) -> None:
    # One possible answer is not a question worth asking.
    request = await repo.create_request(youtube_ref(VIDEO_ID), user_id=USER, chat_id=CHAT)

    await on_type(
        a_callback(),
        TypeCb(req_id=request.req_id, kind=ExtractType.AUDIO),
        repo,
        FakeWorker(),  # pyright: ignore[reportArgumentType]
        FakeYtdlp(),  # pyright: ignore[reportArgumentType]
        registry,
        settings,
    )

    assert await repo.queue_length() == 1
    assert "Queued" in sent.edits[-1][0]


async def test_audio_asks_where_it_should_go(
    repo: Repository, sent: Sent, registry: ProviderRegistry, with_server: Settings
) -> None:
    request = await repo.create_request(youtube_ref(VIDEO_ID), user_id=USER, chat_id=CHAT)

    await on_type(
        a_callback(),
        TypeCb(req_id=request.req_id, kind=ExtractType.AUDIO),
        repo,
        FakeWorker(),  # pyright: ignore[reportArgumentType]
        FakeYtdlp(),  # pyright: ignore[reportArgumentType]
        registry,
        with_server,
    )

    assert "Where should it go" in sent.edits[-1][0]
    assert buttons(sent.edits[-1][1]) == ["Telegram", "Direct link"]
    assert await repo.queue_length() == 0


async def test_picking_a_destination_enqueues_with_it(
    repo: Repository, sent: Sent, registry: ProviderRegistry, with_server: Settings
) -> None:
    request = await repo.create_request(youtube_ref(VIDEO_ID), user_id=USER, chat_id=CHAT)
    await repo.set_request_type(request.req_id, ExtractType.AUDIO)
    worker = FakeWorker()

    await on_destination(
        a_callback(),
        DestCb(req_id=request.req_id, where=Destination.SERVER),
        repo,
        worker,  # pyright: ignore[reportArgumentType]
    )

    jobs = await repo.queued_jobs_for_user(USER)
    assert len(jobs) == 1
    assert jobs[0].destination is Destination.SERVER
    assert worker.wakes == 1


async def test_the_chosen_quality_survives_the_destination_step(
    repo: Repository, sent: Sent, registry: ProviderRegistry, with_server: Settings
) -> None:
    # Quality is picked first, so it has to outlive the keyboard that asked.
    request = await repo.create_request(youtube_ref(VIDEO_ID), user_id=USER, chat_id=CHAT)
    await repo.set_request_type(request.req_id, ExtractType.VIDEO)
    await repo.set_request_formats(
        request.req_id, Request.encode_formats(select_video_formats({"formats": FORMATS}))
    )

    await on_quality(
        a_callback(),
        QualityCb(req_id=request.req_id, format_id="137"),
        repo,
        FakeWorker(),  # pyright: ignore[reportArgumentType]
        with_server,
    )
    assert "Where should it go" in sent.edits[-1][0]

    await on_destination(
        a_callback(),
        DestCb(req_id=request.req_id, where=Destination.TELEGRAM),
        repo,
        FakeWorker(),  # pyright: ignore[reportArgumentType]
    )

    jobs = await repo.queued_jobs_for_user(USER)
    assert len(jobs) == 1
    assert jobs[0].format_id == "137"
    assert jobs[0].kind is ExtractType.VIDEO
    assert jobs[0].destination is Destination.TELEGRAM


async def test_a_destination_callback_for_an_unknown_request_expires(
    repo: Repository, sent: Sent, registry: ProviderRegistry, with_server: Settings
) -> None:
    await on_destination(
        a_callback(),
        DestCb(req_id="gone", where=Destination.SERVER),
        repo,
        FakeWorker(),  # pyright: ignore[reportArgumentType]
    )
    assert sent.toasts == ["Request expired, send the link again."]
    assert await repo.queue_length() == 0
