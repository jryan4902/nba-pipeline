"""Shared helpers for calling the NBA stats API through nba_api.

Usage:
    from ingestion.nba_client import BOX_SCORE_ENDPOINTS, fetch_with_retry, utc_stamp
    payload = fetch_with_retry(ScoreboardV3, DEFAULT_TIMEOUT_SECONDS, game_date="2026-03-15")

Used by ingestion.ingest.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from nba_api.stats.endpoints import BoxScoreAdvancedV3, BoxScoreTraditionalV3

log = logging.getLogger("nba_client")

# stats.nba.com throttles aggressive clients; stay polite.
DEFAULT_SLEEP_SECONDS = 1.5
DEFAULT_TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 3

BOX_SCORE_ENDPOINTS: dict[str, Callable[..., Any]] = {
    "boxscore_traditional": BoxScoreTraditionalV3,
    "boxscore_advanced": BoxScoreAdvancedV3,
}


def utc_stamp() -> str:
    """Filesystem-safe UTC timestamp, sortable as a string."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def fetch_with_retry(endpoint_cls: Callable[..., Any], timeout: int, **params: Any) -> dict:
    """Call an nba_api endpoint and return its raw response dict, retrying with backoff."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return endpoint_cls(timeout=timeout, **params).get_dict()
        except Exception as exc:  # nba_api raises plain requests/JSON errors
            if attempt == MAX_ATTEMPTS:
                raise
            wait = 2**attempt
            log.warning("%s %s failed (%s), retrying in %ss", endpoint_cls.__name__, params, exc, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def game_ids_from_scoreboard(payload: dict) -> list[str]:
    """Extract game IDs from a ScoreboardV3 response."""
    games = payload.get("scoreboard", {}).get("games", [])
    return [g["gameId"] for g in games]
