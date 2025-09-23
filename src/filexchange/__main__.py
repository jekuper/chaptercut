"""Entry point: `python -m filexchange`.

Logging setup lives here rather than in its own module. It is a dozen lines of
structlog boilerplate, and the bot already has its own copy; a shared one would
couple two things that deploy to different machines.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
import uvicorn

from filexchange.app import create_app
from filexchange.settings import load_settings


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=not json_output and sys.stdout.isatty())
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def run() -> None:
    settings = load_settings()
    configure_logging(settings.log_level, settings.log_json)

    if not settings.tls_enabled:
        structlog.get_logger(__name__).warning("tls.disabled", reason="FX_ALLOW_INSECURE is set")

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        ssl_certfile=str(settings.tls_cert) if settings.tls_cert else None,
        ssl_keyfile=str(settings.tls_key) if settings.tls_key else None,
        access_log=False,
        # Uploads are large and slow; do not cut them off mid-stream.
        timeout_keep_alive=75,
    )


if __name__ == "__main__":
    run()
