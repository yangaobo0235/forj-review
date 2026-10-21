"""Async storage and transaction fencing tied to one live executor connection."""

import asyncio
from contextlib import asynccontextmanager, suppress

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from sqlalchemy import BigInteger, column, func, select, table

from forj_review_config_service.contracts.models import ConfigurationError
from forj_review_config_service.integrations.database import Database
from forj_review_database.database import AsyncDatabase as SharedAsyncDatabase


class AsyncDatabase(SharedAsyncDatabase):
    def __init__(self, database: Database):
        super().__init__(database)
        self.owner = None
        self.identity = None
        self.lost = asyncio.Event()
        self.monitor = None

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
                await self.engine.dispose()

    def require_executor(self):
        if self.identity is None or self.lost.is_set():
            raise ConfigurationError(
                "EXECUTOR_LOST", "配置执行器已失去数据库所有权，请重启配置服务", 503
            )

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

    @asynccontextmanager
    async def session(self, *, write=False, readonly=False, repeatable=False, timeout_ms=None):
        async with super().session(
            readonly=readonly, repeatable=repeatable, timeout_ms=timeout_ms
        ) as session:
            if write:
                await self._guard_session(session)
            yield session

    async def _guard_session(self, session):
        self.require_executor()
        await session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(self.schema + ":executor-fence", 0)
                )
            )
        )
        locks = table(
            "pg_locks",
            *(
                column(n)
                for n in ("pid", "locktype", "granted", "mode", "objsubid", "classid", "objid")
            ),
        )
        activity = table("pg_stat_activity", column("pid"), column("backend_start"))
        key = func.hashtextextended(self.schema + ":configuration-executor", 0)
        found = await session.scalar(
            select(1)
            .select_from(locks.join(activity, activity.c.pid == locks.c.pid))
            .where(
                locks.c.locktype == "advisory",
                locks.c.granted,
                locks.c.mode == "ExclusiveLock",
                locks.c.objsubid == 1,
                locks.c.pid == self.identity[0],
                activity.c.backend_start == self.identity[1],
                locks.c.classid.cast(BigInteger) == key.op(">>")(32).op("&")(4294967295),
                locks.c.objid.cast(BigInteger) == key.op("&")(4294967295),
            )
        )
        if found is None:
            self.lost.set()
            self.require_executor()

    @asynccontextmanager
    async def checkpoint_connection(self):
        """The official checkpointer borrows this session's physical connection.

        Fence validation and checkpoint cursor writes commit in one transaction;
        no second driver pool or detached checkpoint transaction is introduced.
        """
        async with self.session(write=True) as session:
            connection = await session.connection()
            raw = await connection.get_raw_connection()
            yield raw.driver_connection

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
        super().__init__(database.owner)
        self.database = database

    @asynccontextmanager
    async def _cursor(self, *, pipeline=False):
        # All cursor operations are guarded, including deletion/pruning methods
        # whose upstream implementations do not mark their cursor as a pipeline.
        async with self.database.checkpoint_connection() as connection:
            async with connection.cursor(row_factory=dict_row, binary=True) as cursor:
                yield cursor
