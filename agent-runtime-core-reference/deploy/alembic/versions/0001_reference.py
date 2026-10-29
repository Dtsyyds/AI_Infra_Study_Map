"""Reference schema: PostgreSQL is the only recovery source."""

from alembic import op

revision = "0001_reference"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Alembic only executes SQL. Runtime uses psycopg, with no ORM unit-of-work.
    op.get_bind().exec_driver_sql(SCHEMA)


def downgrade():
    raise RuntimeError("Use an explicit reviewed migration; no automatic data deletion")


SCHEMA = r"""
CREATE SCHEMA arc_ref;
CREATE SCHEMA arc_business;
CREATE TABLE arc_ref.admissions (
 principal_id text PRIMARY KEY, window_start timestamptz NOT NULL DEFAULT clock_timestamp(),
 requests integer NOT NULL DEFAULT 0
);
CREATE TABLE arc_ref.sessions (
 principal_id text NOT NULL, session_id text NOT NULL, version bigint NOT NULL DEFAULT 0,
 snapshot jsonb NOT NULL DEFAULT '{}', PRIMARY KEY(principal_id, session_id)
);
CREATE TABLE arc_ref.tasks (
 task_id uuid PRIMARY KEY, principal_id text NOT NULL, principal jsonb NOT NULL,
 session_id text NOT NULL, request jsonb NOT NULL, request_digest text NOT NULL,
 status text NOT NULL CHECK(status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED','TIMED_OUT','NEEDS_REVIEW')),
 version bigint NOT NULL DEFAULT 1, fence bigint NOT NULL DEFAULT 0,
 current_attempt uuid, lease_until timestamptz, cancel_requested boolean NOT NULL DEFAULT false,
 checkpoint integer NOT NULL DEFAULT -1, context jsonb NOT NULL DEFAULT '{}',
 result jsonb, error_code text, deadline_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(principal_id,session_id) REFERENCES arc_ref.sessions(principal_id,session_id)
);
CREATE UNIQUE INDEX active_session ON arc_ref.tasks(principal_id, session_id)
 WHERE status IN ('QUEUED','RUNNING','NEEDS_REVIEW');
CREATE INDEX claim_tasks ON arc_ref.tasks(status,created_at);
CREATE TABLE arc_ref.idempotency_keys (
 principal_id text NOT NULL, key text NOT NULL, request_digest text NOT NULL,
 task_id uuid NOT NULL REFERENCES arc_ref.tasks, PRIMARY KEY(principal_id,key)
);
CREATE TABLE arc_ref.attempts (
 attempt_id uuid PRIMARY KEY, task_id uuid NOT NULL REFERENCES arc_ref.tasks,
 worker_id text NOT NULL, fence bigint NOT NULL, status text NOT NULL DEFAULT 'RUNNING',
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
 UNIQUE(task_id,fence)
);
CREATE TABLE arc_ref.actions (
 task_id uuid NOT NULL REFERENCES arc_ref.tasks, step_no integer NOT NULL CHECK(step_no>=0),
 tool_call_id uuid NOT NULL UNIQUE, action jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(task_id,step_no)
);
CREATE TABLE arc_ref.ledger (
 tool_call_id uuid PRIMARY KEY REFERENCES arc_ref.actions(tool_call_id), task_id uuid NOT NULL REFERENCES arc_ref.tasks,
 step_no integer NOT NULL, tool text NOT NULL, effect_class text NOT NULL,
 state text NOT NULL CHECK(state IN ('PREPARED','DISPATCHED','COMMITTED','UNKNOWN','CONFIRMED_APPLIED','CONFIRMED_NOT_APPLIED')),
 dispatch_count integer NOT NULL DEFAULT 0, result jsonb, evidence text,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE arc_ref.steps (
 task_id uuid NOT NULL REFERENCES arc_ref.tasks, step_no integer NOT NULL, attempt_id uuid NOT NULL REFERENCES arc_ref.attempts,
 tool_call_id uuid NOT NULL REFERENCES arc_ref.ledger, result jsonb NOT NULL,
 committed_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(task_id,step_no)
);
CREATE TABLE arc_ref.artifact_quota (singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), bytes bigint NOT NULL DEFAULT 0 CHECK(bytes>=0 AND bytes<=536870912));
INSERT INTO arc_ref.artifact_quota(singleton) VALUES(true);
CREATE TABLE arc_ref.artifacts (
 artifact_id uuid PRIMARY KEY, task_id uuid NOT NULL REFERENCES arc_ref.tasks,
 step_no integer NOT NULL, content bytea NOT NULL CHECK(octet_length(content)<=2097152),
 media_type text NOT NULL DEFAULT 'application/json', UNIQUE(task_id,step_no)
);
CREATE TABLE arc_ref.audit (
 audit_id bigserial PRIMARY KEY, occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 principal_id text NOT NULL, event text NOT NULL, task_id uuid, attempt_id uuid, tool_call_id uuid,
 data jsonb NOT NULL DEFAULT '{}'
);
CREATE FUNCTION arc_ref.audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'audit is append-only'; END $$;
CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON arc_ref.audit FOR EACH ROW EXECUTE FUNCTION arc_ref.audit_immutable();
CREATE TABLE arc_ref.documents (document_id text PRIMARY KEY, version text NOT NULL, data jsonb NOT NULL);
INSERT INTO arc_ref.documents VALUES
 ('DOC-001','1','{"amount":12000,"attachments":["invoice"],"source":"fictional://procurement/DOC-001#v1","fictional":true}'),
 ('DOC-002','1','{"amount":8000,"attachments":["invoice"],"source":"fictional://procurement/DOC-002#v1","fictional":true}'),
 ('DOC-003','1','{"amount":5000,"attachments":[],"source":"fictional://procurement/DOC-003#v1","fictional":true}');
CREATE TABLE arc_ref.rules (rule_id text PRIMARY KEY, version text NOT NULL, data jsonb NOT NULL);
INSERT INTO arc_ref.rules VALUES
 ('RULE-APPROVAL','1','{"source":"fictional://policy/procurement#approval","text":"采购金额超过10000时要求审批附件","rule_type":"approval_threshold","threshold":10000}'),
 ('RULE-INVOICE','1','{"source":"fictional://policy/procurement#invoice","text":"所有采购资料要求发票材料","rule_type":"invoice_required"}');
CREATE TABLE arc_ref.tool_cache (cache_key text PRIMARY KEY, value jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE arc_ref.local_effects (tool_call_id uuid PRIMARY KEY REFERENCES arc_ref.ledger, data jsonb NOT NULL);
CREATE TABLE arc_ref.test_barriers (name text PRIMARY KEY, task_id uuid, reached boolean NOT NULL DEFAULT false, released boolean NOT NULL DEFAULT false, data jsonb NOT NULL DEFAULT '{}');
CREATE TABLE arc_business.requests (request_id bigserial PRIMARY KEY, task_id text NOT NULL, idempotency_key text NOT NULL, mode text NOT NULL, received_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE arc_business.effects (effect_id uuid PRIMARY KEY, task_id text NOT NULL, idempotency_key text, mode text NOT NULL, report jsonb NOT NULL, applied_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE UNIQUE INDEX business_dedup ON arc_business.effects(idempotency_key) WHERE mode IN ('idempotent','queryable');
CREATE TABLE arc_business.controls (name text PRIMARY KEY, value jsonb NOT NULL);
"""
