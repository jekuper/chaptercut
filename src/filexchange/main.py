"""Process entry point."""

from __future__ import annotations

import sys

import uvicorn

from filexchange.app import create_app
from filexchange.logging import configure_logging, get_logger
from filexchange.settings import load_settings

log = get_logger(__name__)


def run() -> None:
    settings = load_settings()
    configure_logging(settings.log_level, settings.log_json)

    if not settings.tls_enabled:
        log.warning("tls.disabled", reason="FX_ALLOW_INSECURE is set")

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


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
