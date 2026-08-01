# blender-mcp fork 改造说明(M0)

- fork 对象:`ahujasid/blender-mcp` @ `da4e16d2069ce5154eaa2535bf995e843caf5c73`(2026-07-21 main HEAD,pyproject v1.6.0;仓库无 git tag,`git describe` 不可用)
- 规格来源:docs/architecture/ARCHITECTURE.md §5;docs/architecture/COMPONENTS.md §5/§7
- 链路:Agent Core --(MCP stdio)--> `server/server.py` --(socket localhost:9876)--> `addon.py`(Blender 宿主 / headless)
- 上游基线(原样封存,勿改):`vendor/`(addon.py / server.py / telemetry.py / telemetry_decorator.py + UPSTREAM.txt + UPSTREAM_LICENSE)
- 宿主实测:**Blender 5.2.0 LTS**(`D:\devloop\blender\blender.exe`,内嵌 Python 3.13)

## 目录

```
mcp_servers/blender_mcp/
├── FORK_NOTES.md            # 本文件
├── addon.py                 # 改造版 Blender 插件(在 Blender 内运行;blender -b --python addon.py 直接起服务)
├── server/                  # 改造版 MCP stdio server(uv run,需 mcp/fastmcp,项目已带)
│   ├── server.py            #   11 个工具(<=12),健康检查,首包重试
│   ├── telemetry.py         #   (a) 硬关 stub:TELEMETRY_ENABLED=False,全无网络/文件 IO
│   └── telemetry_decorator.py # (a) 恒等装饰器(仅保 API 兼容,server.py 已不引用)
├── vendor/                  # 上游原样基线
└── tests/
    ├── run_fork_tests.py    # 验收编排器:起 headless Blender → 跑用例 → 写报告(挂起重试 ≤2)
    ├── socket_test_client.py# 纯 socket 测试客户端,T1–T10 用例
    ├── test_telemetry_off.py# (a) 纯 Python 校验(无需 Blender)
    └── smoke_server_import.py # (d) MCP server 导入冒烟 + 工具清单校验(无需 Blender)
```

## 八项改造落点(文件 + 函数)

| # | 改造 | 落点 | 证明 |
|---|------|------|------|
| a | **遥测硬关** | `server/telemetry.py` 整体替换为 stub(`TELEMETRY_ENABLED = False`,无 env 后门);`server/server.py` 删除全部 telemetry import/调用与 `user_prompt` 参数;`addon.py: TELEMETRY_ENABLED / get_telemetry_consent()` 硬编码 `consent=False`;addon 删掉 telemetry 偏好项 | `tests/test_telemetry_off.py` 18 项校验;socket T3 |
| b | **headless 放开** | `addon.py: BlenderMCPServer.start()` 删除 `bpy.app.background` 阻断;`_handle_client()` background 分支改走 `_bg_queue`;新增 `run_headless_forever()` 主线程泵(`__main__` 在 background 下进入,否则脚本结束 Blender 即退出) | socket 全套用例均在 `blender -b` 下跑通 |
| c | **快照 + AST allowlist** | `addon.py: validate_code_ast()`(import 白名单 bpy/bmesh/mathutils/math;禁 open/exec/eval/__import__/compile/getattr 等;禁 dunder 名/属性);`execute_code()` 执行前 `_save_snapshot()`(`bpy.ops.wm.save_as_mainfile(copy=True)` → `OPENBIMAGENT_SNAPSHOT_DIR` 或 temp 默认,留 12 份轮转 + `snapshot_events.jsonl`);执行异常自动回滚 | socket T4(快照落盘)/T5(11 条敌意代码全拦截,白名单导入放行) |
| d | **工具精简 ≤12 + 3 新工具** | `server/server.py` 仅 11 个 @mcp.tool(砍 Polyhaven×4/Sketchfab×4/Hyper3D×5/Hunyuan×4/set_texture);`addon.py` 同步删除全部外部资产集成代码与 `requests` 依赖(上游 addon 在 factory Blender 上因缺 requests 根本 import 不了);新增 `batch_render()`/`camera_turntable()`/`camera_path_render()`(addon + server 双侧) | `tests/smoke_server_import.py`(11 工具,无被砍工具);socket T8/T9/T10 |
| e | **连接健康检查** | addon:`ping()` handler;server:`BlenderConnection.connect()` 内启动探针(TCP 后必须 ping 通才算连上,5 次);`send_command()` 首包重试(stale socket 重连再试,`FIRST_PACKET_RETRIES=2`);`COMMAND_TIMEOUT` 环境可调(`OPENBIMAGENT_BLENDER_TIMEOUT`,默认 180s 超时切片);`get_blender_connection()` 保活改用 ping(上游用的 get_polyhaven_status 已被砍) | socket T1;编排器端口等待 + 首发 ping 探针 |
| f | **截图非黑断言** | `addon.py: get_viewport_screenshot()`:GUI 走 GPUOffScreen(加 5.2 `gpu.init()` guard)且测亮度,黑图自动转 `_render_fallback_capture()`;**background 无 View3D region,直接走 `bpy.ops.render.render(write_still=True)` 兜底**;`image_file_brightness()` 平均亮度 < 0.01 仍黑则返回 error;server 侧再断言一次 | socket T6(method=render_fallback,亮度>阈值) |
| g | **范围锁** | `addon.py: set_editable_scope()`(对象名/集合白名单,存 server 实例,open_mainfile 后仍存活);`execute_code()` 执行前 `_fingerprint_out_of_scope()`(变换/顶点 md5/修改器/材质/可见性),执行后 `_verify_scope()` 查集合外新增/修改/删除,越界 → `bpy.ops.wm.open_mainfile(快照)` 回滚 + 报错;`__OBMCP_` 前缀临时对象豁免 | socket T7(改/建/删白名单外对象全拦截并回滚,白名单内修改保留) |
| h | **describe_capabilities** | `addon.py: describe_capabilities()`(fork/上游版本、Blender/Python 版本、合法引擎枚举、工具清单、沙箱/范围锁/截图/快照限制、KNOWN_ISSUES 含 5.2 坑);`server/server.py` 同名工具补 mcp_server 段(超时/重试参数) | socket T2 |

