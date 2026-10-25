"""真实 Agent 运行端点：新建任务 → 后台真跑 pipeline → 会话事件可读。

设计约束：
- 有界多并发（默认 2，``OPENBIMAGENT_MAX_CONCURRENT_RUNS`` 可调；409 拒绝超额），
  每个运行独占 ``out/runs/<session_id>/`` 产物目录，互不覆盖；状态经 ``GET /api/v1/runs/active`` 轮询。
- 会话落 ``sessions_dir``（默认 ``out/sessions``，``OPENBIMAGENT_SESSIONS_DIR`` 可覆盖，测试隔离）。
- 离线安全：无 providers registry / 无 CAD 宿主时 pipeline 走确定性模板 + MockCritic（CLAUDE.md 约定路径）。
- Web 运行审批门：触门（execute_code 前 / deliver 前）挂起，待 ``/api/v1/approvals`` 人工决策；超时失败关闭。
- 归档反哺（缺陷一修复）：新任务启动前检索本包归档 Top-3 相似交付，作为**会话首条用户消息**注入
  （In-Context Retrieval 注入会话上下文；读取会话历史的角色可消费，确定性模板路径不消费——如实标注）；
  机制=事件溯源 + In-Context Retrieval，非权重更新（论文表述边界，见 docs/architecture/LIMITATIONS.md）。
"""

from __future__ import annotations

import json
import os
import re
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from openbimagent.server.sse_endpoint import M2_SSE_MAX_ACTIVE_STREAMS, M2SseStreamBudget, follow_session_jsonl

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SESSIONS_DIR = _REPO_ROOT / "out" / "sessions"
_PLAYBOOKS = {
    "municipal_utility": _REPO_ROOT / "domain_packs" / "municipal_utility" / "playbook.md",
    "single_asset_hero": _REPO_ROOT / "domain_packs" / "single_asset_hero" / "playbook.md",
}

_run_lock = threading.Lock()
_runs: dict[str, dict[str, Any]] = {}
_session_sse_budget = M2SseStreamBudget()


def _max_concurrent() -> int:
    try:
        return max(1, int(os.environ.get("OPENBIMAGENT_MAX_CONCURRENT_RUNS", "2")))
    except ValueError:
        return 2


def _run_timeout_s() -> int:
    """单 run 全局超时兜底(容错):超时自动标记 failed,不留僵尸 active 阻塞并发额度。"""
    return max(60, int(os.environ.get("OPENBIMAGENT_RUN_TIMEOUT_S", "900")))


def _sessions_dir() -> Path:
    override = os.environ.get("OPENBIMAGENT_SESSIONS_DIR")
    return Path(override) if override else _DEFAULT_SESSIONS_DIR


def _run_llm_enabled() -> bool:
    """Web run 是否接工作台 LLM；测试沙箱置 OPENBIMAGENT_RUN_LLM=0 强制模板路径（不真调 API）。"""
    return os.environ.get("OPENBIMAGENT_RUN_LLM", "1").strip().lower() not in {"0", "false", "off"}


def _bigrams(text: str) -> set[str]:
    """CJK 友好的二元 token 集（英文按词、其余按字 bigram），用于归档相似度检索。"""
    tokens: set[str] = set()
    word = ""
    for ch in text.lower():
        if ch.isascii() and ch.isalnum():
            word += ch
            continue
        if word:
            tokens.add(word)
            word = ""
        if not ch.isspace():
            tokens.add(ch)
    if word:
        tokens.add(word)
    return tokens


_DOMAIN_KEYWORDS: dict[str, str] = {
    "污水": "domain:sewage",
    "雨水": "domain:storm",
    "给水": "domain:water_supply",
    "再生水": "domain:reclaimed",
    "电力": "domain:power",
    "通信": "domain:telecom",
    "燃气": "domain:gas",
}
"""brief 与归档 IR 共用的领域关键词特征表(结构化对齐,E5)。"""

_DN_PATTERN = re.compile(r"dn\s*(\d{3,4})")


def _text_features(text: str) -> set[str]:
    """从文本提取结构化特征:领域标签 + DN 管径值(确定性,无向量)。"""
    low = text.lower()
    features = {tag for keyword, tag in _DOMAIN_KEYWORDS.items() if keyword in low}
    features.update(f"dn:{m.group(1)}" for m in _DN_PATTERN.finditer(low))
    return features


def _ir_features(ir_path: Path) -> set[str]:
    """从归档 compiled_utility_ir.json 提取特征:领域/DN(全文扫描,形状无关)+ rule_id。

    IR 归档体量小(数 KB),全量确定性扫描即可;读取失败返回空集(检索降级为纯词法)。
    """
    try:
        data = json.loads(ir_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    features = _text_features(json.dumps(data, ensure_ascii=False))
    for evidence in data.get("evidence") or []:
        rule_id = evidence.get("rule_id")
        if rule_id:
            features.add(f"rule:{rule_id}")
    return features


def _retrieve_exemplars(brief: str, pack: Path, *, top_k: int = 3) -> list[dict[str, Any]]:
    """缺陷一修复（归档反哺）：从本包素材归档检索 Top-K 相似交付作为 in-context 范例。

    E5 结构化对齐升级(论文 01 方法论:需求条目-构件对齐检索):
    评分 = 0.6×brief 词法重合(bigram Jaccard) + 0.4×结构化特征重合(领域标签/DN 管径/
    rule_id,从归档 IR 提取) + 时效小幅加权;词法零重合仍不入选(防"无相似也硬凑");
    无任何向量库/权重更新——机制边界=In-Context Retrieval(论文严禁表述为自进化/RL)。
    命中条目附 match 元数据(lexical/structured/matched features)供上下文注入展示。
    """
    archive_root = _archive_root(pack)
    index_path = archive_root / "index.json"
    if not index_path.is_file():
        return []
    try:
        entries = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    query = _bigrams(brief)
    if not query:
        return []
    query_features = _text_features(brief)
    scored: list[tuple[float, dict[str, Any]]] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        record = entry.get("experience_record")
        if isinstance(record, dict):
            stats = record.get("utility_stats") or {}
            # Only admitted, non-deactivated experience may be retrieved. Legacy
            # entries written before experience_record existed stay eligible so
            # existing archives keep working.
            if record.get("admission_verified") is not True or stats.get("active") is not True:
                continue
        cand = _bigrams(str(entry.get("brief", "")))
        if not cand or not (query & cand):
            continue  # 零重合不入选（防"无相似也硬凑"）
        lexical = len(query & cand) / len(query | cand)
        ir_features = _ir_features(archive_root / str(entry.get("session_id", "")) / "compiled_utility_ir.json")
        if query_features and ir_features:
            structured = len(query_features & ir_features) / len(query_features | ir_features)
        else:
            structured = 0.0
        recency = (i + 1) / len(entries) * 0.1  # 近者小幅加权
        score = 0.6 * lexical + 0.4 * structured + recency
        enriched = dict(entry)
        enriched["match"] = {
            "lexical": round(lexical, 3),
            "structured": round(structured, 3),
            "features": sorted(query_features & ir_features)[:8],
        }
        scored.append((score, enriched))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in scored[:top_k]]


