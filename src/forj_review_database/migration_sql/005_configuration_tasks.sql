ALTER TABLE cfg_session
    ADD COLUMN route text NOT NULL DEFAULT 'UNDECIDED'
        CHECK (route IN ('UNDECIDED','CONFIG','LOCAL_CODE','HUMAN_HANDOFF')),
    ADD COLUMN intent_revision bigint NOT NULL DEFAULT 0 CHECK (intent_revision>=0);

ALTER TABLE cfg_validation_run
    ADD COLUMN intent_digest text CHECK (intent_digest ~ '^[a-f0-9]{64}$'),
    ADD COLUMN verification_context jsonb NOT NULL DEFAULT '{"schema_version":"legacy"}'
        CHECK (jsonb_typeof(verification_context)='object'
            AND octet_length(verification_context::text)<=65536),
    ADD COLUMN publish_valid_until timestamptz,
    -- v4 manual CLI evidence legitimately omitted revision. Keep immutable history;
    -- enforce complete candidates for every new write without inventing old revisions.
    ADD CONSTRAINT session_candidate_complete CHECK (
        (session_id IS NULL AND candidate_page_id IS NULL AND candidate_revision IS NULL AND candidate_digest IS NULL) OR (
            candidate_page_id IS NOT NULL AND candidate_revision IS NOT NULL
            AND candidate_digest IS NOT NULL
        )
    ) NOT VALID,
    ADD CONSTRAINT versioned_verification_complete CHECK (
        COALESCE(verification_context->>'schema_version' IN ('legacy','1.0'), false)
        AND (verification_context->>'schema_version'='legacy' OR (
            intent_digest IS NOT NULL AND publish_valid_until IS NOT NULL
        ))
    );

CREATE OR REPLACE FUNCTION guard_release_validation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM cfg_validation_run v WHERE v.id=NEW.validation_run_id
        AND v.status='PASSED' AND v.manifest_digest=NEW.manifest_digest
        AND v.base_release_id IS NOT DISTINCT FROM NEW.base_release_id
        AND v.purpose=NEW.operation_type
        AND (CASE WHEN v.verification_context->>'schema_version'='legacy'
            THEN v.expires_at ELSE v.publish_valid_until END IS NULL
            OR CASE WHEN v.verification_context->>'schema_version'='legacy'
                THEN v.expires_at ELSE v.publish_valid_until END>CURRENT_TIMESTAMP)) THEN
        RAISE EXCEPTION 'Release requires matching completed validation';
    END IF;
    RETURN NEW;
END $$;
