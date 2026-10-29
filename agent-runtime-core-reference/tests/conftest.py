"""Close only pools opened in each test's loop; do not leave live test connections."""

import pytest_asyncio

from runtime.storage.pool import close_pools


@pytest_asyncio.fixture(autouse=True)
async def close_test_pools():
    yield
    await close_pools()
