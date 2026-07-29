"""Builder:batch_ctx + prev_critique → 建模 Python 代码字符串。

对应文档:
- docs/architecture/ARCHITECTURE.md §2 步骤 4-5(批次执行器内 builder)
- docs/architecture/COMPONENTS.md §2.4 orchestrator(agent_fn 注入)、§3 角色 modeler
- src/openbimagent/vision/render_loop.py BuilderFn 形态契约

两条路径:
1. **LLM 路径**:providers.chat(role="modeler") 产出 bpy 代码;FIX 时把
   prev_critique.actionable_feedback 拼进 prompt 让 modeler 重改。LLM 调用失败回退确定性模板,
   不抛(让批次继续跑出可评分的占位,降级链保命)。
2. **确定性模板(无 registry / LLM 失败回退)**:按 batch_ctx 的 IR 资产声明产出占位
   bpy 代码——每资产一个 mesh.primitive_cube_add,命名用 asset.id,落在声明序坐标(0,0,0)。
   同输入必同输出,可离线跑。

返回的 builder_fn 严格符合 vision.render_loop.BuilderFn 形态:
`(prev_critique: CritiqueResult | None, batch_ctx: dict) -> str`。
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from openbimagent.assembly.asset_cache import AssetCache, RateLimitError
from openbimagent.providers.registry import ProviderError
from openbimagent.vision.rubric import CritiqueResult

AGENTS_DIR = Path(__file__).resolve().parents[3] / "agents"
"""角色 prompt 目录(src/openbimagent/assembly/builder.py → 上溯三级为仓库根)。"""

MAX_ATTEMPTS = 2
"""LLM 输出非 bpy 代码时的总尝试次数(首试 + 1 次带错误说明的重试),与 planner 对齐。"""

_PY_FENCE = re.compile(r"```(?:python)?\s*(.*?)```", re.DOTALL)
"""容错提取:markdown fence 包裹的 Python 代码块。"""

# 镜像 mcp_servers/blender_mcp/addon.py 的 AST allowlist(单一事实源在该文件
# validate_code_ast / ALLOWED_IMPORT_ROOTS / BANNED_BUILTIN_NAMES)。此处客户端预校验,
# 把 addon 会拒的代码在送 Blender 前拦下 → 触发重试/回退,不烧 token 不让 render_loop 整批死。
# addon 侧 AST allowlist 仍是硬门禁;此处仅为降级链前置,不放松约束。
_ADDON_ALLOWED_IMPORT_ROOTS = {"bpy", "bmesh", "mathutils", "math"}
_ADDON_BANNED_BUILTIN_NAMES = {
    "open", "exec", "eval", "__import__", "compile", "globals", "locals",
    "vars", "dir", "getattr", "setattr", "delattr", "breakpoint", "exit",
    "quit", "input", "memoryview", "help",
}


class BuilderError(RuntimeError):
    """LLM 路径连续 MAX_ATTEMPTS 次输出非法 bpy 代码;调用方应回退确定性模板。"""


def make_builder_fn(
    *,
    registry: Any = None,
    role: str = "modeler",
    role_brief: str | None = None,
    use_cache: bool = False,
    cache_dir: Path | None = None,
) -> Any:
    """构造 builder_fn(符合 vision.render_loop.BuilderFn 形态)。

    - registry 非空:走 providers.chat(role) LLM 路径;非法输出重试 1 次,仍失败抛 BuilderError
      (调用方应捕获并回退模板,本工厂已内置回退,见 _safe_build)。
    - registry 为空:确定性模板,无 LLM 调用(测试默认路径,可离线)。
    - role_brief 缺省从 agents/<role>.md 正文加载(单一事实源);显式注入跳过文件读(测试友好)。
    - use_cache:是否接入 asset_cache(hash 去重 + 429 退避);默认 False 向后兼容(测试默认不走缓存)。
      开启后:生成前查缓存(命中直接返回),429 限速时回退模板(降级链),生成成功后写缓存。
    - cache_dir:缓存目录;None 时用 asset_cache.DEFAULT_CACHE_DIR(.asset_cache)。
    """
    brief = role_brief if role_brief is not None else _load_role_brief(role)
    cache = AssetCache(cache_dir) if use_cache else None

    def builder(prev_critique: CritiqueResult | None, batch_ctx: dict[str, Any]) -> str:
        # asset_cache 接入(use_cache 时):查缓存命中直接返回;429 限速回退模板。
        # 不破坏降级链:LLM 失败仍回退模板;AST 预校验在 _llm_code 内保留。
        cache_key = _batch_cache_key(batch_ctx, prev_critique) if cache is not None else None
        if cache is not None:
            try:
                cache.check_rate_limit()
            except RateLimitError as exc:
                # 429 退避:被 LLM provider 限速,不走 LLM,直接模板兜底(降级链保命)。
                # 模板代码也写进缓存:下次同参命中,避免反复触发 429。
                rework = prev_critique.actionable_feedback if prev_critique else None
                code = _template_code(batch_ctx, prev_critique)
                code = f"# LLM 路径被 429 限速,回退确定性模板:{exc}\n# rework={rework}\n" + code
                cache.put_text(cache_key, code)
                return code
            cached = cache.get_text(cache_key)
            if cached is not None:
                return "# asset_cache 命中(同参已生成过,跳过 LLM/模板)\n" + cached

        if registry is None:
            code = _template_code(batch_ctx, prev_critique)
        else:
            try:
                code = _llm_code(registry, role, brief, batch_ctx, prev_critique)
            except Exception as exc:
                # 降级链:LLM 任何失败(BuilderError/ProviderError/熔断/缺 key)都回退模板,
                # 不让整批死掉;返工指令拼进代码注释供人审。
                rework = prev_critique.actionable_feedback if prev_critique else None
                code = _template_code(batch_ctx, prev_critique)
                code = f"# LLM 路径失败,回退确定性模板:{exc}\n# rework={rework}\n" + code

        # 写缓存(use_cache 时,生成成功才写;含降级回退的模板代码也写,下次同参命中)
        if cache is not None and cache_key is not None:
            cache.put_text(cache_key, code)
        return code

    return builder


def _batch_cache_key(
    batch_ctx: dict[str, Any], prev_critique: CritiqueResult | None
) -> dict[str, Any]:
    """提取 batch_ctx 稳定字段作缓存键(batch + ir 资产摘要 + 返工反馈)。

    只取 id/category 等稳定字段(剔除 description 等易变文本,避免缓存失效过快);
    prev_critique.actionable_feedback 进 key:FIX 轮与首轮缓存隔离(返工后代码不同)。
    """
    batch = list(batch_ctx.get("batch") or [])
    ir = batch_ctx.get("ir") or {}
    assets = ir.get("assets") or []
    asset_summary = [
        {"id": a.get("id"), "category": a.get("category")}
        for a in assets
        if isinstance(a, dict)
    ]
    feedback = prev_critique.actionable_feedback if prev_critique else None
    return {"batch": batch, "assets": asset_summary, "rework": feedback}


# ---------- 确定性模板 ----------


def _template_code(batch_ctx: dict[str, Any], prev_critique: CritiqueResult | None) -> str:
    """无 LLM / LLM 失败回退的占位 bpy 代码:每资产一个 cube,命名 = asset.id。

    FIX 时把 prev_critique.actionable_feedback 写进代码注释(让 review 能看到 rework 流向)。
    同输入必同输出;不依赖外部状态,可离线跑。
    """
    ir = batch_ctx.get("ir") or {}
    batch = list(batch_ctx.get("batch") or [])
    assets = [a for a in (ir.get("assets") or []) if isinstance(a, dict) and a.get("id") in batch]
    if not assets:
        # batch 引用的资产未在 IR 声明(规划漂移):兜底产一个 batch_label 命名的 cube
        assets = [{"id": batch[0] if batch else "M0Cube", "category": "placeholder"}]
    lines = ["import bpy"]
    if prev_critique is not None and prev_critique.actionable_feedback:
        lines.append(f"# rework(上轮 critic 反馈): {prev_critique.actionable_feedback}")
    for asset in assets:
        aid = str(asset.get("id") or "asset")
        lines.append(
            f"bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 1.0))\n"
            f"obj = bpy.context.active_object\nobj.name = {aid!r}\n"
            f"# asset={aid} category={asset.get('category', 'placeholder')}"
        )
    return "\n".join(lines) + "\n"


# ---------- LLM 路径 ----------


def _llm_code(
    registry: Any,
    role: str,
    brief: str,
    batch_ctx: dict[str, Any],
    prev_critique: CritiqueResult | None,
) -> str:
    """providers.chat(role) 产出 bpy 代码;非法输出重试 1 次,仍失败抛 BuilderError。"""
    messages = _build_modeler_messages(brief, batch_ctx, prev_critique)
    last_error: str | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = registry.chat(role, messages)
        except Exception as exc:
            # registry 自身异常(熔断/降级链全失败/缺 key)直接上抛 → 调用方走模板回退
            raise ProviderError(f"modeler 调用失败:{exc}") from exc
        content = _extract_content(result)
        try:
            code = _extract_code(content)
            _validate_code(code)
        except ValueError as exc:
            last_error = str(exc)
            if attempt >= MAX_ATTEMPTS:
                break
            messages = [
                *messages,
                {"role": "assistant", "content": content},
                {"role": "user", "content": _retry_instruction(last_error)},
            ]
        else:
            return code
    raise BuilderError(f"modeler 连续 {MAX_ATTEMPTS} 次输出非法 bpy 代码:{last_error}")


def _build_modeler_messages(
    brief: str,
    batch_ctx: dict[str, Any],
    prev_critique: CritiqueResult | None,
) -> list[dict[str, Any]]:
    """system(role brief)+ user(批次 + IR 片段 + 输出契约 + 可选 rework)。"""
    batch = list(batch_ctx.get("batch") or [])
    ir = batch_ctx.get("ir") or {}
    assets = [a for a in (ir.get("assets") or []) if isinstance(a, dict) and a.get("id") in batch]
    aid = batch[0] if batch else "M0Scope"
    blocks = [
        f"为批次 {batch!r} 产出 Blender 建模 Python 代码(bpy.ops / bpy.data,可被 addon AST allowlist 通过)。",
        "批次内资产声明(JSON):\n" + json.dumps(assets, ensure_ascii=False, indent=2),
        "输出契约:只输出 Python 代码块(可 ```python fence 包裹),不要任何解释文字;"
        "禁止 os/subprocess/shutil/__import__/写文件。",
        # 风格锚点(M0 冒烟教训:modeler 易退化成灰色盒体,critic style/material 双低分)。
        # 资产 description 已被 planner 注入 style/wear 槽位,以下词表强制模型把风格落成具体几何与材质节点。
        "风格锚点(必须落实,禁止只产灰盒):"
        "①结构拆分——单资产 ≥6 个子对象(主体/边框/按钮/散热口/支架/标牌等),禁止单 cube 糊形;"
        "②PBR 材质——用 Principled BSDF,metallic/roughness 按材质区分(金属 0.7-0.9 / 塑料 0.0+粗糙 0.4),禁止纯默认灰;"
        "③经年磨损——按 description 的磨损等级做边缘磨损与水渍(Noise Texture → Bump/Roughness),禁止一尘不染;"
        "④风格元素——霓虹灯带用自发光材质(Emission,赛博青 #00E5FF / 品红 #FF2D95),传统元素(木纹/瓦/纸窗)用对应 base color;"
        "⑤布光构图——补三点光(主 Key + 补 Fill + 轮廓 Rim)并设相机焦距 35-50mm。",
        # Blender 5.2 引擎枚举(补跑教训:modeler 按 4.x 写 BLENDER_EEVEE_NEXT → 5.2 已合并为 BLENDER_EEVEE,enum 报错回滚)。
        # 宿主实测 5.2.0 LTS,合法引擎仅 ('BLENDER_EEVEE', 'BLENDER_WORKBENCH', 'CYCLES');设 EEVEE 即可,禁写 EEVEE_NEXT。
        "Blender 5.2 兼容(必须遵守):宿主为 5.2.0 LTS,合法渲染引擎仅 BLENDER_EEVEE / BLENDER_WORKBENCH / CYCLES;"
        "scene.render.engine 只能赋这三个值之一(默认 BLENDER_EEVEE);禁止 BLENDER_EEVEE_NEXT(4.x 旧名,5.2 已合并,赋值必抛 TypeError)。",
        # mathutils 取用规范(补跑教训:modeler 写 bpy.mathutils.Vector → AttributeError;mathutils 是顶层模块不是 bpy 子模块)。
        # addon AST allowlist 允许 import mathutils;正确用法是 import 后 mathutils.Vector/Matrix/Euler,禁写 bpy.mathutils。
        "模块取用规范:mathutils 是顶层模块,必须 `import mathutils` 后用 mathutils.Vector/Matrix/Euler;"
        "禁止 bpy.mathutils(非 bpy 子模块,必抛 AttributeError);bmesh 同理需 `import bmesh`。",
        # scene.node_tree(补跑教训:Run B iter3 'Scene' object has no attribute 'node_tree' 直接崩 pipeline)。
        # 5.x 场景级合成器 API 已变;modeler 不需要做后期合成,灯光氛围用灯光对象 + 世界背景实现。
        "禁止 scene.node_tree(Blender 5.x 场景级合成器属性已移除,赋值必抛 AttributeError);"
        "需要背景/氛围时用 bpy.data.worlds 世界背景 + 灯光对象实现,不做后期合成节点。",
        # 范围锁契约:addon 按集合白名单放行(精确对象名匹配会误杀 {asset_id}_base 等子对象);
        # modeler 必须创建以首个 asset.id 命名的集合,并把所有新建对象 link 进该集合,否则被判越界回滚。
        f"范围锁契约(必须遵守):addon 范围锁按集合白名单放行,集合名 = {aid!r};"
        f"所有新建对象必须 link 进集合 {aid!r}。模板:\n"
        f"  coll = bpy.data.collections.get({aid!r}) or bpy.data.collections.new({aid!r})\n"
        f"  if {aid!r} not in [c.name for c in bpy.context.scene.collection.children]:\n"
        f"      bpy.context.scene.collection.children.link(coll)\n"
        f"  # 创建每个对象 obj 后:coll.objects.link(obj)",
    ]
    if prev_critique is not None and prev_critique.actionable_feedback:
        blocks.append(
            "上轮 critic 返工指令(必须落实在代码里):\n" + prev_critique.actionable_feedback
        )
    return [
        {"role": "system", "content": brief},
        {"role": "user", "content": "\n\n".join(blocks)},
    ]


def _retry_instruction(error: str) -> str:
    return f"上一次输出未通过校验:{error}\n请重新输出完整的 Blender 建模 Python 代码块(只输出代码)。"


def _extract_content(result: Any) -> str:
    """chat.completion → 文本(与 planner._message_content 同策略,允许 reasoning 兜底)。"""
    try:
        message = result["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"modeler 响应缺 choices[0].message:{exc}") from exc
    if not isinstance(message, dict):
        raise ValueError(f"modeler 响应 message 形态非法:{type(message).__name__}")
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        text = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if text.strip():
            return text
    reasoning = message.get("reasoning") or message.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning
    raise ValueError("modeler 响应 content 为空(含 reasoning 通道)")


def _extract_code(text: str) -> str:
    """模型输出 → Python 代码:直解 → fence 容错 → 首尾判断;空字符串抛 ValueError。"""
    candidate = text.strip()
    fence = _PY_FENCE.search(candidate)
    if fence:
        candidate = fence.group(1).strip()
    if not candidate:
        raise ValueError("modeler 输出为空")
    # fence 未匹配且文本含中文解释 → 取最长看起来像代码的片段(粗略启发式)
    if not candidate.startswith(("import ", "bpy.", "from ", "#", "import\n")):
        # 尝试找包含 import bpy 的最大片段
        if "import bpy" in candidate:
            start = candidate.find("import bpy")
            candidate = candidate[start:]
            # 截到最后一行看起来是代码的行(粗略)
            lines = candidate.splitlines()
            keep: list[str] = []
            for line in lines:
                if line.strip() and not (line.strip().startswith(("说明", "注意", "解释", "以下"))):
                    keep.append(line)
            if keep:
                candidate = "\n".join(keep)
    return candidate


def _validate_code(code: str) -> None:
    """软校验:镜像 addon AST allowlist(语法 + 导入 + 禁用内置 + dunder)+ 必含 bpy。

    客户端先拦:否则坏代码一路送到 addon 才被 validate_code_ast 拒(如 'use of banned
    builtin name dir'),既烧 token 又让 render_loop 整批死(无客户端重试/回退机会)。
    此处拦下 → _llm_code 重试 1 次 → 仍坏 BuilderError → make_builder_fn 回退确定性模板。
    addon 侧 AST allowlist 仍是硬门禁;此处仅为降级链前置,不放松约束。
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"语法错误(line {exc.lineno}): {exc.msg}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in _ADDON_ALLOWED_IMPORT_ROOTS:
                    raise ValueError(
                        f"禁用导入 '{alias.name}'(addon 仅允许 {sorted(_ADDON_ALLOWED_IMPORT_ROOTS)})"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                raise ValueError("禁用相对导入")
            elif node.module is None:
                raise ValueError("禁用无 module 的 import-from")
            else:
                root = node.module.split(".")[0]
                if root not in _ADDON_ALLOWED_IMPORT_ROOTS:
                    raise ValueError(
                        f"禁用导入 '{node.module}'(addon 仅允许 {sorted(_ADDON_ALLOWED_IMPORT_ROOTS)})"
                    )
        elif isinstance(node, ast.Name):
            if node.id in _ADDON_BANNED_BUILTIN_NAMES:
                raise ValueError(f"禁用内置名 '{node.id}'")
            elif node.id.startswith("__") and node.id.endswith("__"):
                raise ValueError(f"禁用 dunder 名 '{node.id}'")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                raise ValueError(f"禁用 dunder 属性 '.{node.attr}'")
    if "bpy" not in code:
        raise ValueError("代码未含 bpy 调用(疑似非建模代码)")


def _load_role_brief(role: str) -> str:
    """加载 agents/<role>.md 正文(剥 frontmatter)作 system prompt;缺文件给极简兜底。"""
    path = AGENTS_DIR / f"{role}.md"
    if not path.is_file():
        return (
            f"你是 {role} 角色:接收批次资产声明与上轮 critic 返工指令,产出可执行的 Blender 建模"
            " Python 代码(bpy.ops / bpy.data,addon AST allowlist 兼容)。"
        )
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[i + 1 :]).strip()
    return "\n".join(lines).strip()


__all__ = ["BuilderError", "make_builder_fn"]
