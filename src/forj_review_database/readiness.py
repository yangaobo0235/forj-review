"""Read-only ORM checks against the immutable migration contract."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from forj_review_database.migrations import migration_history, validate_history
from forj_review_database.models import SchemaMigration
from forj_review_database.schema import TABLES


def require_ready_sync(session: Session, schema: str) -> None:
    # Qualified lookup is intentional: absent local tables never resolve via public.
    missing = []
    for table in TABLES:
        if session.scalar(select(func.to_regclass(schema + "." + table))) is None:
            missing.append(table)
    if missing:
        raise ValueError("PostgreSQL 存储结构不完整，不会回退 public：" + ",".join(missing))
    rows = session.execute(
        select(SchemaMigration.version, SchemaMigration.checksum).order_by(SchemaMigration.version)
    ).all()
    validate_history(rows, migration_history(), require_current=True)


async def require_ready_async(session: AsyncSession, schema: str) -> None:
    await session.run_sync(require_ready_sync, schema)
