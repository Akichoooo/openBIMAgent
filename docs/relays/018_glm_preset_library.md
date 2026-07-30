# Relay 018 · GLM 5.2 · M1 收官：预置库 + asset_cache

版本:v1 · 2026-07-29 · 发出:主会话(Opus 5)· 执行:GLM 5.2
前置:Relay 017 完成；测试基线 314 passed；当前 commit `df57a8b`
规格来源:ARCHITECTURE.md §3 环 2、blender_mcp/FORK_NOTES.md 预置库计划

---

## 0. 你的运行环境

- 项目根:`D:\devloop\workSpace\app_codex\GenerativeBIM\openBIMAgent`，git 分支 `main`，当前 commit `df57a8b`
- Python 走 uv：所有命令前缀 `uv run`
- 三条验收命令：`uv run pytest -q` / `uv run ruff check src/ tests/ mcp_servers/vectorworks_mcp/` / `uv run python -m compileall -q src mcp_servers/`
- 当前测试基线：314 passed + 1 skipped
- Blender：`D:\devloop\blender\blender.exe`（5.2.0，headless 可行）

## 红线（违反即任务失败）

1. **禁止改动** `config/`、`domain_packs/`、`docs/`（`relay_workspace/` 内你的报告除外）、`.env`
2. **禁止改动** `mcp_servers/blender_mcp/`（除了新增 `assets/` 目录）
3. **可以改动** `mcp_servers/blender_mcp/assets/`（新建目录，存放预置库）
4. **可以改动** `src/openbimagent/assembly/`（asset_cache 实现）
5. 禁止新增第三方依赖；禁止删改任何现有测试（可新增测试）
6. commit 前必须三条验收命令全绿
7. commit 只提交相关改动
8. 任何 401/额度异常：**立即停止，保留日志，报告部分结果**
9. 全程诚实汇报：跑了什么、什么没跑成，不许编造证据（主会话会逐项自验）

---

## 任务概述

M1 里程碑收官任务（对应 ARCHITECTURE.md §3、blender_mcp/FORK_NOTES.md）：

**核心目标**：
1. 创建 **procedural 材质库**（以 Infinigen 节点组为金标准，LLM 只调参数）
2. 创建 **Damage GeoNodes 修改器资产库**（磨损/破损走预置 GeoNodes）
3. 实现 **asset_cache**（hash + 429 退避，避免重复生成）
4. 更新 **agents/materialist.md**（禁止从零写材质节点树）

**为什么需要这些**：
- **材质库**：提升"材质真实感"维度评分（当前 M0 验收 iter1=7.17<8，材质是瓶颈）
- **GeoNodes 库**：提升"经年磨损破损"维度评分（禁止手写 boolean，用预置修改器）
- **asset_cache**：生产级必备，避免重复生成相同资产（hash 去重 + 429 退避）
- **materialist.md**：写死规则，防止 LLM 从零写材质节点树（耗时且质量差）

**当前状态**（Relay 015 已完成 Blender 精检环）：
- ✅ Blender 精检环六维评分已实现
- ✅ 防放水五件套已实现（3/5，剩余 2 项留 M1.5）
- ⚠️ **缺失**：预置材质库、GeoNodes 库、asset_cache、materialist.md

**你的任务**：
1. 创建 procedural 材质库（5-10 个 Infinigen 风格材质）
2. 创建 Damage GeoNodes 库（3-5 个磨损修改器）
3. 实现 asset_cache（hash 去重 + 429 退避）
4. 更新 materialist.md（禁止规则）
5. 编写完整的单元测试

---

## 任务 A：创建 procedural 材质库（Infinigen 风格）

### A1：创建材质库目录结构

```
mcp_servers/blender_mcp/assets/
├── materials/
│   ├── README.md                # 材质库说明
│   ├── metal_worn.blend         # 磨损金属
│   ├── wood_oak.blend           # 橡木材质
│   ├── concrete_rough.blend     # 粗糙混凝土
│   ├── plastic_matte.blend      # 哑光塑料
│   └── glass_frosted.blend      # 磨砂玻璃
└── geonodes/
    ├── README.md                # GeoNodes 库说明
    ├── damage_edge_wear.blend   # 边缘磨损
    ├── damage_rust_spots.blend  # 锈斑
    └── damage_scratches.blend   # 划痕
```

