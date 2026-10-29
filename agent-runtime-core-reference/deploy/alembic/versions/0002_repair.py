"""Add repair facts and conservative persistent model budget reservations."""

from alembic import op

revision = "0002_repair"
down_revision = "0001_reference"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql("""
CREATE TABLE arc_ref.repair_candidates (
 task_id uuid NOT NULL REFERENCES arc_ref.tasks,
 candidate_no integer NOT NULL CHECK(candidate_no BETWEEN 1 AND 3),
 tool_call_id uuid NOT NULL UNIQUE REFERENCES arc_ref.actions(tool_call_id),
 patch jsonb, candidate_hash text, verification jsonb,
 PRIMARY KEY(task_id,candidate_no)
);
CREATE TABLE arc_ref.repair_verifications (
 verification_run_id uuid PRIMARY KEY, task_id uuid NOT NULL REFERENCES arc_ref.tasks,
 attempt_id uuid NOT NULL REFERENCES arc_ref.attempts, tool_call_id uuid NOT NULL REFERENCES arc_ref.actions(tool_call_id),
 candidate_no integer NOT NULL CHECK(candidate_no BETWEEN 0 AND 3), identity jsonb NOT NULL,
 result jsonb, started_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz
);
CREATE INDEX repair_verification_task ON arc_ref.repair_verifications(task_id,tool_call_id);
CREATE TABLE arc_ref.model_budgets (
 budget_id text PRIMARY KEY, config jsonb NOT NULL,
 used_requests integer NOT NULL DEFAULT 0, reserved_tokens bigint NOT NULL DEFAULT 0
);
CREATE TABLE arc_ref.model_calls (
 call_id uuid PRIMARY KEY, budget_id text NOT NULL REFERENCES arc_ref.model_budgets,
 task_id uuid NOT NULL REFERENCES arc_ref.tasks, attempt_id uuid NOT NULL REFERENCES arc_ref.attempts,
 tool_call_id uuid NOT NULL REFERENCES arc_ref.actions(tool_call_id), reserved_tokens integer NOT NULL,
 result jsonb, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX model_calls_task ON arc_ref.model_calls(task_id);
""")


def downgrade():
    raise RuntimeError("explicit reviewed data migration required")
