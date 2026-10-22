from datetime import date

import pytest

from ingestion import inventory
from ingestion.inventory import LocalStore, RawKey, build_report, format_table, parse_key
from ingestion.storage import LocalWriter, raw_key

OLD = "20261021T060000000000Z"
NEW = "20261022T060000000000Z"


def date_keys(game_date, pulled_at, game_ids, advanced_ids=None):
    """Keys one ingest run writes for a date: scoreboard, both box scores per game, _runs summary."""
    advanced_ids = game_ids if advanced_ids is None else advanced_ids
    keys = [raw_key("scoreboard", game_date, pulled_at), raw_key("_runs", game_date, pulled_at)]
    keys += [raw_key("boxscore_traditional", game_date, pulled_at, g) for g in game_ids]
    keys += [raw_key("boxscore_advanced", game_date, pulled_at, g) for g in advanced_ids]
    return {k: 100 for k in keys}


def summary(games_found, skipped=(), failures=()):
    return {
        "games_found": games_found,
        "games_skipped": [{"game_id": g, "game_status": 2} for g in skipped],
        "failures": list(failures),
    }


def report_for(keys, summaries, **kwargs):
    reads = []

    def read_json(key):
        reads.append(key)
        return summaries[key]

    return build_report(keys, read_json, **kwargs), reads


# --- key parsing ---


def test_parse_box_score_key():
    assert parse_key("boxscore_advanced/game_date=2026-10-20/0022600001/20261022T000614483963Z.json") == RawKey(
        "boxscore_advanced", "2026-10-20", "0022600001", "20261022T000614483963Z"
    )


def test_parse_scoreboard_and_runs_keys_have_no_game_id():
    assert parse_key("scoreboard/game_date=2026-10-20/X.json") == RawKey("scoreboard", "2026-10-20", None, "X")
    assert parse_key("_runs/game_date=2026-10-20/X.json") == RawKey("_runs", "2026-10-20", None, "X")


@pytest.mark.parametrize("key", [
    "_checks/s3_check_20261021T234206472732Z.txt",
    "_checks/game_date=2026-10-20/X.json",                    # unknown endpoint
    "scoreboard/game_date=2026-10-20/0022600001/X.json",      # scoreboard under a game_id
    "boxscore_traditional/game_date=2026-10-20/X.json",       # box score without a game_id
    "scoreboard/game_date=2026-13-45/X.json",                 # not a real date
    "scoreboard/game_date=2026-10-20/X.txt",
])
def test_parse_key_ignores_keys_outside_the_layout(key):
    assert parse_key(key) is None


# --- report ---


def test_complete_date_is_ok():
    keys = date_keys("2026-10-20", NEW, ["0022600001", "0022600002", "0022600003"])
    keys["_checks/s3_check_20261021T234206472732Z.txt"] = 40
    summaries = {raw_key("_runs", "2026-10-20", NEW): summary(3)}

    [r], _ = report_for(keys, summaries)

    assert (r.game_date, r.scoreboard_pulls, r.final_games, len(r.traditional), len(r.advanced), r.failures) == (
        "2026-10-20", 1, 3, 3, 3, 0,
    )
    assert r.ok and r.status() == "OK"


def test_missing_advanced_box_score_is_gap_naming_the_game():
    keys = date_keys("2026-10-20", NEW, ["0022600001", "0022600002"], advanced_ids=["0022600001"])
    summaries = {raw_key("_runs", "2026-10-20", NEW): summary(2)}

    [r], _ = report_for(keys, summaries)

    assert not r.ok
    assert r.missing == {"0022600002": ["boxscore_advanced"]}
    assert r.status().startswith("GAP")
    assert "0022600002 missing boxscore_advanced" in r.status()


def test_latest_runs_summary_wins():
    # The older run hit a failure and skipped a game; the newer re-pull got everything.
    keys = {**date_keys("2026-10-20", OLD, ["0022600001"]), **date_keys("2026-10-20", NEW, ["0022600001", "0022600002"])}
    summaries = {
        raw_key("_runs", "2026-10-20", OLD): summary(2, skipped=["0022600002"], failures=[{"endpoint": "boxscore_advanced"}]),
        raw_key("_runs", "2026-10-20", NEW): summary(2),
    }

    [r], reads = report_for(keys, summaries)

    assert reads == [raw_key("_runs", "2026-10-20", NEW)]
    assert (r.scoreboard_pulls, r.final_games, r.failures) == (2, 2, 0)
    assert r.ok


def test_failures_in_latest_run_make_a_gap():
    keys = date_keys("2026-10-20", NEW, ["0022600001"])
    summaries = {raw_key("_runs", "2026-10-20", NEW): summary(1, failures=[{"endpoint": "scoreboard"}])}

    [r], _ = report_for(keys, summaries)

    assert not r.ok
    assert "1 failure(s) in latest run" in r.status()


def test_date_without_runs_summary_is_gap():
    keys = {raw_key("scoreboard", "2026-10-20", NEW): 100}

    [r], _ = report_for(keys, {})

    assert r.final_games is None
    assert "no _runs summary" in r.status()


def test_dates_sorted_and_filtered_by_range():
    keys = {
        **date_keys("2026-10-22", NEW, []),
        **date_keys("2026-10-20", NEW, []),
        **date_keys("2026-10-21", NEW, []),
    }
    summaries = {raw_key("_runs", d, NEW): summary(0) for d in ("2026-10-20", "2026-10-21", "2026-10-22")}

    reports, _ = report_for(keys, summaries)
    assert [r.game_date for r in reports] == ["2026-10-20", "2026-10-21", "2026-10-22"]

    reports, _ = report_for(keys, summaries, start=date(2026, 10, 21), end=date(2026, 10, 21))
    assert [r.game_date for r in reports] == ["2026-10-21"]


def test_format_table_rows_and_total():
    keys = {
        **date_keys("2026-10-20", NEW, ["0022600001", "0022600002"]),
        **date_keys("2026-10-21", NEW, ["0022600003"], advanced_ids=[]),
    }
    summaries = {raw_key("_runs", "2026-10-20", NEW): summary(2), raw_key("_runs", "2026-10-21", NEW): summary(1)}
    reports, _ = report_for(keys, summaries)

    lines = format_table(reports, keys)

    assert len(lines) == 4
    assert lines[1].startswith("2026-10-20") and lines[1].endswith("OK")
    assert lines[2].startswith("2026-10-21") and "GAP" in lines[2]
    assert lines[-1] == "Total: 2 dates (1 GAP), 3 final games, 5 box score files, 900 B"
    assert inventory.format_size(1536) == "1.5 KB"


# --- local store ---


def test_local_store_lists_forward_slash_keys_and_reads_json(tmp_path):
    key = raw_key("_runs", "2026-10-20", NEW)
    LocalWriter(tmp_path).write(key, summary(3))

    store = LocalStore(tmp_path)

    assert list(store.list_keys()) == [key]
    assert store.read_json(key)["games_found"] == 3


def test_main_exit_codes(tmp_path, capsys):
    writer = LocalWriter(tmp_path)
    for key in date_keys("2026-10-20", NEW, ["0022600001"]):
        writer.write(key, summary(1) if key.startswith("_runs/") else {})

    assert inventory.main(["--local", str(tmp_path)]) == 0
    assert "OK" in capsys.readouterr().out

    writer.write(raw_key("boxscore_traditional", "2026-10-21", NEW, "0022600009"), {})
    assert inventory.main(["--local", str(tmp_path)]) == 1
    assert "GAP" in capsys.readouterr().out
