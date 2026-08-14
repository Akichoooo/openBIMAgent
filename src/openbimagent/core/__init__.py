"""Agent 内核:极简循环、SSE 事件类型、权限三态与插件注册中心。

对应文档:ARCHITECTURE.md §0 原则 4(极简内核);COMPONENTS.md §2.1(loop)、§7(权限)。
"""

from openbimagent.core.plugin import (
    AcademicBenchmarkPlugin,
    BIMPlugin,
    BIMPluginContext,
    BIMProfile,
    CADHostBlenderPlugin,
    CADHostVectorworksPlugin,
    MunicipalUtilityPlugin,
    PluginLifecycleState,
    PluginRegistry,
    RuleCompliancePlugin,
    SpatialGraphPlugin,
    create_default_plugin_registry,
    default_plugin_registry,
)

__all__ = [
    "AcademicBenchmarkPlugin",
    "BIMPlugin",
    "BIMPluginContext",
    "BIMProfile",
    "CADHostBlenderPlugin",
    "CADHostVectorworksPlugin",
    "MunicipalUtilityPlugin",
    "PluginLifecycleState",
    "PluginRegistry",
    "RuleCompliancePlugin",
    "SpatialGraphPlugin",
    "create_default_plugin_registry",
    "default_plugin_registry",
]

