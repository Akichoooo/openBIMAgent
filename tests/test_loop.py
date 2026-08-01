"""core.loop 单测:假 provider 跑通极简循环,断言 session 事件顺序与工具结果双视图。"""

import json
import threading

import pytest

from openbimagent.core.loop import AgentLoop
from openbimagent.core.permissions import Permission
from openbimagent.orchestrator.contracts import SubagentStatus
from openbimagent.orchestrator.runtime import ChildRunOutput, LocalSubagentRuntime
from openbimagent.session.schema import EventType
from openbimagent.session.store import SessionStore


def _resp(content: str = "", tool_calls: list | None = None) -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg, "finish_reason": "stop"}]}


def _tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


class FakeProvider:
    """预置返回的假 provider:按调用序弹出响应,记录入参。"""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, *, role, messages, tools=None, cancel_event=None):
        self.calls.append({"role": role, "messages": list(messages), "tools": tools})
        return self.responses.pop(0)


@pytest.fixture()
def workdir(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "hello.txt").write_text("hello world", encoding="utf-8")
    return work


@pytest.fixture()
def session(tmp_path):
    return SessionStore.create(tmp_path / "sessions", title="loop 测试")


def test_loop_read_then_final_answer(session, workdir) -> None:
    """一轮 tool_call(read) + 一轮终答;事件顺序:用户/助手/工具调用/工具结果/助手。"""
    provider = FakeProvider(
        [
            _resp("我先读文件", [_tool_call("read", {"path": "hello.txt"})]),
            _resp("文件内容是 hello world,已完成。"),
        ]
    )
    loop = AgentLoop(["read", "write", "edit", "bash"], session, chat_fn=provider, workdir=workdir)
    final = loop.run("读一下 hello.txt")

    assert final == "文件内容是 hello world,已完成。"
    assert len(provider.calls) == 2 and provider.calls[0]["role"] == "orchestrator"
    # 工具结果回灌:第二次调用时 messages 里已有 role=tool 的 llm_view
    tool_msgs = [m for m in provider.calls[1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 1 and "hello world" in tool_msgs[0]["content"]

    events = session.load()
    assert [e.type for e in events] == [
        EventType.MESSAGE,  # 用户消息
        EventType.MESSAGE,  # 助手消息(带 tool_calls)
        EventType.TOOL_CALL,  # 工具调用(phase=call)
        EventType.TOOL_CALL,  # 工具结果(phase=result)
        EventType.MESSAGE,  # 助手终答
    ]
    assert events[0].payload.role == "user" and events[0].payload.content == "读一下 hello.txt"
    recorded_call = events[1].payload.model_dump()["tool_calls"][0]
    assert recorded_call["toolName"] == "read"
    assert recorded_call["args_summary"] == '{"path":"str"}'
    assert "hello.txt" not in str(recorded_call)
    assert len(recorded_call["args_sha256"]) == 64
    call = events[2].payload
    assert call.phase == "call" and call.toolName == "read" and call.toolCallId == "call_1"
    assert call.args_summary == '{"path":"str"}'
    assert len(call.args_sha256) == 64
    result = events[3].payload
    assert result.phase == "result" and result.status == "ok"
    # 双视图:llm_view 给模型(含文件内容),ui_view 给 UI(结构化)
    assert "hello world" in result.result_llm_view
    assert result.result_ui_view["path"].endswith("hello.txt")
    assert result.result_ui_view["truncated"] is False
    assert events[4].payload.role == "assistant"


def test_loop_deny_short_circuits(session, workdir) -> None:
    """deny 直接拒:不执行、不审批,结果 status=denied 回灌。"""
    provider = FakeProvider(
        [
            _resp("试试删文件", [_tool_call("bash", {"command": "rm hello.txt"})]),
            _resp("被拒绝了,换个办法。"),
        ]
    )
    loop = AgentLoop(
        ["read", "bash"],
        session,
        chat_fn=provider,
        workdir=workdir,
        permission_rules={"bash:rm *": Permission.DENY},
        approval_callback=lambda name, args: pytest.fail("deny 不应触发审批"),
    )
    loop.run("删掉 hello.txt")
    assert (workdir / "hello.txt").is_file()  # 未执行
    result = session.load()[3].payload
    assert result.status == "denied" and "deny" in result.result_llm_view


def test_loop_ask_approval_gate(session, workdir) -> None:
    """ask 走 approval_callback(可注入):放行才执行。"""
    provider = FakeProvider(
        [
            _resp("写个文件", [_tool_call("write", {"path": "out.txt", "content": "由循环写入"})]),
            _resp("写完了。"),
        ]
    )
    approvals: list[str] = []
    loop = AgentLoop(
        ["read", "write"],
        session,
        chat_fn=provider,
        workdir=workdir,
        approval_callback=lambda name, args: (approvals.append(name), True)[1],
    )
    loop.run("创建 out.txt")
    assert approvals == ["write"]  # write 默认 ask
    assert (workdir / "out.txt").read_text(encoding="utf-8") == "由循环写入"
    assert session.load()[3].payload.status == "ok"


def test_loop_cancel_lands_checkpoint(session, workdir) -> None:
    """cancel_event 中断:立即返回并落 checkpoint 事件。"""
    provider = FakeProvider([_resp("不应被调用")])
    loop = AgentLoop(["read"], session, chat_fn=provider, workdir=workdir)
    cancel = threading.Event()
    cancel.set()
    final = loop.run("这会立刻被取消", cancel_event=cancel)
    assert final == "" and not provider.calls  # 未调模型
    events = session.load()
    assert [e.type for e in events] == [EventType.MESSAGE, EventType.MESSAGE]
    checkpoint = events[1].payload.model_dump()
    assert checkpoint["checkpoint"] is True and checkpoint["reason"] == "cancelled"


def test_loop_unimplemented_tool_returns_error_result(session, workdir) -> None:
    """mcp_call 等 4 工具保留 NotImplementedError;循环把未实现作为 error 结果回灌不死。"""
    provider = FakeProvider(
        [
            _resp("调 MCP", [_tool_call("mcp_call", {"server": "blender", "tool": "get_scene_info"})]),
            _resp("MCP 还没接,先到这里。"),
        ]
    )
    loop = AgentLoop(
        ["read", "mcp_call"],
        session,
        chat_fn=provider,
        workdir=workdir,
        approval_callback=lambda name, args: True,
    )
    final = loop.run("看看场景")
    assert final == "MCP 还没接,先到这里。"
    result = session.load()[3].payload
    assert result.status == "error" and "TODO(M0 阶段2+)" in result.result_llm_view


def test_loop_subagent_tool_uses_runtime_v1(session, workdir, tmp_path) -> None:
    """subagent 工具创建 child Session，父循环只收到紧凑结果信封。"""
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "worker.md").write_text(
        "---\nname: worker\nmodel: fake\ntools: [read]\npermissions: { read: allow }\n"
        "context_mode: isolated\nmax_turns: 5\nartifact_contract: summary-v1\nnesting: false\n---\nworker",
        encoding="utf-8",
    )
    runtime = LocalSubagentRuntime(
        sessions_dir=tmp_path / "sessions",
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=agents,
        child_runner=lambda request, profile, child: ChildRunOutput(summary="子任务完成", hint="完成"),
    )
    provider = FakeProvider(
        [
            _resp("派发", [_tool_call("subagent", {"role": "worker", "task": "检查文件"})]),
            _resp("已收到子任务结果。"),
        ]
    )
    loop = AgentLoop(
        ["read", "subagent"],
        session,
        chat_fn=provider,
        workdir=workdir,
        permission_rules={"subagent": Permission.ALLOW},
        subagent_runtime=runtime,
    )
    assert loop.run("派发检查") == "已收到子任务结果。"
    tool_message = next(message for message in provider.calls[1]["messages"] if message["role"] == "tool")
    assert "子任务完成" in tool_message["content"]
    result_event = [event for event in session.load() if event.type is EventType.TOOL_CALL][-1]
    assert result_event.payload.result_ui_view["status"] == SubagentStatus.COMPLETED.value
    assert result_event.payload.result_ui_view["child_session_path"]


def test_loop_subagent_background_dispatch_status_and_join(session, workdir, tmp_path) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "worker.md").write_text(
        "---\nname: worker\nmodel: fake\ntools: []\npermissions: {}\n"
        "context_mode: isolated\nmax_turns: 5\nartifact_contract: summary-v1\nnesting: false\n---\nworker",
        encoding="utf-8",
    )
    runtime = LocalSubagentRuntime(
        sessions_dir=tmp_path / "sessions",
        artifacts_dir=tmp_path / "artifacts",
        agents_dir=agents,
        child_runner=lambda *_: ChildRunOutput(summary="后台完成"),
    )
    loop = AgentLoop(
        ["subagent"],
        session,
        chat_fn=FakeProvider([]),
        workdir=workdir,
        permission_rules={"subagent": Permission.ALLOW},
        subagent_runtime=runtime,
    )
    dispatched = loop._tool_subagent(
        {"role": "worker", "task": "后台检查", "execution_mode": "background"}
    )
    request_id = dispatched["ui_view"]["request_id"]
    assert dispatched["status"] == "ok"
    assert "queued" in dispatched["llm_view"]
    status = loop._tool_subagent({"action": "status", "request_id": request_id})
    assert status["ui_view"]["status"] in {"queued", "running", "completed"}
    joined = loop._tool_subagent({"action": "join", "request_id": request_id, "timeout_s": 5})
    assert joined["status"] == "ok"
    assert "后台完成" in joined["llm_view"]
    runtime.shutdown()


def test_child_loop_cannot_mount_subagent_tool(session, workdir) -> None:
    with pytest.raises(ValueError, match="禁嵌套"):
        AgentLoop(["subagent"], session, chat_fn=FakeProvider([]), workdir=workdir, depth=1)


def test_tool_count_limit(session) -> None:
    from openbimagent.core.loop import TOOL_NAMES

    with pytest.raises(ValueError, match="超过上限"):
        AgentLoop(list(TOOL_NAMES) + ["read"], session, chat_fn=FakeProvider([]))
    with pytest.raises(ValueError, match="未知工具"):
        AgentLoop(["不存在的工具"], session, chat_fn=FakeProvider([]))
