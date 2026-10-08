import time
import torch
from typing import Callable

CudaOperation = Callable[[], torch.Tensor]

def require_cuda() -> None:
    if not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA device is not available"
        )

def warm_up(
    operation: CudaOperation,
    iterations: int,
) -> None:
    if iterations < 0:
        raise ValueError(
            "iterations must be non-negative"
        )

    require_cuda()

    with torch.inference_mode():
        for _ in range(iterations):
            operation()

    torch.cuda.synchronize()


def measure_submission_ms(operation: CudaOperation,) -> float:
    """
    测量 CPU 提交一次 CUDA 操作所用的时间。

    计时前同步，排除之前的 CUDA 工作。

    计时结束后还要同步，以免本次未完成的操作
    干扰下一个样本；但后一次同步不能计入时间。
    """
    require_cuda()
    torch.cuda.synchronize()
    start_at = time.perf_counter()

    operation()
    end_at = time.perf_counter()
    torch.cuda.synchronize()
    return (end_at - start_at)*1000

def measure_synchronized_ms(operation: CudaOperation) -> float:
    """
    Measures the time taken to execute a CUDA operation using torch.cuda.synchronize().

    Args:
        operation (CudaOperation): A callable that performs the CUDA operation.

    Returns:
        float: The time taken in milliseconds.
    """
    require_cuda()
    torch.cuda.synchronize()
    start_at = time.perf_counter()

    operation()

    torch.cuda.synchronize()

    return (time.perf_counter() - start_at) * 1000

def measure_cuda_event_ms(operation: CudaOperation) -> float:
    """
    Measures the time taken to execute a CUDA operation using torch.cuda.Event.

    Args:
        operation (CudaOperation): A callable that performs the CUDA operation.

    Returns:
        float: The time taken in milliseconds.
    """
    require_cuda()
    start_event = torch.cuda.Event(enable_timing=True)
    end_event = torch.cuda.Event(enable_timing=True)

    start_event.record()

    operation()

    end_event.record()
    end_event.synchronize()

    return start_event.elapsed_time(end_event)

