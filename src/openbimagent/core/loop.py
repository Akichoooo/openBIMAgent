"""极简 Agent 循环(loop + ≤8 工具)。

对应文档:
- docs/architecture/COMPONENTS.md §2.1 loop(极简循环)
- docs/architecture/ARCHITECTURE.md §0 原则 4(极简内核,不用 LangGraph/CrewAI/AutoGen)、§6.5 HITL 基座

工具集(≤8):read / write / edit / bash / mcp_call / vision_check / subagent / deliver。
system prompt + 工具定义 < 2000 token;状态外置(session JSONL 树),中断恢复 = 重读文件 + session 树定位。
循环本身不做重试/压缩:韧性集中在 providers 层(COMPONENTS §4),上下文预算与 compaction 属 context 组件(M1)。
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal

from openbimagent.core.permissions import Permission, check_permission
from openbimagent.session.schema import EventType

if TYPE_CHECKING:
    from openbimagent.session.store import SessionStore

ToolName = Literal["read", "write", "edit", "bash", "mcp_call", "vision_check", "subagent", "deliver"]
"""循环允许挂载的 8 个工具名,超出即配置错误(COMPONENTS §2.1)。"""

TOOL_NAMES: tuple[ToolName, ...] = ("read", "write", "edit", "bash", "mcp_call", "vision_check", "subagent", "deliver")

MAX_TOOLS = 8

MAX_SYSTEM_PROMPT_TOKENS = 2000
"""system prompt + 工具定义预算上限(COMPONENTS §2.1/§5)。"""

DEFAULT_SYSTEM_PROMPT = (
    "你是 openBIMAgent 的 orchestrator:用提供的工具完成用户的建模任务。"
    "读文件用 read,写/改用 write/edit,跑命令用 bash;完成后直接用文字总结,不要再调工具。"
)

MAX_READ_CHARS = 50_000
MAX_BASH_OUTPUT_CHARS = 20_000
BASH_TIMEOUT_S = 60

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "read": {
        "type": "function",
        "function": {
            "name": "read",
            "description": "读取文本文件内容。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "文件路径(相对工作目录或绝对)"}},
                "required": ["path"],
            },
        },
    },
    "write": {
        "type": "function",
        "function": {
            "name": "write",
            "description": "写入文本文件(覆盖,自动建父目录)。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    "edit": {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "精确替换文件中的文本;多处匹配时需 replace_all=true。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string", "description": "被替换的原文"},
                    "new": {"type": "string", "description": "替换后的文本"},
                    "replace_all": {"type": "boolean", "default": False},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    "bash": {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "执行 shell 命令(有超时,输出截断)。",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    "mcp_call": {
        "type": "function",
        "function": {
            "name": "mcp_call",
            "description": "调用 MCP server 的工具(blender-mcp / vectorworks-mcp)。",
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {"type": "string"},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["server", "tool"],
            },
        },
    },
    "vision_check": {
        "type": "function",
        "function": {
            "name": "vision_check",
            "description": "双环视觉自检:对截图/渲染图评分。",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string"},
                    "phase": {"type": "string", "enum": ["scad", "blender"]},
                },
                "required": ["image_path", "phase"],
            },
        },
    },
    "subagent": {
        "type": "function",
        "function": {
            "name": "subagent",
            "description": "派发子代理(禁嵌套,并发 ≤4)。",
            "parameters": {
                "type": "object",
                "properties": {"role": {"type": "string"}, "task": {"type": "string"}},
                "required": ["role", "task"],
            },
        },
    },
    "deliver": {
        "type": "function",
        "function": {
            "name": "deliver",
            "description": "交付门禁(C5):核对交付清单。",
            "parameters": {
                "type": "object",
                "properties": {"manifest": {"type": "array", "items": {"type": "string"}}},
                "required": ["manifest"],
            },
        },
    },
}
"""8 个工具的 OpenAI tools 定义;system prompt + 工具定义合计预算 < 2000 token(COMPONENTS §2.1)。"""

ChatFn = Callable[..., dict[str, Any]]
"""模型调用入口(role=..., messages=..., tools=..., cancel_event=...)→ chat.completion 形态 dict。"""

ApprovalCallback = Callable[[str, dict[str, Any]], bool]
"""审批门回调(tool_name, args)→ 是否放行;默认 CLI input 确认。"""


def _default_chat_fn(role: str, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    """缺省模型入口:仓库 config/models.toml + 当前 profile(延迟导入,避免硬依赖)。"""
    from openbimagent.providers.registry import get_default_registry

    return get_default_registry().chat(role, messages, **kwargs)


def _cli_approval(tool_name: str, args: dict[str, Any]) -> bool:
    """默认审批门:CLI input 确认(y/yes 放行,其余拒绝)。"""
    summary = json.dumps(args, ensure_ascii=False)[:200]
    answer = input(f"工具 {tool_name} 请求审批,参数: {summary}\n放行? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


class AgentLoop:
    """极简主循环:组装消息 → 调模型 → 执行工具 → 回灌结果,直到模型不再调工具或 max_steps。

    每个事件(用户消息/助手消息/工具调用/工具结果)都写 SessionStore;工具结果双视图
    (llm_view 回灌模型,ui_view 落 session 供 UI);cancel_event 置位即中断并落 checkpoint 事件。
    """

    def __init__(
        self,
        tools: list[ToolName],
        session: SessionStore,
        *,
        chat_fn: ChatFn | None = None,
        approval_callback: ApprovalCallback | None = None,
        permission_rules: dict[str, Permission] | None = None,
        max_steps: int = 10,
        workdir: Path | None = None,
        system_prompt: str | None = None,
        role: str = "orchestrator",
    ) -> None:
        """挂载工具(≤8,超出报错)并绑定 session 树;system prompt 超 token 预算即配置错误。"""
        if len(tools) > MAX_TOOLS:
            raise ValueError(f"工具数 {len(tools)} 超过上限 {MAX_TOOLS}(COMPONENTS §2.1)")
        unknown = set(tools) - set(TOOL_NAMES)
        if unknown:
            raise ValueError(f"未知工具 {sorted(unknown)};允许: {list(TOOL_NAMES)}")
        self.tools: list[ToolName] = list(tools)
        self.session = session
        self.chat_fn = chat_fn or _default_chat_fn
        self.approval_callback = approval_callback or _cli_approval
        self.permission_rules = permission_rules or {}
        self.max_steps = max_steps
        self.workdir = Path(workdir) if workdir else Path.cwd()
        self.role = role
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        est_tokens = (len(self.system_prompt) + len(json.dumps(self._tool_schemas(), ensure_ascii=False))) // 4
        if est_tokens > MAX_SYSTEM_PROMPT_TOKENS:
            raise ValueError(f"system prompt + 工具定义约 {est_tokens} token,超过预算 {MAX_SYSTEM_PROMPT_TOKENS}")
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]

    # ---------- 主循环 ----------

    def run(self, user_input: str, *, cancel_event: threading.Event | None = None) -> str:
        """执行一轮任务,返回最终助手文本;全程事件写 session 树。

        TODO(M1): 接入 context 预算与 compaction 子代理(COMPONENTS §5)。
        """
        self.session.append_new(EventType.MESSAGE, {"role": "user", "content": user_input})
        self.messages.append({"role": "user", "content": user_input})
        content = ""
        for step in range(self.max_steps):
            if cancel_event is not None and cancel_event.is_set():
                self._checkpoint(step, "cancelled")
                return content
            resp = self.chat_fn(
                role=self.role,
                messages=self.messages,
                tools=self._tool_schemas(),
                cancel_event=cancel_event,
            )
            content, tool_calls, aborted = _normalize_response(resp)
            payload: dict[str, Any] = {"role": "assistant", "content": content}
            if resp.get("model_resolved"):
                payload["gen_ai.request.model"] = resp["model_resolved"]
            if tool_calls:
                payload["tool_calls"] = [
                    {"toolCallId": tc["id"], "toolName": tc["name"], "args": tc["arguments"]} for tc in tool_calls
                ]
            self.session.append_new(EventType.MESSAGE, payload)
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)},
                    }
                    for tc in tool_calls
                ]
            self.messages.append(assistant_msg)
            if aborted:
                self._checkpoint(step, "aborted")
                return content
            if not tool_calls:
                return content
            for tc in tool_calls:
                if cancel_event is not None and cancel_event.is_set():
                    self._checkpoint(step, "cancelled")
                    return content
                result = self._execute_tool(tc)
                self.messages.append(
                    {"role": "tool", "tool_call_id": tc["id"], "content": result["llm_view"]}
                )
        self._checkpoint(self.max_steps, "max_steps")
        return content

    # ---------- 工具执行 ----------

    def _tool_schemas(self) -> list[dict[str, Any]]:
        return [TOOL_SCHEMAS[name] for name in self.tools]

    def _execute_tool(self, tc: dict[str, Any]) -> dict[str, Any]:
        """执行一次工具调用:写 tool_call(call) 事件 → 审批门 → 执行 → 写 tool_call(result) 事件。"""
        name, args = tc["name"], tc["arguments"]
        args_summary = json.dumps(args, ensure_ascii=False)[:200]
        self.session.append_new(
            EventType.TOOL_CALL,
            {"toolCallId": tc["id"], "toolName": name, "args_summary": args_summary, "phase": "call"},
        )
        result = self._dispatch(name, args)
        self.session.append_new(
            EventType.TOOL_CALL,
            {
                "toolCallId": tc["id"],
                "toolName": name,
                "phase": "result",
                "result_llm_view": result["llm_view"],
                "result_ui_view": result["ui_view"],
                "status": result["status"],
            },
        )
        return result

    def _dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """权限审批门:deny 直接拒;ask 走 approval_callback;allow 直接执行。异常转为 error 结果回灌。"""
        perm_key = _permission_key(name, args)
        perm = check_permission(perm_key, self.permission_rules)
        if perm is Permission.DENY:
            return _tool_result("denied", f"工具 {perm_key} 被权限规则拒绝(deny)。", {"permission": "deny"})
        if perm is Permission.ASK and not self.approval_callback(name, args):
            return _tool_result("rejected", f"工具 {perm_key} 被用户拒绝。", {"permission": "rejected"})
        try:
            return self._run_tool(name, args)
        except NotImplementedError as exc:
            return _tool_result("error", f"工具 {name} 尚未实现: {exc}", {"error": str(exc)})
        except Exception as exc:
            return _tool_result("error", f"工具 {name} 执行失败: {exc}", {"error": str(exc)})

    def _run_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = {
            "read": self._tool_read,
            "write": self._tool_write,
            "edit": self._tool_edit,
            "bash": self._tool_bash,
            "mcp_call": self._tool_mcp_call,
            "vision_check": self._tool_vision_check,
            "subagent": self._tool_subagent,
            "deliver": self._tool_deliver,
        }[name]
        return handler(args)

    def _resolve(self, path: str | Path) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.workdir / p

    def _tool_read(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve(args["path"])
        text = path.read_text(encoding="utf-8", errors="replace")
        truncated = len(text) > MAX_READ_CHARS
        llm_view = text[:MAX_READ_CHARS] + ("\n...[截断]" if truncated else "")
        return _tool_result("ok", llm_view, {"path": str(path), "chars": len(text), "truncated": truncated})

    def _tool_write(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve(args["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        content = args["content"]
        path.write_text(content, encoding="utf-8")
        return _tool_result("ok", f"已写入 {path}({len(content)} 字符)。", {"path": str(path), "chars": len(content)})

    def _tool_edit(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve(args["path"])
        text = path.read_text(encoding="utf-8", errors="replace")
        count = text.count(args["old"])
        if count == 0:
            return _tool_result("error", f"在 {path} 中未找到待替换文本。", {"path": str(path), "replaced": 0})
        if count > 1 and not args.get("replace_all"):
            return _tool_result(
                "error",
                f"在 {path} 中匹配到 {count} 处,请提供更多上下文或设 replace_all=true。",
                {"path": str(path), "replaced": 0},
            )
        path.write_text(text.replace(args["old"], args["new"]), encoding="utf-8")
        return _tool_result("ok", f"已在 {path} 替换 {count} 处。", {"path": str(path), "replaced": count})

    def _tool_bash(self, args: dict[str, Any]) -> dict[str, Any]:
        command = args["command"]
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=self.workdir,
                capture_output=True,
                text=True,
                timeout=BASH_TIMEOUT_S,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return _tool_result("error", f"命令超时({BASH_TIMEOUT_S}s): {command}", {"command": command, "timeout": True})
        output = (proc.stdout or "") + (proc.stderr or "")
        truncated = len(output) > MAX_BASH_OUTPUT_CHARS
        llm_view = f"exit={proc.returncode}\n{output[:MAX_BASH_OUTPUT_CHARS]}" + ("\n...[截断]" if truncated else "")
        return _tool_result(
            "ok",
            llm_view,
            {"command": command, "exit_code": proc.returncode, "truncated": truncated},
        )

    def _tool_mcp_call(self, args: dict[str, Any]) -> dict[str, Any]:
        """TODO(M0 阶段2+):接 mcp_clients(blender-mcp / vectorworks-mcp),写操作前先 record_snapshot。"""
        raise NotImplementedError("TODO(M0 阶段2+): mcp_call 接入 MCP 客户端")

    def _tool_vision_check(self, args: dict[str, Any]) -> dict[str, Any]:
        """TODO(M0 阶段2+):接 vision 双环(SCAD 快检 / Blender 精检),评分事件落 customType=score。"""
        raise NotImplementedError("TODO(M0 阶段2+): vision_check 接入视觉双环")

    def _tool_subagent(self, args: dict[str, Any]) -> dict[str, Any]:
        """TODO(M0 阶段2+):接 orchestrator.dispatch(PASS/FIX/ESCALATE,child session)。"""
        raise NotImplementedError("TODO(M0 阶段2+): subagent 接入 orchestrator 调度")

    def _tool_deliver(self, args: dict[str, Any]) -> dict[str, Any]:
        """TODO(M0 阶段2+):接 deliver.gate 交付门禁(C5,人审签)。"""
        raise NotImplementedError("TODO(M0 阶段2+): deliver 接入交付门禁")

    # ---------- 事件辅助 ----------

    def _checkpoint(self, step: int, reason: str) -> None:
        """中断/收尾落 checkpoint(ARCH §6.5);M0 以 message 事件 + extra 字段承载(07 schema 未定此型)。"""
        self.session.append_new(
            EventType.MESSAGE,
            {
                "role": "assistant",
                "content": f"[checkpoint] step={step} reason={reason}",
                "checkpoint": True,
                "step": step,
                "reason": reason,
            },
        )


def _tool_result(status: str, llm_view: str, ui_view: dict[str, Any]) -> dict[str, Any]:
    """工具结果双视图信封(ARCH §0 原则 5):llm_view 回灌模型,ui_view 落 session 供 UI。"""
    return {"status": status, "llm_view": llm_view, "ui_view": ui_view}


def _permission_key(name: str, args: dict[str, Any]) -> str:
    """权限查找键:bash 带命令、mcp_call 带 server.tool,其余用工具名(配合 glob 规则)。"""
    if name == "bash":
        return f"bash:{args.get('command', '')}"
    if name == "mcp_call":
        return f"mcp_call:{args.get('server', '')}.{args.get('tool', '')}"
    return name


def _normalize_response(resp: dict[str, Any]) -> tuple[str, list[dict[str, Any]], bool]:
    """归一化模型返回:OpenAI choices 形态 → (content, tool_calls[{id,name,arguments:dict}], aborted)。"""
    if "choices" in resp:
        msg = resp["choices"][0].get("message") or {}
    else:
        msg = resp.get("message", resp)
    content = msg.get("content") or ""
    tool_calls: list[dict[str, Any]] = []
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        arguments = fn.get("arguments") or {}
        if isinstance(arguments, str):
            arguments = json.loads(arguments or "{}")
        tool_calls.append({"id": tc.get("id") or f"call_{i}", "name": fn.get("name"), "arguments": arguments})
    return content, tool_calls, bool(resp.get("aborted"))
