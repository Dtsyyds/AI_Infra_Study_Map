"""Real PostgreSQL invariants, including intentional trigger failures."""
import asyncio
import hashlib
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio

from config.settings import settings
from core.contracts.action import EffectClass, PreparedAction
from core.contracts.identity import Principal
from core.contracts.task import TaskRequest
from core.errors import LeaseLost, RuntimeFault
from runtime.storage.store import Store, digest

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def store():
    yield Store(settings.database_url.get_secret_value())


@pytest.fixture
def principal():
    return Principal(principal_id=f'test-{uuid4()}')


def request(session=None, **kwargs):
    return TaskRequest(document_id='DOC-001', session_id=session or str(uuid4()), **kwargs)


async def admitted(store, principal, **kwargs):
    task = await store.submit(principal, request(**kwargs), str(uuid4()))
    execution = await store.claim('test-worker', 60, task['task_id'])
    assert execution is not None
    return task, execution


def action(execution, effect=EffectClass.READ_ONLY, step_no=0):
    return PreparedAction(task_id=execution.task_id, step_no=step_no, tool_call_id=str(uuid4()),
                          tool='document.fetch', arguments={'document_id': 'DOC-001'},
                          arguments_digest=digest({'document_id': 'DOC-001'}), effect_class=effect)


async def expire_lease(store, task_id):
    async with store.connection() as conn:
        await conn.execute("UPDATE arc_ref.tasks SET lease_until=clock_timestamp()-interval '1 second' WHERE task_id=%s", (task_id,))


async def test_submit_idempotency_and_session_atomic(store, principal):
    req = request()
    key = str(uuid4())
    first, duplicate = await asyncio.gather(store.submit(principal, req, key), store.submit(principal, req, key))
    assert first['task_id'] == duplicate['task_id']
    with pytest.raises(RuntimeFault, match='IDEMPOTENCY_CONFLICT'):
        await store.submit(principal, request(req.session_id, budget_ms=3000), key)
    with pytest.raises(RuntimeFault, match='SESSION_BUSY'):
        await store.submit(principal, req, str(uuid4()))
    async with store.connection() as conn:
        row = await (await conn.execute('SELECT count(*) AS n FROM arc_ref.tasks WHERE principal_id=%s', (principal.principal_id,))).fetchone()
        assert row['n'] == 1
    await store.cancel(first['task_id'], principal.principal_id)


async def test_claim_attempt_insert_failure_rolls_back(store, principal):
    task = await store.submit(principal, request(), str(uuid4()))
    async with store.connection() as conn:
        await conn.execute("CREATE OR REPLACE FUNCTION arc_ref.test_reject_attempt() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.worker_id='test-real-insert-failure' THEN RAISE EXCEPTION 'intentional attempt INSERT failure'; END IF; RETURN NEW; END $$")
        await conn.execute('CREATE TRIGGER test_reject_attempt BEFORE INSERT ON arc_ref.attempts FOR EACH ROW EXECUTE FUNCTION arc_ref.test_reject_attempt()')
    try:
        with pytest.raises(psycopg.errors.RaiseException, match='intentional attempt INSERT failure'):
            await store.claim('test-real-insert-failure', 60, task['task_id'])
        snapshot = await store.snapshot(task['task_id'])
        assert snapshot['status'] == 'QUEUED'
        assert snapshot['fence'] == 0
        assert snapshot['current_attempt'] is None
        assert (await store.diagnostics(task['task_id']))['attempts'] == []
    finally:
        async with store.connection() as conn:
            await conn.execute('DROP TRIGGER test_reject_attempt ON arc_ref.attempts')
            await conn.execute('DROP FUNCTION arc_ref.test_reject_attempt()')
        await store.cancel(task['task_id'], principal.principal_id)


