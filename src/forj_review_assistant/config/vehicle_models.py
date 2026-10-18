"""车型查询只读取本地 SQLite 目录，不加载任何业务数据库凭据。"""

import os
from dataclasses import dataclass
from pathlib import Path

from forj_review_assistant.config.settings import DEFAULT_ENV_FILE, load_dotenv

DEFAULT_CATALOG_PATH = (Path(__file__).resolve().parents[3] / ".review-agent"
                        / "vehicle-model-catalog" / "vehicle-model-catalog.sqlite")


@dataclass(frozen=True)
class VehicleModelCatalogSettings:
    path: Path

    @classmethod
    def from_env(cls):
        load_dotenv()
        configured = os.getenv("REVIEW_MODEL_CATALOG_PATH", "").strip()
        path = Path(configured).expanduser() if configured else DEFAULT_CATALOG_PATH
        if not path.is_absolute():
            path = DEFAULT_ENV_FILE.parent / path
        path = path.resolve()
        if not path.is_file():
            raise ValueError("离线车型目录尚未安装，请导入车型数据包并配置 REVIEW_MODEL_CATALOG_PATH")
        return cls(path)