def _execute_run(
    brief: str,
    playbook: Path,
    session_id: str,
    enriched_context: str | None = None,
    workspace_id: str | None = None,
    mode: str = "agent",
) -> None:
    """后台线程：真跑 assembly pipeline（离线走确定性模板 + MockCritic）。"""
    from openbimagent.assembly.pipeline import run_pipeline

    sessions_dir = _sessions_dir()
    sessions_dir.mkdir(parents=True, exist_ok=True)
    out_dir = _REPO_ROOT / "out" / "runs" / session_id  # 缺陷四修复：每运行独占产物目录，互不覆盖
    try:
        # 预建会话并写入标题（index.json 侧边栏数据源），pipeline 复用同一 session 文件
        from openbimagent.session.store import SessionStore

        store = SessionStore(sessions_dir / f"{session_id}.jsonl", title=brief[:60] or session_id, playbook=playbook.parent.name)
        # 工作区归属盖章：_sync_index 只更新已知键，workspace 字段在后续同步中保留
        from openbimagent.server.workspaces import current_workspace_id, stamp_session_workspace

        stamp_session_workspace(session_id, workspace_id if workspace_id is not None else current_workspace_id())
        # 缺陷一修复：检索范例作为会话首条用户消息注入（In-Context Retrieval 注入会话上下文；
        # 读取会话历史的角色（clarify 续跑/后续 researcher）可消费；确定性模板路径不消费——如实记录）
        if enriched_context:
            from openbimagent.session.schema import EventType

            store.append_new(EventType.MESSAGE, {"role": "user", "content": enriched_context})
        # Web 审批门：触门即挂起，待前端 /api/v1/approvals 人工决策（撤掉 yes=True 自动放行；自主模式直接放行）
        from openbimagent.server.approvals import make_web_approval_fn

        # 市政主线补 utility_solver_input（pack 内默认输入；否则 domain_gate 因证据缺失 UNKNOWN 阻断）
        solver_input: Path | None = None
        default_input = playbook.parent / "solver_input.default.json"
        if default_input.is_file():
            solver_input = default_input

        # G-web：接工作台 LLM（设置页配置）——有 key 走真 planner 链，缺 key 降级模板并如实发事件
        from openbimagent.server.workbench_llm import workbench_registry

        registry = workbench_registry() if _run_llm_enabled() else None
        try:
            from openbimagent.session.schema import EventType

            store.append_new(
                EventType.CUSTOM,
                {"customType": "llm_planner", "mode": "llm" if registry is not None else "template"},
            )
        except Exception:  # noqa: BLE001 — 事件落盘失败不影响运行
            pass

        run_pipeline(
            playbook_path=playbook,
            out_dir=out_dir,
            sessions_dir=sessions_dir,
            session_id=session_id,
            registry=registry,
            input_func=lambda _prompt="": "",
            approval_fn=make_web_approval_fn(session_id, sessions_dir, auto_approve=(mode == "yolo")),
            utility_solver_input=solver_input,
        )
        # G-web：无 CAD 主机的离线 IFC 交付（domain_gate PASS 才投影；失败只发事件不推翻运行结论）
        ifc_info = _deliver_offline_ifc(out_dir, store)
        _runs[session_id].update(
            active=False, status="done", done_at=datetime.now(timezone.utc).isoformat()
        )
        if ifc_info is not None:
            _runs[session_id]["ifc_delivery"] = ifc_info
    except Exception as exc:  # noqa: BLE001 — 运行失败必须可视化而非吞掉
        _runs[session_id].update(
            active=False,
            status="failed",
            done_at=datetime.now(timezone.utc).isoformat(),
            error=str(exc),
        )
    finally:
        try:
            entry = _archive_run_artifacts(playbook, session_id, brief, out_dir)
            try:
                used_ids = [str(item) for item in (_runs[session_id].get("exemplar_session_ids") or []) if item]
                ir_path = out_dir / "compiled_utility_ir.json"
                if used_ids and ir_path.is_file():
                    _runs[session_id]["exemplar_feedback"] = record_exemplar_feedback(
                        used_ids, _failure_modes_from_ir(ir_path), pack=playbook.parent
                    )
            except Exception:  # noqa: BLE001 — negative-transfer bookkeeping must not break the run
                pass
            # P0-1 自蒸馏钩子：仅成功交付（有归档工件且无错误）才蒸馏 SKILL.md 候选；
            # 候选落 skills/_candidates/，永不自动生效，须人工批准转正（fail-closed 人工门）
            if entry and not _runs[session_id].get("error"):
                from openbimagent.skills.registry import builtin_skills_root, distill_candidate

                candidate = distill_candidate(
                    builtin_skills_root(),
                    session_id=session_id,
                    brief=brief,
                    playbook=playbook.parent.name,
                    files=[f["name"] for f in entry["files"]],
                    archived_at=entry["archived_at"],
                )
                if candidate is not None:
                    _runs[session_id]["skill_candidate"] = candidate.name
        except Exception:  # noqa: BLE001 — 归档/蒸馏失败不影响运行结论
            pass
        # P1-2 hooks：run_end 观测事件（无论成败必触发）
        try:
            from openbimagent.core.hooks import default_hook_bus

            default_hook_bus().emit(
                "run_end",
                session_id=session_id,
                playbook=playbook.parent.name,
                error=_runs[session_id].get("error") or "",
                done_at=_runs[session_id].get("done_at") or "",
            )
        except Exception:  # noqa: BLE001 — hooks 故障不影响运行结论
            pass


#: 离线 IFC 交付产物（build_ifc_ids_package 固定文件名；进归档与工件白名单，前端可下载）
_IFC_DELIVERY_FILES = (
    "municipal_utility.ifc",
    "municipal_utility.ids",
    "ifc_ids_validation_report.json",
    "ifc_ids_rule_evidence.json",
)

#: 运行结束后归档的关键工件名（存在才拷，缺省跳过）
_ARCHIVE_FILES = (
    "artifact_manifest.json",
    "compiled_utility_ir.json",
    "municipal_rule_set.json",
    "domain_gate_report.json",
    "rule_evidence_bundle.json",
    "domain_gate_report.md",
    "PLAN.md",
    *_IFC_DELIVERY_FILES,
)


