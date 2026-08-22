"""插件注册中心 (PluginRegistry) 与微内核调度专项单元测试。"""

from __future__ import annotations

import pytest

from openbimagent.core.plugin import (
    BIMPlugin,
    BIMPluginContext,
    BIMProfile,
    CapabilityOverride,
    CapabilityPolicyDecision,
    CapabilityPolicyRule,
    PluginPolicyForbiddenError,
    PluginPolicyPromptRequiredError,
    PluginRegistry,
    create_default_plugin_registry,
)
from openbimagent.utility import (
    GridRouteSolverInput,
    NetworkGravitySolverInput,
    compile_municipal_rule_set,
)
from test_grid_route_solver import route_payload
from test_network_utility_solver import network_payload


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


class MockBaseSolverPlugin(BIMPlugin):
    plugin_id = "plugin.test.base_solver"
    name = "Base Solver"
    provides_capabilities = ("test:solve",)

    def setup(self, ctx: BIMPluginContext) -> None:
        super().setup(ctx)
        self.register_handler("test:solve", lambda x: f"base-{x}")


class MockAltSolverPlugin(BIMPlugin):
    plugin_id = "plugin.test.alt_solver"
    name = "Alt Solver"
    provides_capabilities = ("test:solve_alt",)

    def setup(self, ctx: BIMPluginContext) -> None:
        super().setup(ctx)
        self.register_handler("test:solve_alt", lambda x: f"alt-native-{x}")
        self.register_handler("test:solve", lambda x: f"alt-{x}")


def test_profile_patch_override_and_restore() -> None:
    """Profile 补丁层：激活时重定向能力提供者，停用时还原原绑定。"""
    registry = PluginRegistry()
    registry.register(MockBaseSolverPlugin())
    registry.register(MockAltSolverPlugin())
    assert registry.invoke("test:solve", "x") == "base-x"

    registry.register_profile(
        BIMProfile(
            profile_id="profile.test.patched",
            name="Patched",
            description="补丁测试",
            plugin_ids=("plugin.test.base_solver", "plugin.test.alt_solver"),
            overrides=(
                CapabilityOverride(capability="test:solve", plugin_id="plugin.test.alt_solver", reason="单元测试"),
            ),
        )
    )

    registry.activate_profile("profile.test.patched")
    assert registry.invoke("test:solve", "x") == "alt-x"

    registry.deactivate_profile("profile.test.patched")
    assert registry.invoke("test:solve", "x") == "base-x"


def test_profile_override_fail_closed() -> None:
    """补丁目标未列入 plugin_ids 或缺少能力 Handler 时失败关闭。"""
    registry = PluginRegistry()
    registry.register(MockBaseSolverPlugin())
    registry.register(MockAltSolverPlugin())
    registry.register(MockHealthyPlugin())

    # 目标插件不在 Profile plugin_ids 内
    registry.register_profile(
        BIMProfile(
            profile_id="profile.test.not_listed",
            name="",
            description="",
            plugin_ids=("plugin.test.base_solver",),
            overrides=(CapabilityOverride(capability="test:solve", plugin_id="plugin.test.alt_solver"),),
        )
    )
    with pytest.raises(ValueError, match="未列入 plugin_ids"):
        registry.activate_profile("profile.test.not_listed")

    # 目标插件未注册该能力 Handler
    registry.register_profile(
        BIMProfile(
            profile_id="profile.test.no_handler",
            name="",
            description="",
            plugin_ids=("plugin.test.healthy",),
            overrides=(CapabilityOverride(capability="test:solve", plugin_id="plugin.test.healthy"),),
        )
    )
    with pytest.raises(ValueError, match="未注册能力"):
        registry.activate_profile("profile.test.no_handler")

    # 失败关闭后能力绑定未被污染
    assert registry.invoke("test:solve", "x") == "base-x"


def _healing_inputs(width: int, height: int) -> tuple[NetworkGravitySolverInput, GridRouteSolverInput]:
    n_dict = network_payload()
    r_dict = route_payload(width=width, height=height)
    r_dict["source_ir_sha256"] = n_dict["source_ir_sha256"]
    for sample in r_dict["surface_samples"]:
        sample["ground_elevation_m"] = 11.0
    r_dict["start"] = {
        "node_id": "source",
        "cell": {"x_index": 0, "y_index": 0},
        "invert_anchor_m": 10.0,
    }
    r_dict["end"] = {"node_id": "junction", "cell": {"x_index": 10, "y_index": 0}}
    return (
        NetworkGravitySolverInput.model_validate(n_dict),
        GridRouteSolverInput.model_validate(r_dict),
    )


