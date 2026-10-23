"""Exact reference lookup uses an isolated PostgreSQL schema, never local/public."""

import pytest

from forj_review_database.database import AsyncDatabase
from forj_review_database.models import EngineEmission
from forj_review_database.readiness import require_ready_sync
from forj_review_service.contracts.engine_emissions import EngineEmissionLookupUnavailable
from forj_review_service.integrations.engine_emissions import lookup_engine_emissions_async


@pytest.mark.asyncio
async def test_lookup_reads_exact_reference_rows_and_does_not_change_data(database):
    async with AsyncDatabase(database) as runtime:
        async with runtime.session() as session:
            session.add(
                EngineEmission(
                    engine_model="SYNTHETIC-E6", emission_standard="国六", source_config_count=1
                )
            )
        assert await lookup_engine_emissions_async(
            ["合成 ＳＹＮＴＨＥＴＩＣ－Ｅ６", "SYNTHETIC-E6", "SYNTHETIC-E6A"], database=runtime
        ) == {"SYNTHETIC-E6": "国六"}
        async with runtime.session(readonly=True) as session:
            row = await session.get(EngineEmission, "SYNTHETIC-E6")
            assert row.emission_standard == "国六"
            assert row.source_config_count == 1


@pytest.mark.asyncio
async def test_missing_reference_table_blocks_readiness_and_is_not_a_lookup_miss(database):
    with database.connect() as connection:
        connection.execute("DROP TABLE ref_engine_emission")
    with database.connect() as connection:
        with pytest.raises(ValueError, match="ref_engine_emission"):
            require_ready_sync(connection.orm, database.schema)
    async with AsyncDatabase(database) as runtime:
        with pytest.raises(EngineEmissionLookupUnavailable):
            await lookup_engine_emissions_async(["SYNTHETIC-E6"], database=runtime)