async def test_two_claimers_fencing_checkpoint_and_recovery(store, principal):
    task = await store.submit(principal, request(), str(uuid4()))
    winners = await asyncio.gather(store.claim('one', 60, task['task_id']), store.claim('two', 60, task['task_id']))
    assert sum(ex is not None for ex in winners) == 1
    old = next(ex for ex in winners if ex is not None)
    prepared = await store.prepare(old, action(old))
    await store.dispatch(old, prepared)
    await store.checkpoint(old, prepared, {'amount': 12000}, {'protected_facts': {'doc': 'DOC-001'}}, b'report')
    next_action = await store.prepare(old, action(old, step_no=1))
    await expire_lease(store, task['task_id'])
    resumed = await store.claim('replacement', 60, task['task_id'])
    assert resumed and resumed.attempt_id != old.attempt_id and resumed.task_id == old.task_id
    assert resumed.checkpoint == 0
    assert (await store.pending_action(resumed, 1)).tool_call_id == next_action.tool_call_id
    for method in (store.guard, lambda ex: store.heartbeat(ex, 60), lambda ex: store.finish(ex, {})):
        with pytest.raises(LeaseLost):
            await method(old)
    info = await store.diagnostics(task['task_id'])
    assert len(info['steps']) == 1 and info['ledger'][0]['dispatch_count'] == 1
    assert len(info['artifacts']) == 1
    assert info['artifacts'][0]['sha256'] == hashlib.sha256(b'report').hexdigest()
    assert info['artifacts'][0]['bytes'] == len(b'report')
    await store.fail(resumed, 'TEST_FINISHED')


async def test_cancel_before_success_and_success_before_cancel(store, principal):
    task, execution = await admitted(store, principal)
    await store.cancel(task['task_id'], principal.principal_id)
    with pytest.raises(RuntimeFault, match='CANCELLED'):
        await store.finish(execution, {'ok': True})
    await store.fail(execution, 'CANCELLED')
    assert (await store.snapshot(task['task_id']))['status'] == 'CANCELLED'
    task2, execution2 = await admitted(store, principal)
    await store.finish(execution2, {'ok': True})
    after = await store.cancel(task2['task_id'], principal.principal_id)
    assert after['status'] == 'SUCCEEDED' and not after['cancel_requested']


async def test_expired_before_claim_creates_no_attempt(store, principal):
    task = await store.submit(principal, request(), str(uuid4()))
    async with store.connection() as conn:
        await conn.execute("UPDATE arc_ref.tasks SET deadline_at=clock_timestamp()-interval '1 second' WHERE task_id=%s", (task['task_id'],))
    assert await store.claim('too-late', 60, task['task_id']) is None
    info = await store.diagnostics(task['task_id'])
    assert info['task']['status'] == 'TIMED_OUT' and info['attempts'] == []


async def test_deadline_does_not_mask_dispatched_external_effect(store, principal):
    task, execution = await admitted(store, principal)
    prepared = await store.prepare(execution, action(execution, EffectClass.EXTERNAL_UNSAFE))
    await store.dispatch(execution, prepared)
    async with store.connection() as conn:
        await conn.execute("UPDATE arc_ref.tasks SET deadline_at=clock_timestamp()-interval '1 second',lease_until=clock_timestamp()-interval '1 second' WHERE task_id=%s", (task['task_id'],))
    assert await store.claim('too-late', 60, task['task_id']) is None
    info = await store.diagnostics(task['task_id'])
    assert info['task']['status'] == 'NEEDS_REVIEW' and info['ledger'][0]['state'] == 'UNKNOWN'
    with pytest.raises(RuntimeFault, match='SESSION_BUSY'):
        await store.submit(principal, execution.request, str(uuid4()))
    operator = Principal(principal_id='demo-operator', role='operator')
    reconciled = await store.reconcile(task['task_id'], operator, info['task']['version'], prepared.tool_call_id, 'CONFIRMED_APPLIED', 'independent test evidence')
    assert reconciled['status'] == 'TIMED_OUT'
    assert reconciled['deadline_at'] == (await store.snapshot(task['task_id']))['deadline_at']


