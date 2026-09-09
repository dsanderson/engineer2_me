"""The optional .env file: it seeds the environment, it never overrides it."""

from __future__ import annotations

import os

import pytest

from app.config import Settings, env_file_is_world_readable, load_env_file


@pytest.fixture(autouse=True)
def restore_environment():
    """load_env_file mutates os.environ by design, so put it back afterwards."""
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


def test_values_are_read_from_the_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "ONSHAPE_ACCESS_KEY=from-file",
                "export ONSHAPE_SECRET_KEY = 'quoted secret'",
                'E2_BASE_URL="http://example.test"',
                "MALFORMED_LINE_WITHOUT_EQUALS",
            ]
        )
    )
    for key in ("ONSHAPE_ACCESS_KEY", "ONSHAPE_SECRET_KEY", "E2_BASE_URL"):
        monkeypatch.delenv(key, raising=False)

    assert load_env_file(path) == path
    settings = Settings()
    assert settings.onshape_access_key == "from-file"
    assert settings.onshape_secret_key == "quoted secret"
    assert settings.base_url == "http://example.test"
    assert settings.onshape_configured


def test_the_environment_wins_over_the_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("ONSHAPE_ACCESS_KEY=from-file\n")
    monkeypatch.setenv("ONSHAPE_ACCESS_KEY", "from-environment")
    load_env_file(path)
    assert Settings().onshape_access_key == "from-environment"


def test_a_missing_file_is_not_an_error(tmp_path):
    assert load_env_file(tmp_path / "nope.env") is None


def test_secrets_are_not_interpolated(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("ONSHAPE_SECRET_KEY=abc$HOME/def\n")
    monkeypatch.delenv("ONSHAPE_SECRET_KEY", raising=False)
    load_env_file(path)
    assert os.environ["ONSHAPE_SECRET_KEY"] == "abc$HOME/def"


def test_world_readable_secrets_are_detected(tmp_path):
    path = tmp_path / ".env"
    path.write_text("ONSHAPE_ACCESS_KEY=x\n")
    path.chmod(0o644)
    assert env_file_is_world_readable(path)
    path.chmod(0o600)
    assert not env_file_is_world_readable(path)
