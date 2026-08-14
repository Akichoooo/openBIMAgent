"""插件注册中心 (PluginRegistry) 与微内核调度专项单元测试。"""

from __future__ import annotations

import pytest

from openbimagent.core.plugin import (
    BIMPlugin,
    BIMPluginContext,
    BIMProfile,
    PluginRegistry,
    create_default_plugin_registry,
)


class MockHealthyPlugin(BIMPlugin):
    plugin_id = "plugin.test.healthy"
    name = "Healthy Test Plugin"
    version = "1.0.0"
    provides_capabilities = ("test:echo", "test:add")

    def setup(self, ctx: BIMPluginContext) -> None:
        super().setup(ctx)
        self.register_handler("test:echo", lambda msg: f"echo: {msg}")
        self.register_handler("test:add", lambda a, b: a + b)


class MockFailingPlugin(BIMPlugin):
    plugin_id = "plugin.test.failing"
    name = "Failing Test Plugin"
    version = "1.0.0"
    provides_capabilities = ("test:fail_cap",)

    def setup(self, ctx: BIMPluginContext) -> None:
        super().setup(ctx)
        raise RuntimeError("模拟 Setup 启动异常崩溃")


class MockDependentPlugin(BIMPlugin):
    plugin_id = "plugin.test.dependent"
    name = "Dependent Test Plugin"
    requires_capabilities = ("test:echo",)
    provides_capabilities = ("test:composite",)

    def setup(self, ctx: BIMPluginContext) -> None:
        super().setup(ctx)
        self.register_handler("test:composite", lambda val: f"composite-{val}")


def test_plugin_registration_and_capability_invoke():
    """测试标准插件注册与微内核能力调度 invoke()。"""
    registry = PluginRegistry()
    plugin = MockHealthyPlugin()

    registry.register(plugin)
    assert registry.has_capability("test:echo")
    assert registry.has_capability("test:add")
    assert not registry.has_capability("test:unknown")

    # 执行能力调度
    res_echo = registry.invoke("test:echo", "hello openbim")
    assert res_echo == "echo: hello openbim"

    res_add = registry.invoke("test:add", 10, 20)
    assert res_add == 30


def test_atomic_registration_and_rollback_on_failure():
    """测试原子性注册：当 setup() 失败时，注册表完全回滚，不残留脏能力。"""
    registry = PluginRegistry()
    failing_plugin = MockFailingPlugin()

    with pytest.raises(RuntimeError, match="模拟 Setup 启动异常崩溃"):
        registry.register(failing_plugin)

    # 验证未被污染
    assert not registry.has_capability("test:fail_cap")
    assert registry.get_plugin("plugin.test.failing") is None
    assert len(registry.list_plugins()) == 0


def test_dependency_checking_fail_closed():
    """测试前置依赖能力校验：缺少依赖时拒绝注册。"""
    registry = PluginRegistry()
    dep_plugin = MockDependentPlugin()

    # 未注册 healthy plugin 前，缺少 test:echo 依赖 -> 抛异常
    with pytest.raises(ValueError, match="缺少前置依赖能力"):
        registry.register(dep_plugin, check_dependencies=True)

    # 注册 healthy plugin 后 -> 成功挂载
    registry.register(MockHealthyPlugin())
    registry.register(dep_plugin, check_dependencies=True)
    assert registry.has_capability("test:composite")
    assert registry.invoke("test:composite", 42) == "composite-42"


def test_plugin_unregister_and_teardown():
    """测试安全卸载与能力注销。"""
    registry = PluginRegistry()
    plugin = MockHealthyPlugin()
    registry.register(plugin)

    assert registry.has_capability("test:echo")
    registry.unregister("plugin.test.healthy")

    assert not registry.has_capability("test:echo")
    assert registry.get_plugin("plugin.test.healthy") is None
    with pytest.raises(ValueError, match="未找到提供能力"):
        registry.invoke("test:echo", "test")


def test_profile_management_and_activation():
    """测试 Profile 组合管理与激活校验。"""
    registry = PluginRegistry()
    registry.register(MockHealthyPlugin())

    profile = BIMProfile(
        profile_id="profile.test.demo",
        name="Demo Profile",
        description="Demo Profile Description",
        plugin_ids=("plugin.test.healthy",),
    )
    registry.register_profile(profile)

    # 激活 Profile
    pids = registry.activate_profile("profile.test.demo")
    assert pids == ["plugin.test.healthy"]

    # 激活包含不存在插件的 Profile 应失败
    broken_profile = BIMProfile(
        profile_id="profile.test.broken",
        name="Broken",
        description="",
        plugin_ids=("plugin.test.non_existent",),
    )
    registry.register_profile(broken_profile)
    with pytest.raises(ValueError, match="包含不可用插件"):
        registry.activate_profile("profile.test.broken")


def test_default_system_plugin_registry_and_solvers_dispatch():
    """测试系统默认插件注册表中全部核心求解器真实调度。"""
    registry = create_default_plugin_registry()

    # 验证核心能力全覆盖
    assert registry.has_capability("solver:straight_gravity")
    assert registry.has_capability("solver:network_gravity")
    assert registry.has_capability("solver:grid_route")
    assert registry.has_capability("solver:hydraulic")
    assert registry.has_capability("solver:self_healing")
    assert registry.has_capability("rules:gb50289")
    assert registry.has_capability("graph:spatial_3d")
    assert registry.has_capability("benchmark:academic_ablation")

    # 验证 export_inventory 格式符合 DSH 契约
    inv = registry.export_inventory()
    assert inv["plugin_count"] >= 6
    assert len(inv["active_plugins"]) >= 6
    assert len(inv["ui_slots"]) >= 5
    assert len(inv["profiles"]) >= 2
