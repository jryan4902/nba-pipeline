import pytest

from ingestion import config


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    """Stop load_settings from pulling the real .env into the test environment."""
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: False)


def test_load_settings_from_env(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")

    settings = config.load_settings()

    assert settings == config.Settings(bucket="test-bucket", region="us-west-2", raw_prefix="raw")


@pytest.mark.parametrize("missing", ["S3_BUCKET", "AWS_DEFAULT_REGION"])
def test_load_settings_names_missing_variable(monkeypatch, missing):
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    monkeypatch.delenv(missing)

    with pytest.raises(config.ConfigError, match=missing):
        config.load_settings()


def test_blank_value_counts_as_missing(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "   ")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")

    with pytest.raises(config.ConfigError, match="S3_BUCKET"):
        config.load_settings()
