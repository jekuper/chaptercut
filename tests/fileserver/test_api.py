"""The HTTP contract, with auth and path safety as the load-bearing parts."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from filexchange.settings import Settings
from tests.fileserver.conftest import BASE, TOKEN, upload

# --- auth --------------------------------------------------------------------


def test_upload_requires_a_token(client: TestClient) -> None:
    assert client.post("/upload", content=b"x").status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        "",
        "Bearer",
        "Bearer wrong",
        TOKEN,  # the draft compared the raw header, so a bare token used to pass
        f"Basic {TOKEN}",
        f"Bearer {TOKEN}extra",
        f"Bearer {TOKEN[:-1]}",
    ],
)
def test_bad_credentials_are_rejected(client: TestClient, header: str) -> None:
    response = client.post("/upload", content=b"x", headers={"Authorization": header})
    assert response.status_code == 401


def test_a_rejected_upload_advertises_the_scheme(client: TestClient) -> None:
    assert client.post("/upload", content=b"x").headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("path", ["/admin/stats", "/admin/files"])
def test_admin_reads_require_a_token(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


def test_admin_deletes_require_a_token(client: TestClient) -> None:
    assert client.delete("/admin/files").status_code == 401
    assert client.delete("/admin/files/whatever").status_code == 401


def test_healthz_is_open(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"ok": True}


def test_the_api_is_not_self_documenting(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


# --- upload and download ------------------------------------------------------


def test_round_trip(client: TestClient, auth: dict[str, str]) -> None:
    body = b"audio-bytes" * 500
    result = upload(client, auth, "Some Album.zip", body)

    assert result["url"].startswith(f"{BASE}/d/")
    assert result["filename"] == "Some Album.zip"
    assert result["size"] == len(body)

    path = result["url"].removeprefix(BASE)
    fetched = client.get(path)
    assert fetched.status_code == 200
    assert fetched.content == body


def test_the_download_is_an_attachment_and_is_not_cached(
    client: TestClient, auth: dict[str, str]
) -> None:
    result = upload(client, auth, "track.mp3", b"data")
    fetched = client.get(result["url"].removeprefix(BASE))
    assert "attachment" in fetched.headers["content-disposition"]
    assert fetched.headers["cache-control"] == "private, no-store"
    assert fetched.headers["x-content-type-options"] == "nosniff"


def test_download_needs_no_credentials(client: TestClient, auth: dict[str, str]) -> None:
    # The link is the capability; that is the whole point of a 256-bit token.
    result = upload(client, auth, "track.mp3", b"data")
    assert client.get(result["url"].removeprefix(BASE)).status_code == 200


def test_tokens_are_unguessable_and_unique(client: TestClient, auth: dict[str, str]) -> None:
    tokens = {upload(client, auth, "a.mp3", b"x")["token"] for _ in range(5)}
    assert len(tokens) == 5
    assert all(len(token) >= 40 for token in tokens)


def test_an_empty_body_is_refused(client: TestClient, auth: dict[str, str]) -> None:
    assert client.post("/upload", content=b"", headers=auth).status_code == 400


def test_a_missing_filename_still_works(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post("/upload", content=b"data", headers=auth)
    assert response.status_code == 201
    assert response.json()["filename"] == "download"


# --- limits -------------------------------------------------------------------


def test_an_oversized_body_is_refused(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post("/upload", content=b"x" * 1_000_001, headers=auth)
    assert response.status_code == 413


def test_a_lying_content_length_is_still_caught(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    # Content-Length is a claim; the byte counter is the enforcement.
    def body() -> bytes:
        return b"x" * 1_000_001

    response = client.post(
        "/upload",
        content=body(),
        headers={**auth, "Content-Length": "10"},
    )
    assert response.status_code == 413


def test_a_refused_upload_leaves_nothing_behind(
    client: TestClient, auth: dict[str, str], settings: Settings
) -> None:
    client.post("/upload", content=b"x" * 1_000_001, headers=auth)
    assert list(settings.uploads_dir.iterdir()) == []


# --- path safety --------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd",
        "..\\..\\windows\\system32",
        "/etc/passwd",
        "....//evil.sh",
    ],
)
def test_a_traversing_filename_is_flattened(
    client: TestClient, auth: dict[str, str], settings: Settings, name: str
) -> None:
    result = upload(client, auth, name, b"data")
    stored = settings.uploads_dir / result["token"] / result["filename"]
    assert stored.is_file()
    assert stored.parent.parent == settings.uploads_dir
    assert ".." not in result["filename"]


@pytest.mark.parametrize(
    "path",
    [
        "/d/../../../etc/passwd",
        "/d/..%2F..%2Fetc/passwd",
        "/d/../etc/passwd",
        "/d/token/../../../etc/passwd",
        "/d/..../....",
    ],
)
def test_a_traversing_download_is_a_404(client: TestClient, path: str) -> None:
    # The draft joined these straight onto the uploads dir.
    assert client.get(path).status_code == 404


def test_an_unknown_token_is_a_404(client: TestClient) -> None:
    assert client.get("/d/" + "a" * 43 + "/file.mp3").status_code == 404


def test_a_malformed_token_is_a_404(client: TestClient) -> None:
    assert client.get("/d/short/file.mp3").status_code == 404


def test_a_wrong_filename_for_a_real_token_is_a_404(
    client: TestClient, auth: dict[str, str]
) -> None:
    result = upload(client, auth, "track.mp3", b"data")
    assert client.get(f"/d/{result['token']}/other.mp3").status_code == 404


# --- admin --------------------------------------------------------------------


def test_stats_counts_what_is_stored(client: TestClient, auth: dict[str, str]) -> None:
    upload(client, auth, "a.mp3", b"x" * 10)
    upload(client, auth, "b.mp3", b"y" * 20)

    stats = client.get("/admin/stats", headers=auth).json()
    assert stats["files"] == 2
    assert stats["bytes"] == 30
    assert stats["retention_hours"] == 24


def test_listing_reports_each_file(client: TestClient, auth: dict[str, str]) -> None:
    upload(client, auth, "a.mp3", b"x" * 10)
    upload(client, auth, "b.mp3", b"y" * 20)

    files = client.get("/admin/files", headers=auth).json()["files"]
    assert {item["filename"] for item in files} == {"a.mp3", "b.mp3"}
    assert all(item["expires_at"] for item in files)


def test_purging_one_leaves_the_rest(client: TestClient, auth: dict[str, str]) -> None:
    keep = upload(client, auth, "keep.mp3", b"x")
    drop = upload(client, auth, "drop.mp3", b"y")

    assert client.delete(f"/admin/files/{drop['token']}", headers=auth).json() == {"deleted": True}

    assert client.get(drop["url"].removeprefix(BASE)).status_code == 404
    assert client.get(keep["url"].removeprefix(BASE)).status_code == 200


def test_purging_something_absent_is_a_404(client: TestClient, auth: dict[str, str]) -> None:
    assert client.delete("/admin/files/" + "a" * 43, headers=auth).status_code == 404


def test_flushing_removes_everything(client: TestClient, auth: dict[str, str]) -> None:
    first = upload(client, auth, "a.mp3", b"x")
    upload(client, auth, "b.mp3", b"y")

    assert client.delete("/admin/files", headers=auth).json() == {"deleted": 2}

    assert client.get("/admin/stats", headers=auth).json()["files"] == 0
    assert client.get(first["url"].removeprefix(BASE)).status_code == 404


def test_flushing_an_empty_store_is_fine(client: TestClient, auth: dict[str, str]) -> None:
    assert client.delete("/admin/files", headers=auth).json() == {"deleted": 0}
