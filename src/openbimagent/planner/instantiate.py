"""Planner 实例化:playbook → Scene Graph IR + PLAN.md + TODO.md。

对应文档:
- docs/architecture/COMPONENTS.md §2.3 planner
- docs/architecture/ARCHITECTURE.md §2 步骤 2、§4 playbook schema

输出三件套:Scene Graph IR(JSON,资产清单 + 空间约束)、PLAN.md、TODO.md。
只出语义不出坐标(C2);批次粒度 = 一次渲染检查单位。
IR 落盘前必须过 schema_gate(schemas/scene_graph_ir.schema.json),漂移即 FIX。

M0 双路径:
- 注入 providers registry → 强模型 JSON 输出(role="planner"),非法 JSON / 不过 schema /
  违反 C2(夹带绝对坐标)一律重试 1 次,仍失败抛 PlanInvalidError;
- 无 registry → 确定性默认模板:按 playbook batches 每批一个占位资产(同输入必同输出,可离线跑)。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from openbimagent.schema_gate import gate as schema_gate

AGENTS_DIR = Path(__file__).resolve().parents[3] / "agents"
"""角色 prompt 目录(src/openbimagent/planner/instantiate.py → 上溯三级为仓库根)。"""

IR_VERSION = "0.1"
"""Scene Graph IR 格式版本(schemas/scene_graph_ir.schema.json 的 version 字段)。"""

IR_FILENAME = "scene_graph_ir.json"
PLAN_FILENAME = "PLAN.md"
TODO_FILENAME = "TODO.md"

MAX_ATTEMPTS = 2
"""LLM 输出非法时的总尝试次数(首试 + 1 次带错误说明的重试),与 vision.critic 对齐。"""

DEFAULT_BATCH = "全部资产"
"""playbook 未声明 asset_batches 时的兜底批次名。"""

DEFAULT_PER_BATCH_STEPS: tuple[str, ...] = ("scad_check", "blender_build", "render_check")
"""批次内默认步骤(与 domain_packs 各 playbook 的 per_batch 约定一致)。"""

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(\n|\Z)", re.DOTALL)
"""playbook.md 的 YAML frontmatter(--- 包围段;与 clarify.slots.load_playbook_slots 同规则)。"""

_FLOW_QMARK_SCALAR = re.compile(r"(\b\w+\s*:\s*)([^\"'\n{}\[\],]*?\?+)(\s*[,}\]])")
"""PyYAML 已知怪癖的修补:flow mapping 内裸标量结尾的 `?`(如 `question: 做什么资产?,`)会被
误判为显式 key token 导致 ParserError;回退时把这类标量包成双引号(值逐字保留)再解析。"""

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
"""容错提取:markdown fence 包裹的 JSON 块(模型未守「只输出 JSON」约定时兜底)。"""

_ABS_COORD = re.compile(r"[\(\[]\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?(?:\s*,\s*-?\d+(?:\.\d+)?)?\s*[\)\]]")
"""C2 哨兵:形如 (x, y) / [x, y, z] 的绝对坐标元组;命中即判 IR 漂移(坐标由 Solver 出,LLM 只出语义)。"""


class PlanInvalidError(RuntimeError):
    """planner 输出经 1 次重试后仍非法(JSON 提取失败 / 不过 schema / 违反 C2);漂移即 FIX 的终点。"""


@dataclass(frozen=True)
class PlanArtifacts:
    """Planner 输出三件套的路径(COMPONENTS §2.3)。"""

    scene_graph_ir: Path  # JSON;schema 见 schemas/scene_graph_ir.schema.json
    plan_md: Path
    todo_md: Path


# ---------- playbook 加载(load_playbook) ----------


def load_playbook(playbook_path: Path) -> dict[str, Any]:
    """解析 playbook.md 的 YAML frontmatter(slots/phases/acceptance/deliverables)+ 正文。

    归一化出 plan 数据(playbook/targets/slots 快照/phases/acceptance/deliverables)并过
    schema_gate(plan.schema.json);漂移抛 SchemaGateError,缺 frontmatter 抛 ValueError。
    返回 dict 同时携带原始 frontmatter/phases(含 batches/per_batch,供 TODO 展开)与正文。
    """
    path = Path(playbook_path)
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    if not match:
        raise ValueError(f"{path} 缺少 YAML frontmatter(--- 包围段)")
    frontmatter = _load_frontmatter(match.group(1), path)
    body = text[match.end() :].strip()

    name = str(frontmatter.get("name") or path.parent.name)
    raw_phases = [dict(p) for p in (frontmatter.get("phases") or []) if isinstance(p, dict)]
    slot_defs = [dict(s) for s in (frontmatter.get("slots") or []) if isinstance(s, dict)]
    batches = _expand_batches(raw_phases)
    playbook: dict[str, Any] = {
        "path": path,
        "name": name,
        "frontmatter": frontmatter,
        "body": body,
        "targets": list(frontmatter.get("targets") or []),
        "slot_defs": slot_defs,
        "phases": raw_phases,
        "batches": batches,
        "acceptance": dict(frontmatter.get("acceptance") or {}),
        "deliverables": [str(d) for d in (frontmatter.get("deliverables") or [])],
    }
    playbook["plan"] = normalize_plan(playbook, slots_filled={})
    schema_gate.gate_or_fix("plan", playbook["plan"])  # ARCH §2 步骤 3:工件漂移即 FIX
    return playbook


def _load_frontmatter(raw: str, path: Path) -> dict[str, Any]:
    """frontmatter YAML → dict;PyYAML 的 flow-`?` 怪癖走引号修补回退,仍失败报清晰错误。"""
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        repaired = _FLOW_QMARK_SCALAR.sub(lambda m: f'{m.group(1)}"{m.group(2)}"{m.group(3)}', raw)
        try:
            data = yaml.safe_load(repaired)
        except yaml.YAMLError as exc:
            raise ValueError(f"{path} 的 frontmatter YAML 解析失败(含 flow-? 修补回退): {exc}") from exc
    frontmatter = data or {}
    if not isinstance(frontmatter, dict):
        raise ValueError(f"{path} 的 frontmatter 须为 YAML mapping,实收 {type(frontmatter).__name__}")
    return frontmatter


def normalize_plan(playbook: dict[str, Any], slots_filled: dict[str, Any]) -> dict[str, Any]:
    """playbook frontmatter → plan.schema.json 形态的结构化数据(PLAN.md/TODO.md 的同源数据)。

    phases 保留执行协议字段(id/agent/tools/output/solver metadata/acceptance/status)，
    batches/per_batch 仍是展开规则不进入 plan；slots 快照 = 默认值 ← slots_filled 覆盖。
    """
    slots: dict[str, Any] = {}
    for s in playbook.get("slot_defs") or []:
        if s.get("id") and s.get("default") is not None:
            slots[str(s["id"])] = _slot_scalar(s["default"])
    for key, value in (slots_filled or {}).items():
        slots[str(key)] = _slot_scalar(value)
    phases: list[dict[str, Any]] = []
    for p in playbook.get("phases") or []:
        phase: dict[str, Any] = {"id": str(p.get("id") or "")}
        for key in (
            "agent",
            "tools",
            "output",
            "solver",
            "solver_version",
            "input_schema",
            "rule_source",
            "rule_set_schema",
            "input",
            "acceptance",
        ):
            if key in p:
                phase[key] = p[key]
        phase["status"] = str(p.get("status") or "pending")
        phases.append(phase)
    plan: dict[str, Any] = {
        "playbook": str(playbook.get("name") or ""),
        "slots": slots,
        "phases": phases,
        "acceptance": dict(playbook.get("acceptance") or {}),
        "deliverables": list(playbook.get("deliverables") or []),
    }
    if playbook.get("targets"):
        plan["targets"] = list(playbook["targets"])
    return plan


def _slot_scalar(value: Any) -> str | int | float | bool:
    """plan.schema.json 的 slots 只允许 string/number/boolean;其余形态(列表等)字符串化。"""
    if isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False)


def _expand_batches(phases: list[dict[str, Any]]) -> list[str]:
    """从 phases 里带 `batches:` 的阶段(asset_batches)按声明序展开批次名。"""
    batches: list[str] = []
    for phase in phases:
        for batch in phase.get("batches") or []:
            batches.append(str(batch))
    return batches


# ---------- 实例化(instantiate) ----------


def instantiate(
    playbook: dict[str, Any],
    slots_filled: dict[str, Any] | None = None,
    out_dir: Path = Path("."),
    *,
    registry: Any = None,
) -> PlanArtifacts:
    """实例化 playbook:填槽 → IR(LLM 或确定性模板)→ 三件套落盘。

    只出语义不出坐标(C2);批次粒度 = 一次渲染检查单位。
    registry 非空走 providers.chat(role="planner") 强模型路径(输出非法重试 1 次,
    仍失败抛 PlanInvalidError);否则走确定性默认模板(每批一个占位资产,可离线)。
    """
    slots_filled = dict(slots_filled or {})
    plan = normalize_plan(playbook, slots_filled)
    schema_gate.gate_or_fix("plan", plan)  # 填槽后复验(归一化后应必过;防 normalize 漂移回归)

    generator = "确定性默认模板(无 registry)"
    if registry is not None:
        try:
            ir, model = _llm_scene_ir(playbook, slots_filled, registry)
            generator = f"LLM(role=planner, model={model})"
        except PlanInvalidError:
            # LLM 已应答但输出非法(非 JSON / 不过 schema / 违 C2 / 批次引用漂移):
            # 这是「漂移即 FIX」的终点,不降级——保持原语义上抛让 pipeline 走 FIX/ESCALATE。
            raise
        except Exception as exc:
            # 降级链:planner LLM infra 失败(ProviderError/503/熔断/缺 key/网络)回退确定性模板,
            # 与 assembly.builder._safe_build 同策略——不让 planner 漂移阻塞整条流水线。
            # 把 clarify 槽位注入占位资产描述:modeler/critic 只从 IR 取语义(playbook
            # 正文未直传 modeler),不注入则两者对着 placeholder 建占位 cube,验收失真。
            ir = _default_scene_ir(playbook)
            _enrich_with_slots(ir, slots_filled)
            generator = f"确定性默认模板(LLM 失败回退:{type(exc).__name__}: {str(exc)[:120]})"
    else:
        ir = _default_scene_ir(playbook)
    _validate_scene_ir(ir)  # 落盘前过门禁(schema + C2 + 批次引用),漂移即 FIX
    schema_gate.gate_or_fix("scene_graph_ir", ir)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ir_path = out / IR_FILENAME
    ir_path.write_text(json.dumps(ir, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan_path = out / PLAN_FILENAME
    plan_path.write_text(_render_plan_md(playbook, plan, ir, generator), encoding="utf-8")
    todo_path = out / TODO_FILENAME
    todo_path.write_text(_render_todo_md(playbook), encoding="utf-8")
    return PlanArtifacts(scene_graph_ir=ir_path, plan_md=plan_path, todo_md=todo_path)


# ---------- Scene Graph IR:确定性默认模板 ----------


def _slug(text: str) -> str:
    """批次名 → 资产 id 片段:空白与斜杠折叠为下划线(CJK 保留),同输入必同输出。"""
    return re.sub(r"[\s/\\]+", "_", text.strip()) or "batch"


def _default_scene_ir(playbook: dict[str, Any]) -> dict[str, Any]:
    """无 registry 时的保底 IR:按 playbook batches 每批一个占位资产(语义描述,无坐标)。"""
    name = playbook.get("name") or "playbook"
    batch_names = list(playbook.get("batches") or []) or [DEFAULT_BATCH]
    assets: list[dict[str, Any]] = []
    batches: list[list[str]] = []
    for i, batch in enumerate(batch_names, start=1):
        asset_id = f"batch_{i:02d}_{_slug(batch)}"
        assets.append(
            {
                "id": asset_id,
                "category": "placeholder",
                "description": f"占位资产:批次「{batch}」(playbook {name};M0 默认模板,待 modeler 精化语义)",
                "count": 1,
                "tags": ["placeholder", "m0_default"],
            }
        )
        batches.append([asset_id])
    return {"version": IR_VERSION, "assets": assets, "spatial_constraints": [], "batches": batches}


def _enrich_with_slots(ir: dict[str, Any], slots_filled: dict[str, Any]) -> None:
    """planner LLM 失败回退模板时,把 clarify 槽位(asset/style/wear_level)注入资产描述。

    modeler 与 critic 只从 IR 取语义(playbook 正文未直传二者),不注入则对着 placeholder
    建占位 cube,验收失真;注入后 modeler 凭槽位建真实资产、critic 据同一语义打分。
    纯语义不含坐标,不触 C2 哨兵;就地改 asset.description/category 字段。
    """
    asset = slots_filled.get("asset")
    if not asset:
        return
    style = slots_filled.get("style") or ""
    wear = slots_filled.get("wear_level")
    parts = [str(asset)]
    if style:
        parts.append(f"style={style}")
    if wear is not None and wear != "":
        parts.append(f"wear_level={wear}")
    desc = f"{parts[0]}({', '.join(parts[1:])})" if len(parts) > 1 else parts[0]
    semantic = f"{desc};planner LLM 不可用,语义来自 clarify 槽位"
    for a in ir.get("assets") or []:
        if isinstance(a, dict):
            a["description"] = semantic
            a["category"] = "semantic_placeholder"


# ---------- Scene Graph IR:LLM 路径 ----------


def _llm_scene_ir(playbook: dict[str, Any], slots_filled: dict[str, Any], registry: Any) -> tuple[dict[str, Any], str]:
    """providers.chat(role="planner") 生成 IR;非法(非 JSON / 不过校验)重试 1 次,仍失败抛 PlanInvalidError。"""
    messages = _build_ir_messages(playbook, slots_filled)
    last_error: ValueError | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result = registry.chat("planner", messages)
        try:
            ir = _extract_json(_message_content(result))
            _normalize_llm_ir(ir, playbook)
            _validate_scene_ir(ir)
        except ValueError as exc:
            last_error = exc
            if attempt >= MAX_ATTEMPTS:
                break
            messages = [
                *messages,
                {"role": "assistant", "content": _safe_content(result)},
                {"role": "user", "content": _retry_instruction(exc)},
            ]
        else:
            return ir, str(result.get("model_resolved") or "unknown")
    raise PlanInvalidError(f"planner 连续 {MAX_ATTEMPTS} 次输出非法 Scene Graph IR,漂移即 FIX 无果: {last_error}")


def _normalize_llm_ir(ir: dict[str, Any], playbook: dict[str, Any]) -> None:
    """就地补全 LLM IR 的可推导缺省:batches 缺省时按资产声明序每资产一批(批次粒度 = 渲染检查单位)。"""
    if "batches" not in ir and isinstance(ir.get("assets"), list):
        ir["batches"] = [[str(a["id"])] for a in ir["assets"] if isinstance(a, dict) and a.get("id")]


def _validate_scene_ir(ir: dict[str, Any]) -> None:
    """IR 合格性总闸:schema 门禁 + C2 坐标哨兵 + 批次引用完整性;任一不过抛 ValueError(触发重试/FIX)。"""
    errors = schema_gate.validate_artifact("scene_graph_ir", ir)
    errors.extend(_c2_violations(ir))
    errors.extend(_batch_ref_errors(ir))
    if errors:
        raise ValueError("Scene Graph IR 未通过校验:\n" + "\n".join(f"  - {e}" for e in errors))


def _c2_violations(ir: Any) -> list[str]:
    """C2 哨兵:字符串字段夹带 (x, y[, z]) 绝对坐标元组即漂移(坐标由 Solver 出)。"""
    hits: list[str] = []
    if not isinstance(ir, dict):
        return hits
    for asset in ir.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        for key in ("id", "category", "description", "material_ref"):
            value = asset.get(key)
            if isinstance(value, str) and _ABS_COORD.search(value):
                hits.append(f"C2 违反:asset {asset.get('id')!r} 的 {key} 夹带绝对坐标 {value!r}(LLM 只出语义)")
    for constraint in ir.get("spatial_constraints") or []:
        if not isinstance(constraint, dict):
            continue
        for key in ("subject", "relation", "object"):
            value = constraint.get(key)
            if isinstance(value, str) and _ABS_COORD.search(value):
                hits.append(f"C2 违反:空间约束 {constraint.get('subject')!r} 的 {key} 夹带绝对坐标 {value!r}")
    return hits


def _batch_ref_errors(ir: Any) -> list[str]:
    """批次引用完整性:batches 引用的资产 id 必须已声明(schema 管不到的跨字段约束)。"""
    if not isinstance(ir, dict):
        return []
    known = {a.get("id") for a in (ir.get("assets") or []) if isinstance(a, dict)}
    errors: list[str] = []
    for i, batch in enumerate(ir.get("batches") or []):
        if not isinstance(batch, list):
            continue
        for asset_id in batch:
            if asset_id not in known:
                errors.append(f"$.batches[{i}] 引用了未声明的资产 id {asset_id!r}")
    return errors


# ---------- LLM prompt 与响应解析 ----------


def _build_ir_messages(playbook: dict[str, Any], slots_filled: dict[str, Any]) -> list[dict[str, Any]]:
    """system(agents/planner.md 正文)+ user(playbook 任务书 + 槽位 + 输出契约)。"""
    blocks = [
        f"把 playbook「{playbook.get('name')}」实例化为 Scene Graph IR(JSON)。",
        f"声明批次(每批 = 一次渲染检查单位,资产必须按批归组): {json.dumps(playbook.get('batches') or [DEFAULT_BATCH], ensure_ascii=False)}",
    ]
    if slots_filled:
        blocks.append("Clarify 槽位填充表:\n" + json.dumps(slots_filled, ensure_ascii=False, indent=2))
    body = str(playbook.get("body") or "").strip()
    if body:
        blocks.append("playbook 任务书正文:\n" + body)
    blocks.append(
        "严格 JSON 输出(不要输出任何其他文字):"
        '{"version": "0.1", "assets": [{"id": "...", "category": "...", "description": "...", '
        '"count": 1, "material_ref": "...", "tags": ["..."]}], '
        '"spatial_constraints": [{"type": "adjacency|alignment|containment|orientation|spacing", '
        '"subject": "...", "relation": "...", "object": "...", "value": 0}], '
        '"batches": [["资产id", "..."]]}。'
        "C2 铁律:只出语义不出坐标——任何字段都禁止出现 (x, y) / [x, y, z] 形态的绝对坐标数值;"
        "batches 引用的 id 必须已在 assets 声明。"
    )
    return [
        {"role": "system", "content": _load_role_brief("planner")},
        {"role": "user", "content": "\n\n".join(blocks)},
    ]


def _load_role_brief(role: str) -> str:
    """加载 agents/<role>.md 正文(剥 frontmatter)作为 system prompt;缺文件报清晰错误。"""
    path = AGENTS_DIR / f"{role}.md"
    if not path.is_file():
        raise FileNotFoundError(f"planner 角色文件不存在: {path}(agents/ 是 system prompt 单一事实源)")
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[i + 1 :]).strip()
    return "\n".join(lines).strip()


def _message_content(result: dict[str, Any]) -> str:
    """chat.completion → 正文文本;content 为空时回退 reasoning 通道(与 vision.critic 同策略)。"""
    try:
        message = result["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"planner 响应缺 choices[0].message: {exc}") from exc
    if not isinstance(message, dict):
        raise ValueError(f"planner 响应 message 形态非法: {type(message).__name__}")
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):  # content-part 形态(部分 OpenAI 兼容端点)
        text = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if text.strip():
            return text
    reasoning = message.get("reasoning") or message.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning
    raise ValueError("planner 响应 content 为空(含 reasoning 通道)")


def _safe_content(result: dict[str, Any]) -> str:
    """尽力取回上次输出原文(回填 assistant 消息供重试对照);取不到给占位。"""
    try:
        return _message_content(result)
    except ValueError:
        return "(上一次输出无法读取)"


def _extract_json(text: str) -> dict[str, Any]:
    """模型输出 → JSON object:直解 → markdown fence 容错 → 首尾花括号切片;失败抛 ValueError。"""
    candidate = text.strip()
    fence = _JSON_FENCE.search(candidate)
    if fence:
        candidate = fence.group(1).strip()
    parsed = _try_loads(candidate)
    if parsed is None and "{" in candidate:
        parsed = _try_loads(candidate[candidate.find("{") : candidate.rfind("}") + 1])
    if not isinstance(parsed, dict):
        raise ValueError("planner 输出无法解析为 JSON object")
    return parsed


def _try_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _retry_instruction(error: ValueError) -> str:
    """重试说明:附校验错误 + 重申输出契约(C2 禁坐标、批次引用须已声明)。"""
    return (
        f"上一次输出未通过校验:{error}\n"
        "请修正后重新输出完整 Scene Graph IR JSON:只出语义不出坐标(C2),"
        "batches 引用的资产 id 必须已在 assets 声明,字段严格遵守 scene_graph_ir.schema。"
    )


# ---------- PLAN.md / TODO.md 渲染 ----------


def _render_plan_md(playbook: dict[str, Any], plan: dict[str, Any], ir: dict[str, Any], generator: str) -> str:
    """PLAN.md:来源、槽位快照、阶段表、批次-资产映射、验收阈值、交付清单(由 plan 同源数据派生)。"""
    name = plan["playbook"]
    lines = [
        f"# PLAN · {name}",
        "",
        f"- 来源 playbook: domain_packs/{name}/playbook.md",
        f"- targets: {', '.join(plan.get('targets') or ['(未声明)'])}",
        f"- IR 生成方式: {generator}",
        "",
        "## 槽位(Clarify 放行快照)",
        "",
    ]
    slots = plan.get("slots") or {}
    lines.extend([f"- {k}: {v}" for k, v in slots.items()] or ["- (无槽位)"])
    lines.extend(["", "## 阶段", "", "| 阶段 | 角色 | 输出 | 状态 |", "|---|---|---|---|"])
    for phase in plan.get("phases") or []:
        lines.append(
            f"| {phase['id']} | {phase.get('agent', '-')} | {phase.get('output', '-')} | {phase.get('status', 'pending')} |"
        )
    lines.extend(["", "## 批次 → 资产(渲染检查单位)", ""])
    asset_desc = {a["id"]: a.get("description", "") for a in ir.get("assets") or []}
    for i, batch in enumerate(ir.get("batches") or [], start=1):
        lines.append(f"{i}. 批次 {i}: " + ", ".join(f"`{a}`" for a in batch))
        for a in batch:
            if asset_desc.get(a):
                lines.append(f"   - {a}: {asset_desc[a]}")
    acceptance = plan.get("acceptance") or {}
    if acceptance:
        lines.extend(["", "## 验收阈值(超限 ESCALATE 不死循环)", ""])
        for loop, cfg in acceptance.items():
            lines.append(f"- {loop}: min_score {cfg.get('min_score')} / max_iters {cfg.get('max_iters')}")
    lines.extend(["", "## 交付清单(C5 强校验)", ""])
    lines.extend(f"- {d}" for d in plan.get("deliverables") or [])
    lines.append("")
    return "\n".join(lines)


def _render_todo_md(playbook: dict[str, Any]) -> str:
    """TODO.md:checkbox 清单,从 phases/batches 展开(asset_batches 按 批次 × per_batch 步骤展开)。"""
    name = playbook.get("name") or "playbook"
    lines = [f"# TODO · {name}", ""]
    for phase in playbook.get("phases") or []:
        phase_id = str(phase.get("id") or "?")
        batch_names = [str(b) for b in (phase.get("batches") or [])]
        if batch_names:
            steps = [str(s) for s in (phase.get("per_batch") or [])] or list(DEFAULT_PER_BATCH_STEPS)
            lines.append(f"- [ ] {phase_id}(共 {len(batch_names)} 批)")
            for i, batch in enumerate(batch_names, start=1):
                lines.append(f"  - [ ] 批次 {i}/{len(batch_names)}:{batch}")
                lines.extend(f"    - [ ] {step}" for step in steps)
        else:
            suffix = f"({phase['agent']})" if phase.get("agent") else ""
            arrow = f" → {phase['output']}" if phase.get("output") else ""
            lines.append(f"- [ ] {phase_id}{suffix}{arrow}")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "PlanArtifacts",
    "PlanInvalidError",
    "instantiate",
    "load_playbook",
    "normalize_plan",
]
