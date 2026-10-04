"""
app/request_context.py

放纯数据结构
~ RequestPhase
~ RequestOutcome
~ FinalizeEvent
~ RequestContext
"""
import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class RequestPhase(str, Enum):
    ACTIVE = "active"
    FINALIZING = "finalizing"
    FINALIZED = "finalized"

class RequestOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    FAILED = "failed"

class FinalizeEvent(str, Enum):
    COMPLETED = "completed"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    CLIENT_DISCONNECTED = "client_disconnected"
    INTERNAL_ERROR = "internal_error"
    SEND_TIMEOUT = "send_timeout"
    RESPONSE_DEADLINE_EXCEEDED = "response_deadline_exceeded"

class TransportOutcome(str, Enum):
    SENT = "sent"
    DISCONNECTED = "disconnected"
    SEND_TIMED_OUT = "send_timed_out"
    CANCELLED = "cancelled"
    FAILED = "failed"
    RESPONSE_TIMED_OUT = "response_timed_out"

@dataclass(slots=True)
class RequestContext:
    # 身份信息
    request_id: str

    # loop.time() 体系下的绝对截止时间
    deadline: float
    # 生命周期状态
    phase: RequestPhase = RequestPhase.ACTIVE
    outcome: RequestOutcome | None = None
    finalize_winner: FinalizeEvent | None = None

    # 请求级同步原语
    state_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        repr=False,
    )
    """
    state_lock: asyncio.Lock 类型注释属于 dataclass 的字段声明,
    冒号后是类型提示，告诉 IDE / 类型检查器：这个字段应该是 asyncio.Lock
    关键点：Python 运行时不会强制检查类型。它只是注解，不是约束。
    在 @dataclass 里，带注解的字段会被识别为 dataclass 字段。

    field() 是 dataclasses 模块提供的函数，用来定制字段行为。
    不写 field()，默认就是这个字段的默认值。
    写 field() 是为了设置 default_factory、repr、init、compare 等。

    可变对象做默认值，必须用 default_factory，不能直接 =
    """
    cancel_event: asyncio.Event = field(
        default_factory=asyncio.Event,
        repr=False,
    )

    finalized_event: asyncio.Event = field(
        default_factory=asyncio.Event,
        repr=False,
    )

    # 请求独占任务资源
    background_task: asyncio.Task[Any] | None = field(
        default=None,
        repr=False,
    )

    # 请求实际持有的资源
    queue_semaphore: asyncio.Semaphore | None = field(
        default=None,
        repr=False,
    )
    execution_semaphore: asyncio.Semaphore | None = field(
        default=None,
        repr=False,
    )

    deadline_task: asyncio.Task[bool] | None = field(
        default=None,
        repr=False,
    )

    disconnect_task: asyncio.Task[bool] | None = field(
        default=None,
        repr=False,
    )

    stream_queue: asyncio.Queue[str] | None = field(
        default=None,
        repr=False,
    )


    permit_acquired: bool = False
    queue_slot_acquired: bool = False

    transport_outcome: TransportOutcome | None = None
    transport_error: str | None = None
    response_deadline: float | None = None

    # 最终结果
    result: Any | None = None
    error: str | None = None
    trace: Any | None = None

