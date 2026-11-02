ALTER TABLE tool_calls
    ADD COLUMN status TEXT NOT NULL DEFAULT 'succeeded',
    ALTER COLUMN result DROP NOT NULL,
    ALTER COLUMN completed_at DROP NOT NULL,
    ADD CONSTRAINT tool_calls_status_result_check CHECK (
        (status = 'succeeded' AND result IS NOT NULL AND completed_at IS NOT NULL)
        OR
        (status IN ('started', 'unknown') AND result IS NULL AND completed_at IS NULL)
    );