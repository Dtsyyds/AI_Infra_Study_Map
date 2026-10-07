from prometheus_client import CollectorRegistry, Histogram, Gauge, Counter
from typing import Literal
from .request_context import RequestOutcome

QueueWaitResult = Literal[
    "acquired",
    "cancelled",
    "error",
]

AdmissionRejectionReason = Literal[
    "active_limit",
    "capacity_exceeded",
]

class ServiceMetrics:
    def __init__(self):
        self.registry = CollectorRegistry()

        self.producer_duration_seconds = Histogram(
            "agent_producer_duration_seconds",
            "Producer lifetime in seconds, including internal cleanup.",
            registry=self.registry,
        )

        self.queue_wait_seconds = Histogram(
            "agent_queue_wait_seconds",
            "Time spent waiting for an execution permit.",
            labelnames=("result",),
            registry=self.registry,
        )

        self.running_requests = Gauge(
            "agent_running_requests",
            "Current number of requests holding an execution permit",
            registry=self.registry,
        )

        self.waiting_requests = Gauge(
            "agent_waiting_requests",
            "Current number of requests holding a queue slot",
            registry=self.registry,
        )

        self.active_requests = Gauge(
            "agent_active_requests",
            "Current number of protected HTTP requests",
            registry=self.registry,
        )

        self.http_request_duration_seconds = Histogram(
            "agent_http_request_duration_seconds",
            (
                "End-to-end HTTP request duration for protected "
                "Agent endpoints, including response transmission."
            ),
            registry=self.registry,
        )

        self.request_outcomes_total = Counter(
            "agent_request_outcomes_total",
            "Completed Agent business requests by final outcome.",
            labelnames=("outcome",),
            registry=self.registry,
        )

        # 提前创建所有已知标签，使未发生的结果也显示为 0。
        for outcome in RequestOutcome:
            self.request_outcomes_total.labels(
                outcome=outcome.value,
            )

        self.admission_rejections_total = Counter(
            "agent_admission_rejections_total",
            "Agent requests rejected before entering business execution.",
            labelnames=("reason",),
            registry=self.registry,
        )

        for reason in (
            "active_limit",
            "capacity_exceeded",
        ):
            self.admission_rejections_total.labels(
                reason=reason,
            )

    def observe_producer_duration(
        self,
        started_at: float | None,
        finished_at: float,
    ) -> None:
        # 由你补充：
        # 1. 未启动生产者时，直接返回。
        # 2. 否则计算耗时，并调用 Histogram.observe()。
        if started_at is None:
            return

        duration = finished_at - started_at
        self.producer_duration_seconds.observe(duration)


    def observe_queue_wait(
        self,
        started_at: float,
        finished_at: float,
        result: QueueWaitResult,
    ) -> None:
        duration = finished_at - started_at

        self.queue_wait_seconds.labels(
            result=result,
        ).observe(duration)


    def execution_acquired(self) -> None:
        self.running_requests.inc()

    def execution_released(self) -> None:
        self.running_requests.dec()

    def queue_entered(self) -> None:
        self.waiting_requests.inc()

    def queue_left(self) -> None:
        self.waiting_requests.dec()

    def active_request_entered(self) -> None:
        self.active_requests.inc()

    def active_request_left(self) -> None:
        self.active_requests.dec()

    def observe_http_request_duration(
        self,
        started_at: float,
        finished_at: float,
    ) -> None:
        duration = finished_at - started_at
        self.http_request_duration_seconds.observe(duration)

    def record_request_outcome(
        self,
        outcome: RequestOutcome,
    ) -> None:
        self.request_outcomes_total.labels(
            outcome=outcome.value,
        ).inc()

    def record_admission_rejection(
        self,
        reason: AdmissionRejectionReason,
    ) -> None:
        self.admission_rejections_total.labels(
            reason=reason,
        ).inc()