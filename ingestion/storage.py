"""Writers for raw API payloads: local disk or S3, same key layout for both.

Usage:
    from ingestion.storage import LocalWriter, S3Writer, raw_key

    key = raw_key("boxscore_traditional", "2026-03-15", pulled_at, game_id="0022500976")
    LocalWriter(Path("data/raw")).write(key, payload)   # data/raw/<key>
    S3Writer.from_settings(settings).write(key, payload)  # s3://<bucket>/raw/<key>

Key layout (matches the Phase 0 spike):
    <endpoint>/game_date=<YYYY-MM-DD>/<pulled_at>.json
    <endpoint>/game_date=<YYYY-MM-DD>/<game_id>/<pulled_at>.json

Writes never overwrite: an existing file or object raises AlreadyExistsError.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Protocol

import boto3
from botocore.exceptions import ClientError

from ingestion.config import Settings

log = logging.getLogger("storage")

# S3 error codes for a conditional put (IfNoneMatch="*") hitting an existing key.
# 412 when the object exists; 409 when a concurrent write to the same key wins the race.
ALREADY_EXISTS_CODES = frozenset({"PreconditionFailed", "ConditionalRequestConflict"})


class AlreadyExistsError(Exception):
    """Raised when a write would replace an existing file or object."""


class Writer(Protocol):
    def write(self, key: str, payload: dict) -> str:
        """Store payload at key and return where it went (a path or s3:// URI)."""
        ...


def raw_key(endpoint: str, game_date: str, pulled_at: str, game_id: str | None = None) -> str:
    """Relative key for one raw payload. Always forward slashes, on any OS."""
    parts = [endpoint, f"game_date={game_date}"]
    if game_id:
        parts.append(game_id)
    parts.append(f"{pulled_at}.json")
    return "/".join(parts)


def serialize(payload: dict) -> str:
    """The one JSON encoding both writers use."""
    return json.dumps(payload, indent=2)


class LocalWriter:
    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, key: str, payload: dict) -> str:
        body = serialize(payload).encode("utf-8")  # serialize first so a bad payload never leaves an empty file
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Binary mode keeps LF line endings on Windows, so local files match S3 byte for byte.
            with path.open("xb") as f:
                f.write(body)
        except FileExistsError as exc:
            raise AlreadyExistsError(f"{path} already exists") from exc
        log.debug("Wrote %s", path)
        return str(path)


class S3Writer:
    def __init__(self, bucket: str, prefix: str = "raw", client: Any = None) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = client if client is not None else boto3.client("s3")

    @classmethod
    def from_settings(cls, settings: Settings) -> S3Writer:
        return cls(settings.bucket, settings.raw_prefix, boto3.client("s3", region_name=settings.region))

    def full_key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def write(self, key: str, payload: dict) -> str:
        full_key = self.full_key(key)
        uri = f"s3://{self.bucket}/{full_key}"
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=full_key,
                Body=serialize(payload).encode("utf-8"),
                ContentType="application/json",
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ALREADY_EXISTS_CODES:
                raise AlreadyExistsError(f"{uri} already exists") from exc
            raise
        log.debug("Wrote %s", uri)
        return uri
