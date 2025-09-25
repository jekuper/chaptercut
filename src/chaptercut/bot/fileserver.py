"""Client for the filexchange server.

The server lives on another machine with a certificate no public CA has signed,
so the bot verifies against a pinned PEM instead. There is deliberately no
"skip verification" option: without verification the TLS would be decoration,
and the upload token would be handed to whoever answered the connection.
"""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp

from chaptercut.logging import get_logger
from chaptercut.util.jsonish import as_dict, as_int, as_str, dict_list
from chaptercut.util.timefmt import parse_iso

log = get_logger(__name__)

READ_CHUNK = 1024 * 1024
UPLOAD_TIMEOUT = 3600.0
ADMIN_TIMEOUT = 30.0

ProgressCallback = Callable[[int, int], None]


class FileServerError(RuntimeError):
    """The server could not be reached, or refused the request."""


@dataclass(frozen=True, slots=True)
class RemoteFile:
    url: str
    token: str
    filename: str
    size: int
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class RemoteStats:
    files: int
    bytes: int
    retention_hours: int


class FileServerClient:
    def __init__(self, base_url: str, token: str, ca_file: Path | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.ca_file = ca_file

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _ssl_context(self) -> ssl.SSLContext | None:
        """Trust exactly one PEM when the server uses a private certificate.

        Works unchanged for a self-signed leaf and for a private CA: the PEM is
        just whichever of the two the operator generated.
        """
        if not self.base_url.startswith("https://"):
            return None
        if self.ca_file is None:
            # No pinned PEM: fall back to the system trust store, which is
            # correct once the certificate is signed by something installed.
            return ssl.create_default_context()
        if not self.ca_file.is_file():
            raise FileServerError(f"trust file {self.ca_file} is missing")
        return ssl.create_default_context(cafile=str(self.ca_file))

    def _session(self, timeout: float) -> aiohttp.ClientSession:
        connector = aiohttp.TCPConnector(ssl=self._ssl_context() or True)
        return aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
            connector=connector,
            headers=self.headers,
        )

    # --- upload -----------------------------------------------------------

    async def upload(
        self,
        path: Path,
        filename: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> RemoteFile:
        """Stream `path` to the server and return the download link.

        Streamed from disk a chunk at a time: these are the files that were too
        big for Telegram, so loading one into memory is not an option.
        """
        if not path.is_file():
            raise FileServerError(f"{path.name} does not exist")
        total = path.stat().st_size
        name = filename or path.name

        try:
            async with self._session(UPLOAD_TIMEOUT) as session:
                response = await session.post(
                    f"{self.base_url}/upload",
                    data=_file_chunks(path, total, on_progress),
                    headers={
                        "X-Filename": name,
                        "Content-Length": str(total),
                        "Content-Type": "application/octet-stream",
                    },
                )
                async with response:
                    payload = await _decode(response)
        except aiohttp.ClientError as exc:
            raise FileServerError(_reason(exc)) from exc
        except TimeoutError as exc:
            raise FileServerError("the file server timed out") from exc

        remote = _remote_file(payload)
        log.info("fileserver.uploaded", filename=remote.filename, bytes=remote.size)
        return remote

    # --- admin ------------------------------------------------------------

    async def stats(self) -> RemoteStats:
        payload = await self._request("GET", "/admin/stats")
        return RemoteStats(
            files=as_int(payload.get("files")) or 0,
            bytes=as_int(payload.get("bytes")) or 0,
            retention_hours=as_int(payload.get("retention_hours")) or 0,
        )

    async def list_files(self) -> list[RemoteFile]:
        payload = await self._request("GET", "/admin/files")
        return [_remote_file(item) for item in dict_list(payload.get("files"))]

    async def purge_all(self) -> int:
        payload = await self._request("DELETE", "/admin/files")
        return as_int(payload.get("deleted")) or 0

    async def purge(self, token: str) -> bool:
        try:
            await self._request("DELETE", f"/admin/files/{token}")
        except FileServerError as exc:
            if "404" in str(exc):
                return False
            raise
        return True

    async def _request(self, method: str, path: str) -> dict[str, Any]:
        try:
            async with self._session(ADMIN_TIMEOUT) as session:
                async with session.request(method, f"{self.base_url}{path}") as response:
                    return await _decode(response)
        except aiohttp.ClientError as exc:
            raise FileServerError(_reason(exc)) from exc
        except TimeoutError as exc:
            raise FileServerError("the file server timed out") from exc


async def _file_chunks(
    path: Path, total: int, on_progress: ProgressCallback | None
) -> AsyncIterator[bytes]:
    sent = 0
    with path.open("rb") as handle:
        while chunk := handle.read(READ_CHUNK):
            sent += len(chunk)
            if on_progress is not None:
                on_progress(sent, total)
            yield chunk


async def _decode(response: aiohttp.ClientResponse) -> dict[str, Any]:
    if response.status == 401:
        raise FileServerError("the file server rejected the upload token")
    if response.status == 413:
        raise FileServerError("the file server refused the file as too large")
    if response.status >= 400:
        raise FileServerError(f"the file server returned {response.status}")
    try:
        return as_dict(await response.json())
    except (aiohttp.ContentTypeError, ValueError) as exc:
        raise FileServerError("the file server returned an unreadable response") from exc


def _remote_file(payload: dict[str, Any]) -> RemoteFile:
    raw_expiry = as_str(payload.get("expires_at"))
    expires_at: datetime | None = None
    if raw_expiry:
        try:
            expires_at = parse_iso(raw_expiry)
        except ValueError:
            expires_at = None
    return RemoteFile(
        url=as_str(payload.get("url")),
        token=as_str(payload.get("token")),
        filename=as_str(payload.get("filename")),
        size=as_int(payload.get("size")) or 0,
        expires_at=expires_at,
    )


def _reason(exc: aiohttp.ClientError) -> str:
    """A short line for the user. Never leak the URL or the token."""
    if isinstance(exc, aiohttp.ClientSSLError):
        return "the file server's certificate was not trusted"
    if isinstance(exc, aiohttp.ClientConnectorError):
        return "could not reach the file server"
    return "the file server request failed"
