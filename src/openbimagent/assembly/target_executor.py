"""按 playbook targets 组合 Blender/Vectorworks 批次执行器。"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openbimagent.assembly.vectorworks_plan import (
    ReceiptStatus,
    VectorworksExecutionPlan,
    VectorworksExecutionReceipt,
)
from openbimagent.orchestrator.dispatch import BatchReport, Verdict

LegacyVectorworksBuilder = Callable[[list[str], dict[str, Any], str | None], str]
VectorworksBuilder = Any
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
    """构造 Vectorworks 执行器。

    G1 主路径接收 ``CompiledUtilityIR`` 并产出 ``VectorworksExecutionPlan``，再交给
    具备 ``execute_plan`` 的 typed executor。旧的三参数 ``vs.*`` 字符串 builder 仅保留
    为兼容路径；不能由该兼容路径宣称 G1 门禁通过。
    """
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)

    def execute(batch: str, rework: str | None) -> BatchReport:
        idx = _batch_index(batch, batch_names)
        batches = ir.get("batches") or []
        assets = list(batches[idx]) if idx < len(batches) else [batch]
        try:
            built = _build_vectorworks_payload(builder_fn, assets, ir, rework)
        except Exception as exc:
            return BatchReport(
                Verdict.FIX,
                hint=f"target=vectorworks batch={batch} builder 失败: {str(exc)[:120]}",
                rework_instruction=f"修复 Vectorworks builder 输入或生成逻辑后重跑: {str(exc)[:300]}",
            )
        if isinstance(built, VectorworksExecutionPlan):
            approved = auto_approve
            if approval_fn is not None:
                approved = approval_fn(
                    "execute_vectorworks_plan",
                    {
                        "batch": batch,
                        "batch_assets": assets,
                        "plan_id": built.plan_id,
                        "canonical_sha256": built.canonical_sha256,
                        "idempotency_key": built.idempotency_key,
                        "operation_count": len(built.operations),
                    },
                )
                if not approved:
                    return BatchReport(
                        Verdict.ESCALATE,
                        hint=f"target=vectorworks batch={batch} 用户拒绝 execution plan 审批门",
                    )
            if not approved:
                return BatchReport(
                    Verdict.ESCALATE,
                    hint=f"target=vectorworks batch={batch} execution plan 未获批准",
                )
            try:
                receipt = _run_vectorworks_plan(client, built)
            except Exception as exc:
                return BatchReport(
                    Verdict.FIX,
                    hint=f"target=vectorworks batch={batch} typed plan 执行失败: {str(exc)[:120]}",
                    rework_instruction=f"依据 receipt/宿主能力修复 typed plan 后重跑: {str(exc)[:300]}",
                )
            artifact = root / f"batch_{idx + 1:02d}_vectorworks_receipt.json"
            artifact.write_text(
                json.dumps(receipt.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            verdict = Verdict.PASS if receipt.status is ReceiptStatus.COMPLETED else Verdict.FIX
            return BatchReport(
                verdict,
                hint=f"target=vectorworks batch={batch} status={receipt.status.value} receipt={artifact.name}",
                rework_instruction=(
                    None
                    if verdict is Verdict.PASS
                    else f"从部分 receipt 恢复，保留已确认对象并重试剩余操作: {receipt.errors}"
                ),
            )

        code = built
        if not isinstance(code, str) or not code.strip():
            return BatchReport(
                Verdict.ESCALATE,
                hint=f"target=vectorworks batch={batch} builder 未产出 typed plan 或可执行兼容代码",
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
                    f"修复批次 {batch} 的 Vectorworks 兼容代码并重跑；服务端错误: {str(exc)[:300]}"
                ),
            )
        artifact = root / f"batch_{idx + 1:02d}_vectorworks_result.json"
        artifact.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return BatchReport(Verdict.PASS, hint=f"target=vectorworks batch={batch} ok result={artifact.name}")

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


def _build_vectorworks_payload(
    builder_fn: VectorworksBuilder,
    assets: list[str],
    ir: dict[str, Any],
    rework: str | None,
) -> VectorworksExecutionPlan | str:
    if hasattr(builder_fn, "build"):
        # G1 typed 主链以完整 CompiledUtilityIR 为原子计划输入。Scene Graph 的批次名/资产 ID
        # 不属于该 IR 的 stable ID 空间，不能用于过滤，否则会产生空计划或语义缺失。
        return builder_fn.build(ir)
    try:
        signature = inspect.signature(builder_fn)
    except (TypeError, ValueError):
        signature = None
    if signature is not None and len(signature.parameters) <= 2:
        return builder_fn(ir, asset_ids=assets or None)
    return builder_fn(assets, ir, rework)


def _run_vectorworks_plan(client: Any, plan: VectorworksExecutionPlan) -> VectorworksExecutionReceipt:
    if not hasattr(client, "execute_plan"):
        raise RuntimeError("Vectorworks typed executor 缺少 execute_plan；不能回退自由脚本")
    capabilities = client.describe_capabilities()
    if inspect.isawaitable(capabilities) or inspect.iscoroutinefunction(client.execute_plan):
        async def run() -> VectorworksExecutionReceipt:
            effective_caps = await capabilities if inspect.isawaitable(capabilities) else capabilities
            result = client.execute_plan(plan, capabilities=effective_caps)
            if inspect.isawaitable(result):
                result = await result
            return VectorworksExecutionReceipt.model_validate(result)

        return asyncio.run(run())
    result = client.execute_plan(plan, capabilities=capabilities)
    return result if isinstance(result, VectorworksExecutionReceipt) else VectorworksExecutionReceipt.model_validate(result)


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
