"""Pipeline settings loaded from the environment (and .env, if present).

Usage:
    from ingestion.config import load_settings
    settings = load_settings()

Required variables:
    S3_BUCKET            bucket for raw payloads
    AWS_DEFAULT_REGION   region of that bucket

AWS credentials are deliberately not read here: boto3 finds
AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in the environment on its own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

REQUIRED_VARS = ("S3_BUCKET", "AWS_DEFAULT_REGION")


class ConfigError(RuntimeError):
    """Raised when a required setting is missing."""


@dataclass(frozen=True)
class Settings:
    bucket: str
    region: str
    raw_prefix: str = "raw"


def load_settings() -> Settings:
    """Load .env into os.environ (without overriding existing vars) and build Settings."""
    load_dotenv()
    values = {name: os.environ.get(name, "").strip() for name in REQUIRED_VARS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigError(f"Missing required environment variable(s): {', '.join(missing)}. Set them in .env (see .env.example).")
    return Settings(bucket=values["S3_BUCKET"], region=values["AWS_DEFAULT_REGION"])
