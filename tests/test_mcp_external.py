"""P1-1 通用 MCP client 桥测试：真实 fastmcp 内存传输挂 toy server（不走 stdio 防 flake）。

验证：工具发现 → 能力映射 mcp:<server>:<tool> → prompt 策略门（无 confirm 拒、有 confirm 真调用）
→ 结果序列化 → 失败关闭（坏 server 不拖垮注册表）。
"""

import pytest
from fastmcp import FastMCP

from openbimagent.core.plugin import (
    PluginPolicyPromptRequiredError,
    PluginRegistry,
)
from openbimagent.mcp_clients.external import ExternalMcpPlugin, attach_external_servers


def _toy_server() -> FastMCP:
    server = FastMCP("toy")

    @server.tool()
    def add(a: int, b: int) -> int:
        """两数相加。"""
        return a + b

    @server.tool()
    def greet(name: str) -> str:
        """打招呼。"""
        return f"hello {name}"

    return server


@pytest.fixture(scope="module")
def registry() -> PluginRegistry:
    from openbimagent.core.plugin import (
        CapabilityPolicyDecision,
        CapabilityPolicyRule,
    )

    reg = PluginRegistry()
    reg.set_capability_policies(
        [CapabilityPolicyRule(pattern="mcp:*", decision=CapabilityPolicyDecision.PROMPT, justification="外部工具需确认")]
    )
    return reg


@pytest.fixture(scope="module")
def attached(registry: PluginRegistry) -> list[str]:
    return attach_external_servers(registry, {"toy": _toy_server()})


class TestDiscovery:
    def test_attach_and_capability_mapping(self, registry: PluginRegistry, attached: list[str]) -> None:
        assert attached == ["toy"]
        inv = registry.export_inventory()
        caps = inv["capabilities_map"]
        assert caps.get("mcp:toy:add") == "plugin.external.mcp.toy"
        assert caps.get("mcp:toy:greet") == "plugin.external.mcp.toy"

    def test_tool_catalog_has_schema(self, attached: list[str]) -> None:
        plugin = ExternalMcpPlugin("toy2", _toy_server())  # 独立实例看目录
        rows = plugin.tool_catalog()
        by_tool = {r["tool"]: r for r in rows}
        assert by_tool["add"]["description"] == "两数相加。"
        assert "a" in by_tool["add"]["input_schema"].get("properties", {})

    def test_illegal_server_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="slug"):
            ExternalMcpPlugin("Bad Name!", _toy_server())


class TestInvokeThroughPolicyGate:
    def test_prompt_policy_requires_confirm(self, registry: PluginRegistry, attached: list[str]) -> None:
        with pytest.raises(PluginPolicyPromptRequiredError):
            registry.invoke("mcp:toy:add", a=1, b=2)  # 无 confirm：prompt 门拦截

    def test_invoke_with_confirm_returns_real_result(self, registry: PluginRegistry, attached: list[str]) -> None:
        result = registry.invoke("mcp:toy:add", a=2, b=3, confirm=True)
        assert result["data"] == 5 or "5" in result["content"]
        result2 = registry.invoke("mcp:toy:greet", name="bim", confirm=True)
        assert "hello bim" in (result2["content"] + str(result2["data"]))

    def test_unknown_tool_capability(self, registry: PluginRegistry, attached: list[str]) -> None:
        with pytest.raises(ValueError, match="未找到"):
            registry.invoke("mcp:toy:nonexistent", confirm=True)


class TestFailClosed:
    def test_bad_server_skipped_without_breaking_registry(self) -> None:
        reg = PluginRegistry()
        attached = attach_external_servers(reg, {"bad": {"command": "no-such-command-xyz-404", "args": []}})
        assert attached == []  # 连接失败：跳过
        assert reg.export_inventory()["capabilities_map"] == {}  # 注册表不受影响
