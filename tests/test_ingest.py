from datetime import date, datetime, timezone

import pytest

from ingestion import ingest, nba_client

SCOREBOARDS = {
    "2026-10-20": [("0022600001", 3), ("0022600002", 3)],
    "2026-10-21": [("0022600003", 3), ("0022600004", 2)],  # second game still in progress
}


def fake_scoreboard(timeout, game_date):
    games = [{"gameId": gid, "gameStatus": status, "gameStatusText": "x"} for gid, status in SCOREBOARDS.get(game_date, [])]
    return FakeResponse({"scoreboard": {"gameDate": game_date, "games": games}})


def fake_box_score(timeout, game_id):
    return FakeResponse({"boxScore": {"gameId": game_id}})


def failing_box_score(timeout, game_id):
    raise ConnectionError(f"stats.nba.com timed out for {game_id}")


class FakeResponse:
    """Stands in for an nba_api endpoint instance so tests never hit the network."""

    def __init__(self, payload):
        self.payload = payload

    def get_dict(self):
        return self.payload


class FakeWriter:
    """Records every write in a dict keyed by storage key."""

    def __init__(self):
        self.files: dict[str, dict] = {}

    def write(self, key, payload):
        self.files[key] = payload
        return f"fake://{key}"


@pytest.fixture(autouse=True)
def fake_api(monkeypatch):
    monkeypatch.setattr(ingest, "ScoreboardV3", fake_scoreboard)
    monkeypatch.setattr(ingest, "BOX_SCORE_ENDPOINTS", {"boxscore_traditional": fake_box_score, "boxscore_advanced": fake_box_score})
    monkeypatch.setattr(nba_client, "MAX_ATTEMPTS", 1)  # no retry backoff sleeps in tests


def run(dates):
    writer = FakeWriter()
    summaries = ingest.run(dates, writer, sleep_seconds=0, timeout=5)
    return writer, summaries


# --- date resolution ---


def test_resolve_dates_single_date():
    args = ingest.parse_args(["--date", "2026-10-21"])
    assert ingest.resolve_dates(args, today=date(2030, 1, 1)) == [date(2026, 10, 21)]


def test_resolve_dates_inclusive_range():
    args = ingest.parse_args(["--start", "2026-10-30", "--end", "2026-11-02"])
    assert ingest.resolve_dates(args, today=date(2030, 1, 1)) == [
        date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2),
    ]


def test_resolve_dates_nightly_defaults_to_three_days_before_today():
    args = ingest.parse_args(["--nightly"])
    assert ingest.resolve_dates(args, today=date(2026, 10, 22)) == [
        date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21),
    ]


def test_resolve_dates_nightly_days():
    args = ingest.parse_args(["--nightly", "--days", "1"])
    assert ingest.resolve_dates(args, today=date(2026, 10, 22)) == [date(2026, 10, 21)]


def test_eastern_today_uses_new_york_not_utc():
    # 02:30 UTC on Oct 22 is still 22:30 on Oct 21 in New York.
    assert ingest.eastern_today(datetime(2026, 10, 22, 2, 30, tzinfo=timezone.utc)) == date(2026, 10, 21)


@pytest.mark.parametrize("argv", [
    [],
    ["--date", "2026-10-21", "--nightly"],
    ["--start", "2026-10-21"],
    ["--start", "2026-10-22", "--end", "2026-10-21"],
    ["--date", "2026-10-21", "--days", "2"],
])
def test_parse_args_rejects_bad_combinations(argv):
    with pytest.raises(SystemExit):
        ingest.parse_args(argv)


def test_parse_args_local_default_and_path(tmp_path):
    assert ingest.parse_args(["--nightly", "--local"]).local == ingest.DEFAULT_LOCAL_ROOT
    assert ingest.parse_args(["--nightly", "--local", str(tmp_path)]).local == tmp_path
    assert ingest.parse_args(["--nightly"]).local is None


# --- run behavior ---


def test_run_over_two_dates_writes_all_keys_with_one_pulled_at():
    writer, summaries = run([date(2026, 10, 20), date(2026, 10, 21)])

    pulled_at = summaries[0]["pulled_at"]
    assert summaries[1]["pulled_at"] == pulled_at
    assert set(writer.files) == {
        f"scoreboard/game_date=2026-10-20/{pulled_at}.json",
        f"boxscore_traditional/game_date=2026-10-20/0022600001/{pulled_at}.json",
        f"boxscore_advanced/game_date=2026-10-20/0022600001/{pulled_at}.json",
        f"boxscore_traditional/game_date=2026-10-20/0022600002/{pulled_at}.json",
        f"boxscore_advanced/game_date=2026-10-20/0022600002/{pulled_at}.json",
        f"_runs/game_date=2026-10-20/{pulled_at}.json",
        f"scoreboard/game_date=2026-10-21/{pulled_at}.json",
        f"boxscore_traditional/game_date=2026-10-21/0022600003/{pulled_at}.json",
        f"boxscore_advanced/game_date=2026-10-21/0022600003/{pulled_at}.json",
        f"_runs/game_date=2026-10-21/{pulled_at}.json",
    }
    run_summary = writer.files[f"_runs/game_date=2026-10-20/{pulled_at}.json"]
    assert run_summary["games_found"] == 2
    assert run_summary["box_scores_written"] == 4
    assert run_summary["failures"] == []
    assert ingest.report(summaries) == 0


