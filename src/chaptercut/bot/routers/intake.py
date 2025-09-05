"""Link intake: a YouTube URL in, a choice keyboard out.

The video id comes from a regex. The predecessor made a full yt-dlp network
round-trip just to read the id out of the result, which cost seconds per link
for something an 11-character match already answers.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from chaptercut.bot import keyboards, texts
from chaptercut.logging import get_logger
from chaptercut.queue.repository import Repository
from chaptercut.util.youtube import canonical_url, find_video_ids

log = get_logger(__name__)

router = Router(name="intake")


@router.message(F.text)
async def handle_text(message: Message, repo: Repository) -> None:
    text = message.text or ""
    if text.startswith("/"):
        return

    video_ids = find_video_ids(text)
    if not video_ids:
        await message.answer(texts.NOT_A_LINK)
        return

    if len(video_ids) > 1:
        await message.answer(texts.MULTIPLE_LINKS)

    video_id = video_ids[0]
    user = message.from_user
    if user is None:  # pragma: no cover - messages from channels have no user
        return

    request = await repo.create_request(
        user_id=user.id,
        chat_id=message.chat.id,
        url=canonical_url(video_id),
        video_id=video_id,
    )
    log.info("intake.request", req_id=request.req_id, video_id=video_id, user_id=user.id)

    await message.answer(
        texts.CHOOSE_TYPE_PLAIN,
        reply_markup=keyboards.type_keyboard(request.req_id),
    )
