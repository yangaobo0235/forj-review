"""Read standard libpq PG* connection variables from a private file or environment."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from psycopg.conninfo import make_conninfo

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_FILE = WORKSPACE_ROOT / ".env.database"
SETTING_NAMES = (
    "PGHOST", "PGPORT", "PGDATABASE", "PGUSER",
    "PGPASSWORD", "PGSSLMODE",
)


@dataclass(frozen=True)
class ConnectionSettings:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)
    sslmode: str = "prefer"

    def conninfo(self):
        # libpq escaping handles @, #, %, quotes and backslashes in passwords.
        return make_conninfo(host=self.host, port=self.port, user=self.user,
                             password=self.password, sslmode=self.sslmode,
                             dbname=self.database,
                             connect_timeout=5)


def load_connection_settings(path: Path | None = None):
    path = path or Path(os.getenv("DATABASE_CONFIG_FILE", str(DEFAULT_DATABASE_FILE)))
    values = {}
    if path.is_file():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, separator, value = line.partition("=")
            if separator and key.strip() in SETTING_NAMES:
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                values[key.strip()] = value
    values.update({key: os.environ[key] for key in SETTING_NAMES if key in os.environ})
    if not values.get("PGPASSWORD"):
        raise ValueError(f"请在 {path} 填写 PGPASSWORD；系统仅支持 PostgreSQL")
    try:
        port = int(values.get("PGPORT", "5432"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError("PGPORT 必须是 1 到 65535 之间的端口") from None
    database = values.get("PGDATABASE", "forj_review")
    if not database or len(database.encode()) > 63 or "\x00" in database:
        raise ValueError("PGDATABASE 不是有效的 PostgreSQL 数据库名")
    return ConnectionSettings(
        host=values.get("PGHOST", "127.0.0.1"), port=port,
        database=database, user=values.get("PGUSER", "postgres"),
        password=values["PGPASSWORD"],
        sslmode=values.get("PGSSLMODE", "prefer"),
    )


def database_url_from_config() -> str:
    return load_connection_settings().conninfo()
