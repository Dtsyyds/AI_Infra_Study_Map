"""仅拉取固定白名单镜像，Docker CLI不读取用户既有配置。"""
import asyncio
import json

from runtime.tools.docker_backend import IMAGE, DockerBackend


async def main() -> int:
    backend = DockerBackend()
    status = await backend.availability()
    if not status["available"]:
        print(json.dumps({"status": "blocked", **status}))
        return 2
    code, output, _ = await backend._docker("pull", IMAGE, max_seconds=180)
    print(output)
    return code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
