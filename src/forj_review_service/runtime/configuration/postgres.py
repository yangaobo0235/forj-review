"""Load a validated in-memory page snapshot from PostgreSQL at startup."""

from forj_review_service.businesses.registry import BusinessRegistry
from forj_review_service.integrations.database import Database
from forj_review_service.runtime.configuration.service import validated_snapshot


def load_registry(database: Database) -> BusinessRegistry:
    with database.connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        row = connection.execute("SELECT files FROM page_snapshots WHERE id=1").fetchone()
    if row is None:
        raise ValueError("PostgreSQL 页面配置快照缺失，请核对数据库与配置空间")
    _, registry = validated_snapshot(row[0])
    return registry
