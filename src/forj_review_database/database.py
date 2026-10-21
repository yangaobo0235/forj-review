"""SQLAlchemy lifecycle and short transactions; raw access is maintenance-only."""

import asyncio
import sys
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from forj_review_database.readiness import require_ready_async, require_ready_sync


class PoolSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATABASE_", extra="ignore", frozen=True)
    pool_size: int = Field(default=6, ge=1, le=64)
    pool_timeout: float = Field(default=5, gt=0)
    pool_recycle: int = Field(default=1800, ge=1)


@dataclass(frozen=True)
class Database:
    url: str = field(repr=False)
    schema: str = "local"
    application_name: str = "forj-review-service"

    def __post_init__(self):
        import re

        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", self.schema):
            raise ValueError(
                "数据库 Schema 只允许小写字母、数字和下划线，须以字母开头，最长 63 字符"
            )

    @property
    def conninfo(self):
        # Preserve service URL overrides and libpq escaping, including special passwords.
        url = self.url.replace("postgresql+psycopg://", "postgresql://", 1)
        return make_conninfo(
            url,
            connect_timeout=5,
            application_name=self.application_name,
            options=f"-c search_path={self.schema} -c statement_timeout=15000 -c lock_timeout=5000",
        )

    def connect_args(self):
        values = conninfo_to_dict(self.conninfo)
        if "port" in values:
            values["port"] = int(values["port"])
        values["connect_timeout"] = int(values["connect_timeout"])
        values["prepare_threshold"] = 0
        return values

    @contextmanager
    def connect(self, **kwargs):
        """Explicit synchronous migration/test bridge, never used by HTTP business paths.

        DDL/COPY retain a psycopg cursor; ORM operations share the exact same
        SQLAlchemy-owned transaction, so a failed import rolls back everything.
        """
        if kwargs.get("autocommit"):
            with psycopg.connect(self.conninfo, **kwargs) as raw:
                yield raw
            return
        args = self.connect_args() | kwargs
        engine = create_engine(
            URL.create("postgresql+psycopg"),
            connect_args=args,
            poolclass=NullPool,
            hide_parameters=True,
        )
        try:
            with engine.begin() as connection:
                with Session(
                    bind=connection, expire_on_commit=False, join_transaction_mode="rollback_only"
                ) as session:
                    yield MaintenanceConnection(connection.connection.driver_connection, session)
        finally:
            engine.dispose()

    def require_ready(self):
        try:
            with self.connect() as db:
                db.execute("SET TRANSACTION READ ONLY")
                require_ready_sync(db.orm, self.schema)
        except (SQLAlchemyError, psycopg.Error):
            raise ValueError("无法读取 PostgreSQL 存储，请检查数据库连接、权限及现有结构") from None


class MaintenanceConnection:
    """Raw maintenance cursor plus an ORM session on its owning transaction."""

    def __init__(self, raw, session):
        self.raw = raw
        self.orm = session

    def __getattr__(self, name):
        return getattr(self.raw, name)


class AsyncDatabase:
    """One engine per service/event loop. Never share sessions between tasks."""

    def __init__(self, settings: Database):
        self.settings = settings
        self.schema = settings.schema
        import os

        from forj_review_database.settings import DEFAULT_DATABASE_FILE

        pool = PoolSettings(_env_file=os.getenv("DATABASE_CONFIG_FILE", str(DEFAULT_DATABASE_FILE)))
        self.engine = create_async_engine(
            URL.create("postgresql+psycopg"),
            connect_args=settings.connect_args(),
            pool_size=pool.pool_size,
            max_overflow=0,
            pool_timeout=pool.pool_timeout,
            pool_recycle=pool.pool_recycle,
            pool_pre_ping=True,
            hide_parameters=True,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self, *, readonly=False, repeatable=False, timeout_ms=None):
        async with self.sessions() as session:
            async with session.begin():
                options = {"postgresql_readonly": readonly}
                if repeatable:
                    options["isolation_level"] = "REPEATABLE READ"
                await session.connection(execution_options=options)
                if timeout_ms is not None:
                    await session.execute(
                        select(func.set_config("statement_timeout", str(timeout_ms), True))
                    )
                yield session

    async def require_ready(self):
        if sys.platform == "win32" and isinstance(
            asyncio.get_running_loop(), asyncio.ProactorEventLoop
        ):
            raise ValueError(
                "Windows 的 PostgreSQL 异步连接需要 SelectorEventLoop；"
                "请使用项目服务入口启动，或在 uvicorn 参数中添加 "
                "--loop asyncio:SelectorEventLoop"
            )
        try:
            async with self.session(readonly=True) as session:
                await require_ready_async(session, self.schema)
        except SQLAlchemyError:
            raise ValueError("无法读取 PostgreSQL 存储，请检查数据库连接、权限及现有结构") from None
