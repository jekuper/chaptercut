"""Release the bot token from Telegram's cloud Bot API server.

A token is bound to one Bot API server at a time. Before a self-hosted server
will accept it, the cloud server has to let it go. Run once, then wait about
ten minutes before starting compose.
"""

from __future__ import annotations

import asyncio
import sys

from aiogram import Bot

from chaptercut.settings import load_settings

WAIT_NOTE = (
    "Logged out of the cloud Bot API server.\n"
    "Wait about 10 minutes, then start the local server with `just up`.\n"
    "To go back to the cloud later, call bot.close() against the local server first."
)


async def main() -> int:
    settings = load_settings()
    bot = Bot(token=settings.bot_token.get_secret_value())
    try:
        await bot.log_out()
    except Exception as exc:  # noqa: BLE001 - this is a one-shot operator script
        print(f"log_out failed: {exc}", file=sys.stderr)
        return 1
    finally:
        await bot.session.close()
    print(WAIT_NOTE)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
