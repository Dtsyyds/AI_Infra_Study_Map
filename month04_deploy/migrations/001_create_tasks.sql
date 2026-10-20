CREATE TABLE tasks (
    task_id UUID PRIMARY KEY,

    task_type TEXT NOT NULL,
    payload JSONB NOT NULL,

    status TEXT NOT NULL DEFAULT 'queued',

    result JSONB,
    error JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT valid_task_status
        CHECK (
            status IN (
                'queued',
                'running',
                'succeeded',
                'failed',
                'cancelled'
            )
        ),

    CONSTRAINT succeeded_has_result
        CHECK (
            status <> 'succeeded'
            OR result IS NOT NULL
        )
);