## Blender 5.2 兼容说明(对 spike 结论的逐条落地)

1. **引擎枚举探测**:`addon.py: probe_render_engines()` 用**赋值试探**(逐个候选引擎赋给 `scene.render.engine`,TypeError 即非法,与 spike 从报错信息取证的方式一致)——注意 5.2 background 下 `bl_rna ... enum_items` 对动态枚举会**少报**(只列出 `BLENDER_EEVEE`,漏掉 addon 注册的 WORKBENCH/CYCLES),赋值试探才是全量;`pick_render_engine()` 按 `("BLENDER_EEVEE","BLENDER_EEVEE_NEXT","BLENDER_WORKBENCH","CYCLES")` 顺序挑第一个合法的——5.2 上是 `BLENDER_EEVEE`,4.2–4.4 上是 `BLENDER_EEVEE_NEXT`,不写死。
2. **gpu.init()**:`gpu_init_guard()` 用 `hasattr(gpu,'init')` 分支,5.x background 下先 init 再 GPUOffScreen;4.x 无此 API 直接跳过。
3. **headless 出图正道**:background 下不做 viewport 尝试,直接 `bpy.ops.render.render(write_still=True)`(spike 实测 18.7s/512px 非黑,亮度 0.282,断言阈值 0.01 留足余量)。
4. **Python 3.13**:fork 不再 vendor 任何二进制依赖;顺手砍掉的 `requests` 本来就不随 Blender 内嵌 Python 发布(上游 addon 在 factory 安装上 import 即失败,fork 修复)。
5. background 下 `bpy.app.timers` 不触发(无事件循环)→ 改造 b 的队列泵是 headless 能跑的前提。

## 运行方式

```bash
# 起 headless Blender 服务(测试用 9887 端口,正式默认 9876)
"D:/devloop/blender/blender.exe" --background --factory-startup --python mcp_servers/blender_mcp/addon.py
# 验收（自动起 Blender、跑 T1–T10；历史临时报告已清理，结论见本文件“状态”）
uv run python mcp_servers/blender_mcp/tests/run_fork_tests.py
# 无需 Blender 的两项快检
uv run python mcp_servers/blender_mcp/tests/test_telemetry_off.py
uv run python mcp_servers/blender_mcp/tests/smoke_server_import.py
```

## 已知限制(M0 接受,M1 处理)

- AST 白名单是**静态**检查:`bpy.ops.wm.*`(save/open_mainfile 等文件操作)按设计保留(快照/回滚自身需要),`bpy.data` 深层写路径不设防;真正的越界破坏由范围锁兜底。
- 范围锁指纹对 >20 万顶点的 mesh 只哈希前 1000 顶点;GeometryNodes 只改求值结果不改基础网格时不触发指纹变化。
- `open_mainfile` 回滚会丢弃未保存的 non-scene 状态(如未落盘的图像);快照轮转只留 12 份。
- 首个 EEVEE 渲染要编译着色器(本机 ~19s),客户端超时必须 ≥180s。
- 范围锁默认**解锁**(显式调用 `set_editable_scope` 才生效);编排器接入时应按批次上锁。
- 上游 `telemetry.py` 自身有 bug(懒导入不存在的 `.config` 模块,实例化即 ModuleNotFoundError)——fork 用 stub 整体取代,不受影响。
- MCP server 侧未做 AST 复检(单一事实源在 addon,agent 无法绕过 socket 直接 exec)。

## 预置库计划(M1,未动)

- **procedural 材质库**:以 Infinigen 节点组为金标准,挂载为预置资产;LLM 只调参数。
- **Damage GeoNodes 修改器资产库**:磨损/破损一律走预置 GeoNodes;禁止从零写材质节点树,严禁手写 boolean 破损(同步写死进 agents/materialist.md)。

## 状态

- [x] fork 落仓 + vendor 基线(M0)
- [x] 八项改造 a–h(M0,逐项落点见上表)
- [x] 脚本化验收完成：纯 Python 校验 18+9 项全过；一次性临时报告已在结果固化后清理
- [ ] 预置材质库 / Damage GeoNodes 挂载(M1)
- [ ] MCP stdio 端对端(经真实 MCP client 握手)联调(M1,随 Agent Core 接入)
