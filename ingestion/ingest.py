"""Pull scoreboards and final box scores for a date range and save the raw JSON.

Usage:
    python -m ingestion.ingest --date 2026-10-21
    python -m ingestion.ingest --start 2026-10-20 --end 2026-10-22
    python -m ingestion.ingest --nightly            # last 3 Eastern game dates
    python -m ingestion.ingest --nightly --days 5
    python -m ingestion.ingest --date 2026-10-21 --local            # data/raw instead of S3
    python -m ingestion.ingest --date 2026-10-21 --local some/dir

Keys (see ingestion.storage.raw_key), all sharing one pulled_at per run:
    scoreboard/game_date=<date>/<pulled_at>.json
    boxscore_traditional/game_date=<date>/<game_id>/<pulled_at>.json
    boxscore_advanced/game_date=<date>/<game_id>/<pulled_at>.json
    _runs/game_date=<date>/<pulled_at>.json   (per-date summary)

Box scores are pulled only for final games (gameStatus 3); other games are
recorded as skipped and picked up by a later nightly re-pull. Exits 1 if any
API call or write failed, else 0.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from nba_api.stats.endpoints import ScoreboardV3

from ingestion.config import ConfigError, load_settings
from ingestion.nba_client import (
    BOX_SCORE_ENDPOINTS,
    DEFAULT_SLEEP_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    fetch_with_retry,
    utc_stamp,
)
from ingestion.storage import AlreadyExistsError, LocalWriter, S3Writer, Writer, raw_key

log = logging.getLogger("ingest")

# NBA game dates are Eastern, so "yesterday" must be too.
EASTERN = ZoneInfo("America/New_York")
DEFAULT_NIGHTLY_DAYS = 3
DEFAULT_LOCAL_ROOT = Path("data/raw")
GAME_STATUS_FINAL = 3


def eastern_today(now: datetime | None = None) -> date:
    """Today's date in America/New_York."""
    return (now or datetime.now(EASTERN)).astimezone(EASTERN).date()


def date_range(start: date, end: date) -> list[date]:
    """Every date from start to end, inclusive."""
    if end < start:
        raise ValueError(f"--end {end} is before --start {start}")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def nightly_dates(today: date, days: int) -> list[date]:
    """Yesterday and the days-1 dates before it, oldest first."""
    return [today - timedelta(days=i) for i in range(days, 0, -1)]


def resolve_dates(args: argparse.Namespace, today: date) -> list[date]:
    """Turn the parsed date options into the list of game dates to pull, oldest first."""
    if args.date:
        return [args.date]
    if args.start:
        return date_range(args.start, args.end)
    return nightly_dates(today, args.days or DEFAULT_NIGHTLY_DAYS)


def ingest_date(game_date: str, pulled_at: str, writer: Writer, sleep_seconds: float, timeout: int) -> dict:
    """Pull and save one date's scoreboard and final box scores; return its run summary."""
    started = time.monotonic()
    summary: dict[str, Any] = {
        "game_date": game_date,
        "pulled_at": pulled_at,
        "games_found": 0,
        "games_skipped": [],
        "box_scores_written": 0,
        "files_written": [],
        "failures": [],
    }

    def save(key: str, payload: dict, endpoint: str, game_id: str | None = None) -> bool:
        try:
            location = writer.write(key, payload)
        except AlreadyExistsError as exc:
            log.error("%s", exc)
            summary["failures"].append({"endpoint": endpoint, "game_id": game_id, "error": str(exc)})
            return False
        summary["files_written"].append(location)
        log.info("Saved %s", location)
        return True

    log.info("Fetching scoreboard for %s", game_date)
    try:
        scoreboard = fetch_with_retry(ScoreboardV3, timeout, game_date=game_date)
    except Exception as exc:
        log.error("scoreboard %s failed: %s", game_date, exc)
        summary["failures"].append({"endpoint": "scoreboard", "game_id": None, "error": str(exc)})
        scoreboard = None

    if scoreboard is not None:
        save(raw_key("scoreboard", game_date, pulled_at), scoreboard, "scoreboard")
        games = scoreboard.get("scoreboard", {}).get("games", [])
        summary["games_found"] = len(games)

        for game in games:
            game_id, status = game["gameId"], game.get("gameStatus")
            if status != GAME_STATUS_FINAL:
                log.info("Skipping %s: gameStatus %s (%s)", game_id, status, game.get("gameStatusText", "?"))
                summary["games_skipped"].append({"game_id": game_id, "game_status": status})
                continue
            for endpoint, cls in BOX_SCORE_ENDPOINTS.items():
                time.sleep(sleep_seconds)
                try:
                    payload = fetch_with_retry(cls, timeout, game_id=game_id)
                except Exception as exc:
                    log.error("%s %s failed: %s", endpoint, game_id, exc)
                    summary["failures"].append({"endpoint": endpoint, "game_id": game_id, "error": str(exc)})
                    continue
                if save(raw_key(endpoint, game_date, pulled_at, game_id), payload, endpoint, game_id):
                    summary["box_scores_written"] += 1

    summary["elapsed_seconds"] = round(time.monotonic() - started, 1)
    run_key = raw_key("_runs", game_date, pulled_at)
    try:
        writer.write(run_key, summary)
    except AlreadyExistsError as exc:
        log.error("%s", exc)
        summary["failures"].append({"endpoint": "_runs", "game_id": None, "error": str(exc)})
    return summary


