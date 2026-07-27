"""blender-mcp fork 客户端:统一 async 接口,MCP stdio 主路径 + socket 直连回退。

对应文档:
- docs/architecture/ARCHITECTURE.md §5 blender-mcp(fork ahujasid@da4e16d,八项改造 a–h)
- mcp_servers/blender_mcp/FORK_NOTES.md(fork 改造清单、socket 协议、describe_capabilities)
- docs/architecture/COMPONENTS.md §5(MCP 工具 ≤12)、§7(AST allowlist + 快照 + 范围锁)

链路两条:
1. **MCP stdio(主路径)**:Agent Core --(fastmcp Client stdio)--> server/server.py
   --(socket localhost:9876/9887)--> addon.py(Blender 宿主 / headless)。
   fastmcp Client 起 server.py 子进程并完成 MCP initialize 握手;调用受限工具集
   (11 个 @mcp.tool,≤12)。fork 已验收冻结:describe_capabilities / ping /
   execute_blender_code / get_viewport_screenshot / batch_render / camera_turntable /
   camera_path_render / set_editable_scope / restore_snapshot / get_object_info / get_scene_info。
   实测端到端跑通(见 relay_workspace/m0_spikes/blender_spike.md § MCP stdio 探针)。
2. **socket 直连(回退)**:Agent Core --(raw TCP socket)--> addon.py。
   协议见 mcp_servers/blender_mcp/tests/socket_test_client.py:一行 JSON 命令
   (`{"type":<cmd>,"params":{...}}`)→ 一行 JSON 响应(`{"status":"success|error","result":{...}}`),
   chunked recv 直到 JSON 完整。fork T1–T10 验收用例全走此路。

# MCP stdio vs socket 取舍结论

- **MCP stdio 优先**:符合 ARCH §5 设计(单一事实源),fastmcp Client 提供 initialize
  握手 / list_tools / call_tool / 异常分类,且与未来 Agent Core 接入零额外适配;
  fork 的 server.py 已做首包重试 + 启动 ping 探针 + 超时可调,稳定性足够。
- **socket 回退**用于两种场景:① 联调期 server.py 不在路径里(直接拿 addon 试);
  ② MCP 握手在某个 fastmcp 版本上失效时退一档保命。socket 是 fork tests 的事实协议,
  无第三方依赖,排查 addon 行为时干扰最小。
- **取舍**:MCP 多一跳 server.py 子进程 + 多一层 JSON 字符串化(addon result 字典 →
  server 字符串化 → MCP structured_content['result'] 再 JSON 解一次);socket 直连
  拿到的就是 dict。本客户端两层都做 `result` 字段解包 + 必要时 JSON 二次解析,对上层
  暴露统一 `dict[str, Any]`,把差异吸收在客户端内部。
- **不做**:不在此层做 AST 预检(单一事实源在 addon,server 侧也不复检;agent 无法绕过
  socket 直接 exec)。范围锁/快照由 addon 强制;`session.record_snapshot` 由 render_loop
  在每批建模前后调用(本客户端只透传 addon 返回的 snapshot 路径)。

统一接口(均 async):connect / health_check / describe_capabilities / execute_code /
set_editable_scope / screenshot_or_render / batch_render / turntable / close。
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from pathlib import Path
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
"""addon socket 默认端口(fork tests 用 9887 区分;server.py 默认 9876,经 BLENDER_PORT 覆盖)。"""

COMMAND_TIMEOUT = 180.0
"""单命令默认超时(秒);EEVAE 首帧着色器编译 ~19s,留足余量(与 server.COMMAND_TIMEOUT 对齐)。"""

RECV_CHUNK = 65536
"""socket recv 块大小(与 socket_test_client 一致)。"""

FORK_SERVER_PATH = Path(__file__).resolve().parents[3] / "mcp_servers" / "blender_mcp" / "server" / "server.py"
"""fork server.py 路径(src/openbimagent/mcp_clients/blender.py → 上溯三级为仓库根)。"""


class BlenderClientError(RuntimeError):
    """客户端层异常:addon 报 error / MCP 调用 is_error / 握手失败 / 超时。"""


class BlenderMCPClient:
    """fork 版 blender-mcp 客户端(统一 async 接口;MCP stdio 主,socket 回退)。

    使用方式:

        client = BlenderMCPClient.transport_stdio(port=9887)  # 或 .transport_socket(port=9887)
        await client.connect()
        try:
            caps = await client.describe_capabilities()
            await client.set_editable_scope(objects=["M0Cube"], enabled=True)
            r = await client.execute_code("import bpy\\nbpy.ops.mesh.primitive_cube_add()")
            shot = await client.screenshot_or_render(filepath="shot.png")
        finally:
            await client.close()

    两种传输层暴露相同方法,返回值统一为 addon 的 `result` dict(`status=success` 时)。
    """

    # ---------- 构造与传输层选择 ----------

    def __init__(
        self,
        *,
        transport: str,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        server_command: list[str] | None = None,
        server_env: dict[str, str] | None = None,
        timeout: float = COMMAND_TIMEOUT,
    ) -> None:
        """不要直接构造;用 `transport_stdio` / `transport_socket` 工厂。

        - transport="stdio":起 server.py 子进程,MCP initialize 握手;server_env 必须包含
          BLENDER_PORT 指向 addon(否则 server 默认连 9876,与 addon 9887 不一致会握手失败)。
        - transport="socket":直接 TCP 连 addon socket(fork tests 协议)。
        """
        if transport not in ("stdio", "socket"):
            raise ValueError(f"transport 须为 stdio|socket,实收 {transport!r}")
        self._transport = transport
        self._host = host
        self._port = port
        self._server_command = server_command or [sys.executable, str(FORK_SERVER_PATH)]
        self._server_env = server_env or {}
        self._timeout = timeout
        self._connected = False
        # stdio 传输层私有状态(lazy import fastmcp,避免测试 mock 时硬依赖)
        self._mcp_client: Any = None
        self._mcp_cm: Any = None
        # socket 传输层私有状态
        self._sock: socket.socket | None = None
        self._sock_lock = asyncio.Lock()  # 串行化 socket send/recv,防并发命令串话

    @classmethod
    def transport_stdio(
        cls,
        *,
        port: int = DEFAULT_PORT,
        host: str = DEFAULT_HOST,
        server_command: list[str] | None = None,
        server_env: dict[str, str] | None = None,
        timeout: float = COMMAND_TIMEOUT,
    ) -> "BlenderMCPClient":
        """MCP stdio 传输层(主路径):fastmcp Client 起 server.py 子进程并完成 MCP 握手。

        server_env 默认注入 BLENDER_PORT/OPENBIMAGENT_BLENDER_TIMEOUT 指向 addon。
        """
        env = {"BLENDER_PORT": str(port), "OPENBIMAGENT_BLENDER_TIMEOUT": str(int(timeout)), **(server_env or {})}
        return cls(
            transport="stdio",
            host=host,
            port=port,
            server_command=server_command,
            server_env=env,
            timeout=timeout,
        )

    @classmethod
    def transport_socket(
        cls,
        *,
        port: int = DEFAULT_PORT,
        host: str = DEFAULT_HOST,
        timeout: float = COMMAND_TIMEOUT,
    ) -> "BlenderMCPClient":
        """socket 直连传输层(回退):raw TCP 到 addon,JSON 行协议(见 socket_test_client)。"""
        return cls(transport="socket", host=host, port=port, timeout=timeout)

    @property
    def transport(self) -> str:
        return self._transport

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ---------- 生命周期 ----------

    async def connect(self) -> None:
        """建立连接并完成健康探针:stdio 走 MCP initialize + ping;socket 走 TCP + ping。

        测试可预注入 `_mcp_client` / `_sock` 跳过真实握手(单测专用);生产路径每次都重连。
        """
        if self._transport == "stdio":
            if self._mcp_client is None:  # 测试可能预注入 fake client
                await self._stdio_connect()
        else:
            if self._sock is None:  # 测试可能预注入 socket
                await self._socket_connect()
        # 健康探针:ping 必须返回 pong=True 才算连上(与 server 启动探针同规则)
        pong = await self.health_check()
        if not pong.get("pong"):
            await self._unsafe_close()
            raise BlenderClientError(f"健康探针失败:ping 未返回 pong,响应={pong!r}")
        self._connected = True

    async def close(self) -> None:
        """关闭连接;stdio 关闭 MCP 客户端(连带终止 server.py 子进程);socket 关闭 TCP。"""
        await self._unsafe_close()
        self._connected = False

    async def _unsafe_close(self) -> None:
        if self._transport == "stdio":
            if self._mcp_cm is not None:
                try:
                    await self._mcp_cm.__aexit__(None, None, None)
                except Exception:
                    pass
                self._mcp_cm = None
                self._mcp_client = None
        else:
            if self._sock is not None:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None

    # ---------- 统一接口 ----------

    async def health_check(self) -> dict[str, Any]:
        """ping addon → 返回 {pong, blender_version, fork_version, ...}(fork 改造 e)。"""
        return await self._call("ping", {})

    async def describe_capabilities(self) -> dict[str, Any]:
        """fork 改造 h:server/host/tools/limits/telemetry/known_issues 全量清单。"""
        return await self._call("describe_capabilities", {})

    async def execute_code(self, code: str) -> dict[str, Any]:
        """执行受限 Python 代码(addon AST allowlist + 操作前快照 + 范围锁校验,fork 改造 c/g)。

        返回 {executed, result, snapshot, scope_checked};失败(addon 抛 error)走 BlenderClientError。
        """
        return await self._call("execute_code", {"code": code})

    async def set_editable_scope(
        self,
        *,
        objects: list[str] | None = None,
        collections: list[str] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """范围锁(fork 改造 g):锁定可编辑对象/集合白名单;enabled=False 解锁。

        fork 默认解锁,render_loop 每批必须显式上锁,防止建模代码越界改/建/删白名单外对象。
        """
        return await self._call(
            "set_editable_scope",
            {"objects": list(objects or []), "collections": list(collections or []), "enabled": enabled},
        )

    async def screenshot_or_render(
        self,
        *,
        filepath: str,
        max_size: int = 512,
        format: str = "png",
    ) -> dict[str, Any]:
        """视口截图(GUI 走 GPUOffScreen,headless 走 render_fallback,fork 改造 f)。

        返回 {brightness, method, filepath};brightness<0.01 视为黑图(addon 已断言,本层复检)。
        """
        result = await self._call(
            "get_viewport_screenshot",
            {"filepath": filepath, "max_size": max_size, "format": format},
        )
        brightness = float(result.get("brightness", 0.0))
        if brightness < 0.01:
            raise BlenderClientError(f"截图黑图:brightness={brightness} < 0.01(filepath={filepath})")
        return result

    async def batch_render(
        self,
        *,
        output_dir: str,
        cameras: list[str],
        width: int = 512,
        height: int = 512,
    ) -> dict[str, Any]:
        """批量正式渲染(fork 改造 d):指定相机列表逐张渲染,返回 {count, all_nonblack, results[]}。"""
        return await self._call(
            "batch_render",
            {"output_dir": output_dir, "cameras": list(cameras), "width": width, "height": height},
        )

    async def turntable(
        self,
        *,
        output_dir: str,
        target: str,
        frames: int = 4,
        width: int = 256,
    ) -> dict[str, Any]:
        """turntable 环绕渲染(fork 改造 d):绕目标对象拍 N 帧,返回 {frames, all_nonblack, results[]}。"""
        return await self._call(
            "camera_turntable",
            {"output_dir": output_dir, "target": target, "frames": frames, "width": width},
        )

    async def restore_snapshot(self, *, snapshot_path: str) -> dict[str, Any]:
        """回滚到指定 .blend 快照(fork 改造 c 的回滚点);render_loop 在 divergence_fallback 时调用。"""
        return await self._call("restore_snapshot", {"snapshot_path": snapshot_path})

    # ---------- stdio 传输层实现 ----------

    async def _stdio_connect(self) -> None:
        """fastmcp Client 起 server.py 子进程 + MCP initialize 握手。"""
        try:
            from fastmcp import Client
        except ImportError as exc:  # pragma: no cover - 环境异常路径,测试不覆盖
            raise BlenderClientError(
                "MCP stdio 路径需要 fastmcp(project 已带依赖);若环境异常请改用 transport_socket"
            ) from exc
        config: dict[str, Any] = {
            "mcpServers": {
                "blender": {
                    "command": self._server_command[0],
                    "args": list(self._server_command[1:]),
                    "env": dict(self._server_env),
                }
            }
        }
        self._mcp_client = Client(config, name="openbimagent-blender", timeout=self._timeout)
        self._mcp_cm = self._mcp_client  # async context manager
        try:
            await self._mcp_cm.__aenter__()
        except Exception as exc:
            self._mcp_cm = None
            self._mcp_client = None
            raise BlenderClientError(f"MCP stdio 握手失败:{exc}") from exc

    async def _stdio_call(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        """MCP call_tool → 解包 structured_content → 必要时 JSON 二次解析(addon result 字符串化)。"""
        if self._mcp_client is None:
            raise BlenderClientError("MCP 客户端未连接(忘记 await connect()?)")
        try:
            result = await self._mcp_client.call_tool(tool, params)
        except Exception as exc:
            raise BlenderClientError(f"MCP 调用 {tool} 异常:{exc}") from exc
        if getattr(result, "is_error", False):
            text = _extract_mcp_text(result) or str(result)  # is_error 兜底 str(result)
            raise BlenderClientError(f"MCP 调用 {tool} 返回 is_error:{text}")
        return _unpack_mcp_result(result, tool)

    # ---------- socket 传输层实现 ----------

    async def _socket_connect(self) -> None:
        """raw TCP 连 addon socket;blocking 模式 + timeout,实际 IO 在 to_thread 里跑。"""
        try:
            sock = socket.create_connection((self._host, self._port), timeout=self._timeout)
        except OSError as exc:
            raise BlenderClientError(f"socket 连接 {self._host}:{self._port} 失败:{exc}") from exc
        sock.settimeout(self._timeout)
        self._sock = sock

    async def _socket_call(self, cmd_type: str, params: dict[str, Any]) -> dict[str, Any]:
        """socket 行协议:发一行 JSON → chunked recv 直到 JSON 完整 → 解包 result dict。

        串行化(asyncio.Lock)防并发命令串话(socket 是流式,多命令交叉会撕帧);
        blocking IO 放 asyncio.to_thread,不卡事件循环(单命令超时由 sock.settimeout 兜底)。
        """
        if self._sock is None:
            raise BlenderClientError("socket 未连接(忘记 await connect()?)")
        payload = json.dumps({"type": cmd_type, "params": params}).encode("utf-8")
        async with self._sock_lock:
            response = await asyncio.to_thread(self._socket_round_trip, payload)
        if response.get("status") == "error":
            raise BlenderClientError(f"addon 报 error:{response.get('message', 'unknown')}")
        result = response.get("result", {})
        if not isinstance(result, dict):
            return {"value": result}
        return result

    def _socket_round_trip(self, payload: bytes) -> dict[str, Any]:
        """blocking send/recv 一次命令(socket 已 settimeout;与 fork socket_test_client 同模式)。"""
        assert self._sock is not None  # 由调用方保证
        self._sock.sendall(payload)
        chunks: list[bytes] = []
        while True:
            chunk = self._sock.recv(RECV_CHUNK)
            if not chunk:
                raise BlenderClientError("addon 关闭了 socket 连接(可能是 Blender 进程退出)")
            chunks.append(chunk)
            try:
                return json.loads(b"".join(chunks).decode("utf-8"))
            except json.JSONDecodeError:
                continue

    # ---------- 调度 ----------

    async def _call(self, cmd_type: str, params: dict[str, Any]) -> dict[str, Any]:
        """根据传输层调度;socket 走原始 type 字段,stdio 走 MCP tool 名(两者同名,无映射)。"""
        if self._transport == "stdio":
            return await self._stdio_call(cmd_type, params)
        return await self._socket_call(cmd_type, params)


# ---------- MCP 响应解包工具(纯函数,可单测) ----------


def _extract_mcp_text(result: Any) -> str:
    """从 CallToolResult.content 取文本(is_error=True 时用于错误消息)。

    空 content 返回空字符串(不兜底 str(result)),让 _unpack_mcp_result 能正确判定无可用文本 → 抛错。
    is_error 路径如需兜底,调用方自行 `text or str(result)`。
    """
    contents = getattr(result, "content", None) or []
    parts: list[str] = []
    for item in contents:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "; ".join(parts)


def _unpack_mcp_result(result: Any, tool: str) -> dict[str, Any]:
    """MCP CallToolResult → addon result dict。

    fork server 把 addon 返回的 dict 序列化为字符串放进 MCP `result` 字段;
    structured_content 形如 {'result': '<json string>'} 或 {'result': {...}}。
    本函数把字符串再 JSON 解一次,失败则原样包成 {'raw': ...} 不抛(调用方按 key 取值)。
    """
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict):
        inner = sc.get("result")
        if isinstance(inner, str):
            try:
                parsed = json.loads(inner)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
            return {"raw": inner}
        if isinstance(inner, dict):
            return inner
        if inner is not None:
            return {"value": inner}
    # 兜底:从 content[].text 解析(部分 server 直接返回 TextContent)
    text = _extract_mcp_text(result)
    if text:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {"raw": text}
    raise BlenderClientError(f"MCP 调用 {tool} 响应无 structured_content:{result!r}")


__all__ = ["BlenderClientError", "BlenderMCPClient"]
