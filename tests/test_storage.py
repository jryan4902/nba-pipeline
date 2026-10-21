import json

import pytest
from botocore.exceptions import ClientError

from ingestion import storage
from ingestion.storage import AlreadyExistsError, LocalWriter, S3Writer, raw_key

PAYLOAD = {"boxScore": {"gameId": "0022500976", "homeTeam": {"score": 112}}}


class FakeS3Client:
    """Records put_object calls; optionally raises a canned error instead."""

    def __init__(self, error: ClientError | None = None):
        self.calls: list[dict] = []
        self.error = error

    def put_object(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {}


def client_error(code: str, status: int) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": f"{code} message"}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "PutObject",
    )


def test_raw_key_without_game_id():
    assert raw_key("scoreboard", "2026-03-15", "20261009T224652108720Z") == (
        "scoreboard/game_date=2026-03-15/20261009T224652108720Z.json"
    )


def test_raw_key_with_game_id():
    assert raw_key("boxscore_advanced", "2026-03-15", "20261009T224652108720Z", game_id="0022500976") == (
        "boxscore_advanced/game_date=2026-03-15/0022500976/20261009T224652108720Z.json"
    )


def test_local_writer_writes_json_and_refuses_overwrite(tmp_path):
    key = raw_key("boxscore_traditional", "2026-03-15", "20261009T224652108720Z", game_id="0022500976")
    writer = LocalWriter(tmp_path)

    location = writer.write(key, PAYLOAD)

    expected = tmp_path / "boxscore_traditional" / "game_date=2026-03-15" / "0022500976" / "20261009T224652108720Z.json"
    assert location == str(expected)
    assert json.loads(expected.read_text()) == PAYLOAD

    original = expected.read_bytes()
    with pytest.raises(AlreadyExistsError):
        writer.write(key, {"different": True})
    assert expected.read_bytes() == original


def test_s3_writer_put_object_arguments():
    client = FakeS3Client()
    writer = S3Writer("nba-bucket", client=client)
    key = raw_key("scoreboard", "2026-03-15", "20261009T224652108720Z")

    location = writer.write(key, PAYLOAD)

    assert location == f"s3://nba-bucket/raw/{key}"
    [call] = client.calls
    assert call["Bucket"] == "nba-bucket"
    assert call["Key"] == f"raw/{key}"
    assert call["ContentType"] == "application/json"
    assert call["IfNoneMatch"] == "*"
    assert json.loads(call["Body"]) == PAYLOAD


def test_writers_serialize_identically(tmp_path):
    client = FakeS3Client()
    key = raw_key("scoreboard", "2026-03-15", "20261009T224652108720Z")

    S3Writer("nba-bucket", client=client).write(key, PAYLOAD)
    LocalWriter(tmp_path).write(key, PAYLOAD)

    assert client.calls[0]["Body"] == (tmp_path / key).read_bytes()


@pytest.mark.parametrize(("code", "status"), [("PreconditionFailed", 412), ("ConditionalRequestConflict", 409)])
def test_s3_writer_existing_key_raises_already_exists(code, status):
    writer = S3Writer("nba-bucket", client=FakeS3Client(error=client_error(code, status)))

    with pytest.raises(AlreadyExistsError):
        writer.write("scoreboard/game_date=2026-03-15/x.json", PAYLOAD)


def test_s3_writer_lets_other_errors_through():
    error = client_error("AccessDenied", 403)
    writer = S3Writer("nba-bucket", client=FakeS3Client(error=error))

    with pytest.raises(ClientError) as excinfo:
        writer.write("scoreboard/game_date=2026-03-15/x.json", PAYLOAD)

    assert excinfo.value is error
    assert not isinstance(excinfo.value, AlreadyExistsError)


def test_from_settings_uses_bucket_prefix_and_region(monkeypatch):
    regions = []
    monkeypatch.setattr(storage.boto3, "client", lambda service, region_name=None: regions.append(region_name) or FakeS3Client())

    writer = S3Writer.from_settings(storage.Settings(bucket="nba-bucket", region="us-west-2", raw_prefix="landing"))

    assert (writer.bucket, writer.prefix, regions) == ("nba-bucket", "landing", ["us-west-2"])
