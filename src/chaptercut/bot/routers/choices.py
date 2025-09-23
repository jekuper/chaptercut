"""Callback handlers for the choice keyboards.

Nothing here blocks: fetching the format list is an awaited subprocess, and the
result is cached on the request row so the Back button never refetches.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery, Message

from chaptercut.bot import keyboards, texts
from chaptercut.bot.callbacks import BackCb, DestCb, QualityCb, TypeCb
from chaptercut.logging import get_logger
from chaptercut.pipeline.formats import find_option, select_video_formats
from chaptercut.pipeline.ytdlp import YtdlpError, YtdlpFactory
from chaptercut.providers.registry import ProviderRegistry
from chaptercut.queue.models import ExtractType, Request
from chaptercut.queue.repository import Repository
from chaptercut.queue.worker import Worker
from chaptercut.settings import Settings

log = get_logger(__name__)

router = Router(name="choices")


async def _expired(callback: CallbackQuery) -> None:
    """A request the bot no longer knows about, e.g. after a long gap."""
    await callback.answer(texts.REQUEST_EXPIRED, show_alert=False)
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)


async def _resolve(callback: CallbackQuery, repo: Repository, req_id: str) -> Request | None:
    request = await repo.get_request(req_id)
    if request is None:
        await _expired(callback)
        return None
    return request


@router.callback_query(TypeCb.filter())
async def on_type(
    callback: CallbackQuery,
    callback_data: TypeCb,
    repo: Repository,
    worker: Worker,
    ytdlp: YtdlpFactory,
    registry: ProviderRegistry,
    settings: Settings,
) -> None:
    request = await _resolve(callback, repo, callback_data.req_id)
    if request is None:
        return
    await repo.set_request_type(request.req_id, callback_data.kind)
    await callback.answer()

    if callback_data.kind is ExtractType.AUDIO:
        await _ask_destination(callback, repo, worker, settings, request, ExtractType.AUDIO)
        return
    await _show_qualities(callback, repo, ytdlp, registry, request)


@router.callback_query(BackCb.filter())
async def on_back(callback: CallbackQuery, callback_data: BackCb, repo: Repository) -> None:
    request = await _resolve(callback, repo, callback_data.req_id)
    if request is None:
        return
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            texts.CHOOSE_TYPE_PLAIN,
            reply_markup=keyboards.type_keyboard(request.req_id),
        )


@router.callback_query(QualityCb.filter())
async def on_quality(
    callback: CallbackQuery,
    callback_data: QualityCb,
    repo: Repository,
    worker: Worker,
    settings: Settings,
) -> None:
    request = await _resolve(callback, repo, callback_data.req_id)
    if request is None:
        return
    option = find_option(request.formats, callback_data.format_id)
    if option is None:
        await _expired(callback)
        return
    await callback.answer()
    await _ask_destination(
        callback,
        repo,
        worker,
        settings,
        request,
        ExtractType.VIDEO,
        format_id=callback_data.format_id,
    )


@router.callback_query(DestCb.filter())
async def on_destination(
    callback: CallbackQuery,
    callback_data: DestCb,
    repo: Repository,
    worker: Worker,
) -> None:
    request = await _resolve(callback, repo, callback_data.req_id)
    if request is None:
        return
    if request.extract_type is None:
        await _expired(callback)
        return
    await repo.set_request_destination(request.req_id, callback_data.where)
    await callback.answer()

    request.destination = callback_data.where
    await _enqueue(
        callback, repo, worker, request, request.extract_type, format_id=request.chosen_format_id
    )


async def _show_qualities(
    callback: CallbackQuery,
    repo: Repository,
    ytdlp: YtdlpFactory,
    registry: ProviderRegistry,
    request: Request,
) -> None:
    message = callback.message
    if not isinstance(message, Message):
        return
    await message.edit_text(texts.FETCHING_FORMATS)

    options = request.formats
    if not options:
        try:
            provider = registry.get(request.provider)
            info = await ytdlp.for_provider(provider).info(request.url)
        except YtdlpError as exc:
            log.warning(
                "choices.formats_failed",
                provider=request.provider,
                video_id=request.video_id,
                bot_check=exc.bot_check,
            )
            await message.edit_text(texts.FAILED_BOT_CHECK if exc.bot_check else texts.NO_FORMATS)
            return
        options = select_video_formats(info.raw)
        if options:
            await repo.set_request_formats(request.req_id, Request.encode_formats(options))

    if not options:
        await message.edit_text(texts.NO_FORMATS)
        return

    await message.edit_text(
        texts.CHOOSE_QUALITY,
        reply_markup=keyboards.quality_keyboard(request.req_id, options),
    )


async def _ask_destination(
    callback: CallbackQuery,
    repo: Repository,
    worker: Worker,
    settings: Settings,
    request: Request,
    kind: ExtractType,
    format_id: str | None = None,
) -> None:
    """Offer Telegram or a direct link.

    With no file server configured there is only one possible answer, so the
    step disappears rather than showing a keyboard with one button.
    """
    if format_id is not None:
        await repo.set_request_format_choice(request.req_id, format_id)
        request.chosen_format_id = format_id

    if not settings.fileserver_enabled:
        await _enqueue(callback, repo, worker, request, kind, format_id=format_id)
        return

    message = callback.message
    if not isinstance(message, Message):
        return
    await message.edit_text(
        texts.CHOOSE_DESTINATION,
        reply_markup=keyboards.destination_keyboard(request.req_id),
    )


async def _enqueue(
    callback: CallbackQuery,
    repo: Repository,
    worker: Worker,
    request: Request,
    kind: ExtractType,
    format_id: str | None = None,
) -> None:
    message = callback.message
    if not isinstance(message, Message):
        return
    job = await repo.enqueue(request, kind, status_msg_id=message.message_id, format_id=format_id)
    position = await repo.queue_position(job.job_id)
    await message.edit_text(texts.queued_text("", position), parse_mode="HTML")
    worker.wake()
