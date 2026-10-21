CREATE TABLE sys_schema_migration (
    version integer PRIMARY KEY,
    name text NOT NULL,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE cfg_page (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    page_code text NOT NULL UNIQUE CHECK (btrim(page_code)<>''),
    business_type text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE cfg_page_version (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    page_id bigint NOT NULL REFERENCES cfg_page(id),
    version_no text NOT NULL CHECK (btrim(version_no)<>''),
    base_page_version_id bigint,
    schema_version text NOT NULL CHECK (schema_version='2.0'),
    digest_version text NOT NULL CHECK (digest_version='page-v2'),
    title text NOT NULL,
    region_code text NOT NULL,
    enabled boolean NOT NULL,
    page_options jsonb NOT NULL CHECK (jsonb_typeof(page_options)='object'
        AND NOT (page_options ?| ARRAY['page_id','version','active','enabled','fields','materials','title','schema_version','business_type','region'])),
    content_digest text NOT NULL CHECK (content_digest ~ '^[a-f0-9]{64}$'),
    change_reason text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(page_id,version_no), UNIQUE(page_id,id),
    FOREIGN KEY(page_id,base_page_version_id) REFERENCES cfg_page_version(page_id,id)
);
CREATE INDEX idx_page_version_history ON cfg_page_version(page_id,id DESC);
CREATE TABLE cfg_page_field (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    page_version_id bigint NOT NULL REFERENCES cfg_page_version(id),
    field_key text NOT NULL,
    label text NOT NULL,
    sort_order integer NOT NULL CHECK (sort_order>=0),
    reviewable boolean NOT NULL,
    required boolean NOT NULL,
    writable boolean NOT NULL,
    normalizer text NOT NULL,
    options jsonb NOT NULL CHECK (jsonb_typeof(options)='object'
        AND NOT (options ?| ARRAY['key','label','reviewable','required','writable','normalizer'])),
    UNIQUE(page_version_id,field_key), UNIQUE(page_version_id,sort_order)
);
CREATE TABLE cfg_page_material (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    page_version_id bigint NOT NULL REFERENCES cfg_page_version(id),
    material_code text NOT NULL,
    label text NOT NULL,
    sort_order integer NOT NULL CHECK (sort_order>=0),
    guidance text NOT NULL DEFAULT '',
    options jsonb NOT NULL CHECK (jsonb_typeof(options)='object'
        AND NOT (options ?| ARRAY['document_type','label','required','minimum_count','guidance'])),
    UNIQUE(page_version_id,material_code), UNIQUE(page_version_id,sort_order)
);
CREATE TABLE cfg_capture (
    id text PRIMARY KEY,
    extension_id text NOT NULL,
    snapshot_id text NOT NULL,
    page_instance_id text NOT NULL,
    page_url text NOT NULL,
    title text NOT NULL,
    structure_digest text NOT NULL,
    snapshot_schema_version text NOT NULL,
    snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot)='object'),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at timestamptz NOT NULL DEFAULT (CURRENT_TIMESTAMP + INTERVAL '7 days')
);
CREATE TABLE cfg_session (
    id text PRIMARY KEY,
    page_id bigint NOT NULL REFERENCES cfg_page(id),
    capture_id text REFERENCES cfg_capture(id),
    base_page_version_id bigint NOT NULL,
    base_release_id bigint NOT NULL,
    baseline_digest text NOT NULL,
    state text NOT NULL CHECK (state IN ('DRAFTING','NEEDS_INPUT','READY_FOR_CONFIRMATION',
        'PUBLISHED','BLOCKED','CLOSED')),
    turn_status text NOT NULL CHECK (turn_status IN ('COMPLETED','RUNNING','QUEUED','WAITING',
        'INTERRUPTED','CANCELLED','FAILED')),
    revision bigint NOT NULL CHECK (revision>=0),
    draft jsonb NOT NULL CHECK (jsonb_typeof(draft)='object'),
    draft_digest text NOT NULL CHECK (draft_digest ~ '^[a-f0-9]{64}$'),
    workflow_schema_version text NOT NULL DEFAULT '1.0',
    workflow jsonb NOT NULL CHECK (jsonb_typeof(workflow)='object'),
    current_validation_run_id text,
    published_release_id bigint,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at timestamptz,
    expires_at timestamptz,
    FOREIGN KEY(page_id,base_page_version_id) REFERENCES cfg_page_version(page_id,id)
);
CREATE TABLE cfg_release (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    release_no text NOT NULL UNIQUE,
    base_release_id bigint REFERENCES cfg_release(id),
    rollback_target_release_id bigint REFERENCES cfg_release(id),
    operation_type text NOT NULL CHECK (operation_type IN ('IMPORT','PUBLISH','ROLLBACK')),
    source text NOT NULL CHECK (source IN ('MANUAL','ASSISTANT','MIGRATION')),
    source_session_id text,
    operation_id text NOT NULL UNIQUE,
    request_digest text NOT NULL,
    manifest_digest text NOT NULL,
    digest_version text NOT NULL CHECK (digest_version='manifest-v1'),
    validation_run_id text NOT NULL,
    minimum_kernel_version text NOT NULL,
    created_by text NOT NULL,
    confirmed_by text NOT NULL,
    confirmed_at timestamptz NOT NULL,
    change_reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE cfg_session ADD FOREIGN KEY(base_release_id) REFERENCES cfg_release(id);
ALTER TABLE cfg_session ADD FOREIGN KEY(published_release_id) REFERENCES cfg_release(id);
CREATE TABLE cfg_validation_run (
    id text PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 160),
    session_id text,
    purpose text NOT NULL CHECK (purpose IN ('PUBLISH','ROLLBACK','IMPORT')),
    base_release_id bigint REFERENCES cfg_release(id),
    candidate_page_id bigint REFERENCES cfg_page(id),
    candidate_revision bigint CHECK (candidate_revision>=0),
    candidate_digest text CHECK (candidate_digest ~ '^[a-f0-9]{64}$'),
    manifest_digest text NOT NULL CHECK (manifest_digest ~ '^[a-f0-9]{64}$'),
    digest_version text NOT NULL CHECK (digest_version='manifest-v1'),
    scene_digest text,
    kernel_version text NOT NULL,
    kernel_fingerprint text NOT NULL,
    report_schema_version text NOT NULL CHECK (report_schema_version='1.0'),
    status text NOT NULL CHECK (status IN ('PASSED','FAILED','ERROR')),
    report jsonb NOT NULL CHECK (jsonb_typeof(report)='object' AND octet_length(report::text)<=262144),
    performed_by text NOT NULL,
    started_at timestamptz NOT NULL,
    completed_at timestamptz NOT NULL CHECK (completed_at>=started_at),
    expires_at timestamptz
);
ALTER TABLE cfg_release ADD FOREIGN KEY(validation_run_id) REFERENCES cfg_validation_run(id);
ALTER TABLE cfg_session ADD FOREIGN KEY(current_validation_run_id) REFERENCES cfg_validation_run(id);
CREATE INDEX idx_validation_session ON cfg_validation_run(session_id,completed_at DESC);
CREATE INDEX idx_validation_base ON cfg_validation_run(base_release_id);
CREATE INDEX idx_validation_expiry ON cfg_validation_run(expires_at);
CREATE INDEX idx_release_base ON cfg_release(base_release_id);
CREATE INDEX idx_release_rollback ON cfg_release(rollback_target_release_id);
CREATE INDEX idx_release_session ON cfg_release(source_session_id);
CREATE INDEX idx_release_validation ON cfg_release(validation_run_id);
CREATE TABLE cfg_release_item (
    release_id bigint NOT NULL REFERENCES cfg_release(id),
    page_id bigint NOT NULL REFERENCES cfg_page(id),
    page_version_id bigint NOT NULL,
    PRIMARY KEY(release_id,page_id),
    FOREIGN KEY(page_id,page_version_id) REFERENCES cfg_page_version(page_id,id)
);
CREATE INDEX idx_release_item_version ON cfg_release_item(page_version_id);
CREATE TABLE cfg_head (
    id integer PRIMARY KEY CHECK (id=1),
    release_id bigint REFERENCES cfg_release(id),
    revision bigint NOT NULL CHECK (revision>=0),
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO cfg_head(id,release_id,revision) VALUES(1,NULL,0);
CREATE TABLE cfg_session_event (
    seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id text NOT NULL REFERENCES cfg_session(id),
    kind text NOT NULL CHECK (btrim(kind)<>''),
    role text CHECK (role IN ('USER','ASSISTANT','TOOL')),
    content text CHECK (length(content)<=65536),
    tool_call_id text,
    tool_name text,
    payload_schema_version text NOT NULL DEFAULT '1.0',
    payload jsonb CHECK (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=262144),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_session_event_session_seq ON cfg_session_event(session_id,seq);
CREATE TABLE cfg_idempotency (
    operation_type text NOT NULL,
    scope_key text NOT NULL,
    request_id text NOT NULL,
    request_digest text NOT NULL CHECK (btrim(request_digest)<>''),
    result jsonb NOT NULL CHECK (jsonb_typeof(result)='object' AND octet_length(result::text)<=8192),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at timestamptz NOT NULL DEFAULT (CURRENT_TIMESTAMP + INTERVAL '30 days'),
    PRIMARY KEY(operation_type,scope_key,request_id)
);
CREATE INDEX idx_idempotency_expiry ON cfg_idempotency(expires_at);
CREATE INDEX idx_capture_expiry ON cfg_capture(expires_at);
CREATE INDEX idx_session_expiry ON cfg_session(expires_at);
CREATE INDEX idx_session_page ON cfg_session(page_id);
CREATE INDEX idx_session_capture ON cfg_session(capture_id);
CREATE INDEX idx_session_updated ON cfg_session(updated_at DESC,id);
CREATE INDEX idx_capture_created ON cfg_capture(created_at DESC,id);
CREATE TABLE ref_vehicle_model_key (
    model_id bigint PRIMARY KEY REFERENCES forj_vehicle_model(model_id),
    announcement_code text,
    engine_code text
);
CREATE INDEX idx_vehicle_model_key_codes ON ref_vehicle_model_key(announcement_code,engine_code);
CREATE TABLE ref_engine_emission (
    engine_model text PRIMARY KEY CHECK (btrim(engine_model)<>''),
    emission_standard text NOT NULL CHECK (emission_standard IN ('国三','国四','国五','国六')),
    source_config_count bigint NOT NULL CHECK (source_config_count>0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE ref_dataset_import (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_code text NOT NULL,
    source_name text NOT NULL,
    source_environment text,
    source_exported_at timestamptz,
    source_tables text[] NOT NULL,
    source_digest text NOT NULL,
    format_version text NOT NULL,
    row_count bigint NOT NULL CHECK (row_count>=0),
    builder_version text NOT NULL,
    source_row_count bigint CHECK (source_row_count>=0),
    excluded_ambiguous_count bigint CHECK (excluded_ambiguous_count>=0),
    excluded_invalid_count bigint CHECK (excluded_invalid_count>=0),
    imported_by text NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_dataset_import_latest ON ref_dataset_import(dataset_code,id DESC);

CREATE FUNCTION guard_configuration_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Published configuration history is immutable; create a new version or release';
END $$;
CREATE TRIGGER immutable_page BEFORE UPDATE OR DELETE ON cfg_page
    FOR EACH ROW EXECUTE FUNCTION guard_configuration_history();
CREATE TRIGGER immutable_version BEFORE UPDATE OR DELETE ON cfg_page_version
    FOR EACH ROW EXECUTE FUNCTION guard_configuration_history();
CREATE TRIGGER immutable_field BEFORE UPDATE OR DELETE ON cfg_page_field
    FOR EACH ROW EXECUTE FUNCTION guard_configuration_history();
CREATE TRIGGER immutable_material BEFORE UPDATE OR DELETE ON cfg_page_material
    FOR EACH ROW EXECUTE FUNCTION guard_configuration_history();
CREATE TRIGGER immutable_release BEFORE UPDATE OR DELETE ON cfg_release
    FOR EACH ROW EXECUTE FUNCTION guard_configuration_history();
CREATE TRIGGER immutable_release_item BEFORE UPDATE OR DELETE ON cfg_release_item
    FOR EACH ROW EXECUTE FUNCTION guard_configuration_history();
CREATE FUNCTION guard_configuration_append() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME IN ('cfg_page_field','cfg_page_material') THEN
        IF EXISTS (SELECT 1 FROM cfg_page_version WHERE id=NEW.page_version_id
            AND xmin::text::bigint<>(pg_current_xact_id()::text::bigint % 4294967296)) THEN
            RAISE EXCEPTION 'Cannot append members to a committed configuration version';
        END IF;
    ELSIF EXISTS (SELECT 1 FROM cfg_release WHERE id=NEW.release_id
        AND xmin::text::bigint<>(pg_current_xact_id()::text::bigint % 4294967296)) THEN
        RAISE EXCEPTION 'Cannot append pages to a committed release';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER protect_field_insert BEFORE INSERT ON cfg_page_field
    FOR EACH ROW EXECUTE FUNCTION guard_configuration_append();
CREATE TRIGGER protect_material_insert BEFORE INSERT ON cfg_page_material
    FOR EACH ROW EXECUTE FUNCTION guard_configuration_append();
CREATE TRIGGER protect_release_insert BEFORE INSERT ON cfg_release_item
    FOR EACH ROW EXECUTE FUNCTION guard_configuration_append();
CREATE TRIGGER immutable_validation BEFORE UPDATE ON cfg_validation_run
    FOR EACH ROW EXECUTE FUNCTION guard_configuration_history();
CREATE FUNCTION guard_validation_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.expires_at IS NULL OR OLD.expires_at>CURRENT_TIMESTAMP
        OR EXISTS (SELECT 1 FROM cfg_release WHERE validation_run_id=OLD.id)
        OR EXISTS (SELECT 1 FROM cfg_session WHERE current_validation_run_id=OLD.id
            AND state NOT IN ('PUBLISHED','CLOSED')) THEN
        RAISE EXCEPTION 'Published or active validation evidence cannot be deleted';
    END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER protect_validation_delete BEFORE DELETE ON cfg_validation_run
    FOR EACH ROW EXECUTE FUNCTION guard_validation_delete();
CREATE FUNCTION guard_release_validation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM cfg_validation_run v WHERE v.id=NEW.validation_run_id
        AND v.status='PASSED' AND v.manifest_digest=NEW.manifest_digest
        AND v.base_release_id IS NOT DISTINCT FROM NEW.base_release_id
        AND v.purpose=NEW.operation_type
        AND (v.expires_at IS NULL OR v.expires_at>CURRENT_TIMESTAMP)) THEN
        RAISE EXCEPTION 'Release requires matching completed validation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER require_release_validation BEFORE INSERT ON cfg_release
    FOR EACH ROW EXECUTE FUNCTION guard_release_validation();
