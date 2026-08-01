"""CLI 测试(M0 阶段4;ARCH §6.5 HITL 基座)。

覆盖:
- 命令解析:无子命令 argparse 报错退出码 2;run/sessions/tree/export 子命令路由。
- run --no-blender --no-hitl --yes:走全流程(orchestrator escalate + deliver missing)→ 退出码 1。
- HITL 斜杠命令(_handle_slash):/help /exit /quit /unknown /sessions /tree /export /M1 桩。
- 审批门 _make_cli_approval:y/yes→True,n/空/任意→False,EOF/Ctrl+C→False(安全拒绝)。
- Ctrl+C 兜底:pipeline 抛 KeyboardInterrupt → _cmd_run 返回 130(pipeline 内部已捕,这里测 CLI 兜底)。

全程 mock:--no-blender 不连 Blender;input 注入避免阻塞。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openbimagent import cli
from openbimagent.cli import _handle_slash, _make_cli_approval, main
from openbimagent.session.schema import EventType
from openbimagent.session.store import SessionStore

PACKS = Path(__file__).resolve().parents[1] / "domain_packs"
SINGLE = PACKS / "single_asset_hero" / "playbook.md"


# ---------- 命令解析 ----------


def test_no_subcommand_exits_2() -> None:
    """无子命令:argparse subparser required=True → SystemExit(2)。"""
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_run_no_blender_no_hitl_returns_1(tmp_path, monkeypatch) -> None:
    """run --no-blender --no-hitl --yes:全链路(escalate + deliver missing)→ 退出码 1。

    clarify 3 个槽位全有默认值,回车接受 → completion_score=100 放行。
    blender_client=None → orchestrator 跑空批次全 ESCALATE;deliver 缺产物 → ok=False → 退出码 1。
    """
    answers = iter(["", "", ""])  # 3 槽位全回车
    monkeypatch.setattr("builtins.input", lambda p="": next(answers))
    code = main([
        "run", "--playbook", str(SINGLE),
        "--out", str(tmp_path / "out"),
        "--sessions-dir", str(tmp_path / "sessions"),
        "--no-blender", "--no-hitl", "--yes",
    ])
    assert code == 1


def test_sessions_subcommand_empty_dir(tmp_path, capsys) -> None:
    """sessions 子命令:空目录打印「无会话」返回 0。"""
    code = main(["sessions", "--sessions-dir", str(tmp_path / "empty")])
    assert code == 0
    assert "无会话" in capsys.readouterr().out


def test_sessions_subcommand_lists_entries(tmp_path, capsys) -> None:
    """sessions 子命令:有会话时按 last_active 倒序列出。"""
    sessions_dir = tmp_path / "sessions"
    SessionStore.create(sessions_dir, title="alpha", playbook=str(SINGLE))
    SessionStore.create(sessions_dir, title="beta", playbook=str(SINGLE))
    code = main(["sessions", "--sessions-dir", str(sessions_dir)])
    assert code == 0
    out = capsys.readouterr().out
    assert "alpha" in out and "beta" in out
    assert "会话列表" in out


def test_tree_subcommand_creates_branch(tmp_path, capsys) -> None:
    """tree 子命令:从指定事件分支,产新会话文件。"""
    sessions_dir = tmp_path / "sessions"
    store = SessionStore.create(sessions_dir, title="tree-test", playbook=str(SINGLE))
    event = store.append_new(EventType.MESSAGE, {"role": "user", "content": "hi"})
    code = main([
        "tree", store.session_id, event.id,
        "--sessions-dir", str(sessions_dir),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "新会话" in out


def test_tree_subcommand_unknown_event_returns_1(tmp_path, capsys) -> None:
    """tree 子命令:事件 id 不存在 → KeyError 提示,返回 1。"""
    sessions_dir = tmp_path / "sessions"
    store = SessionStore.create(sessions_dir, title="tree-err", playbook=str(SINGLE))
    code = main([
        "tree", store.session_id, "bogus-event-id",
        "--sessions-dir", str(sessions_dir),
    ])
    assert code == 1
    assert "不存在" in capsys.readouterr().out


def test_tree_subcommand_unknown_session_returns_1(tmp_path, capsys) -> None:
    """tree 子命令:会话 id 不存在 → 返回 1。"""
    code = main(["tree", "no-such-session", "any-event", "--sessions-dir", str(tmp_path / "empty")])
    assert code == 1


def test_export_subcommand(tmp_path, capsys) -> None:
    """export 子命令:导出会话 JSONL 到指定路径。"""
    sessions_dir = tmp_path / "sessions"
    store = SessionStore.create(sessions_dir, title="export-test", playbook=str(SINGLE))
    store.append_new(EventType.MESSAGE, {"role": "user", "content": "payload"})
    out_path = tmp_path / "export.jsonl"
    code = main([
        "export", store.session_id, str(out_path),
        "--sessions-dir", str(sessions_dir),
    ])
    assert code == 0
    assert out_path.is_file()
    assert "导出" in capsys.readouterr().out


def test_export_subcommand_default_path(tmp_path, capsys, monkeypatch) -> None:
    """export 子命令:未给 out_path → 默认 <session_id>.jsonl 写当前目录。"""
    sessions_dir = tmp_path / "sessions"
    store = SessionStore.create(sessions_dir, title="export-default")
    store.append_new(EventType.MESSAGE, {"role": "user", "content": "x"})
    # 切到 tmp_path 避免污染仓库
    monkeypatch.chdir(tmp_path)
    code = main(["export", store.session_id, "--sessions-dir", str(sessions_dir)])
    assert code == 0
    assert (tmp_path / f"{store.session_id}.jsonl").is_file()


def _write_control_attempt(sessions_dir: Path):
    from openbimagent.orchestrator.contracts import ExecutionMode, SubagentHandle, SubagentRequest, SubagentStatus
    from openbimagent.orchestrator.state import RuntimeStateStore

    request = SubagentRequest.create(
        parent_session_id="parent-session",
        role="worker",
        task="private task",
        execution_mode=ExecutionMode.BACKGROUND,
    )
    handle = SubagentHandle(
        request_id=request.request_id,
        agent_id="agent-1",
        parent_session_id=request.parent_session_id,
        child_session_id="child-1",
        child_session_path=str(sessions_dir / "child-1.jsonl"),
        status=SubagentStatus.QUEUED,
        lineage_id=request.lineage_id,
        attempt_number=1,
    )
    RuntimeStateStore(sessions_dir / "_runtime").write(
        request=request,
        handle=handle,
        status=SubagentStatus.QUEUED,
        phase="prepared",
    )
    return request


def test_control_attempts_json_is_read_only_projection(tmp_path, capsys) -> None:
    sessions_dir = tmp_path / "sessions"
    request = _write_control_attempt(sessions_dir)
    code = main(["control", "attempts", "--sessions-dir", str(sessions_dir), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["request_id"] == request.request_id
    assert payload[0]["status"] == "queued"
    assert "task" not in payload[0]


def test_control_attempt_requires_id_and_unknown_returns_1(tmp_path, capsys) -> None:
    sessions_dir = tmp_path / "sessions"
    assert main(["control", "attempt", "--sessions-dir", str(sessions_dir)]) == 1
    assert "需要 request_id" in capsys.readouterr().out
    assert main(["control", "attempt", "missing", "--sessions-dir", str(sessions_dir)]) == 1
    assert "未知 request_id" in capsys.readouterr().out


def test_control_attempts_invalid_status_returns_1(tmp_path, capsys) -> None:
    code = main([
        "control", "attempts", "--sessions-dir", str(tmp_path / "sessions"), "--status", "invalid",
    ])
    assert code == 1
    assert "control error" in capsys.readouterr().out


# ---------- HITL 斜杠命令(_handle_slash) ----------


def _ctx(result=None, sessions_dir: Path = Path(".")) -> dict:
    return {"result": result, "sessions_dir": Path(sessions_dir)}


def test_slash_help_prints_listing(capsys) -> None:
    """/help 打印斜杠命令列表,继续 REPL。"""
    assert _handle_slash("/help", "", _ctx()) is True
    assert "[HITL 斜杠命令]" in capsys.readouterr().out


def test_slash_exit_and_quit_in_repl_return(monkeypatch, tmp_path) -> None:
    """/exit /quit 在 REPL 层退出(_handle_slash 不处理 /exit,REPL 自己捕获并返回)。

    _handle_slash 把 /exit 当未知命令返回 True;REPL 在调用 _handle_slash 之前
    优先识别 /exit /quit 直接 return。此处测 REPL 行为:输入 /exit 后函数返回不卡死。
    """
    from openbimagent.cli import _hitl_repl

    inputs = iter(["/exit"])
    monkeypatch.setattr("builtins.input", lambda p="": next(inputs))
    _hitl_repl(result=None, sessions_dir=tmp_path)  # 不抛异常即通过

    inputs = iter(["/quit"])
    monkeypatch.setattr("builtins.input", lambda p="": next(inputs))
    _hitl_repl(result=None, sessions_dir=tmp_path)


def test_slash_unknown_command(capsys) -> None:
    """/bogus → 未知命令提示,继续 REPL。"""
    assert _handle_slash("/bogus", "", _ctx()) is True
    assert "未知命令" in capsys.readouterr().out


def test_slash_m1_stubs_print_notice(capsys) -> None:
    """/undo /redo /retry /compact /model → M1 桩提示(继续 REPL)。"""
    for cmd in ("/undo", "/redo", "/retry", "/compact", "/model"):
        assert _handle_slash(cmd, "", _ctx()) is True
    assert "M1 桩" in capsys.readouterr().out


def test_slash_sessions_lists(tmp_path, capsys) -> None:
    """/sessions 列出多会话。"""
    sessions_dir = tmp_path / "sessions"
    SessionStore.create(sessions_dir, title="repl-list")
    assert _handle_slash("/sessions", "", _ctx(sessions_dir=sessions_dir)) is True
    assert "repl-list" in capsys.readouterr().out


def test_slash_sessions_empty(tmp_path, capsys) -> None:
    """/sessions 空目录 → 打印「无会话」。"""
    assert _handle_slash("/sessions", "", _ctx(sessions_dir=tmp_path / "empty")) is True
    assert "无会话" in capsys.readouterr().out


def test_slash_tree_branches_current_session(tmp_path, capsys) -> None:
    """/tree <event_id> 从当前会话(result.session)分支。"""

    class _Result:
        def __init__(self, store):
            self.session = store

    sessions_dir = tmp_path / "sessions"
    store = SessionStore.create(sessions_dir, title="repl-tree")
    event = store.append_new(EventType.MESSAGE, {"role": "user", "content": "x"})
    assert _handle_slash("/tree", event.id, _ctx(result=_Result(store), sessions_dir=sessions_dir)) is True
    assert "新会话" in capsys.readouterr().out


def test_slash_tree_missing_arg_prints_usage(capsys) -> None:
    """/tree 无参数 → 用法提示。"""
    assert _handle_slash("/tree", "", _ctx()) is True
    assert "用法" in capsys.readouterr().out


def test_slash_tree_no_current_session(capsys) -> None:
    """/tree 但无当前会话(result.session=None)→ 打印「无当前会话」。"""
    assert _handle_slash("/tree", "some-id", _ctx(result=None)) is True
    assert "无当前会话" in capsys.readouterr().out


def test_slash_export_writes_file(tmp_path, capsys) -> None:
    """/export <out_path> 导出当前会话。"""

    class _Result:
        def __init__(self, store):
            self.session = store

    sessions_dir = tmp_path / "sessions"
    store = SessionStore.create(sessions_dir, title="repl-export")
    store.append_new(EventType.MESSAGE, {"role": "user", "content": "y"})
    out_path = tmp_path / "repl_out.jsonl"
    assert _handle_slash("/export", str(out_path),
                         _ctx(result=_Result(store), sessions_dir=sessions_dir)) is True
    assert out_path.is_file()


def test_slash_export_no_current_session(capsys) -> None:
    """/export 但无当前会话 → 打印「无当前会话」。"""
    assert _handle_slash("/export", "", _ctx(result=None)) is True
    assert "无当前会话" in capsys.readouterr().out


# ---------- 审批门 _make_cli_approval(y/N) ----------


def test_cli_approval_yes_variants(monkeypatch) -> None:
    """y/Y/yes/YES/Yes → True。"""
    approve = _make_cli_approval(yes=False)
    for ans in ("y", "Y", "yes", "YES", "Yes"):
        monkeypatch.setattr("builtins.input", lambda p="", a=ans: a)
        assert approve("execute_code", {"batch": "主体"}) is True


def test_cli_approval_no_variants(monkeypatch) -> None:
    """n/N/no/空/任意字符串 → False。"""
    approve = _make_cli_approval(yes=False)
    for ans in ("n", "N", "no", "", "bogus", "拒绝"):
        monkeypatch.setattr("builtins.input", lambda p="", a=ans: a)
        assert approve("execute_code", {"batch": "主体"}) is False


def test_cli_approval_eof_returns_false(monkeypatch) -> None:
    """EOFError(无 TTY/管道输入)→ 安全拒绝 False。"""
    approve = _make_cli_approval(yes=False)

    def raise_eof(p=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)
    assert approve("deliver", {"artifacts_dir": "/tmp"}) is False


def test_cli_approval_ctrl_c_returns_false(monkeypatch) -> None:
    """KeyboardInterrupt → 安全拒绝 False。"""
    approve = _make_cli_approval(yes=False)

    def raise_int(p=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", raise_int)
    assert approve("execute_code", {}) is False


# ---------- Ctrl+C 兜底 ----------


def test_run_ctrl_c_fallback_returns_130(tmp_path, monkeypatch) -> None:
    """Ctrl+C 兜底:pipeline 抛 KeyboardInterrupt → _cmd_run 捕获返回 130。

    pipeline 内部已捕 KeyboardInterrupt 落 checkpoint;此处 monkeypatch run_pipeline
    直接抛,验证 CLI 层的兜底 except 分支(打印中断提示 + 退出码 130)。
    """
    def boom(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_pipeline", boom)
    monkeypatch.setattr("builtins.input", lambda p="": "")  # 防意外 input 阻塞
    code = main([
        "run", "--playbook", str(SINGLE),
        "--out", str(tmp_path / "out"),
        "--no-blender", "--no-hitl", "--yes",
    ])
    assert code == 130


def test_run_ok_with_mock_blender_returns_0(tmp_path, monkeypatch) -> None:
    """run --no-hitl + mock Blender 全流程成功 → 退出码 0。

    注入 fake render_loop_fn(通过 monkeypatch pipeline.make_batch_executor)
    让 render 环 perfect_score → PASS;预放 .blend + hero.png 让 deliver 通过。
    """
    import base64

    from openbimagent.assembly import pipeline as pipeline_mod
    from openbimagent.vision.render_loop import RenderLoopResult
    from openbimagent.vision.rubric import MockCritic

    _PNG_1PX = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )

    out = tmp_path / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "scene.blend").write_bytes(b"mock-blend")
    (out / "英雄镜头渲染 x1.png").write_bytes(_PNG_1PX)  # 文件名含 deliverable 子串

    render_result = RenderLoopResult(
        converged=True, best_score=9.5, best_snapshot=Path("/tmp/fake.blend"),
        iters=1, terminate_reason="perfect_score", scores=(9.5,),
        html_report=Path("/tmp/fake.html"),
    )

    async def fake_render_fn(*args, **kwargs):
        return render_result

    orig_make = pipeline_mod.make_batch_executor

    def patched_make(**kwargs):
        kwargs["render_loop_fn"] = fake_render_fn
        kwargs["render_critic"] = MockCritic([9.5])
        kwargs["scad_critic"] = None
        return orig_make(**kwargs)

    monkeypatch.setattr(pipeline_mod, "make_batch_executor", patched_make)
    # accepted 需 session score 事件;测试隔离 deliver found 逻辑,直接返回 True
    monkeypatch.setattr(pipeline_mod, "make_acceptance_fn", lambda *a, **k: lambda: True)
    monkeypatch.setattr("builtins.input", lambda p="": "")  # clarify 全回车

    # 注入一个 fake blender client(不连真实 Blender;make_batch_executor 不直接调它的网络方法,
    # 因为 render_loop_fn 已被 fake 替换;但 client 仍需非 None 才能进 orchestrate 分支)
    from openbimagent.mcp_clients.blender import BlenderMCPClient

    fake_client = BlenderMCPClient.transport_socket(port=9887)  # 不真连
    fake_client._connected = True  # 标记已连(batch_executor 的 is_connected 守护跳过真 connect)

    # 让 _cmd_run 用我们的 fake_client 而非构造真实 client
    monkeypatch.setattr(cli, "_make_blender_client", lambda args: fake_client)
    monkeypatch.setattr(cli, "_load_registry", lambda profile: None)
    monkeypatch.setattr(cli, "_make_critics", lambda registry: (None, MockCritic([9.5])))

    code = main([
        "run", "--playbook", str(SINGLE),
        "--out", str(out),
        "--sessions-dir", str(tmp_path / "sessions"),
        "--yes", "--no-hitl",
        # 注意:不用 --no-blender,让 blender_client 非 None 进 orchestrate 分支
    ])
    assert code == 0


# ---------- usage atexit 落盘(修复 2)----------


def test_dump_usage_on_exit_writes_when_not_done(tmp_path) -> None:
    """atexit 兜底:done=False 时落盘 usage_summary.json,total.total_tokens 正确(修复 2)。

    再追加一次 chat 后以 done=True 调 → 文件内容未变(已显式落盘则跳过,不重写)。
    """
    import json

    from openbimagent.cli import _UsageTrackingRegistry, _dump_usage_on_exit

    class FakeInner:
        """最小 fake registry:chat 返回固定 usage。"""

        def chat(self, role, messages, **kwargs):
            return {
                "model_resolved": "fake-1",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }

    reg = _UsageTrackingRegistry(FakeInner())
    reg.chat("modeler", [])  # 1 次调用,usage_log 记 1 条

    # done=False:atexit 兜底应落盘
    _dump_usage_on_exit(reg, tmp_path, {"done": False})
    summary_path = tmp_path / "usage_summary.json"
    assert summary_path.exists()
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    assert data["total"]["total_tokens"] == 15
    assert data["total"]["calls"] == 1

    # 再 chat 一次(usage_log 变 2 条),但 done=True → 不重写,文件内容未变
    reg.chat("modeler", [])
    first_text = summary_path.read_text(encoding="utf-8")
    _dump_usage_on_exit(reg, tmp_path, {"done": True})
    assert summary_path.read_text(encoding="utf-8") == first_text


def test_install_sigbreak_bridge_raises_keyboard_interrupt() -> None:
    """SIGBREAK 桥(012 冒烟:Windows CREATE_NEW_PROCESS_GROUP 禁 CTRL_C,只有 CTRL_BREAK 可达)。

    装桥后 SIGBREAK 处理器应抛 KeyboardInterrupt;非 Windows 无 SIGBREAK 则跳过。
    """
    import signal

    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is None:
        pytest.skip("非 Windows 平台,无 SIGBREAK")
    original = signal.getsignal(sigbreak)
    try:
        cli._install_sigbreak_bridge()
        handler = signal.getsignal(sigbreak)
        assert callable(handler)
        with pytest.raises(KeyboardInterrupt):
            handler(sigbreak, None)
    finally:
        signal.signal(sigbreak, original)  # 还原,免得影响 pytest 自身信号处理


# ---------- HITL /tree 统一到 fork(Relay 015 任务 B2) ----------


def test_slash_tree_uses_fork_writes_forked_from(tmp_path, capsys) -> None:
    """HITL /tree 使用 fork(非 branch):新会话 index entry 含 forked_from 元数据(branch 不写)。

    验证 Relay 015 任务 B1:HITL /tree 从 branch 统一到 fork。
    fork 会写 forked_from(parent_session_id / parent_event_id)到 index.json;branch 不写。
    """
    class _Result:
        def __init__(self, store):
            self.session = store

    sessions_dir = tmp_path / "sessions"
    store = SessionStore.create(sessions_dir, title="repl-fork-test", playbook=str(SINGLE))
    event = store.append_new(EventType.MESSAGE, {"role": "user", "content": "fork-test"})

    assert _handle_slash("/tree", event.id, _ctx(result=_Result(store), sessions_dir=sessions_dir)) is True
    out = capsys.readouterr().out
    assert "新会话" in out

    # fork 写 forked_from 元数据(branch 不写);验证用了 fork
    entries = SessionStore.list_sessions(sessions_dir)
    forked_entries = [e for e in entries if e["id"] != store.session_id]
    assert len(forked_entries) == 1
    forked_entry = forked_entries[0]
    assert "forked_from" in forked_entry, "HITL /tree 应使用 fork(写 forked_from 元数据)"
    assert forked_entry["forked_from"]["parent_session_id"] == store.session_id
    assert forked_entry["forked_from"]["parent_event_id"] == event.id


def test_slash_tree_fork_unknown_event_prints_error(tmp_path, capsys) -> None:
    """HITL /tree 事件 id 不存在 → fork 抛 ValueError → 打印「事件 id 不存在」。"""
    class _Result:
        def __init__(self, store):
            self.session = store

    sessions_dir = tmp_path / "sessions"
    store = SessionStore.create(sessions_dir, title="repl-fork-err", playbook=str(SINGLE))
    assert _handle_slash("/tree", "bogus-event-id", _ctx(result=_Result(store), sessions_dir=sessions_dir)) is True
    assert "不存在" in capsys.readouterr().out
