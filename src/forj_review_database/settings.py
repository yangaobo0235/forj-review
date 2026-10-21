"""One typed database configuration contract; environment overrides private dotenv."""

import os
from pathlib import Path

from psycopg.conninfo import make_conninfo
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_FILE = WORKSPACE_ROOT / ".env.database"


class ConnectionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        frozen=True,
        populate_by_name=True,
        hide_input_in_errors=True,
        env_file_encoding="utf-8-sig",
    )
    host: str = Field(default="127.0.0.1", validation_alias="PGHOST")
    port: int = Field(default=5432, ge=1, le=65535, validation_alias="PGPORT")
    database: str = Field(default="forj_review", validation_alias="PGDATABASE")
    user: str = Field(default="postgres", validation_alias="PGUSER")
    password: str = Field(min_length=1, repr=False, validation_alias="PGPASSWORD")
    sslmode: str = Field(default="prefer", validation_alias="PGSSLMODE")

    @field_validator("database")
    @classmethod
    def valid_database(cls, value):
        if not value or len(value.encode()) > 63 or "\x00" in value:
            raise ValueError("PGDATABASE 不是有效的 PostgreSQL 数据库名")
        return value

    def conninfo(self):
        return make_conninfo(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            sslmode=self.sslmode,
            dbname=self.database,
            connect_timeout=5,
        )


def load_connection_settings(path: Path | None = None):
    path = path or Path(os.getenv("DATABASE_CONFIG_FILE", str(DEFAULT_DATABASE_FILE)))
    return ConnectionSettings(_env_file=path)


def database_url_from_config() -> str:
    return load_connection_settings().conninfo()
