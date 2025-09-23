"""Inline keyboard builders."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from chaptercut.bot import texts
from chaptercut.bot.callbacks import BackCb, DestCb, QualityCb, TypeCb
from chaptercut.pipeline.formats import FormatOption
from chaptercut.queue.models import Destination, ExtractType


def type_keyboard(req_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BUTTON_AUDIO, callback_data=TypeCb(req_id=req_id, kind=ExtractType.AUDIO)
    )
    builder.button(
        text=texts.BUTTON_VIDEO, callback_data=TypeCb(req_id=req_id, kind=ExtractType.VIDEO)
    )
    builder.adjust(2)
    return builder.as_markup()


def destination_keyboard(req_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BUTTON_TELEGRAM,
        callback_data=DestCb(req_id=req_id, where=Destination.TELEGRAM),
    )
    builder.button(
        text=texts.BUTTON_SERVER,
        callback_data=DestCb(req_id=req_id, where=Destination.SERVER),
    )
    builder.adjust(2)
    return builder.as_markup()


def quality_keyboard(req_id: str, options: list[FormatOption]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for option in options:
        builder.button(
            text=texts.quality_label(option.height, option.ext, option.size_bytes),
            callback_data=QualityCb(req_id=req_id, format_id=option.format_id),
        )
    builder.button(text=texts.BUTTON_BACK, callback_data=BackCb(req_id=req_id))
    builder.adjust(1)
    return builder.as_markup()
