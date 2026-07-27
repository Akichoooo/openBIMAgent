"""HTML 验收页生成器(零依赖单文件:inline CSS + base64 截图,无 JS/无外链)。

对应文档:
- docs/architecture/ARCHITECTURE.md §3 双环视觉自检、§6.5 HITL 基座与预览双线
- docs/architecture/COMPONENTS.md §2.5 vision(评分落盘字段)

contact sheet:当批三视角截图(base64 内嵌)+ rubric 评分表(含 0/5/10 锚点)+
与上版对比区 + 返工指令区 + 事件时间戳。写入 session artifacts 目录,返回文件路径。
"""

from __future__ import annotations

import base64
import html
from datetime import datetime, timezone
from pathlib import Path

from openbimagent.vision.rubric import ANCHORS, DIMENSION_LABELS, REWORK_COMMAND_REQUIRED_BELOW, Dimension

VIEW_LABELS: dict[str, str] = {"iso": "轴测", "front": "正视", "top": "俯视"}
"""视角 key → 中文名(contact sheet 图注)。"""


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _img_data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _shots_row(screenshots: dict[str, Path]) -> str:
    cards = []
    for view, png in screenshots.items():
        label = VIEW_LABELS.get(view, view)
        cards.append(
            f'<figure><img src="{_img_data_uri(png)}" alt="{_esc(view)}"/>'
            f"<figcaption>{_esc(label)}({ _esc(view) })</figcaption></figure>"
        )
    return '<div class="shots">' + "".join(cards) + "</div>"


def _score_table(scores: dict[str, float], previous_scores: dict[str, float] | None) -> str:
    rows = []
    for key, value in scores.items():
        dim = Dimension(key)
        anchors = ANCHORS[dim]
        ok = value >= REWORK_COMMAND_REQUIRED_BELOW
        verdict = f'<span class="{"pass" if ok else "fail"}">{"达标" if ok else "返工"}</span>'
        delta_cell = ""
        if previous_scores is not None:
            prev = previous_scores.get(key)
            delta_cell = f"<td>{'—' if prev is None else f'{value - prev:+.1f}'}</td>"
        rows.append(
            f"<tr><td>{_esc(DIMENSION_LABELS[dim])}({ _esc(key) })</td>"
            f"<td>{value:.1f}</td>{delta_cell}<td>{verdict}</td>"
            f"<td>{_esc(anchors[0])} / { _esc(anchors[5]) } / { _esc(anchors[10]) }</td></tr>"
        )
    delta_head = "<th>较上版</th>" if previous_scores is not None else ""
    return (
        f"<table><thead><tr><th>维度</th><th>得分</th>{delta_head}<th>判定(≥{REWORK_COMMAND_REQUIRED_BELOW:.0f})</th>"
        "<th>锚点 0/5/10</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


_CSS = """
body{font-family:"Segoe UI","Microsoft YaHei",sans-serif;margin:24px;background:#14171c;color:#e6e9ee}
h1{font-size:20px;margin:0 0 4px} h2{font-size:15px;margin:24px 0 8px;color:#9fb4d8}
.meta{color:#8a93a3;font-size:12px;margin-bottom:16px}
.shots{display:flex;gap:12px;flex-wrap:wrap}
figure{margin:0;background:#1c2129;border:1px solid #2a313d;border-radius:8px;padding:8px}
figure img{display:block;width:240px;height:240px;object-fit:contain;background:#fff;border-radius:4px}
figcaption{text-align:center;font-size:12px;color:#8a93a3;margin-top:6px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid #2a313d;padding:6px 10px;text-align:left}
th{background:#1c2129} .pass{color:#4ade80} .fail{color:#f87171}
.compare{display:flex;gap:16px;flex-wrap:wrap}
.compare>div{flex:1;min-width:280px}
.compare h3{font-size:13px;color:#8a93a3;margin:0 0 8px}
.rework{background:#2a1d1d;border:1px solid #7f1d1d;border-radius:8px;padding:12px;
 white-space:pre-wrap;font-size:13px}
.rework.ok{background:#14261a;border-color:#166534}
.trace{color:#8a93a3;font-size:12px;white-space:pre-wrap}
""".strip()


def write_html_report(
    out_dir: Path,
    *,
    screenshots: dict[str, Path],
    scores: dict[str, float],
    reasoning: str,
    anchor_ref: str,
    actionable_feedback: str,
    previous_screenshots: dict[str, Path] | None = None,
    previous_scores: dict[str, float] | None = None,
    timestamp: datetime | None = None,
    title: str = "SCAD 环验收页",
    name: str = "scad_acceptance_report.html",
) -> Path:
    """生成零依赖单文件 HTML 验收页,写入 out_dir(session artifacts 目录),返回文件路径。

    screenshots: 当批 {view: png};previous_* 非空时渲染「与上版对比」区(A/B swap 留痕)。
    任一维 < 8 时返工指令区标红(强制 actionable_rework_command,ARCH §3 防放水第 2 条)。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = timestamp or datetime.now(timezone.utc)
    ts_iso = ts.isoformat()
    needs_rework = any(v < REWORK_COMMAND_REQUIRED_BELOW for v in scores.values())

    sections = [
        f"<h1>{_esc(title)}</h1>",
        f'<div class="meta">事件时间戳:{_esc(ts_iso)} · 阈值:任一维 ≥ {REWORK_COMMAND_REQUIRED_BELOW:.0f} 达标</div>',
        "<h2>当批三视角截图</h2>",
        _shots_row(screenshots),
        "<h2>rubric 评分</h2>",
        _score_table(scores, previous_scores),
    ]
    if previous_screenshots:
        sections += [
            "<h2>与上版对比(A/B swap)</h2>",
            '<div class="compare"><div><h3>上版</h3>'
            + _shots_row(previous_screenshots)
            + "</div><div><h3>当批</h3>"
            + _shots_row(screenshots)
            + "</div></div>",
        ]
    sections += [
        "<h2>返工指令(actionable_rework_command)</h2>",
        f'<div class="rework{"" if needs_rework else " ok"}">{_esc(actionable_feedback)}</div>',
        "<h2>评分留痕(强制 CoT + 锚点对齐)</h2>",
        f'<div class="trace">anchor_ref:{_esc(anchor_ref)}\n\nreasoning:\n{_esc(reasoning)}</div>',
    ]
    page = (
        '<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="utf-8"/>'
        f"<title>{_esc(title)}</title><style>{_CSS}</style></head><body>"
        + "".join(sections)
        + "</body></html>\n"
    )
    path = out / name
    path.write_text(page, encoding="utf-8")
    return path
