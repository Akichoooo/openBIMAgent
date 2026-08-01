"""按 playbook targets 组合 Blender/Vectorworks 批次执行器。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openbimagent.orchestrator.dispatch import BatchReport, Verdict

VectorworksBuilder = Callable[[list[str], dict[str, Any], str | None], str]
TargetExecutor = Callable[[str, str | None], BatchReport]


def make_vectorworks_batch_executor(
    *,
    ir: dict[str, Any],
    batch_names: list[str],
    work_dir: Path,
    client: Any,
    builder_fn: VectorworksBuilder,
    approval_fn: Callable[[str, dict[str, Any]], bool] | None = None,
    auto_approve: bool = False,
) -> TargetExecutor:
    """构造 Vectorworks 执行器；builder 负责产出受 toolset 约束的 ``vs.*`` 代码。"""
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)

    def execute(batch: str, rework: str | None) -> BatchReport:
        idx = _batch_index(batch, batch_names)
        batches = ir.get("batches") or []
        assets = list(batches[idx]) if idx < len(batches) else [batch]
        try:
            code = builder_fn(assets, ir, rework)
        except Exception as exc:
            return BatchReport(
                Verdict.FIX,
                hint=f"target=vectorworks batch={batch} builder 失败: {str(exc)[:120]}",
                rework_instruction=f"修复 Vectorworks builder 输入或生成逻辑后重跑: {str(exc)[:300]}",
            )
        if not isinstance(code, str) or not code.strip():
            return BatchReport(
                Verdict.ESCALATE,
                hint=f"target=vectorworks batch={batch} builder 未产出可执行代码",
            )

        approved = auto_approve
        if approval_fn is not None:
            approved = approval_fn(
                "execute_vs_code",
                {"batch": batch, "batch_assets": assets, "code_preview": code[:200]},
            )
            if not approved:
                return BatchReport(
                    Verdict.ESCALATE,
                    hint=f"target=vectorworks batch={batch} 用户拒绝 execute_vs_code 审批门",
                )

        try:
            result = _run_vectorworks(client, code, approved=approved)
        except Exception as exc:
            return BatchReport(
                Verdict.FIX,
                hint=f"target=vectorworks batch={batch} 执行失败: {str(exc)[:120]}",
                rework_instruction=(
                    f"修复批次 {batch} 的 Vectorworks vs.* 代码并重跑；"
                    f"服务端错误: {str(exc)[:300]}"
                ),
            )

        artifact = root / f"batch_{idx + 1:02d}_vectorworks_result.json"
        artifact.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return BatchReport(
            Verdict.PASS,
            hint=f"target=vectorworks batch={batch} ok result={artifact.name}",
        )

    return execute


def combine_target_executors(executors: dict[str, TargetExecutor]) -> TargetExecutor:
    """聚合多个 target：任一 ESCALATE 优先，其次 FIX，全部 PASS 才 PASS。

    同一批 FIX 重试时缓存已经 PASS 的 target，避免对 Blender/Vectorworks 重复执行写操作。
    """
    ordered = list(executors.items())
    if not ordered:
        raise ValueError("targets 至少需要一个执行器")
    passed: dict[str, set[str]] = {}

    def execute(batch: str, rework: str | None) -> BatchReport:
        batch_passed = passed.setdefault(batch, set())
        reports: list[tuple[str, BatchReport]] = []
        for target, fn in ordered:
            if target in batch_passed:
                reports.append((target, BatchReport(Verdict.PASS, hint="沿用上轮已通过结果")))
                continue
            report = fn(batch, rework)
            reports.append((target, report))
            if report.verdict is Verdict.PASS:
                batch_passed.add(target)
        hints = "; ".join(f"{target}={report.verdict.value}:{report.hint}" for target, report in reports)
        escalated = [(target, report) for target, report in reports if report.verdict is Verdict.ESCALATE]
        if escalated:
            return BatchReport(Verdict.ESCALATE, hint=hints)
        fixes = [(target, report) for target, report in reports if report.verdict is Verdict.FIX]
        if fixes:
            instructions = [
                f"[{target}] {report.rework_instruction or report.hint}"
                for target, report in fixes
            ]
            return BatchReport(Verdict.FIX, hint=hints, rework_instruction="；".join(instructions))
        return BatchReport(Verdict.PASS, hint=hints)

    return execute


def missing_target_executor(target: str, reason: str) -> TargetExecutor:
    def execute(batch: str, rework: str | None) -> BatchReport:
        return BatchReport(
            Verdict.ESCALATE,
            hint=f"target={target} batch={batch} 未执行: {reason}",
        )

    return execute


def _run_vectorworks(client: Any, code: str, *, approved: bool) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        if hasattr(client, "connect") and not getattr(client, "is_connected", False):
            await client.connect()
        try:
            return await client.execute_code(code, approved=approved)
        finally:
            if hasattr(client, "close"):
                await client.close()

    return asyncio.run(run())


def _batch_index(batch: str, batch_names: list[str]) -> int:
    try:
        return batch_names.index(batch)
    except ValueError:
        return 0


__all__ = [
    "TargetExecutor",
    "VectorworksBuilder",
    "combine_target_executors",
    "make_vectorworks_batch_executor",
    "missing_target_executor",
]
