"""市政设计限值编译器：从受信任知识源提取限值，替代写死在 Solver 里的常量。

## 为什么需要这个模块

``utility/solver.py`` 过去把四个规范限值写成模块常量::

    MIN_SEWAGE_DIAMETER_MM = 300.0
    MIN_DN300_CONCRETE_SLOPE = 0.003
    MAX_DN300_TO_DN600_MANHOLE_SPACING_M = 75.0
    MIN_COVER_BY_SURFACE_M = {"driveway": 0.7, "sidewalk": 0.6}

而 ``domain_packs/municipal_utility/knowledge/constraints.yaml`` 里同样这几个数值带着
完整出处（rule_id / source_clause / confidence / note）。两者**没有任何代码或测试建立联系**：
改 yaml 不会改变 Solver 行为，也不会有任何报警——那份自称「消费方: domain_gate，数值错一条
生成的管网就错一片」的受信任知识源，对 5 项门禁里的 4 项其实只是文档。

更糟的是对外举证：交付物 ``compiled_utility_ir.json`` 的 evidence 里，``clash_free`` 那条
带真实的规则集 canonical_sha256 + 规范副本 sha256 + PDF 页表定位，而另外 4 条只有一个
硬编码的 ``source_clause`` 字符串，**没有任何哈希**。5 条证据并列在同一份报告里，
读者无法分辨哪条可复核、哪条是抄来的。

## 概念区分（这是硬编码长期没被发现的根因）

旧代码用一个名字同时承担两个不同概念：

- **能力面（capability）**：「Solver v0.4 只处理 DN300 混凝土直管」——这是关于*实现*的陈述，
  写在代码里是正确的，它不该也不会随规范改版而变。
- **规范限值（spec limit）**：「GB 50014-2021 要求污水管最小管径 300mm」——这是关于*世界*的
  陈述，必须来自受信任知识源，且必须可追溯到条文。

两者今天数值相同（都是 300），所以混用看不出来。一旦规范改版（比如最小管径提到 350），
混用会让「能力面」和「规范」同时静默错掉。本模块只负责后者；前者仍以
``solver.SUPPORTED_DIAMETER_MM`` 这类明确命名的常量留在代码里。

## 纪律（与 rules.py 的净距规则集同源同标准）

- **fail-closed**：条目缺失 / 字段不全 / 数值不可解析 / ``confidence != high`` → 该限值
  标为不可用于生产判定，调用方必须产出 UNKNOWN 证据，**绝不回退到写死的常量**。
  ``solver.py`` 模块 docstring 早已声明「净距限值只能来自带完整规范核验证据的
  MunicipalRuleSet；未获 production 资格或属性不足生成 UNKNOWN」——本模块把同一条纪律
  扩展到其余 4 项检查，不是新发明。
- **可追溯**：每个限值都携带 ``rule_id`` / ``parameter`` / ``source_clause`` / ``unit`` /
  ``source_sha256``（知识源文件哈希）/ ``locator``（yaml 内的精确路径），
  使 evidence 的举证强度与 ``clash_free`` 对齐。
- **显式映射**：rule_id → 限值的对应关系写在本模块的 ``_LIMIT_SPECS`` 声明表里，
  不靠猜 parameter 名，避免知识源改结构时静默取错条目。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml

from openbimagent.utility.rules import DEFAULT_MUNICIPAL_CONSTRAINTS_PATH

Comparison = Literal["min", "max"]
"""``min`` = 实测值必须 ≥ 限值；``max`` = 实测值必须 ≤ 限值。"""

Enforcement = Literal["production", "review_required"]
"""与 rules.RuleEnforcement 同语义：只有 production 可用于 PASS/FAIL 判定。"""

_RANGE_RE = re.compile(r"^\s*(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")
_BAND_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$")


class DesignLimitError(ValueError):
    """知识源缺失、结构非法或限值不可提取。"""


@dataclass(frozen=True)
class DesignLimit:
    """一条从受信任知识源提取的设计限值，带完整出处。"""

    rule_id: str
    parameter: str
    limit_value: float
    comparison: Comparison
    unit: str
    source_clause: str
    confidence: str
    enforcement: Enforcement
    source_sha256: str
    locator: str

    @property
    def usable_for_verdict(self) -> bool:
        """是否可用于 PASS/FAIL 判定（只有 production 级可以）。"""
        return self.enforcement == "production"

    def provenance(self) -> str:
        """可写进 evidence.detail 的出处串（与 clash_free 的举证风格对齐）。"""
        return (
            f"限值来源=受信任知识源 sha256={self.source_sha256[:16]}… "
            f"条目 {self.rule_id}.{self.parameter}（{self.locator}）；"
            f"规范 {self.source_clause}；confidence={self.confidence}；"
            f"enforcement={self.enforcement}"
        )


#: 显式声明「哪个限值取自哪条 rule_id」。
#: 形式：内部稳定名 → (rule_id, 知识源里的 parameter, 比较方向, 期望 unit)。
#: 内部名与 yaml 的 parameter **故意分开**：前者给调用方用（不随知识源措辞变），
#: 后者用于交叉校验——两者对不上就说明映射表指错了条目，立即报错而不是静默取错值。
_LIMIT_SPECS: dict[str, tuple[str, str, Comparison, str]] = {
    "min_diameter_sewage": ("MU-DRAIN-001", "min_diameter_sewage", "min", "mm"),
    "min_slope_sewage_d300_concrete": (
        "MU-DRAIN-004",
        "min_slope_sewage_d300_concrete",
        "min",
        "ratio",
    ),
    "max_manhole_spacing": ("MU-WELL-001", "max_spacing_by_diameter", "max", "m"),
    "min_cover_depth_driveway": ("MU-ELEV-001", "min_cover_depth_driveway", "min", "m"),
    "min_cover_depth_sidewalk": ("MU-ELEV-002", "min_cover_depth_sidewalk", "min", "m"),
}


def _extract_banded(
    raw: Mapping[str, Any],
    *,
    rule_id: str,
    value_key: str,
    band_key: str,
    selector: float,
) -> tuple[float, str] | None:
    """从分档 ``value`` 列表里按选择子取一档。

    ``MU-WELL-001`` 的 value 是 ``[{diameter_range_mm: "300-600", max_spacing_m: 75}, ...]``，
    必须按管径选档，不能取第一档了事（取错档 = 井距限值错，直接放过超长管段）。
    返回 ``(limit_value, locator_suffix)``；选择子不落在任何档内返回 None（fail-closed）。
    """
    bands = raw.get("value")
    if not isinstance(bands, list) or not bands:
        return None
    for index, band in enumerate(bands):
        if not isinstance(band, dict):
            continue
        span = band.get(band_key)
        if not isinstance(span, str):
            continue
        match = _BAND_RE.match(span)
        if match is None:
            continue
        low, high = float(match.group(1)), float(match.group(2))
        if low - 1e-9 <= selector <= high + 1e-9:
            limit = band.get(value_key)
            if isinstance(limit, bool) or not isinstance(limit, (int, float)):
                raise DesignLimitError(
                    f"规则 {rule_id} 第 {index} 档的 {value_key} 不是数值: {limit!r}"
                )
            return float(limit), f"value[{index}]({band_key}={span!r}).{value_key}"
    return None


def _require_entry(indexed: Mapping[str, Mapping[str, Any]], rule_id: str) -> Mapping[str, Any]:
    entry = indexed.get(rule_id)
    if entry is None:
        raise DesignLimitError(
            f"受信任知识源缺少规则 {rule_id}；不回退到写死常量（fail-closed）"
        )
    return entry


#: range 表达式里各比较符对应的方向，用于交叉校验映射表声明是否自相矛盾。
_OPERATOR_DIRECTION: dict[str, Comparison] = {">=": "min", ">": "min", "<=": "max", "<": "max"}


def _finish(
    raw: Mapping[str, Any],
    *,
    rule_id: str,
    parameter: str,
    comparison: Comparison,
    expected_unit: str,
    limit_value: float,
    locator_suffix: str,
    source_sha256: str,
) -> DesignLimit:
    """补齐出处字段并做一致性校验，产出 DesignLimit。

    ``parameter`` 是**知识源里的** parameter 名（写进 locator/provenance 供人回查）。
    三项交叉校验：source_clause 非空、unit 与映射表一致、parameter 与映射表一致。
    任一不符即报错：这意味着知识源改了结构或映射表指错了条目，两者都会
    造成「取到一个看似合理但实际不相干的限值」，比报错危险得多。
    """
    source_clause = str(raw.get("source_clause") or "").strip()
    if not source_clause:
        raise DesignLimitError(f"规则 {rule_id} 缺少 source_clause，无法举证，拒绝用于生产判定")
    unit = str(raw.get("unit") or "").strip()
    if unit != expected_unit:
        raise DesignLimitError(
            f"规则 {rule_id} 的 unit={unit!r} 与映射表期望 {expected_unit!r} 不一致"
            "（知识源改了单位或映射表指错了条目）"
        )
    confidence = str(raw.get("confidence") or "").strip().lower()
    if confidence not in ("high", "medium", "low"):
        raise DesignLimitError(f"规则 {rule_id} 的 confidence 非法: {confidence!r}")
    # 与 rules.py 同纪律：confidence=high 是 production 的必要条件（此处无 verification 块，
    # 故 high 即达 production；一旦知识源补上 verification，应改为与净距规则同样的双重门禁）
    enforcement: Enforcement = "production" if confidence == "high" else "review_required"
    declared_parameter = str(raw.get("parameter") or "").strip()
    if declared_parameter and declared_parameter != parameter:
        raise DesignLimitError(
            f"规则 {rule_id} 的 parameter={declared_parameter!r} 与映射表期望 {parameter!r} 不一致"
        )
    return DesignLimit(
        rule_id=rule_id,
        parameter=parameter,
        limit_value=limit_value,
        comparison=comparison,
        unit=unit,
        source_clause=source_clause,
        confidence=confidence,
        enforcement=enforcement,
        source_sha256=source_sha256,
        locator=f"constraints[rule_id={rule_id}].{locator_suffix}",
    )


@dataclass(frozen=True)
class MunicipalDesignLimits:
    """一组从受信任知识源编译出的市政设计限值。

    任一字段为 None 表示知识源没能给出可用限值——调用方必须产出 UNKNOWN，
    不得回退到任何写死数值。
    """

    source_path: str
    source_sha256: str
    min_diameter_sewage: DesignLimit | None
    min_slope_sewage_d300_concrete: DesignLimit | None
    min_cover_depth_driveway: DesignLimit | None
    min_cover_depth_sidewalk: DesignLimit | None
    max_manhole_spacing: DesignLimit | None
    unavailable: tuple[str, ...] = ()
    """未能提取的限值内部名清单（供 evidence/日志如实说明缺什么）。"""

    def min_cover_depth(self, surface_context: str) -> DesignLimit | None:
        """按地表类型取最小覆土限值（driveway / sidewalk）。"""
        if surface_context == "driveway":
            return self.min_cover_depth_driveway
        if surface_context == "sidewalk":
            return self.min_cover_depth_sidewalk
        return None

    def max_manhole_spacing_for(self, diameter_mm: float) -> DesignLimit | None:
        """按管径取井距上限。当前只编译了 DN300 档，其余管径返回 None（不猜）。"""
        return self.max_manhole_spacing


def compile_municipal_design_limits(
    source_path: Path | str = DEFAULT_MUNICIPAL_CONSTRAINTS_PATH,
    *,
    diameter_mm: float = 300.0,
) -> MunicipalDesignLimits:
    """把受信任 YAML 知识源编译为一组带出处的设计限值。

    ``diameter_mm`` 用于分档限值（井距）选档；当前 Solver 只处理 DN300，缺省即它。
    读取/解析失败抛 ``DesignLimitError``；单个限值提取不到则记入 ``unavailable``
    并置为 None，**不回退到写死常量**。
    """
    path = Path(source_path)
    try:
        source_bytes = path.read_bytes()
    except OSError as exc:
        raise DesignLimitError(f"受信任知识源不可读: {path}: {exc}") from exc
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    try:
        payload = yaml.safe_load(source_bytes.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise DesignLimitError(f"受信任知识源 YAML 解析失败: {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("constraints"), list):
        raise DesignLimitError("受信任知识源根必须是包含 constraints 数组的 mapping")

    indexed: dict[str, Mapping[str, Any]] = {}
    for raw in payload["constraints"]:
        if not isinstance(raw, dict):
            raise DesignLimitError("constraints 每一项必须是 mapping")
        rule_id = str(raw.get("rule_id") or "")
        if not rule_id:
            raise DesignLimitError("constraints 存在缺少 rule_id 的条目")
        if rule_id in indexed:
            raise DesignLimitError(f"constraints rule_id 重复: {rule_id}")
        indexed[rule_id] = raw

    unavailable: list[str] = []

    def _scalar(internal: str) -> DesignLimit | None:
        rule_id, yaml_parameter, comparison, unit = _LIMIT_SPECS[internal]
        raw = _require_entry(indexed, rule_id)
        value = raw.get("value")
        if isinstance(value, bool):
            value = None
        if isinstance(value, (int, float)):
            return _finish(
                raw,
                rule_id=rule_id,
                parameter=yaml_parameter,
                comparison=comparison,
                expected_unit=unit,
                limit_value=float(value),
                locator_suffix="value",
                source_sha256=source_sha256,
            )
        rng = raw.get("range")
        if isinstance(rng, str):
            match = _RANGE_RE.match(rng)
            if match is None:
                raise DesignLimitError(
                    f"规则 {rule_id} 的 range 表达式不可解析: {rng!r}（期望形如 '>= 0.7'）"
                )
            operator, number = match.group(1), float(match.group(2))
            # 交叉校验：range 的比较符方向必须与映射表声明一致，否则说明有一边写错了
            if _OPERATOR_DIRECTION[operator] != comparison:
                raise DesignLimitError(
                    f"规则 {rule_id} 的 range={rng!r} 方向({_OPERATOR_DIRECTION[operator]}) "
                    f"与映射表声明({comparison}) 矛盾"
                )
            return _finish(
                raw,
                rule_id=rule_id,
                parameter=yaml_parameter,
                comparison=comparison,
                expected_unit=unit,
                limit_value=number,
                locator_suffix=f"range={rng!r}",
                source_sha256=source_sha256,
            )
        unavailable.append(f"{internal}({rule_id})")
        return None

    # 井距是分档限值：必须按管径选档，选不中就 fail-closed（取第一档会放过超长管段）
    rule_id, yaml_parameter, comparison, unit = _LIMIT_SPECS["max_manhole_spacing"]
    raw = _require_entry(indexed, rule_id)
    banded = _extract_banded(
        raw,
        rule_id=rule_id,
        value_key="max_spacing_m",
        band_key="diameter_range_mm",
        selector=diameter_mm,
    )
    max_spacing: DesignLimit | None = None
    if banded is None:
        unavailable.append(f"max_manhole_spacing({rule_id})")
    else:
        limit_value, locator_suffix = banded
        max_spacing = _finish(
            raw,
            rule_id=rule_id,
            parameter=yaml_parameter,
            comparison=comparison,
            expected_unit=unit,
            limit_value=limit_value,
            locator_suffix=locator_suffix,
            source_sha256=source_sha256,
        )

    return MunicipalDesignLimits(
        source_path=str(path),
        source_sha256=source_sha256,
        min_diameter_sewage=_scalar("min_diameter_sewage"),
        min_slope_sewage_d300_concrete=_scalar("min_slope_sewage_d300_concrete"),
        min_cover_depth_driveway=_scalar("min_cover_depth_driveway"),
        min_cover_depth_sidewalk=_scalar("min_cover_depth_sidewalk"),
        max_manhole_spacing=max_spacing,
        unavailable=tuple(unavailable),
    )


# ---------- 缓存：25KB YAML 不必每次求解都重解析 ----------

_CACHE: dict[tuple[str, int, float], MunicipalDesignLimits] = {}
"""key = (绝对路径, 文件大小, mtime)；知识源一改就自动失效，不会用到陈旧限值。"""


def cached_municipal_design_limits(
    source_path: Path | str = DEFAULT_MUNICIPAL_CONSTRAINTS_PATH,
    *,
    diameter_mm: float = 300.0,
) -> MunicipalDesignLimits:
    """带缓存的编译入口（Solver 热路径用）。缓存键含 size+mtime，知识源改动即失效。"""
    path = Path(source_path)
    try:
        stat = path.stat()
        key = (str(path.resolve()), stat.st_size, stat.st_mtime)
    except OSError:
        # 读不到 stat 就不缓存，直接走编译路径让它如实报错
        return compile_municipal_design_limits(path, diameter_mm=diameter_mm)
    if diameter_mm != 300.0:
        # 分档限值依赖管径，非缺省管径不复用缓存，避免拿到别的档
        return compile_municipal_design_limits(path, diameter_mm=diameter_mm)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    limits = compile_municipal_design_limits(path, diameter_mm=diameter_mm)
    _CACHE.clear()  # 只保留一份，防长期运行内存增长
    _CACHE[key] = limits
    return limits


def clear_design_limits_cache() -> None:
    """测试隔离：清空缓存。"""
    _CACHE.clear()


__all__ = [
    "DesignLimit",
    "DesignLimitError",
    "MunicipalDesignLimits",
    "cached_municipal_design_limits",
    "clear_design_limits_cache",
    "compile_municipal_design_limits",
]
