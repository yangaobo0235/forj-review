-- Explicit reset of configuration-agent work only. Published history is immutable.
-- Reject unfinished publication reconciliation before removing any work.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM cfg_session WHERE published_release_id IS NULL
        AND COALESCE((workflow->'pending'->>'confirmed')::boolean, false)) THEN
        RAISE EXCEPTION 'Reconcile confirmed configuration operations before migration';
    END IF;
END $$;

DROP TABLE cfg_session_event;
DROP TABLE cfg_session_capture;
DROP TABLE cfg_capture_image;
DROP TABLE cfg_session;
DROP TABLE cfg_capture;
-- This table is owned exclusively by the configuration-agent repository.
DELETE FROM cfg_idempotency;
ALTER TABLE cfg_idempotency RENAME COLUMN scope_key TO scope_id;
ALTER TABLE cfg_idempotency RENAME COLUMN result TO result_ref;
ALTER TABLE cfg_idempotency ADD COLUMN scope_type text NOT NULL DEFAULT 'TASK'
    CHECK (scope_type IN ('TASK','GLOBAL'));

CREATE TABLE cfg_capture (
    id text PRIMARY KEY,
    page_id bigint REFERENCES cfg_page(id),
    source_extension_id text NOT NULL,
    snapshot_id text NOT NULL,
    page_instance_id text NOT NULL,
    page_url text NOT NULL CHECK (page_url ~ '^https?://'),
    title text NOT NULL,
    captured_at timestamptz NOT NULL,
    observed_page_version text NOT NULL,
    detected_page_id text NOT NULL,
    record_identity jsonb NOT NULL CHECK (jsonb_typeof(record_identity)='object'),
    structure_digest text NOT NULL,
    content_digest text NOT NULL,
    snapshot_schema_version text NOT NULL,
    fields jsonb NOT NULL CHECK (jsonb_typeof(fields)='array' AND jsonb_array_length(fields)<=500),
    samples jsonb NOT NULL CHECK (jsonb_typeof(samples)='array' AND jsonb_array_length(samples)<=32),
    diagnostics jsonb NOT NULL CHECK (jsonb_typeof(diagnostics)='array'),
    headings jsonb NOT NULL CHECK (jsonb_typeof(headings)='array'),
    expires_at timestamptz NOT NULL
);
CREATE INDEX ix_cfg_capture_time ON cfg_capture(captured_at DESC,id DESC);
CREATE TRIGGER immutable_capture BEFORE UPDATE ON cfg_capture
    FOR EACH ROW EXECUTE FUNCTION guard_configuration_history();

CREATE TABLE cfg_task (
    id text PRIMARY KEY,
    page_id bigint NOT NULL REFERENCES cfg_page(id),
    base_page_version_id bigint NOT NULL REFERENCES cfg_page_version(id),
    base_release_id bigint NOT NULL REFERENCES cfg_release(id),
    base_page_digest text NOT NULL,
    kernel_fingerprint text NOT NULL,
    row_version bigint NOT NULL CHECK (row_version>=0),
    intent_revision bigint NOT NULL CHECK (intent_revision>=0),
    draft_revision bigint NOT NULL CHECK (draft_revision>=0),
    evidence_revision bigint NOT NULL CHECK (evidence_revision>=0),
    status text NOT NULL CHECK (status IN ('DRAFTING','NEEDS_INPUT','READY_FOR_CONFIRMATION','PUBLISHED','BLOCKED','CLOSED')),
    wait_reason text,
    draft jsonb NOT NULL CHECK (jsonb_typeof(draft)='object'),
    draft_digest text NOT NULL,
    -- Small typed working context; requirements, runs and interactions live below.
    context jsonb NOT NULL CHECK (jsonb_typeof(context)='object' AND octet_length(context::text)<=1048576),
    current_capture_id text REFERENCES cfg_capture(id),
    current_run_id text,
    current_validation_run_id text REFERENCES cfg_validation_run(id),
    published_release_id bigint REFERENCES cfg_release(id),
    parent_task_id text REFERENCES cfg_task(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz,
    expires_at timestamptz
);
CREATE INDEX ix_cfg_task_updated ON cfg_task(updated_at DESC,id DESC);
CREATE INDEX ix_cfg_task_expiry ON cfg_task(expires_at);
CREATE INDEX ix_cfg_task_capture ON cfg_task(current_capture_id);

CREATE TABLE cfg_task_capture (
    task_id text NOT NULL REFERENCES cfg_task(id) ON DELETE CASCADE,
    capture_id text NOT NULL REFERENCES cfg_capture(id),
    added_at timestamptz NOT NULL DEFAULT now(),
    source text NOT NULL DEFAULT 'USER',
    bindings_schema_version text NOT NULL DEFAULT '1.0',
    bindings jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(bindings)='object'),
    PRIMARY KEY(task_id,capture_id)
);
CREATE INDEX ix_cfg_task_capture_capture ON cfg_task_capture(capture_id);