def test_default_ablation_profile_disables_self_healing() -> None:
    """内置消融 Profile：补丁重定向 solver:self_healing 到单轮直连基线并可还原。"""
    registry = create_default_plugin_registry()
    network_input, route_input = _healing_inputs(11, 5)
    kwargs = dict(
        network_input=network_input,
        route_input=route_input,
        rule_set=compile_municipal_rule_set(),
        synthetic_obstacles=[(5, 0)],
        max_iterations=3,
    )

    # 默认绑定：冲突驱动自愈收敛
    assert registry.invoke("solver:self_healing", **kwargs).converged is True

    # 激活消融补丁：单轮直连，不再自愈
    registry.activate_profile("profile.ablation.no_self_healing")
    assert (
        registry.get_provider_for_capability("solver:self_healing").plugin_id
        == "plugin.ablation.direct_solver"
    )
    direct = registry.invoke("solver:self_healing", **kwargs)
    assert direct.converged is False
    assert direct.iterations_spent == 1

    # 停用补丁：自动还原自愈能力
    registry.deactivate_profile("profile.ablation.no_self_healing")
    assert (
        registry.get_provider_for_capability("solver:self_healing").plugin_id
        == "plugin.core.municipal_utility"
    )
    assert registry.invoke("solver:self_healing", **kwargs).converged is True

    # 清单导出包含补丁层声明
    inv = registry.export_inventory()
    ablation = next(p for p in inv["profiles"] if p["profile_id"] == "profile.ablation.no_self_healing")
    assert ablation["overrides"][0]["capability"] == "solver:self_healing"
    assert ablation["overrides"][0]["plugin_id"] == "plugin.ablation.direct_solver"


# =========================================================================
# 能力策略门 (CapabilityPolicy) 测试：对标 Codex execpolicy 三态语义
# =========================================================================


def test_policy_default_is_allow_without_rules():
    """无策略表 = 开放内核，能力直接放行（治理显式开启）。"""
    registry = PluginRegistry()
    registry.register(MockHealthyPlugin())
    assert registry.capability_policy_for("test:echo") is None
    assert registry.invoke("test:echo", msg="hi") == "echo: hi"


def test_policy_forbidden_blocks_with_justification():
    registry = PluginRegistry()
    registry.register(MockHealthyPlugin())
    registry.set_capability_policies([
        CapabilityPolicyRule(
            pattern="test:fail_cap",
            decision=CapabilityPolicyDecision.FORBIDDEN,
            justification="实验性能力禁止在生产调用",
        ),
        CapabilityPolicyRule(
            pattern="test:*",
            decision=CapabilityPolicyDecision.FORBIDDEN,
            justification="测试命名空间全部封禁",
        ),
    ])
    # test:echo 只命中 test:* 通配（test:fail_cap 精确规则不匹配），justification 原样进入拒绝信息
    with pytest.raises(PluginPolicyForbiddenError, match="测试命名空间全部封禁"):
        registry.invoke("test:echo", msg="nope")


def test_policy_prompt_requires_explicit_confirm():
    registry = PluginRegistry()
    registry.register(MockHealthyPlugin())
    registry.set_capability_policies([
        CapabilityPolicyRule(
            pattern="test:echo",
            decision=CapabilityPolicyDecision.PROMPT,
            justification="该能力产生外部可见输出",
        ),
    ])
    with pytest.raises(PluginPolicyPromptRequiredError, match="confirm=True"):
        registry.invoke("test:echo", msg="hi")
    assert registry.invoke("test:echo", msg="hi", confirm=True) == "echo: hi"


def test_policy_longest_prefix_wins_over_wildcard():
    registry = PluginRegistry()
    registry.register(MockHealthyPlugin())
    registry.set_capability_policies([
        CapabilityPolicyRule(
            pattern="test:*",
            decision=CapabilityPolicyDecision.FORBIDDEN,
            justification="命名空间默认封禁",
        ),
        CapabilityPolicyRule(
            pattern="test:echo",
            decision=CapabilityPolicyDecision.ALLOW,
            justification="echo 经审计可放行",
        ),
    ])
    # 精确规则特异性更强，覆盖通配封禁
    assert registry.invoke("test:echo", msg="ok") == "echo: ok"
    with pytest.raises(PluginPolicyForbiddenError, match="命名空间默认封禁"):
        registry.invoke("test:add", a=1, b=2)


def test_policy_table_validation_fail_closed():
    registry = PluginRegistry()
    for bad in (
        CapabilityPolicyRule(pattern="", decision=CapabilityPolicyDecision.FORBIDDEN, justification="x"),
        CapabilityPolicyRule(pattern="*", decision=CapabilityPolicyDecision.FORBIDDEN, justification="x"),
        CapabilityPolicyRule(pattern="a:*b", decision=CapabilityPolicyDecision.FORBIDDEN, justification="x"),
        CapabilityPolicyRule(pattern="a:b", decision=CapabilityPolicyDecision.FORBIDDEN, justification="  "),
    ):
        with pytest.raises(ValueError):
            registry.set_capability_policies([bad])
    # 重复 pattern 拒绝整表
    dup = CapabilityPolicyRule(pattern="a:b", decision=CapabilityPolicyDecision.ALLOW, justification="x")
    with pytest.raises(ValueError, match="重复"):
        registry.set_capability_policies([dup, dup])
    # 失败的策略表装载不改变既有策略（空表保持）
    assert registry.capability_policy_for("a:b") is None


def test_policy_exposed_in_inventory():
    registry = create_default_plugin_registry()
    registry.set_capability_policies([
        CapabilityPolicyRule(
            pattern="benchmark:*",
            decision=CapabilityPolicyDecision.PROMPT,
            justification="学术评测能力单次运行耗时较长，需人工确认",
        ),
    ])
    inventory = registry.export_inventory()
    assert inventory["capability_policies"] == [
        {
            "pattern": "benchmark:*",
            "decision": "prompt",
            "justification": "学术评测能力单次运行耗时较长，需人工确认",
        }
    ]
