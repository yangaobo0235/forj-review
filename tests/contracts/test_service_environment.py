"""Package-local settings and explicit file-relative reference overrides."""

import os
from pathlib import Path

from forj_review_service.config import settings as review


def test_package_env_and_data_paths_are_independent_of_working_directory(tmp_path, monkeypatch):
    package = tmp_path / "src/review"
    package.mkdir(parents=True)
    env = package / ".env"
    env.write_text(
        "DASHSCOPE_API_KEY=example\nREVIEW_LOG_DIR=../../logs\nREVIEW_PAGES_DIR=../../pages\nREVIEW_ENGINE_EMISSIONS_FILE=../../emissions.json\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.setattr(review, "DEFAULT_ENV_FILE", env)
    monkeypatch.chdir(tmp_path)
    assert review.load_qwen_config().api_key == "example"
    for key, expected in [
        ("REVIEW_LOG_DIR", "logs"),
        ("REVIEW_PAGES_DIR", "pages"),
        ("REVIEW_ENGINE_EMISSIONS_FILE", "emissions.json"),
    ]:
        assert Path(os.environ[key]) == tmp_path / expected
    monkeypatch.chdir(package)
    assert review.load_qwen_config().api_key == "example"


def test_missing_package_env_does_not_load_root_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=root-secret\n", encoding="utf-8")
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(review, "DEFAULT_ENV_FILE", tmp_path / "missing/.env")
    assert review.load_qwen_config().api_key is None


def test_process_settings_override_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DASHSCOPE_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "process-key")
    assert review.load_qwen_config(env).api_key == "process-key"
