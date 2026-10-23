"""A populated v4 schema upgrades without changing releases or inventing legacy intent."""

import pytest
from psycopg.errors import CheckViolation

from forj_review_database import migrations
from forj_review_service.interfaces.database_tools import migrate
from tests.support.postgres import isolated_database, seed_complete_pages


def test_v4_completed_evidence_and_release_history_survive_v5(database, tmp_path, monkeypatch):
    with monkeypatch.context() as baseline:
        baseline.setattr(migrations, "UPGRADES", ())
        with isolated_database(database.url) as legacy:
            seed_complete_pages(legacy)
            with legacy.connect() as db:
                # The v4 manual CLI saved a page and digest without a revision.
                db.execute("""
                    INSERT INTO cfg_validation_run
                      (id,purpose,candidate_page_id,candidate_digest,manifest_digest,
                       digest_version,kernel_version,kernel_fingerprint,report_schema_version,
                       status,report,performed_by,started_at,completed_at)
                    SELECT 'legacy-manual','PUBLISH',1,candidate,
                           manifest_digest,digest_version,kernel_version,kernel_fingerprint,
                           report_schema_version,status,report,'manual-cli',started_at,completed_at
                    FROM cfg_validation_run CROSS JOIN (SELECT repeat('0',64) AS candidate) x
                    LIMIT 1
                """)
                before = db.execute(
                    "SELECT id,manifest_digest,validation_run_id FROM cfg_release ORDER BY id"
                ).fetchall()
                evidence = db.execute(
                    "SELECT id,report,kernel_version,expires_at FROM cfg_validation_run ORDER BY id"
                ).fetchall()
            baseline.undo()
            result = migrate(legacy, backup_dir=tmp_path / "v4-backup")
            assert result["version"] == migrations.migration_history()[-1].version
            legacy.require_ready()
            with legacy.connect() as db:
                assert (
                    db.execute(
                        "SELECT id,manifest_digest,validation_run_id FROM cfg_release ORDER BY id"
                    ).fetchall()
                    == before
                )
                assert (
                    db.execute(
                        "SELECT id,report,kernel_version,expires_at FROM cfg_validation_run ORDER BY id"
                    ).fetchall()
                    == evidence
                )
                assert (
                    db.execute(
                        "SELECT intent_digest,verification_context,publish_valid_until FROM cfg_validation_run"
                    ).fetchall()
                    == [(None, {"schema_version": "legacy"}, None)] * 2
                )
                assert db.execute(
                    "SELECT candidate_revision FROM cfg_validation_run WHERE id='legacy-manual'"
                ).fetchone() == (None,)
            with pytest.raises(CheckViolation), legacy.connect() as db:
                db.execute(
                    "INSERT INTO cfg_validation_run SELECT 'invalid-new',session_id,purpose,base_release_id,candidate_page_id,candidate_revision,candidate_digest,manifest_digest,digest_version,scene_digest,kernel_version,kernel_fingerprint,report_schema_version,status,report,performed_by,started_at,completed_at,expires_at,intent_digest,verification_context,publish_valid_until FROM cfg_validation_run WHERE id='legacy-manual'"
                )
