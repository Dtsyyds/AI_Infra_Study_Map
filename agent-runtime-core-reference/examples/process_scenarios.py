"""真实子进程故障实验。发布探针仅验证 L2/L3，不代表完整业务审核通过。"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

import httpx
from psycopg.types.json import Jsonb

from config.settings import settings
from core.contracts.action import EffectClass, PreparedAction
from core.contracts.identity import Principal
from core.contracts.task import TaskRequest
from runtime.storage.store import Store
from runtime.tools.registry import Report

ROOT = Path(__file__).resolve().parents[1]
REVIEWER = Principal(principal_id='demo-reviewer')
OPERATOR = Principal(principal_id='demo-operator', role='operator')
Row = dict[str, Any]


def _environment(**overrides: str) -> dict[str, str]:
    # 显式环境避免将宿主代理和已有用户凭据传播到演示子进程。
    return {
        'PATH': f'{ROOT / ".venv/bin"}:/usr/bin:/bin', 'LANG': 'C.UTF-8',
        'PYTHONUNBUFFERED': '1', 'ARC_TEST_MODE': 'true', 'ARC_LEASE_SECONDS': '3',
        'ARC_DATABASE_URL': settings.database_url.get_secret_value(),
        'ARC_BUSINESS_URL': settings.business_url, 'ARC_DOCKER_IMAGE': settings.docker_image,
        **overrides,
    }


async def _spawn(module: str, *args: str, **env: str) -> tuple[asyncio.subprocess.Process, str]:
    log = ROOT / 'reports' / f'process-{uuid4()}.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('wb') as output:
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', module, *args, cwd=ROOT, env=_environment(**env),
            start_new_session=True, stdout=output, stderr=asyncio.subprocess.STDOUT,
        )
    return process, str(log.relative_to(ROOT))


def _descendants(pid: int) -> list[int]:
    """只沿本次启动 PID 的 children 链找子进程，不按宽泛进程名清理。"""
    result = []
    try:
        children = Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
    except FileNotFoundError:
        return []
    for child in children:
        child_pid = int(child)
        result.extend(_descendants(child_pid))
        result.append(child_pid)
    return result


async def _kill(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        # MCP SDK 的 stdio server 可能另建 session，故先记录我们自己的子进程链。
        descendants = _descendants(process.pid)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        for child in descendants:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child, signal.SIGKILL)
    await process.wait()


async def _wait_condition(check: Callable[[], Awaitable[Any]], wait_seconds: float = 25) -> Any:
    async with asyncio.timeout(wait_seconds):
        while True:
            result = await check()
            if result:
                return result
            # 轮询的是明确已提交条件；不是通过等待固定时长猜故障时机。
            await asyncio.sleep(0.05)


async def _barrier(store: Store, name: str, process: asyncio.subprocess.Process) -> Row:
    async def check() -> Row | None:
        async with store.connection() as conn:
            row = await (await conn.execute('SELECT * FROM arc_ref.test_barriers WHERE name=%s AND reached', (name,))).fetchone()
        if row:
            return row
        if process.returncode is not None:
            raise RuntimeError(f'process exited {process.returncode} before barrier {name}')
        return None
    result: Row = await _wait_condition(check)
    return result


async def _wait_expired(store: Store, task_id: str) -> None:
    async def check() -> bool:
        async with store.connection() as conn:
            row = await (await conn.execute('SELECT lease_until<=clock_timestamp() AS expired FROM arc_ref.tasks WHERE task_id=%s', (task_id,))).fetchone()
            return bool(row and row['expired'])
    await _wait_condition(check, wait_seconds=12)


async def _wait_exit(process: asyncio.subprocess.Process, log: str) -> None:
    async with asyncio.timeout(30):
        code = await process.wait()
    if code != 0:
        raise RuntimeError(f'process exit code {code}; evidence: {log}')


async def _release_barrier(store: Store, name: str) -> None:
    async with store.connection() as conn:
        await conn.execute('UPDATE arc_ref.test_barriers SET released=true WHERE name=%s', (name,))


async def _cleanup_task(store: Store, task_id: str) -> None:
    snapshot = await store.snapshot(task_id)
    if snapshot['status'] in ('QUEUED', 'RUNNING'):
        await store.cancel(task_id, REVIEWER.principal_id)
        # A crashed Attempt is never reused to persist cancellation. Claim resolves it after expiry.
        if snapshot['status'] == 'RUNNING':
            await _wait_expired(store, task_id)
        await store.claim('arc-ref-scenario-cleanup', 3, task_id)


async def crash_checkpoint() -> Row:
    """在 Step 0 checkpoint 已提交之后 SIGKILL，观察真实 Worker 恢复。"""
    store = Store(settings.database_url.get_secret_value())
    # 只清除此演示身份、资料和当前版本的精确缓存项，确保第一次真走 MCP。
    # 此字段集合与 ToolRuntime._cache_key 一致，不清空其他任务/身份的缓存。
    cache_identity = {
        'principal': REVIEWER.principal_id, 'scope': REVIEWER.scope,
        'document_id': 'DOC-001', 'document_version': await store.document_version('DOC-001'),
        'tool': 'document.fetch', 'tool_version': '1',
    }
    cache_key = hashlib.sha256(json.dumps(cache_identity, sort_keys=True).encode()).hexdigest()
    async with store.connection() as conn:
        await conn.execute('DELETE FROM arc_ref.tool_cache WHERE cache_key=%s', (cache_key,))
    task = await store.submit(REVIEWER, TaskRequest(document_id='DOC-001', session_id=f'process-c-{uuid4()}', budget_ms=120000), str(uuid4()))
    task_id = task['task_id']
    barrier = f'after_checkpoint:0:{task_id}'
    processes: list[asyncio.subprocess.Process] = []
    logs: list[str] = []
    try:
        first, log = await _spawn('runtime.orchestrator.worker', '--once', '--task-id', task_id,
                                  '--worker-id', 'arc-ref-crash-first', ARC_TEST_BARRIER='after_checkpoint:0')
        processes.append(first)
        logs.append(log)
        reached = await _barrier(store, barrier, first)
        before = await store.diagnostics(task_id)
        assert before['task']['checkpoint'] == 0
        assert len(before['steps']) == 1 and len(before['actions']) == 1
        completed = before['steps'][0]
        old_ledger = next(row for row in before['ledger'] if row['step_no'] == 0)
        await _kill(first)
        await _wait_expired(store, task_id)
        await _release_barrier(store, barrier)
        second, log = await _spawn('runtime.orchestrator.worker', '--once', '--task-id', task_id,
                                   '--worker-id', 'arc-ref-crash-replacement')
        processes.append(second)
        logs.append(log)
        await _wait_exit(second, log)
        after = await store.diagnostics(task_id)
        new_ledger = next(row for row in after['ledger'] if row['step_no'] == 0)
        new_step = next(row for row in after['steps'] if row['step_no'] == 0)
        assert after['task']['task_id'] == task_id
        assert len(after['attempts']) == 2
        assert after['task']['current_attempt'] != reached['data']['attempt_id']
        assert new_step['tool_call_id'] == completed['tool_call_id']
        assert new_step['attempt_id'] == completed['attempt_id']
        assert new_ledger['dispatch_count'] == old_ledger['dispatch_count']
        # MCP Server 仅在真实读取 PostgreSQL 文档后向 stderr 写此标记。
        # 因而这里独立验证执行次数，不把 Runtime ledger 自报当工具执行证据。
        real_read_counts = {
            worker: sum(line.startswith('ARC_REF_DOCUMENT_READ DOC-001 ')
                        for line in (ROOT / log_path).read_text().splitlines())
            for worker, log_path in zip(('first_worker', 'replacement_worker'), logs, strict=True)
        }
        assert real_read_counts == {'first_worker': 1, 'replacement_worker': 0}
        full_completed = after['task']['status'] == 'SUCCEEDED'
        if not full_completed:
            assert after['task']['error_code'] == 'DOCKER_UNAVAILABLE', after['task']
        return {
            'scenario': 'C_worker_checkpoint_crash', 'status': 'passed' if full_completed else 'blocked',
            'task_id': task_id, 'attempt_ids': [row['attempt_id'] for row in after['attempts']],
            'tool_call_id': completed['tool_call_id'], 'logs': logs,
            'invariants': {'checkpoint_recovery': True, 'stable_task_id': True, 'new_attempt': True,
                           'completed_step_not_reexecuted': True, 'original_step_attempt_preserved': True,
                           'real_mcp_read_not_repeated': True},
            'real_read_counts': real_read_counts,
            'completed_step_dispatch_count_before': old_ledger['dispatch_count'],
            'completed_step_dispatch_count_after': new_ledger['dispatch_count'],
            'final_task_status': after['task']['status'],
            'full_report': 'passed' if full_completed else 'blocked: real Docker unavailable',
        }
    finally:
        for process in processes:
            await _kill(process)
        await _release_barrier(store, barrier)
        await _cleanup_task(store, task_id)


async def _counts(store: Store, tool_call_id: str) -> Row:
    async with store.connection() as conn:
        # 单语句读取模拟服务自己的事实；不依赖 Runtime 自报 dispatch_count。
        row = await (await conn.execute(
            'SELECT (SELECT count(*) FROM arc_business.requests WHERE idempotency_key=%s) AS requests,'
            '(SELECT count(*) FROM arc_business.effects WHERE idempotency_key=%s) AS effects',
            (tool_call_id, tool_call_id))).fetchone()
        assert row is not None
        return row


async def external_effect(mode: str) -> Row:
    """业务效果已独立提交，Runtime 尚未收到结果时杀死 Worker。"""
    if mode not in ('idempotent', 'unsafe', 'queryable'):
        raise ValueError('unsupported scenario mode')
    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.get(f'{settings.business_url}/health')
        response.raise_for_status()
    store = Store(settings.database_url.get_secret_value())
    task = await store.submit(REVIEWER, TaskRequest(document_id='DOC-002', session_id=f'process-d-{uuid4()}', budget_ms=120000, publish_mode=cast(Literal['idempotent', 'unsafe', 'queryable'], mode)), str(uuid4()))
    task_id = task['task_id']
    processes: list[asyncio.subprocess.Process] = []
    logs: list[str] = []
    tool_call_id = str(uuid4())
    barrier = f'business:{tool_call_id}'
    try:
        fixture = await store.claim('arc-ref-publish-fixture', 3, task_id)
        assert fixture
        report = Report(document_id='DOC-002', document_version='1', review_decision='PASS',
                        missing_documents=[], citations=[]).model_dump(mode='json')
        arguments = {'report': report, 'mode': mode}
        encoded = json.dumps(arguments, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        prepared = PreparedAction(task_id=task_id, step_no=0, tool_call_id=tool_call_id,
                                  tool='report.publish', arguments=arguments,
                                  arguments_digest=hashlib.sha256(encoded.encode()).hexdigest(),
                                  effect_class={'idempotent': EffectClass.EXTERNAL_IDEMPOTENT,
                                                'unsafe': EffectClass.EXTERNAL_UNSAFE,
                                                'queryable': EffectClass.EXTERNAL_QUERYABLE}[mode])
        await store.prepare(fixture, prepared)
        async with store.connection() as conn:
            await conn.execute('INSERT INTO arc_business.controls(name,value) VALUES(%s,%s)', (tool_call_id, Jsonb({'pause': True})))
            # 仅测试预置：父进程没有派发工具，准备好持久 Action 后让独立探针认领。
            await conn.execute("UPDATE arc_ref.tasks SET lease_until=clock_timestamp()-interval '1 second' WHERE task_id=%s", (task_id,))
        first, log = await _spawn('tests.fakes.publish_probe', task_id)
        processes.append(first)
        logs.append(log)
        reached = await _barrier(store, barrier, first)
        before = await store.diagnostics(task_id)
        assert before['ledger'][0]['state'] == 'DISPATCHED' and before['steps'] == []
        assert await _counts(store, tool_call_id) == {'requests': 1, 'effects': 1}
        first_attempt = before['task']['current_attempt']
        await _kill(first)
        async with store.connection() as conn:
            await conn.execute('UPDATE arc_business.controls SET value=%s WHERE name=%s', (Jsonb({'pause': False}), tool_call_id))
        await _wait_expired(store, task_id)
        second, log = await _spawn('tests.fakes.publish_probe', task_id)
        processes.append(second)
        logs.append(log)
        await _wait_exit(second, log)
        recovered = await store.diagnostics(task_id)
        counts = await _counts(store, tool_call_id)
        assert recovered['task']['current_attempt'] != first_attempt
        assert recovered['actions'][0]['tool_call_id'] == tool_call_id
        assert len(recovered['actions']) == 1
        reconciliation: Row | None = None
        if mode == 'unsafe':
            assert recovered['task']['status'] == 'NEEDS_REVIEW'
            assert recovered['ledger'][0]['state'] == 'UNKNOWN'
            assert counts == {'requests': 1, 'effects': 1}
            inconclusive = await store.reconcile(task_id, OPERATOR, recovered['task']['version'], tool_call_id,
                                                 'INCONCLUSIVE', 'test operator has not yet inspected independent effects')
            assert inconclusive['status'] == 'NEEDS_REVIEW'
            confirmed = await store.reconcile(task_id, OPERATOR, inconclusive['version'], tool_call_id,
                                              'CONFIRMED_APPLIED', f"independent arc_business effect_id={reached['data']['effect_id']}")
            assert confirmed['status'] == 'QUEUED'
            third, log = await _spawn('tests.fakes.publish_probe', task_id)
            processes.append(third)
            logs.append(log)
            await _wait_exit(third, log)
            final = await store.snapshot(task_id)
            assert final['status'] == 'SUCCEEDED'
            assert await _counts(store, tool_call_id) == counts
            reconciliation = {'inconclusive_status': inconclusive['status'], 'confirmed_status': confirmed['status'],
                              'final_status': final['status'], 'additional_dispatches': 0}
        else:
            assert recovered['task']['status'] == 'SUCCEEDED'
            assert counts == {'requests': 2 if mode == 'idempotent' else 1, 'effects': 1}
        return {
            'scenario': f'D_external_effect_{mode}', 'status': 'passed', 'scope': 'isolated L2/L3 publish recovery probe; not document_review completion',
            'task_id': task_id, 'tool_call_id': tool_call_id,
            'attempt_ids': [row['attempt_id'] for row in recovered['attempts']], 'logs': logs,
            'recovery_task_status': recovered['task']['status'], 'recovery_ledger_state': recovered['ledger'][0]['state'],
            'business_counts': counts, 'reconciliation': reconciliation,
            'invariants': {'independent_effect_committed_before_crash': True, 'stable_tool_call_id': True,
                           'new_attempt': True, 'single_business_effect': True,
                           'unsafe_no_automatic_redispatch': mode == 'unsafe' or None},
        }
    finally:
        for process in processes:
            await _kill(process)
        async with store.connection() as conn:
            await conn.execute('UPDATE arc_business.controls SET value=%s WHERE name=%s', (Jsonb({'pause': False}), tool_call_id))
        await _release_barrier(store, barrier)
        await _cleanup_task(store, task_id)


async def lease_competition() -> Row:
    """双独立 Worker 竞争，并让已失效 Worker 在恢复后遭数据库 fencing 拒绝。"""
    store = Store(settings.database_url.get_secret_value())
    task = await store.submit(REVIEWER, TaskRequest(document_id='DOC-001', session_id=f'process-e-{uuid4()}', budget_ms=120000), str(uuid4()))
    task_id = task['task_id']
    barrier = f'after_checkpoint:0:{task_id}'
    processes: list[asyncio.subprocess.Process] = []
    logs: list[str] = []
    try:
        first, log = await _spawn('runtime.orchestrator.worker', '--once', '--task-id', task_id,
                                  '--worker-id', 'arc-ref-competition-one', ARC_TEST_BARRIER='after_checkpoint:0')
        processes.append(first)
        logs.append(log)
        second, log = await _spawn('runtime.orchestrator.worker', '--once', '--task-id', task_id,
                                   '--worker-id', 'arc-ref-competition-two', ARC_TEST_BARRIER='after_checkpoint:0')
        processes.append(second)
        logs.append(log)
        # 任一进程可能获胜，因此通过持久屏障判断，不能固定等待first。
        async def reached() -> Row | None:
            async with store.connection() as conn:
                return await (await conn.execute('SELECT * FROM arc_ref.test_barriers WHERE name=%s AND reached', (barrier,))).fetchone()
        marker: Row = await _wait_condition(reached)
        before = await store.diagnostics(task_id)
        assert len(before['attempts']) == 1
        winner_id = before['attempts'][0]['worker_id']
        winner, loser = (first, second) if winner_id.endswith('one') else (second, first)
        winner_log = logs[0] if winner is first else logs[1]
        await _wait_exit(loser, logs[1] if loser is second else logs[0])
        # SIGSTOP 明确停住心跳；数据库当前时间负责判定真实租约自然到期。
        os.killpg(winner.pid, signal.SIGSTOP)
        await _wait_expired(store, task_id)
        replacement, log = await _spawn('runtime.orchestrator.worker', '--once', '--task-id', task_id,
                                        '--worker-id', 'arc-ref-competition-replacement')
        processes.append(replacement)
        logs.append(log)
        await _wait_exit(replacement, log)
        after = await store.diagnostics(task_id)
        assert len(after['attempts']) == 2
        assert after['task']['current_attempt'] != marker['data']['attempt_id']
        await _release_barrier(store, barrier)
        os.killpg(winner.pid, signal.SIGCONT)
        await _wait_exit(winner, winner_log)
        log_text = (ROOT / winner_log).read_text()
        assert 'LEASE_UNCERTAIN' in log_text
        final = await store.diagnostics(task_id)
        assert final['task']['version'] == after['task']['version']
        assert len(final['steps']) == len(after['steps'])
        return {
            'scenario': 'E_two_workers_and_stale_writer', 'status': 'passed', 'task_id': task_id,
            'attempt_ids': [row['attempt_id'] for row in final['attempts']], 'logs': logs,
            'invariants': {'one_initial_claim': True, 'natural_lease_expiry': True,
                           'replacement_claim': True, 'old_worker_fencing_rejected': True,
                           'old_worker_did_not_modify_task': True},
            'final_task_status': final['task']['status'],
        }
    finally:
        for process in processes:
            await _kill(process)
        await _release_barrier(store, barrier)
        await _cleanup_task(store, task_id)
