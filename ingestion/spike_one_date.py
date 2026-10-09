"""Phase 0 spike: pull the schedule and every box score for one date, save raw JSON.

Usage:
    python -m ingestion.spike_one_date --date 2026-03-15

Writes to data/raw/ (gitignored):
    data/raw/scoreboard/game_date=2026-03-15/<pulled_at>.json
    data/raw/boxscore_traditional/game_date=2026-03-15/<game_id>/<pulled_at>.json
    data/raw/boxscore_advanced/game_date=2026-03-15/<game_id>/<pulled_at>.json
    data/raw/_runs/game_date=2026-03-15/<pulled_at>.json   (run summary)

The layout mirrors the S3 keys planned for Phase 1, so moving to S3 is a
change of writer, not of structure. Files are never overwritten: each pull
gets its own pulled_at timestamp.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from nba_api.stats.endpoints import (
    BoxScoreAdvancedV3,
    BoxScoreTraditionalV3,
    ScoreboardV3,
)

log = logging.getLogger("spike")

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


def write_json(root: Path, endpoint: str, game_date: str, pulled_at: str, payload: dict, game_id: str | None = None) -> Path:
    parts = [root, endpoint, f"game_date={game_date}"]
    if game_id:
        parts.append(game_id)
    out_dir = Path(*parts)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{pulled_at}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def run(game_date: str, out_root: Path, sleep_seconds: float, timeout: int) -> dict:
    started = time.monotonic()
    pulled_at = utc_stamp()
    summary: dict[str, Any] = {
        "game_date": game_date,
        "pulled_at": pulled_at,
        "games_found": 0,
        "files_written": [],
        "failures": [],
    }

    log.info("Fetching scoreboard for %s", game_date)
    scoreboard = fetch_with_retry(ScoreboardV3, timeout, game_date=game_date)
    summary["files_written"].append(str(write_json(out_root, "scoreboard", game_date, pulled_at, scoreboard)))

    game_ids = game_ids_from_scoreboard(scoreboard)
    summary["games_found"] = len(game_ids)
    log.info("Found %d games: %s", len(game_ids), ", ".join(game_ids) or "none")

    for game_id in game_ids:
        for endpoint, cls in BOX_SCORE_ENDPOINTS.items():
            time.sleep(sleep_seconds)
            try:
                payload = fetch_with_retry(cls, timeout, game_id=game_id)
            except Exception as exc:
                log.error("%s %s failed: %s", endpoint, game_id, exc)
                summary["failures"].append({"endpoint": endpoint, "game_id": game_id, "error": str(exc)})
                continue
            path = write_json(out_root, endpoint, game_date, pulled_at, payload, game_id=game_id)
            summary["files_written"].append(str(path))
            log.info("Saved %s", path)

    summary["elapsed_seconds"] = round(time.monotonic() - started, 1)
    write_json(out_root, "_runs", game_date, pulled_at, summary)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", required=True, type=date.fromisoformat, help="Game date, YYYY-MM-DD")
    parser.add_argument("--out", default="data/raw", type=Path, help="Output root (default: data/raw)")
    parser.add_argument("--sleep", default=DEFAULT_SLEEP_SECONDS, type=float, help="Seconds between box score calls")
    parser.add_argument("--timeout", default=DEFAULT_TIMEOUT_SECONDS, type=int, help="Per-request timeout in seconds")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    summary = run(args.date.isoformat(), args.out, args.sleep, args.timeout)
    expected = summary["games_found"] * len(BOX_SCORE_ENDPOINTS)
    pulled = expected - len(summary["failures"])
    log.info(
        "Done: %d games, %d/%d box scores pulled in %ss",
        summary["games_found"], pulled, expected, summary["elapsed_seconds"],
    )
    return 1 if summary["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
