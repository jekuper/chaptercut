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
from chaptercut.queue.repository import Repository

log = get_logger(__name__)

router = Router(name="intake")


@router.message(F.text)
async def handle_text(message: Message, repo: Repository, registry: ProviderRegistry) -> None:
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

    await message.answer(
        texts.CHOOSE_TYPE_PLAIN,
        reply_markup=keyboards.type_keyboard(request.req_id),
    )
