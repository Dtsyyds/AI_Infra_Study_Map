"""Bounded request-body buffering, including chunked bodies. No candidate upload API."""

import asyncio

from starlette.responses import JSONResponse


class BodyLimit:
    def __init__(self, app, max_bytes=65536):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        data = bytearray()
        try:
            async with asyncio.timeout(5):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    data.extend(message.get("body", b""))
                    if len(data) > self.max_bytes:
                        return await JSONResponse({"error": "BODY_TOO_LARGE"}, status_code=413)(
                            scope, receive, send
                        )
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            return await JSONResponse({"error": "BODY_TIMEOUT"}, status_code=408)(
                scope, receive, send
            )
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(data), "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)
