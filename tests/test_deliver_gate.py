"""deliver.gate 测试(M0 阶段4a;ARCH §0 原则 3 C5、§2 步骤 7)。

覆盖:deliverable found/missing 逐项核对(直接文件名 / 扩展名令牌 / 缺失)、
ok = 全 found 且 accepted、C5 accepted 判定(session 最后一个 score 事件
overall ≥ acceptance 阈值;显式 overall_score 优先、last-wins、无记录拒收、
判定函数参数化注入)。全程无网络。
"""

import pytest

from openbimagent.deliver.gate import (
    acceptance_threshold,
    check_deliverables,
    is_accepted,
    last_score_overall,
    make_acceptance_fn,
)
from openbimagent.session.schema import EventType
from openbimagent.session.store import SessionStore

ACCEPTANCE = {"scad_loop": {"min_score": 8.0, "max_iters": 6}, "blender_loop": {"min_score": 8.5, "max_iters": 4}}


def _score_payload(*, geometry: float = 9.0, style: float = 9.0, overall: float | None = None) -> dict:
    """最小合法 score payload(schema 必填五件 + 可选 overall_score 平铺)。"""
    payload = {
        "customType": "score",
        "rubric_scores": {"geometry": geometry, "style": style},
        "reasoning": "CoT:逐维对照锚点后打分。",
        "anchor_ref": "anchor:geometry=10(遵循物理空间)",
        "actionable_feedback": "无需返工:Object base 可再沿 Z 降 0.1",
        "critic_model": "gpt-5.5-test",
    }
    if overall is not None:
        payload["overall_score"] = overall
    return payload


def _touch(root, name: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


# ---------- deliverable 齐备核对(found/missing) ----------


def test_found_and_missing_by_direct_name(tmp_path) -> None:
    """逐项核对:直接文件名命中 found,缺失 missing;ok=False 且 notes 点名缺失项。"""
    _touch(tmp_path, "asset.blend")
    report = check_deliverables(["asset.blend", "hero.png"], tmp_path, accepted_fn=lambda: True)
    assert report.items == {"asset.blend": True, "hero.png": False}
    assert report.missing == ("hero.png",)
    assert report.accepted is True  # 判定函数注入:accepted 但缺项仍不放行
    assert report.ok is False
    assert "hero.png" in report.notes


def test_extension_token_match(tmp_path) -> None:
    """人类措辞 deliverable:首令牌为扩展名形态(「.blend 工程」)按后缀匹配。"""
    _touch(tmp_path, "scene_final.blend")
    report = check_deliverables([".blend 工程"], tmp_path, accepted_fn=lambda: True)
    assert report.items[".blend 工程"] is True
    assert report.ok is True


def test_substring_and_nested_dir_match(tmp_path) -> None:
    """子目录递归 + 归一化子串命中文件名。"""
    _touch(tmp_path, "renders/hero_night.png")
    report = check_deliverables(["hero_night"], tmp_path, accepted_fn=lambda: True)
    assert report.items["hero_night"] is True


def test_missing_artifacts_dir_all_missing(tmp_path) -> None:
    """artifacts_dir 不存在:全部 missing,不抛异常。"""
    report = check_deliverables(["asset.blend"], tmp_path / "nope", accepted_fn=lambda: True)
    assert report.items == {"asset.blend": False}
    assert report.ok is False


# ---------- C5:ok = 全 found 且 accepted ----------


def test_no_acceptance_fn_rejects_even_when_all_found(tmp_path) -> None:
    """C5 从严:无判定函数 = 无验收证据,accepted=False,产物一律拒收。"""
    _touch(tmp_path, "asset.blend")
    report = check_deliverables(["asset.blend"], tmp_path)
    assert report.items["asset.blend"] is True
    assert report.accepted is False
    assert report.ok is False
    assert "拒收" in report.notes


def test_acceptance_fn_false_rejects(tmp_path) -> None:
    _touch(tmp_path, "asset.blend")
    report = check_deliverables(["asset.blend"], tmp_path, accepted_fn=lambda: False)
    assert report.ok is False


def test_all_found_and_accepted_passes(tmp_path) -> None:
    _touch(tmp_path, "asset.blend")
    _touch(tmp_path, "hero.png")
    report = check_deliverables(["asset.blend", "hero.png"], tmp_path, accepted_fn=lambda: True)
    assert report.ok is True
    assert "放行" in report.notes


# ---------- C5 accepted 默认判定:session 最后一个 score 事件 ≥ 阈值 ----------


def test_last_score_overall_mean_of_rubric(tmp_path) -> None:
    """无显式 overall_score 时取 rubric_scores 均值。"""
    store = SessionStore.create(tmp_path / "sessions", title="deliver-test")
    store.append_new(EventType.CUSTOM, _score_payload(geometry=9.0, style=7.0))
    assert last_score_overall(store) == pytest.approx(8.0)


def test_is_accepted_last_score_wins(tmp_path) -> None:
    """只看最后一个 score 事件:先高分后低分 → 拒收(last-wins,不取历史最好)。"""
    store = SessionStore.create(tmp_path / "sessions")
    store.append_new(EventType.CUSTOM, _score_payload(geometry=9.0, style=9.0))  # 均值 9.0 ≥ 8.5
    assert is_accepted(store, ACCEPTANCE) is True
    store.append_new(EventType.CUSTOM, _score_payload(geometry=6.0, style=6.0))  # 均值 6.0 < 8.5
    assert is_accepted(store, ACCEPTANCE) is False


def test_is_accepted_explicit_overall_score(tmp_path) -> None:
    """环内落盘的显式 overall_score 优先于 rubric 均值。"""
    store = SessionStore.create(tmp_path / "sessions")
    store.append_new(EventType.CUSTOM, _score_payload(geometry=5.0, style=5.0, overall=9.2))
    assert is_accepted(store, ACCEPTANCE) is True


def test_is_accepted_no_score_event_rejects(tmp_path) -> None:
    """无 score 记录 = 未过双环验收,一律拒收(C5)。"""
    store = SessionStore.create(tmp_path / "sessions")
    assert last_score_overall(store) is None
    assert is_accepted(store, ACCEPTANCE) is False


def test_is_accepted_numeric_threshold_and_path_input(tmp_path) -> None:
    """acceptance 可直接传数值;session 可传 JSONL 路径(只读解析,不写 index)。"""
    store = SessionStore.create(tmp_path / "sessions")
    store.append_new(EventType.CUSTOM, _score_payload(geometry=8.2, style=8.2))
    assert is_accepted(store, 8.0) is True
    assert is_accepted(store.path, 8.5) is False


def test_acceptance_threshold_errors() -> None:
    """acceptance 形态非法(缺 min_score / bool)报清晰错误,不静默放行。"""
    with pytest.raises(ValueError, match="min_score"):
        acceptance_threshold({})
    with pytest.raises(ValueError, match="min_score"):
        acceptance_threshold({"blender_loop": {}})
    with pytest.raises(ValueError):
        acceptance_threshold(True)


def test_make_acceptance_fn_integration(tmp_path) -> None:
    """判定函数参数化注入:make_acceptance_fn(session, acceptance) 接入 check_deliverables。"""
    _touch(tmp_path, "asset.blend")
    store = SessionStore.create(tmp_path / "sessions")
    store.append_new(EventType.CUSTOM, _score_payload(geometry=9.0, style=9.0))
    report = check_deliverables(["asset.blend"], tmp_path, accepted_fn=make_acceptance_fn(store, ACCEPTANCE))
    assert report.accepted is True
    assert report.ok is True
