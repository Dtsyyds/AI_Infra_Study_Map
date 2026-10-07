import argparse
import asyncio
import math
import time
from collections import Counter, defaultdict
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class RequestSample:
    latency_seconds: float
    status_code: int | None
    result: str


def percentile(
    values: list[float],
    quantile: float,
) -> float:
    if not values:
        return 0.0

    ordered = sorted(values)

    # nearest-rank 方法：
    # P95 表示至少 95% 的样本不大于该位置的值。
    rank = math.ceil(quantile * len(ordered))
    index = max(0, rank - 1)

    return ordered[index]


def classify_response(
    response: httpx.Response,
) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"http_{response.status_code}"

    if response.status_code == 200:
        return body.get("outcome", "succeeded")

    if response.status_code == 503:
        detail = body.get("detail", {})
        code = detail.get("code", "http_503")

        if code == "active_request_limit":
            return "active_limit"

        return code

    if response.status_code == 504:
        return body.get("outcome", "timed_out")

    return f"http_{response.status_code}"


async def send_request(
    *,
    client: httpx.AsyncClient,
    request_number: int,
    start_gate: asyncio.Event,
    client_limiter: asyncio.Semaphore,
    business_timeout_seconds: float,
) -> RequestSample:
    # 所有任务先在这里等待，尽量形成同时到达。
    await start_gate.wait()

    async with client_limiter:
        started_at = time.perf_counter()

        try:
            response = await client.post(
                "/v1/agent/run",
                json={
                    "prompt": f"load-{request_number}",
                    "timeout_seconds": business_timeout_seconds,
                },
            )

        except httpx.RequestError as exc:
            finished_at = time.perf_counter()

            return RequestSample(
                latency_seconds=finished_at - started_at,
                status_code=None,
                result=f"client_error:{type(exc).__name__}",
            )

        finished_at = time.perf_counter()

        return RequestSample(
            latency_seconds=finished_at - started_at,
            status_code=response.status_code,
            result=classify_response(response),
        )


async def run_load(
    *,
    base_url: str,
    total_requests: int,
    concurrency: int,
    business_timeout_seconds: float,
) -> None:
    start_gate = asyncio.Event()

    # 这是客户端并发限制，与服务端 max_active 不同。
    client_limiter = asyncio.Semaphore(concurrency)

    limits = httpx.Limits(
        max_connections=concurrency,
        max_keepalive_connections=concurrency,
    )

    timeout = httpx.Timeout(10.0)

    async with httpx.AsyncClient(
        base_url=base_url,
        limits=limits,
        timeout=timeout,
    ) as client:
        tasks = [
            asyncio.create_task(
                send_request(
                    client=client,
                    request_number=index,
                    start_gate=start_gate,
                    client_limiter=client_limiter,
                    business_timeout_seconds=(
                        business_timeout_seconds
                    ),
                )
            )
            for index in range(total_requests)
        ]

        wall_started_at = time.perf_counter()

        # 一次性释放所有客户端任务。
        start_gate.set()

        samples = await asyncio.gather(*tasks)
        latencies_by_result: dict[str, list[float]] = defaultdict(list)
        for sample in samples:
            latencies_by_result[sample.result].append(
                sample.latency_seconds
            )

        wall_finished_at = time.perf_counter()
        wall_duration = wall_finished_at - wall_started_at

        latencies = [
            sample.latency_seconds
            for sample in samples
        ]

        result_counts = Counter(
            sample.result
            for sample in samples
        )

        status_counts = Counter(
            sample.status_code
            for sample in samples
        )

        print("=== Load summary ===")
        print(f"requests: {total_requests}")
        print(f"concurrency: {concurrency}")
        print(f"wall_seconds: {wall_duration:.3f}")
        print(
            "throughput_rps: "
            f"{total_requests / wall_duration:.2f}"
        )
        print(f"latency_p50: {percentile(latencies, 0.50):.3f}")
        print(f"latency_p95: {percentile(latencies, 0.95):.3f}")
        print(f"latency_p99: {percentile(latencies, 0.99):.3f}")
        print(f"status_counts: {dict(status_counts)}")
        print(f"result_counts: {dict(result_counts)}")

        metrics_response = await client.get("/metrics")
        metrics_response.raise_for_status()

        interesting_prefixes = (
            "agent_active_requests",
            "agent_running_requests",
            "agent_waiting_requests",
            "agent_http_request_duration_seconds_count",
            "agent_producer_duration_seconds_count",
            "agent_request_outcomes_total",
            "agent_admission_rejections_total",
        )

        print("=== Selected server metrics ===")

        for line in metrics_response.text.splitlines():
            if line.startswith(interesting_prefixes):
                print(line)

        print("=== Latency by result ===")

        for result in sorted(latencies_by_result):
            result_latencies = latencies_by_result[result]

            print(
                f"{result}: "
                f"count={len(result_latencies)}, "
                f"p50={percentile(result_latencies, 0.50):.3f}s, "
                f"p95={percentile(result_latencies, 0.95):.3f}s, "
                f"p99={percentile(result_latencies, 0.99):.3f}s"
            )

        succeeded_count = result_counts.get(
            "succeeded",
            0,
        )

        rejected_count = (
            result_counts.get("active_limit", 0)
            + result_counts.get("capacity_exceeded", 0)
        )

        success_rate = succeeded_count / total_requests
        rejection_rate = rejected_count / total_requests

        print(
            "successful_throughput_rps: "
            f"{succeeded_count / wall_duration:.2f}"
        )
        print(f"success_rate: {success_rate:.2%}")
        print(f"rejection_rate: {rejection_rate:.2%}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Small load test for Month04 Agent service."
    )

    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=12,
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=12,
    )
    parser.add_argument(
        "--business-timeout",
        type=float,
        default=3.0,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.requests <= 0:
        raise SystemExit("--requests must be positive")

    if args.concurrency <= 0:
        raise SystemExit("--concurrency must be positive")

    asyncio.run(
        run_load(
            base_url=args.base_url,
            total_requests=args.requests,
            concurrency=args.concurrency,
            business_timeout_seconds=args.business_timeout,
        )
    )


if __name__ == "__main__":
    main()