"""Async storage and transaction fencing tied to one live executor connection."""

import asyncio
from contextlib import asynccontextmanager, suppress

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from forj_review_config_service.contracts.models import ConfigurationError
from forj_review_config_service.integrations.database import Database


class AsyncDatabase:
    def __init__(self, database: Database):
        self.settings = database
        self.schema = database.schema
        self.pool = AsyncConnectionPool(
            database.conninfo,
            min_size=1,
            max_size=6,
            open=False,
            timeout=5,
            kwargs={"prepare_threshold": 0},
        )
        self.owner = None
        self.identity = None
        self.lost = asyncio.Event()
        self.monitor = None

    async def __aenter__(self):
        try:
            await self.pool.open(wait=True, timeout=5)
        except BaseException:
            await self.pool.close()
            raise
        return self

    async def __aexit__(self, *exc):
        self.lost.set()
        try:
            if self.monitor is not None:
                self.monitor.cancel()
                with suppress(asyncio.CancelledError):
                    await self.monitor
        finally:
            try:
                if self.owner is not None:
                    await self.owner.close()
            finally:
                await self.pool.close()

    def require_executor(self):
        if self.identity is None or self.lost.is_set():
            raise ConfigurationError("EXECUTOR_LOST", "配置执行器已失去数据库所有权，请重启配置服务", 503)

    async def acquire_executor(self):
        """Serialize takeover against every in-flight authoritative transaction."""
        if self.owner is not None:
            raise RuntimeError("配置执行器已经取得所有权")
        owner = await psycopg.AsyncConnection.connect(self.settings.conninfo, autocommit=True)
        try:
            async with owner.transaction():
                await self._fence(owner)
                cursor = await owner.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,0))",
                    (self.schema + ":configuration-executor",),
                )
                if not (await cursor.fetchone())[0]:
                    raise ValueError("此数据库配置空间已有配置服务运行，请先停止原服务")
                cursor = await owner.execute(
                    "SELECT pid,backend_start FROM pg_stat_activity WHERE pid=pg_backend_pid()"
                )
                self.identity = await cursor.fetchone()
            self.owner = owner
        except BaseException:
            await owner.close()
            raise

    async def _fence(self, connection):
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (self.schema + ":executor-fence",),
        )

    async def _assert_owner(self, connection):
        self.require_executor()
        cursor = await connection.execute(
            "SELECT 1 FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid "
            "WHERE l.locktype='advisory' AND l.granted AND l.mode='ExclusiveLock' "
            "AND l.objsubid=1 AND l.pid=%s AND a.backend_start=%s "
            "AND l.classid::bigint=((hashtextextended(%s,0)>>32)&4294967295) "
            "AND l.objid::bigint=(hashtextextended(%s,0)&4294967295)",
            (*self.identity, *([self.schema + ":configuration-executor"] * 2)),
        )
        if await cursor.fetchone() is None:
            self.lost.set()
            self.require_executor()

    @asynccontextmanager
    async def connect(self, *, write=False):
        async with self.pool.connection() as connection:
            async with connection.transaction():
                if write:
                    await self._fence(connection)
                    await self._assert_owner(connection)
                yield connection

    async def require_ready(self):
        async with self.connect() as connection:
            cursor = await connection.execute("SELECT version FROM storage_version")
            if await cursor.fetchall() != [(2,)]:
                raise ValueError("PostgreSQL 存储版本不兼容")
            for table in (
                "page_snapshots",
                "sessions",
                "captures",
                "events",
                "requests",
                "checkpoints",
                "checkpoint_blobs",
                "checkpoint_writes",
                "checkpoint_migrations",
            ):
                cursor = await connection.execute("SELECT to_regclass(%s)", (table,))
                if (await cursor.fetchone())[0] is None:
                    raise ValueError("PostgreSQL 存储结构不完整")

    def watch_executor(self, stop):
        async def watch():
            try:
                while True:
                    await asyncio.sleep(1)
                    await asyncio.wait_for(self.owner.execute("SELECT 1"), timeout=3)
            except (psycopg.Error, TimeoutError):
                self.lost.set()
                await stop()

        self.monitor = asyncio.create_task(watch(), name="configuration-executor-watch")


class ExecutorCheckpointer(AsyncPostgresSaver):
    """Fence checkpoint writes using the same transaction as their SQL updates."""

    def __init__(self, database: AsyncDatabase):
        super().__init__(database.pool)
        self.database = database

    @asynccontextmanager
    async def _cursor(self, *, pipeline=False):
        # All cursor operations are guarded, including deletion/pruning methods
        # whose upstream implementations do not mark their cursor as a pipeline.
        async with self.database.connect(write=True) as connection:
            async with connection.cursor(row_factory=dict_row, binary=True) as cursor:
                yield cursor
