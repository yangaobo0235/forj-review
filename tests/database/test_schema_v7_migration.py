"""Reset only agent work; keep immutable publication and protected source rows."""

import psycopg
import pytest

from forj_review_database import migrations
from forj_review_service.interfaces.database_tools import migrate, table_fingerprint
from tests.support.postgres import isolated_database, seed_complete_pages

FORMAL = (
    "cfg_page",
    "cfg_page_version",
    "cfg_page_field",
    "cfg_page_material",
    "cfg_release",
    "cfg_release_item",
    "cfg_head",
    "cfg_validation_run",
)


@pytest.mark.parametrize("confirmed", [False, True])
def test_v6_reset_preserves_all_formal_rows_and_refuses_unreconciled_publication(
    database, tmp_path, monkeypatch, confirmed
):
    upgrades = migrations.UPGRADES
    with monkeypatch.context() as previous:
        previous.setattr(migrations, "UPGRADES", upgrades[:2])
        with isolated_database(database.url) as legacy:
            seed_complete_pages(legacy)
            with legacy.connect() as db:
                db.execute("""INSERT INTO cfg_capture
                    (id,extension_id,snapshot_id,page_instance_id,page_url,title,structure_digest,snapshot_schema_version,snapshot,created_by,expires_at)
                    VALUES('old',repeat('a',32),'old','instance','https://example.test/record','synthetic','structure','1.0','{}','test',now()+interval '7 days')""")
                db.execute(
                    """INSERT INTO cfg_session
                    (id,page_id,capture_id,base_page_version_id,base_release_id,baseline_digest,state,turn_status,revision,draft,draft_digest,workflow_schema_version,workflow,created_by)
                    SELECT 'old',p.id,'old',v.id,h.release_id,repeat('0',64),'DRAFTING','COMPLETED',0,'{}',repeat('0',64),'1.0',%s::jsonb,'test'
                    FROM cfg_head h JOIN cfg_release_item i ON i.release_id=h.release_id
                    JOIN cfg_page p ON p.id=i.page_id JOIN cfg_page_version v ON v.id=i.page_version_id LIMIT 1""",
                    (
                        '{"pending":{"kind":"confirmation","confirmed":true}}'
                        if confirmed
                        else "{}",
                    ),
                )
                before = {t: table_fingerprint(db, legacy.schema, t) for t in FORMAL}
            previous.setattr(migrations, "UPGRADES", upgrades)
            if confirmed:
                with pytest.raises(psycopg.errors.RaiseException, match="Reconcile"):
                    migrate(legacy, backup_dir=tmp_path / "blocked")
                with legacy.connect() as db:
                    assert db.execute("SELECT count(*) FROM cfg_session").fetchone() == (1,)
                    assert db.execute(
                        "SELECT max(version) FROM sys_schema_migration"
                    ).fetchone() == (6,)
                return
            assert migrate(legacy, backup_dir=tmp_path / "reset")["version"] == 7
            legacy.require_ready(consumer="configuration")
            with legacy.connect() as db:
                assert {t: table_fingerprint(db, legacy.schema, t) for t in FORMAL} == before
                for table in (
                    "cfg_task",
                    "cfg_capture",
                    "cfg_task_run",
                    "cfg_task_interaction",
                    "cfg_task_requirement",
                ):
                    assert db.execute("SELECT count(*) FROM " + table).fetchone() == (0,)
                assert db.execute(
                    "SELECT to_regclass(%s)", (legacy.schema + ".cfg_capture_image",)
                ).fetchone() == (None,)
