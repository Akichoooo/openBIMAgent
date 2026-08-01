"""targets 多后端执行器测试：Vectorworks 生命周期与聚合语义。"""

from __future__ import annotations

from openbimagent.assembly.target_executor import (
    combine_target_executors,
    make_vectorworks_batch_executor,
    missing_target_executor,
)
from openbimagent.orchestrator.dispatch import BatchReport, Verdict


class FakeVectorworksClient:
    def __init__(self) -> None:
        self.is_connected = False
        self.calls: list[tuple[str, bool]] = []
        self.closed = 0

    async def connect(self) -> None:
        self.is_connected = True

    async def execute_code(self, code: str, *, approved: bool = False):
        self.calls.append((code, approved))
        return {"ok": True, "stdout": "done"}

    async def close(self) -> None:
        self.is_connected = False
        self.closed += 1


def test_vectorworks_executor_runs_builder_and_writes_result(tmp_path) -> None:
    client = FakeVectorworksClient()
    executor = make_vectorworks_batch_executor(
        ir={
            "assets": [{"id": "pipe-A", "category": "pipe", "description": "污水管"}],
            "batches": [["pipe-A"]],
        },
        batch_names=["管段"],
        work_dir=tmp_path,
        client=client,
        builder_fn=lambda assets, ir, rework: "vs.Rect((0, 0), (10, 10))",
        auto_approve=True,
    )

    report = executor("管段", None)
    assert report.verdict is Verdict.PASS
    assert client.calls == [("vs.Rect((0, 0), (10, 10))", True)]
    assert client.closed == 1
    assert (tmp_path / "batch_01_vectorworks_result.json").is_file()


def test_vectorworks_builder_failure_becomes_actionable_fix(tmp_path) -> None:
    executor = make_vectorworks_batch_executor(
        ir={"batches": [["pipe-A"]]},
        batch_names=["管段"],
        work_dir=tmp_path,
        client=FakeVectorworksClient(),
        builder_fn=lambda assets, ir, rework: (_ for _ in ()).throw(ValueError("compiled IR 缺失")),
    )
    report = executor("管段", None)
    assert report.verdict is Verdict.FIX
    assert "builder" in (report.rework_instruction or "")


def test_vectorworks_executor_failure_becomes_actionable_fix(tmp_path) -> None:
    class Broken(FakeVectorworksClient):
        async def execute_code(self, code: str, *, approved: bool = False):
            raise RuntimeError("arity 校验失败")

    executor = make_vectorworks_batch_executor(
        ir={"batches": [["pipe-A"]]},
        batch_names=["管段"],
        work_dir=tmp_path,
        client=Broken(),
        builder_fn=lambda assets, ir, rework: "vs.Rect((0, 0))",
    )
    report = executor("管段", None)
    assert report.verdict is Verdict.FIX
    assert "Vectorworks" in (report.rework_instruction or "")
    assert "arity" in (report.rework_instruction or "")


def test_combine_targets_requires_all_pass() -> None:
    combined = combine_target_executors({
        "blender": lambda batch, rework: BatchReport(Verdict.PASS, hint="视觉完成"),
        "vectorworks": missing_target_executor("vectorworks", "缺客户端"),
    })
    report = combined("主体", None)
    assert report.verdict is Verdict.ESCALATE
    assert "blender=PASS" in report.hint
    assert "vectorworks=ESCALATE" in report.hint


def test_combine_targets_does_not_repeat_already_passed_side_effect() -> None:
    blender_calls = 0
    vectorworks_calls = 0

    def blender(batch, rework):
        nonlocal blender_calls
        blender_calls += 1
        return BatchReport(Verdict.PASS, hint="视觉完成")

    def vectorworks(batch, rework):
        nonlocal vectorworks_calls
        vectorworks_calls += 1
        if vectorworks_calls == 1:
            return BatchReport(Verdict.FIX, hint="语义缺失", rework_instruction="补 IFC 语义")
        return BatchReport(Verdict.PASS, hint="BIM 完成")

    combined = combine_target_executors({"blender": blender, "vectorworks": vectorworks})
    assert combined("主体", None).verdict is Verdict.FIX
    assert combined("主体", "补 IFC 语义").verdict is Verdict.PASS
    assert blender_calls == 1
    assert vectorworks_calls == 2


def test_combine_targets_aggregates_fix_instructions() -> None:
    combined = combine_target_executors({
        "blender": lambda batch, rework: BatchReport(
            Verdict.FIX,
            hint="材质失败",
            rework_instruction="补 PBR 材质",
        ),
        "vectorworks": lambda batch, rework: BatchReport(
            Verdict.FIX,
            hint="语义失败",
            rework_instruction="补 IfcPipeSegment",
        ),
    })
    report = combined("主体", None)
    assert report.verdict is Verdict.FIX
    assert "[blender] 补 PBR 材质" in (report.rework_instruction or "")
    assert "[vectorworks] 补 IfcPipeSegment" in (report.rework_instruction or "")