def test_non_final_game_is_skipped():
    writer, [summary] = run([date(2026, 10, 21)])

    assert summary["games_found"] == 2
    assert summary["games_skipped"] == [{"game_id": "0022600004", "game_status": 2}]
    assert not any("0022600004" in key for key in writer.files)


def test_day_without_games_saves_scoreboard_and_summary_only():
    writer, [summary] = run([date(2026, 7, 4)])

    assert sorted(k.split("/")[0] for k in writer.files) == ["_runs", "scoreboard"]
    assert summary["games_found"] == 0
    assert ingest.report([summary]) == 0


def test_failed_box_score_is_recorded_and_run_continues(monkeypatch):
    monkeypatch.setattr(ingest, "BOX_SCORE_ENDPOINTS", {"boxscore_traditional": fake_box_score, "boxscore_advanced": failing_box_score})

    writer, [summary] = run([date(2026, 10, 20)])

    assert [(f["endpoint"], f["game_id"]) for f in summary["failures"]] == [
        ("boxscore_advanced", "0022600001"),
        ("boxscore_advanced", "0022600002"),
    ]
    assert sum(k.startswith("boxscore_traditional/") for k in writer.files) == 2
    assert not any(k.startswith("boxscore_advanced/") for k in writer.files)
    assert summary["box_scores_written"] == 2
    assert ingest.report([summary]) == 1


def test_failed_scoreboard_skips_that_date_only(monkeypatch):
    def flaky_scoreboard(timeout, game_date):
        if game_date == "2026-10-20":
            raise ConnectionError("blocked")
        return fake_scoreboard(timeout, game_date)

    monkeypatch.setattr(ingest, "ScoreboardV3", flaky_scoreboard)

    writer, summaries = run([date(2026, 10, 20), date(2026, 10, 21)])

    assert [f["endpoint"] for f in summaries[0]["failures"]] == ["scoreboard"]
    assert [k.split("/")[0] for k in writer.files if "2026-10-20" in k] == ["_runs"]
    assert summaries[1]["box_scores_written"] == 2
    assert ingest.report(summaries) == 1


def test_log_file_gets_summary_lines_and_appends(tmp_path):
    log_file = tmp_path / "logs" / "nested" / "nightly.log"  # parent folders don't exist yet
    argv = ["--date", "2026-10-20", "--local", str(tmp_path / "raw"), "--sleep", "0", "--log-file", str(log_file)]

    assert ingest.main(argv) == 0
    assert ingest.main(argv) == 0  # second run gets a new pulled_at, so no overwrite

    text = log_file.read_text(encoding="utf-8")
    assert text.count("2026-10-20: 2 games, 4 box scores saved, 0 skipped, 0 failures") == 2
    assert text.count("Total: 1 dates, 2 games, 4 box scores saved, 0 skipped, 0 failures") == 2


def test_unexpected_crash_is_logged_to_file(tmp_path, monkeypatch):
    def broken_run(*args, **kwargs):
        raise RuntimeError("AccessDenied on PutObject")

    monkeypatch.setattr(ingest, "run", broken_run)
    log_file = tmp_path / "nightly.log"

    assert ingest.main(["--date", "2026-10-20", "--local", str(tmp_path / "raw"), "--log-file", str(log_file)]) == 1
    text = log_file.read_text(encoding="utf-8")
    assert "Ingest crashed" in text and "AccessDenied on PutObject" in text


def test_already_exists_is_recorded_as_failure():
    class ExistingWriter(FakeWriter):
        def write(self, key, payload):
            if key.startswith("scoreboard/"):
                raise ingest.AlreadyExistsError(f"{key} already exists")
            return super().write(key, payload)

    writer = ExistingWriter()
    [summary] = ingest.run([date(2026, 10, 20)], writer, sleep_seconds=0, timeout=5)

    assert [f["endpoint"] for f in summary["failures"]] == ["scoreboard"]
    assert summary["box_scores_written"] == 4
    assert ingest.report([summary]) == 1
