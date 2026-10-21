"""Synthetic schemas for isolated PostgreSQL tests; never used by application startup."""

from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import sql

from forj_review_config_service.integrations.database import Database


def complete_test_pages():
    from tests.support.pages import configuration_state

    return configuration_state()


def seed_complete_pages(database):
    from forj_review_service.integrations.storage.configuration import publish_state

    with database.connect() as db:
        publish_state(db, complete_test_pages(), operation_id="testfixture")


def publish_validated_test_state(db, state, **kwargs):
    """Synthetic completed proof for relational constraints, not workflow validation."""
    from datetime import UTC, datetime

    from forj_review_database.models import ValidationRun
    from forj_review_service.businesses.page_config.inspection import digest
    from forj_review_service.businesses.page_config.kernel import KERNEL_VERSION
    from forj_review_service.integrations.storage.configuration import publish_state

    identifier = uuid4().hex
    now = datetime.now(UTC)
    db.orm.add(ValidationRun(
        id=identifier, purpose=kwargs.get("operation_type", "PUBLISH"),
        base_release_id=state.release_id,
        manifest_digest=digest({p: digest(state.source(p, v)) for p, v in sorted(state.active.items())}),
        digest_version="manifest-v1", kernel_version=KERNEL_VERSION, kernel_fingerprint=KERNEL_VERSION,
        report_schema_version="1.0", status="PASSED",
        report={"checks": [], "collection": None, "replays": [], "summary": {}},
        performed_by="synthetic-storage-test", started_at=now, completed_at=now,
    ))
    db.orm.flush()
    return publish_state(db, state, validation_id=identifier, **kwargs)


def initialize(database):
    from forj_review_service.integrations.storage.migrations import initialize_schema

    with database.connect() as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(database.schema)))
        db.execute("CREATE TABLE forj_vehicle_brand(id bigint PRIMARY KEY)")
        db.execute("CREATE TABLE forj_vehicle_series(id bigint PRIMARY KEY)")
        db.execute("CREATE TABLE forj_vehicle_model(model_id bigint PRIMARY KEY)")
        db.execute("CREATE TABLE forj_vehicle_model_config(model_id bigint PRIMARY KEY)")
        initialize_schema(db)
    with database.connect(autocommit=True) as db:
        PostgresSaver(db).setup()


@contextmanager
def isolated_database(url):
    database = Database(url, "forj_test_" + uuid4().hex[:16])
    initialize(database)
    try:
        yield database
    finally:
        with psycopg.connect(url, autocommit=True) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(database.schema)))


def read_page_state(directory: Path):
    """Load complete synthetic pages for database and kernel tests."""
    from forj_review_service.businesses.page_config.loader import load_page_profiles
    from forj_review_service.businesses.page_config.state import ConfigurationState

    pages = load_page_profiles(directory)
    return ConfigurationState(
        tuple(d.model_dump(mode="json") for _, d in pages.sources),
        {p.page_id: p.version for p in pages.active},
    )