### A2：编写材质库 README.md

创建 `mcp_servers/blender_mcp/assets/materials/README.md`：

```markdown
# Procedural 材质库（Infinigen 风格）

## 设计原则

1. **纯 procedural**：全部基于 Blender Shader Nodes，零外部贴图
2. **Infinigen 风格**：参数化节点组，LLM 只调参数（颜色/粗糙度/金属度）
3. **PBR 标准**：Principled BSDF 为核心，物理正确

## 使用方式

```python
# 在 Blender Python 中加载预置材质
import bpy

# 1. Append 材质节点组
bpy.ops.wm.append(
    filepath="//assets/materials/metal_worn.blend/NodeTree/MetalWorn",
    directory="..."
)

# 2. 应用到对象
mat = bpy.data.materials.new("MyMetal")
mat.use_nodes = True
mat.node_tree.nodes.clear()
group = mat.node_tree.nodes.new('ShaderNodeGroup')
group.node_tree = bpy.data.node_groups["MetalWorn"]

# 3. 调整参数
group.inputs["Color"].default_value = (0.8, 0.8, 0.8, 1.0)
group.inputs["Roughness"].default_value = 0.7
```

## 材质列表

| 材质 | 参数 | 用途 |
|------|------|------|
| metal_worn | Color, Roughness, Metallic | 金属物体（管道/支架） |
| wood_oak | Color, Roughness | 木质物体（家具/地板） |
| concrete_rough | Color, Roughness, Bump Scale | 混凝土（墙体/地面） |
| plastic_matte | Color, Roughness | 塑料物体（外壳/容器） |
| glass_frosted | IOR, Roughness | 玻璃物体（窗户/瓶子） |
```

### A3：创建示例材质节点组（Python 脚本生成）

创建 `mcp_servers/blender_mcp/assets/materials/generate_materials.py`：

```python
"""生成预置材质库（Infinigen 风格）。"""

import bpy

def create_metal_worn():
    """创建磨损金属材质节点组。"""
    # 创建节点组
    group = bpy.data.node_groups.new("MetalWorn", 'ShaderNodeTree')
    
    # 输入节点
    group_input = group.nodes.new('NodeGroupInput')
    group.inputs.new('NodeSocketColor', 'Color')
    group.inputs.new('NodeSocketFloat', 'Roughness')
    group.inputs.new('NodeSocketFloat', 'Metallic')
    
    # 设置默认值
    group.inputs['Color'].default_value = (0.8, 0.8, 0.8, 1.0)
    group.inputs['Roughness'].default_value = 0.7
    group.inputs['Metallic'].default_value = 1.0
    
    # Principled BSDF
    bsdf = group.nodes.new('ShaderNodeBsdfPrincipled')
    
    # 连接
    group.links.new(group_input.outputs['Color'], bsdf.inputs['Base Color'])
    group.links.new(group_input.outputs['Roughness'], bsdf.inputs['Roughness'])
    group.links.new(group_input.outputs['Metallic'], bsdf.inputs['Metallic'])
    
    # 输出节点
    group_output = group.nodes.new('NodeGroupOutput')
    group.outputs.new('NodeSocketShader', 'Shader')
    group.links.new(bsdf.outputs['BSDF'], group_output.inputs['Shader'])
    
    print("Created: MetalWorn")

def save_materials():
    """保存所有材质到 .blend 文件。"""
    create_metal_worn()
    # TODO: 创建其他 4 个材质
    
    # 保存
    bpy.ops.wm.save_as_mainfile(filepath="metal_worn.blend")
    print("Saved: metal_worn.blend")

if __name__ == "__main__":
    save_materials()
```

**执行**：
```bash
blender --background --python mcp_servers/blender_mcp/assets/materials/generate_materials.py
```

---

## 任务 B：创建 Damage GeoNodes 修改器资产库

### B1：编写 GeoNodes 库 README.md

创建 `mcp_servers/blender_mcp/assets/geonodes/README.md`：

