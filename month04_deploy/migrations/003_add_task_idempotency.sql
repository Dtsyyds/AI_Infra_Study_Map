ALTER TABLE tasks
    ADD COLUMN idempotency_key TEXT,
    ADD COLUMN request_fingerprint TEXT,

    ADD CONSTRAINT tasks_idempotency_key_unique
        UNIQUE (idempotency_key),

    ADD CONSTRAINT tasks_idempotency_pair
        CHECK (
            (idempotency_key IS NULL)
            =
            (request_fingerprint IS NULL)
        ),

    ADD CONSTRAINT tasks_idempotency_key_length
        CHECK (
            idempotency_key IS NULL
            OR char_length(idempotency_key) BETWEEN 1 AND 128
        ),

    ADD CONSTRAINT tasks_fingerprint_format
        CHECK (
            request_fingerprint IS NULL
            OR request_fingerprint ~ '^[0-9a-f]{64}$'
        );