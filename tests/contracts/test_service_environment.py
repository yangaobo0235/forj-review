"""Backend-local env files, independent credentials, and stable file-relative paths."""

import os
from pathlib import Path

from forj_configuration_agent.config import settings as configuration
from forj_review_assistant.config import settings as review


def test_review_uses_its_package_env_from_any_working_directory(tmp_path, monkeypatch):
    package = tmp_path / "src/review"
    package.mkdir(parents=True)
    env = package / ".env"
    env.write_text("\ufeffexport DASHSCOPE_API_KEY='review-only'\nDASHSCOPE_MODEL=review-model\n"
                   "REVIEW_PAGES_DIR=../../pages\nREVIEW_LOG_DIR=../../logs\n", encoding="utf-8")
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=root-key\n", encoding="utf-8")
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.setattr(review, "DEFAULT_ENV_FILE", env)
    monkeypatch.chdir(tmp_path)
    config = review.load_qwen_config()
    assert config.api_key == "review-only" and config.model == "review-model"
    assert Path(os.environ["REVIEW_PAGES_DIR"]) == tmp_path / "pages"
    assert Path(os.environ["REVIEW_LOG_DIR"]) == tmp_path / "logs"
    monkeypatch.chdir(package)
    assert review.load_qwen_config().api_key == "review-only"
    assert Path(os.environ["REVIEW_PAGES_DIR"]) == tmp_path / "pages"


def test_configuration_uses_own_key_endpoint_model_and_file_relative_directories(tmp_path, monkeypatch):
    package = tmp_path / "src/configuration"
    package.mkdir(parents=True)
    env = package / ".env"
    env.write_text("export CONFIGURATION_API_KEY='configuration-only'\n"
        "CONFIGURATION_MODEL=configuration-model\nCONFIGURATION_MODEL_ENDPOINT=https://config.example/v1/\n"
        "REVIEW_PAGES_DIR=../../pages\nCONFIGURATION_STATE_DIR=../../state\n"
        "CONFIGURATION_CASES_DIR=../../cases\n", encoding="utf-8")
    monkeypatch.setattr(os, "environ", {"DASHSCOPE_API_KEY": "review-key", "DASHSCOPE_MODEL": "review-model"})
    monkeypatch.setattr(configuration, "DEFAULT_ENV_FILE", env)
    monkeypatch.chdir(package)
    config = configuration.load_settings()
    assert config.api_key == "configuration-only" and config.model == "configuration-model"
    assert config.endpoint == "https://config.example/v1"
    assert config.pages_dir == tmp_path / "pages"
    assert config.state_dir == tmp_path / "state" and config.cases_dir == tmp_path / "cases"
    monkeypatch.chdir(tmp_path)
    assert configuration.load_settings() == config


def test_missing_package_env_never_falls_back_to_root_env_or_review_credentials(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=root-secret\nCONFIGURATION_API_KEY=root-secret\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "environ", {})
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
                   "REVIEW_PAGES_DIR=file-pages\n", encoding="utf-8")
    pages = str(tmp_path / "external-pages")
    monkeypatch.setattr(os, "environ", {"DASHSCOPE_API_KEY": "process-review",
        "CONFIGURATION_API_KEY": "process-configuration", "REVIEW_PAGES_DIR": pages})
    assert review.load_qwen_config(env).api_key == "process-review"
    config = configuration.load_settings(env)
    assert config.api_key == "process-configuration" and config.pages_dir == Path(pages)


def test_examples_point_both_services_at_the_same_pages_without_real_keys(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    with monkeypatch.context() as context:
        context.setattr(os, "environ", {})
        cfg = review.load_qwen_config(root / "src/forj_review_assistant/.env.example")
        review_pages = Path(os.environ["REVIEW_PAGES_DIR"])
        assert not cfg.available
    with monkeypatch.context() as context:
        context.setattr(os, "environ", {})
        cfg = configuration.load_settings(root / "src/forj_configuration_agent/.env.example")
        assert not cfg.api_key
        assert cfg.pages_dir == review_pages == root / "review-pages"
        assert cfg.state_dir == root / ".review-agent"
