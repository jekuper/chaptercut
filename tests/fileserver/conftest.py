from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from filexchange.app import create_app
from filexchange.settings import Settings

TOKEN = "test-upload-token-that-is-long-enough-000"
BASE = "https://files.invalid:8443"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        upload_token=TOKEN,  # pyright: ignore[reportArgumentType]
        public_url=BASE,
        data_dir=tmp_path,
        allow_insecure=True,
        max_upload_bytes=1_000_000,
        retention_hours=24,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def upload(client: TestClient, auth: dict[str, str], name: str, body: bytes) -> dict[str, str]:
    response = client.post("/upload", content=body, headers={**auth, "X-Filename": name})
    assert response.status_code == 201, response.text
    return response.json()
