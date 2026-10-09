# nba-pipeline
Data engineering project to create an nba stat pipeline for the 26-27 season

## Layout

- `ingestion/`: pulls raw data from the NBA stats API (via [`nba_api`](https://github.com/swar/nba_api))
- `dbt/`: warehouse models (Phase 2)
- `airflow/`: orchestration (Phase 3)
- `tests/`: unit tests, no network needed

## Phase 0 spike: pull one night of games

Run this from your own machine; stats.nba.com often rejects cloud and data-center IPs.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

python -m ingestion.spike_one_date --date 2026-03-15
```

Raw JSON lands in `data/raw/` (gitignored), partitioned the same way the Phase 1 S3 keys will be:

```
data/raw/scoreboard/game_date=2026-03-15/<pulled_at>.json
data/raw/boxscore_traditional/game_date=2026-03-15/<game_id>/<pulled_at>.json
data/raw/boxscore_advanced/game_date=2026-03-15/<game_id>/<pulled_at>.json
data/raw/_runs/game_date=2026-03-15/<pulled_at>.json
```

The `_runs` file records games found, files written, failures and elapsed time. The script exits non-zero if any box score failed.

Run the tests with `pytest`.
