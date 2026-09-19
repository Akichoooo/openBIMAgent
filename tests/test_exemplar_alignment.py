"""E5 测试:归档相似交付检索的结构化对齐升级(论文 01 需求-构件对齐方法论)。

- 词法 + 结构化(领域标签/DN/rule_id)双通道评分,词法零重合仍不入选;
- 归档 IR 缺失/损坏时降级纯词法(不抛错);
- 命中条目携带 match 元数据,注入上下文可见特征匹配项。
"""

from __future__ import annotations

import json
from pathlib import Path

from openbimagent.server.runs import _ir_features, _retrieve_exemplars, _text_features


def _make_archive(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    entries = []
    for session_id, brief, ir in [
        ("s-sewage-400", "测试:DN400 污水管新建任务", {"evidence": [{"rule_id": "MU-DRAIN-001"}], "systems": ["污水"]}),
        ("s-storm-300", "测试:DN300 雨水管新建任务", {"evidence": [{"rule_id": "MU-STORM-002"}], "systems": ["雨水"]}),
    ]:
        d = root / session_id
        d.mkdir()
        (d / "compiled_utility_ir.json").write_text(
            json.dumps({"ir_id": session_id, **ir, "notes": f"{brief} DN400 DN300 污水 雨水"}, ensure_ascii=False),
            encoding="utf-8",
        )
        entries.append({"session_id": session_id, "brief": brief, "archived_at": "2026-09-01T00:00:00+00:00", "files": []})
    (root / "index.json").write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def test_text_features_domains_and_dn() -> None:
    features = _text_features("新建 DN400 污水重力管,接入现状雨水箱涵")
    assert "domain:sewage" in features and "domain:storm" in features
    assert "dn:400" in features


def test_ir_features_rule_ids_and_domains() -> None:
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    ir = tmp / "compiled_utility_ir.json"
    ir.write_text(
        json.dumps({"evidence": [{"rule_id": "MU-DRAIN-001"}], "detail": "污水管径 DN300"}, ensure_ascii=False),
        encoding="utf-8",
    )
    features = _ir_features(ir)
    assert "rule:MU-DRAIN-001" in features
    assert "domain:sewage" in features and "dn:300" in features
    assert _ir_features(tmp / "missing.json") == set()


def test_structured_alignment_ranks_matching_domain_first(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "archive" / "municipal_utility"
    _make_archive(archive)
    monkeypatch.setenv("OPENBIMAGENT_ARCHIVE_DIR", str(tmp_path / "archive"))
    pack = Path("domain_packs/municipal_utility")
    results = _retrieve_exemplars("新建 DN400 污水管道,长约 200 米", pack)
    assert results, "词法+结构化均应有命中"
    assert results[0]["session_id"] == "s-sewage-400"
    match = results[0]["match"]
    assert match["structured"] > 0
    assert "domain:sewage" in match["features"] and "dn:400" in match["features"]
    assert all("match" in e for e in results)


def test_lexical_zero_overlap_still_excluded(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "archive" / "municipal_utility"
    _make_archive(archive)
    monkeypatch.setenv("OPENBIMAGENT_ARCHIVE_DIR", str(tmp_path / "archive"))
    results = _retrieve_exemplars("做一个赛博朋克街区英雄镜头", Path("domain_packs/municipal_utility"))
    assert results == []


def test_broken_ir_degrades_to_lexical(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "archive" / "municipal_utility"
    _make_archive(archive)
    (archive / "s-sewage-400" / "compiled_utility_ir.json").write_text("not json", encoding="utf-8")
    monkeypatch.setenv("OPENBIMAGENT_ARCHIVE_DIR", str(tmp_path / "archive"))
    results = _retrieve_exemplars("新建 DN400 污水管道", Path("domain_packs/municipal_utility"))
    assert results, "IR 损坏应降级纯词法,不抛错"
    by_id = {e["session_id"]: e for e in results}
    if "s-sewage-400" in by_id:  # 被破坏 IR 的条目结构化得分归零
        assert by_id["s-sewage-400"]["match"]["structured"] == 0.0
