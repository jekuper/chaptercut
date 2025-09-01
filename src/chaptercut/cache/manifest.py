"""The manifest that makes a cache directory valid. No manifest, no cache."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"


class ManifestTrack(BaseModel):
    n: int
    file: str
    title: str
    start_ms: int
    end_ms: int


class Manifest(BaseModel):
    schema_version: int = Field(default=SCHEMA_VERSION, alias="schema")
    video_id: str
    url: str
    title: str
    uploader: str = ""
    upload_date: str = ""
    duration_ms: int = 0
    cover: str | None = "cover.jpg"
    tracks: list[ManifestTrack] = []
    downloaded_at: str

    model_config = {"populate_by_name": True}

    def dump_json(self) -> str:
        return json.dumps(self.model_dump(by_alias=True), ensure_ascii=False, indent=2)

    def write(self, directory: Path) -> None:
        (directory / MANIFEST_NAME).write_text(self.dump_json(), encoding="utf-8")


def read_manifest(directory: Path) -> Manifest | None:
    """Parse the manifest, or None if it is missing, unparseable, the wrong
    schema, or points at files that are not on disk."""
    path = directory / MANIFEST_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError:
        return None
    if manifest.schema_version != SCHEMA_VERSION or not manifest.tracks:
        return None
    for track in manifest.tracks:
        if not (directory / track.file).is_file():
            return None
    return manifest
