"""Trace 只用于观测；恢复始终读取 PostgreSQL checkpoint。"""

import json
import logging
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

from runtime.security import redact

_LOGGER = logging.getLogger("arc_reference.trace")
_EVENTS = frozenset({"on_start", "on_step", "on_tool", "on_finish", "on_error"})
_TOOLS = frozenset({"document.fetch", "document.check", "report.render", "report.publish",
                    "repair.baseline", "repair.generate", "repair.verify", "repair.report"})
_OUTCOMES = frozenset({"success", "denied", "error", "unknown", "cached"})
_STATUSES = frozenset({"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "NEEDS_REVIEW"})
_LEDGER_STATES = frozenset({"PREPARED", "DISPATCHED", "COMMITTED", "UNKNOWN", "CONFIRMED_APPLIED", "CONFIRMED_NOT_APPLIED"})
_EVENT_COUNT = Counter(
    "arc_reference_events_total", "Runtime lifecycle observations", ["event"]
)
_TOOL_COUNT = Counter(
    "arc_reference_tool_calls_total", "Tool outcomes by fixed tool name", ["tool", "outcome"]
)
_PERSISTED_TASKS = Gauge(
    "arc_reference_persisted_tasks", "Current task counts read from PostgreSQL", ["status"]
)
_PERSISTED_TOOLS = Gauge(
    "arc_reference_persisted_tool_calls", "Current ledger counts read from PostgreSQL", ["tool", "state"]
)
_PERSISTED_DISPATCHES = Gauge(
    "arc_reference_persisted_dispatches", "Recorded dispatches grouped by current ledger state", ["tool", "state"]
)
_ARTIFACT_BYTES = Gauge("arc_reference_artifact_bytes", "Logical artifact bytes read from PostgreSQL")


class TelemetryHook:
    async def emit(self, event: str, fields: dict[str, Any]) -> None:
        category = event if event in _EVENTS else "other"
        _EVENT_COUNT.labels(event=category).inc()
        clean = redact(fields)
        _LOGGER.info(json.dumps({"event": category, **clean}, ensure_ascii=False, default=str))


def record_tool(tool: str, outcome: str) -> None:
    _TOOL_COUNT.labels(
        tool=tool if tool in _TOOLS else "other",
        outcome=outcome if outcome in _OUTCOMES else "other",
    ).inc()


def set_persistent_metrics(snapshot: dict[str, Any]) -> None:
    """API 与 Worker 不共享内存；可抓取的运行指标来自 PG 一致只读聚合。"""
    _PERSISTED_TASKS.clear()
    _PERSISTED_TOOLS.clear()
    _PERSISTED_DISPATCHES.clear()
    task_totals: dict[str, int] = {}
    for row in snapshot["tasks"]:
        status = row["status"] if row["status"] in _STATUSES else "other"
        task_totals[status] = task_totals.get(status, 0) + int(row["count"])
    for status, count in task_totals.items():
        _PERSISTED_TASKS.labels(status=status).set(count)
    tool_totals: dict[tuple[str, str], tuple[int, int]] = {}
    for row in snapshot["tools"]:
        tool = row["tool"] if row["tool"] in _TOOLS else "other"
        state = row["state"] if row["state"] in _LEDGER_STATES else "other"
        count, dispatched = tool_totals.get((tool, state), (0, 0))
        tool_totals[tool, state] = (count + int(row["count"]), dispatched + int(row["dispatch_count"]))
    for (tool, state), (count, dispatched) in tool_totals.items():
        _PERSISTED_TOOLS.labels(tool=tool, state=state).set(count)
        _PERSISTED_DISPATCHES.labels(tool=tool, state=state).set(dispatched)
    _ARTIFACT_BYTES.set(int(snapshot["artifact_bytes"]))


def metrics_response() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
