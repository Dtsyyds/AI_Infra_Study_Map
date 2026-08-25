import argparse
import json
import math
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from unittest.mock import patch

from fastapi.testclient import TestClient

from month02_rag_agent.api import create_app


class BenchmarkEmbedder:
    """
    基准测试使用的轻量 Embedder。

    该对象只用于满足应用 lifespan 的资源创建契约，
    实际向量计算由 fake_build_document_index() 替代。
    """

    model_name = "benchmark_embedder"
    dimension = 3
    normalized = True


def percentile(
    values: list[float],
    quantile: float,
) -> float | None:
    """使用线性插值计算分位数。"""
    if not values:
        return None

    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile 必须位于 0 和 1 之间")

    # 必须按照从小到大排列，不能使用 reverse=True。
    ordered = sorted(values)

    position = (len(ordered) - 1) * quantile
    lower_index = math.floor(position)
    upper_index = math.ceil(position)

    if lower_index == upper_index:
        return ordered[lower_index]

    weight = position - lower_index

    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * weight


def summarize_latencies(
    values: list[float],
) -> dict[str, int | float | None]:
    """汇总一组延迟，并验证分位数顺序。"""
    if not values:
        return {
            "count": 0,
            "min_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "max_ms": None,
        }

    minimum = min(values)
    maximum = max(values)

    # 注意这里不能把 0.50 和 0.95 写反。
    p50 = percentile(values, 0.50)
    p95 = percentile(values, 0.95)

    if p50 is None or p95 is None:
        raise RuntimeError("非空延迟数据未能计算分位数")

    # 指标不变量：如果计算代码以后被改坏，应立即报错，
    # 不能输出一份看似正常但实际错误的报告。
    if not minimum <= p50 <= p95 <= maximum:
        raise RuntimeError("延迟分位数不满足 min <= P50 <= P95 <= max")

    return {
        "count": len(values),
        "min_ms": round(minimum, 3),
        "p50_ms": round(p50, 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(maximum, 3),
    }


def run_benchmark(
    *,
    total_requests: int,
    concurrency: int,
    capacity: int,
    build_ms: float,
) -> dict:
    """
    执行一次进程内并发基准

    total_requests:
        一共提交多少个 http 请求
    concurrency:
        客户端同时使用多少个线程发送请求
    capacity:
        服务端允许同时执行多少个索引任务
    build_ms:
        模拟一次索引构建所需的时间
    """
    if total_requests < 1:
        raise ValueError("total_requests 必须大于等于 1")

    if concurrency < 1:
        raise ValueError("concurrency 必须大于等于 1")

    if capacity < 1:
        raise ValueError("capacity 必须大于等于 1")

    if build_ms < 0:
        raise ValueError("build_ms 不能小于 0")

    def fake_build_document_index(**_kwargs):
        # sleep 会释放 Python GIL，因此多个请求线程可以同时执行。
        time.sleep(build_ms / 1000)
        return {
            "schema_version": 1,
            "model": "benchmark_embedder",
            "dimension": 3,
            "normalized": True,
            "records": [],
        }

    app = create_app(
        embedder_factory=BenchmarkEmbedder,
        max_concurrent_index_builds=capacity,
    )

    # Event 用来让已经创建的客户端线程尽量同时发起请求
    start_event = threading.Event()

    def send_request(
        client: TestClient,
        request_number: int,
    ) -> dict[str, int | float]:
        start_event.wait()

        started_at = perf_counter()

        response = client.post(
            "/v1/indexes",
            json={
                "input_path": ("month02_rag_agent/tests/fixtures/document.md"),
                "output_path": (f"/tmp/benchmark-index-{request_number}.json"),
                "chunk_size": 100,
                "overlap": 20,
            },
            headers={
                "X-Request-ID": (f"benchmark-request-{request_number:04d}"),
            },
        )

        latency_ms = (perf_counter() - started_at) * 1000

        return {
            "status_code": response.status_code,
            "latency_ms": latency_ms,
        }

    # new= 表示直接使用普通函数替换目标，
    # 不创建 MagicMock 包装层。
    with patch(
        "month02_rag_agent.api.build_document_index",
        new=fake_build_document_index,
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [
                    pool.submit(
                        send_request,
                        client,
                        request_number,
                    )
                    for request_number in range(total_requests)
                ]

                wall_started_at = perf_counter()

                # 统一放行客户端线程。
                start_event.set()

                results = [future.result(timeout=30) for future in futures]

                wall_seconds = perf_counter() - wall_started_at

    status_counts = Counter(result["status_code"] for result in results)
    accepted = status_counts[201]
    rejected = status_counts[503]

    accepted_latencies = [
        result["latency_ms"] for result in results if result["status_code"] == 201
    ]

    rejected_latencies = [
        result["latency_ms"] for result in results if result["status_code"] == 503
    ]

    other_latencies = [
        result["latency_ms"]
        for result in results
        if result["status_code"] not in {201, 503}
    ]

    return {
        "configuration": {
            "total_requests": total_requests,
            "concurrency": concurrency,
            "server_capacity": capacity,
            "simulated_build_ms": build_ms,
        },
        "summary": {
            "accepted_201": accepted,
            "rejected_503": rejected,
            "other_responses": (total_requests - accepted - rejected),
            "success_rate": round(
                accepted / total_requests,
                4,
            ),
            "rejection_rate": round(
                rejected / total_requests,
                4,
            ),
            "wall_seconds": round(
                wall_seconds,
                4,
            ),
            "offered_rps": round(
                total_requests / wall_seconds,
                3,
            ),
            "accepted_rps": round(
                accepted / wall_seconds,
                3,
            ),
        },
        "latency_by_status": {
            "201": summarize_latencies(accepted_latencies),
            "503": summarize_latencies(rejected_latencies),
            "other": summarize_latencies(other_latencies),
        },
        "status_counts": dict(status_counts),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Month02 RAG API 并发背压基准")

    parser.add_argument(
        "--requests",
        type=int,
        default=20,
        help="请求总数",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=20,
        help="客户端并发线程数",
    )
    parser.add_argument(
        "--capacity",
        type=int,
        default=1,
        help="服务端索引构建并发槽位",
    )
    parser.add_argument(
        "--build-ms",
        type=float,
        default=100,
        help="模拟单次索引构建耗时，单位毫秒",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    report = run_benchmark(
        total_requests=args.requests,
        concurrency=args.concurrency,
        capacity=args.capacity,
        build_ms=args.build_ms,
    )

    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
