"""Inventory of raw files per game date, to check a backfill is complete.

Usage:
    python -m ingestion.inventory                                  # everything in S3
    python -m ingestion.inventory --start 2026-10-20 --end 2026-10-31
    python -m ingestion.inventory --local                          # data/raw instead of S3
    python -m ingestion.inventory --local some/dir --start 2026-10-20

Reads the key layout written by ingestion.ingest (see ingestion.storage.raw_key):
    scoreboard/game_date=<date>/<pulled_at>.json
    boxscore_traditional/game_date=<date>/<game_id>/<pulled_at>.json
    boxscore_advanced/game_date=<date>/<game_id>/<pulled_at>.json
    _runs/game_date=<date>/<pulled_at>.json

A date is OK when it has a traditional and an advanced box score for every
final game in its latest _runs summary and that run had no failures;
otherwise it is a GAP. Exits 1 if any date is a GAP (or nothing was found).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ingestion.config import ConfigError, Settings, load_settings
from ingestion.storage import raw_key

log = logging.getLogger("inventory")

DEFAULT_LOCAL_ROOT = Path("data/raw")
SCOREBOARD = "scoreboard"
RUNS = "_runs"
TRADITIONAL = "boxscore_traditional"
ADVANCED = "boxscore_advanced"
BOX_SCORES = (TRADITIONAL, ADVANCED)
KNOWN_ENDPOINTS = frozenset({SCOREBOARD, RUNS, *BOX_SCORES})

KEY_RE = re.compile(
    r"^(?P<endpoint>[^/]+)/game_date=(?P<game_date>\d{4}-\d{2}-\d{2})/(?:(?P<game_id>[^/]+)/)?(?P<pulled_at>[^/]+)\.json$"
)


@dataclass(frozen=True)
class RawKey:
    endpoint: str
    game_date: str
    game_id: str | None
    pulled_at: str


@dataclass
class DateReport:
    game_date: str
    scoreboard_pulls: int
    final_games: int | None  # None when the date has no _runs summary
    traditional: set[str]
    advanced: set[str]
    failures: int
    missing: dict[str, list[str]] = field(default_factory=dict)  # game_id -> endpoints it lacks

    @property
    def ok(self) -> bool:
        return (
            self.final_games is not None
            and len(self.traditional) >= self.final_games
            and len(self.advanced) >= self.final_games
            and self.failures == 0
            and not self.missing
        )

    def status(self) -> str:
        if self.ok:
            return "OK"
        notes = []
        if self.final_games is None:
            notes.append("no _runs summary")
        else:
            short = [name for name, ids in ((TRADITIONAL, self.traditional), (ADVANCED, self.advanced)) if len(ids) < self.final_games]
            if short:
                notes.append("fewer box scores than final games: " + ", ".join(short))
        for game_id, endpoints in sorted(self.missing.items()):
            notes.append(f"{game_id} missing {', '.join(endpoints)}")
        if self.failures:
            notes.append(f"{self.failures} failure(s) in latest run")
        return "GAP: " + "; ".join(notes)


class RawStore(Protocol):
    def list_keys(self) -> dict[str, int]:
        """Every key relative to the raw prefix, mapped to its size in bytes."""
        ...

    def read_json(self, key: str) -> Any:
        """Read and parse one key (relative to the raw prefix)."""
        ...


class LocalStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def list_keys(self) -> dict[str, int]:
        if not self.root.is_dir():
            raise FileNotFoundError(f"{self.root} is not a directory")
        return {p.relative_to(self.root).as_posix(): p.stat().st_size for p in self.root.rglob("*") if p.is_file()}

    def read_json(self, key: str) -> Any:
        return json.loads((self.root / key).read_bytes())


class S3Store:
    def __init__(self, bucket: str, prefix: str = "raw", client: Any = None) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = client if client is not None else boto3.client("s3")

    @classmethod
    def from_settings(cls, settings: Settings) -> S3Store:
        return cls(settings.bucket, settings.raw_prefix, boto3.client("s3", region_name=settings.region))

    def list_keys(self) -> dict[str, int]:
        list_prefix = f"{self.prefix}/" if self.prefix else ""
        keys: dict[str, int] = {}
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=list_prefix):
            for obj in page.get("Contents", []):
                keys[obj["Key"][len(list_prefix):]] = obj["Size"]
        return keys

    def read_json(self, key: str) -> Any:
        full_key = f"{self.prefix}/{key}" if self.prefix else key
        return json.loads(self.client.get_object(Bucket=self.bucket, Key=full_key)["Body"].read())


def parse_key(key: str) -> RawKey | None:
    """Split a raw key into its parts, or None if it doesn't follow the layout."""
    m = KEY_RE.match(key)
    if not m or m["endpoint"] not in KNOWN_ENDPOINTS:
        return None
    # Box scores live under a game_id folder; scoreboards and run summaries don't.
    if (m["endpoint"] in BOX_SCORES) != (m["game_id"] is not None):
        return None
    try:
        date.fromisoformat(m["game_date"])
    except ValueError:
        return None
    return RawKey(m["endpoint"], m["game_date"], m["game_id"], m["pulled_at"])


