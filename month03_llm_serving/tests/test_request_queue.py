import pytest

from month03_llm_serving.request_queue import (
    InferenceRequest,
    RequestQueue,
    RequestQueueFullError,
    RequestQueueEmptyError,
)

def make_request(request_id: str) -> InferenceRequest:
    return InferenceRequest(
        request_id=request_id,
        prompt_tokens=10,
        max_new_tokens=4,
        arrived_at=1.0,
    )

def test_request_queue_is_bounded_and_fifo():
    request_queue = RequestQueue(capacity=2)

    first = make_request("request-1")
    second = make_request("request-2")
    rejected = make_request("request-3")

    request_queue.submit(first)
    request_queue.submit(second)

    with pytest.raises(
        RequestQueueFullError,
        match="队列已满",
    ):
        request_queue.submit(rejected)

    assert len(request_queue) == 2
    assert request_queue.pop_next() is first
    assert request_queue.pop_next() is second
    # assert request_queue.pop_next() == 0

@pytest.mark.parametrize("capacity", [0, -1, True])
def test_request_queue_rejects_invalid_capacity(capacity):
    with pytest.raises(ValueError, match="capacity"):
        RequestQueue(capacity=capacity)


def test_request_queue_rejects_pop_when_empty():
    request_queue = RequestQueue(capacity=1)

    with pytest.raises(
        RequestQueueEmptyError,
        match="队列为空",
    ):
        request_queue.pop_next()
