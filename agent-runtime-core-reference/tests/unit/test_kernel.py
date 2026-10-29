from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core.contracts.action import PreparedAction
from core.contracts.context import ContextBundle
from core.contracts.execution import Execution
from core.contracts.identity import Principal
from core.contracts.task import TaskRequest
from core.hooks import Hooks
from core.kernel import Kernel


class Journal:
    def __init__(self):
        self.records = []
        self.actions = {}
        self.result = None

    async def guard(self, execution):
        self.records.append("guard")

    async def pending_action(self, execution, step):
        return self.actions.get(step)

    async def prepare(self, execution, action):
        self.records.append("prepare")
        self.actions[action.step_no] = action
        return action

    async def checkpoint(self, execution, action, observation, context, artifact=None):
        self.records.append("checkpoint")
        if action.tool == "report.render":
            assert artifact is not None

    async def finish(self, execution, result):
        self.result = result
        self.records.append("finish")

    async def fail(self, execution, code):
        raise AssertionError("hooks must not fail committed task")


class Context:
    async def build(self, execution):
        return ContextBundle(rules=[], protected_facts={})

    def observe(self, context, observation):
        return {**context, **observation}


class Tools:
    def __init__(self, journal):
        self.journal = journal

    async def invoke(self, action: PreparedAction, execution: Execution) -> dict[str, Any]:
        assert action.step_no in self.journal.actions
        self.journal.records.append("invoke")
        return {"from_actual_observation": action.tool}

    async def stop(self):
        pass


class BrokenTelemetry:
    async def emit(self, event, fields):
        raise RuntimeError("simulated telemetry failure")


@pytest.mark.asyncio
async def test_barriers_and_hook_isolation():
    execution = Execution(task_id="t", attempt_id="a", fence=1,
                          deadline_at=datetime.now(UTC)+timedelta(seconds=20),
                          principal=Principal(principal_id="p"),
                          request=TaskRequest(document_id="DOC-001", session_id="s"))
    journal = Journal()
    await Kernel(Tools(journal), Context(), journal, hooks=Hooks([BrokenTelemetry()])).run(execution)
    assert journal.result == {"from_actual_observation": "report.render"}
    assert journal.records.count("prepare") == 3
    assert journal.records.count("checkpoint") == 3
    assert journal.records[-1] == "finish"
