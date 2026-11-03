-- 已有任务默认没有待确认的清理。待清理的 Attempt 必须属于同一条 Task。
ALTER TABLE task_attempts
    ADD CONSTRAINT task_attempts_task_attempt_unique UNIQUE (task_id, attempt_id);

ALTER TABLE tasks
    ADD COLUMN cleanup_pending BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN cleanup_attempt_id UUID,
    ADD CONSTRAINT tasks_cleanup_owner_check
        CHECK (cleanup_pending = (cleanup_attempt_id IS NOT NULL)),
    ADD CONSTRAINT tasks_cleanup_attempt_fk
        FOREIGN KEY (task_id, cleanup_attempt_id)
        REFERENCES task_attempts (task_id, attempt_id);
