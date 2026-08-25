import json
import pytest
import threading
from uuid import UUID
from pathlib import Path
from fastapi.testclient import TestClient
from month02_rag_agent.api import create_app
from concurrent.futures import ThreadPoolExecutor


class FakeEmbedder:
    def __init__(self, model_name="fake-model", normalized=False):
        self.model_name = model_name
        self.normalized = normalized
        self.received_texts = []

    def embed_documents(self, texts):
        self.received_texts = list(texts)
        return [[1.0, 0.0] for _ in self.received_texts]


def test_healthz_returns_ok():
    """
    GET /healthz
    returns:
    {
        "status": "ok"
    }
    """
    app = create_app(embedder_factory=FakeEmbedder)
    # app 给 Uvicorn 启动服务使用
    # create_app() 给测试ui创建相互隔离的应用，并允许注入 FakeEmbedder

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# FastAPI 会把iu请求 JSON 转换成 Pydantic 模型，Pydantic 会验证字段类型和必填字段
# HTTP JSON -> Pydantic 校验 -> 业务函数参数 -> build_document_index()
def test_create_index_returns_201_and_summary(tmp_path):
    """
    使用 FakeEmbedder
    请求 POST /v1/indexs
    状态码 201
    响应包含 model、dimension、normalized、record_count
    输出 JSON 文件真实存在
    响应中不要返回真实的 records 和向量
    """
    input_path = Path(__file__).parent / "fixtures" / "document.md"
    output_path = tmp_path / "index.json"

    app = create_app(embedder_factory=FakeEmbedder)

    payload = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "chunk_size": 100,
        "overlap": 20,
    }

    with TestClient(app) as client:
        response = client.post("/v1/indexes", json=payload)

    assert response.status_code == 201

    body = response.json()
    assert body["status"] == "created"
    assert body["model"] == "fake-model"
    assert body["dimension"] == 2
    assert body["normalized"] is False
    assert body["record_count"] >= 1
    assert body["output_path"] == str(output_path)
    assert body["schema_version"] == 1
    assert "records" not in body
    assert "embeddings" not in body

    assert output_path.exists()

    saved_index = json.loads(output_path.read_text(encoding="utf-8"))

    assert "records" in saved_index
    assert len(saved_index["records"]) == body["record_count"]


def test_create_index_returns_404_when_input_missing(tmp_path):
    """
    状态码 404
    返回结构化错误，而不是纯字符串
    至少包含稳定的错误码

    {
        "error": {
            "code": "INPUT_FILE_NOT_FOUND",
            "message": "输入文件不存在"
        }
    }
    """
    input_path = Path(__file__).parent / "fixtures" / "missing.md"
    output_path = tmp_path / "index.json"

    app = create_app(embedder_factory=FakeEmbedder)

    payload = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "chunk_size": 100,
        "overlap": 20,
    }

    with TestClient(app) as client:
        response = client.post("/v1/indexes", json=payload)

    assert response.status_code == 404

    body = response.json()

    assert body == {
        "error": {"code": "INPUT_FILE_NOT_FOUND", "message": "输入文件不存在"}
    }

    assert not output_path.exists()


def test_create_index_returns_422_when_request_invalid(tmp_path):
    output_path = tmp_path / "index.json"
    app = create_app(embedder_factory=FakeEmbedder)

    payload = {"output_path": str(output_path), "chunk_size": 100, "overlap": 20}

    with TestClient(app) as client:
        response = client.post("/v1/indexes", json=payload)

    print(response.json())
    assert response.status_code == 422
    assert not output_path.exists()


def test_create_index_returns_422_when_required_fields_missing(tmp_path):
    app = create_app(embedder_factory=FakeEmbedder)

    payload = {
        "chunk_size": 100,
        "overlap": 20,
    }

    with TestClient(app) as client:
        response = client.post("/v1/indexes", json=payload)

    assert response.status_code == 422

    body = response.json()
    details = body["error"]["details"]

    assert any(
        detail["field"] == "body.input_path" and detail["type"] == "missing"
        for detail in details
    )

    assert any(
        detail["field"] == "body.output_path" and detail["type"] == "missing"
        for detail in details
    )


