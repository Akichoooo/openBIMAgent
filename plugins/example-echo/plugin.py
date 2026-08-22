"""示例外部插件：最小 Echo 能力，演示 manifest 约定与外部加载器。"""

from __future__ import annotations

from openbimagent.core.plugin import BIMPlugin, BIMPluginContext


class ExampleEchoPlugin(BIMPlugin):
    """最小外部插件实现。"""

    plugin_id = "plugin.external.example_echo"
    name = "示例 Echo 外部插件"
    version = "1.0.0"
    description = "演示 openbimagent-plugin.toml manifest 约定与外部发现加载器"
    provides_capabilities = ("example:echo",)

    def setup(self, ctx: BIMPluginContext) -> None:
        super().setup(ctx)
        self.register_handler("example:echo", lambda msg: f"echo: {msg}")


def create_plugin() -> BIMPlugin:
    """外部插件约定的工厂入口。"""
    return ExampleEchoPlugin()
