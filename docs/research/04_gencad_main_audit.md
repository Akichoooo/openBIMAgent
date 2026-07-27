# GenCAD-main 盘点(继承资产审计)

调研日期:2026-07-21 · 对象:`../GenCAD-main` · 方法:只读探索,结论有路径佐证。

## 1. 是什么

- 论文 **GenCAD**(TMLR 2025,arXiv:2409.16294)官方实现的克隆(上游 `github.com/ferdous-alam/GenCAD`),已被用户收入自己的 GenerativeBIM 系列(gitee remote,仅 3 条本地提交)。
- 任务:**图像条件 CAD 生成**(给一张零件图 → 生成可编辑 CAD 建模序列),**不是文本转 CAD,不含任何 LLM 调用**(全库 grep openai/gpt/llm/prompt/mcp 零命中)。
- 链路(`inference_gencad.py:136-300`):PNG → ResNet-18 + CCIP 图像编码 → 扩散先验采样 CAD latent → 自回归 Transformer 解码命令序列 → `vec2CADsolid`(pythonocc)重建 B-Rep → 导出 STEP/STL/多视角 PNG。
- 技术栈:Python 3.10 + PyTorch + pythonocc-core 7.9.0(OpenCASCADE);2GB 预训练权重已 vendor 在 `model/ckpt/`,本机跑通过(`data/test_images/` 有生成结果)。

## 2. 与 openBIMForge 的继承关系

继承的是**接口约定,不是代码本体**:`inference_gencad.py:262-271` 写出的 `cad_vectors/{name}.json` 被 openBIMForge 直接消费——`forge_core/build_agent/unified_runtime.py:191` `_read_cad_vector_hint` 把命令流拆成 `[CAD_PROFILE_n]` 喂 LLM 提示词;`forge_core/layout_agent/layout_adapter.py:128` 从 `cad_vectors/` 拷贝 JSON。即 GenCAD-main 曾当 openBIMForge 的外部布局/体量引擎。

## 3. 对 openBIMAgent 的可复用清单(按价值排序)

| # | 资产 | 路径 | 用途 |
|---|---|---|---|
| 1 | `save_view` + `remove_bg`(约 90 行) | `inference_gencad.py:57-120` | 多视角离屏渲染 + 描边 + 去背景——「渲染→看图」能力的 OCC 版先例,可作 SCAD 环对照截图器 |
| 2 | `cadlib/` 全套(约 1000 行) | `cadlib/macro.py` 等 | DeepCAD 命令规范(Line/Arc/Circle/EOS/SOL/Ext,`MAX_TOTAL_LEN=60` 紧凑格式适合进 LLM 提示词)+ OCC 重建实体完整实现 |
| 3 | STEP 导出 | `utils/export2step.py`(61 行) | BIM/IFC 场景比 STL 有用 |
| 4 | STL 批量转 PNG | `stl2img.py` | 评测/回归对比小工具 |
| 5 | 2GB 权重 + 推理管线 | `model/ckpt/`、`inference_gencad.py` | 可选「图片→CAD 草图 hint」工具节点(openBIMForge 已证明能喂 Agent 做几何约束) |
| 6 | 数据划分 | `data/filtered_data.json`、`image_ids.json` | 评测集骨架(原始 DeepCAD 数据未 vendor) |

**不用看**:`model/` 训练侧、`trainer/`、`config/`、`Dockerfile`(`CMD python demo.py` 指向不存在文件,已坏)。

## 4. 结论与坑

- **定位**:不作为 openBIMAgent 主资产。价值在两点——① OCC 离屏截图/STEP/cadlib 作为**第三工具节点的候选内核**(若未来要「草图→精确 CAD 实体」);② 证明「渲染→看图」在我们谱系里早有先例。
- **坑**:pythonocc 离屏渲染 Windows 兼容性差(需 `xvfb-run` 类环境,用前实测);依赖钉死且含冷门包(`x_clip`/`ema_pytorch`),环境重建成本高;无测试无评测;`vec2CADsolid` 失败仅打印告警跳过,失败率不可见。
- 决策:**M0/M1 不接入**,归档为候选资产;SCAD 环截图仍走 OpenSCAD,不引入 OCC 依赖。
