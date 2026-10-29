"""Trusted baseline process: direct HTTP, no Runtime ledger or reconciliation."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from config.settings import settings


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    key = parser.add_mutually_exclusive_group(required=True)
    key.add_argument("--key")
    key.add_argument("--key-file", type=Path)
    args = parser.parse_args()
    if not settings.test_mode or settings.model_enabled:
        raise RuntimeError("comparison requires trusted test mode and disabled models")
    request = json.loads(args.request.read_text())
    request["tool_call_id"] = (json.loads(args.key_file.read_text())["tool_call_id"]
                               if args.key_file else args.key)
    async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
        response = await client.post(settings.business_url + "/publish", json=request)
        response.raise_for_status()
        print(json.dumps(response.json()), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