async def test_audit_failure_blocks_dispatch(store, principal):
    task, execution = await admitted(store, principal)
    prepared = await store.prepare(execution, action(execution, EffectClass.EXTERNAL_UNSAFE))
    async with store.connection() as conn:
        await conn.execute("CREATE OR REPLACE FUNCTION arc_ref.test_reject_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.event='tool.dispatch' AND NEW.principal_id LIKE 'test-%' THEN RAISE EXCEPTION 'intentional audit failure'; END IF; RETURN NEW; END $$")
        await conn.execute('CREATE TRIGGER test_reject_audit BEFORE INSERT ON arc_ref.audit FOR EACH ROW EXECUTE FUNCTION arc_ref.test_reject_audit()')
    try:
        with pytest.raises(psycopg.errors.RaiseException, match='intentional audit failure'):
            await store.dispatch(execution, prepared)
        ledger = await store.ledger(prepared.tool_call_id)
        assert ledger['state'] == 'PREPARED' and ledger['dispatch_count'] == 0
    finally:
        async with store.connection() as conn:
            await conn.execute('DROP TRIGGER test_reject_audit ON arc_ref.audit')
            await conn.execute('DROP FUNCTION arc_ref.test_reject_audit()')
        await store.fail(execution, 'TEST_FINISHED')


async def test_artifact_atomic_rejection_and_local_transaction(store, principal):
    task, execution = await admitted(store, principal)
    prepared = await store.prepare(execution, action(execution, EffectClass.LOCAL_TRANSACTIONAL))
    await store.dispatch(execution, prepared)
    with pytest.raises(RuntimeFault, match='ARTIFACT_ITEM_QUOTA'):
        await store.checkpoint(execution, prepared, {'ok': True}, {}, b'x' * (2 * 1024 * 1024 + 1))
    info = await store.diagnostics(task['task_id'])
    assert info['task']['checkpoint'] == -1 and info['steps'] == [] and info['artifacts'] == []
    async with store.connection() as conn:
        row = await (await conn.execute('SELECT count(*) AS n FROM arc_ref.local_effects WHERE tool_call_id=%s', (prepared.tool_call_id,))).fetchone()
        assert row['n'] == 0
    await store.checkpoint(execution, prepared, {'ok': True}, {}, b'complete')
    async with store.connection() as conn:
        row = await (await conn.execute('SELECT count(*) AS n FROM arc_ref.local_effects WHERE tool_call_id=%s', (prepared.tool_call_id,))).fetchone()
        assert row['n'] == 1
    assert execution.session_version == 1 and execution.context['session_version'] == 1
    await store.finish(execution, {'ok': True})


async def test_reconcile_cas_inconclusive_and_cancel_blocks_requeue(store, principal):
    task, execution = await admitted(store, principal)
    prepared = await store.prepare(execution, action(execution, EffectClass.EXTERNAL_UNSAFE))
    await store.dispatch(execution, prepared)
    await store.unknown(execution, prepared)
    operator = Principal(principal_id='demo-operator', role='operator')
    snapshot = await store.snapshot(task['task_id'])
    with pytest.raises(RuntimeFault, match='VERSION_CONFLICT'):
        await store.reconcile(task['task_id'], operator, snapshot['version'] - 1, prepared.tool_call_id, 'CONFIRMED_APPLIED', 'test evidence')
    result = await store.reconcile(task['task_id'], operator, snapshot['version'], prepared.tool_call_id, 'INCONCLUSIVE', 'not enough evidence')
    assert result['status'] == 'NEEDS_REVIEW'
    cancelled = await store.cancel(task['task_id'], principal.principal_id)
    resolved = await store.reconcile(task['task_id'], operator, cancelled['version'], prepared.tool_call_id, 'CONFIRMED_NOT_APPLIED', 'authoritative no effect')
    assert resolved['status'] == 'CANCELLED'


async def test_admission_quota_and_rate_are_atomic(store, principal):
    tasks = await asyncio.gather(*(store.submit(principal, request(), str(uuid4())) for _ in range(4)), return_exceptions=True)
    accepted = [task for task in tasks if isinstance(task, dict)]
    assert len(accepted) == 3
    denied = [task for task in tasks if isinstance(task, RuntimeFault)]
    assert len(denied) == 1 and denied[0].code == 'QUOTA_EXCEEDED'
    for task in accepted:
        await store.cancel(task['task_id'], principal.principal_id)
    async with store.connection() as conn:
        await conn.execute('UPDATE arc_ref.admissions SET requests=30,window_start=clock_timestamp() WHERE principal_id=%s', (principal.principal_id,))
    with pytest.raises(RuntimeFault, match='RATE_LIMITED'):
        await store.submit(principal, request(), str(uuid4()))