def _deliver_offline_ifc(out_dir: Path, store: Any) -> dict[str, Any] | None:
    """无 CAD 主机的离线 IFC 交付：domain_gate PASS 时把 compiled IR 投影成 IFC4X3+IDS 包。

    复用测试/benchmark 同一条离线链（FakeBlenderSemanticExecutor → build_ifc_ids_package,
    IfcOpenShell 写盘,零主机依赖）。规则身份用落盘工件 sha256 钉死
    （rule set / gate 报告）,决策状态取 gate 结论——IFC 每个对象的 Pset 都可回溯到本 run
    的规则评估。任何失败只发事件不抛：运行结论已由 pipeline 给出,IFC 打包是叠加交付层。
    """
    import hashlib

    gate_path = out_dir / "domain_gate_report.json"
    ir_path = out_dir / "compiled_utility_ir.json"
    rule_set_path = out_dir / "municipal_rule_set.json"
    if not (gate_path.is_file() and ir_path.is_file() and rule_set_path.is_file()):
        return None
    try:
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        if gate.get("ok") is not True:
            return None  # fail-closed：门禁非 PASS 不交付
        from openbimagent.assembly.rule_projection import RuleProjectionIdentity
        from openbimagent.assembly.semantic_snapshot import FakeBlenderSemanticExecutor
        from openbimagent.deliver.ifc_ids import build_ifc_ids_package
        from openbimagent.utility import CompiledUtilityIR

        compiled = CompiledUtilityIR.model_validate(json.loads(ir_path.read_text(encoding="utf-8")))
        identity = RuleProjectionIdentity(
            rule_evidence_bundle_sha256=hashlib.sha256(rule_set_path.read_bytes()).hexdigest(),
            rule_evaluation_sha256=hashlib.sha256(gate_path.read_bytes()).hexdigest(),
            rule_decision_status=str(gate.get("status") or "pass").lower(),
            production_verification="eligible",
            exception_approval_id=None,
            exception_approval_sha256=None,
        )
        snapshot = FakeBlenderSemanticExecutor().execute(compiled, rule_identity=identity)
        package = build_ifc_ids_package(snapshot, output_dir=out_dir)
        info: dict[str, Any] = {
            "status": "pass" if package.report.ok else "fail",
            "files": list(_IFC_DELIVERY_FILES),
            "checked_entity_count": package.report.checked_entity_count,
            "findings": len(package.report.findings),
        }
    except Exception as exc:  # noqa: BLE001 — IFC 打包失败必须可见，但不推翻 pipeline 结论
        info = {"status": "error", "error": str(exc), "files": list(_IFC_DELIVERY_FILES)}
    try:
        from openbimagent.session.schema import EventType

        store.append_new(EventType.CUSTOM, {"customType": "ifc_delivery", **info})
    except Exception:  # noqa: BLE001 — 事件落盘失败不影响交付本身
        pass
    return info


def _archive_root(pack: Path) -> Path:
    """归档根目录：OPENBIMAGENT_ARCHIVE_DIR 覆盖（测试沙箱）→ <root>/<pack>/；缺省 <pack>/assets/auto_archive/。"""
    override = os.environ.get("OPENBIMAGENT_ARCHIVE_DIR")
    if override:
        return Path(override) / pack.name
    return pack / "assets" / "auto_archive"


