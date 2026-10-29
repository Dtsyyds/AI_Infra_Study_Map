"""显式 PostgreSQL 事务；checkpoint 和账本才是恢复依据，日志不是。

每个写回先锁 task 并用数据库时间验证 fencing。连接不跨方法共享，
调用者不能把两个各自提交的方法误当成一个事务。
"""
import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from core.contracts.action import PreparedAction
from core.contracts.execution import Execution
from core.contracts.identity import Principal
from core.contracts.task import TERMINAL, TaskRequest
from core.errors import LeaseLost, RuntimeFault

Row = dict[str, Any]
Connection = psycopg.AsyncConnection[Row]
EXTERNAL = ('EXTERNAL_IDEMPOTENT', 'EXTERNAL_QUERYABLE', 'EXTERNAL_UNSAFE')


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (datetime, UUID)):
        return str(value)
    return value


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Store:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[Connection]:
        from runtime.storage.pool import get_pool
        pool = await get_pool(self.dsn)
        from psycopg_pool import PoolTimeout, TooManyRequests
        try:
            async with pool.connection() as conn:
                yield conn
        except (PoolTimeout, TooManyRequests) as exc:
            raise RuntimeFault('DB_BUSY') from exc

    async def _one(self, conn: Connection, sql: str, args: tuple[Any, ...] = ()) -> Row:
        row = await (await conn.execute(sql, args)).fetchone()
        if row is None:
            raise RuntimeFault('NOT_FOUND')
        return row

    async def _audit(self, conn: Connection, principal: str, event: str,
                     task_id: str | None = None, attempt_id: str | None = None,
                     tool_call_id: str | None = None, data: Row | None = None) -> None:
        from runtime.security.redaction import redact
        # Only identifiers, safe metadata and digests belong here, never raw tool input.
        await conn.execute(
            'INSERT INTO arc_ref.audit(principal_id,event,task_id,attempt_id,tool_call_id,data) '
            'VALUES(%s,%s,%s,%s,%s,%s)',
            (principal, event, task_id, attempt_id, tool_call_id, Jsonb(redact(data or {}))),
        )

    async def audit(self, principal: Principal | str, event: str,
                    execution: Execution | None = None, action: PreparedAction | None = None,
                    data: Row | None = None) -> None:
        from runtime.security.redaction import redact
        async with self.connection() as conn:
            await self._audit(conn, principal.principal_id if isinstance(principal, Principal) else principal,
                              event, execution.task_id if execution else None,
                              execution.attempt_id if execution else None,
                              action.tool_call_id if action else None, redact(data or {}))

    async def submit(self, principal: Principal, request: TaskRequest, key: str) -> Row:
        from runtime.security.policy import admission_policy
        policy = admission_policy(principal)
        if not key or len(key) > 200:
            raise RuntimeFault('INVALID_IDEMPOTENCY_KEY')
        payload = request.model_dump(mode='json')
        if request.task_type == 'document_review':
            # Preserve the pre-enhancement idempotency payload for existing requests.
            for field in ('case_id','planner_mode','repair_strategy'):
                payload.pop(field,None)
        request_hash = digest(payload)
        async with self.connection() as conn:
            await conn.execute('INSERT INTO arc_ref.admissions(principal_id) VALUES(%s) ON CONFLICT DO NOTHING', (principal.principal_id,))
            admission = await self._one(conn, 'SELECT *,clock_timestamp() AS now FROM arc_ref.admissions WHERE principal_id=%s FOR UPDATE', (principal.principal_id,))
            replay = await (await conn.execute('SELECT request_digest,task_id FROM arc_ref.idempotency_keys WHERE principal_id=%s AND key=%s', (principal.principal_id, key))).fetchone()
            if replay:
                if replay['request_digest'] != request_hash:
                    raise RuntimeFault('IDEMPOTENCY_CONFLICT')
                return jsonable(await self._one(conn, 'SELECT * FROM arc_ref.tasks WHERE task_id=%s', (replay['task_id'],)))
            active = await self._one(conn, "SELECT count(*) AS n FROM arc_ref.tasks WHERE principal_id=%s AND status IN ('QUEUED','RUNNING','NEEDS_REVIEW')", (principal.principal_id,))
            busy = await (await conn.execute("SELECT 1 FROM arc_ref.tasks WHERE principal_id=%s AND session_id=%s AND status IN ('QUEUED','RUNNING','NEEDS_REVIEW')", (principal.principal_id, request.session_id))).fetchone()
            if busy:
                raise RuntimeFault('SESSION_BUSY')
            if active['n'] >= policy.active_limit:
                raise RuntimeFault('QUOTA_EXCEEDED')
            if (admission['now'] - admission['window_start']).total_seconds() >= 60:
                await conn.execute('UPDATE arc_ref.admissions SET requests=0,window_start=clock_timestamp() WHERE principal_id=%s', (principal.principal_id,))
            elif admission['requests'] >= policy.rate_per_minute:
                raise RuntimeFault('RATE_LIMITED')
            await conn.execute('UPDATE arc_ref.admissions SET requests=requests+1 WHERE principal_id=%s', (principal.principal_id,))
            await conn.execute('INSERT INTO arc_ref.sessions(principal_id,session_id) VALUES(%s,%s) ON CONFLICT DO NOTHING', (principal.principal_id, request.session_id))
            task_id = str(uuid4())
            initial_context: Row = {}
            if request.task_type == 'code_repair':
                from runtime.repair.manifest import load_case
                manifest, base = await asyncio.to_thread(load_case, request.case_id or '')
                initial_context = {'manifest': manifest.model_dump(), 'base_files': base}
            row = await self._one(conn,
                "INSERT INTO arc_ref.tasks(task_id,principal_id,principal,session_id,request,request_digest,status,deadline_at) VALUES(%s,%s,%s,%s,%s,%s,'QUEUED',clock_timestamp()+(%s*interval '1 millisecond')) RETURNING *",
                (task_id, principal.principal_id, Jsonb(principal.model_dump()), request.session_id, Jsonb(payload), request_hash, request.budget_ms))
            if initial_context:
                row['context'] = initial_context
                await conn.execute('UPDATE arc_ref.tasks SET context=%s WHERE task_id=%s', (Jsonb(initial_context), task_id))
            await conn.execute('INSERT INTO arc_ref.idempotency_keys(principal_id,key,request_digest,task_id) VALUES(%s,%s,%s,%s)', (principal.principal_id, key, request_hash, task_id))
            await self._audit(conn, principal.principal_id, 'task.accepted', task_id)
            return jsonable(row)

    async def snapshot(self, task_id: str, principal_id: str | None = None) -> Row:
        async with self.connection() as conn:
            # A single statement gives a coherent state/version snapshot at READ COMMITTED.
            return jsonable(await self._one(conn,
                'SELECT t.*, (SELECT count(*) FROM arc_ref.artifacts a WHERE a.task_id=t.task_id) AS artifact_count FROM arc_ref.tasks t WHERE task_id=%s AND (%s::text IS NULL OR principal_id=%s)',
                (task_id, principal_id, principal_id)))

    async def _uncertain(self, conn: Connection, task_id: str) -> bool:
        row = await self._one(conn,
            "SELECT EXISTS(SELECT 1 FROM arc_ref.ledger WHERE task_id=%s AND (state='UNKNOWN' OR (state='DISPATCHED' AND effect_class=ANY(%s)))) AS found", (task_id, list(EXTERNAL)))
        return bool(row['found'])

    async def _terminal(self, conn: Connection, task: Row, status: str,
                        result: Row | None = None, code: str | None = None) -> None:
        await conn.execute('UPDATE arc_ref.tasks SET status=%s,result=%s,error_code=%s,lease_until=NULL,version=version+1,updated_at=clock_timestamp() WHERE task_id=%s', (status, Jsonb(result) if result is not None else None, code, task['task_id']))
        if task['current_attempt']:
            await conn.execute('UPDATE arc_ref.attempts SET status=%s,finished_at=clock_timestamp() WHERE attempt_id=%s', (status, task['current_attempt']))
        await self._audit(conn, task['principal_id'], f'task.{status.lower()}', str(task['task_id']), str(task['current_attempt']) if task['current_attempt'] else None)

    async def claim(self, worker_id: str, lease_seconds: int,
                    task_id: str | None = None) -> Execution | None:
        async with self.connection() as conn:
            # Loop skips expired/cancelled queued tasks without inventing an Attempt.
            for _ in range(100):
                task = await (await conn.execute("SELECT *,clock_timestamp() AS now FROM arc_ref.tasks WHERE (%s::uuid IS NULL OR task_id=%s) AND (status='QUEUED' OR (status='RUNNING' AND lease_until<=clock_timestamp())) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1", (task_id, task_id))).fetchone()
                if not task:
                    return None
                if task['cancel_requested'] or task['now'] >= task['deadline_at']:
                    uncertain = await self._uncertain(conn, str(task['task_id']))
                    status = 'NEEDS_REVIEW' if uncertain else ('CANCELLED' if task['cancel_requested'] else 'TIMED_OUT')
                    if uncertain:
                        await conn.execute("UPDATE arc_ref.ledger SET state='UNKNOWN' WHERE task_id=%s AND state='DISPATCHED' AND effect_class=ANY(%s)", (task['task_id'], list(EXTERNAL)))
                    await self._terminal(conn, task, status)
                    continue
                attempt_id = str(uuid4())
                fence = task['fence'] + 1
                if task['current_attempt']:
                    await conn.execute("UPDATE arc_ref.attempts SET status='LEASE_LOST',finished_at=clock_timestamp() WHERE attempt_id=%s", (task['current_attempt'],))
                await conn.execute("UPDATE arc_ref.tasks SET status='RUNNING',fence=%s,current_attempt=%s,lease_until=clock_timestamp()+(%s*interval '1 second'),version=version+1,updated_at=clock_timestamp() WHERE task_id=%s", (fence, attempt_id, lease_seconds, task['task_id']))
                # Real INSERT is in the same transaction. Trigger/constraint failure rolls back claim.
                await conn.execute('INSERT INTO arc_ref.attempts(attempt_id,task_id,worker_id,fence) VALUES(%s,%s,%s,%s)', (attempt_id, task['task_id'], worker_id, fence))
                session = await self._one(conn, 'SELECT version FROM arc_ref.sessions WHERE principal_id=%s AND session_id=%s', (task['principal_id'], task['session_id']))
                await self._audit(conn, task['principal_id'], 'task.claimed', str(task['task_id']), attempt_id, data={'fence': fence})
                return Execution(task_id=str(task['task_id']), attempt_id=attempt_id, fence=fence,
                                 deadline_at=task['deadline_at'], principal=Principal.model_validate(task['principal']),
                                 request=TaskRequest.model_validate(task['request']), checkpoint=task['checkpoint'],
                                 context=task['context'], session_version=session['version'])
            return None

    async def _locked(self, conn: Connection, execution: Execution, controls: bool = True) -> Row:
        task = await self._one(conn, 'SELECT * FROM arc_ref.tasks WHERE task_id=%s FOR UPDATE', (execution.task_id,))
        # Evaluate time after acquiring the lock: waiting must not preserve an old lease check.
        task['now'] = (await self._one(conn, 'SELECT clock_timestamp() AS now'))['now']
        if task['status'] != 'RUNNING' or str(task['current_attempt']) != execution.attempt_id or task['fence'] != execution.fence or not task['lease_until'] or task['lease_until'] <= task['now']:
            raise LeaseLost()
        if controls:
            if task['cancel_requested']:
                raise RuntimeFault('CANCELLED')
            if task['now'] >= task['deadline_at']:
                raise RuntimeFault('TIMED_OUT')
        return task

    async def guard(self, execution: Execution) -> None:
        async with self.connection() as conn:
            await self._locked(conn, execution)

    async def remaining_ms(self, execution: Execution) -> int:
        async with self.connection() as conn:
            task = await self._locked(conn, execution)
            return max(0, int((task['deadline_at'] - task['now']).total_seconds() * 1000))

    async def heartbeat(self, execution: Execution, lease_seconds: int) -> None:
        async with self.connection() as conn:
            await self._locked(conn, execution, controls=False)
            await conn.execute("UPDATE arc_ref.tasks SET lease_until=clock_timestamp()+(%s*interval '1 second') WHERE task_id=%s", (lease_seconds, execution.task_id))

    async def pending_action(self, execution: Execution, step_no: int) -> PreparedAction | None:
        async with self.connection() as conn:
            await self._locked(conn, execution)
            row = await (await conn.execute('SELECT action FROM arc_ref.actions WHERE task_id=%s AND step_no=%s', (execution.task_id, step_no))).fetchone()
            return PreparedAction.model_validate(row['action']) if row else None

    async def prepare(self, execution: Execution, action: PreparedAction) -> PreparedAction:
        async with self.connection() as conn:
            task = await self._locked(conn, execution)
            """
            _locked() 会对任务行执行 SELECT ... FOR UPDATE,再检查:
            任务状态是否为 RUNNING,当前 Attempt ID 是否匹配,租约是否过期,以及是否有取消请求或超时。
            """
            if action.task_id != execution.task_id or action.step_no != task['checkpoint'] + 1:     # 它限制当前只能准备数据库进度之后的那一步
                raise RuntimeFault('INVALID_STEP')
            existing = await (await conn.execute('SELECT action FROM arc_ref.actions WHERE task_id=%s AND step_no=%s', (execution.task_id, action.step_no))).fetchone()
            if existing:
                return PreparedAction.model_validate(existing['action'])
            await conn.execute('INSERT INTO arc_ref.actions(task_id,step_no,tool_call_id,action) VALUES(%s,%s,%s,%s)', (execution.task_id, action.step_no, action.tool_call_id, Jsonb(action.model_dump(mode='json'))))
            await conn.execute("INSERT INTO arc_ref.ledger(tool_call_id,task_id,step_no,tool,effect_class,state) VALUES(%s,%s,%s,%s,%s,'PREPARED')", (action.tool_call_id, execution.task_id, action.step_no, action.tool, action.effect_class.value))
            if action.tool == 'repair.generate':
                from runtime.repair.persistence import reserve_candidate
                await reserve_candidate(conn, task, action)
            return action

    async def ledger(self, tool_call_id: str) -> Row:
        async with self.connection() as conn:
            return jsonable(await self._one(conn, 'SELECT * FROM arc_ref.ledger WHERE tool_call_id=%s', (tool_call_id,)))

    async def _persisted_action(self, conn: Connection, execution: Execution,
                                action: PreparedAction) -> Row:
        row = await self._one(conn,
            'SELECT a.action,l.* FROM arc_ref.actions a JOIN arc_ref.ledger l USING(tool_call_id) WHERE a.task_id=%s AND a.step_no=%s',
            (execution.task_id, action.step_no))
        if PreparedAction.model_validate(row['action']) != action:
            raise RuntimeFault('PREPARED_ACTION_MISMATCH')
        return row

    async def dispatch(self, execution: Execution, action: PreparedAction) -> Row:
        async with self.connection() as conn:
            await self._locked(conn, execution)
            ledger = await self._persisted_action(conn, execution, action)
            if ledger['state'] == 'UNKNOWN':
                raise RuntimeFault('NEEDS_REVIEW')
            if ledger['state'] in ('COMMITTED', 'CONFIRMED_APPLIED'):
                return jsonable(ledger)
            if ledger['effect_class'] == 'EXTERNAL_UNSAFE' and ledger['state'] == 'DISPATCHED':
                raise RuntimeFault('NEEDS_REVIEW')
            # Audit failure rolls back dispatch; caller cannot execute the effect afterwards.
            await self._audit(conn, execution.principal.principal_id, 'tool.dispatch', execution.task_id, execution.attempt_id, action.tool_call_id, {'tool': action.tool, 'arguments_digest': action.arguments_digest, 'effect_class': action.effect_class.value})
            row = await self._one(conn, "UPDATE arc_ref.ledger SET state='DISPATCHED',dispatch_count=dispatch_count+1,updated_at=clock_timestamp() WHERE tool_call_id=%s RETURNING *", (action.tool_call_id,))
            return jsonable(row)

    async def checkpoint(self, execution: Execution, action: PreparedAction, observation: Row,
                         context: Row, artifact: bytes | None = None) -> None:
        new_context = dict(context)
        async with self.connection() as conn:
            task = await self._locked(conn, execution)
            if action.step_no != task['checkpoint'] + 1:
                raise RuntimeFault('INVALID_STEP')
            ledger = await self._persisted_action(conn, execution, action)
            allowed = {'DISPATCHED', 'CONFIRMED_APPLIED'}
            if action.effect_class.value == 'READ_ONLY':
                allowed.add('PREPARED')  # A permission-scoped cache hit did not dispatch a tool.
            if ledger['state'] not in allowed:
                raise RuntimeFault('INVALID_LEDGER_TRANSITION')
            session = await self._one(conn, 'SELECT * FROM arc_ref.sessions WHERE principal_id=%s AND session_id=%s FOR UPDATE', (execution.principal.principal_id, execution.request.session_id))
            if session['version'] != execution.session_version:
                raise RuntimeFault('SESSION_VERSION_CONFLICT')
            new_context['session_version'] = execution.session_version + 1
            if action.tool.startswith('repair.'):
                from runtime.repair.persistence import commit_repair
                await commit_repair(conn, task, action, observation)
            if artifact is not None:
                if len(artifact) > 2 * 1024 * 1024:
                    raise RuntimeFault('ARTIFACT_ITEM_QUOTA')
                quota = await self._one(conn, 'SELECT bytes FROM arc_ref.artifact_quota WHERE singleton FOR UPDATE')
                task_bytes = await self._one(conn, 'SELECT COALESCE(sum(octet_length(content)),0) AS bytes FROM arc_ref.artifacts WHERE task_id=%s', (execution.task_id,))
                if task_bytes['bytes'] + len(artifact) > 8 * 1024 * 1024:
                    raise RuntimeFault('ARTIFACT_TASK_QUOTA')
                if quota['bytes'] + len(artifact) > 512 * 1024 * 1024:
                    raise RuntimeFault('ARTIFACT_PLATFORM_QUOTA')
                await conn.execute('INSERT INTO arc_ref.artifacts(artifact_id,task_id,step_no,content) VALUES(%s,%s,%s,%s)', (str(uuid4()), execution.task_id, action.step_no, artifact))
                await conn.execute('UPDATE arc_ref.artifact_quota SET bytes=bytes+%s WHERE singleton', (len(artifact),))
            if action.effect_class.value == 'LOCAL_TRANSACTIONAL':
                # The local effect and its COMMITTED ledger share exactly this transaction.
                await conn.execute('INSERT INTO arc_ref.local_effects(tool_call_id,data) VALUES(%s,%s) ON CONFLICT DO NOTHING', (action.tool_call_id, Jsonb(observation)))
            await conn.execute('INSERT INTO arc_ref.steps(task_id,step_no,attempt_id,tool_call_id,result) VALUES(%s,%s,%s,%s,%s)', (execution.task_id, action.step_no, execution.attempt_id, action.tool_call_id, Jsonb(observation)))
            await conn.execute("UPDATE arc_ref.ledger SET state='COMMITTED',result=%s,updated_at=clock_timestamp() WHERE tool_call_id=%s AND task_id=%s", (Jsonb(observation), action.tool_call_id, execution.task_id))
            await conn.execute('UPDATE arc_ref.sessions SET version=version+1,snapshot=%s WHERE principal_id=%s AND session_id=%s', (Jsonb(new_context), execution.principal.principal_id, execution.request.session_id))
            await conn.execute('UPDATE arc_ref.tasks SET checkpoint=%s,context=%s,version=version+1,updated_at=clock_timestamp() WHERE task_id=%s', (action.step_no, Jsonb(new_context), execution.task_id))
            correlation = {'step_no': action.step_no}
            if action.tool.startswith('repair.'):
                correlation['candidate_no'] = action.arguments['candidate_no']
                identity = observation.get('record', {}).get('identity', {})
                if identity.get('verification_run_id'):
                    correlation['verification_run_id'] = identity['verification_run_id']
            await self._audit(conn, execution.principal.principal_id, 'step.committed', execution.task_id, execution.attempt_id, action.tool_call_id, correlation)
        execution.checkpoint = action.step_no
        execution.context = new_context
        execution.session_version += 1
        context['session_version'] = execution.session_version

    async def finish(self, execution: Execution, result: Row) -> None:
        async with self.connection() as conn:
            task = await self._locked(conn, execution)
            if await self._uncertain(conn, execution.task_id):
                raise RuntimeFault('NEEDS_REVIEW')
            if execution.request.task_type == 'code_repair':
                from runtime.repair.persistence import validate_delivery
                await validate_delivery(conn, task, result)
            await self._terminal(conn, task, 'SUCCEEDED', result)

    async def fail(self, execution: Execution, code: str) -> None:
        async with self.connection() as conn:
            task = await self._locked(conn, execution, controls=False)
            uncertain = await self._uncertain(conn, execution.task_id)
            if uncertain:
                await conn.execute("UPDATE arc_ref.ledger SET state='UNKNOWN' WHERE task_id=%s AND state='DISPATCHED' AND effect_class=ANY(%s)", (execution.task_id, list(EXTERNAL)))
            status = 'NEEDS_REVIEW' if uncertain or code == 'NEEDS_REVIEW' else ('CANCELLED' if task['cancel_requested'] or code == 'CANCELLED' else ('TIMED_OUT' if task['now'] >= task['deadline_at'] or code == 'TIMED_OUT' else 'FAILED'))
            await self._terminal(conn, task, status, code=code)

    async def unknown(self, execution: Execution, action: PreparedAction) -> None:
        async with self.connection() as conn:
            task = await self._locked(conn, execution, controls=False)
            await conn.execute("UPDATE arc_ref.ledger SET state='UNKNOWN',updated_at=clock_timestamp() WHERE tool_call_id=%s AND task_id=%s", (action.tool_call_id, execution.task_id))
            await self._terminal(conn, task, 'NEEDS_REVIEW', code='UNKNOWN_EFFECT')

    async def cancel(self, task_id: str, principal_id: str) -> Row:
        async with self.connection() as conn:
            task = await self._one(conn, 'SELECT * FROM arc_ref.tasks WHERE task_id=%s AND principal_id=%s FOR UPDATE', (task_id, principal_id))
            if task['status'] in TERMINAL:
                return jsonable(task)
            await conn.execute('UPDATE arc_ref.tasks SET cancel_requested=true,version=version+1,updated_at=clock_timestamp() WHERE task_id=%s', (task_id,))
            await self._audit(conn, principal_id, 'task.cancel_requested', task_id)
            if task['status'] == 'QUEUED':
                await self._terminal(conn, task, 'NEEDS_REVIEW' if await self._uncertain(conn, task_id) else 'CANCELLED')
            return jsonable(await self._one(conn, 'SELECT * FROM arc_ref.tasks WHERE task_id=%s', (task_id,)))

    async def reconcile(self, task_id: str, operator: Principal, version: int,
                        tool_call_id: str, outcome: str, evidence: str) -> Row:
        from runtime.security.policy import authorize_reconcile
        authorize_reconcile(operator)
        if outcome not in ('CONFIRMED_APPLIED', 'CONFIRMED_NOT_APPLIED', 'INCONCLUSIVE') or not evidence.strip():
            raise RuntimeFault('INVALID_RECONCILIATION')
        async with self.connection() as conn:
            task = await self._one(conn, 'SELECT * FROM arc_ref.tasks WHERE task_id=%s FOR UPDATE', (task_id,))
            task['now'] = (await self._one(conn, 'SELECT clock_timestamp() AS now'))['now']
            if task['version'] != version or task['status'] != 'NEEDS_REVIEW':
                raise RuntimeFault('VERSION_CONFLICT')
            ledger = await self._one(conn, 'SELECT * FROM arc_ref.ledger WHERE task_id=%s AND tool_call_id=%s FOR UPDATE', (task_id, tool_call_id))
            if ledger['state'] != 'UNKNOWN':
                raise RuntimeFault('NOT_UNKNOWN')
            safe_evidence = hashlib.sha256(evidence.encode()).hexdigest()
            if outcome != 'INCONCLUSIVE':
                await conn.execute('UPDATE arc_ref.ledger SET state=%s,evidence=%s,result=%s,updated_at=clock_timestamp() WHERE tool_call_id=%s', (outcome, safe_evidence, Jsonb({'reconciled': True, 'outcome': outcome}), tool_call_id))
            blocked = await self._uncertain(conn, task_id)
            next_status = 'NEEDS_REVIEW'
            if not blocked:
                next_status = 'CANCELLED' if task['cancel_requested'] else ('TIMED_OUT' if task['now'] >= task['deadline_at'] else 'QUEUED')
            await conn.execute('UPDATE arc_ref.tasks SET status=%s,version=version+1,lease_until=NULL,updated_at=clock_timestamp() WHERE task_id=%s', (next_status, task_id))
            await self._audit(conn, operator.principal_id, 'effect.reconciled', task_id, tool_call_id=tool_call_id, data={'outcome': outcome, 'evidence_sha256': safe_evidence, 'expected_version': version})
            return jsonable(await self._one(conn, 'SELECT * FROM arc_ref.tasks WHERE task_id=%s', (task_id,)))

    async def diagnostics(self, task_id: str) -> Row:
        async with self.connection() as conn:
            await conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            result: Row = {'task': jsonable(await self._one(conn, 'SELECT * FROM arc_ref.tasks WHERE task_id=%s', (task_id,)))}
            for table in ('attempts', 'steps', 'actions', 'ledger', 'audit'):
                rows = await (await conn.execute(f'SELECT * FROM arc_ref.{table} WHERE task_id=%s', (task_id,))).fetchall()
                result[table] = jsonable(rows)
            for table in ('repair_candidates', 'repair_verifications', 'model_calls'):
                result[table] = jsonable(await (await conn.execute(f'SELECT * FROM arc_ref.{table} WHERE task_id=%s', (task_id,))).fetchall())
            result['artifacts'] = jsonable(await (await conn.execute("SELECT artifact_id,step_no,media_type,octet_length(content) AS bytes,encode(sha256(content),'hex') AS sha256 FROM arc_ref.artifacts WHERE task_id=%s", (task_id,))).fetchall())
            return result

    async def metrics(self) -> Row:
        async with self.connection() as conn:
            await conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            tasks = await (await conn.execute('SELECT status,count(*) AS count FROM arc_ref.tasks GROUP BY status')).fetchall()
            tools = await (await conn.execute('SELECT tool,state,count(*) AS count,sum(dispatch_count) AS dispatch_count FROM arc_ref.ledger GROUP BY tool,state')).fetchall()
            quota = await self._one(conn, 'SELECT bytes FROM arc_ref.artifact_quota WHERE singleton')
            return {'tasks': tasks, 'tools': tools, 'artifact_bytes': quota['bytes']}

    async def artifact(self, task_id: str, artifact_id: str, principal_id: str) -> bytes:
        async with self.connection() as conn:
            row = await self._one(conn, 'SELECT a.content FROM arc_ref.artifacts a JOIN arc_ref.tasks t USING(task_id) WHERE a.task_id=%s AND a.artifact_id=%s AND t.principal_id=%s', (task_id, artifact_id, principal_id))
            return bytes(row['content'])

    async def attempt_active(self, task_id: str, attempt_id: str, fence: int) -> bool | None:
        async with self.connection() as conn:
            row = await (await conn.execute("SELECT t.current_attempt=a.attempt_id AND t.fence=a.fence AND t.status='RUNNING' AND t.lease_until>clock_timestamp() AS active FROM arc_ref.attempts a JOIN arc_ref.tasks t USING(task_id) WHERE a.task_id=%s AND a.attempt_id=%s AND a.fence=%s", (task_id, attempt_id, fence))).fetchone()
            # Unknown or mismatched ownership is not an expired platform Attempt.
            return bool(row['active']) if row is not None else None

    async def active_attempts(self) -> set[str]:
        async with self.connection() as conn:
            rows = await (await conn.execute("SELECT current_attempt FROM arc_ref.tasks WHERE status='RUNNING' AND lease_until>clock_timestamp() AND current_attempt IS NOT NULL")).fetchall()
            return {str(row['current_attempt']) for row in rows}

    async def rules(self) -> list[Row]:
        async with self.connection() as conn:
            rows = await (await conn.execute('SELECT * FROM arc_ref.rules ORDER BY rule_id')).fetchall()
            return [{'rule_id': row['rule_id'], 'version': row['version'], **row['data']} for row in rows]

    async def document(self, document_id: str) -> Row:
        async with self.connection() as conn:
            row = await self._one(conn, 'SELECT * FROM arc_ref.documents WHERE document_id=%s', (document_id,))
            return {'document_id': row['document_id'], 'version': row['version'], **row['data']}

    async def document_version(self, document_id: str) -> str:
        document = await self.document(document_id)
        # Even an accidentally unversioned teaching-data edit must invalidate cached facts.
        return f"{document['version']}:{digest(document)}"

    async def cache_get(self, key: str) -> Row | None:
        async with self.connection() as conn:
            row = await (await conn.execute('SELECT value FROM arc_ref.tool_cache WHERE cache_key=%s', (key,))).fetchone()
            return row['value'] if row else None

    async def cache_put(self, key: str, value: Row) -> None:
        async with self.connection() as conn:
            await conn.execute('INSERT INTO arc_ref.tool_cache(cache_key,value) VALUES(%s,%s) ON CONFLICT(cache_key) DO UPDATE SET value=excluded.value,created_at=clock_timestamp()', (key, Jsonb(value)))

    async def barrier(self, name: str, execution: Execution) -> None:
        # Only trusted worker test configuration calls this, never user request fields.
        async with self.connection() as conn:
            await conn.execute('INSERT INTO arc_ref.test_barriers(name,task_id,reached,data) VALUES(%s,%s,true,%s) ON CONFLICT(name) DO UPDATE SET task_id=excluded.task_id,reached=true,data=excluded.data', (name, execution.task_id, Jsonb({'attempt_id': execution.attempt_id, 'checkpoint': execution.checkpoint})))
        while True:
            async with self.connection() as conn:
                row = await self._one(conn, 'SELECT released FROM arc_ref.test_barriers WHERE name=%s', (name,))
                if row['released']:
                    return
            await asyncio.sleep(0.05)
