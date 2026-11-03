CREATE TABLE task_runtime_limits (
    singleton_id SMALLINT PRIMARY KEY CHECK (singleton_id = 1),
    max_running_tasks INTEGER NOT NULL CHECK (max_running_tasks > 0)
);

INSERT INTO task_runtime_limits (singleton_id, max_running_tasks)
VALUES (1, 2);