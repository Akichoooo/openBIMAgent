"""BlenderMCPClient 单测:socket 直连 + MCP stdio 双传输层(全程 mock,禁真实 Blender)。

覆盖:
- socket 传输层:起本地 TCP 楔子服务端响应 JSON,验证 ping/describe_capabilities/execute_code/
  set_editable_scope/screenshot_or_render(含黑图断言)/batch_render/turntable/restore_snapshot
  的请求格式与响应解包;connect 健康探针;addon 报 error → BlenderClientError;连接失败。
- MCP stdio 传输层:注入 fake fastmcp Client 桩,验证 call_tool 调用、is_error 抛错、
  structured_content['result'] 字符串化二次解析、兜底从 content[].text 解析。
- _unpack_mcp_result 纯函数:dict 直返 / JSON 字符串解析 / 非 JSON 走 raw / 无结构抛错。
- 工厂方法与传输层选择:transport_stdio 注入 BLENDER_PORT env;transport_socket 不依赖 fastmcp。
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from typing import Any

import pytest

from openbimagent.assembly.blender_plan import BlenderBuilder, BlenderCapabilities
from openbimagent.mcp_clients.blender import (
    BlenderClientError,
    BlenderMCPClient,
    _extract_mcp_text,
    _unpack_mcp_result,
)
from test_compiled_utility_ir import solved_payload


# ---------- TCP 楔子服务端(响应 canned JSON,模拟 addon socket 协议) ----------


class _FakeAddonServer:
    """本地 TCP 楔子:addon socket 协议(一行 JSON 命令 → 一行 JSON 响应,chunked recv)。

    按命令 type 路由到 handler 字典;默认返回 {"status":"success","result":{"echo":<params>}}。
    """

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(1)
        self.handlers: dict[str, Any] = {}
        self.received: list[dict[str, Any]] = []
        self._thread: threading.Thread | None = None
        self._stop = False
        self._conn: socket.socket | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _serve(self) -> None:
        try:
            self.sock.settimeout(0.5)
            while not self._stop:
                try:
                    conn, _ = self.sock.accept()
                except socket.timeout:
                    continue
                self._conn = conn
                self._handle(conn)
        except OSError:
            pass

    def _handle(self, conn: socket.socket) -> None:
        """单连接多命令循环:与 fork addon 行为一致(socket_test_client 同连接发多条命令)。

        recv 直到 JSON 完整 → 路由 handler → 回包 → 清空 chunks 等下一条;客户端关连接才退出。
        """
        conn.settimeout(2.0)
        try:
            chunks: list[bytes] = []
            while True:
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break  # 客户端关连接
                chunks.append(chunk)
                try:
                    cmd = json.loads(b"".join(chunks).decode("utf-8"))
                except json.JSONDecodeError:
                    continue  # 不完整,继续 recv
                chunks.clear()  # 一条命令完整,清空等下一条
                self.received.append(cmd)
                handler = self.handlers.get(cmd.get("type"))
                if handler is None:
                    resp = {"status": "success", "result": {"echo": cmd.get("params", {})}}
                else:
                    resp = handler(cmd.get("params", {}))
                conn.sendall(json.dumps(resp).encode("utf-8"))
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
            self._conn = None


@pytest.fixture()
def fake_server():
    server = _FakeAddonServer()
    server.start()
    yield server
    server.stop()


# ---------- socket 传输层 ----------


def test_socket_connect_health_check_and_describe_capabilities(fake_server) -> None:
    """socket connect 完成 ping 健康探针;describe_capabilities 返回解包后的 result dict。"""
    fake_server.handlers["ping"] = lambda p: {
        "status": "success",
        "result": {"pong": True, "blender_version": "5.2.0 LTS", "fork_version": "1.0.0-m0"},
    }
    fake_server.handlers["describe_capabilities"] = lambda p: {
        "status": "success",
        "result": {"server": "obmcp", "host": {"render_engines_legal": ["BLENDER_EEVEE"]}, "tools": []},
    }
    client = BlenderMCPClient.transport_socket(port=fake_server.port, timeout=3.0)

    async def run() -> None:
        await client.connect()
        try:
            assert client.is_connected is True
            pong = await client.health_check()
            assert pong["pong"] is True and pong["blender_version"] == "5.2.0 LTS"
            caps = await client.describe_capabilities()
            assert caps["host"]["render_engines_legal"] == ["BLENDER_EEVEE"]
        finally:
            await client.close()

    asyncio.run(run())
    # ping + describe_capabilities 两条命令已收到
    assert [c["type"] for c in fake_server.received] == ["ping", "ping", "describe_capabilities"]


def test_socket_set_editable_scope_request_format(fake_server) -> None:
    """set_editable_scope 发送 {objects,collections,enabled} 三字段;enabled=False 解锁。"""
    fake_server.handlers["ping"] = lambda p: {"status": "success", "result": {"pong": True}}
    fake_server.handlers["set_editable_scope"] = lambda p: {
        "status": "success",
        "result": {"enabled": p["enabled"], "objects": p["objects"]},
    }
    client = BlenderMCPClient.transport_socket(port=fake_server.port, timeout=3.0)

    async def run() -> None:
        await client.connect()
        try:
            r = await client.set_editable_scope(objects=["M0Cube", "Ground"], enabled=True)
            assert r["enabled"] is True and r["objects"] == ["M0Cube", "Ground"]
            r2 = await client.set_editable_scope(enabled=False)
            assert r2["enabled"] is False
        finally:
            await client.close()

    asyncio.run(run())
    # 验证发出去的请求参数格式
    set_calls = [c for c in fake_server.received if c["type"] == "set_editable_scope"]
    assert len(set_calls) == 2
    assert set_calls[0]["params"] == {"objects": ["M0Cube", "Ground"], "collections": [], "enabled": True}
    assert set_calls[1]["params"] == {"objects": [], "collections": [], "enabled": False}


def test_socket_execute_code_returns_snapshot_path(fake_server) -> None:
    """execute_code 把 addon 返回的 {executed, result, snapshot, scope_checked} 透传。"""
    fake_server.handlers["ping"] = lambda p: {"status": "success", "result": {"pong": True}}
    fake_server.handlers["execute_code"] = lambda p: {
        "status": "success",
        "result": {
            "executed": True,
            "result": "cube-ok",
            "snapshot": "/tmp/snapshot_xxx.blend",
            "scope_checked": True,
        },
    }
    client = BlenderMCPClient.transport_socket(port=fake_server.port, timeout=3.0)

    async def run() -> None:
        await client.connect()
        try:
            r = await client.execute_code("import bpy\nbpy.ops.mesh.primitive_cube_add()")
            assert r["executed"] is True
            assert r["snapshot"] == "/tmp/snapshot_xxx.blend"
            assert r["scope_checked"] is True
        finally:
            await client.close()

    asyncio.run(run())
    exec_calls = [c for c in fake_server.received if c["type"] == "execute_code"]
    assert exec_calls[0]["params"] == {"code": "import bpy\nbpy.ops.mesh.primitive_cube_add()"}


def test_socket_execute_plan_sends_typed_payload_and_validates_receipt(fake_server, tmp_path) -> None:
    plan = BlenderBuilder().build(solved_payload())
    output = tmp_path / "case.blend"
    fake_server.handlers["ping"] = lambda p: {"status": "success", "result": {"pong": True}}
    fake_server.handlers["execute_plan"] = lambda p: {
        "status": "success",
        "result": {
            "receipt_id": "receipt-1",
            "plan_id": p["plan"]["plan_id"],
            "idempotency_key": p["plan"]["idempotency_key"],
            "canonical_sha256": p["plan"]["canonical_sha256"],
            "status": "completed",
            "output_path": p["output_path"],
            "snapshot_path": None,
            "state_path": p["output_path"] + ".openbimagent.json",
            "applied_operations": [],
            "confirmed_object_ids": [],
            "semantic_snapshot": None,
            "errors": [],
        },
    }
    client = BlenderMCPClient.transport_socket(
        port=fake_server.port,
        timeout=3.0,
        authorized_root=tmp_path,
    )

    async def run() -> None:
        await client.connect()
        try:
            receipt = await client.execute_plan(
                plan,
                output_path=output,
                approved=True,
                capabilities=BlenderCapabilities(),
            )
            assert receipt.plan_id == plan.plan_id
        finally:
            await client.close()

    asyncio.run(run())
    call = next(item for item in fake_server.received if item["type"] == "execute_plan")
    assert call["params"] == {
        "plan": plan.model_dump(mode="json"),
        "output_path": str(output.resolve()),
        "approved": True,
    }
    assert "code" not in call["params"]


def test_socket_execute_plan_rejects_scope_escape_and_receipt_tampering(fake_server, tmp_path) -> None:
    plan = BlenderBuilder().build(solved_payload())
    fake_server.handlers["ping"] = lambda p: {"status": "success", "result": {"pong": True}}
    fake_server.handlers["execute_plan"] = lambda p: {
        "status": "success",
        "result": {
            "receipt_id": "receipt-tampered",
            "plan_id": "different-plan",
            "idempotency_key": p["plan"]["idempotency_key"],
            "canonical_sha256": p["plan"]["canonical_sha256"],
            "status": "completed",
            "output_path": p["output_path"],
            "snapshot_path": None,
            "state_path": p["output_path"] + ".openbimagent.json",
            "applied_operations": [],
            "confirmed_object_ids": [],
            "semantic_snapshot": None,
            "errors": [],
        },
    }
    client = BlenderMCPClient.transport_socket(
        port=fake_server.port,
        timeout=3.0,
        authorized_root=tmp_path,
    )

    async def run() -> None:
        await client.connect()
        try:
            with pytest.raises(BlenderClientError, match="超出授权根目录"):
                await client.execute_plan(
                    plan,
                    output_path=tmp_path.parent / "outside.blend",
                    approved=True,
                    capabilities=BlenderCapabilities(),
                )
            with pytest.raises(BlenderClientError, match="plan_id"):
                await client.execute_plan(
                    plan,
                    output_path=tmp_path / "inside.blend",
                    approved=True,
                    capabilities=BlenderCapabilities(),
                )
        finally:
            await client.close()

    asyncio.run(run())


def test_socket_screenshot_or_render_rejects_black_frame(fake_server, tmp_path) -> None:
    """screenshot_or_render: brightness < 0.01 抛 BlenderClientError(黑图断言,与 fork T6 一致)。"""
    fake_server.handlers["ping"] = lambda p: {"status": "success", "result": {"pong": True}}
    fake_server.handlers["get_viewport_screenshot"] = lambda p: {
        "status": "success",
        "result": {"brightness": 0.005, "method": "render_fallback", "filepath": p["filepath"]},
    }
    client = BlenderMCPClient.transport_socket(port=fake_server.port, timeout=3.0)

    async def run() -> None:
        await client.connect()
        try:
            with pytest.raises(BlenderClientError, match="黑图"):
                await client.screenshot_or_render(filepath=str(tmp_path / "shot.png"))
        finally:
            await client.close()

    asyncio.run(run())


def test_socket_screenshot_or_render_passes_non_black(fake_server, tmp_path) -> None:
    """screenshot_or_render: brightness ≥ 0.01 通过,返回完整 result。"""
    fake_server.handlers["ping"] = lambda p: {"status": "success", "result": {"pong": True}}
    fake_server.handlers["get_viewport_screenshot"] = lambda p: {
        "status": "success",
        "result": {"brightness": 0.282, "method": "render_fallback", "filepath": p["filepath"]},
    }
    client = BlenderMCPClient.transport_socket(port=fake_server.port, timeout=3.0)

    async def run() -> None:
        await client.connect()
        try:
            r = await client.screenshot_or_render(filepath=str(tmp_path / "shot.png"), max_size=256)
            assert r["brightness"] == 0.282 and r["method"] == "render_fallback"
        finally:
            await client.close()

    asyncio.run(run())
    shot_calls = [c for c in fake_server.received if c["type"] == "get_viewport_screenshot"]
    assert shot_calls[0]["params"]["max_size"] == 256


def test_socket_batch_render_and_turntable(fake_server, tmp_path) -> None:
    """batch_render / turntable 透传 results 数组;参数格式校验。"""
    fake_server.handlers["ping"] = lambda p: {"status": "success", "result": {"pong": True}}
    fake_server.handlers["batch_render"] = lambda p: {
        "status": "success",
        "result": {
            "count": len(p["cameras"]),
            "all_nonblack": True,
            "results": [{"filepath": f"{p['output_dir']}/batch_{i:03d}_{c}.png", "brightness": 0.3} for i, c in enumerate(p["cameras"])],
        },
    }
    fake_server.handlers["camera_turntable"] = lambda p: {
        "status": "success",
        "result": {
            "frames": p["frames"],
            "all_nonblack": True,
            "results": [{"filepath": f"{p['output_dir']}/tt_{i:03d}.png", "brightness": 0.3} for i in range(p["frames"])],
        },
    }
    client = BlenderMCPClient.transport_socket(port=fake_server.port, timeout=3.0)

    async def run() -> None:
        await client.connect()
        try:
            br = await client.batch_render(output_dir=str(tmp_path / "batch"), cameras=["CamA", "CamB"])
            assert br["count"] == 2 and br["all_nonblack"] is True
            assert len(br["results"]) == 2
            tt = await client.turntable(output_dir=str(tmp_path / "tt"), target="M0Cube", frames=4)
            assert tt["frames"] == 4 and len(tt["results"]) == 4
        finally:
            await client.close()

    asyncio.run(run())
    batch_calls = [c for c in fake_server.received if c["type"] == "batch_render"]
    assert batch_calls[0]["params"]["cameras"] == ["CamA", "CamB"]
    assert batch_calls[0]["params"]["width"] == 512 and batch_calls[0]["params"]["height"] == 512


def test_socket_restore_snapshot(fake_server) -> None:
    """restore_snapshot 透传 snapshot_path 参数;render_loop divergence_fallback 用。"""
    fake_server.handlers["ping"] = lambda p: {"status": "success", "result": {"pong": True}}
    fake_server.handlers["restore_snapshot"] = lambda p: {
        "status": "success",
        "result": {"restored": True, "snapshot_path": p["snapshot_path"]},
    }
    client = BlenderMCPClient.transport_socket(port=fake_server.port, timeout=3.0)

    async def run() -> None:
        await client.connect()
        try:
            r = await client.restore_snapshot(snapshot_path="/tmp/best.blend")
            assert r["restored"] is True
        finally:
            await client.close()

    asyncio.run(run())
    rs_calls = [c for c in fake_server.received if c["type"] == "restore_snapshot"]
    assert rs_calls[0]["params"] == {"snapshot_path": "/tmp/best.blend"}


def test_socket_addon_error_raises_client_error(fake_server) -> None:
    """addon 返回 status=error → BlenderClientError 含 addon message。"""
    fake_server.handlers["ping"] = lambda p: {"status": "success", "result": {"pong": True}}
    fake_server.handlers["execute_code"] = lambda p: {
        "status": "error",
        "message": "AST allowlist violation: import os",
    }
    client = BlenderMCPClient.transport_socket(port=fake_server.port, timeout=3.0)

    async def run() -> None:
        await client.connect()
        try:
            with pytest.raises(BlenderClientError, match="AST allowlist violation"):
                await client.execute_code("import os")
        finally:
            await client.close()

    asyncio.run(run())


def test_socket_connect_failure_raises_client_error() -> None:
    """TCP 连不上(端口空闲)→ BlenderClientError 点名 socket 连接失败。"""
    # 找一个未占用端口:bind 后立即 close,大概率仍空闲
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    client = BlenderMCPClient.transport_socket(port=port, timeout=1.0)

    async def run() -> None:
        with pytest.raises(BlenderClientError, match="socket 连接"):
            await client.connect()

    asyncio.run(run())


def test_socket_health_check_failure_rolls_back_connection(fake_server) -> None:
    """ping 返回 pong=False(或无 pong)→ connect 抛 BlenderClientError 且连接已关闭。"""
    fake_server.handlers["ping"] = lambda p: {"status": "success", "result": {"pong": False}}
    client = BlenderMCPClient.transport_socket(port=fake_server.port, timeout=3.0)

    async def run() -> None:
        with pytest.raises(BlenderClientError, match="健康探针失败"):
            await client.connect()
        # 健康探针失败后必须关连接,不能假连
        assert client.is_connected is False

    asyncio.run(run())


# ---------- MCP stdio 传输层(注入 fake fastmcp Client) ----------


class _FakeTextContent:
    """模拟 fastmcp TextContent(只暴露 .text 属性,_extract_mcp_text 用 getattr 取)。"""

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeCallToolResult:
    """模拟 fastmcp Client.call_tool 返回的 CallToolResult(只暴露本客户端用到的属性)。

    content 接受 dict 形态(测试书写方便)或 _FakeTextContent;dict 自动转 _FakeTextContent,
    与真实 fastmcp TextContent 行为一致(_extract_mcp_text 用 getattr(item, "text") 取值)。
    """

    def __init__(
        self,
        *,
        structured_content: dict[str, Any] | None = None,
        content: list[dict[str, Any] | _FakeTextContent] | None = None,
        is_error: bool = False,
    ) -> None:
        self.structured_content = structured_content
        self.content = [
            _FakeTextContent(item["text"]) if isinstance(item, dict) else item
            for item in (content or [])
        ]
        self.is_error = is_error


class _FakeMCPClient:
    """模拟 fastmcp Client:记录 call_tool 调用,按队列吐出预设结果。"""

    def __init__(self, results: list[_FakeCallToolResult]) -> None:
        self._results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> _FakeCallToolResult:
        self.calls.append({"name": name, "arguments": arguments})
        if not self._results:
            raise AssertionError(f"_FakeMCPClient 没有预设结果可用,call_tool({name}) 多调一次")
        return self._results.pop(0)

    async def __aenter__(self) -> "_FakeMCPClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


def test_stdio_connect_skips_real_subprocess_when_mcp_client_injected() -> None:
    """stdio connect 不真的起子进程:直接注入 _FakeMCPClient,跳过 fastmcp import 与子进程握手。

    验证:connect 完成 ping 探针;后续 call_tool 走 _FakeMCPClient;close 不抛。
    """
    fake_mcp = _FakeMCPClient([
        _FakeCallToolResult(structured_content={"result": '{"pong": true, "blender_version": "5.2.0 LTS"}'}),
    ])
    client = BlenderMCPClient.transport_stdio(port=9887, timeout=3.0)
    # 绕过真实 _stdio_connect(避免起 server.py 子进程):直接注入 fake client + 标记已 enter
    client._mcp_client = fake_mcp  # type: ignore[private-name-access]
    client._mcp_cm = fake_mcp  # type: ignore[private-name-access]

    async def run() -> None:
        await client.connect()  # 内部 health_check 走 fake ping
        try:
            assert client.is_connected is True
            assert client.transport == "stdio"
        finally:
            await client.close()

    asyncio.run(run())
    assert fake_mcp.calls[0] == {"name": "ping", "arguments": {}}


def test_stdio_call_unpacks_stringified_result(fake_server_unused=None) -> None:
    """MCP structured_content['result'] 是 JSON 字符串(addon dict 被 server 序列化)
    → _stdio_call 二次解析为 dict;调用方拿到 pong=True 等。"""
    fake_mcp = _FakeMCPClient([
        _FakeCallToolResult(structured_content={"result": '{"pong": true, "blender_version": "5.2.0 LTS"}'}),
        _FakeCallToolResult(structured_content={"result": '{"executed": true, "snapshot": "/tmp/x.blend"}'}),
    ])
    client = BlenderMCPClient.transport_stdio(port=9887)
    client._mcp_client = fake_mcp  # type: ignore[private-name-access]
    client._mcp_cm = fake_mcp  # type: ignore[private-name-access]

    async def run() -> None:
        await client.connect()
        try:
            r = await client.execute_code("import bpy")
            assert r["executed"] is True and r["snapshot"] == "/tmp/x.blend"
        finally:
            await client.close()

    asyncio.run(run())
    # 第二次 call_tool 应是 execute_code
    assert fake_mcp.calls[1] == {"name": "execute_code", "arguments": {"code": "import bpy"}}


def test_stdio_execute_plan_uses_typed_tool_and_capability_envelope(tmp_path) -> None:
    plan = BlenderBuilder().build(solved_payload())
    output = tmp_path / "stdio.blend"
    receipt = {
        "receipt_id": "receipt-stdio",
        "plan_id": plan.plan_id,
        "idempotency_key": plan.idempotency_key,
        "canonical_sha256": plan.canonical_sha256,
        "status": "completed",
        "output_path": str(output.resolve()),
        "snapshot_path": None,
        "state_path": str(output.resolve()) + ".openbimagent.json",
        "applied_operations": [],
        "confirmed_object_ids": [],
        "semantic_snapshot": None,
        "errors": [],
    }
    fake_mcp = _FakeMCPClient([
        _FakeCallToolResult(structured_content={"result": '{"pong": true}'}),
        _FakeCallToolResult(structured_content={"result": json.dumps(receipt)}),
    ])
    client = BlenderMCPClient.transport_stdio(port=9887, authorized_root=tmp_path)
    client._mcp_client = fake_mcp  # type: ignore[private-name-access]
    client._mcp_cm = fake_mcp  # type: ignore[private-name-access]

    async def run() -> None:
        await client.connect()
        try:
            result = await client.execute_plan(
                plan,
                output_path=output,
                approved=True,
                capabilities={"typed_execution": BlenderCapabilities().model_dump(mode="json")},
            )
            assert result.receipt_id == "receipt-stdio"
        finally:
            await client.close()

    asyncio.run(run())
    assert fake_mcp.calls[1]["name"] == "execute_plan"
    assert fake_mcp.calls[1]["arguments"]["approved"] is True
    assert "code" not in fake_mcp.calls[1]["arguments"]


def test_stdio_call_raises_on_is_error() -> None:
    """MCP 返回 is_error=True → BlenderClientError 含 content[].text。"""
    fake_mcp = _FakeMCPClient([
        _FakeCallToolResult(structured_content={"result": '{"pong": true}'}),  # connect ping
        _FakeCallToolResult(
            is_error=True,
            content=[{"type": "text", "text": "AST allowlist violation: import os"}],
        ),
    ])
    client = BlenderMCPClient.transport_stdio(port=9887)
    client._mcp_client = fake_mcp  # type: ignore[private-name-access]
    client._mcp_cm = fake_mcp  # type: ignore[private-name-access]

    async def run() -> None:
        await client.connect()
        try:
            with pytest.raises(BlenderClientError, match="is_error.*AST allowlist"):
                await client.execute_code("import os")
        finally:
            await client.close()

    asyncio.run(run())


def test_stdio_call_raises_when_mcp_client_not_connected() -> None:
    """未 connect 就调 _stdio_call → BlenderClientError 点名未连接。"""
    client = BlenderMCPClient.transport_stdio(port=9887)

    async def run() -> None:
        with pytest.raises(BlenderClientError, match="未连接"):
            await client.execute_code("import bpy")

    asyncio.run(run())


# ---------- _unpack_mcp_result 纯函数 ----------


def test_unpack_mcp_result_dict_returned_directly() -> None:
    """structured_content['result'] 是 dict → 原样返回。"""
    result = _FakeCallToolResult(structured_content={"result": {"pong": True, "n": 42}})
    out = _unpack_mcp_result(result, "ping")
    assert out == {"pong": True, "n": 42}


def test_unpack_mcp_result_json_string_parsed() -> None:
    """structured_content['result'] 是 JSON 字符串 → 解析为 dict。"""
    result = _FakeCallToolResult(
        structured_content={"result": '{"executed": true, "snapshot": "/tmp/x.blend"}'}
    )
    out = _unpack_mcp_result(result, "execute_code")
    assert out == {"executed": True, "snapshot": "/tmp/x.blend"}


def test_unpack_mcp_result_non_json_string_wrapped_as_raw() -> None:
    """structured_content['result'] 是非 JSON 字符串 → 包成 {'raw': ...} 不抛。"""
    result = _FakeCallToolResult(structured_content={"result": "plain text not json"})
    out = _unpack_mcp_result(result, "ping")
    assert out == {"raw": "plain text not json"}


def test_unpack_mcp_result_falls_back_to_content_text() -> None:
    """structured_content 缺失 → 从 content[].text 解析(部分 server 直接返回 TextContent)。"""
    result = _FakeCallToolResult(
        structured_content=None,
        content=[{"type": "text", "text": '{"pong": true}'}],
    )
    out = _unpack_mcp_result(result, "ping")
    assert out == {"pong": True}


def test_unpack_mcp_result_no_structure_raises() -> None:
    """structured_content 与 content 都没东西 → BlenderClientError 点名无 structured_content。"""
    result = _FakeCallToolResult(structured_content=None, content=[])
    with pytest.raises(BlenderClientError, match="无 structured_content"):
        _unpack_mcp_result(result, "ping")


def test_extract_mcp_text_joins_content_items() -> None:
    """多个 TextContent 拼成 '; ' 分隔字符串(is_error 错误消息用)。"""
    result = _FakeCallToolResult(
        content=[{"type": "text", "text": "first"}, {"type": "text", "text": "second"}]
    )
    assert _extract_mcp_text(result) == "first; second"


# ---------- 工厂方法与传输层选择 ----------


def test_transport_stdio_injects_blender_port_env() -> None:
    """transport_stdio 工厂:server_env 默认注入 BLENDER_PORT/OPENBIMAGENT_BLENDER_TIMEOUT 指向 addon。"""
    client = BlenderMCPClient.transport_stdio(port=9887, timeout=42.0)
    assert client.transport == "stdio"
    assert client._server_env["BLENDER_PORT"] == "9887"  # type: ignore[private-name-access]
    assert client._server_env["OPENBIMAGENT_BLENDER_TIMEOUT"] == "42"  # type: ignore[private-name-access]


def test_transport_socket_does_not_require_fastmcp() -> None:
    """transport_socket 工厂:不依赖 fastmcp(纯 socket);server_command/env 留空。"""
    client = BlenderMCPClient.transport_socket(port=9887)
    assert client.transport == "socket"
    assert client._server_env == {}  # type: ignore[private-name-access]
    assert client._server_command is not None  # type: ignore[private-name-access]


def test_invalid_transport_rejected() -> None:
    """transport 非 stdio|socket → ValueError(直接构造时校验)。"""
    with pytest.raises(ValueError, match="transport"):
        BlenderMCPClient(transport="http")  # type: ignore[arg-type]


def test_socket_call_without_connect_raises() -> None:
    """socket 未 connect 就调方法 → BlenderClientError 点名未连接。"""
    client = BlenderMCPClient.transport_socket(port=9887)

    async def run() -> None:
        with pytest.raises(BlenderClientError, match="未连接"):
            await client.health_check()

    asyncio.run(run())


def test_socket_call_after_close_raises() -> None:
    """close 后再调用 → is_connected=False,BlenderClientError 点名未连接。"""
    fake_server_handlers = {"ping": lambda p: {"status": "success", "result": {"pong": True}}}
    # 复用 fake_server fixture 的逻辑,内联一个最小服务端
    server = _FakeAddonServer()
    server.handlers.update(fake_server_handlers)
    server.start()
    try:
        client = BlenderMCPClient.transport_socket(port=server.port, timeout=3.0)

        async def run() -> None:
            await client.connect()
            await client.close()
            assert client.is_connected is False
            with pytest.raises(BlenderClientError, match="未连接"):
                await client.health_check()

        asyncio.run(run())
    finally:
        server.stop()
