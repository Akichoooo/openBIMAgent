"""vectorworks-mcp stdio 客户端(占位,M1)。

对应文档:
- docs/architecture/ARCHITECTURE.md §5 vectorworks-mcp(自研,从 openBIMForge 单体拆分)
- mcp_servers/vectorworks_mcp/README.md(三层结构 / 文件 IPC / vs_index / 工具集预设)

链路:Agent Core --(MCP stdio)--> vectorworks-mcp --(文件 IPC jobs/ + results/)--> VW 宿主 Python runner(vs.*)。
handoff/hash/approval 在 Executor 层重验(治理不降级);发送前 arity 校验防引擎崩溃;
工具集预设 full / modeling / minimal(VWX_TOOLSET 思路)。
"""

from __future__ import annotations

from typing import Any


class VectorworksMCPClient:
    """自研 vectorworks-mcp 的 stdio 客户端(fastmcp)。"""

    def __init__(self, server_command: list[str], *, toolset: str = "modeling") -> None:
        """TODO(M1): 起 stdio 子进程并握手;按 toolset 预设加载工具集(full/modeling/minimal)。"""
        raise NotImplementedError

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """调用 VW 工具;副作用类调用经 Executor 层 handoff/hash/approval 重验。

        TODO(M1): vs_index.json arity 预检;结果轮询超时与重试策略。
        """
        raise NotImplementedError

    async def close(self) -> None:
        """TODO(M1): 关闭 stdio 连接。"""
        raise NotImplementedError
