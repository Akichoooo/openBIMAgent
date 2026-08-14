"""openBIMAgent 微内核插件系统与能力调度中枢 (Plugin Core & Capability Dispatcher)。

对标 DeepSeek-Harness 的 Cordis 微内核与 UI-Slots 架构：
  - BIMPlugin 插件基础规范与可逆生命周期 (setup / teardown)
  - 原子性注册与回滚保护 (Atomic Registration & Rollback)
  - 运行时能力调度中枢 (Runtime Capability Dispatch: registry.invoke())
  - BIMProfile 声明式领域专家组合与依赖校验
  - 声明式 UI-Slots 前后端插槽映射
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class PluginLifecycleState(StrEnum):
    """插件运行时状态。"""

    UNLOADED = "unloaded"
    LOADED = "loaded"
    ACTIVE = "active"
    DISABLED = "disabled"
    ERROR = "error"


@dataclass
class BIMPluginContext:
    """插件生命周期上下文。"""

    registry: PluginRegistry
    config: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UISlotSpec:
    """UI 插槽规格声明。"""

    slot_key: str
    plugin_id: str
    title: str
    description: str
    target_area: str  # "header" | "sidebar" | "chat" | "workbench" | "viewport"
    icon: str = "box"


class BIMPlugin:
    """openBIMAgent 标准插件基类。
    
    所有领域专家包 (Domain Packs)、求解器 (Solvers)、CAD 宿主驱动 (Hosts)
    及 UI 扩展均继承此类，并提供可执行的能力处理函数。
    """

    plugin_id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = "openBIMAgent Team"
    provides_capabilities: tuple[str, ...] = ()
    requires_capabilities: tuple[str, ...] = ()
    declared_slots: tuple[UISlotSpec, ...] = ()

    def __init__(self) -> None:
        self.state: PluginLifecycleState = PluginLifecycleState.LOADED
        self.error_message: str | None = None
        self._handlers: dict[str, Callable[..., Any]] = {}

    def setup(self, ctx: BIMPluginContext) -> None:
        """插件挂载初始化钩子。"""
        self.state = PluginLifecycleState.ACTIVE

    def teardown(self, ctx: BIMPluginContext) -> None:
        """插件卸载清理钩子（释放 socket、子进程、清理临时资源）。"""
        self.state = PluginLifecycleState.UNLOADED

    def register_handler(self, capability: str, handler: Callable[..., Any]) -> None:
        """注册能力执行处理器。"""
        self._handlers[capability] = handler

    def invoke_capability(self, capability: str, *args: Any, **kwargs: Any) -> Any:
        """执行插件提供的具体能力。"""
        if self.state != PluginLifecycleState.ACTIVE:
            raise RuntimeError(f"插件 {self.plugin_id} 当前非 ACTIVE 状态 ({self.state.value})，无法调度能力 {capability}")

        handler = self._handlers.get(capability)
        if handler is None:
            raise NotImplementedError(f"插件 {self.plugin_id} 声明了能力 {capability}，但未注册具体执行 Handler")

        return handler(*args, **kwargs)

    def get_info(self) -> dict[str, Any]:
        """导出插件元数据信息。"""
        return {
            "plugin_id": self.plugin_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "state": self.state.value,
            "provides_capabilities": list(self.provides_capabilities),
            "requires_capabilities": list(self.requires_capabilities),
            "declared_slots": [
                {
                    "slot_key": s.slot_key,
                    "title": s.title,
                    "description": s.description,
                    "target_area": s.target_area,
                    "icon": s.icon,
                }
                for s in self.declared_slots
            ],
        }


@dataclass(frozen=True)
class BIMProfile:
    """声明式工程专家 Profile（组合多个插件为一个专业领域方案）。"""

    profile_id: str
    name: str
    description: str
    plugin_ids: tuple[str, ...]


class PluginRegistry:
    """插件注册中心与微内核调度器 (Microkernel Core)。"""

    def __init__(self) -> None:
        self._plugins: dict[str, BIMPlugin] = {}
        self._profiles: dict[str, BIMProfile] = {}
        self._capabilities: dict[str, str] = {}  # capability_name -> plugin_id
        self._context = BIMPluginContext(registry=self)

    def register(self, plugin: BIMPlugin, check_dependencies: bool = False) -> None:
        """原子性注册并挂载插件。
        
        保证：setup 成功后才提交能力映射，setup 失败时完全回滚，避免脏状态残留。
        """
        if plugin.plugin_id in self._plugins:
            raise ValueError(f"Plugin ID 已存在: {plugin.plugin_id}")

        if check_dependencies and plugin.requires_capabilities:
            missing = [req for req in plugin.requires_capabilities if req not in self._capabilities]
            if missing:
                raise ValueError(f"插件 {plugin.plugin_id} 缺少前置依赖能力: {missing}")

        # 1. 尝试执行 setup 初始化
        try:
            plugin.setup(self._context)
        except Exception as exc:
            plugin.state = PluginLifecycleState.ERROR
            plugin.error_message = str(exc)
            logger.exception("插件 setup 失败，已执行原子回滚: %s: %s", plugin.plugin_id, exc)
            raise

        # 2. setup 成功后原子提交
        self._plugins[plugin.plugin_id] = plugin
        for cap in plugin.provides_capabilities:
            self._capabilities[cap] = plugin.plugin_id

        logger.info("插件原子加载成功: %s (v%s)", plugin.plugin_id, plugin.version)

    def unregister(self, plugin_id: str) -> None:
        """安全卸载插件并清理能力映射与资源。"""
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            return

        try:
            plugin.teardown(self._context)
        except Exception as exc:
            logger.error("插件 teardown 异常: %s: %s", plugin_id, exc)

        for cap in plugin.provides_capabilities:
            if self._capabilities.get(cap) == plugin_id:
                del self._capabilities[cap]

        del self._plugins[plugin_id]
        logger.info("插件卸载完成: %s", plugin_id)

    def invoke(self, capability: str, *args: Any, **kwargs: Any) -> Any:
        """通过微内核调度并执行某项能力 (Capability Dispatch)。"""
        plugin_id = self._capabilities.get(capability)
        if plugin_id is None:
            raise ValueError(f"未找到提供能力 '{capability}' 的可用活跃插件。当前已提供能力: {list(self._capabilities.keys())}")

        plugin = self._plugins.get(plugin_id)
        if plugin is None or plugin.state != PluginLifecycleState.ACTIVE:
            raise RuntimeError(f"提供能力 '{capability}' 的插件 '{plugin_id}' 不可用 (state={getattr(plugin, 'state', None)})")

        return plugin.invoke_capability(capability, *args, **kwargs)

    def get_plugin(self, plugin_id: str) -> BIMPlugin | None:
        """获取指定插件实例。"""
        return self._plugins.get(plugin_id)

    def list_plugins(self) -> list[BIMPlugin]:
        """获取所有已注册插件。"""
        return list(self._plugins.values())

    def has_capability(self, capability: str) -> bool:
        """检查指定能力是否已由某个插件提供。"""
        return capability in self._capabilities

    def get_provider_for_capability(self, capability: str) -> BIMPlugin | None:
        """获取提供某项能力的插件。"""
        pid = self._capabilities.get(capability)
        return self._plugins.get(pid) if pid else None

    def register_profile(self, profile: BIMProfile) -> None:
        """注册一个工程专家 Profile。"""
        self._profiles[profile.profile_id] = profile

    def get_profile(self, profile_id: str) -> BIMProfile | None:
        """获取 Profile。"""
        return self._profiles.get(profile_id)

    def list_profiles(self) -> list[BIMProfile]:
        """获取所有 Profiles。"""
        return list(self._profiles.values())

    def activate_profile(self, profile_id: str) -> list[str]:
        """激活一个 Profile，严格校验所有插件是否存在与活跃。"""
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"Profile 未找到: {profile_id}")

        missing_plugins: list[str] = []
        for pid in profile.plugin_ids:
            p = self._plugins.get(pid)
            if p is None or p.state != PluginLifecycleState.ACTIVE:
                missing_plugins.append(pid)

        if missing_plugins:
            raise ValueError(f"Profile '{profile_id}' 包含不可用插件: {missing_plugins}")

        return list(profile.plugin_ids)

    def export_inventory(self) -> dict[str, Any]:
        """导出 DSH 兼容的完整插件清单与 UI 插槽配置。"""
        active_plugins = [p for p in self._plugins.values() if p.state == PluginLifecycleState.ACTIVE]
        all_slots: list[dict[str, Any]] = []
        for p in active_plugins:
            for s in p.declared_slots:
                all_slots.append(
                    {
                        "slot_key": s.slot_key,
                        "plugin_id": p.plugin_id,
                        "title": s.title,
                        "description": s.description,
                        "target_area": s.target_area,
                        "icon": s.icon,
                    }
                )

        return {
            "plugin_count": len(self._plugins),
            "active_plugins": [p.get_info() for p in active_plugins],
            "total_capabilities": len(self._capabilities),
            "capabilities_map": dict(self._capabilities),
            "ui_slots": all_slots,
            "profiles": [
                {
                    "profile_id": pf.profile_id,
                    "name": pf.name,
                    "description": pf.description,
                    "plugin_ids": list(pf.plugin_ids),
                }
                for pf in self._profiles.values()
            ],
        }


# =========================================================================
# 内置标准核心插件 (Built-in Standard Plugins with Real Handlers)
# =========================================================================


class MunicipalUtilityPlugin(BIMPlugin):
    """市政管线综合求解器核心插件。"""

    plugin_id = "plugin.core.municipal_utility"
    name = "市政管网确定性求解器"
    version = "1.5.0"
    description = "提供重力流、多节点管网、A* 标高自适应走廊、水力流速与规则自愈求解器"
    provides_capabilities = (
        "solver:straight_gravity",
        "solver:network_gravity",
        "solver:grid_route",
        "solver:hydraulic",
        "solver:self_healing",
    )
    declared_slots = (
        UISlotSpec(
            slot_key="workbench:tab.compiled_ir",
            plugin_id="plugin.core.municipal_utility",
            title="Compiled IR",
            description="CompiledUtilityIR v1.0 不可变几何与拓扑定义",
            target_area="workbench",
            icon="code",
        ),
        UISlotSpec(
            slot_key="chat:card.hydraulic_calc",
            plugin_id="plugin.core.municipal_utility",
            title="水力核算卡片",
            description="Manning 均匀流速与容量裕量核算结果卡片",
            target_area="chat",
            icon="activity",
        ),
    )

    def setup(self, ctx: BIMPluginContext) -> None:
        super().setup(ctx)
        # 延迟动态导入，避免循环依赖，真正将求解函数挂载到能力执行器上
        from openbimagent.utility import (
            solve_grid_route,
            solve_hydraulic_network,
            solve_network_gravity_utility,
            solve_self_healing_route,
            solve_straight_gravity_utility,
        )

        self.register_handler("solver:straight_gravity", solve_straight_gravity_utility)
        self.register_handler("solver:network_gravity", solve_network_gravity_utility)
        self.register_handler("solver:grid_route", solve_grid_route)
        self.register_handler("solver:hydraulic", solve_hydraulic_network)
        self.register_handler("solver:self_healing", solve_self_healing_route)


class RuleCompliancePlugin(BIMPlugin):
    """国家标准自动合规审查 (ACC) 插件。"""

    plugin_id = "plugin.core.rule_compliance"
    name = "GB 50289-2016 规范审查引擎"
    version = "1.1.0"
    description = "提供管线综合间距、覆土深度、最小坡度与防淤流速 4 态判定与签名证据包"
    provides_capabilities = (
        "rules:gb50289",
        "evidence:municipal_bundle",
    )
    declared_slots = (
        UISlotSpec(
            slot_key="workbench:tab.rules_tree",
            plugin_id="plugin.core.rule_compliance",
            title="GB 50289 规则树",
            description="国家标准四态审查判定树与证据溯源",
            target_area="workbench",
            icon="check-circle",
        ),
        UISlotSpec(
            slot_key="workbench:tab.artifacts",
            plugin_id="plugin.core.rule_compliance",
            title="交付工件清单",
            description="IFC4X3 / IDS 1.0 / VWX 不可变清单",
            target_area="workbench",
            icon="file-text",
        ),
    )

    def setup(self, ctx: BIMPluginContext) -> None:
        super().setup(ctx)
        from openbimagent.utility import (
            compile_municipal_rule_evidence_bundle,
            compile_municipal_rule_set,
        )

        self.register_handler("rules:gb50289", compile_municipal_rule_set)
        self.register_handler("evidence:municipal_bundle", compile_municipal_rule_evidence_bundle)


class CADHostBlenderPlugin(BIMPlugin):
    """Blender 5.2 真实三维 CAD 宿主驱动插件。"""

    plugin_id = "plugin.host.blender_mcp"
    name = "Blender 3D CAD 宿主驱动"
    version = "5.2.0"
    description = "基于 Blender MCP 的高保真 3D 几何建模、材质赋予与离线渲染快照"
    provides_capabilities = (
        "cad_host:blender",
        "vision:blender_render",
    )
    declared_slots = (
        UISlotSpec(
            slot_key="header:status.blender_mcp",
            plugin_id="plugin.host.blender_mcp",
            title="Blender MCP",
            description="Blender 5.2.0 LTS 宿主连接状态指示灯",
            target_area="header",
            icon="box",
        ),
        UISlotSpec(
            slot_key="workbench:tab.viewport_3d",
            plugin_id="plugin.host.blender_mcp",
            title="3D 视口",
            description="WebGL Three.js 交互式 3D 管道与视口渲染",
            target_area="workbench",
            icon="eye",
        ),
    )

    def setup(self, ctx: BIMPluginContext) -> None:
        super().setup(ctx)
        from openbimagent.assembly.blender_plan import BlenderBuilder

        self.register_handler("cad_host:blender", lambda ir, **kw: BlenderBuilder().build(ir, **kw))


class CADHostVectorworksPlugin(BIMPlugin):
    """Vectorworks 2024 工程 CAD 宿主驱动插件。"""

    plugin_id = "plugin.host.vectorworks_mcp"
    name = "Vectorworks 2024 CAD 宿主驱动"
    version = "2024.0.0"
    description = "基于 Vectorworks MCP 的工程施工图、2D/3D 混合图元与 VWX 原生工件生成"
    provides_capabilities = (
        "cad_host:vectorworks",
    )
    declared_slots = (
        UISlotSpec(
            slot_key="header:status.vwx_mcp",
            plugin_id="plugin.host.vectorworks_mcp",
            title="Vectorworks MCP",
            description="Vectorworks 2024 工程 CAD 宿主状态指示灯",
            target_area="header",
            icon="layers",
        ),
    )

    def setup(self, ctx: BIMPluginContext) -> None:
        super().setup(ctx)
        from openbimagent.assembly.vectorworks_plan import VectorworksBuilder

        self.register_handler("cad_host:vectorworks", lambda ir, **kw: VectorworksBuilder().build(ir, **kw))


class SpatialGraphPlugin(BIMPlugin):
    """三维空间拓扑图谱引擎插件。"""

    plugin_id = "plugin.engine.spatial_graph"
    name = "3D Spatial Graph 空间图谱引擎"
    version = "1.0.0"
    description = "提供空间邻域检索、三维管线垂直交叉分析与水力 DAG 有向无环性验证"
    provides_capabilities = (
        "graph:spatial_3d",
        "graph:dag_verifier",
    )
    declared_slots = (
        UISlotSpec(
            slot_key="workbench:tab.spatial_graph",
            plugin_id="plugin.engine.spatial_graph",
            title="空间图谱 & 自愈",
            description="3D 拓扑图谱 DAG 分析与规则自愈闭环时间线",
            target_area="workbench",
            icon="share-2",
        ),
    )

    def setup(self, ctx: BIMPluginContext) -> None:
        super().setup(ctx)
        from openbimagent.utility import SpatialBIMGraph

        self.register_handler("graph:spatial_3d", SpatialBIMGraph.build_from_ir)
        self.register_handler("graph:dag_verifier", lambda ir: SpatialBIMGraph.build_from_ir(ir).check_hydraulic_dag())


class AcademicBenchmarkPlugin(BIMPlugin):
    """BIMBench-Municipal 学术实验与消融评测插件。"""

    plugin_id = "plugin.benchmark.academic_suite"
    name = "BIMBench 学术实验套件"
    version = "2026.1.0"
    description = "自动化执行 B1–B10 消融实验与论文标准量化对比指标生成"
    requires_capabilities = (
        "solver:self_healing",
        "rules:gb50289",
    )
    provides_capabilities = (
        "benchmark:academic_ablation",
    )
    declared_slots = ()

    def setup(self, ctx: BIMPluginContext) -> None:
        super().setup(ctx)
        from openbimagent.benchmark.academic_bench import run_academic_benchmark

        self.register_handler("benchmark:academic_ablation", run_academic_benchmark)


def create_default_plugin_registry() -> PluginRegistry:
    """初始化装载所有核心系统插件与标准 Profiles（开启严格依赖校验）。"""
    registry = PluginRegistry()

    # 1. 注册核心系统插件 (严格按依赖拓扑顺序注册并开启 check_dependencies)
    registry.register(MunicipalUtilityPlugin(), check_dependencies=True)
    registry.register(RuleCompliancePlugin(), check_dependencies=True)
    registry.register(CADHostBlenderPlugin(), check_dependencies=True)
    registry.register(CADHostVectorworksPlugin(), check_dependencies=True)
    registry.register(SpatialGraphPlugin(), check_dependencies=True)
    registry.register(AcademicBenchmarkPlugin(), check_dependencies=True)

    # 2. 注册预设专家 Profile
    registry.register_profile(
        BIMProfile(
            profile_id="profile.municipal.complete",
            name="市政综合管网全流程专家",
            description="包含四大确定性求解器、GB 50289 规则库、空间图谱与 Blender/VWX 双宿主驱动",
            plugin_ids=(
                "plugin.core.municipal_utility",
                "plugin.core.rule_compliance",
                "plugin.host.blender_mcp",
                "plugin.host.vectorworks_mcp",
                "plugin.engine.spatial_graph",
                "plugin.benchmark.academic_suite",
            ),
        )
    )

    registry.register_profile(
        BIMProfile(
            profile_id="profile.academic.evaluation",
            name="学术评测与消融实验方案",
            description="专注 Benchmark 场景复现、规则自愈验证与论文图表数据输出",
            plugin_ids=(
                "plugin.core.municipal_utility",
                "plugin.core.rule_compliance",
                "plugin.engine.spatial_graph",
                "plugin.benchmark.academic_suite",
            ),
        )
    )

    return registry


# 全局默认单例
default_plugin_registry = create_default_plugin_registry()
