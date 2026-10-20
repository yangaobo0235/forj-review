"""Read the engine emission reference table without manufacturer or suffix inference."""

from collections.abc import Iterable
from threading import BoundedSemaphore

import psycopg

from forj_review_service.contracts.engine_emissions import (
    EMISSION_STANDARDS,
    EngineEmissionLookupUnavailable,
    engine_model_key,
)
from forj_review_service.integrations.database import Database, review_database

_CONNECTIONS = BoundedSemaphore(4)

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS engine_emission_standards (
    engine_model TEXT PRIMARY KEY CHECK (length(engine_model)>0),
    emission_standard TEXT NOT NULL CHECK (emission_standard IN ('国三','国四','国五','国六')),
    source_config_count BIGINT NOT NULL CHECK (source_config_count>0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def create_reference_table(connection):
    """Explicit maintenance/test operation; never called during normal review or startup."""
    connection.execute(TABLE_DDL)
    connection.execute("COMMENT ON TABLE engine_emission_standards IS "
                       "'发动机完整型号与唯一国三至国六排放值的参考映射，由车型目录显式生成'")
    for column, comment in (
        ("engine_model", "规范化的完整发动机型号；去格式及前置品牌文字，保留全部型号后缀"),
        ("emission_standard", "目录中该型号唯一的国三、国四、国五或国六值；不是厂家认证结论"),
        ("source_config_count", "生成时支持该排放值的源车型配置条数；不表示独立验证次数"),
        ("created_at", "该映射批次的导入时间"),
    ):
        connection.execute(psycopg.sql.SQL("COMMENT ON COLUMN engine_emission_standards.{} IS {}")
                           .format(psycopg.sql.Identifier(column), psycopg.sql.Literal(comment)))


def lookup_engine_emissions(
    models: Iterable[str], *, database: Database | None = None,
) -> dict[str, str]:
    keys = sorted({engine_model_key(model) for model in models} - {""})
    if not keys:
        return {}
    if not _CONNECTIONS.acquire(timeout=1):
        raise EngineEmissionLookupUnavailable("发动机排放对应表查询繁忙，请稍后重试")
    try:
        with (database or review_database()).connect() as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            rows = connection.execute(
                "SELECT engine_model,emission_standard FROM engine_emission_standards "
                "WHERE engine_model=ANY(%s)", (keys,),
            ).fetchall()
        if any(standard not in EMISSION_STANDARDS for _, standard in rows):
            raise EngineEmissionLookupUnavailable("发动机排放对应表包含无效值，请维护参考数据")
        return dict(rows)
    except (psycopg.Error, OSError, ValueError):
        raise EngineEmissionLookupUnavailable("发动机排放对应表读取失败，请检查数据库或完成参考表导入") from None
    finally:
        _CONNECTIONS.release()
