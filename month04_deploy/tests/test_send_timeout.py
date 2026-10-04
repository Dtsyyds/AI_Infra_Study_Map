import asyncio

import pytest

from month04_deploy.app.send_timeout import (
    SendTimeoutError,
    make_timed_send,
)


@pytest.mark.asyncio
async def test_stalled_send_times_out_and_cleans_up():
    never_finish = asyncio.Event()
    send_cleanup_finished = asyncio.Event()

    async def stalled_send(message):
        try:
            await never_finish.wait()
        finally:
            send_cleanup_finished.set()

    timed_send = make_timed_send(
        stalled_send,
        timeout_seconds=0.02,
    )

    message = {
        "type": "http.response.body",
        "body": b"A",
        "more_body": True,
    }

    with pytest.raises(SendTimeoutError):
        await asyncio.wait_for(
            timed_send(message),
            timeout=1,
        )

    assert send_cleanup_finished.is_set() is True

@pytest.mark.asyncio
async def test_downstream_timeout_is_preserved():
    original_error = TimeoutError("downstream timeout")

    async def failing_send(message):
        raise original_error

    timed_send = make_timed_send(
        failing_send,
        timeout_seconds=1,
    )

    message = {
        "type": "http.response.body",
        "body": b"A",
        "more_body": True,
    }

    with pytest.raises(TimeoutError) as caught:
        await timed_send(message)

    assert caught.value is original_error
    assert not isinstance(caught.value, SendTimeoutError)