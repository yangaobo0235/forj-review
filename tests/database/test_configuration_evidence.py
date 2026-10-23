"""Completed proofs, immutable aggregates and stale workflow fencing."""

from copy import deepcopy

import psycopg
import pytest

from forj_review_config_service.contracts.models import ConfigurationError
from forj_review_config_service.integrations.configuration_store.async_postgres import (
    AsyncTaskStore,
)
from tests.support.configuration import candidate
from tests.support.postgres import seed_complete_pages


@pytest.mark.asyncio
async def test_preview_cannot_be_saved_as_completed_validation(async_database, settings):
    kernel, record = candidate(settings)
    record.report = kernel.validate(record)
    with pytest.raises(ConfigurationError) as rejected:
        await AsyncTaskStore(async_database).put(record)
    assert rejected.value.code == "INCOMPLETE_VALIDATION"


@pytest.mark.asyncio
async def test_same_revision_draft_and_existing_proof_cannot_be_overwritten(
    async_database, settings
):
    _, record = candidate(settings)
    store = AsyncTaskStore(async_database)
    await store.put(record)
    changed = deepcopy(record)
    changed.draft["title"] = "unversioned overwrite"
    with pytest.raises(ConfigurationError) as rejected:
        await store.put(changed)
    assert rejected.value.code == "STALE_REVISION"
    changed = deepcopy(record)
    changed.report["candidate_digest"] = "a" * 64
    with pytest.raises(ConfigurationError) as rejected:
        await store.put(changed)
    assert rejected.value.code == "STALE_VALIDATION"
    saved = await store.get(record.task_id)
    assert (
        saved.draft == record.draft
        and saved.report["candidate_digest"] == record.report["candidate_digest"]
    )


@pytest.mark.parametrize(
    "member,parent,key",
    [
        ("cfg_page_field", "cfg_page_version", "page_version_id"),
        ("cfg_page_material", "cfg_page_version", "page_version_id"),
        ("cfg_release_item", "cfg_release", "release_id"),
    ],
)
def test_committed_aggregate_rejects_late_append(database, member, parent, key):
    seed_complete_pages(database)
    with pytest.raises(psycopg.errors.RaiseException), database.connect() as db:
        columns = [
            r[0]
            for r in db.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema=%s "
                "AND table_name=%s AND column_name<>'id' ORDER BY ordinal_position",
                (database.schema, member),
            ).fetchall()
        ]
        from psycopg import sql

        names = sql.SQL(",").join(map(sql.Identifier, columns))
        # BEFORE INSERT must reject even if uniqueness would reject the copied row later.
        db.execute(
            sql.SQL("INSERT INTO {} ({}) SELECT {} FROM {} LIMIT 1").format(
                sql.Identifier(member), names, names, sql.Identifier(member)
            )
        )
