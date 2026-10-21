"""Check that the configured S3 bucket is writable and readable.

Usage:
    python -m ingestion.s3_check

Writes a small text object to _checks/s3_check_<UTC timestamp>.txt, reads it
back and compares the body. The put uses IfNoneMatch="*", so S3 refuses to
overwrite an existing key. The IAM user has no delete permission, so check
objects are left in place.

Exits 0 on success, 1 on any failure (with a short readable error).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from ingestion.config import ConfigError, load_settings

log = logging.getLogger("s3_check")

CHECK_PREFIX = "_checks"


def utc_stamp() -> str:
    """Key-safe UTC timestamp, sortable as a string."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def run_check(bucket: str, region: str) -> str:
    """Put a check object, read it back, and return its key. Raises on any failure."""
    s3 = boto3.client("s3", region_name=region)
    stamp = utc_stamp()
    key = f"{CHECK_PREFIX}/s3_check_{stamp}.txt"
    body = f"nba-pipeline s3 check {stamp}\n".encode()

    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="text/plain", IfNoneMatch="*")
    log.info("Wrote s3://%s/%s", bucket, key)

    read_back = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if read_back != body:
        raise RuntimeError(f"Body mismatch for {key}: wrote {len(body)} bytes, read {len(read_back)}")
    log.info("Read back s3://%s/%s, body matches", bucket, key)
    return key


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = load_settings()
        key = run_check(settings.bucket, settings.region)
    except ConfigError as exc:
        log.error("Config error: %s", exc)
        return 1
    except NoCredentialsError:
        log.error("No AWS credentials found: set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env")
        return 1
    except ClientError as exc:
        err = exc.response.get("Error", {})
        log.error("S3 %s failed: %s: %s", exc.operation_name, err.get("Code"), err.get("Message"))
        return 1
    except (BotoCoreError, RuntimeError) as exc:
        log.error("S3 check failed: %s", exc)
        return 1

    print(f"OK bucket={settings.bucket} key={key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