def test_create_index_returns_422_when_overlap_negative(tmp_path):
    input_path = Path(__file__).parent / "fixtures" / "document.md"
    output_path = tmp_path / "index.json"

    app = create_app(embedder_factory=FakeEmbedder)

    payload = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "chunk_size": 100,
        "overlap": -1,
    }

    with TestClient(app) as client:
        response = client.post("/v1/indexes", json=payload)

    assert response.status_code == 422
    assert not output_path.exists()

    # errors = response.json()["detail"]

    # assert any(
    #     error["loc"] == ["body", "overlap"]
    #     and error["type"] == "greater_than_equal"
    #     for error in errors
    # )
    body = response.json()

    assert body["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert body["error"]["message"] == "请求参数校验失败"

    details = body["error"]["details"]

    assert any(
        detail["field"] == "body.overlap" and detail["type"] == "greater_than_equal"
        for detail in details
    )


def test_create_index_returns_422_when_extra_field_provided(tmp_path):
    input_path = Path(__file__).parent / "fixtures" / "document.md"
    output_path = tmp_path / "index.json"

    app = create_app(embedder_factory=FakeEmbedder)

    payload = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "chunk_size": 100,
        "overlap": 20,
        "outpt": "unexpected_field",
    }

    with TestClient(app) as client:
        response = client.post("/v1/indexes", json=payload)

    assert response.status_code == 422
    assert not output_path.exists()
    print(response.json())
    body = response.json()
    details = body["error"]["details"]

    assert any(
        detail["field"] == "body.outpt" and detail["type"] == "extra_forbidden"
        for detail in details
    )


def test_embedder_lifecycle_loads_once_and_cleans_up():
    factory_call_count = 0

    def counting_factory():
        nonlocal factory_call_count
        # nonlocal 关键字声明：这里的 factory_call_count 不是本函数的局部变量，而是来自外层函数 test_embedder_lifecycle_loads_once_and_cleans_up 的变量。
        factory_call_count += 1
        # 这样内层函数就可以修改外层函数的变量，否则 Python 会把 factory_call_count 当作新的局部变量
        return FakeEmbedder()

    app = create_app(embedder_factory=counting_factory)
    # create_app() 只构建应用，尚未进入 lifespan
    assert factory_call_count == 0

    with TestClient(app) as client:
        assert factory_call_count == 1

        embedder = app.state.embedder

        response1 = client.get("/healthz")
        response2 = client.get("/healthz")

        assert response1.status_code == 200
        assert response2.status_code == 200

        # 健康检查不涉及 embedder，所以 embedder 只应该被创建一次
        assert factory_call_count == 1
        assert app.state.embedder is embedder

    assert app.state.embedder is None


def test_ready_returns_200_when_embedder_loaded():
    app = create_app(embedder_factory=FakeEmbedder)

    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 200

    assert response.json() == {
        "status": "ready",
    }


def test_healthz_stays_alive_when_embedder_not_ready():
    app = create_app(embedder_factory=FakeEmbedder)

    with TestClient(app) as client:
        # 模拟服务运行过程中 Embedder 失效
        app.state.embedder = None

        health_response = client.get("/healthz")
        ready_response = client.get("/readyz")

        assert health_response.status_code == 200
        assert ready_response.status_code == 503
        assert ready_response.json() == {
            "error": {
                "code": "EMBEDDER_NOT_READY",
                "message": "Embedding 模型尚未就绪",
            }
        }


def test_app_startup_fails_when_embedder_factory_raises():
    factory_call_count = 0

    def failing_factory():
        nonlocal factory_call_count
        factory_call_count += 1

        # 模拟模型文件损坏、显存不足或模型加载失败
        raise RuntimeError("embedder load failed")

    app = create_app(embedder_factory=failing_factory)  # 这里只创建应用，不会触发异常

    assert factory_call_count == 0

    with pytest.raises(
        RuntimeError,
        match="embedder load failed",
    ):
        # 进入 TestClient 上下文时才启动 lifespan
        # failing_factory() 在进入 yield 前抛出异常
        with TestClient(app):
            pass

        # 工厂只尝试调用一次，不应该无限重试
        assert factory_call_count == 1


# 测试服务器生成 Request ID
def test_middleware_generates_observability_headers():
    app = create_app(embedder_factory=FakeEmbedder)

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200

    request_id = response.headers["X-Request-ID"]
    # UUID 可以验证字符串是不是合法 UUID
    assert str(UUID(request_id)) == request_id

    # Header 中的值都是字符串
    process_time_ms = float(response.headers["X-Process-Time-MS"])

    assert process_time_ms > 0