```markdown
# Damage GeoNodes 修改器资产库

## 设计原则

1. **非破坏性修改器**：保留原始几何体，通过 GeoNodes 添加磨损效果
2. **参数化**：LLM 只调参数（强度/随机种子/范围）
3. **禁止手写 boolean**：磨损/破损一律走预置 GeoNodes（写死进 materialist.md）

## 使用方式

```python
# 在 Blender Python 中应用 GeoNodes
import bpy

# 1. Append GeoNodes 节点组
bpy.ops.wm.append(
    filepath="//assets/geonodes/damage_edge_wear.blend/NodeTree/EdgeWear",
    directory="..."
)

# 2. 应用到对象
obj = bpy.context.active_object
mod = obj.modifiers.new("EdgeWear", 'NODES')
mod.node_group = bpy.data.node_groups["EdgeWear"]

# 3. 调整参数
mod["Input_2"] = 0.5  # 强度
mod["Input_3"] = 42   # 随机种子
```

## GeoNodes 列表

| 修改器 | 参数 | 效果 |
|--------|------|------|
| damage_edge_wear | Intensity, Seed | 边缘磨损（倒角 + 噪声） |
| damage_rust_spots | Density, Size, Seed | 锈斑（实例化小凹陷） |
| damage_scratches | Count, Length, Seed | 划痕（程序化线条） |
```

### B2：创建示例 GeoNodes（Python 脚本生成）

创建 `mcp_servers/blender_mcp/assets/geonodes/generate_geonodes.py`：

```python
"""生成预置 GeoNodes 库。"""

import bpy

def create_edge_wear():
    """创建边缘磨损 GeoNodes。"""
    # 创建 GeoNodes 节点组
    group = bpy.data.node_groups.new("EdgeWear", 'GeometryNodeTree')
    
    # 输入节点
    group_input = group.nodes.new('NodeGroupInput')
    group.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket('Intensity', in_out='INPUT', socket_type='NodeSocketFloat')
    group.interface.new_socket('Seed', in_out='INPUT', socket_type='NodeSocketInt')
    
    # 边缘选择节点（Edge Angle）
    # TODO: 完整的 GeoNodes 逻辑
    
    # 输出节点
    group_output = group.nodes.new('NodeGroupOutput')
    group.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    
    print("Created: EdgeWear")

def save_geonodes():
    """保存所有 GeoNodes 到 .blend 文件。"""
    create_edge_wear()
    # TODO: 创建其他 2 个 GeoNodes
    
    bpy.ops.wm.save_as_mainfile(filepath="damage_edge_wear.blend")
    print("Saved: damage_edge_wear.blend")

if __name__ == "__main__":
    save_geonodes()
```

---

## 任务 C：实现 asset_cache（hash 去重 + 429 退避）

### C1：创建 asset_cache.py

创建 `src/openbimagent/assembly/asset_cache.py`：

```python
"""Asset Cache：hash 去重 + 429 退避（生产级必备）。"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any

class AssetCache:
    """资产缓存：基于参数 hash 去重，429 退避。"""
    
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # 429 退避状态
        self.rate_limited = False
        self.retry_after = 0
    
    def compute_hash(self, params: dict[str, Any]) -> str:
        """计算参数 hash（用于缓存键）。"""
        params_str = json.dumps(params, sort_keys=True)
        return hashlib.sha256(params_str.encode()).hexdigest()[:16]
    
    def get(self, params: dict[str, Any]) -> Path | None:
        """查询缓存：返回缓存路径或 None。"""
        cache_key = self.compute_hash(params)
        cache_path = self.cache_dir / f"{cache_key}.blend"
        
        if cache_path.exists():
            return cache_path
        
        return None
    
    def put(self, params: dict[str, Any], asset_path: Path):
        """写入缓存：复制资产到缓存目录。"""
        cache_key = self.compute_hash(params)
        cache_path = self.cache_dir / f"{cache_key}.blend"
        
        # 复制文件
        import shutil
        shutil.copy(asset_path, cache_path)
        
        # 写入元数据
        meta_path = cache_path.with_suffix(".json")
        meta_path.write_text(json.dumps({
            "params": params,
            "cached_at": time.time()
        }), encoding="utf-8")
    
    def check_rate_limit(self):
        """429 退避检查：如果被限速，抛出异常。"""
        if self.rate_limited:
            wait_time = self.retry_after - time.time()
            if wait_time > 0:
                raise RuntimeError(f"429 Too Many Requests，请等待 {wait_time:.1f}s")
            else:
                # 重置限速状态
                self.rate_limited = False
    
    def mark_rate_limited(self, retry_after_seconds: int):
        """标记 429 限速：记录重试时间。"""
        self.rate_limited = True
        self.retry_after = time.time() + retry_after_seconds
```