def run(dates: list[date], writer: Writer, sleep_seconds: float, timeout: int) -> list[dict]:
    """Ingest each date oldest first, all under one pulled_at."""
    pulled_at = utc_stamp()
    log.info("Run %s: %d date(s), %s to %s", pulled_at, len(dates), dates[0], dates[-1])
    summaries = []
    for i, game_date in enumerate(dates):
        if i:
            time.sleep(sleep_seconds)
        summaries.append(ingest_date(game_date.isoformat(), pulled_at, writer, sleep_seconds, timeout))
    return summaries


def report(summaries: list[dict]) -> int:
    """Log one line per date plus a total; return the exit code."""
    for s in summaries:
        log.info(
            "%s: %d games, %d box scores saved, %d skipped, %d failures",
            s["game_date"], s["games_found"], s["box_scores_written"], len(s["games_skipped"]), len(s["failures"]),
        )
    failures = sum(len(s["failures"]) for s in summaries)
    log.info(
        "Total: %d dates, %d games, %d box scores saved, %d skipped, %d failures",
        len(summaries),
        sum(s["games_found"] for s in summaries),
        sum(s["box_scores_written"] for s in summaries),
        sum(len(s["games_skipped"]) for s in summaries),
        failures,
    )
    return 1 if failures else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--date", type=date.fromisoformat, help="One game date, YYYY-MM-DD")
    mode.add_argument("--start", type=date.fromisoformat, help="First date of an inclusive range (needs --end)")
    mode.add_argument("--nightly", action="store_true", help="The most recent finished Eastern game dates")
    parser.add_argument("--end", type=date.fromisoformat, help="Last date of the --start range, inclusive")
    parser.add_argument("--days", type=int, help=f"With --nightly: how many dates (default {DEFAULT_NIGHTLY_DAYS})")
    parser.add_argument(
        "--local", nargs="?", const=DEFAULT_LOCAL_ROOT, type=Path, metavar="PATH",
        help=f"Write to disk (default {DEFAULT_LOCAL_ROOT}) instead of S3",
    )
    parser.add_argument("--sleep", default=DEFAULT_SLEEP_SECONDS, type=float, help="Seconds between API calls")
    parser.add_argument("--timeout", default=DEFAULT_TIMEOUT_SECONDS, type=int, help="Per-request timeout in seconds")
    args = parser.parse_args(argv)

    if args.start and not args.end:
        parser.error("--start needs --end")
    if args.end and not args.start:
        parser.error("--end only goes with --start")
    if args.start and args.end < args.start:
        parser.error(f"--end {args.end} is before --start {args.start}")
    if args.days is not None and not args.nightly:
        parser.error("--days only goes with --nightly")
    if args.days is not None and args.days < 1:
        parser.error("--days must be at least 1")
    return args


def make_writer(local_root: Path | None) -> Writer:
    if local_root is not None:
        return LocalWriter(local_root)
    return S3Writer.from_settings(load_settings())


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    dates = resolve_dates(args, eastern_today())
    try:
        writer = make_writer(args.local)
    except ConfigError as exc:
        log.error("Config error: %s", exc)
        return 1
    return report(run(dates, writer, args.sleep, args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())
