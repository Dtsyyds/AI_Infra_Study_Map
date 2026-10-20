CREATE TABLE task_steps (
    task_id UUID NOT NULL,
    step_index INTEGER NOT NULL,
    step_name TEXT NOT NULL,
    result JSONB NOT NULL,

    completed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT task_steps_pkey
        PRIMARY KEY (task_id, step_index),

    CONSTRAINT task_steps_task_fk
        FOREIGN KEY (task_id)
        REFERENCES tasks(task_id),

    CONSTRAINT task_steps_index_nonnegative
        CHECK (step_index >= 0),

    CONSTRAINT task_steps_name_nonempty
        CHECK (length(trim(step_name)) > 0)
);