def in_range(game_date: str, start: date | None, end: date | None) -> bool:
    d = date.fromisoformat(game_date)
    return (start is None or d >= start) and (end is None or d <= end)


def build_report(
    keys: Mapping[str, int],
    read_json: Callable[[str], Any],
    start: date | None = None,
    end: date | None = None,
) -> list[DateReport]:
    """One DateReport per game date in range, oldest first. Reads only the latest _runs summary per date."""
    by_date: dict[str, list[RawKey]] = defaultdict(list)
    for key in keys:
        parsed = parse_key(key)
        if parsed and in_range(parsed.game_date, start, end):
            by_date[parsed.game_date].append(parsed)

    reports = []
    for game_date in sorted(by_date):
        parsed_keys = by_date[game_date]
        ids = {name: {k.game_id for k in parsed_keys if k.endpoint == name} for name in BOX_SCORES}
        run_stamps = [k.pulled_at for k in parsed_keys if k.endpoint == RUNS]

        final_games, failures = None, 0
        if run_stamps:
            latest = read_json(raw_key(RUNS, game_date, max(run_stamps)))  # pulled_at sorts as a string
            final_games = latest.get("games_found", 0) - len(latest.get("games_skipped", []))
            failures = len(latest.get("failures", []))

        missing: dict[str, list[str]] = {}
        for game_id in sorted(ids[TRADITIONAL] | ids[ADVANCED]):
            lacking = [name for name in BOX_SCORES if game_id not in ids[name]]
            if lacking:
                missing[game_id] = lacking

        reports.append(DateReport(
            game_date=game_date,
            scoreboard_pulls=len({k.pulled_at for k in parsed_keys if k.endpoint == SCOREBOARD}),
            final_games=final_games,
            traditional=ids[TRADITIONAL],
            advanced=ids[ADVANCED],
            failures=failures,
            missing=missing,
        ))
    return reports


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def format_table(reports: list[DateReport], keys: Mapping[str, int], start: date | None = None, end: date | None = None) -> list[str]:
    """Aligned rows, one per date, plus a total line."""
    lines = [f"{'game_date':<10}  {'scoreboards':>11}  {'final':>5}  {'trad':>4}  {'adv':>4}  {'failures':>8}  status"]
    for r in reports:
        final = "?" if r.final_games is None else str(r.final_games)
        lines.append(
            f"{r.game_date:<10}  {r.scoreboard_pulls:>11}  {final:>5}  {len(r.traditional):>4}  {len(r.advanced):>4}  {r.failures:>8}  {r.status()}"
        )

    in_scope = {k: size for k, size in keys.items() if (p := parse_key(k)) and in_range(p.game_date, start, end)}
    box_files = sum(1 for k in in_scope if parse_key(k).endpoint in BOX_SCORES)
    games = sum(r.final_games or 0 for r in reports)
    gaps = sum(1 for r in reports if not r.ok)
    lines.append(
        f"Total: {len(reports)} dates ({gaps} GAP), {games} final games, {box_files} box score files, "
        f"{format_size(sum(in_scope.values()))}"
    )
    return lines


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=date.fromisoformat, help="First game date to include, YYYY-MM-DD")
    parser.add_argument("--end", type=date.fromisoformat, help="Last game date to include, YYYY-MM-DD")
    parser.add_argument(
        "--local", nargs="?", const=DEFAULT_LOCAL_ROOT, type=Path, metavar="PATH",
        help=f"Read disk (default {DEFAULT_LOCAL_ROOT}) instead of S3",
    )
    args = parser.parse_args(argv)
    if args.start and args.end and args.end < args.start:
        parser.error(f"--end {args.end} is before --start {args.start}")
    return args


def make_store(local_root: Path | None) -> RawStore:
    if local_root is not None:
        return LocalStore(local_root)
    return S3Store.from_settings(load_settings())


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    try:
        store = make_store(args.local)
        keys = store.list_keys()
        reports = build_report(keys, store.read_json, args.start, args.end)
    except ConfigError as exc:
        log.error("Config error: %s", exc)
        return 1
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1
    except ClientError as exc:
        err = exc.response.get("Error", {})
        log.error("S3 %s failed: %s: %s", exc.operation_name, err.get("Code"), err.get("Message"))
        return 1
    except BotoCoreError as exc:
        log.error("S3 failed: %s", exc)
        return 1

    if not reports:
        log.error("No raw files found%s", " in that date range" if args.start or args.end else "")
        return 1
    print("\n".join(format_table(reports, keys, args.start, args.end)))
    return 1 if any(not r.ok for r in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
