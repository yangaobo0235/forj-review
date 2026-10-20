"""PostgreSQL connections, transactions and storage readiness for this service."""

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field

import psycopg
from psycopg.conninfo import make_conninfo

from forj_review_service.config.database import database_url_from_config
from forj_review_service.config.settings import load_dotenv


@dataclass(frozen=True)
class Database:
    url: str = field(repr=False)
    schema: str = "public"

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", self.schema):
            raise ValueError("数据库 Schema 只允许小写字母、数字和下划线，须以字母开头，最长 63 字符")

    @property
    def conninfo(self):
        return make_conninfo(
            self.url, connect_timeout=5, application_name="forj-review-service",
            options=f"-c search_path={self.schema} -c statement_timeout=15000 -c lock_timeout=5000",
        )

    @contextmanager
    def connect(self, **kwargs):
        with psycopg.connect(self.conninfo, **kwargs) as connection:
            yield connection

    def require_ready(self):
        """Validate existing storage; normal service startup never creates database objects."""
        try:
            with self.connect() as db:
                db.execute("SET TRANSACTION READ ONLY")
                if db.execute("SELECT version FROM storage_version").fetchall() != [(2,)]:
                    raise ValueError("PostgreSQL 存储版本不兼容")
                tables = ("page_snapshots", "sessions", "captures", "events", "requests",
                          "checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations",
                          "engine_emission_standards")
                if any(db.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None
                       for table in tables):
                    raise ValueError("PostgreSQL 存储结构不完整")
        except psycopg.Error:
            raise ValueError("无法读取 PostgreSQL 存储，请检查数据库连接、权限及现有结构") from None


def review_database() -> Database:
    load_dotenv()
    url = os.getenv("REVIEW_DATABASE_URL", "").strip() or database_url_from_config()
    return Database(url, os.getenv("REVIEW_DATABASE_SCHEMA", "public"))