# 测试透传上游 request ID
def test_middleware_preserves_incoming_request_id():
    app = create_app(embedder_factory=FakeEmbedder)
    incoming_request_id = "request-from-api-gateway-001"

    with TestClient(app) as client:
        response = client.get(
            "/healthz",
            headers={
                "X-Request-ID": incoming_request_id,
            },
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == incoming_request_id


# 请求校验边界早于业务边界
def test_middleware_adds_headers_to_validate_error(
    tmp_path,
):
    app = create_app(embedder_factory=FakeEmbedder)
    incoming_requests_id = "request-error-request-001"

    payload = {
        "input_path": "unused.md",
        "output_path": str(tmp_path / "index.json"),
        "chunk_size": 100,
        "overlap": -1,  # overlap=-1 在 Pydantic 校验阶段失效，路由和 build_document_index() 不会执行
    }

    with TestClient(app) as client:
        response = client.post(
            "/v1/indexes",
            json=payload,
            headers={
                "X-Request-ID": incoming_requests_id,
            },
        )

    assert response.status_code == 422
    assert response.headers["X-Request-ID"] == incoming_requests_id
    assert float(response.headers["X-Process-Time-Ms"]) > 0

    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"


def test_middleware_adds_headers_to_not_found_error(tmp_path):
    input_path = Path(__file__).parent / "fixtures" / "missing.md"
    output_path = tmp_path / "index.json"

    app = create_app(embedder_factory=FakeEmbedder)

    payload = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "chunk_size": 100,
        "overlap": 20,
    }

    with TestClient(app) as client:
        response = client.post("/v1/indexes", json=payload)

    assert response.status_code == 404
    # 客户端没有传入 request ID，所以服务端生成一个新的 UUID
    request_id = response.headers["X-Request-ID"]
    assert str(UUID(request_id)) == request_id

    assert float(response.headers["X-Process-Time-MS"]) > 0

    assert response.json()["error"]["code"] == "INPUT_FILE_NOT_FOUND"


# tmp_path 和 monkeypatch 都是 pytest fixture，由 pytest 根据参数名自动注入
def test_unexpected_error_returns_structured_500(
    tmp_path,
    monkeypatch,
):

    def failing_build_document_index(**_kwargs):
        # 模拟模型、磁盘在其他内部组件发生未预期故障
        raise RuntimeError("sensitive internal model failure")

    # 必须修改 api.py 实际调用的符号
    monkeypatch.setattr(
        "month02_rag_agent.api.build_document_index",
        failing_build_document_index,
    )

    input_path = Path(__file__).parent / "fixtures" / "document.md"
    output_path = tmp_path / "index.json"

    app = create_app(embedder_factory=FakeEmbedder)

    request_id = "unexpected-error-001"

    payload = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "chunk_size": 100,
        "overlap": 20,
    }

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/indexes",
            json=payload,
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    # assert response.content-type == "text/plain"
    # assert response.text == "INternal Server Error"
    # assert response.error.code is None
    assert response.json() == {
        "error": {
            "code": "INTERNAL_SERVER_ERROR",
            "message": "服务器内部错误",
            "request_id": request_id,
        }
    }

    # 内部异常可记录到服务端日志，但不能直接暴露给客户端
    assert "sensitive internal model failure" not in response.text
    # 失败响应可追踪
    assert response.headers["X-Request-ID"] == request_id

    # 业务失败后不残留不完整索引
    assert not output_path.exists()


