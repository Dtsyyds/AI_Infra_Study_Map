"""仅协议测试的真实 stdio 子进程，不是业务数据替代服务。"""

from mcp.server import MCPServer
from pydantic import BaseModel

server = MCPServer("arc-reference-protocol-probe", version="1.0")


class ProbeResult(BaseModel):
    computed_sum: int


@server.tool()
def add(left: int, right: int) -> ProbeResult:
    return ProbeResult(computed_sum=left + right)


if __name__ == "__main__":
    server.run(transport="stdio")
