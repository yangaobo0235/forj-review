"""v6 preserves v5 scenes and backfills current and archived evidence references."""

from forj_review_database import migrations
from forj_review_service.interfaces.database_tools import migrate
from tests.support.postgres import isolated_database, seed_complete_pages


def test_v5_upgrade_preserves_scene_and_release_without_inventing_saved_images(
    database, tmp_path, monkeypatch
):
    original_upgrades = migrations.UPGRADES
    with monkeypatch.context() as legacy_registry:
        legacy_registry.setattr(migrations, "UPGRADES", migrations.UPGRADES[:1])
        with isolated_database(database.url) as legacy:
            seed_complete_pages(legacy)
            with legacy.connect() as db:
                db.execute("""
                    INSERT INTO cfg_capture
                      (id,extension_id,snapshot_id,page_instance_id,page_url,title,structure_digest,
                       snapshot_schema_version,snapshot,created_by,expires_at)
                    SELECT id,repeat('a',32),id,'instance','https://example.test/record','synthetic',
                           'structure','1.0',
                           '{"fields":[],"samples":[{"sample_id":"license","url":"https://expired.example.test/material.png"}]}',
                           'synthetic',now()+interval '7 days'
                    FROM (VALUES ('old'),('current')) captures(id)
                """)
                db.execute("""
                    INSERT INTO cfg_session
                      (id,page_id,capture_id,base_page_version_id,base_release_id,baseline_digest,
                       state,turn_status,revision,draft,draft_digest,workflow_schema_version,
                       workflow,created_by)
                    SELECT 'task',p.id,'current',v.id,h.release_id,repeat('0',64),
                           'DRAFTING','COMPLETED',0,'{}',repeat('0',64),'1.0',
                           '{"archive":{"capture_id":"old"}}','synthetic'
                    FROM cfg_head h JOIN cfg_release_item i ON i.release_id=h.release_id
                    JOIN cfg_page p ON p.id=i.page_id JOIN cfg_page_version v ON v.id=i.page_version_id
                    LIMIT 1
                """)
                scenes = db.execute("SELECT id,snapshot FROM cfg_capture ORDER BY id").fetchall()
                releases = db.execute(
                    "SELECT id,manifest_digest FROM cfg_release ORDER BY id"
                ).fetchall()
            legacy_registry.setattr(
                migrations, "UPGRADES", migrations.UPGRADES + (original_upgrades[1],)
            )
            result = migrate(legacy, backup_dir=tmp_path / "v5-backup")
            assert result["version"] == 6
            legacy.require_ready()
            with legacy.connect() as db:
                assert (
                    db.execute("SELECT id,snapshot FROM cfg_capture ORDER BY id").fetchall()
                    == scenes
                )
                assert (
                    db.execute("SELECT id,manifest_digest FROM cfg_release ORDER BY id").fetchall()
                    == releases
                )
                assert db.execute(
                    "SELECT session_id,capture_id FROM cfg_session_capture ORDER BY capture_id"
                ).fetchall() == [("task", "current"), ("task", "old")]
                assert db.execute("SELECT count(*) FROM cfg_capture_image").fetchone() == (0,)
