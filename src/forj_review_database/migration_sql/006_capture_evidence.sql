-- Capture images remain readable after their source URLs or browser tabs expire.
CREATE TABLE cfg_capture_image (
    capture_id text NOT NULL REFERENCES cfg_capture(id) ON DELETE CASCADE,
    sample_id text NOT NULL CHECK (length(sample_id) BETWEEN 1 AND 200),
    content_digest text NOT NULL CHECK (content_digest ~ '^[0-9a-f]{64}$'),
    media_type text NOT NULL CHECK (media_type IN ('image/png','image/jpeg','image/webp','image/gif')),
    byte_size integer NOT NULL CHECK (byte_size BETWEEN 1 AND 8388608),
    content bytea NOT NULL CHECK (octet_length(content) = byte_size),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (capture_id, sample_id)
);

-- Replacing the current scene must not discard evidence cited earlier in a task.
CREATE TABLE cfg_session_capture (
    session_id text NOT NULL REFERENCES cfg_session(id) ON DELETE CASCADE,
    capture_id text NOT NULL REFERENCES cfg_capture(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, capture_id)
);
CREATE INDEX ix_cfg_session_capture_capture ON cfg_session_capture(capture_id);
INSERT INTO cfg_session_capture(session_id, capture_id)
    SELECT id, capture_id FROM cfg_session WHERE capture_id IS NOT NULL;
INSERT INTO cfg_session_capture(session_id, capture_id)
    SELECT s.id, c.id FROM cfg_session s
    JOIN cfg_capture c ON c.id = s.workflow->'archive'->>'capture_id'
    ON CONFLICT DO NOTHING;
