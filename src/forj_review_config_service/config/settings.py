"""Typed configuration settings; process variables override the service dotenv file."""

import os
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from forj_review_config_service.config.database import database_url_from_config

DEFAULT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
WORKSPACE_ROOT = DEFAULT_ENV_FILE.parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CONFIGURATION_", extra="ignore", frozen=True,
        env_file_encoding="utf-8-sig", populate_by_name=True, hide_input_in_errors=True)
    state_dir: Path = WORKSPACE_ROOT / ".review-config"
    cases_dir: Path = WORKSPACE_ROOT / "examples/page-config"
    review_url: str = "http://127.0.0.1:26101"
    endpoint: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1",
                          validation_alias="CONFIGURATION_MODEL_ENDPOINT")
    model: str = "qwen3.7-plus"
    api_key: str = Field(default="", repr=False)
    timeout: float = Field(default=90, gt=0)
    max_calls: int = Field(default=24, ge=1)
    database_url: str = Field(default="", repr=False)
    database_schema: str = Field(default="local", pattern=r"^[a-z][a-z0-9_]{0,62}$")

    @field_validator("review_url", "endpoint")
    @classmethod
    def strip_trailing_slash(cls, value):
        return value.rstrip("/")


def load_settings(env_file: Path | None = None) -> Settings:
    path = (env_file if env_file is not None else DEFAULT_ENV_FILE).resolve()
    settings = Settings(_env_file=path)
    updates = {}
    for name in ("state_dir", "cases_dir"):
        value = getattr(settings, name)
        # Only file-provided relative paths are relative to that file.
        if not value.is_absolute() and "CONFIGURATION_" + name.upper() not in os.environ:
            value = path.parent / value
        updates[name] = value.resolve()
    if not settings.database_url.strip():
        updates["database_url"] = database_url_from_config()
    return settings.model_copy(update=updates)
