# openBIMAgent 手动测试与快速体验指南

本文档提供清晰、一键复制执行的命令与验证流程，帮助你手动测试验证 **openBIMAgent** 的所有核心功能与前沿特性。

---

## 快速导航

- [一、Web 数字化工作台体验 (3D视口 + 规则树 + 空间图谱)](#一web-数字化工作台体验)
- [二、规则自愈式生成求解器测试 (Self-Healing)](#二规则自愈式生成求解器测试)
- [三、三维空间拓扑图谱引擎测试 (SpatialBIMGraph)](#三三维空间拓扑图谱引擎测试)
- [四、BIMBench-Municipal 学术消融实验一键生成](#四bimbench-municipal-学术消融实验一键生成)
- [五、CLI 命令行与会话管理测试](#五cli-命令行与会话管理测试)
- [六、全仓自动化回归与质量门禁](#六全仓自动化回归与质量门禁)

---

## 一、Web 数字化工作台体验

openBIMAgent 提供了基于 FastAPI + SSE + WebGL Three.js 的现代化三栏 BIM 数字化工作台。

### 1. 启动 Web 宿主服务

在项目根目录下打开终端，执行以下命令：

```powershell
uv run uvicorn openbimagent.server.fastapi_app:app --host 127.0.0.1 --port 8000 --reload
```

### 2. 打开浏览器访问

打开浏览器访问：[http://127.0.0.1:8000](http://127.0.0.1:8000)

### 3. 手动验证检查清单

| 区域 | 操作与功能 | 预期效果 |
| :--- | :--- | :--- |
| **顶部状态栏** | 观察连接指示灯 | `Blender MCP: Ready`、`Vectorworks MCP: Ready`、`CodeGraph: 4.9k Nodes`、`SSE Live` 绿灯常亮 |
| **左栏** | 查看领域包与会话树 | 显示市政管网当前规则版本、B1–B10 预设场景及 `/tree` 入口 |
| **中栏** | 观察 Agent 执行流 | 显示 Slot 槽位提取卡片、Manning 确定性水力求解、HITL 审批条及不可变交付物就绪提示 |
| **右栏 Tab 1** | **3D 视口** | WebGL 交互式渲染 3D 管道与检查井模型，下方展示视觉双闭环打分矩阵 (9.8+ 分) |
| **右栏 Tab 2** | **GB 50289 规则树** | 查看 MU-CLEAR-001 (净距)、MU-COVER-001 (覆土)、MU-SLOPE-001 (坡度)、MU-HYDR-001 (流速) 规则 4 态核验结果 |
| **右栏 Tab 3** | **空间图谱 & 自愈** | 查看**规则自愈闭环**状态（显示检测冲突、缓冲区膨胀、自动绕行收敛）及 3D 拓扑有向无环 (DAG) 指标 |
| **右栏 Tab 4** | **交付工件** | 查看生成的 `openbimagent_output.ifc` (IFC4X3)、`openbimagent_ids.xml` (IDS 1.0)、`openbimagent_b1.vwx` 及签名证据包 |
| **右栏 Tab 5** | **Compiled IR** | 查看符合 v1.0 架构的 CompiledUtilityIR 不可变确定性几何 JSON 语法高亮 |

---

## 二、规则自愈式生成求解器测试

测试在遇到地下障碍物或空间冲突时，求解器自动执行**安全缓冲区膨胀 (Buffer Inflation)** 并动态重新规划的能力。

### 1. 运行自愈求解器单元测试

```powershell
uv run pytest tests/test_self_healing_solver.py -v
```

* **预期结果**：`3 passed`（包含：首轮清洁收敛、遇障碍物第 2 轮自愈绕行收敛、完全受阻时优雅失败关闭）。

### 2. 交互式 Python 运行自愈演示

在终端运行以下脚本，实时查看自愈日志与迭代记录：

```powershell
uv run python -c "from openbimagent.utility import GridRouteSolverInput, NetworkGravitySolverInput, compile_municipal_rule_set, solve_self_healing_route; from test_grid_route_solver import route_payload; from test_network_utility_solver import network_payload; n_dict = network_payload(); r_dict = route_payload(width=11, height=5); r_dict['source_ir_sha256'] = n_dict['source_ir_sha256']; [s.update({'ground_elevation_m': 11.0}) for s in r_dict['surface_samples']]; r_dict['start'] = {'node_id': 'source', 'cell': {'x_index': 0, 'y_index': 0}, 'invert_anchor_m': 10.0}; r_dict['end'] = {'node_id': 'junction', 'cell': {'x_index': 10, 'y_index': 0}}; res = solve_self_healing_route(network_input=NetworkGravitySolverInput.model_validate(n_dict), route_input=GridRouteSolverInput.model_validate(r_dict), rule_set=compile_municipal_rule_set(), synthetic_obstacles=[(5, 0)]); print('收敛状态:', res.converged); print('消耗轮次:', res.iterations_spent); print('自愈违规修复项:', len(res.resolved_violations)); [print(l) for l in res.log]"
```

---

## 三、三维空间拓扑图谱引擎测试

测试从 `CompiledUtilityIR v1` 提取 3D 空间图谱、计算空间邻域、管线相交分析与水力 DAG 有向无环性。

### 1. 运行空间图谱单元测试

```powershell
uv run pytest tests/test_spatial_graph.py -v
```

* **预期结果**：`3 passed`（包含：图谱构建与 DAG 拓扑排序、二维/三维半径空间检索、JSON 拓扑序列化导出）。

### 2. 交互式查看空间图谱结构

```powershell
uv run python -c "from tests.test_spatial_graph import _make_sample_ir; from openbimagent.utility import SpatialBIMGraph; g = SpatialBIMGraph.build_from_ir(_make_sample_ir()); print('节点数:', len(g.nodes)); print('管网总长:', g.calculate_total_network_length(), 'm'); is_dag, seq = g.check_hydraulic_dag(); print('DAG 状态:', is_dag, '拓扑序列:', seq)"
```

---

## 四、BIMBench-Municipal 学术消融实验一键生成

自动化运行对比实验（Neuro-Symbolic openBIMAgent vs LLM-Direct Prompting vs Heuristic Baseline），直接输出符合国际顶刊与毕业论文标准的 Markdown / LaTeX 表格。

### 1. 执行学术实验套件

```powershell
uv run pytest tests/test_academic_bench.py -v
```

### 2. 终端打印完整对比指标表

```powershell
uv run python -c "from openbimagent.benchmark.academic_bench import run_academic_benchmark; report = run_academic_benchmark(); print(report.to_markdown_table())"
```

* **输出效果**：
```text
### BIMBench-Municipal 消融实验与方法对比 (Ablation Study)

| 方法范式 (Method Paradigm) | 拓扑有效率 | 规范合规率 (ACC) | 水力达标率 | 平均延迟 (ms) | 工具调用数 | Token 消耗 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **openBIMAgent (Neuro-Symbolic + Solvers)** | 100.0% | 100.0% | 100.0% | 15.0 ms | 2.4 | ~1850 |
| **LLM-Direct Prompting (Baseline GPT-4/Claude)** | 42.5% | 36.0% | 28.0% | 4200.0 ms | 18.5 | ~14200 |
| **Heuristic Linear Solver (Non-Adaptive)** | 80.0% | 55.0% | 60.0% | 120.0 ms | 1.0 | 0 |
```

---

## 五、CLI 命令行与会话管理测试

### 1. 查看命令行帮助

```powershell
uv run openbimagent --help
```

### 2. 查看会话列表

```powershell
uv run openbimagent sessions list
```

---

## 六、全仓自动化回归与质量门禁

确保全仓代码质量 100% 达标、无 lint 错误与类型违规：

```powershell
# 1. 运行全仓 999+ 项测试套件
uv run pytest tests/ -q

# 2. 运行 Ruff 代码规范检查
uv run ruff check src/ tests/

# 3. 运行 Python 字节码预编译检查
uv run python -m compileall src/ tests/
```

* **验收标准**：
  * `pytest`: **999 passed, 4 skipped, 2 warnings**
  * `ruff`: **All checks passed!**
  * `compileall`: **100% 编译成功无语法错误**