async def test_lease_check_uses_time_after_row_lock(store, principal):
    task, execution = await admitted(store, principal)
    async with store.connection() as blocker:
        await blocker.execute('SELECT task_id FROM arc_ref.tasks WHERE task_id=%s FOR UPDATE', (task['task_id'],))
        guard = asyncio.create_task(store.guard(execution))
        try:
            async with asyncio.timeout(5):
                while True:
                    async with store.connection() as observer:
                        row = await (await observer.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE 'SELECT * FROM arc_ref.tasks WHERE task_id=%') AS waiting")).fetchone()
                    if row['waiting']:
                        break
                    await asyncio.sleep(0.01)
            await blocker.execute("UPDATE arc_ref.tasks SET lease_until=clock_timestamp()-interval '1 second' WHERE task_id=%s", (task['task_id'],))
            await blocker.commit()
            with pytest.raises(LeaseLost):
                await guard
        finally:
            guard.cancel()
    resumed = await store.claim('replacement-after-lock', 60, task['task_id'])
    assert resumed
    await store.fail(resumed, 'TEST_FINISHED')


async def test_checkpoint_rejects_changed_prepared_action_and_session_version(store, principal):
    task, execution = await admitted(store, principal)
    prepared = await store.prepare(execution, action(execution))
    changed = prepared.model_copy(update={'arguments': {'document_id': 'DOC-002'}})
    with pytest.raises(RuntimeFault, match='PREPARED_ACTION_MISMATCH'):
        await store.dispatch(execution, changed)
    await store.dispatch(execution, prepared)
    async with store.connection() as conn:
        await conn.execute('UPDATE arc_ref.sessions SET version=version+1 WHERE principal_id=%s AND session_id=%s', (principal.principal_id, execution.request.session_id))
    with pytest.raises(RuntimeFault, match='SESSION_VERSION_CONFLICT'):
        await store.checkpoint(execution, prepared, {'ok': True}, {}, b'no commit')
    info = await store.diagnostics(task['task_id'])
    assert info['steps'] == [] and info['artifacts'] == [] and info['task']['checkpoint'] == -1
    await store.fail(execution, 'TEST_FINISHED')


async def test_task_and_platform_artifact_quotas(store, principal):
    task, execution = await admitted(store, principal)
    for step in range(4):
        prepared = await store.prepare(execution, action(execution, step_no=step))
        await store.dispatch(execution, prepared)
        await store.checkpoint(execution, prepared, {'step': step}, {}, b'x' * (2 * 1024 * 1024))
    fifth = await store.prepare(execution, action(execution, step_no=4))
    await store.dispatch(execution, fifth)
    with pytest.raises(RuntimeFault, match='ARTIFACT_TASK_QUOTA'):
        await store.checkpoint(execution, fifth, {}, {}, b'x')
    assert len((await store.diagnostics(task['task_id']))['artifacts']) == 4
    await store.fail(execution, 'TEST_FINISHED')
    task2, execution2 = await admitted(store, principal)
    prepared2 = await store.prepare(execution2, action(execution2))
    await store.dispatch(execution2, prepared2)
    async with store.connection() as conn:
        row = await (await conn.execute('SELECT bytes FROM arc_ref.artifact_quota WHERE singleton FOR UPDATE')).fetchone()
        previous_bytes = row['bytes']
        await conn.execute('UPDATE arc_ref.artifact_quota SET bytes=536870912 WHERE singleton')
    try:
        with pytest.raises(RuntimeFault, match='ARTIFACT_PLATFORM_QUOTA'):
            await store.checkpoint(execution2, prepared2, {}, {}, b'x')
        assert (await store.diagnostics(task2['task_id']))['artifacts'] == []
    finally:
        async with store.connection() as conn:
            await conn.execute('UPDATE arc_ref.artifact_quota SET bytes=%s WHERE singleton', (previous_bytes,))
        await store.fail(execution2, 'TEST_FINISHED')
