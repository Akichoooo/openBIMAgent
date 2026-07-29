# vectorworks-mcp fork 改造说明(M1 第一阶段)

- fork 对象:`openBIMForge` 单体的 `vectorworks_plugin` VW 模块(自研,非第三方开源)
- 规格来源:docs/architecture/ARCHITECTURE.md §5;docs/architecture/COMPONENTS.md §5/§7
- 链路:Agent Core --(MCP stdio)--> `server/server.py` --(文件 IPC:jobs/+results/)--> `runner.py`(VW 宿主侧 Python 脚本)
- 上游基线(原样封存,勿改):`vendor/`(vs_interface.py + UPSTREAM.txt)
- 参照规范:`mcp_servers/blender_mcp/`(成熟的 FastMCP 封装,只读不改)

## 与 blender_mcp 的关键差异

| 维度 | blender_mcp | vectorworks_mcp |
|------|-------------|-----------------|
| 传输层 | socket localhost:9876(常驻 addon) | 文件 IPC(jobs/+results/ 轮询,VW 不支持常驻 socket server) |
| 宿主侧 | `addon.py`(Blender 插件,`blender -b --python`) | `runner.py`(纯 Python 脚本,在 VW 内嵌 Python 跑) |
| 上游来源 | ahujasid/blender-mcp(第三方开源,MIT) | openBIMForge(自研单体,VW 模块提取) |
| 工具数 | 11 个 @mcp.tool(<=12 预算) | 3 个基础工具(M1 第一阶段):ping/describe_capabilities/execute_vs_code |
| 遥测 | stub 硬关(替换上游 phoning-home) | stub 硬关(参照 blender_mcp,本就无遥测) |

## 目录

```
mcp_servers/vectorworks_mcp/
├── README.md               # 架构说明(已存在,勿覆盖):三层结构 + 文件 IPC + vs_index.json 计划
├── FORK_NOTES.md           # 本文件
├── runner.py               # VW 宿主侧 Python runner(等价 blender addon.py,但走文件 IPC)
├── server/                 # MCP stdio server
│   ├── __init__.py
│   ├── server.py           # FastMCP + FileIPCClient + 3 个基础工具
│   └── telemetry.py        # (a) 硬关 stub:TELEMETRY_ENABLED=False,无网络/文件 IO
├── tests/                  # 目录占位(实际单元测试放根 tests/,因 testpaths=["tests"])
└── vendor/                 # 上游原样基线
    ├── vs_interface.py     # 从 openBIMForge 复制(proxy/bridge 模块,非直接 vs.* 封装)
    └── UPSTREAM.txt        # 提取来源记录
```

## 四项改造落点(文件 + 函数)

| # | 改造 | 落点 | 说明 |
|---|------|------|------|
| a | **遥测硬关** | `server/telemetry.py` 整体 stub(`TELEMETRY_ENABLED=False`,无 env 后门);参照 blender_mcp,全无网络/文件 IO | 本就无遥测,stub 保 API 兼容 |
| b | **文件 IPC 替代 socket** | `server/server.py: FileIPCClient.send_command()` 写 jobs/<job_id>.json,轮询 results/<job_id>.json/.failed;`runner.py: poll_jobs_once()` 消费 jobs/ 写 results/ | VW 不支持常驻 socket server,改用文件 IPC(jobs/+results/ 轮询,100ms 间隔) |
| c | **版本探测与兼容性声明** | `server/server.py: describe_capabilities()` 返回 VW 版本;`runner.py: get_vw_version()` 探测;`runner.py: execute_command("describe_capabilities")` 返回 known_issues | 避免"模型不清楚 VW 版本工具出 bug",每次 describe_capabilities 必带版本 + 已知坑清单 |
| d | **jobs/+results/ 轮询机制** | `runner.py: poll_jobs_once()` glob jobs/*.json,写 .running 标记,执行后写 .json 成功/.failed 失败,清理 job 与 .running;`main()` 死循环 + sleep 0.1 | .running 标记供客户端观测进行中状态;job 处理后立即删除防重复消费 |

## 文件 IPC 协议

```
MCP server (FileIPCClient)              VW runner (poll_jobs_once)
─────────────────────────              ──────────────────────────
1. 写 jobs/<uuid>.json
   {"command","params","timestamp"}
                              ───►
2. 轮询 results/<uuid>.json             3. glob jobs/*.json
   或 results/<uuid>.failed                写 results/<uuid>.running
   (100ms 间隔,超时 60s)                   读 job → execute_command()
                                           写 results/<uuid>.json (成功)
                                           或 results/<uuid>.failed (失败)
                                           删 jobs/<uuid>.json + .running
                              ◄───
4. 读到 result → 返回
   读到 .failed → 抛 RuntimeError
   超时 → 抛 TimeoutError
   清理 result/.failed 文件
```

## VW API 已知坑(节选自 README.md AGENTS.md 坑清单)

- **ArcByCenter 在 VW2024 中已损坏**,用 Oval 替代
- **Arc 第六参数为 Sweep 角度**,非终点角度
- vs_index.json 生成(Relay 017):从 vs.py(1.4MB 绑定)离线提取 args/arity/ret/doc,发送前做 arity 校验防引擎崩溃

## vs_interface.py 提取说明

`vendor/vs_interface.py` 是 openBIMForge 的 **proxy/bridge 模块**(非直接 vs.* API 封装):
- 由 VectorWorks .vlb 插件直接 import,注入 openBIMForge 路径
- 委托真实 palette 执行给 `forge_core/design_agent/vs_interface.py`(importlib 加载)
- 暴露 `excute_webpalette_po_coder` 等函数,**无 `execute_vs_code` 函数**
- 硬依赖 vs 模块(VW 内置)与 forge_core 包,无法在测试环境独立 import

因此 `runner.py` 自行实现 `execute_vs_code`(exec with vs in globals),单元测试 mock vs 模块。

## 运行方式

```bash
# MCP server(Agent Core 经 MCP stdio 调用)
uv run python mcp_servers/vectorworks_mcp/server/server.py

# VW 宿主侧 runner(在 VW 内嵌 Python 中跑)
python mcp_servers/vectorworks_mcp/runner.py

# 单元测试(根 tests/,被 uv run pytest 收集)
uv run pytest tests/test_vw_file_ipc.py tests/test_vw_runner.py tests/test_vw_server.py -v
```

## 状态

- [x] 目录结构创建(参照 blender_mcp)
- [x] MCP server 基础实现(3 工具:ping/describe_capabilities/execute_vs_code)
- [x] 文件 IPC 协议(jobs/+results/ 轮询)
- [x] VW Python runner(poll_jobs_once + 命令分发)
- [x] 版本探测(describe_capabilities 返回 VW 版本 + 已知坑)
- [x] 单元测试(15 个:8 IPC + 4 runner + 3 server)
- [ ] vs_index.json 生成(Relay 017)
- [ ] 工具集预设 full/modeling/minimal(Relay 017)
- [ ] handoff/hash/approval 植入 Executor 层(Relay 018)
