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
    CapabilityOverride,
    CapabilityPolicyDecision,
    CapabilityPolicyRule,
    MunicipalDirectPlugin,
    MunicipalUtilityPlugin,
    PluginLifecycleState,
    PluginPolicyForbiddenError,
    PluginPolicyPromptRequiredError,
    PluginRegistry,
    RuleCompliancePlugin,
    SpatialGraphPlugin,
    create_default_plugin_registry,
    default_plugin_registry,
)
from openbimagent.core.plugin_loader import (
    MANIFEST_FILENAME,
    PLUGINS_DIR_ENV,
    PluginLoaderError,
    load_external_plugins,
)

__all__ = [
    "AcademicBenchmarkPlugin",
    "BIMPlugin",
    "BIMPluginContext",
    "BIMProfile",
    "CADHostBlenderPlugin",
    "CADHostVectorworksPlugin",
    "CapabilityOverride",
    "CapabilityPolicyDecision",
    "CapabilityPolicyRule",
    "MANIFEST_FILENAME",
    "MunicipalDirectPlugin",
    "MunicipalUtilityPlugin",
    "PLUGINS_DIR_ENV",
    "PluginLifecycleState",
    "PluginLoaderError",
    "PluginPolicyForbiddenError",
    "PluginPolicyPromptRequiredError",
    "PluginRegistry",
    "RuleCompliancePlugin",
    "SpatialGraphPlugin",
    "create_default_plugin_registry",
    "default_plugin_registry",
    "load_external_plugins",
]

