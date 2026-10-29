import asyncio
import json
import re
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from api.limits import BodyLimit
from config.settings import settings
from core.contracts.identity import RequestContext
from core.contracts.task import TERMINAL, TaskRequest
from core.errors import RuntimeFault
from runtime.observability import set_persistent_metrics
from runtime.observability.telemetry import metrics_response
from runtime.security.policy import authenticate, authorize_reconcile
from runtime.security.redaction import redact
from runtime.storage.store import Store


@asynccontextmanager
async def lifespan(app):
    yield
    from runtime.storage.pool import close_pools
    await close_pools()


app = FastAPI(lifespan=lifespan, title="Agent Runtime Core Reference", version="0.1.0")
store = Store(settings.database_url.get_secret_value())

app.add_middleware(BodyLimit)


def request_context(authorization: Annotated[str | None, Header()] = None) -> RequestContext:
    return RequestContext(principal=authenticate(authorization), request_id=str(uuid4()))


Identity = Annotated[RequestContext, Depends(request_context)]
TaskId = Annotated[str, Path(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")]


@app.exception_handler(RuntimeFault)
async def runtime_error(request: Request, exc: RuntimeFault) -> JSONResponse:
    status = {"UNAUTHENTICATED": 401, "UNAUTHORIZED": 403, "FORBIDDEN": 403,
              "DB_BUSY": 503, "NOT_FOUND": 404, "RATE_LIMITED": 429, "QUOTA_EXCEEDED": 429}.get(exc.code, 409)
    return JSONResponse(status_code=status, content={"error": exc.code})


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "reference_only": True, "planner": "deterministic"}


@app.get("/health/live")
async def live() -> dict[str, bool]:
    return {"live": True}


@app.get("/health/ready")
async def ready() -> JSONResponse:
    from runtime.tools.docker_backend import DockerBackend
    database = False
    try:
        async with store.connection() as conn:
            await conn.execute('SELECT 1 FROM arc_ref.tasks LIMIT 1')
            database = True
    except Exception:
        pass
    docker = await DockerBackend().availability()
    available = database and docker['available']
    return JSONResponse(status_code=200 if available else 503,
                        content={"ready":available,"database":database,"docker":docker['available']})


@app.post("/tasks", status_code=202)
async def submit(body: TaskRequest, identity: Identity,
                 idempotency_key: Annotated[str, Header(min_length=1, max_length=128)]) -> dict[str, Any]:
    if identity.principal.role == "operator":
        raise RuntimeFault("FORBIDDEN")
    # 返回之前 Store 的事务上下文已经成功退出。
    return await store.submit(identity.principal, body, idempotency_key)


@app.get("/tasks/{task_id}")
async def get_task(task_id: TaskId, identity: Identity) -> dict[str, Any]:
    return redact(await store.snapshot(task_id, identity.principal.principal_id))


@app.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: TaskId, identity: Identity) -> dict[str, Any]:
    return await store.cancel(task_id, identity.principal.principal_id)


class Reconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    tool_call_id: str
    outcome: Literal["CONFIRMED_APPLIED", "CONFIRMED_NOT_APPLIED", "INCONCLUSIVE"]
    evidence: str = Field(min_length=10, max_length=2000)


@app.post("/tasks/{task_id}/reconcile")
async def reconcile_task(task_id: TaskId, body: Reconciliation, identity: Identity) -> dict[str, Any]:
    authorize_reconcile(identity.principal)
    return await store.reconcile(task_id, identity.principal, body.version, body.tool_call_id,
                                 body.outcome, body.evidence)


@app.get("/tasks/{task_id}/diagnostics")
async def diagnostics(task_id: TaskId, identity: Identity) -> dict[str, Any]:
    if identity.principal.role != "operator":
        await store.snapshot(task_id, identity.principal.principal_id)
    return redact(await store.diagnostics(task_id))


@app.get("/tasks/{task_id}/artifacts/{artifact_id}")
async def artifact(task_id: TaskId, artifact_id: TaskId, identity: Identity) -> Response:
    await store.snapshot(task_id, identity.principal.principal_id)
    value = await store.artifact(task_id, artifact_id, identity.principal.principal_id)
    return Response(value, media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="review.json"'})


def validate_event_id(value: str | None, version: int) -> int | None:
    if value is None:
        return None
    if not re.fullmatch(r"[0-9]{1,18}", value) or int(value) > version:
        raise HTTPException(400, "invalid or future Last-Event-ID")
    return int(value)


@app.get("/tasks/{task_id}/events")
async def events(task_id: TaskId, identity: Identity, request: Request,
                 last_event_id: Annotated[str | None, Header()] = None) -> StreamingResponse:
    snapshot = await store.snapshot(task_id, identity.principal.principal_id)
    previous = validate_event_id(last_event_id, snapshot["version"])

    async def snapshots():
        nonlocal snapshot
        version = snapshot["version"]
        snapshot["reconciled"] = previous is not None and version > previous + 1
        yield f"id: {version}\nevent: snapshot\ndata: {json.dumps(redact(snapshot))}\n\n"
        while snapshot["status"] not in TERMINAL:
            await asyncio.sleep(1)
            if await request.is_disconnected():
                return
            snapshot = await store.snapshot(task_id, identity.principal.principal_id)
            if snapshot["version"] != version:
                snapshot["reconciled"] = snapshot["version"] > version + 1
                version = snapshot["version"]
                yield f"id: {version}\nevent: snapshot\ndata: {json.dumps(redact(snapshot))}\n\n"

    return StreamingResponse(snapshots(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/metrics")
async def metrics() -> Response:
    set_persistent_metrics(await store.metrics())
    body, content_type = metrics_response()
    return Response(body, headers={"Content-Type": content_type})
