ALTER TABLE task_runtime_limits
    ADD COLUMN max_queued_tasks INTEGER NOT NULL DEFAULT 32
        CONSTRAINT task_runtime_limits_max_queued_positive CHECK (max_queued_tasks > 0);
