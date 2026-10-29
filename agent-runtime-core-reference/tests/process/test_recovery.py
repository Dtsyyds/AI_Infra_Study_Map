"""Requires real PostgreSQL and the independent local simulated business service."""
import httpx
import psycopg
import pytest
import pytest_asyncio

from config.settings import settings
from examples.process_scenarios import crash_checkpoint, external_effect, lease_competition

pytestmark = pytest.mark.process


@pytest_asyncio.fixture(autouse=True)
async def real_dependencies():
    try:
        async with await psycopg.AsyncConnection.connect(settings.database_url.get_secret_value()) as conn:
            await conn.execute('SELECT 1 FROM arc_ref.tasks LIMIT 1')
    except psycopg.OperationalError as exc:
        pytest.skip(f'BLOCKED: real PostgreSQL unavailable: {type(exc).__name__}')


async def test_worker_crash_recovers_committed_checkpoint():
    result = await crash_checkpoint()
    assert result['invariants']['checkpoint_recovery']
    assert result['invariants']['completed_step_not_reexecuted']
    assert result['invariants']['real_mcp_read_not_repeated']
    assert result['real_read_counts'] == {'first_worker': 1, 'replacement_worker': 0}
    assert result['completed_step_dispatch_count_before'] == result['completed_step_dispatch_count_after']
    # 完整Docker报告的blocked单独在demo结果记录；本测试只证明进程恢复语义。
    assert result['status'] in ('passed', 'blocked')


@pytest.mark.parametrize('mode', ['idempotent', 'unsafe', 'queryable'])
async def test_external_effect_after_worker_crash(mode):
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(f'{settings.business_url}/health')
            response.raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f'BLOCKED: independent simulated business service unavailable: {type(exc).__name__}')
    result = await external_effect(mode)
    assert result['status'] == 'passed'
    assert result['business_counts']['effects'] == 1
    assert result['business_counts']['requests'] == (2 if mode == 'idempotent' else 1)
    if mode == 'unsafe':
        assert result['recovery_task_status'] == 'NEEDS_REVIEW'
        assert result['recovery_ledger_state'] == 'UNKNOWN'
        assert result['reconciliation']['additional_dispatches'] == 0


async def test_competing_processes_and_stale_worker_rejection():
    result = await lease_competition()
    assert result['status'] == 'passed'
    assert all(result['invariants'].values())
