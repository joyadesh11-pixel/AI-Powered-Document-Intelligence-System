"""backend/.env is loaded for the API, worker and scripts alike; real env vars win."""
from __future__ import annotations

from pathlib import Path

from app.pipeline import config


def test_loads_values_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DOCIQ_TEST_VAR", raising=False)
    env = tmp_path / ".env"
    env.write_text("DOCIQ_TEST_VAR=from-file\n", encoding="utf-8")
    config.load_env(env)
    import os
    assert os.environ["DOCIQ_TEST_VAR"] == "from-file"
    monkeypatch.delenv("DOCIQ_TEST_VAR")


def test_real_environment_wins_over_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCIQ_TEST_VAR", "from-shell")
    env = tmp_path / ".env"
    env.write_text("DOCIQ_TEST_VAR=from-file\n", encoding="utf-8")
    config.load_env(env)
    import os
    assert os.environ["DOCIQ_TEST_VAR"] == "from-shell"


def test_missing_file_is_not_an_error(tmp_path):
    config.load_env(tmp_path / "does-not-exist.env")


def test_default_location_is_backend_env():
    assert config.ENV_FILE == Path(config.__file__).resolve().parents[2] / ".env"


def test_non_live_tests_never_see_the_real_api_key():
    import os
    assert "GOOGLE_API_KEY" not in os.environ