CREATE TABLE cfg_task_event (
    seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id text NOT NULL UNIQUE,
    task_id text NOT NULL REFERENCES cfg_task(id) ON DELETE CASCADE,
    run_id text,
    kind text NOT NULL,
    role text,
    content text,
    tool_call_id text,
    tool_name text,
    payload_schema_version text NOT NULL,
    payload jsonb CHECK (payload IS NULL OR jsonb_typeof(payload)='object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(task_id,seq), UNIQUE(task_id,id)
);
CREATE INDEX ix_cfg_task_event_read ON cfg_task_event(task_id,kind,seq);
CREATE TRIGGER immutable_task_event BEFORE UPDATE ON cfg_task_event
    FOR EACH ROW EXECUTE FUNCTION guard_configuration_history();

CREATE TABLE cfg_task_requirement (
    id text PRIMARY KEY,
    task_id text NOT NULL REFERENCES cfg_task(id) ON DELETE CASCADE,
    source_event_id text,
    last_changed_event_id text,
    requirement_revision bigint NOT NULL CHECK (requirement_revision>=0),
    text text NOT NULL CHECK (length(text) BETWEEN 1 AND 16000),
    target_refs jsonb NOT NULL CHECK (jsonb_typeof(target_refs)='array'),
    status text NOT NULL CHECK (status IN ('PENDING','ACTIVE','SUPERSEDED','WITHDRAWN','QUERY')),
    replaces jsonb NOT NULL CHECK (jsonb_typeof(replaces)='array'),
    depends_on jsonb NOT NULL CHECK (jsonb_typeof(depends_on)='array'),
    assessment jsonb NOT NULL CHECK (jsonb_typeof(assessment)='object'),
    acceptance jsonb NOT NULL CHECK (jsonb_typeof(acceptance)='array'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(task_id,source_event_id) REFERENCES cfg_task_event(task_id,id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(task_id,last_changed_event_id) REFERENCES cfg_task_event(task_id,id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX ix_cfg_requirement_task ON cfg_task_requirement(task_id,created_at,id);

CREATE TABLE cfg_task_run (
    id text PRIMARY KEY,
    task_id text NOT NULL REFERENCES cfg_task(id) ON DELETE CASCADE,
    run_no bigint NOT NULL,
    trigger_event_id text,
    input_intent_revision bigint NOT NULL,
    input_draft_revision bigint NOT NULL,
    input_evidence_revision bigint NOT NULL,
    status text NOT NULL CHECK (status IN ('COMPLETED','RUNNING','QUEUED','WAITING','INTERRUPTED','CANCELLED','FAILED')),
    model_id text NOT NULL,
    budget jsonb NOT NULL CHECK (jsonb_typeof(budget)='object'),
    usage jsonb NOT NULL CHECK (jsonb_typeof(usage)='object'),
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    error_code text,
    UNIQUE(task_id,run_no), UNIQUE(task_id,id),
    FOREIGN KEY(task_id,trigger_event_id) REFERENCES cfg_task_event(task_id,id) DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE cfg_task ADD CONSTRAINT current_run_owned
    FOREIGN KEY(id,current_run_id) REFERENCES cfg_task_run(task_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE cfg_task_event ADD CONSTRAINT event_run_owned
    FOREIGN KEY(task_id,run_id) REFERENCES cfg_task_run(task_id,id) DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE cfg_task_interaction (
    id text PRIMARY KEY,
    task_id text NOT NULL REFERENCES cfg_task(id) ON DELETE CASCADE,
    run_id text,
    kind text NOT NULL CHECK (kind IN ('question','browser','confirmation')),
    status text NOT NULL CHECK (status IN ('PENDING','ANSWERED','SUPERSEDED')),
    intent_revision bigint NOT NULL,
    draft_revision bigint NOT NULL,
    evidence_revision bigint NOT NULL,
    candidate_digest text,
    validation_run_id text REFERENCES cfg_validation_run(id),
    payload_schema_version text NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=262144),
    answer_event_id text,
    expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    FOREIGN KEY(task_id,run_id) REFERENCES cfg_task_run(task_id,id),
    FOREIGN KEY(task_id,answer_event_id) REFERENCES cfg_task_event(task_id,id) DEFERRABLE INITIALLY DEFERRED
);
CREATE UNIQUE INDEX ix_cfg_interaction_pending ON cfg_task_interaction(task_id) WHERE status='PENDING';

ALTER TABLE cfg_task ADD CONSTRAINT base_page_version_owned
    FOREIGN KEY(page_id,base_page_version_id) REFERENCES cfg_page_version(page_id,id);
ALTER TABLE cfg_task ADD CONSTRAINT current_capture_attached
    FOREIGN KEY(id,current_capture_id) REFERENCES cfg_task_capture(task_id,capture_id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE cfg_validation_run ADD CONSTRAINT validation_task_identity UNIQUE(session_id,id);
ALTER TABLE cfg_task ADD CONSTRAINT current_validation_owned
    FOREIGN KEY(id,current_validation_run_id) REFERENCES cfg_validation_run(session_id,id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE cfg_task_interaction ADD CONSTRAINT interaction_validation_owned
    FOREIGN KEY(task_id,validation_run_id) REFERENCES cfg_validation_run(session_id,id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE cfg_task_event ADD CONSTRAINT bounded_task_event CHECK (
    btrim(kind)<>'' AND (role IS NULL OR role IN ('USER','ASSISTANT','TOOL'))
    AND (content IS NULL OR length(content)<=65536)
    AND (payload IS NULL OR octet_length(payload::text)<=262144));

CREATE FUNCTION guard_task_capture_page() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM cfg_capture c JOIN cfg_task t ON t.id=NEW.task_id
        JOIN cfg_page p ON p.id=t.page_id WHERE c.id=NEW.capture_id
        AND (c.detected_page_id<>'' AND c.detected_page_id<>p.page_code
            OR c.page_id IS NOT NULL AND c.page_id<>t.page_id)) THEN
        RAISE EXCEPTION 'Capture belongs to another page' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER task_capture_page BEFORE INSERT OR UPDATE ON cfg_task_capture
    FOR EACH ROW EXECUTE FUNCTION guard_task_capture_page();

CREATE OR REPLACE FUNCTION guard_validation_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.expires_at IS NULL OR OLD.expires_at>CURRENT_TIMESTAMP
        OR EXISTS (SELECT 1 FROM cfg_release WHERE validation_run_id=OLD.id)
        OR EXISTS (SELECT 1 FROM cfg_task WHERE current_validation_run_id=OLD.id
            AND status NOT IN ('PUBLISHED','CLOSED')) THEN
        RAISE EXCEPTION 'Published or active validation evidence cannot be deleted';
    END IF;
    RETURN OLD;
END $$;

ALTER TABLE cfg_validation_run DROP CONSTRAINT versioned_verification_complete;
ALTER TABLE cfg_validation_run ADD CONSTRAINT versioned_verification_complete CHECK (
    COALESCE(verification_context->>'schema_version' IN ('legacy','1.0','2.0'),false)
    AND (verification_context->>'schema_version'='legacy' OR
        (intent_digest IS NOT NULL AND publish_valid_until IS NOT NULL))
);

-- No delete of formal validation: published and long-lived proof always survives.
-- Checkpoint tables are owned by this single configuration executor. Their framework
-- migration history is not work data and must remain intact.
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['checkpoint_writes','checkpoint_blobs','checkpoints'] LOOP
        IF to_regclass(quote_ident(current_schema())||'.'||quote_ident(t)) IS NOT NULL THEN
            EXECUTE format('DELETE FROM %I.%I',current_schema(),t);
        END IF;
    END LOOP;
END $$;