### C2：集成 asset_cache 到 builder.py

修改 `src/openbimagent/assembly/builder.py`：

```python
from .asset_cache import AssetCache

def make_builder_fn(registry, cache_dir=None):
    """构造 builder_fn，集成 asset_cache。"""
    
    # 初始化缓存
    cache = None
    if cache_dir:
        cache = AssetCache(cache_dir)
    
    def builder_fn(prev_critique, context):
        # 提取资产参数
        asset_params = context.get("asset_params", {})
        
        # 1. 查询缓存
        if cache:
            cached_path = cache.get(asset_params)
            if cached_path:
                return f"# 从缓存加载：{cached_path}"
        
        # 2. 429 退避检查
        if cache:
            cache.check_rate_limit()
        
        # 3. 生成代码（原有逻辑）
        code = _generate_code(prev_critique, context)
        
        # 4. 写入缓存（假设生成成功）
        # TODO: 在 render_loop 成功后调用 cache.put
        
        return code
    
    return builder_fn
```

---

## 任务 D：更新 agents/materialist.md（禁止规则）

### D1：修改 agents/materialist.md

在 `agents/materialist.md` 中添加禁止规则：

```markdown
# materialist

材质艺术家：负责为 3D 对象赋予真实感材质与磨损破损效果。

## 核心约束（严禁违反）

1. **禁止从零写材质节点树**：
   - 一律使用 `mcp_servers/blender_mcp/assets/materials/` 中的预置材质
   - 只调整参数（颜色/粗糙度/金属度），不创建新节点

2. **禁止手写 boolean 破损**：
   - 磨损/破损一律使用 `mcp_servers/blender_mcp/assets/geonodes/` 中的预置 GeoNodes
   - 只调整参数（强度/随机种子），不手写 boolean 操作

3. **预置库优先级**：
   - 金属 → metal_worn
   - 木材 → wood_oak
   - 混凝土 → concrete_rough
   - 塑料 → plastic_matte
   - 玻璃 → glass_frosted

## 示例代码

```python
# ✅ 正确：使用预置材质
bpy.ops.wm.append(filepath="//assets/materials/metal_worn.blend/NodeTree/MetalWorn")
mat.node_tree.nodes.new('ShaderNodeGroup').node_tree = bpy.data.node_groups["MetalWorn"]

# ❌ 错误：从零创建材质节点树
mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
mat.node_tree.nodes.new('ShaderNodeTexNoise')
# ... 繁琐且质量差
```

## 为什么有这些规则

1. **提升质量**：预置材质基于 Infinigen 金标准，质量远超 LLM 现场生成
2. **节省时间**：避免生成 50+ 行节点树代码
3. **提升评分**：材质真实感是六维评分的关键，预置库直接冲 8 分
```

---

## 任务 E：单元测试

### E1：asset_cache 单元测试（tests/test_asset_cache.py，新建）

**6 个测试**：

1. `test_compute_hash`
   - 计算参数 hash
   - 断言：返回 16 字符 hash

2. `test_get_cache_miss`
   - 查询不存在的缓存
   - 断言：返回 None

3. `test_put_and_get_cache_hit`
   - 写入缓存后查询
   - 断言：返回缓存路径

4. `test_check_rate_limit_not_limited`
   - 未被限速时调用 check_rate_limit
   - 断言：不抛出异常

5. `test_mark_rate_limited`
   - 标记 429 限速
   - 调用 check_rate_limit
   - 断言：抛出 RuntimeError，消息包含 "429"

6. `test_rate_limit_expires`
   - 标记限速 1 秒
   - 等待 1.1 秒
   - 调用 check_rate_limit
   - 断言：不抛出异常（限速已过期）

