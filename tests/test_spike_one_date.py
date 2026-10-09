import json

from ingestion import spike_one_date as spike


class FakeEndpoint:
    """Stands in for an nba_api endpoint class so tests never hit the network."""

    def __init__(self, timeout, **params):
        self.params = params

    def get_dict(self):
        if "game_date" in self.params:
            return {"scoreboard": {"games": [{"gameId": "0022500001"}, {"gameId": "0022500002"}]}}
        return {"boxScore": {"gameId": self.params["game_id"]}}


def test_game_ids_from_scoreboard_handles_empty_day():
    assert spike.game_ids_from_scoreboard({"scoreboard": {"games": []}}) == []
    assert spike.game_ids_from_scoreboard({}) == []


def test_run_writes_partitioned_files(tmp_path, monkeypatch):
    monkeypatch.setattr(spike, "ScoreboardV3", FakeEndpoint)
    monkeypatch.setattr(spike, "BOX_SCORE_ENDPOINTS", {"boxscore_traditional": FakeEndpoint, "boxscore_advanced": FakeEndpoint})

    summary = spike.run("2026-03-15", tmp_path, sleep_seconds=0, timeout=5)

    assert summary["games_found"] == 2
    assert summary["failures"] == []
    assert len(list((tmp_path / "scoreboard" / "game_date=2026-03-15").glob("*.json"))) == 1
    for endpoint in ("boxscore_traditional", "boxscore_advanced"):
        for game_id in ("0022500001", "0022500002"):
            files = list((tmp_path / endpoint / "game_date=2026-03-15" / game_id).glob("*.json"))
            assert len(files) == 1
            assert json.loads(files[0].read_text())["boxScore"]["gameId"] == game_id
    run_log = next((tmp_path / "_runs" / "game_date=2026-03-15").glob("*.json"))
    assert json.loads(run_log.read_text())["games_found"] == 2
