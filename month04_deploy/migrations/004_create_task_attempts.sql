CREATE TABLE task_attempts (
    attempt_id UUID PRIMARY KEY,

    task_id UUID NOT NULL,
    attempt_no INTEGER NOT NULL,

    status TEXT NOT NULL DEFAULT 'running',
    error JSONB,

    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ,

    CONSTRAINT task_attempts_task_fk
        FOREIGN KEY (task_id) REFERENCES tasks(task_id),

    CONSTRAINT task_attempts_number_unique
        UNIQUE (task_id, attempt_no),

    CONSTRAINT task_attempts_number_positive
        CHECK (attempt_no >= 1),

    CONSTRAINT task_attempts_status_valid
        CHECK (
            status IN (
                'running',
                'succeeded',
                'failed',
                'interrupted'
            )
        ),

    CONSTRAINT task_attempts_finish_time_valid
        CHECK (
            (status = 'running' AND finished_at IS NULL)
            OR
            (status <> 'running' AND finished_at IS NOT NULL)
        )
);

CREATE UNIQUE INDEX task_attempts_one_running_per_task
    ON task_attempts (task_id)
    WHERE status = 'running';