"""确定性领域门禁：仅基于显式 evidence 裁决，不把缺失数据当作通过。

Planner 的 Scene Graph IR 目前是语义 IR，不含坡度、碰撞、覆土等确定性事实。
因此 domain_gate 接收 Solver/compiled IR/外部检查器提供的 evidence：True=通过、
False=失败、缺失或 None=不可判定。不可判定必须阻断后续构建，避免伪造规范合规。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class DomainGateReport:
    status: GateStatus
    required: tuple[str, ...] = ()
    passed: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    details: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status in {GateStatus.PASS, GateStatus.SKIPPED}

    @property
    def rework_instruction(self) -> str | None:
        if self.failed:
            return "修复领域硬约束后重跑：" + "；".join(self.failed)
        if self.unknown:
            return "先由 Solver/确定性检查器补齐证据后重跑：" + "；".join(self.unknown)
        return None


def evaluate_domain_gate(
    requirements: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
) -> DomainGateReport:
    """按 acceptance.domain_gate 要求核对显式证据。

    只对值为 True 的 requirement 执行硬门禁；False 表示该规则未启用。evidence
    可直接给 bool，也可给 ``{"ok": bool|None, "detail": str}``。缺失、None
    或无法识别的 evidence 均为 UNKNOWN。
    """
    requirements = dict(requirements or {})
    required = tuple(sorted(str(key) for key, enabled in requirements.items() if enabled is True))
    if not required:
        return DomainGateReport(status=GateStatus.SKIPPED)

    evidence = dict(evidence or {})
    passed: list[str] = []
    failed: list[str] = []
    unknown: list[str] = []
    details: list[str] = []

    for rule in required:
        state, detail = _evidence_state(evidence.get(rule))
        label = f"{rule}: {detail}" if detail else rule
        if state is True:
            passed.append(rule)
        elif state is False:
            failed.append(label)
        else:
            unknown.append(label)
        if detail:
            details.append(f"{rule}: {detail}")

    if failed:
        status = GateStatus.FAIL
    elif unknown:
        status = GateStatus.UNKNOWN
    else:
        status = GateStatus.PASS
    return DomainGateReport(
        status=status,
        required=required,
        passed=tuple(passed),
        failed=tuple(failed),
        unknown=tuple(unknown),
        details=tuple(details),
    )


def _evidence_state(value: Any) -> tuple[bool | None, str]:
    if isinstance(value, bool):
        return value, ""
    if isinstance(value, dict):
        state = value.get("ok")
        detail = str(value.get("detail") or value.get("source") or "")
        return (state if isinstance(state, bool) else None), detail
    return None, ""


__all__ = ["DomainGateReport", "GateStatus", "evaluate_domain_gate"]
