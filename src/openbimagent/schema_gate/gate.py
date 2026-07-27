"""Schema 门禁:工件 JSON Schema 校验,漂移即 FIX。

对应文档:
- docs/architecture/ARCHITECTURE.md §0 原则 8(工件即协议)、§2 步骤 3、§8(工件格式漂移风险)
- docs/architecture/COMPONENTS.md §1 schema_gate 组件(技术:jsonschema)

加载 schemas/ 目录全部 *.schema.json(draft 2020-12);PLAN/IR 等工件过校验,
不合格抛 SchemaGateError(带字段级错误摘要,供 orchestrator 生成 FIX 返工指令)。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from jsonschema import validators

SCHEMAS_DIR = Path(__file__).resolve().parents[3] / "schemas"
"""仓库内置 schemas 目录(src/openbimagent/schema_gate/gate.py → 上溯三级为仓库根)。"""


class SchemaGateError(Exception):
    """校验失败:携带逐条漂移点(路径 + 原因),供 orchestrator 生成 FIX 返工指令。"""

    def __init__(self, artifact: str, errors: list[str]) -> None:
        self.artifact = artifact
        self.errors = errors
        detail = "\n".join(f"  - {e}" for e in errors)
        super().__init__(f"工件未通过 Schema 门禁({artifact}),共 {len(errors)} 处漂移:\n{detail}")


class SchemaGate:
    """schemas 目录的运行时视图;Draft 2020-12 校验,错误汇总为人类可读漂移清单(非遇错即停)。"""

    def __init__(self, schemas_dir: Path = SCHEMAS_DIR) -> None:
        self.schemas_dir = Path(schemas_dir)
        self._schemas: dict[str, dict[str, Any]] = {}
        for path in sorted(self.schemas_dir.glob("*.schema.json")):
            self._schemas[path.name] = json.loads(path.read_text(encoding="utf-8"))
        if not self._schemas:
            raise FileNotFoundError(f"{self.schemas_dir} 下未找到任何 *.schema.json")

    def schema_names(self) -> list[str]:
        """已加载的 schema 文件名列表。"""
        return sorted(self._schemas)

    def validate_artifact(self, name: str, data: dict[str, Any] | Path) -> list[str]:
        """校验单个工件,返回字段级错误列表(空 = 通过);name 可省略 .schema.json 后缀。"""
        schema = self._schemas.get(self._normalize_name(name))
        if schema is None:
            raise KeyError(f"未知 schema {name!r};已加载: {self.schema_names()}")
        if isinstance(data, Path):
            data = json.loads(data.read_text(encoding="utf-8"))
        validator_cls = validators.validator_for(schema)
        errors: list[str] = []
        for err in sorted(validator_cls(schema).iter_errors(data), key=lambda e: list(e.absolute_path)):
            errors.append(f"{_json_path(err)}: {err.message}")
        return errors

    def gate_or_fix(self, name: str, data: dict[str, Any] | Path) -> None:
        """门禁入口:通过直接返回;不合格抛 SchemaGateError(原地 FIX 返工,不进下一棒)。"""
        errors = self.validate_artifact(name, data)
        if errors:
            raise SchemaGateError(self._normalize_name(name), errors)

    @staticmethod
    def _normalize_name(name: str) -> str:
        return name if name.endswith(".schema.json") else f"{name}.schema.json"


def _json_path(err: Any) -> str:
    """jsonschema.ValidationError.absolute_path → $.a.b[0] 形态;根错误即 ``$``。"""
    path = "$"
    for part in err.absolute_path:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


_default_gate: SchemaGate | None = None
_default_lock = threading.Lock()


def _default() -> SchemaGate:
    global _default_gate
    with _default_lock:
        if _default_gate is None:
            _default_gate = SchemaGate()
        return _default_gate


def validate_artifact(name: str, data: dict[str, Any] | Path) -> list[str]:
    """便捷入口:用仓库 schemas/ 目录校验,返回错误列表(空 = 通过)。"""
    return _default().validate_artifact(name, data)


def gate_or_fix(name: str, data: dict[str, Any] | Path) -> None:
    """便捷入口:通过返回;不合格抛 SchemaGateError(字段级摘要供 FIX 返工)。"""
    _default().gate_or_fix(name, data)
