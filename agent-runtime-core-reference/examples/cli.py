"""Developer CLI; all task operations use authenticated HTTP, never direct SQL."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from config.settings import settings


async def run(args):
    parsed = urlparse(args.url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username
    ):
        raise ValueError("demo API must be loopback")
    token = os.environ.get("ARC_USER_TOKEN", settings.user_token.get_secret_value())
    async with httpx.AsyncClient(
        base_url=args.url, headers={"Authorization": "Bearer " + token}, trust_env=False, timeout=10
    ) as client:
        if args.command == "submit":
            body = {"session_id": args.session or str(uuid4()), "budget_ms": args.budget_ms}
            if args.case:
                body.update(
                    task_type="code_repair",
                    case_id=args.case,
                    planner_mode=args.planner,
                    repair_strategy=args.strategy,
                )
            else:
                body["document_id"] = args.document
            response = await client.post(
                "/tasks", json=body, headers={"Idempotency-Key": args.key or str(uuid4())}
            )
        elif args.command == "cancel":
            response = await client.post(f"/tasks/{args.task}/cancel")
        elif args.command == "artifact":
            response = await client.get(f"/tasks/{args.task}/artifacts/{args.artifact}")
            response.raise_for_status()
            await asyncio.to_thread(Path(args.output).write_bytes, response.content)
            return
        elif args.command == "wait":
            async with asyncio.timeout(args.seconds):
                while True:
                    response = await client.get(f"/tasks/{args.task}")
                    response.raise_for_status()
                    if response.json()["status"] not in {"QUEUED", "RUNNING"}:
                        break
                    await asyncio.sleep(0.2)
        else:
            suffix = "/diagnostics" if args.command == "diagnostics" else ""
            response = await client.get(f"/tasks/{args.task}{suffix}")
        response.raise_for_status()
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18081")
    sub = parser.add_subparsers(dest="command", required=True)
    submit = sub.add_parser("submit")
    submit.add_argument("--case")
    submit.add_argument("--document", default="DOC-001")
    submit.add_argument("--session")
    submit.add_argument("--key")
    submit.add_argument("--budget-ms", type=int, default=120000)
    submit.add_argument("--planner", choices=["fake", "real"], default="fake")
    submit.add_argument("--strategy", choices=["single", "context", "loop"], default="loop")
    for name in ("status", "cancel", "wait", "diagnostics", "artifact"):
        command = sub.add_parser(name)
        command.add_argument("task")
        if name == "wait":
            command.add_argument("--seconds", type=float, default=60)
        if name == "artifact":
            command.add_argument("artifact")
            command.add_argument("--output", required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
