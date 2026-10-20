"""Backend-local env files, independent credentials, and stable file-relative paths."""

import os
from pathlib import Path

from forj_review_config_service.config import settings as configuration
from forj_review_service.config import settings as review


def test_review_uses_its_package_env_from_any_working_directory(tmp_path, monkeypatch):
    package = tmp_path / "src/review"
    package.mkdir(parents=True)
    env = package / ".env"
    env.write_text("\ufeffexport DASHSCOPE_API_KEY='review-only'\nDASHSCOPE_MODEL=review-model\n"
                   "REVIEW_LOG_DIR=../../logs\n", encoding="utf-8")
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=root-key\n", encoding="utf-8")
    monkeypatch.setattr(os, "environ", {"CONFIGURATION_DATABASE_URL": "host=127.0.0.1 port=1 dbname=unused"})
    monkeypatch.setattr(review, "DEFAULT_ENV_FILE", env)
    monkeypatch.chdir(tmp_path)
    config = review.load_qwen_config()
    assert config.api_key == "review-only" and config.model == "review-model"
    assert Path(os.environ["REVIEW_LOG_DIR"]) == tmp_path / "logs"
    monkeypatch.chdir(package)
    assert review.load_qwen_config().api_key == "review-only"


def test_configuration_uses_own_key_endpoint_model_and_file_relative_directories(tmp_path, monkeypatch):
    package = tmp_path / "src/configuration"
    package.mkdir(parents=True)
    env = package / ".env"
    env.write_text("export CONFIGURATION_API_KEY='configuration-only'\n"
        "CONFIGURATION_MODEL=configuration-model\nCONFIGURATION_MODEL_ENDPOINT=https://config.example/v1/\n"
        "CONFIGURATION_STATE_DIR=../../state\n"
        "CONFIGURATION_CASES_DIR=../../cases\n", encoding="utf-8")
    monkeypatch.setattr(os, "environ", {"CONFIGURATION_DATABASE_URL": "host=127.0.0.1 port=1 dbname=unused", "DASHSCOPE_API_KEY": "review-key", "DASHSCOPE_MODEL": "review-model"})
    monkeypatch.setattr(configuration, "DEFAULT_ENV_FILE", env)
    monkeypatch.chdir(package)
    config = configuration.load_settings()
    assert config.api_key == "configuration-only" and config.model == "configuration-model"
    assert config.endpoint == "https://config.example/v1"
    assert config.state_dir == tmp_path / "state" and config.cases_dir == tmp_path / "cases"
    monkeypatch.chdir(tmp_path)
    assert configuration.load_settings() == config


def test_missing_package_env_never_falls_back_to_root_env_or_review_credentials(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=root-secret\nCONFIGURATION_API_KEY=root-secret\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "environ", {"CONFIGURATION_DATABASE_URL": "host=127.0.0.1 port=1 dbname=unused"})
    monkeypatch.setattr(review, "DEFAULT_ENV_FILE", tmp_path / "missing-review/.env")
    monkeypatch.setattr(configuration, "DEFAULT_ENV_FILE", tmp_path / "missing-config/.env")
    assert review.load_qwen_config().api_key is None
    os.environ.update(DASHSCOPE_API_KEY="review-secret", DASHSCOPE_MODEL="review-model", DASHSCOPE_BASE_URL="https://review.example")
    config = configuration.load_settings()
    assert config.api_key == "" and config.model != "review-model"
    assert config.endpoint != "https://review.example"


def test_process_environment_remains_authoritative_for_each_backend(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DASHSCOPE_API_KEY=file-review\nCONFIGURATION_API_KEY=file-configuration\n"
                   "CONFIGURATION_STATE_DIR=file-state\n", encoding="utf-8")
    state = str(tmp_path / "external-state")
    monkeypatch.setattr(os, "environ", {"CONFIGURATION_DATABASE_URL": "host=127.0.0.1 port=1 dbname=unused", "DASHSCOPE_API_KEY": "process-review",
        "CONFIGURATION_API_KEY": "process-configuration", "CONFIGURATION_STATE_DIR": state})
    assert review.load_qwen_config(env).api_key == "process-review"
    config = configuration.load_settings(env)
    assert config.api_key == "process-configuration" and config.state_dir == Path(state)


def test_examples_use_postgres_without_local_pages_or_real_keys(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    with monkeypatch.context() as context:
        context.setattr(os, "environ", {"CONFIGURATION_DATABASE_URL": "host=127.0.0.1 port=1 dbname=unused"})
        cfg = review.load_qwen_config(root / "src/forj_review_service/.env.example")
        assert "REVIEW_PAGES_DIR" not in os.environ
        assert not cfg.available
    with monkeypatch.context() as context:
        context.setattr(os, "environ", {"CONFIGURATION_DATABASE_URL": "host=127.0.0.1 port=1 dbname=unused"})
        cfg = configuration.load_settings(root / "src/forj_review_config_service/.env.example")
        assert not cfg.api_key
        assert "REVIEW_PAGES_DIR" not in os.environ
        assert cfg.state_dir == root / ".review-config"
