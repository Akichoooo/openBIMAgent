"""vectorworks-mcp stdio 客户端:Agent Core 到自研 VW 文件 IPC 服务的统一 async 接口。

链路:Agent Core --(MCP stdio)--> vectorworks-mcp --(文件 IPC)--> VW 宿主 runner(vs.*)。
服务端负责发送前 arity 校验与 handoff/hash/approval 门禁;本客户端负责 MCP 生命周期、
工具名白名单、结果解包和错误归一化，不绕过服务端治理。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from openbimagent.assembly.vectorworks_plan import (
    VectorworksCapabilities,
    VectorworksExecutionPlan,
    VectorworksExecutionReceipt,
    validate_plan_capabilities,
)

COMMAND_TIMEOUT = 60.0
SERVER_PATH = Path(__file__).resolve().parents[3] / "mcp_servers" / "vectorworks_mcp" / "server" / "server.py"
TOOLSETS_PATH = Path(__file__).resolve().parents[3] / "mcp_servers" / "vectorworks_mcp" / "toolsets.json"
VALID_TOOLSETS = frozenset({"full", "modeling", "minimal"})
MCP_TOOLS = frozenset({"ping", "describe_capabilities", "execute_plan", "execute_vs_code"})


class VectorworksClientError(RuntimeError):
    """MCP 握手、调用、协议解包或 VW 执行失败。"""


class VectorworksMCPClient:
    """自研 vectorworks-mcp 的 stdio 客户端。

    ``connect`` 启动 FastMCP 子进程并执行 ping/describe_capabilities 双探针；
    ``execute_code`` 始终调用服务端 ``execute_vs_code``，由服务端执行 arity 与审批门禁。
    测试可预注入 ``_mcp_client``，避免启动真实子进程。
    """

    def __init__(
        self,
        server_command: list[str] | None = None,
        *,
        toolset: str = "modeling",
        jobs_dir: Path | str | None = None,
        results_dir: Path | str | None = None,
        authorized_root: Path | str | None = None,
        default_output_path: Path | str | None = None,
        timeout: float = COMMAND_TIMEOUT,
        server_env: dict[str, str] | None = None,
    ) -> None:
        if toolset not in VALID_TOOLSETS:
            raise ValueError(f"toolset 须为 {sorted(VALID_TOOLSETS)},实收 {toolset!r}")
        self._server_command = list(server_command or [sys.executable, str(SERVER_PATH)])
        if not self._server_command:
            raise ValueError("server_command 不能为空")
        self._toolset = toolset
        self._timeout = float(timeout)
        env = dict(server_env or {})
        env["VW_TOOLSET"] = toolset
        env["VW_MCP_TIMEOUT"] = str(timeout)
        if jobs_dir is not None:
            env["VW_MCP_JOBS_DIR"] = str(Path(jobs_dir).resolve())
        if results_dir is not None:
            env["VW_MCP_RESULTS_DIR"] = str(Path(results_dir).resolve())
        if authorized_root is not None:
            env["VW_MCP_AUTHORIZED_ROOT"] = str(Path(authorized_root).resolve())
        self._default_output_path = (
            Path(default_output_path).resolve() if default_output_path is not None else None
        )
        self._server_env = env
        self._mcp_client: Any = None
        self._mcp_cm: Any = None
        self._connected = False
        self._capabilities: dict[str, Any] | None = None
        self._toolset_functions = _load_toolset_functions(toolset)

    @property
    def toolset(self) -> str:
        return self._toolset

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def capabilities(self) -> dict[str, Any] | None:
        return dict(self._capabilities) if self._capabilities is not None else None

    async def connect(self) -> None:
        """启动 MCP stdio 子进程，完成 initialize + ping + capabilities 探针。"""
        if self._connected:
            return
        if self._mcp_client is None:
            await self._stdio_connect()
        try:
            pong = await self.call_tool("ping", {})
            if not _is_pong(pong):
                raise VectorworksClientError(f"健康探针失败:ping 响应={pong!r}")
            caps = await self.call_tool("describe_capabilities", {})
            if caps.get("error"):
                raise VectorworksClientError(f"能力探针失败:{caps['error']}")
            self._capabilities = caps
            self._connected = True
        except Exception:
            await self._unsafe_close()
            raise

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """调用 VW MCP 工具并解包结果；仅允许服务端公开的三个治理入口。"""
        if name not in MCP_TOOLS:
            raise VectorworksClientError(f"未知 Vectorworks MCP 工具 {name!r};允许: {sorted(MCP_TOOLS)}")
        if self._mcp_client is None:
            raise VectorworksClientError("MCP 客户端未连接(忘记 await connect()?)")
        try:
            result = await self._mcp_client.call_tool(name, dict(arguments or {}))
        except Exception as exc:
            raise VectorworksClientError(f"MCP 调用 {name} 异常:{exc}") from exc
        if getattr(result, "is_error", False):
            text = _extract_mcp_text(result) or str(result)
            raise VectorworksClientError(f"MCP 调用 {name} 返回 is_error:{text}")
        return _unpack_mcp_result(result, name)

    async def health_check(self) -> dict[str, Any]:
        """调用 ping；统一返回 ``{"pong": bool, "message": ...}``。"""
        raw = await self.call_tool("ping", {})
        return {"pong": _is_pong(raw), **raw}

    async def describe_capabilities(self) -> dict[str, Any]:
        """返回 VW 版本、toolset、vs_index 状态、文件 IPC 限制和已知问题。"""
        caps = await self.call_tool("describe_capabilities", {})
        self._capabilities = caps
        return caps

    async def execute_plan(
        self,
        plan: VectorworksExecutionPlan | dict[str, Any],
        *,
        output_path: Path | str | None = None,
        approved: bool = False,
        capabilities: VectorworksCapabilities | dict[str, Any] | None = None,
    ) -> VectorworksExecutionReceipt:
        """发送 canonical typed plan；响应必须绑定同一计划身份。"""
        typed_plan = (
            plan
            if isinstance(plan, VectorworksExecutionPlan)
            else VectorworksExecutionPlan.model_validate(plan)
        )
        if capabilities is not None:
            if isinstance(capabilities, VectorworksCapabilities):
                typed_capabilities = capabilities
            elif isinstance(capabilities, dict):
                typed_payload = capabilities.get("typed_execution")
                typed_capabilities = VectorworksCapabilities.model_validate(
                    typed_payload if isinstance(typed_payload, dict) else capabilities
                )
            else:
                raise TypeError("Vectorworks capabilities 必须是 typed model 或 dict")
            validate_plan_capabilities(typed_plan, typed_capabilities)
        effective_output = output_path or self._default_output_path
        if effective_output is None:
            raise ValueError("Vectorworks execute_plan 缺少 output_path/default_output_path")
        target = str(Path(effective_output))
        if not target.lower().endswith(".vwx"):
            raise ValueError("Vectorworks output_path 必须以 .vwx 结尾")
        result = await self.call_tool(
            "execute_plan",
            {
                "plan": typed_plan.model_dump(mode="json"),
                "output_path": target,
                "approved": approved,
            },
        )
        if result.get("ok") is False:
            flag = "approval_gate" if result.get("gate_blocked") else "typed_execution"
            raise VectorworksClientError(
                f"Vectorworks typed plan 执行失败 ({flag}):{result.get('error', 'unknown')}"
            )
        try:
            receipt = VectorworksExecutionReceipt.model_validate(result)
        except Exception as exc:
            raise VectorworksClientError(f"Vectorworks receipt 无效:{exc}") from exc
        if (
            receipt.plan_id != typed_plan.plan_id
            or receipt.idempotency_key != typed_plan.idempotency_key
            or receipt.canonical_sha256 != typed_plan.canonical_sha256
        ):
            raise VectorworksClientError("Vectorworks receipt identity 与请求计划不一致")
        return receipt

    async def execute_code(self, code: str, *, approved: bool = False) -> dict[str, Any]:
        """执行 ``vs.*`` 代码；服务端继续负责 arity 与 handoff/hash/approval 门禁。"""
        if not isinstance(code, str) or not code.strip():
            raise ValueError("Vectorworks code 不能为空")
        unknown = _unknown_vs_functions(code, self._toolset_functions)
        if unknown:
            raise VectorworksClientError(
                f"代码调用了当前 {self._toolset!r} 工具集之外的函数:{unknown};请切换 toolset 或改写代码"
            )
        payload = {"code": code, "approved": approved}
        result = await self.call_tool("execute_vs_code", payload)
        if result.get("ok") is False:
            flags = []
            if result.get("validation_failed"):
                flags.append("arity_validation")
            if result.get("gate_blocked"):
                flags.append("approval_gate")
            suffix = f" ({','.join(flags)})" if flags else ""
            raise VectorworksClientError(f"Vectorworks 执行失败{suffix}:{result.get('error', 'unknown')}")
        return result

    async def close(self) -> None:
        """关闭 MCP 连接并终止服务端子进程；可重复调用。"""
        await self._unsafe_close()
        self._connected = False

    async def _stdio_connect(self) -> None:
        try:
            from fastmcp import Client
        except ImportError as exc:  # pragma: no cover - pyproject 已声明依赖
            raise VectorworksClientError("Vectorworks MCP stdio 需要 fastmcp 依赖") from exc
        config = {
            "mcpServers": {
                "vectorworks": {
                    "command": self._server_command[0],
                    "args": self._server_command[1:],
                    "env": {**os.environ, **self._server_env},
                }
            }
        }
        self._mcp_client = Client(config, name="openbimagent-vectorworks", timeout=self._timeout)
        self._mcp_cm = self._mcp_client
        try:
            await self._mcp_cm.__aenter__()
        except Exception as exc:
            self._mcp_cm = None
            self._mcp_client = None
            raise VectorworksClientError(f"Vectorworks MCP stdio 握手失败:{exc}") from exc

    async def _unsafe_close(self) -> None:
        if self._mcp_cm is not None:
            try:
                await self._mcp_cm.__aexit__(None, None, None)
            except Exception:
                pass
        self._mcp_cm = None
        self._mcp_client = None


def _load_toolset_functions(toolset: str) -> frozenset[str]:
    """读取工具集函数名；文件缺失时返回空集并把校验权交还服务端。"""
    try:
        raw = json.loads(TOOLSETS_PATH.read_text(encoding="utf-8"))
        entry = raw.get(toolset) or {}
        functions = entry.get("functions") or []
        return frozenset(str(name) for name in functions)
    except (OSError, json.JSONDecodeError, AttributeError):
        return frozenset()


def _unknown_vs_functions(code: str, allowed: frozenset[str]) -> list[str]:
    """找出代码中不在当前 toolset 的 ``vs.Func``；allowed 为空时降级不拦截。"""
    if not allowed:
        return []
    import ast

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []  # 服务端 execute_code 的 arity 校验会继续处理语法/正则降级
    used = {
        f"vs.{node.func.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "vs"
    }
    return sorted(used - allowed)


def _is_pong(result: dict[str, Any]) -> bool:
    if result.get("pong") is True:
        return True
    text = str(result.get("result") or result.get("raw") or result.get("message") or "").lower()
    return text == "pong" or text.startswith("pong")


def _extract_mcp_text(result: Any) -> str:
    parts = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "; ".join(parts)


def _unpack_mcp_result(result: Any, tool: str) -> dict[str, Any]:
    """CallToolResult → dict；兼容 structured_content、TextContent 和标量字符串。"""
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        value = structured.get("result", structured)
        parsed = _coerce_result(value)
        if parsed is not None:
            return parsed
    text = _extract_mcp_text(result)
    if text:
        parsed = _coerce_result(text)
        if parsed is not None:
            return parsed
    raise VectorworksClientError(f"MCP 调用 {tool} 响应无法解包:{result!r}")


def _coerce_result(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"result": value}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    if value is not None:
        return {"value": value}
    return None


__all__ = ["VectorworksClientError", "VectorworksMCPClient"]
