"""The bot's file server client, driven against a real running server.

A live uvicorn on a random port, so the streaming upload path is exercised for
real rather than mocked. TLS is off here; the certificate handling is checked
separately below, since generating a real certificate per test is not worth it.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from chaptercut.bot.fileserver import FileServerClient, FileServerError
from filexchange.app import create_app
from filexchange.settings import Settings as ServerSettings

TOKEN = "integration-token-long-enough-for-checks"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    settings = ServerSettings(
        upload_token=TOKEN,  # pyright: ignore[reportArgumentType]
        public_url=base,
        data_dir=tmp_path / "server",
        allow_insecure=True,
        max_upload_bytes=5_000_000,
        retention_hours=24,
        # Long enough that the sweeper never fires during a test.
        sweep_interval_seconds=3600,
    )
    config = uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="error")
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    for _ in range(200):
        if instance.started:
            break
        threading.Event().wait(0.05)
    else:  # pragma: no cover - the server failed to come up
        raise RuntimeError("test server did not start")
    yield base
    instance.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def client(server: str) -> FileServerClient:
    return FileServerClient(base_url=server, token=TOKEN)


@pytest.fixture
def big_file(tmp_path: Path) -> Path:
    # Several read chunks' worth, so the streaming path actually loops.
    path = tmp_path / "Some Album.zip"
    path.write_bytes(b"chaptercut" * 300_000)
    return path


# --- upload -------------------------------------------------------------------


async def test_upload_returns_a_working_link(client: FileServerClient, big_file: Path) -> None:
    remote = await client.upload(big_file)

    assert remote.url.startswith(client.base_url)
    assert remote.filename == "Some Album.zip"
    assert remote.size == big_file.stat().st_size
    assert remote.expires_at is not None


async def test_the_uploaded_bytes_match(client: FileServerClient, big_file: Path) -> None:
    import aiohttp

    remote = await client.upload(big_file)
    async with aiohttp.ClientSession() as session, session.get(remote.url) as response:
        assert response.status == 200
        assert await response.read() == big_file.read_bytes()


async def test_progress_is_reported_and_reaches_the_total(
    client: FileServerClient, big_file: Path
) -> None:
    seen: list[tuple[int, int]] = []
    await client.upload(big_file, on_progress=lambda sent, total: seen.append((sent, total)))

    assert len(seen) > 1, "a multi-chunk file should report more than once"
    assert seen[-1][0] == seen[-1][1] == big_file.stat().st_size
    assert [sent for sent, _ in seen] == sorted(sent for sent, _ in seen)


async def test_a_bad_token_is_reported_clearly(server: str, big_file: Path) -> None:
    wrong = FileServerClient(base_url=server, token="a" * 40)
    with pytest.raises(FileServerError, match="rejected the upload token"):
        await wrong.upload(big_file)


async def test_an_oversized_file_is_reported_clearly(
    client: FileServerClient, tmp_path: Path
) -> None:
    huge = tmp_path / "huge.bin"
    huge.write_bytes(b"x" * 5_000_001)
    with pytest.raises(FileServerError, match="too large"):
        await client.upload(huge)


async def test_a_missing_file_is_refused_before_any_request(
    client: FileServerClient, tmp_path: Path
) -> None:
    with pytest.raises(FileServerError, match="does not exist"):
        await client.upload(tmp_path / "gone.mp3")


async def test_an_unreachable_server_is_reported_clearly(tmp_path: Path) -> None:
    path = tmp_path / "x.mp3"
    path.write_bytes(b"data")
    dead = FileServerClient(base_url=f"http://127.0.0.1:{_free_port()}", token=TOKEN)
    with pytest.raises(FileServerError, match="could not reach"):
        await dead.upload(path)


# --- admin --------------------------------------------------------------------


async def test_stats_and_listing(client: FileServerClient, big_file: Path) -> None:
    await client.upload(big_file)

    stats = await client.stats()
    assert stats.files == 1
    assert stats.bytes == big_file.stat().st_size
    assert stats.retention_hours == 24

    listed = await client.list_files()
    assert [item.filename for item in listed] == ["Some Album.zip"]


async def test_purging_one_file(client: FileServerClient, big_file: Path) -> None:
    remote = await client.upload(big_file)

    assert await client.purge(remote.token) is True
    assert (await client.stats()).files == 0


async def test_purging_something_absent_returns_false(client: FileServerClient) -> None:
    assert await client.purge("a" * 43) is False


async def test_flushing_everything(client: FileServerClient, big_file: Path) -> None:
    await client.upload(big_file)
    await client.upload(big_file)

    assert await client.purge_all() == 2
    assert (await client.stats()).files == 0
    assert await client.list_files() == []


async def test_admin_calls_need_the_right_token(server: str) -> None:
    wrong = FileServerClient(base_url=server, token="a" * 40)
    with pytest.raises(FileServerError, match="rejected the upload token"):
        await wrong.stats()


# --- certificate handling -----------------------------------------------------


def test_plain_http_uses_no_tls_context() -> None:
    client = FileServerClient(base_url="http://example.invalid", token=TOKEN)
    assert client._ssl_context() is None


def test_https_without_a_pinned_pem_uses_the_system_store() -> None:
    client = FileServerClient(base_url="https://example.invalid", token=TOKEN)
    context = client._ssl_context()
    assert context is not None
    # Still verifying: there is deliberately no way to turn this off.
    assert context.verify_mode.name == "CERT_REQUIRED"
    assert context.check_hostname is True


def test_a_pinned_pem_is_loaded(tmp_path: Path) -> None:
    # Any real PEM will do; the point is that the file is what gets trusted.
    import ssl

    pem = tmp_path / "ca.crt"
    pem.write_text(_SELF_SIGNED_PEM, encoding="utf-8")

    client = FileServerClient(base_url="https://example.invalid", token=TOKEN, ca_file=pem)
    context = client._ssl_context()

    assert context is not None
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert any(cert["subject"] for cert in context.get_ca_certs())


def test_a_missing_pinned_pem_fails_loudly(tmp_path: Path) -> None:
    # Better to refuse than to quietly fall back to trusting anything.
    client = FileServerClient(
        base_url="https://example.invalid", token=TOKEN, ca_file=tmp_path / "gone.crt"
    )
    with pytest.raises(FileServerError, match="trust file"):
        client._ssl_context()


# A throwaway self-signed certificate, generated purely as test data.
_SELF_SIGNED_PEM = """-----BEGIN CERTIFICATE-----
MIIDJzCCAg+gAwIBAgIUP2rxVgzbHubBpw9cSVAlrzWemFQwDQYJKoZIhvcNAQEL
BQAwIjEgMB4GA1UEAwwXY2hhcHRlcmN1dCB0ZXN0IGZpeHR1cmUwIBcNMjYwODIz
MjE1NjA3WhgPMjEyNjA3MzAyMTU2MDdaMCIxIDAeBgNVBAMMF2NoYXB0ZXJjdXQg
dGVzdCBmaXh0dXJlMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0pVr
qwajqdNELhPA9KG5Jb9p6hL6Ll3pIqEOiXKskIwzTNdBuU81xpySHZZqi1lk4+d+
XUxQ6FhFI905aH2dSs98SyXjvbWEkaAo6K90Gfrafz+wFVGZqUqJfDBDYkavB6Xo
OFv4banJOh3j/ibm1Uc6XhIT3VMHleo/pEwilU0UImUROH1qA0Q43NAdfaPI8o2u
HSQX2pu+l2c8WMP0COqYEF46Ti9PNA+xDQIXaj2HcvpibQQeFESOgayebwM2nxaQ
Qmn6vGpVQkpBgffFkaWbrSkKl9hbHydYAtXlUxbHKBHyfPEej1nR9duKfDczBvIk
yhKA0sFyKjy9K0eIcwIDAQABo1MwUTAdBgNVHQ4EFgQU9XFzI0VD/eceH/P7wS/V
6rflIxEwHwYDVR0jBBgwFoAU9XFzI0VD/eceH/P7wS/V6rflIxEwDwYDVR0TAQH/
BAUwAwEB/zANBgkqhkiG9w0BAQsFAAOCAQEATkn1F8F7oNEpfhFww1pde2yYu4pS
xpOzZrg+y/hLP9Mamtzv+hipH9dZEouAsAU5eeTdFVGOboDXqjrtyOkCh6oKARnu
mCZkXjQSxh0ecBW48zOwFVaDt1Kbj5fcUcniKkzDlyrJb5a3uAjLuHTtsLogPVjZ
Peto8snEQVPzeTHWWMITxxHbRcNVDt5KMt0D5W9CSQbbSxdI6GPn5oYLQD0VJBOG
eO9fqCqI8ZSQyFVosjBkrAYHfqEE0Sb5mF1evJtI0ABL+tqLSA2W+fVhjJBDtmOA
LwfSt0AK487QBZqb6I57rZNiURoEI2I1DTR+JuKNdDX/CPgS8wRfVlia/Q==
-----END CERTIFICATE-----
"""
