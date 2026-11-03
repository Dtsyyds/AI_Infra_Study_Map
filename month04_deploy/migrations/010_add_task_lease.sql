ALTER TABLE tasks
    ADD COLUMN lease_version BIGINT NOT NULL DEFAULT 0
        CHECK (lease_version >= 0),
    ADD COLUMN lease_expires_at TIMESTAMPTZ;

