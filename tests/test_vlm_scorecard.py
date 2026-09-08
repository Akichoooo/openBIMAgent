"""A4 VLM 六维评分接入测试：MockCritic 验证链路 + 离线/失败诚实降级（不伪造评分）。"""

from __future__ import annotations

from openbimagent.benchmark.vlm_scorecard import run_vlm_scorecard
from openbimagent.vision.rubric import BLENDER_DIMENSIONS, MockCritic


def _fake_image(tmp_path, name="render.png"):
    img = tmp_path / name
    img.write_bytes(b"\x89PNG\r\n\x1a\n fake render bytes")
    return img


def test_vlm_scorecard_with_mock_critic_measured(tmp_path) -> None:
    img = _fake_image(tmp_path)
    critic = MockCritic([{d.value: 8.5 for d in BLENDER_DIMENSIONS}])
    sc = run_vlm_scorecard([img], critic=critic)
    assert sc.measured is True
    assert sc.image_count == 1
    assert sc.overall_score == 8.5
    assert len(sc.dimension_scores) == len(BLENDER_DIMENSIONS)
    assert sc.provenance


def test_vlm_scorecard_no_image_degrades_honestly(tmp_path) -> None:
    # 无有效渲染图 → measured=False 占位，绝不伪造评分
    sc = run_vlm_scorecard([tmp_path / "nonexistent.png"])
    assert sc.measured is False
    assert sc.overall_score is None
    assert sc.dimension_scores == {}
    assert "禁止引用" in sc.provenance


def test_vlm_scorecard_critic_failure_degrades(tmp_path) -> None:
    img = _fake_image(tmp_path)

    class _BoomCritic:
        def critique(self, *args, **kwargs):
            raise RuntimeError("401 限流 / 无 vision key")

    sc = run_vlm_scorecard([img], critic=_BoomCritic())
    assert sc.measured is False
    assert "调用失败" in sc.provenance


def test_vlm_scorecard_markdown_rows(tmp_path) -> None:
    img = _fake_image(tmp_path)
    critic = MockCritic([{d.value: 7.0 for d in BLENDER_DIMENSIONS}])
    rows = run_vlm_scorecard([img], critic=critic).to_markdown_rows()
    assert any("VLM 六维综合" in r for r in rows)
    # 未实测占位行的 markdown
    placeholder = run_vlm_scorecard([]).to_markdown_rows()
    assert any("未实测占位" in r for r in placeholder)
