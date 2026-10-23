"""Read exact engine models from the configured schema's reference table."""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from forj_review_database.database import AsyncDatabase
from forj_review_database.models import EngineEmission
from forj_review_service.contracts.engine_emissions import (
    EMISSION_STANDARDS,
    EngineEmissionLookupUnavailable,
    engine_model_key,
)


async def lookup_engine_emissions_async(
    models: Iterable[str], *, database: AsyncDatabase
) -> dict[str, str]:
    keys = sorted({engine_model_key(value) for value in models} - {""})
    if not keys:
        return {}
    try:
        async with database.session(readonly=True) as session:
            rows = (
                await session.execute(
                    select(EngineEmission.engine_model, EngineEmission.emission_standard).where(
                        EngineEmission.engine_model.in_(keys)
                    )
                )
            ).all()
    except SQLAlchemyError:
        raise EngineEmissionLookupUnavailable("发动机排放参考表不可读取，请检查数据库") from None
    if any(standard not in EMISSION_STANDARDS for _, standard in rows):
        raise EngineEmissionLookupUnavailable("发动机排放参考表存在无效标准，请人工复核")
    return dict(rows)
