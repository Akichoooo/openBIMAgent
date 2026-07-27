# vectorworks-mcp(自研,M1;从 openBIMForge 单体拆分)

规格来源:docs/architecture/ARCHITECTURE.md §5;docs/architecture/COMPONENTS.md §5/§7。

## 三层结构(vicquick 式)

Trigger → Executor → Work:

- **Trigger**:MCP stdio 接口层,接收 Agent Core 工具调用,落 job 文件。
- **Executor**:调度核心。**handoff/hash/approval 植入本层**——副作用前重验(handoff 摘要 + 参数 hash + 权限审批),治理不降级,原样继承 openBIMForge 机制。
- **Work**:VW 宿主侧 Python runner(`vs.*`);M1 不引 C++ 插件(P1-3 延后),沿用已跑通的 runner。

## 文件 IPC

沿用已跑通的文件 IPC:`jobs/` 放待执行 JSON,`results/` 收执行结果 JSON,宿主 runner 轮询消费。
协议层 mock 宿主进 CI,真机手动验收(ARCH §8)。

## vs_index.json 生成计划

从 `vs.py`(1.4MB 绑定)离线提取生成 `vs_index.json`(args/arity/ret/doc);发送前做 arity 校验防引擎崩溃;
作为渐进披露知识文件,模型按需 `read`,不预注进上下文。

## 工具集预设(VWX_TOOLSET 思路)

- `full` / `modeling` / `minimal` 三档预设,上下文工具数 248 → 40~100。
- 随 `vs_index.json` 一起生成,配置切换(客户端 `toolset` 参数)。

## AGENTS.md 坑清单

VW API 坑写给 agent 读(如 ArcByCenter 损坏用 Oval 替代、Arc 第六参为 Sweep),随本 server 维护。
