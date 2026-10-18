"""Revision-bound municipal editing, checking and confirmed delivery.

This is a task workbench, not an experience store. Invalid drafts are retained;
only checked, explicitly confirmed revisions may be handed to a CAD host.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
from typing import Any, Callable
from uuid import uuid4

from fastapi import FastAPI, HTTPException


class WorkflowError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _review_token(state: dict) -> str:
    return _hash({key: state.get(key) for key in ("revision", "candidate_sha256", "validation", "visual", "locks")})


def _tokens(path: str) -> list[str]:
    if not isinstance(path, str) or not path.startswith("/") or path == "/":
        raise WorkflowError("参数路径无效。", 422)
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _at(value: Any, path: str) -> Any:
    try:
        for key in _tokens(path):
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise WorkflowError(f"参数已不存在：{path}。请刷新任务。") from exc


def _replace(value: dict, path: str, replacement: Any) -> None:
    keys = _tokens(path)
    parent: Any = value
    try:
        for key in keys[:-1]:
            parent = parent[int(key)] if isinstance(parent, list) else parent[key]
        key = int(keys[-1]) if isinstance(parent, list) else keys[-1]
        parent[key] = deepcopy(replacement)
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise WorkflowError(f"无法修改参数：{path}。", 422) from exc


def _validate(candidate: dict, context: dict | None) -> dict:
    from openbimagent.utility.candidate import UtilityCandidate, validate_utility_candidate
    from openbimagent.utility.candidate import FrozenRuleContext

    result = validate_utility_candidate(
        UtilityCandidate(solver_input=candidate),
        rule_context=FrozenRuleContext.model_validate(context) if context else None,
    )
    return result.model_dump(mode="json")


class MunicipalWorkflowStore:
    """Single-process atomic state publication, with optimistic revision checks.

    Locks are process-local. Deploy this workbench with one application worker.
    No claim of a transaction across the filesystem and an external CAD host.
    """

    def __init__(self, root: Path, *, validator: Callable = _validate):
        self.root = Path(root)
        self.validator = validator
        self._guard = threading.RLock()
        self._host_guard = threading.Lock()
        self._instance = uuid4().hex

    def _path(self, session_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
            raise WorkflowError("会话标识无效。", 422)
        return self.root / session_id / "state.json"

    def get(self, session_id: str) -> dict | None:
        with self._guard:
            path = self._path(session_id)
            state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
            if state and state.get("delivery", {}).get("status") == "running" and state["delivery"].get("owner") != self._instance:
                state["delivery"].update(status="interrupted", error="服务曾中断，宿主结果未知。请先核对宿主文件；不会自动重试。")
                state["confirmation"] = None
                state["history"].append({**deepcopy(state["delivery"]), "action": "delivery_interrupted", "revision": state["revision"], "at": _now()})
                return self._write(session_id, state)
            if state:
                state["review_token"] = _review_token(state)
            return state

    def _write(self, session_id: str, state: dict) -> dict:
        state["review_token"] = _review_token(state)
        path = self._path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"state-{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
        return deepcopy(state)

    def _current(self, sid: str, revision: Any, digest: Any) -> dict:
        state = self.get(sid)
        if state is None:
            raise WorkflowError("请先保存管网条件。", 404)
        if state["revision"] != revision or state["candidate_sha256"] != digest:
            raise WorkflowError("任务已更新，本次操作未应用。请刷新后核对最新版本。")
        if state.get("delivery", {}).get("status") == "running":
            raise WorkflowError("当前版本正在交给宿主执行，完成后才能修改。")
        return state

    @staticmethod
    def _check_locks(state: dict, candidate: dict) -> None:
        for lock in state.get("locks", []):
            # An index cannot silently change identity under a parameter lock.
            path = lock["path"]
            if _at(candidate, path) != lock["value"]:
                raise WorkflowError(f"{path} 已锁定，请先明确解锁再修改。")
            if lock.get("object_path") and _at(candidate, lock["object_path"]) != lock["object_id"]:
                raise WorkflowError("锁定的对象不能被删除、替换或重排。请先解锁。")

    def save(self, sid: str, body: dict) -> dict:
        with self._guard:
            candidate = body.get("candidate")
            if not isinstance(candidate, dict):
                raise WorkflowError("请提供管网条件对象。", 422)
            candidate = deepcopy(candidate)
            # The request provenance is the actual submitted conditions, never a fixture hash.
            candidate["source_ir_sha256"] = _hash({k: v for k, v in candidate.items() if k != "source_ir_sha256"})
            if len(json.dumps(candidate, ensure_ascii=False, allow_nan=False)) > 250_000:
                raise WorkflowError("当前工作台仅支持小型管网，请减少构件数量。", 422)
            old = self.get(sid)
            if old:
                old = self._current(sid, body.get("base_revision"), body.get("candidate_sha256"))
                self._check_locks(old, candidate)
            elif body.get("base_revision", 0) != 0:
                raise WorkflowError("任务不存在，请重新加载。")
            source = body.get("source", "correction")
            if source not in {"correction", "preference", "requirement_change"}:
                raise WorkflowError("请选择修改原因：纠错、偏好或需求变更。", 422)
            validation = self.validator(candidate, old.get("rule_context") if old else None)
            state = {
                "session_id": sid, "revision": (old or {}).get("revision", 0) + 1,
                "requirement_revision": (old or {}).get("requirement_revision", 1) + int(bool(old) and source == "requirement_change"),
                "candidate": deepcopy(candidate), "candidate_sha256": _hash(candidate),
                "rule_context": validation.get("rule_context"), "validation": validation,
                "locks": deepcopy((old or {}).get("locks", [])), "confirmation": None,
                "visual": None, "proposal": None, "delivery": {},
                "history": deepcopy((old or {}).get("history", [])), "updated_at": _now(),
            }
            state["history"].append({"action": "save", "source": source, "revision": state["revision"],
                                     "at": state["updated_at"], "before_sha256": (old or {}).get("candidate_sha256"),
                                     "after_sha256": state["candidate_sha256"], "candidate": deepcopy(candidate)})
            return self._write(sid, state)

    def lock(self, sid: str, body: dict) -> dict:
        with self._guard:
            state = self._current(sid, body.get("base_revision"), body.get("candidate_sha256"))
            path = body.get("path", "")
            value = _at(state["candidate"], path)
            locks = [item for item in state["locks"] if item["path"] != path]
            if body.get("locked") is True:
                item = {"path": path, "value": deepcopy(value)}
                keys = _tokens(path)
                if len(keys) >= 2 and keys[0] in {"nodes", "segments"}:
                    obj_path = f"/{keys[0]}/{keys[1]}/{ 'node_id' if keys[0] == 'nodes' else 'segment_id'}"
                    item.update(object_path=obj_path, object_id=_at(state["candidate"], obj_path))
                locks.append(item)
            state.update(locks=locks, revision=state["revision"] + 1, confirmation=None, proposal=None, visual=None, updated_at=_now())
            state["history"].append({"action": "lock" if body.get("locked") else "unlock", "path": path,
                                     "revision": state["revision"], "at": state["updated_at"], "source": "human"})
            return self._write(sid, state)

    def propose(self, sid: str, body: dict, proposer: Callable) -> dict:
        with self._guard:
            state = self._current(sid, body.get("base_revision"), body.get("candidate_sha256"))
            source = body.get("source", "deterministic")
            if source == "visual":
                visual = state.get("visual")
                if not visual or visual.get("revision") != state["revision"] or visual.get("workflow_candidate_sha256") != state["candidate_sha256"]:
                    raise WorkflowError("请先对当前版本执行视觉检查。")
                proposal = deepcopy(visual.get("patches", []))
            elif source == "deterministic":
                proposal = proposer(deepcopy(state))
            else:
                raise WorkflowError("未知修复来源。", 422)
            state["proposal"] = {"proposal_id": uuid4().hex, "base_revision": state["revision"], "candidate_sha256": state["candidate_sha256"],
                                  "patches": proposal, "source": source, "at": _now()}
            return self._write(sid, state)

    def apply(self, sid: str, body: dict) -> dict:
        with self._guard:
            state = self._current(sid, body.get("base_revision"), body.get("candidate_sha256"))
            proposal = state.get("proposal")
            if not proposal or proposal["base_revision"] != state["revision"] or not proposal["patches"]:
                raise WorkflowError("没有可应用的修复建议，请先检查并生成建议。")
            if not proposal.get("proposal_id") or body.get("proposal_id") != proposal["proposal_id"]:
                raise WorkflowError("修复建议已更新，请重新加载并审阅后再应用。")
            candidate = deepcopy(state["candidate"])
            for patch in proposal["patches"]:
                if _at(candidate, patch["path"]) != patch["old_value"]:
                    raise WorkflowError("修复建议基于旧参数，未应用。请重新生成建议。")
                _replace(candidate, patch["path"], patch["value"])
            self._check_locks(state, candidate)
            result = self.validator(candidate, state["rule_context"])
            before = _blocking(state["validation"])
            after = _blocking(result)
            visual_proposal = proposal.get("source") == "visual"
            if after - before or (not visual_proposal and len(after) >= len(before)) or (visual_proposal and not result.get("executable")):
                raise WorkflowError("候选修复没有减少问题，或产生了新的硬约束问题；已保留原版本，请人工处理。")
            state.update(candidate=candidate, candidate_sha256=_hash(candidate), validation=result,
                         revision=state["revision"] + 1, confirmation=None, visual=None, proposal=None,
                         delivery={}, updated_at=_now())
            state["history"].append({"action": "repair", "source": "visual_proposal" if visual_proposal else "automatic", "revision": state["revision"],
                                     "at": state["updated_at"], "patches": proposal["patches"],
                                     "after_sha256": state["candidate_sha256"], "candidate": deepcopy(candidate)})
            return self._write(sid, state)

    def visual(self, sid: str, body: dict, checker: Callable) -> dict:
        with self._guard:
            state = self._current(sid, body.get("base_revision"), body.get("candidate_sha256"))
        # Model/render calls do not hold the store lock. Results for stale drafts are rejected.
        report = checker(deepcopy(state), self._path(sid).parent / f"visual-r{state['revision']}-{uuid4().hex[:8]}")
        with self._guard:
            state = self._current(sid, body.get("base_revision"), body.get("candidate_sha256"))
            state.update(visual=report, confirmation=None, proposal=None, updated_at=_now())
            state["history"].append({"action": "visual", "revision": state["revision"], "at": _now(), "report": report})
            return self._write(sid, state)

    def confirm(self, sid: str, body: dict) -> dict:
        with self._guard:
            state = self._current(sid, body.get("base_revision"), body.get("candidate_sha256"))
            if not state["validation"].get("executable"):
                raise WorkflowError("还有未解决的硬约束或必要资料缺失。请处理问题列表后确认。")
            if body.get("reviewed") is not True:
                raise WorkflowError("请核对当前模型及检查范围后明确确认。", 422)
            if body.get("review_token") != _review_token(state):
                raise WorkflowError("检查结果已更新，请重新加载并核对当前证据后确认。")
            state["confirmation"] = {"revision": state["revision"], "candidate_sha256": state["candidate_sha256"],
                                     "validation_sha256": _hash(state["validation"]),
                                     "visual_sha256": _hash(state["visual"]) if state.get("visual") else None,
                                     "visual_review": "reviewed" if state.get("visual") and state["visual"].get("status") != "unavailable" else "not_run_acknowledged",
                                     "at": _now(), "actor": "human:web-operator"}
            state["history"].append({"action": "confirm", **state["confirmation"]})
            return self._write(sid, state)

    def export(self, sid: str, body: dict, exporter: Callable) -> dict:
        if not self._host_guard.acquire(blocking=False):
            raise WorkflowError("另一个管网版本正在执行宿主操作，请稍后再试。")
        try:
            return self._export(sid, body, exporter)
        finally:
            self._host_guard.release()

    def _export(self, sid: str, body: dict, exporter: Callable) -> dict:
        with self._guard:
            state = self._current(sid, body.get("base_revision"), body.get("candidate_sha256"))
            confirmation = state.get("confirmation")
            if not confirmation or confirmation["revision"] != state["revision"] or confirmation["candidate_sha256"] != state["candidate_sha256"]:
                raise WorkflowError("请先确认当前版本，修改参数后需要重新确认。")
            if body.get("confirm") is not True or body.get("host") not in {"vectorworks", "blender"}:
                raise WorkflowError("请选择宿主并确认执行。", 422)
            attempts = [item for item in [*state["history"], state.get("delivery", {})]
                        if item.get("revision") == state["revision"] and item.get("host") == body["host"]
                        and item.get("status") in {"completed", "partial", "failed", "interrupted"}]
            successful = next((item for item in reversed(attempts)
                               if item["status"] == "completed" or (item["status"] == "partial"
                               and item.get("receipt", {}).get("host_status") == "completed")), None)
            if successful:
                state["delivery"] = {key: value for key, value in successful.items() if key != "action"}
                return self._write(sid, state)
            if attempts:
                previous_id = attempts[-1].get("execution_id")
                if not previous_id or body.get("retry_of") != previous_id:
                    raise WorkflowError("前次宿主执行未完成或结果未知。请核对宿主文件后，明确确认重新执行。")
            # Recheck from the persisted candidate and pinned rules, never accept a client-supplied IR.
            validation = self.validator(state["candidate"], state["rule_context"])
            if not validation.get("executable") or _hash(validation) != confirmation["validation_sha256"]:
                raise WorkflowError("执行前复核与已确认结果不一致，请重新检查并确认。")
            execution_id = f"{sid}-r{state['revision']}-{uuid4().hex[:12]}"
            delivery_context = {
                "revision": state["revision"],
                "candidate_sha256": state["candidate_sha256"],
                "validation_sha256": confirmation["validation_sha256"],
                "rule_context_sha256": validation.get("rule_context_sha256")
                or _hash(state.get("rule_context")),
                "rule_context": validation.get("rule_context"),
                "issues": validation.get("issues") or [],
            }
            state["delivery"] = {"status": "running", "host": body["host"], "revision": state["revision"], "at": _now(), "owner": self._instance, "execution_id": execution_id}
            self._write(sid, state)
        try:
            receipt = exporter(
                validation["compiled_ir"],
                body["host"],
                execution_id,
                delivery_context=delivery_context,
            )
            status = receipt["status"] if receipt.get("status") in {"completed", "partial"} else "failed"
            layers = receipt.get("delivery_layers")
            if layers is None and receipt.get("ifc"):
                from openbimagent.server.municipal_actions import build_delivery_layers

                layers = build_delivery_layers(receipt, receipt.get("ifc") or {})
            base = {key: value for key, value in state["delivery"].items() if key != "ifc"}
            result = {
                **base,
                "status": status,
                "receipt": receipt,
                "delivery_layers": layers,
                "finished_at": _now(),
            }
        except Exception as exc:
            result = {**state["delivery"], "status": "failed", "error": str(exc), "finished_at": _now()}
        with self._guard:
            state = self.get(sid)
            state["delivery"] = result
            state["history"].append({"action": "export", **result})
            return self._write(sid, state)

    def retry_ifc(self, sid: str, body: dict, retryer: Callable) -> dict:
        """Rebuild the IFC stage only; the host receipt and host file are reused unchanged."""
        with self._guard:
            state = self._current(sid, body.get("base_revision"), body.get("candidate_sha256"))
            confirmation = state.get("confirmation")
            if not confirmation or confirmation["revision"] != state["revision"] or confirmation["candidate_sha256"] != state["candidate_sha256"]:
                raise WorkflowError("请先确认当前版本，修改参数后需要重新确认。")
            if body.get("confirm") is not True:
                raise WorkflowError("请明确确认重试 IFC。", 422)
            delivery = state.get("delivery") or {}
            receipt = delivery.get("receipt") or {}
            if delivery.get("status") not in {"completed", "partial"} or receipt.get("status") not in {"completed", "partial"}:
                raise WorkflowError("当前没有可复用的宿主回执；IFC 重试不会执行宿主，请先完成宿主执行。")
            if delivery.get("revision") != state["revision"]:
                raise WorkflowError("宿主回执属于旧版本，不能用于当前版本的 IFC 交付。")
            validation = self.validator(state["candidate"], state["rule_context"])
            if not validation.get("executable") or _hash(validation) != confirmation["validation_sha256"]:
                raise WorkflowError("执行前复核与已确认结果不一致，请重新检查并确认。")
            from openbimagent.utility.contracts import CompiledUtilityIR

            compiled_ir = CompiledUtilityIR.model_validate(validation.get("compiled_ir") or {})
            compiled_sha256 = compiled_ir.canonical_sha256()
            receipt_ir_sha256 = receipt.get("source_ir_sha256")
            if not receipt_ir_sha256:
                raise WorkflowError(
                    "宿主回执缺少可验证的 IR 身份，不能复用；请重新执行宿主。"
                )
            if receipt_ir_sha256 != compiled_sha256:
                raise WorkflowError("宿主回执与当前 IR 身份不一致，不能复用；请重新执行宿主。")
            context = {
                "revision": state["revision"],
                "candidate_sha256": state["candidate_sha256"],
                "validation_sha256": confirmation["validation_sha256"],
                "rule_context_sha256": validation.get("rule_context_sha256")
                or _hash(state.get("rule_context")),
                "rule_context": validation.get("rule_context"),
                "issues": validation.get("issues") or [],
            }
            previous_ifc = delivery.get("ifc") or (delivery.get("receipt") or {}).get("ifc")
            previous_attempt = int((previous_ifc or {}).get("attempt") or 1)
            attempt = previous_attempt + 1
            execution_id = delivery.get("execution_id") or f"{sid}-r{state['revision']}-ifc"
            ifc_stage = retryer(
                compiled_ir.model_dump(mode="json"),
                receipt,
                context,
                execution_id,
                attempt,
            )
            history_entry = {
                "action": "ifc_retry",
                "revision": state["revision"],
                "at": _now(),
                "attempt": attempt,
                "previous_status": (previous_ifc or {}).get("status"),
                "previous_error": (previous_ifc or {}).get("error"),
                "status": ifc_stage.get("status"),
            }
            updated_receipt = {**receipt, "ifc": ifc_stage}
            from openbimagent.server.municipal_actions import build_delivery_layers

            layers = build_delivery_layers(updated_receipt, ifc_stage)
            updated_receipt["delivery_layers"] = layers
            updated = {
                **{key: value for key, value in delivery.items() if key != "ifc"},
                "receipt": updated_receipt,
                "delivery_layers": layers,
                "ifc_history": [*(delivery.get("ifc_history") or []), history_entry],
                "ifc_updated_at": _now(),
            }
            state["delivery"] = updated
            state["history"].append(history_entry)
            return self._write(sid, state)

    def ifc_download(self, sid: str) -> Path:
        state = self.get(sid) or {}
        delivery = state.get("delivery") or {}
        # First-export compatibility: the IFC stage lives inside the receipt.
        # Retry writes the same canonical location; no second copy is stored.
        ifc = (delivery.get("receipt") or {}).get("ifc") or delivery.get("ifc") or {}
        generation = ifc.get("generation") or {}
        if generation.get("status") != "generated":
            raise WorkflowError("当前没有可下载的 IFC 文件。", 404)
        path = Path(str(generation.get("ifc_path") or ""))
        if not path.is_file():
            raise WorkflowError(
                "IFC 文件缺失（可能已被移动或删除）。请重试 IFC 重新生成。", 404
            )
        recorded_sha256 = str(generation.get("ifc_sha256") or "")
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if not recorded_sha256 or actual_sha256 != recorded_sha256:
            raise WorkflowError(
                "IFC 文件内容与交付记录不一致（文件可能被修改）。请重试 IFC 重新生成。", 409
            )
        revision = ifc.get("revision")
        if revision is None:
            raise WorkflowError(
                "IFC 交付记录缺少版本身份，不能确认与当前版本一致。请重试 IFC 重新生成。", 409
            )
        try:
            recorded_revision = int(revision)
        except (TypeError, ValueError) as exc:
            raise WorkflowError("IFC 交付记录的版本身份非法。请重试 IFC 重新生成。", 409) from exc
        if recorded_revision != int(state["revision"]):
            raise WorkflowError("IFC 属于旧版本，不能用于当前版本下载。请重新导出或重试 IFC。", 409)
        candidate_sha256 = ifc.get("candidate_sha256")
        if not isinstance(candidate_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", candidate_sha256):
            raise WorkflowError(
                "IFC 交付记录缺少有效的候选版本身份。请重试 IFC 重新生成。", 409
            )
        if candidate_sha256 != state["candidate_sha256"]:
            raise WorkflowError("IFC 与当前确认版本不一致。请重新导出或重试 IFC。", 409)
        return path


def _blocking(validation: dict) -> set[str]:
    return {f"{i.get('rule_id')}|{i.get('check_name')}|{i.get('object_ids')}|{i.get('field_path')}"
            for i in validation.get("issues", []) if i.get("status") in {"fail", "unknown"} and i.get("severity") != "info"}


def add_municipal_workflow(app: FastAPI) -> None:
    from openbimagent.server.runs import _sessions_dir
    store = MunicipalWorkflowStore(_sessions_dir() / "municipal")
    app.state.municipal_workflow = store

    def call(sid: str, fn: Callable, *args: Any) -> Any:
        try:
            store._path(sid)
            if not (_sessions_dir() / f"{sid}.jsonl").is_file():
                raise WorkflowError("会话不存在，请先新建工程对话。", 404)
            return fn(sid, *args)
        except WorkflowError as exc:
            raise HTTPException(exc.status, detail=str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, detail=str(exc)) from exc

    @app.get("/api/v1/municipal/vision-config", tags=["Municipal workbench"])
    def get_vision_config():
        """Expose role/provider/model/endpoint/key-presence, never key values."""
        from openbimagent.server.municipal_actions import vision_configuration

        try:
            return vision_configuration()
        except Exception as exc:  # noqa: BLE001 — configuration diagnostics must not 500 the page
            raise HTTPException(503, detail=f"视觉配置读取失败：{exc}") from exc

    @app.get("/api/v1/municipal/{session_id}", tags=["Municipal workbench"])
    def get_task(session_id: str):
        return {"state": call(session_id, store.get)}

    @app.put("/api/v1/municipal/{session_id}", tags=["Municipal workbench"])
    def save_task(session_id: str, body: dict):
        return {"state": call(session_id, store.save, body)}

    @app.post("/api/v1/municipal/{session_id}/import-ir", tags=["Municipal workbench"])
    def import_ir(session_id: str, body: dict):
        from openbimagent.utility.candidate import compiled_to_candidate

        def import_draft(sid: str, data: dict):
            candidate = compiled_to_candidate(data.get("compiled_ir")).solver_input
            return store.save(sid, {**data, "candidate": candidate, "source": "requirement_change"})
        return {"state": call(session_id, import_draft, body)}

    @app.get("/api/v1/municipal/{session_id}/preview/{view}", tags=["Municipal workbench"])
    def preview_image(session_id: str, view: str, revision: int):
        from fastapi.responses import FileResponse

        state = call(session_id, store.get)
        preview = ((state or {}).get("visual") or {}).get("preview") or {}
        filename = preview.get("images", {}).get(view)
        if not filename or preview.get("revision") != revision:
            raise HTTPException(404, detail="当前版本没有这张预览图。")
        path = Path(filename).resolve()
        if not path.is_relative_to(store._path(session_id).parent.resolve()) or not path.is_file():
            raise HTTPException(404, detail="预览图不可用。")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/municipal/{session_id}/{action}", tags=["Municipal workbench"])
    def act(session_id: str, action: str, body: dict):
        from openbimagent.server.municipal_actions import (
            check_visual,
            export_revision,
            propose_repairs,
            retry_ifc_delivery,
        )
        actions = {"lock": (store.lock, ()), "propose": (store.propose, (propose_repairs,)),
                   "apply": (store.apply, ()), "visual": (store.visual, (check_visual,)),
                   "confirm": (store.confirm, ()), "export": (store.export, (export_revision,)),
                   "ifc-retry": (store.retry_ifc, (retry_ifc_delivery,))}
        if action not in actions:
            raise HTTPException(404, detail="未知管网操作")
        fn, extra = actions[action]
        return {"state": call(session_id, fn, body, *extra)}

    @app.get("/api/v1/municipal/{session_id}/delivery/ifc", tags=["Municipal workbench"])
    def download_ifc(session_id: str):
        from fastapi.responses import FileResponse

        path = call(session_id, store.ifc_download)
        return FileResponse(path, filename=path.name, media_type="application/x-step")