def _load_municipal_state(session_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        return None
    path = _sessions_dir() / "municipal" / session_id / "state.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _failure_modes_from_ir(ir_path: Path) -> set[str]:
    try:
        data = json.loads(ir_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    modes: set[str] = set()
    for evidence in data.get("evidence") or []:
        if isinstance(evidence, dict) and evidence.get("status") in {"fail", "unknown"}:
            rule_id = evidence.get("rule_id")
            if rule_id:
                modes.add(str(rule_id))
    return modes


def _extract_experience_record(
    brief: str,
    state: dict[str, Any],
    three: dict[str, Any],
    diff_0_1: list[dict[str, Any]],
    diff_1_2: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Build a conditional experience record from S0/S1/S2 evidence.

    Only automatic/visual repairs and human corrections qualify. Preference-only
    edits are excluded because they are not engineering failure evidence. The
    record never stores absolute X/Y coordinates, only field-level direction and
    relative deltas for non-coordinate fields.
    """
    history = [entry for entry in (state.get("history") or []) if isinstance(entry, dict)]
    effective: set[str] = set()
    for entry in history:
        action, source = entry.get("action"), entry.get("source")
        if action == "repair" and source in {"automatic", "visual_proposal"}:
            effective.add(str(source))
        elif action == "save" and source == "correction":
            effective.add("correction")
    if not effective:
        return None

    from openbimagent.utility.state_diff import compute_candidate_state_diff

    candidate = three.get("S1") or state.get("candidate") or {}
    validation = state.get("validation") or {}
    raw_history = state.get("history") or []
    source_diffs = list(diff_0_1)
    indices = three.get("indices") or {}
    s1_index, s2_index = indices.get("S1"), indices.get("S2")
    if isinstance(s1_index, int) and isinstance(s2_index, int) and s2_index > s1_index:
        current = deepcopy(three.get("S1") or {})
        for index in range(s1_index + 1, s2_index + 1):
            entry = raw_history[index] if index < len(raw_history) else None
            if not isinstance(entry, dict):
                continue
            next_candidate = entry.get("candidate")
            if not isinstance(next_candidate, dict):
                continue
            action, source = entry.get("action"), entry.get("source")
            keep = (action == "save" and source == "correction") or (
                action == "repair" and source in {"automatic", "visual_proposal"}
            )
            if keep:
                source_diffs.extend(compute_candidate_state_diff(current, next_candidate))
            current = next_candidate
    else:
        source_diffs.extend(diff_1_2)

    conditions = set(_text_features(brief or ""))
    system_id = candidate.get("system_id")
    if isinstance(system_id, str) and system_id:
        conditions.add(f"system:{system_id}")
    for key in ("nodes", "segments"):
        items = candidate.get(key)
        if isinstance(items, list):
            conditions.add(f"{key}:{len(items)}")
    for issue in validation.get("issues") or []:
        if isinstance(issue, dict) and issue.get("rule_id"):
            conditions.add(f"rule:{issue['rule_id']}")

    failure_modes: set[str] = set()
    for entry in history:
        if entry.get("action") == "auto_loop_start":
            for rule_id in entry.get("initial_failure_modes") or []:
                if rule_id:
                    failure_modes.add(str(rule_id))
        for item in entry.get("before_failures") or []:
            rule_id = str(item).split("|", 1)[0]
            if rule_id:
                failure_modes.add(rule_id)
        report = entry.get("report") or {}
        if isinstance(report, dict):
            for issue in report.get("issues") or []:
                if not isinstance(issue, dict):
                    continue
                for rule_id in issue.get("constraint_ids") or []:
                    if rule_id:
                        failure_modes.add(str(rule_id))
        for patch in entry.get("patches") or []:
            if not isinstance(patch, dict):
                continue
            for rule_id in patch.get("constraint_ids") or []:
                if rule_id:
                    failure_modes.add(str(rule_id))
    if not failure_modes:
        for issue in validation.get("issues") or []:
            if isinstance(issue, dict) and issue.get("status") in {"fail", "unknown"} and issue.get("rule_id"):
                failure_modes.add(str(issue["rule_id"]))

    strategy: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add_strategy(entity_type: str, field: str, old: Any, new: Any) -> None:
        if not field:
            return
        numeric = (
            isinstance(old, (int, float)) and not isinstance(old, bool)
            and isinstance(new, (int, float)) and not isinstance(new, bool)
        )
        if numeric:
            direction = "increase" if new > old else "decrease" if new < old else "unchanged"
        elif old is None and new is not None:
            direction = "set"
        elif old is not None and new is None:
            direction = "clear"
        else:
            direction = "changed"
        key = (entity_type, field, direction)
        if key in seen:
            return
        seen.add(key)
        item: dict[str, Any] = {
            "entity_type": entity_type,
            "field": field,
            "direction": direction,
            "rule_alignment": sorted(failure_modes)[:8],
        }
        if numeric and field not in {"x_m", "y_m"}:
            item["delta"] = round(float(new - old), 6)
        strategy.append(item)

    for diff in source_diffs:
        if isinstance(diff, dict):
            add_strategy(str(diff.get("entity_type") or ""), str(diff.get("field") or ""),
                         diff.get("old_value"), diff.get("new_value"))
    for entry in history:
        for patch in entry.get("patches") or []:
            if not isinstance(patch, dict):
                continue
            tokens = str(patch.get("path") or "").strip("/").split("/")
            entity_type = {"nodes": "node", "segments": "segment"}.get(tokens[0]) if tokens else None
            if entity_type is None or len(tokens) < 2:
                continue
            add_strategy(entity_type, tokens[-1], patch.get("old_value"), patch.get("value"))

    evidence_sources: list[str] = []
    if "automatic" in effective:
        evidence_sources.append("deterministic_repair")
    if "visual_proposal" in effective:
        evidence_sources.append("visual_proposal")
    if "correction" in effective:
        evidence_sources.append("human_correction")

    confirmation = state.get("confirmation") or {}
    delivery = state.get("delivery") or {}
    receipt = delivery.get("receipt") or {}
    delivery_ok = delivery.get("status") == "completed" or (
        delivery.get("status") == "partial" and receipt.get("host_status") == "completed"
    )
    s2_sha = (three.get("sha256") or {}).get("S2")
    admitted = bool(
        validation.get("executable")
        and isinstance(confirmation, dict)
        and s2_sha
        and confirmation.get("candidate_sha256") == s2_sha
        and delivery_ok
        and delivery.get("revision") == confirmation.get("revision")
    )
    return {
        "applicable_conditions": sorted(conditions)[:40],
        "failure_modes": sorted(failure_modes)[:20],
        "repair_strategy": strategy[:20],
        "evidence_sources": evidence_sources,
        "admission_verified": admitted,
        "utility_stats": {"used_count": 0, "pass_count": 0, "fail_streak": 0, "active": True},
    }


def record_exemplar_feedback(
    used_session_ids: list[str] | tuple[str, ...],
    failure_modes: list[str] | tuple[str, ...] | set[str],
    *,
    pack: Path | None = None,
    archive_root: Path | None = None,
) -> dict[str, Any]:
    """Update exemplar utility stats and deactivate repeated negative transfer.

    A repeated failure mode in two uses sets ``active`` to false. Retrieval then
    stops injecting that exemplar while keeping the archive entry for audit.
    """
    if pack is None:
        pack = _PLAYBOOKS["municipal_utility"].parent
    root = archive_root if archive_root is not None else _archive_root(Path(pack))
    index_path = root / "index.json"
    if not index_path.is_file():
        return {"updated": 0, "deactivated": [], "missing": sorted(set(used_session_ids))}
    try:
        entries = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"updated": 0, "deactivated": [], "missing": sorted(set(used_session_ids))}
    if not isinstance(entries, list):
        return {"updated": 0, "deactivated": [], "missing": sorted(set(used_session_ids))}
    wanted_ids = set(used_session_ids)
    wanted_modes = {str(item) for item in failure_modes if item}
    updated: list[str] = []
    deactivated: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("session_id") not in wanted_ids:
            continue
        record = entry.get("experience_record")
        if not isinstance(record, dict):
            continue
        stats = record.setdefault("utility_stats", {})
        stats["used_count"] = int(stats.get("used_count") or 0) + 1
        repeated = wanted_modes & {str(item) for item in record.get("failure_modes") or []}
        if repeated:
            stats["fail_streak"] = int(stats.get("fail_streak") or 0) + 1
            if stats["fail_streak"] >= 2:
                stats["active"] = False
                deactivated.append(str(entry.get("session_id")))
        else:
            stats["pass_count"] = int(stats.get("pass_count") or 0) + 1
            stats["fail_streak"] = 0
        updated.append(str(entry.get("session_id")))
    if updated:
        index_path.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"updated": len(updated), "deactivated": sorted(set(deactivated)),
            "missing": sorted(wanted_ids - set(updated))}



def retrieve_municipal_ontology_recipes(state: dict[str, Any], base_dir: Path | None = None) -> dict[str, Any]:
    """检索市政本体配方（runs 入口的薄封装；两跳图谱检索见 ``utility.wiki_ontology``）。

    输入为 municipal workflow state（或含 ``candidate``/``validation`` 的等价 dict），
    返回 ``operator_priority`` / ``recommended_camera_requests`` / ``reusable_assets``。
    只读：本体目录不存在时返回模式驱动的缺省配方，绝不写盘。
    """
    from openbimagent.utility.wiki_ontology import retrieve_ontology_recipes

    issues = [item for item in ((state or {}).get("validation") or {}).get("issues", []) if isinstance(item, dict)]
    return retrieve_ontology_recipes((state or {}).get("candidate") or {}, issues, base_dir=base_dir, top_k=3)


def _archive_run_artifacts(playbook: Path, session_id: str, brief: str, out_dir: Path) -> dict[str, Any] | None:
    """P2 素材积累：交付工件只增不改地归档进 Domain Pack assets/auto_archive/<session>/。

    设计：纯增量（不写回 knowledge/ 受信任规则）；目录经 .gitignore 忽略；
    index.json 记录每次归档（session/时间/文件清单/sha256），供 researcher 角色后续引用。
    返回本次归档的 index 条目（无工件可归档时返回 None）。
    """
    import hashlib

    pack = playbook.parent
    archive_root = _archive_root(pack)
    dest_dir = archive_root / session_id
    copied: list[dict[str, Any]] = []
    for name in _ARCHIVE_FILES:
        src = out_dir / name
        if not src.is_file():
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        (dest_dir / name).write_bytes(data)
        copied.append({"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    if not copied:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    index_path = archive_root / "index.json"
    index: list[dict[str, Any]] = []
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            index = []
    entry = {
        "session_id": session_id,
        "brief": brief[:120],
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "files": copied,
    }
    three: dict[str, Any] | None = None
    diffs = {"S0_S1": [], "S1_S2": []}
    experience_record: dict[str, Any] | None = None
    try:
        state = _load_municipal_state(session_id)
        if state is not None:
            from openbimagent.utility.state_diff import (
                compute_candidate_state_diff,
                derive_revision_three_states,
            )

            three = derive_revision_three_states(state.get("history") or [])
            s0, s1, s2 = three.get("S0"), three.get("S1"), three.get("S2")
            if s0 is not None:
                diffs["S0_S1"] = compute_candidate_state_diff(s0, s1 if s1 is not None else s0)
                diffs["S1_S2"] = compute_candidate_state_diff(
                    s1 if s1 is not None else s0,
                    s2 if s2 is not None else s1 if s1 is not None else s0,
                )
                experience_record = _extract_experience_record(
                    brief, state, three, diffs["S0_S1"], diffs["S1_S2"]
                )
                entry["three_state_summary"] = {
                    "requirement_revision": three.get("requirement_revision"),
                    "sha256": three.get("sha256"),
                    "auto_retry_rounds": three.get("auto_retry_rounds"),
                    "degradation_rollback": three.get("degradation_rollback"),
                    "human_sources": three.get("human_sources"),
                    "diff_counts": {"S0_S1": len(diffs["S0_S1"]), "S1_S2": len(diffs["S1_S2"])},
                }
    except Exception:  # noqa: BLE001 — experience extraction must never break archiving
        three = None
        experience_record = None
    if experience_record is not None:
        if experience_record.get("admission_verified") is True:
            entry["experience_record"] = experience_record
        else:
            try:
                from openbimagent.audit import log_audit

                log_audit("experience_rejected", {
                    "session": session_id,
                    "reason": "admission_not_verified",
                    "failure_modes": experience_record.get("failure_modes", []),
                })
            except Exception:  # noqa: BLE001 — audit failure must not break archiving
                pass
    index.append(entry)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    # BIM-Wiki-Ontology 写回链路：经验录取通过（confirm + 零硬冲突 + 交付完成）时，
    # 把同一会话编译为 Wiki 案例词条（落在 <pack>/wiki_ontology/，与本归档同包）。
    if experience_record is not None and experience_record.get("admission_verified") is True and state is not None:
        try:
            from openbimagent.utility.wiki_ontology import compile_and_archive_wiki_case

            wiki = compile_and_archive_wiki_case(
                session_id,
                {"candidate": (three or {}).get("S0"), "validation": None},
                state,
                {},
                base_dir=pack / "wiki_ontology",
            )
            entry["wiki_case"] = {key: wiki.get(key) for key in ("case_id", "markdown_path", "winning_operators", "total_cost_score")}
            index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:  # noqa: BLE001 — Wiki 沉淀失败不影响既有归档
            pass
    return entry


def add_runs(app: FastAPI) -> None:
    """注册真实运行端点（由 build_m2_readonly_app 调用）。"""

    @app.post("/api/v1/sessions", summary="新建对话会话：初始化 JSONL 与 index.json 索引（不跑后台管道）", tags=["Workbench"])
    async def create_session_endpoint(request: dict[str, Any] | None = None) -> JSONResponse:
        req = request or {}
        raw_title = req.get("title")
        title = str(raw_title).strip() if raw_title else "新工程对话"
        playbook_key = str(req.get("playbook", "municipal_utility")).strip() or "municipal_utility"
        from openbimagent.session.schema import uuid7
        from openbimagent.session.store import SessionStore
        from openbimagent.server.workspaces import current_workspace_id, stamp_session_workspace

        session_id = f"sess-{str(uuid7())[:8]}"
        sessions_dir = _sessions_dir()
        sessions_dir.mkdir(parents=True, exist_ok=True)
        SessionStore(sessions_dir / f"{session_id}.jsonl", title=title, playbook=playbook_key)

        raw_ws = req.get("workspace")
        ws_id = str(raw_ws).strip() if raw_ws else current_workspace_id()
        if ws_id:
            stamp_session_workspace(session_id, ws_id)

        from openbimagent.audit import log_audit
        log_audit("session_create", {"session": session_id, "title": title, "playbook": playbook_key, "workspace": ws_id})

        return JSONResponse(
            content={
                "status": "success",
                "session_id": session_id,
                "title": title,
                "playbook": playbook_key,
                "workspace": ws_id,
            }
        )

    @app.post("/api/v1/runs", summary="新建任务：后台真跑 pipeline（有界多并发；离线模板安全；归档范例反哺）", tags=["Workbench"])
    async def start_run(request: dict[str, Any]) -> JSONResponse:
        brief = str(request.get("brief", "")).strip()
        if not brief:
            return JSONResponse(status_code=400, content={"status": "error", "error": "brief 不能为空"})
        playbook_key = str(request.get("playbook", "municipal_utility"))
        playbook = _PLAYBOOKS.get(playbook_key, _PLAYBOOKS["municipal_utility"])
        if not playbook.is_file():
            return JSONResponse(status_code=500, content={"status": "error", "error": f"playbook 缺失: {playbook}"})
        from openbimagent.server.workspaces import current_workspace_id, get_workspace_execution_mode

        ws_id = str(request.get("workspace_id") or request.get("workspace") or "").strip() or None
        effective_ws = ws_id or current_workspace_id()
        effective_mode = str(request.get("mode") or "").strip() or get_workspace_execution_mode(effective_ws)
        if effective_mode != "agent":
            return JSONResponse(status_code=403, content={
                "status": "error", "code": "approval_required",
                "error": "HTTP runs require agent mode with server-side approval gates",
            })
        with _run_lock:
            idem = str(request.get("idempotency_key") or "").strip()
            if idem:
                for sid, r in _runs.items():
                    if r.get("idempotency_key") == idem:
                        return JSONResponse(
                            content={"status": "success", "session_id": sid, "idempotent_replay": True}
                        )
            active_ids = [sid for sid, r in _runs.items() if r["active"]]
            if len(active_ids) >= _max_concurrent():
                return JSONResponse(
                    status_code=409,
                    content={"status": "error", "error": f"并发上限 {_max_concurrent()} 已满", "active": active_ids},
                )
            from openbimagent.session.schema import uuid7

            session_id = str(uuid7())
            # 缺陷一修复：检索本包归档 Top-3 相似交付，作为 in-context 范例注入本轮 brief
            exemplars = _retrieve_exemplars(brief, playbook.parent)
            enriched_context = brief
            if exemplars:
                lines = "\n".join(
                    f"{i + 1}. {e.get('brief', '')}（交付于 {str(e.get('archived_at', ''))[:10]}，工件："
                    f"{'、'.join(f['name'] for f in e.get('files', []))}）"
                    for i, e in enumerate(exemplars)
                )
                enriched_context = (
                    f"{brief}\n\n[相似历史交付参考（系统自素材归档检索注入，仅供对齐口径，不得照抄坐标）]\n{lines}"
                )
                experience_lines: list[str] = []
                for i, exemplar in enumerate(exemplars):
                    record = exemplar.get("experience_record")
                    if not isinstance(record, dict):
                        continue
                    modes = "、".join(str(m) for m in record.get("failure_modes") or []) or "无"
                    strategies = "；".join(
                        f"{item.get('entity_type')}.{item.get('field')} {item.get('direction')}"
                        + (f" Δ={item.get('delta')}" if item.get("delta") is not None else "")
                        for item in (record.get("repair_strategy") or []) if isinstance(item, dict)
                    ) or "无"
                    experience_lines.append(f"- 范例{i + 1} 缺陷类：{modes}；修正策略：{strategies}")
                if experience_lines:
                    enriched_context = (
                        f"{enriched_context}\n\n[历史缺陷与修正策略（系统自归档经验检索注入，仅供规避同类缺陷；"
                        "严禁照抄坐标或直接复制数值）]\n" + "\n".join(experience_lines)
                    )
            # P0-1 渐进披露：技能目录（仅 name/description，不含正文）注入上下文，供规划阶段按需调用
            from openbimagent.skills.registry import default_skill_registry

            skill_fragment = default_skill_registry().catalog_fragment()
            if skill_fragment:
                enriched_context = f"{enriched_context}\n\n{skill_fragment}"
            # P0-4 记忆层：末 N 条长期记忆/用户画像注入（人工审批写入的偏好，非工程证据）
            from openbimagent.core.memory import default_memory_store

            memory_fragment = default_memory_store().prompt_fragment()
            if memory_fragment:
                enriched_context = f"{enriched_context}\n\n{memory_fragment}"
            _runs[session_id] = {
                "active": True,
                "status": "active",
                "idempotency_key": idem,
                "session_id": session_id,
                "brief": brief,
                "playbook": playbook_key,
                "exemplars": [e.get("brief", "") for e in exemplars],
                "exemplar_session_ids": [e.get("session_id") for e in exemplars if e.get("session_id")],
                "started_at": datetime.now(timezone.utc).isoformat(),
                "done_at": None,
                "error": None,
            }
            thread = threading.Thread(
                target=_execute_run,
                args=(brief, playbook, session_id, enriched_context, effective_ws, effective_mode),
                daemon=True,
            )
            thread.start()
        from openbimagent.audit import log_audit

        log_audit(
            "run_start",
            {"session": session_id, "playbook": playbook_key, "mode": effective_mode, "workspace": effective_ws},
        )
        return JSONResponse(
            content={"status": "success", "session_id": session_id, "playbook": playbook_key, "mode": effective_mode, "workspace": effective_ws, "exemplars_used": len(exemplars)}
        )

    @app.get("/api/v1/runs/active", summary="运行状态（轮询用；runs 全量 + run 兼容字段）", tags=["Workbench"])
    async def run_active() -> dict:
        # 容错:超时仍 active 的 run 标记 failed(run_timeout),不留僵尸运行阻塞并发额度
        now = datetime.now(timezone.utc)
        for r in _runs.values():
            if r.get("active") and r.get("started_at"):
                try:
                    el = (now - datetime.fromisoformat(r["started_at"])).total_seconds()
                    if el > _run_timeout_s():
                        r.update(active=False, status="failed", error="run_timeout", done_at=now.isoformat())
                        from openbimagent.audit import log_audit

                        log_audit("run_timeout", {"session": r.get("session_id"), "elapsed_s": int(el)}, "failed")
                except ValueError:
                    pass
        runs = sorted(_runs.values(), key=lambda r: r["started_at"] or "", reverse=True)
        active = next((r for r in runs if r["active"]), runs[0] if runs else None)
        return {"status": "success", "runs": runs, "run": active, "max_concurrent": _max_concurrent()}

    @app.post("/api/v1/runs/{session_id}/stop", summary="停止运行：拒绝其全部待决审批票据唤醒阻塞线程（在下一个审批门处中止，不杀线程）", tags=["Workbench"])
    async def stop_run(session_id: str) -> JSONResponse:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        run = _runs.get(safe)
        if run is None:
            return JSONResponse(status_code=404, content={"status": "error", "error": f"运行不存在: {safe}"})
        if not run.get("active"):
            return JSONResponse(status_code=409, content={"status": "error", "error": "运行已结束，无需停止"})
        from openbimagent.server.approvals import reject_pending_for_session

        run["stop_requested"] = True
        woken = reject_pending_for_session(safe)
        return JSONResponse(
            content={
                "status": "success",
                "session_id": safe,
                "woken_approvals": woken,
                "note": "已拒绝该运行全部待决票据；线程将在当前审批门处按拒绝路径退出（确定性求解中段不打断，语义安全）",
            }
        )

    @app.get("/api/v1/sessions/{session_id}/export", summary="导出会话（fmt=jsonl 原始事件流 / fmt=md 可读纪要）", tags=["Workbench"])
    async def export_session(session_id: str, fmt: str = "jsonl"):
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        path = _sessions_dir() / f"{safe}.jsonl"
        if not path.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": f"会话不存在: {safe}"})
        from fastapi.responses import Response

        raw = path.read_bytes()
        if fmt == "jsonl":
            return Response(
                content=raw,
                media_type="application/x-ndjson",
                headers={"Content-Disposition": f'attachment; filename="session-{safe[:8]}.jsonl"'},
            )
        if fmt == "md":
            lines = [f"# 会话导出 {safe}", ""]
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                p = e.get("payload") or {}
                ts = str(e.get("timestamp", ""))[:19].replace("T", " ")
                if e.get("type") == "message":
                    role = "用户" if p.get("role") == "user" else "Agent"
                    lines.append(f"**{role}**（{ts}）：{p.get('content', '')}\n")
                elif e.get("type") == "tool_call":
                    lines.append(f"- `{p.get('toolName', 'tool')}`（{ts}）：{str(p.get('result_ui_view') or p.get('args_summary') or '')[:200]}")
                elif e.get("type") == "custom":
                    lines.append(f"- ◆ {p.get('customType', 'custom')}（{ts}）")
            return Response(
                content="\n".join(lines).encode("utf-8"),
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="session-{safe[:8]}.md"'},
            )
        return JSONResponse(status_code=400, content={"status": "error", "error": "fmt 仅支持 jsonl / md"})

    @app.get("/api/v1/sessions/{session_id}/events", summary="读取会话事件（Session JSONL，倒序截尾）", tags=["Workbench"])
    async def session_events(session_id: str, tail: int = 200) -> JSONResponse:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        path = _sessions_dir() / f"{safe}.jsonl"
        if not path.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": f"会话不存在: {safe}"})
        events: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # 损坏行容错：跳过（与 SessionStore 语义一致）
        except OSError as exc:
            return JSONResponse(status_code=500, content={"status": "error", "error": f"读取失败: {exc}"})
        return JSONResponse(content={"status": "success", "session_id": safe, "events": events[-max(1, min(tail, 1000)):]})

    @app.delete("/api/v1/sessions/{session_id}", summary="删除会话（JSONL + index 条目 + FTS 索引行一致性清理；运行中 409）", tags=["Workbench"])
    async def delete_session(session_id: str) -> JSONResponse:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        if safe in _runs and _runs[safe].get("active"):
            return JSONResponse(status_code=409, content={"status": "error", "error": "会话所属运行仍在进行，不能删除（先等收敛或拒绝审批门）"})
        path = _sessions_dir() / f"{safe}.jsonl"
        if not path.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": f"会话不存在: {safe}"})
        try:
            path.unlink()
            # index.json 条目移除（与 SessionStore 同一把索引锁 + 原子写）
            from openbimagent.session.store import INDEX_FILENAME, _atomic_write_json, _locked_index

            index_path = _sessions_dir() / INDEX_FILENAME
            with _locked_index(index_path):
                if index_path.is_file():
                    index = json.loads(index_path.read_text(encoding="utf-8"))
                    index["sessions"] = [e for e in index.get("sessions", []) if e.get("id") != safe]
                    _atomic_write_json(index_path, index)
            # FTS 索引行清理（删过的会话不再被 /recall 命中）
            from openbimagent.session.search import default_search_index

            default_search_index(_sessions_dir()).delete_session(safe)
        except OSError as exc:
            return JSONResponse(status_code=500, content={"status": "error", "error": f"删除失败: {exc}"})
        return JSONResponse(content={"status": "success", "deleted": safe})

    @app.get("/api/v1/sessions/search", summary="会话全文检索（FTS5；返回可溯源 session/event/snippet）", tags=["Workbench"])
    async def sessions_search(q: str, limit: int = 10) -> JSONResponse:
        from openbimagent.session.search import default_search_index

        index = default_search_index(_sessions_dir())
        index.sync()
        return JSONResponse(content={"status": "success", "query": q, "items": index.search(q, limit=limit)})

    @app.get(
        "/api/v1/sessions/{session_id}/events/stream",
        summary="会话事件 SSE 实时跟随（P1：回放后持续推送新增，运行结束自动关闭）",
        tags=["Workbench"],
    )
    async def session_events_stream(session_id: str, request: Request):
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        path = _sessions_dir() / f"{safe}.jsonl"
        if not path.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": f"会话不存在: {safe}"})
        if not _session_sse_budget.try_acquire():
            return JSONResponse(status_code=429, content={
                "status": "error", "code": "rate_limited",
                "error": f"SSE 并发流超出预算（{M2_SSE_MAX_ACTIVE_STREAMS}）",
            })
        # EventSource 重连自动携带 Last-Event-ID；初始手动回放用 ?cursor=。
        # 游标 = 已消费字节 offset；非法值回退 0 全量重放（rewind 截断由 follow 内部自愈）。
        start_offset = 0
        cursor = request.headers.get("last-event-id") or request.query_params.get("cursor")
        if cursor:
            try:
                start_offset = max(0, int(cursor))
            except ValueError:
                start_offset = 0

        def _is_active() -> bool:
            run = _runs.get(safe)
            return bool(run and run["active"])

        async def _guarded_follow() -> Any:
            try:
                async for frame in follow_session_jsonl(path, start_offset=start_offset, is_active=_is_active):
                    yield frame
            finally:
                _session_sse_budget.release()

        return StreamingResponse(
            _guarded_follow(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/api/v1/sessions/{session_id}/fork", summary="会话分支（/tree fork：from_event_id 缺省取当前头）", tags=["Workbench"])
    async def fork_session(session_id: str, request: dict[str, Any]) -> JSONResponse:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        path = _sessions_dir() / f"{safe}.jsonl"
        if not path.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": f"会话不存在: {safe}"})
        from openbimagent.session.store import SessionStore

        try:
            store = SessionStore(path)
            from_event_id = str(request.get("from_event_id") or "").strip()
            title = request.get("title")
            title = title if isinstance(title, str) else None
            if from_event_id:
                new_store = store.fork(from_event_id, title=title)
            else:
                chain = store.get_event_chain()
                if not chain:
                    return JSONResponse(status_code=400, content={"status": "error", "error": "空会话无事件可分支"})
                new_store = store.branch(chain[-1].id, title=title)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"status": "error", "error": str(exc)})
        except OSError as exc:
            return JSONResponse(status_code=500, content={"status": "error", "error": f"分支失败: {exc}"})
        from openbimagent.audit import log_audit

        log_audit("fork", {"session": safe, "new_session": new_store.session_id})
        return JSONResponse(content={"status": "success", "session_id": new_store.session_id, "forked_from": safe})

    @app.post(
        "/api/v1/sessions/{session_id}/rewind",
        summary="时间旅行回滚（截断 after_event_id 之后的事件，保留主干）",
        tags=["Workbench"],
    )
    async def rewind_session(session_id: str, request: dict[str, Any]) -> JSONResponse:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        path = _sessions_dir() / f"{safe}.jsonl"
        if not path.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": f"会话不存在: {safe}"})
        from openbimagent.session.store import SessionStore

        after_event_id = str(request.get("after_event_id") or "").strip()
        if not after_event_id:
            return JSONResponse(status_code=400, content={"status": "error", "error": "缺少 after_event_id"})
        restore_files = bool(request.get("restore_files", False))
        restored = 0
        try:
            store = SessionStore(path)
            removed = store.truncate_after(after_event_id)
            from openbimagent.audit import log_audit
            from openbimagent.core.loop import restore_file_checkpoints

            if restore_files:
                restored = restore_file_checkpoints(safe)  # 文件级物理回滚(Claude 范式)
            log_audit(
                "rewind",
                {"session": safe, "after": after_event_id, "removed": removed, "restored_files": restored},
            )
        except KeyError as exc:
            return JSONResponse(status_code=404, content={"status": "error", "error": str(exc)})
        except OSError as exc:
            return JSONResponse(status_code=500, content={"status": "error", "error": f"回滚失败: {exc}"})
        return JSONResponse(
            content={"status": "success", "session_id": safe, "removed": removed, "restored_files": restored}
        )

    @app.get("/api/v1/audit", summary="审计日志(最近 tail 条,倒序;安全操作留痕)", tags=["Workbench"])
    async def get_audit(tail: int = 100) -> JSONResponse:
        from openbimagent.audit import read_audit

        return JSONResponse(content={"status": "success", "items": read_audit(min(500, max(1, tail)))})

    @app.post("/api/v1/audit", summary="客户端错误上报(容错:前端崩溃/异常写入审计)", tags=["Workbench"])
    async def post_audit(request: dict[str, Any]) -> JSONResponse:
        from openbimagent.audit import log_audit

        action = str(request.get("action") or "client_error")
        raw_detail = request.get("detail")
        detail = raw_detail if isinstance(raw_detail, dict) else {"raw": str(raw_detail)[:300]}
        log_audit(action, detail, str(request.get("result") or "reported"), actor="frontend")
        return JSONResponse(content={"status": "success"})

    @app.post(
        "/api/v1/runs/{session_id}/steer",
        summary="运行中插话纠偏(Devin 范式):指令写会话流留痕,供审批门/后续角色消费",
        tags=["Workbench"],
    )
    async def steer_run(session_id: str, request: dict[str, Any]) -> JSONResponse:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        instruction = str(request.get("instruction") or "").strip()
        if not instruction:
            return JSONResponse(status_code=400, content={"status": "error", "error": "缺少 instruction"})
        with _run_lock:
            run = _runs.get(safe)
            if run is None or not run.get("active"):
                return JSONResponse(
                    status_code=409, content={"status": "error", "error": f"运行不存在或已结束: {safe}"}
                )
            queue = run.setdefault("steer_queue", [])
            queue.append(instruction)
            pending = len(queue)
        # steer_requested 事件写会话流(schema CustomType 已预留;前端立即可见,审批人/后续角色可消费)
        try:
            from openbimagent.session.schema import EventType
            from openbimagent.session.store import SessionStore

            store = SessionStore(_sessions_dir() / f"{safe}.jsonl")
            store.append_new(EventType.CUSTOM, {"customType": "steer_requested", "instruction": instruction})
        except Exception:  # noqa: BLE001 — 事件落盘失败不影响纠偏队列本身
            pass
        from openbimagent.audit import log_audit

        log_audit("steer", {"session": safe, "instruction": instruction[:200]})
        return JSONResponse(content={"status": "success", "pending": pending})

    @app.get(
        "/api/v1/training/export",
        summary="导出自愈轨迹为 SFT/DPO 微调格式(仅成功交付案例;轨迹采集非权重更新)",
        tags=["Workbench"],
    )
    async def export_training(fmt: str = "sft") -> JSONResponse:
        if fmt not in ("sft", "dpo"):
            return JSONResponse(status_code=400, content={"status": "error", "error": "fmt 必须是 sft/dpo"})
        from openbimagent.training.trace_export import export_traces

        result = export_traces(fmt)
        from openbimagent.audit import log_audit

        log_audit("training_export", {"format": fmt, "count": result["count"]})
        return JSONResponse(content={"status": "success", **result})

    @app.patch("/api/v1/sessions/{session_id}", summary="更新会话（重命名/归档/解归档）", tags=["Workbench"])
    async def rename_session(session_id: str, request: dict[str, Any]) -> JSONResponse:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        index_path = _sessions_dir() / "index.json"
        if not index_path.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": "会话索引不存在"})
        from openbimagent.session.store import _atomic_write_json, _locked_index

        new_title = request.get("title")
        archived = request.get("archived")
        new_playbook = request.get("playbook")
        new_mode = request.get("mode") or request.get("toolset")

        if new_title is None and archived is None and new_playbook is None and new_mode is None:
            return JSONResponse(status_code=400, content={"status": "error", "error": "缺少更新参数(title, archived, playbook 或 mode)"})

        with _locked_index(index_path):
            try:
                data = json.loads(index_path.read_text(encoding="utf-8"))
                target = None
                for s in data.get("sessions", []):
                    if s.get("id") == safe:
                        target = s
                        break
                if not target:
                    return JSONResponse(status_code=404, content={"status": "error", "error": f"会话不存在: {safe}"})

                if new_title is not None:
                    title_str = str(new_title).strip()
                    if not title_str:
                        return JSONResponse(status_code=400, content={"status": "error", "error": "标题不能为空"})
                    target["title"] = title_str

                if new_playbook is not None:
                    target["playbook"] = str(new_playbook).strip()

                if new_mode is not None:
                    target["mode"] = str(new_mode).strip()

                if archived is not None:
                    is_archived = bool(archived)
                    target["archived"] = is_archived
                    if is_archived:
                        target["archived_at"] = datetime.now(timezone.utc).isoformat()
                    else:
                        target["archived"] = False
                        target.pop("archived_at", None)

                _atomic_write_json(index_path, data)
                return JSONResponse(content={"status": "success", "session": target})
            except Exception as exc:
                return JSONResponse(status_code=500, content={"status": "error", "error": f"更新会话失败: {exc}"})
        return JSONResponse(
            content={
                "status": "success",
                "session_id": safe,
                "title": target.get("title", ""),
                "archived": target.get("archived", False),
            }
        )

    @app.get("/api/v1/archive", summary="Domain Pack 素材归档索引（P2：只增不改）", tags=["Workbench"])
    async def list_archive() -> dict:
        items: list[dict[str, Any]] = []
        for playbook in {p for p in _PLAYBOOKS.values()}:
            index_path = _archive_root(playbook.parent) / "index.json"
            if not index_path.is_file():
                continue
            try:
                entries = json.loads(index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for entry in entries:
                items.append({"pack": playbook.parent.name, **entry})
        items.sort(key=lambda e: e.get("archived_at", ""), reverse=True)
        return {"status": "success", "items": items, "count": len(items)}

    @app.get(
        "/api/v1/runs/artifact",
        summary="运行工件读取（白名单 + sha256/mtime；缺陷六修复：视口流式生长的数据源）",
        tags=["Workbench"],
    )
    async def run_artifact(session: str, name: str) -> JSONResponse:
        safe_session = "".join(c for c in session if c.isalnum() or c in "-_")
        if name not in _ARCHIVE_FILES:
            return JSONResponse(status_code=400, content={"status": "error", "error": f"工件名不在白名单: {name}"})
        path = _REPO_ROOT / "out" / "runs" / safe_session / name
        if not path.is_file():
            for playbook in _PLAYBOOKS.values():
                cand = _archive_root(playbook.parent) / safe_session / name
                if cand.is_file():
                    path = cand
                    break
        if not path.is_file():
            return JSONResponse(status_code=404, content={"status": "error", "error": f"工件不存在: {safe_session}/{name}"})
        import hashlib

        try:
            data = path.read_bytes()
            payload: dict[str, Any] = {
                "status": "success",
                "session": safe_session,
                "name": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "mtime": path.stat().st_mtime,
                "size": len(data),
            }
            if name.endswith(".json"):
                payload["data"] = json.loads(data.decode("utf-8"))
            elif name.endswith((".ifc", ".ids")):
                # 文本 schema（IFC4X3/IDS 均为 STEP/XML 文本），量级数十 KB，直接内联供前端下载
                payload["text"] = data.decode("utf-8", errors="replace")
            return JSONResponse(content=payload)
        except (OSError, json.JSONDecodeError) as exc:
            return JSONResponse(status_code=500, content={"status": "error", "error": f"工件读取失败: {exc}"})
