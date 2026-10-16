"""Configuration service owns its settings; credentials never enter conversation state."""

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
WORKSPACE_ROOT = DEFAULT_ENV_FILE.parent.parent.parent
PATH_SETTINGS = {"REVIEW_PAGES_DIR", "CONFIGURATION_STATE_DIR", "CONFIGURATION_CASES_DIR"}


def _dotenv(path: Path):
    path = path.resolve()
    if path.is_file():
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or not key:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
                value = value[1:-1]
            if key in PATH_SETTINGS and value:
                value = str((path.parent / value).resolve())
            os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    state_dir: Path = field(default_factory=lambda: Path(".review-agent"))
    pages_dir: Path = field(default_factory=lambda: Path("review-pages"))
    cases_dir: Path = field(default_factory=lambda: Path("docs/示例/页面配置"))
    review_url: str = "http://127.0.0.1:8010"
    endpoint: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen3.7-plus"
    api_key: str = field(default="", repr=False)
    timeout: float = 90
    max_calls: int = 24


def load_settings(env_file: Path | None = None) -> Settings:
    _dotenv(env_file if env_file is not None else DEFAULT_ENV_FILE)
    return Settings(
        state_dir=Path(os.getenv("CONFIGURATION_STATE_DIR", str(WORKSPACE_ROOT / ".review-agent"))).resolve(),
        pages_dir=Path(os.getenv("REVIEW_PAGES_DIR", str(WORKSPACE_ROOT / "review-pages"))).resolve(),
        cases_dir=Path(os.getenv("CONFIGURATION_CASES_DIR", str(WORKSPACE_ROOT / "docs/示例/页面配置"))).resolve(),
        review_url=os.getenv("CONFIGURATION_REVIEW_URL", "http://127.0.0.1:8010").rstrip("/"),
        endpoint=os.getenv("CONFIGURATION_MODEL_ENDPOINT", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/"),
        model=os.getenv("CONFIGURATION_MODEL", "qwen3.7-plus"),
        api_key=os.getenv("CONFIGURATION_API_KEY", ""),
        max_calls=int(os.getenv("CONFIGURATION_MAX_CALLS", "24")),
    )
