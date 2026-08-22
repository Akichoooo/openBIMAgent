# openBIMAgent 外部插件目录

本目录是外部插件的发现根。设置环境变量后，微内核会在启动时自动发现、核验并注册：

```bash
export OPENBIMAGENT_PLUGINS_DIR=/path/to/openBIMAgent/plugins
```

## 插件约定

每个外部插件 = 一个子目录，内含：

1. **恰好一个 `.py` 模块**，暴露 `create_plugin() -> BIMPlugin` 工厂；
2. **`openbimagent-plugin.toml` manifest**，声明与运行时完全一致的插件事实：

```toml
[plugin]
plugin_id = "plugin.external.your_plugin"
name = "你的插件名"
version = "1.0.0"
description = "一句话描述"
capabilities = ["your:capability"]
```

## 失败关闭核验

- manifest 缺失、字段不全、`plugin_id` 或 `capabilities` 与运行时不符 → **整批拒绝加载**，注册表零污染；
- 注册时开启依赖校验：插件可声明 `requires_capabilities` 依赖内核能力（如 `solver:self_healing`）。

## 生态索引约定

为你的插件仓库打上 GitHub topic **`openbimagent-plugin`** 即视为加入生态索引（对标 DSH 的 `dsh-plugin` topic 约定）。

## 示例

见 [`example-echo/`](example-echo/)——最小可运行外部插件。

## 安全边界

加载外部 Python 模块即执行其代码。`OPENBIMAGENT_PLUGINS_DIR` 必须只指向受信任目录。