### E2：预置库集成测试（tests/test_preset_library.py，新建）

**4 个测试**：

1. `test_materials_directory_exists`
   - 检查 `mcp_servers/blender_mcp/assets/materials/` 存在
   - 断言：目录存在

2. `test_materials_readme_exists`
   - 检查 README.md 存在
   - 断言：文件存在，内容包含 "Infinigen"

3. `test_geonodes_directory_exists`
   - 检查 `mcp_servers/blender_mcp/assets/geonodes/` 存在
   - 断言：目录存在

4. `test_geonodes_readme_exists`
   - 检查 README.md 存在
   - 断言：文件存在，内容包含 "GeoNodes"

---

## 任务 F：验收与提交

### F1：三条验收命令

```bash
# 1. 全量测试（应 ≥324 passed，新增 10 个测试）
uv run pytest -q

# 2. 代码检查
uv run ruff check src/ tests/ mcp_servers/vectorworks_mcp/

# 3. 编译检查
uv run python -m compileall -q src mcp_servers/
```

### F2：提交策略（分两个 commit）

**Commit 1**（预置库 + asset_cache）：
```bash
git add mcp_servers/blender_mcp/assets/ src/openbimagent/assembly/asset_cache.py agents/materialist.md
git commit -m "M1 收官: 预置库 + asset_cache

- assets/materials: 5 个 Infinigen 风格 procedural 材质（metal/wood/concrete/plastic/glass）
- assets/geonodes: 3 个 Damage GeoNodes 修改器（边缘磨损/锈斑/划痕）
- asset_cache.py: hash 去重 + 429 退避，避免重复生成
- materialist.md: 禁止从零写材质节点树，禁止手写 boolean 破损"
```

**Commit 2**（测试覆盖）：
```bash
git add tests/
git commit -m "M1 测试: asset_cache + 预置库单元测试全覆盖

- tests/test_asset_cache.py: hash/get/put/429 退避 6个测试
- tests/test_preset_library.py: 预置库目录与文档 4个测试"
```

---

## 任务 G：报告

写到 `relay_workspace/m1_preset_library/report.md`，包含：

### G1：实现总结
1. 预置库统计（材质数/GeoNodes 数/文件大小）
2. asset_cache 实现细节（hash 算法/429 退避策略）
3. materialist.md 禁止规则

### G2：测试证据
1. 三条验收命令的**原始输出**
2. 新增测试列表（10 个测试）

### G3：Commit 证据
1. 两个 commit 的 hash
2. 文件变更统计

### G4：M1 收官清单
1. M1 里程碑完成度（核心能力全部就绪）
2. 端到端冒烟准备（下一步工作）
3. 遗留问题（如果有）

### G5：入库检查单
1. 改动文件清单
2. 给主会话的建议（M1 冒烟验证计划）

---

## 回执格式

完成后只回：**「018 完成」+ 报告路径 + 测试统计（新增 X 个测试，Y passed）+ 两个 commit hash**。

细节都在报告里，主会话自己去验。

---

## 关键技术要点（避免踩坑）

### 1. 生成 .blend 文件（Blender Python）

任务书 A3/B2 有完整脚本，使用 Blender headless 模式生成。

### 2. asset_cache hash 计算

任务书 C1 有完整实现，使用 sha256 前 16 位。

### 3. 429 退避策略

任务书 C1 有完整实现，记录 retry_after 时间戳。

### 4. materialist.md 禁止规则

任务书 D1 有完整规则，直接复制。

---

## 最后检查清单

执行前确认：
- [ ] 已读完整个任务书
- [ ] 已理解预置库的设计原则
- [ ] 已理解 asset_cache 的重要性
- [ ] 已准备好写 10 个单元测试
- [ ] 已确认 Blender 路径存在

执行中遵守：
- ✅ 诚实汇报：跑了什么、没跑成什么
- ✅ 代码质量：类型注解、docstring、错误处理
- ✅ 测试隔离：用 `tmp_path`，不污染项目目录
- ❌ 不编造证据：pytest 输出必须真实
- ❌ 不违反红线

祝顺利！这是 M1 最后一个 Relay，完成后即可冲刺端到端验证！🚀
