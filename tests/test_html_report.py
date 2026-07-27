"""HTML 验收页生成器测试(ARCH §3 + §6.5 HITL 预览;COMPONENTS §2.5)。

覆盖:三视角截图 base64 内嵌、rubric 评分表(含锚点)、与上版对比区、返工指令区、
事件时间戳、<8 分标红、HTML 转义;零依赖单文件,写入 artifacts 目录并返回路径。
"""

import base64
from datetime import datetime, timezone

import pytest

from openbimagent.vision.html_report import write_html_report

_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_TS = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def shots(tmp_path):
    def make(subdir):
        d = tmp_path / subdir
        d.mkdir()
        return {view: _write_png(d, view) for view in ("iso", "front", "top")}

    return make


def _write_png(d, view):
    png = d / f"{view}.png"
    png.write_bytes(_PNG_1PX)
    return png


def test_report_contains_shots_scores_rework_and_timestamp(shots, tmp_path) -> None:
    """页面含:三视角 base64 截图、评分表(维度/锚点/判定)、对比区、返工指令区、事件时间戳。"""
    path = write_html_report(
        tmp_path / "artifacts",
        screenshots=shots("cur"),
        scores={"geometry": 9.0, "composition": 6.5},
        reasoning="CoT:iso/front/top 三视角,主体无漂浮;构图居中平庸。",
        anchor_ref="anchor:composition=5(居中平庸)",
        actionable_feedback="Object A 缩放 0.8 并沿 Z 降 0.2;<b>禁空泛</b>",
        previous_screenshots=shots("prev"),
        previous_scores={"geometry": 7.5, "composition": 6.0},
        timestamp=_TS,
    )
    assert path == tmp_path / "artifacts" / "scad_acceptance_report.html"
    assert path.is_file()
    page = path.read_text(encoding="utf-8")

    assert page.count("data:image/png;base64,") == 9  # 当批 3 + 对比区(上版 3 + 当批 3)
    assert "几何正确性" in page and "镜头构图" in page
    assert ">9.0<" in page and ">6.5<" in page and "+1.5" in page  # 得分 + 较上版 delta
    assert "严重漂浮 / 轻微重叠 / 遵循物理空间" in page  # 锚点 0/5/10
    assert "居中平庸" in page
    assert "与上版对比" in page and "上版" in page  # A/B swap 对比区
    assert "返工指令" in page and "Object A 缩放 0.8 并沿 Z 降 0.2" in page
    assert "&lt;b&gt;禁空泛&lt;/b&gt;" in page  # 文本 HTML 转义
    assert "2026-07-21T12:00:00+00:00" in page  # 事件时间戳
    assert "anchor:composition=5(居中平庸)" in page and "CoT:iso/front/top" in page  # 留痕区
    assert 'class="rework"' in page  # composition 6.5 <8 → 返工区标红(非 ok)
    assert "<style>" in page and "http" not in page.split("<style>")[0]  # 零依赖单文件,头部无外链


def test_report_marks_ok_when_all_dimensions_pass(shots, tmp_path) -> None:
    """全维 ≥8:返工区标 ok,判定列全「达标」;无上版时不渲染对比区。"""
    path = write_html_report(
        tmp_path / "artifacts",
        screenshots=shots("cur"),
        scores={"geometry": 8.0, "composition": 9.5},
        reasoning="CoT:达标。",
        anchor_ref="anchor:geometry=10(遵循物理空间)",
        actionable_feedback="无需返工",
        timestamp=_TS,
    )
    page = path.read_text(encoding="utf-8")
    assert 'class="rework ok"' in page
    assert page.count("达标") >= 2 and "与上版对比" not in page
    assert page.count("data:image/png;base64,") == 3
