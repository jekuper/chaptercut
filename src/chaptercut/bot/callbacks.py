"""Typed callback data.

Everything a button needs is carried in the callback payload plus a `req_id`
that resolves to a row in `requests`. No FSM state, so several links can be
mid-dialogue at once without colliding, and a restart invalidates nothing that
has not genuinely expired.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData

from chaptercut.queue.models import Destination, ExtractType


class TypeCb(CallbackData, prefix="t"):
    """Audio or video was picked for a request."""

    req_id: str
    kind: ExtractType


class QualityCb(CallbackData, prefix="q"):
    """A video quality was picked."""

    req_id: str
    format_id: str


class DestCb(CallbackData, prefix="d"):
    """Where the finished files should go."""

    req_id: str
    where: Destination


class BackCb(CallbackData, prefix="b"):
    """Return from the quality keyboard to the type keyboard."""

    req_id: str
