"""Link intake: a recognised link in, a choice keyboard out.

Ids come from the provider regexes rather than a yt-dlp lookup: a pattern
match answers instantly, where a network round-trip would cost seconds per
link before the user has even chosen anything.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from chaptercut.bot import keyboards, texts
from chaptercut.logging import get_logger
from chaptercut.providers.registry import ProviderRegistry
from chaptercut.queue.models import ExtractType, Request
from chaptercut.queue.repository import Repository
from chaptercut.queue.worker import Worker
from chaptercut.settings import Settings

log = get_logger(__name__)

router = Router(name="intake")


@router.message(F.text)
async def handle_text(
    message: Message,
    repo: Repository,
    registry: ProviderRegistry,
    worker: Worker,
    settings: Settings,
) -> None:
    text = message.text or ""
    if text.startswith("/"):
        return

    refs = registry.find_refs(text)
    if not refs:
        await message.answer(texts.not_a_link(registry.labels))
        return

    if len(refs) > 1:
        await message.answer(texts.MULTIPLE_LINKS)

    ref = refs[0]
    user = message.from_user
    if user is None:  # pragma: no cover - messages from channels have no user
        return

    request = await repo.create_request(ref, user_id=user.id, chat_id=message.chat.id)
    log.info(
        "intake.request",
        req_id=request.req_id,
        provider=ref.provider,
        video_id=ref.media_id,
        user_id=user.id,
    )

    if not registry.get(ref.provider).supports_video:
        await _audio_only(message, repo, worker, settings, request)
        return

    await message.answer(
        texts.CHOOSE_TYPE_PLAIN,
        reply_markup=keyboards.type_keyboard(request.req_id),
    )


async def _audio_only(
    message: Message, repo: Repository, worker: Worker, settings: Settings, request: Request
) -> None:
    """Skip the Audio/Video question for a site that only has audio."""
    await repo.set_request_type(request.req_id, ExtractType.AUDIO)
    if settings.fileserver_enabled:
        await message.answer(
            texts.CHOOSE_DESTINATION,
            reply_markup=keyboards.destination_keyboard(request.req_id),
        )
        return

    # The status message goes out first so the job is born knowing its id.
    position = await repo.queue_length() + 1
    status = await message.answer(texts.queued_text("", position), parse_mode="HTML")
    await repo.enqueue(request, ExtractType.AUDIO, status_msg_id=status.message_id)
    worker.wake()
