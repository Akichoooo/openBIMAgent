"""compiled utility IR v1 的最小确定性编译入口。

该入口不执行路线求解，也不生成占位坐标。调用方必须提交 Solver 已求解的完整 payload；
入口依次执行 Pydantic 语义/数值门禁与仓库 JSON Schema Gate。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from openbimagent.schema_gate.gate import SchemaGate, SchemaGateError
from openbimagent.utility.contracts import CompiledUtilityIR


class UtilityCompileError(ValueError):
    """Solver 输出无法形成合法 compiled utility IR 时的失败关闭错误。"""



def compile_solved_utility_ir(
    solved_payload: Mapping[str, Any],
    *,
    schema_gate: SchemaGate | None = None,
) -> CompiledUtilityIR:
    """校验并规范化 Solver 输出；不会推断或补造任何工程事实。"""
    try:
        compiled = CompiledUtilityIR.model_validate(dict(solved_payload))
        (schema_gate or SchemaGate()).gate_or_fix("compiled_utility_ir", compiled.model_dump(mode="json"))
    except (ValidationError, SchemaGateError) as exc:
        raise UtilityCompileError(f"Solver 输出未通过 compiled utility IR v1 门禁: {exc}") from exc
    return compiled


__all__ = ["UtilityCompileError", "compile_solved_utility_ir"]
