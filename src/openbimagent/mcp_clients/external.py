"""通用 MCP client 桥（P1-1）：把任意第三方 MCP server 的工具挂进微内核能力面。

- 每个 server 成为一个 ``ExternalMcpPlugin``：工具映射为能力 ``mcp:<server>:<tool>``；
- **所有 ``mcp:*`` 能力默认 prompt 策略**（第三方进程/网络写面未知，fail-closed 需人工确认）；
- server 配置来自 ``OPENBIMAGENT_MCP_SERVERS``（JSON，fastmcp MCPConfig 的 servers 段格式）：
  ``{"filesystem": {"command": "npx", "args": ["-y", "@mcp/server-filesystem", "."]},
     "remote": {"url": "https://example.com/mcp"}}``
- 调用语义：每次调用新建短连接（无状态、可恢复），结果序列化为 ``{"data", "content"}``。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from typing import Any

from openbimagent.core.plugin import BIMPlugin, BIMPluginContext, PluginRegistry

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,31}$")
_CALL_TIMEOUT_S = 60


def _run_sync(coro: Any) -> Any:
    """在同步上下文执行协程；若已被事件循环包围（如 ASGI 线程外直接调用）则放独立线程跑。"""
    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False
    if not in_loop:
        return asyncio.run(coro)
    box: dict[str, Any] = {}

    def _t() -> None:
        try:
            box["v"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 — 跨线程透传
            box["e"] = exc

    t = threading.Thread(target=_t, daemon=True)
    t.start()
    t.join(timeout=_CALL_TIMEOUT_S)
    if "e" in box:
        raise box["e"]
    if "v" not in box:
        raise TimeoutError(f"MCP 调用超时（{_CALL_TIMEOUT_S}s）")
    return box["v"]


def _serialize_result(result: Any) -> dict[str, Any]:
    """CallToolResult → 可 JSON 化 dict（data 优先，退化 content 文本拼接）。"""
    data = getattr(result, "data", None)
    if data is None:
        data = getattr(result, "structured_content", None)
    texts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            texts.append(str(text))
    return {"data": data, "content": "\n".join(texts)}


class ExternalMcpPlugin(BIMPlugin):
    """一个第三方 MCP server = 一个插件；工具清单在构造时真实发现（连不上即失败关闭）。"""

    def __init__(self, server_name: str, transport: Any) -> None:
        super().__init__()
        if not _NAME_RE.match(server_name):
            raise ValueError(f"MCP server 名非法（需 slug）: {server_name!r}")
        self.plugin_id = f"plugin.external.mcp.{server_name}"
        self.name = f"外部 MCP · {server_name}"
        self.version = "1.0.0"
        self.description = f"第三方 MCP server {server_name} 的工具桥（每次调用短连接；mcp:* 默认 prompt 策略）"
        self._server_name = server_name
        self._transport = self._normalize_transport(server_name, transport)
        # 构造期真实发现工具清单（fail-closed：连不上直接抛错，注册方负责跳过）
        self._tools = self._discover_tools()
        self.provides_capabilities = tuple(f"mcp:{server_name}:{name}" for name in sorted(self._tools))

    @staticmethod
    def _normalize_transport(server_name: str, transport: Any) -> Any:
        """dict 形式（单 server 条目）包装为 fastmcp MCPConfig；FastMCP 实例等原样透传。"""
        if isinstance(transport, dict):
            return {"mcpServers": {server_name: transport}}
        return transport

    def _discover_tools(self) -> dict[str, Any]:
        from fastmcp import Client

        async def _do() -> dict[str, Any]:
            async with Client(self._transport) as client:
                tools = await client.list_tools()
                return {t.name: t for t in tools}

        return _run_sync(_do())

    def setup(self, ctx: BIMPluginContext) -> None:
        super().setup(ctx)
        for tool_name in self._tools:
            self.register_handler(f"mcp:{self._server_name}:{tool_name}", self._make_handler(tool_name))

    def _make_handler(self, tool_name: str) -> Any:
        def _handler(**kwargs: Any) -> dict[str, Any]:
            return self._call_tool(tool_name, kwargs)

        return _handler

    def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        from fastmcp import Client

        async def _do() -> dict[str, Any]:
            async with Client(self._transport) as client:
                result = await client.call_tool(tool_name, arguments)
                return _serialize_result(result)

        return _run_sync(_do())

    def tool_catalog(self) -> list[dict[str, Any]]:
        """工具清单（名称 + 描述 + inputSchema），供端点/审查展示。"""
        rows = []
        for name, tool in sorted(self._tools.items()):
            rows.append(
                {
                    "capability": f"mcp:{self._server_name}:{name}",
                    "tool": name,
                    "description": getattr(tool, "description", "") or "",
                    "input_schema": getattr(tool, "inputSchema", None) or {},
                }
            )
        return rows


def attach_external_servers(registry: PluginRegistry, config: dict[str, Any]) -> list[str]:
    """把配置中的每个 server 注册为 ExternalMcpPlugin；失败关闭（单个失败不拖垮整体，记日志跳过）。"""
    attached: list[str] = []
    for name, entry in config.items():
        try:
            registry.register(ExternalMcpPlugin(str(name), entry))
            attached.append(str(name))
            logger.info("外部 MCP server 已挂载: %s", name)
        except ValueError as exc:
            if "已存在" in str(exc):
                continue  # 重复 build（测试多 app 实例）：幂等跳过
            logger.warning("外部 MCP server %s 挂载失败（已跳过）: %s", name, exc)
        except Exception as exc:  # noqa: BLE001 — 连接失败等：fail-closed 跳过，不拖垮 app 启动
            logger.warning("外部 MCP server %s 连接失败（已跳过）: %s", name, exc)
    return attached


def attach_external_servers_from_env(registry: PluginRegistry) -> list[str]:
    """从 OPENBIMAGENT_MCP_SERVERS（JSON）读取配置并挂载；未配置返回空。"""
    raw = os.environ.get("OPENBIMAGENT_MCP_SERVERS", "").strip()
    if not raw:
        return []
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("OPENBIMAGENT_MCP_SERVERS JSON 解析失败（未挂载任何外部 server）: %s", exc)
        return []
    if not isinstance(config, dict):
        logger.warning("OPENBIMAGENT_MCP_SERVERS 必须是对象（server 名 → 连接配置）")
        return []
    return attach_external_servers(registry, config)
