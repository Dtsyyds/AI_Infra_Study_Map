-- 演示工具实际修改的数据。
CREATE TABLE demo_counters (
    counter_name TEXT PRIMARY KEY,
    value BIGINT NOT NULL DEFAULT 0
);

-- 同数据库工具成功执行后的结果账本。
CREATE TABLE tool_calls (
    tool_call_id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES tasks(task_id),

    step_index INTEGER NOT NULL CHECK (step_index >= 0),
    call_index INTEGER NOT NULL CHECK (call_index >= 0),

    tool_name TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    result JSONB NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_tool_calls_logical_position
        UNIQUE (task_id, step_index, call_index)
);