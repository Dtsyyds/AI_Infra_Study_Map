ALTER TABLE tasks
    ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 1,
    ADD CONSTRAINT tasks_max_attempts_positive
        CHECK (max_attempts >= 1);
