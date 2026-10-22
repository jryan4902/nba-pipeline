# nba-pipeline
Data engineering project to create an nba stat pipeline for the 26-27 season

## Layout

- `ingestion/`: pulls raw data from the NBA stats API (via [`nba_api`](https://github.com/swar/nba_api)) and saves it to S3
- `scripts/`: Windows Task Scheduler scripts for the nightly run
- `docs/`: notes, including the raw payload shapes ([docs/payloads.md](docs/payloads.md))
- `dbt/`: warehouse models (Phase 2)
- `airflow/`: orchestration (Phase 3)
- `tests/`: unit tests, no network needed

## Setup

Windows (PowerShell 5.1 or 7):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env      # then fill in the AWS keys, region and bucket
python -m ingestion.s3_check     # writes and reads back a test object; prints OK bucket=... key=...
```

If PowerShell refuses to run `Activate.ps1`, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once and try again.

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env             # then fill it in
python -m ingestion.s3_check
```

`.env` is gitignored. The AWS key only needs `s3:ListBucket`, `s3:GetObject` and `s3:PutObject` on the bucket; nothing in the pipeline deletes.

## How raw ingest works

Every file is the API response unchanged, saved under `raw/` in the bucket:

```
raw/scoreboard/game_date=<YYYY-MM-DD>/<pulled_at>.json
raw/boxscore_traditional/game_date=<YYYY-MM-DD>/<game_id>/<pulled_at>.json
raw/boxscore_advanced/game_date=<YYYY-MM-DD>/<game_id>/<pulled_at>.json
raw/_runs/game_date=<YYYY-MM-DD>/<pulled_at>.json      # per-date run summary
```

- **One `pulled_at` per run.** Every file a run writes shares the same UTC timestamp, so `pulled_at` versions runs.
- **Files are never overwritten.** Writes are create-only: S3 enforces it with `IfNoneMatch="*"`, and local writes use exclusive create. A re-pull lands next to the old file under a new `pulled_at`.
- **Box scores only for final games** (`gameStatus` 3). Other games are listed as skipped in the `_runs` summary.
- **The nightly run re-pulls the last 3 game dates**, so it picks up games that weren't final yet and stat corrections made after the first pull.
- **Downstream picks the latest `pulled_at` per game.** Several versions of the same game are expected.

Game dates are US Eastern, matching the NBA. Payload fields and join keys are in [docs/payloads.md](docs/payloads.md).

## Commands

`python -m ingestion.ingest` needs exactly one of `--date`, `--start`/`--end` or `--nightly`:

```powershell
python -m ingestion.ingest --date 2026-10-21                       # one date
python -m ingestion.ingest --start 2026-10-02 --end 2026-10-21     # inclusive range (backfill)
python -m ingestion.ingest --nightly                               # yesterday (Eastern) and the 2 days before; --days N to change
python -m ingestion.ingest --date 2026-10-21 --local               # write to data/raw (or --local PATH) instead of S3
python -m ingestion.ingest --nightly --log-file logs\nightly.log   # also append the log to a file
```

`--sleep` (default 1.5 s between API calls) and `--timeout` (default 30 s) control pacing. The command exits 1 if any API call or write failed.

`python -m ingestion.inventory` checks what's in the bucket, one row per game date:

```powershell
python -m ingestion.inventory --start 2026-10-02 --end 2026-10-21  # --local [PATH] reads data/raw instead
```

A date is OK when every final game in its latest `_runs` summary has both box scores and that run had no failures; otherwise it's GAP, with the missing game IDs. The command exits 1 if any date is GAP.

## Nightly schedule

The nightly ingest runs on this PC with Windows Task Scheduler, because stats.nba.com blocks most cloud IPs. It moves to a server in Phase 4.

Register it once from an **admin** PowerShell:

```powershell
cd C:\path\to\nba-pipeline
.\scripts\register_nightly_task.ps1                # daily at 6:00 AM
.\scripts\register_nightly_task.ps1 -At 7:30AM     # change the time (re-running replaces the task)
.\scripts\register_nightly_task.ps1 -Unregister    # remove it
```

The task `nba-nightly-ingest` runs `scripts\run_nightly.ps1`, which runs `ingest --nightly` with the venv's Python. It:
- runs whether or not you're logged in, without storing your password (S4U logon);
- runs as soon as possible after a missed start (PC off or asleep), and wakes the PC if wake timers are allowed in the power plan;
- retries twice, 15 minutes apart, if the task fails to start, and stops after 1 hour.

Logs go to `logs\nightly_<yyyy-MM-dd>.log` (gitignored) and are deleted after 30 days. Check the last run:

```powershell
Get-ScheduledTaskInfo nba-nightly-ingest | Select-Object LastRunTime, LastTaskResult, NextRunTime
```

`LastTaskResult` 0 means success; 1 means the run had failures (see that day's log). Run `.\scripts\run_nightly.ps1` by hand to test it.

## Tests

```powershell
pytest
```
