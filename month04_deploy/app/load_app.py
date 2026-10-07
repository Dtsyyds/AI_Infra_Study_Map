import asyncio

from .api import create_app
from .request_context import RequestContext


async def slow_agent(
    prompt: str,
    ctx: RequestContext,
) -> str:
    await asyncio.sleep(0.5)
    return f"echo:{prompt}"


app = create_app(
    agent_call=slow_agent,
    max_running=2,
    max_waiting=30,
    max_active=4,
)