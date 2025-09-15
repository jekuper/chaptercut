"""Building the Bot and Dispatcher."""

from __future__ import annotations

from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from chaptercut.bot.middlewares.auth import AuthMiddleware
from chaptercut.bot.middlewares.throttle import ThrottleMiddleware
from chaptercut.bot.routers import choices, commands, intake
from chaptercut.settings import Settings


def create_bot(settings: Settings) -> Bot:
    """A Bot pointed at the configured API server.

    With a local server sharing the data volume, `is_local=True` makes aiogram
    pass file paths instead of uploading bytes, which is what lifts the 50 MB
    cap to 2 GB.
    """
    session = AiohttpSession(
        api=TelegramAPIServer.from_base(settings.bot_api_url, is_local=settings.bot_api_local)
    )
    return Bot(
        token=settings.bot_token.get_secret_value(),
        session=session,
        default=DefaultBotProperties(parse_mode=None),
    )


def create_dispatcher(settings: Settings, **context: Any) -> Dispatcher:
    """Wire middlewares and routers. `context` is injected into every handler."""
    dispatcher = Dispatcher(settings=settings, **context)

    auth = AuthMiddleware(settings)
    throttle = ThrottleMiddleware(settings.rate_limit_per_minute)
    # Outer, so an unauthorized update never reaches a filter.
    dispatcher.update.outer_middleware(auth)
    dispatcher.update.outer_middleware(throttle)

    dispatcher.include_router(commands.router)
    dispatcher.include_router(choices.router)
    # Intake matches any remaining text, so it goes last.
    dispatcher.include_router(intake.router)
    return dispatcher
