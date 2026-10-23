"""
Qwen 客户端配置。

主要职责：从环境变量和本地环境文件加载模型连接参数。
修改日期：2026-08-26
"""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class QwenConfig:
    """Qwen 服务连接配置；API Key 缺失时客户端保持不可用状态。"""

    api_key: str | None
    base_url: str
    model: str
    # 传输层超时是兜底，不是主控。单图预算（`RecognitionService.image_timeout`，
    # 默认 50 秒）比它短，所以实际总是外层先触发；这样超时会被归类为
    # "识别超时"而不是"识别失败"，对审核员更有意义。**不要把这里调到 50 以下**
    # ——那会让 httpx 先抛异常，同一件事被报成失败。
    timeout: float = 60.0

    @property
    def available(self) -> bool:
        """返回当前配置是否具备调用模型的必要凭据。"""
        return bool(self.api_key)


# Bind local settings to this backend package, independently of the launch directory.
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
PATH_SETTINGS = {"REVIEW_LOG_DIR"}


def load_dotenv(env_file: Path | None = None) -> None:
    """Load package settings; file-relative directories and process overrides are explicit."""
    env_file = (env_file if env_file is not None else DEFAULT_ENV_FILE).resolve()
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key in PATH_SETTINGS and value:
            value = str((env_file.parent / value).resolve())
        os.environ.setdefault(key, value)


def load_qwen_config(env_file: Path | None = None) -> QwenConfig:
    """合并本地环境文件和进程环境，生成不可变的 Qwen 客户端配置。"""
    load_dotenv(env_file)
    return QwenConfig(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url=os.getenv(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ).rstrip("/"),
        model=os.getenv("DASHSCOPE_MODEL", "qwen3.7-plus"),
    )


def image_private_hosts() -> tuple[str, ...]:
    """Only the operator can explicitly allow internal image hosts, never page input."""
    return tuple(
        host.strip().lower().rstrip(".")
        for host in os.getenv("REVIEW_IMAGE_PRIVATE_HOSTS", "").split(",")
        if host.strip()
    )