def test_create_index_returns_503_when_capacity_is_full(
    tmp_path,
    monkeypatch,
):
    """
    验证索引构建的并发背压。

    场景：
    1. 第一个请求进入业务函数并持续占用执行槽位；
    2. 第二个请求在槽位占满时到达；
    3. 第二个请求必须立即返回结构化 503；
    4. 第二个请求不能真正进入索引构建函数。
    """

    # Event 是线程之间使用的同步信号。
    #
    # first_build_started：
    # 表示第一个请求已经进入业务函数。
    #
    # release_first_build：
    # 表示测试允许第一个请求继续执行并结束。
    first_build_started = threading.Event()
    release_first_build = threading.Event()

    call_count = 0
    # 多线程修改 call_count 需要加锁,避免多个线程同时修改导致数据错乱
    call_count_lock = threading.Lock()

    def controlled_build_document_index(**_kwargs):
        nonlocal call_count
        with call_count_lock:
            call_count += 1
            current_call = call_count

        if current_call == 1:
            first_build_started.set()
            # 第一个请求占用执行槽位，但不立即返回

            if not release_first_build.wait(timeout=5):
                raise RuntimeError("测试超时，第一个请求未释放执行槽位")

        return {
            "schema_version": 1,
            "model": "fake_model",
            "dimension": 3,
            "normalized": True,
            "records": [],
        }

    monkeypatch.setattr(
        "month02_rag_agent.api.build_document_index",
        controlled_build_document_index,
    )

    input_path = Path(__file__).parent / "fixtures" / "document.md"
    output_path = tmp_path

    app = create_app(embedder_factory=FakeEmbedder)

    first_payload = {
        "input_path": str(input_path),
        "output_path": str(output_path / "index-1.json"),
        "chunk_size": 100,
        "overlap": 20,
    }

    second_payload = {
        "input_path": str(input_path),
        "output_path": str(tmp_path / "index-2.json"),
        "chunk_size": 100,
        "overlap": 20,
    }

    with TestClient(app, raise_server_exceptions=False) as client:
        # ThreadPoolExecutor 让第一个 HTTP 请求在另一个线程中执行
        with ThreadPoolExecutor(max_workers=1) as pool:
            first_future = pool.submit(
                client.post,
                "/v1/indexes",
                json=first_payload,
                headers={"X-Request-ID": "index-request-001"},
            )
            try:
                # 必须确认第一个请求已经进入业务函数，
                # 再发送第二个请求，否则可能没有真正形成并发。
                assert first_build_started.wait(timeout=2)
                second_response = client.post(
                    "/v1/indexes",
                    json=second_payload,
                    headers={"X-Request-ID": "index-request-002"},
                )

            finally:
                release_first_build.set()

            first_response = first_future.result(timeout=5)

        assert first_response.status_code == 201
        assert second_response.status_code == 503
        assert second_response.json() == {
            "error": {
                "code": "SERVICE_BUSY",
                "message": "服务器繁忙，请稍后重试",
                "request_id": "index-request-002",
            }
        }
        assert second_response.headers["X-Request-ID"] == "index-request-002"
        assert call_count == 1
        assert second_response.headers["Retry-After"] == "5"


def test_index_capacity_is_released_after_unexpected_error(
    tmp_path,
    monkeypatch,
):
    """
    验证索引任务抛出异常后，并发执行许可不会泄漏。

    场景：
    1. 最大并发数为 1；
    2. 第一个请求获得许可，但业务函数抛出 RuntimeError；
    3. finally 必须归还许可；
    4. 第二个请求应该重新获得许可并返回 201。
    """
    call_count = 0

    def unstable_build_document_index(**_kwargs):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            # 模拟第一个索引任务执行失败。
            raise RuntimeError("simulated index build failure")

        # 第二次调用恢复正常。
        return {
            "schema_version": 1,
            "model": "fake_model",
            "dimension": 3,
            "normalized": True,
            "records": [],
        }

    monkeypatch.setattr(
        "month02_rag_agent.api.build_document_index",
        unstable_build_document_index,
    )

    input_path = Path(__file__).parent / "fixtures" / "document.md"
    first_payload = {
        "input_path": str(input_path),
        "output_path": str(tmp_path / "failed-index.json"),
        "chunk_size": 100,
        "overlap": 20,
    }
    second_payload = {
        "input_path": str(input_path),
        "output_path": str(tmp_path / "recovered-index.json"),
        "chunk_size": 100,
        "overlap": 20,
    }

    app = create_app(
        embedder_factory=FakeEmbedder,
        max_concurrent_index_builds=1,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        first_response = client.post(
            "/v1/indexes",
            json=first_payload,
            headers={"X-Request-ID": "index-request-001"},
        )

        second_response = client.post(
            "/v1/indexes",
            json=second_payload,
            headers={"X-Request-ID": "index-request-002"},
        )

    assert first_response.status_code == 500
    assert first_response.json() == {
        "error": {
            "code": "INTERNAL_SERVER_ERROR",
            "message": "服务器内部错误",
            "request_id": "index-request-001",
        }
    }

    # 如果 finally 没有释放许可，
    # 第二个请求会得到 503，而不是 201。
    assert second_response.status_code == 201

    # 证明第二个请求真正进入了业务函数。
    assert call_count == 2

    # 内部异常不能泄露到客户端。
    assert "simulated index build failure" not in first_response.text
