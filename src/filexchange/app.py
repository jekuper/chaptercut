# Route handlers are registered by decorator, so strict mode sees every one of
# them as dead code. The registration is the use.
# pyright: reportUnusedFunction=false

"""The HTTP surface: authenticated upload and admin, capability-URL download.

Uploads arrive as a raw streamed body rather than multipart. Multipart would
have Starlette spool the whole thing to a temp file before we ever see it, so a
2 GB upload would be written to disk twice and parsed for no benefit; the only
thing the form envelope carries is a filename, which fits in a header.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import anyio
import structlog
from anyio import to_thread
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse

from filexchange.settings import Settings
from filexchange.storage import Storage, safe_name

log = structlog.get_logger(__name__)

FILENAME_HEADER = "X-Filename"


def _authorize(settings: Settings, authorization: str | None) -> None:
    """Bearer check in constant time, so the token cannot be probed byte by byte."""
    expected = settings.upload_token.get_secret_value()
    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(presented, expected):
        log.warning("auth.rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def _sweeper(storage: Storage, interval: float) -> None:
    """Delete expired entries forever.

    Every iteration is guarded, because an unhandled exception here would end
    the task and silently stop retention altogether.
    """
    while True:
        try:
            removed = await to_thread.run_sync(storage.sweep)
            if removed:
                log.info("sweep.removed", count=removed)
        except Exception as exc:  # noqa: BLE001 - the loop must outlive any single failure
            log.warning("sweep.failed", error=type(exc).__name__)
        await asyncio.sleep(interval)


def create_app(settings: Settings) -> FastAPI:
    storage = Storage(settings.uploads_dir, timedelta(hours=settings.retention_hours))
    settings.ensure_dirs()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        task = asyncio.create_task(_sweeper(storage, settings.sweep_interval_seconds))
        log.info(
            "startup",
            uploads=str(settings.uploads_dir),
            tls=settings.tls_enabled,
            retention_hours=settings.retention_hours,
        )
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(
        title="filexchange",
        version="1.0.0",
        lifespan=lifespan,
        # Nothing here is worth advertising to an unauthenticated caller.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def authorized(authorization: Annotated[str | None, Header()] = None) -> None:
        _authorize(settings, authorization)

    admin = [Depends(authorized)]

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    # --- upload -----------------------------------------------------------

    @app.post("/upload", dependencies=admin)
    async def upload(request: Request) -> JSONResponse:
        filename = safe_name(request.headers.get(FILENAME_HEADER, ""))

        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit():
            if int(declared) > settings.max_upload_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="file too large",
                )

        token, destination = storage.begin(filename)
        written = 0
        try:
            async with await anyio.open_file(destination, "wb") as handle:
                async for chunk in request.stream():
                    written += len(chunk)
                    # Content-Length is a claim; this is the enforcement.
                    if written > settings.max_upload_bytes:
                        raise HTTPException(
                            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            detail="file too large",
                        )
                    await handle.write(chunk)
            if written == 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty body")
            stored = await to_thread.run_sync(storage.commit, token)
        except BaseException:
            # Covers client disconnects as well as errors: never leave a
            # half-written file where a link could point at it.
            storage.abort(token)
            raise

        log.info("upload.stored", token=token, filename=stored.filename, bytes=stored.size)
        return JSONResponse(
            {
                "url": settings.link_for(stored.token, stored.filename),
                "token": stored.token,
                "filename": stored.filename,
                "size": stored.size,
                "expires_at": stored.expires_at.isoformat(),
            },
            status_code=status.HTTP_201_CREATED,
        )

    # --- admin ------------------------------------------------------------

    @app.get("/admin/stats", dependencies=admin)
    async def stats() -> dict[str, Any]:
        return {
            "files": storage.count(),
            "bytes": storage.usage_bytes(),
            "retention_hours": settings.retention_hours,
            "max_upload_bytes": settings.max_upload_bytes,
        }

    @app.get("/admin/files", dependencies=admin)
    async def list_files() -> dict[str, Any]:
        entries = await to_thread.run_sync(storage.list_entries)
        return {
            "files": [
                {
                    "token": entry.token,
                    "filename": entry.filename,
                    "size": entry.size,
                    "expires_at": entry.expires_at.isoformat(),
                }
                for entry in entries
            ]
        }

    @app.delete("/admin/files", dependencies=admin)
    async def purge_all() -> dict[str, int]:
        removed = await to_thread.run_sync(storage.purge_all)
        log.info("admin.purged_all", count=removed)
        return {"deleted": removed}

    @app.delete("/admin/files/{token}", dependencies=admin)
    async def purge_one(token: str) -> dict[str, bool]:
        if not storage.delete(token):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
        log.info("admin.purged", token=token)
        return {"deleted": True}

    # --- download ---------------------------------------------------------

    @app.get("/d/{token}/{filename}")
    async def download(token: str, filename: str) -> FileResponse:
        path = storage.resolve(token, filename)
        if path is None or _is_expired(path, storage.retention):
            # Same answer for malformed, missing and expired: a probe learns
            # nothing about which tokens exist.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
        return FileResponse(
            path,
            filename=filename,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return app


def _is_expired(path: Path, retention: timedelta) -> bool:
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return datetime.now(UTC) - modified > retention
