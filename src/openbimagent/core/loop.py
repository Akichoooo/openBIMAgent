"""极简 Agent 循环(loop + ≤8 工具)。

对应文档:
- docs/architecture/COMPONENTS.md §2.1 loop(极简循环)
- docs/architecture/ARCHITECTURE.md §0 原则 4(极简内核,不用 LangGraph/CrewAI/AutoGen)、§6.5 HITL 基座

工具集(≤8):read / write / edit / bash / mcp_call / vision_check / subagent / deliver。
system prompt + 工具定义 < 2000 token;状态外置(session JSONL 树),中断恢复 = 重读文件 + session 树定位。
循环本身不做重试/压缩:韧性集中在 providers 层(COMPONENTS §4),上下文预算与 compaction 属 context 组件(M1)。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal

from openbimagent.core.permissions import Permission, check_permission
from openbimagent.session.schema import EventType

if TYPE_CHECKING:
    from openbimagent.orchestrator.runtime import LocalSubagentRuntime
    from openbimagent.session.store import SessionStore

ToolName = Literal["read", "write", "edit", "bash", "mcp_call", "vision_check", "subagent", "deliver"]
"""循环允许挂载的 8 个工具名,超出即配置错误(COMPONENTS §2.1)。"""

TOOL_NAMES: tuple[ToolName, ...] = ("read", "write", "edit", "bash", "mcp_call", "vision_check", "subagent", "deliver")

MAX_TOOLS = 8

MAX_SYSTEM_PROMPT_TOKENS = 2000
"""system prompt + 工具定义预算上限(COMPONENTS §2.1/§5)。"""

# ---------- 上下文预算与压缩(COMPONENTS §5;对齐 Codex auto-compaction / pi 滑窗纪律) ----------
CONTEXT_BUDGET_RATIO = 0.6
"""估算 token 超过 context_window × 此比例即触发压缩(留足生成与工具结果空间)。"""

CONTEXT_HARD_CAP_RATIO = 0.92
"""压缩后仍超此硬上限 → 从旧到新丢弃非锚点消息(保底不爆窗)。"""

COMPACT_KEEP_RECENT = 12
"""压缩时保留的最近消息数(近期工作记忆不丢;更早的进摘要)。"""

DEFAULT_CONTEXT_WINDOW = 131_072
"""models.toml 未声明 context_window 时的兜底窗口。"""

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
                    "camera_view": {"type": "string", "default": "viewport"},
                },
                "required": ["image_path", "phase"],
            },
        },
    },
    "subagent": {
        "type": "function",
        "function": {
            "name": "subagent",
            "description": "派发受控子代理(禁嵌套,并发 ≤4,过程留 child session)。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["dispatch", "status", "cancel", "join", "resume", "steer"],
                        "default": "dispatch",
                    },
                    "role": {"type": "string", "description": "dispatch 时的受信任 agents/<role>.md 角色名"},
                    "task": {"type": "string", "description": "dispatch 时的任务"},
                    "request_id": {"type": "string", "description": "status/cancel/join 的 background request_id"},
                    "timeout_s": {"type": "number", "minimum": 0, "description": "join 最长等待秒数"},
                    "instruction": {"type": "string", "description": "resume/steer 的显式新指令"},
                    "requested_by": {"type": "string", "default": "parent-agent"},
                    "idempotency_key": {"type": "string", "description": "resume 调用方提供的稳定幂等键"},
                    "context_mode": {"type": "string", "enum": ["isolated", "fork"], "default": "isolated"},
                    "execution_mode": {"type": "string", "enum": ["foreground", "background"], "default": "foreground"},
                    "artifact_contract": {"type": "string", "default": "summary-v1"},
                },
                "additionalProperties": False,
                "oneOf": [
                    {
                        "properties": {"action": {"const": "dispatch"}},
                        "required": ["role", "task"],
                    },
                    {
                        "properties": {"action": {"enum": ["status", "cancel", "join"]}},
                        "required": ["action", "request_id"],
                    },
                    {
                        "properties": {"action": {"const": "resume"}},
                        "required": ["action", "request_id", "instruction", "idempotency_key"],
                    },
                    {
                        "properties": {"action": {"const": "steer"}},
                        "required": ["action", "request_id", "instruction"],
                    },
                ],
            },
        },
    },
    "deliver": {
        "type": "function",
        "function": {
            "name": "deliver",
            "description": "交付门禁(C5):校验 Domain Gate、hash 与路径，提交统一不可变 Artifact Manifest。",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "artifacts": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "path": {"type": "string"},
                                "kind": {"type": "string"},
                                "media_type": {"type": "string"},
                                "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                                "dependencies": {"type": "array", "items": {"type": "string"}},
                                "status": {"const": "completed"},
                            },
                            "required": ["path", "kind", "media_type", "sha256"],
                        },
                    },
                    "idempotency_key": {"type": "string"},
                    "domain_gate_status": {"const": "PASS"},
                    "source_attempt_id": {"type": "string"},
                    "lineage_id": {"type": "string"},
                    "attempt_number": {"type": "integer", "minimum": 1},
                    "resumed_from_request_id": {"type": "string"},
                },
                "required": [
                    "artifacts",
                    "idempotency_key",
                    "domain_gate_status",
                    "source_attempt_id",
                ],
            },
        },
    },
}
"""8 个工具的 OpenAI tools 定义;system prompt + 工具定义合计预算 < 2000 token(COMPONENTS §2.1)。"""

ChatFn = Callable[..., dict[str, Any]]
"""模型调用入口(role=..., messages=..., tools=..., cancel_event=...)→ chat.completion 形态 dict。"""

ApprovalCallback = Callable[[str, dict[str, Any]], bool]
"""审批门回调(tool_name, args)→ 是否放行;默认 CLI input 确认。"""

ApprovalRequestCallback = Callable[[str, str, dict[str, Any], threading.Event | None], bool]
"""P1b-B 审批回调(tool_name, permission_key, args, cancel_event)→ 是否放行。"""

SteerCallback = Callable[[], tuple[str, ...]]
"""P1c 在安全轮次边界拉取当前 attempt 的 steer 指令。"""


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
        approval_request_callback: ApprovalRequestCallback | None = None,
        steer_callback: SteerCallback | None = None,
        permission_rules: dict[str, Permission] | None = None,
        max_steps: int = 10,
        workdir: Path | None = None,
        system_prompt: str | None = None,
        role: str = "orchestrator",
        subagent_runtime: LocalSubagentRuntime | None = None,
        depth: int = 0,
        mcp_clients: dict[str, Any] | None = None,
        vision_checker: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
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
        self.approval_request_callback = approval_request_callback
        self.steer_callback = steer_callback
        self.permission_rules = permission_rules or {}
        self._cancel_event: threading.Event | None = None
        self.max_steps = max_steps
        self.workdir = Path(workdir) if workdir else Path.cwd()
        self.role = role
        self.subagent_runtime = subagent_runtime
        self.mcp_clients = dict(mcp_clients or {})
        self.vision_checker = vision_checker
        # 进程内成功结果缓存只做同一 AgentLoop 的重试去重；跨重启幂等仍由宿主 receipt 协议负责。
        self._mcp_result_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.depth = depth
        if depth > 0 and "subagent" in self.tools:
            raise ValueError("child AgentLoop 不得挂载 subagent 工具(禁嵌套)")
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        est_tokens = (len(self.system_prompt) + len(json.dumps(self._tool_schemas(), ensure_ascii=False))) // 4
        if est_tokens > MAX_SYSTEM_PROMPT_TOKENS:
            raise ValueError(f"system prompt + 工具定义约 {est_tokens} token,超过预算 {MAX_SYSTEM_PROMPT_TOKENS}")
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]

    # ---------- 主循环 ----------

    # ----- 上下文预算与压缩(COMPONENTS §5) -----

    def _estimate_tokens(self, messages: list[dict[str, Any]] | None = None) -> int:
        """粗估消息 token(字符数/4 + 每条 4 开销;与挂载检查同口径,不引 tokenizer 依赖)。"""
        msgs = messages if messages is not None else self.messages
        total = 0
        for msg in msgs:
            content = msg.get("content") or ""
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            total += len(content) // 4 + 4
        return total

    def _context_window(self) -> int:
        """当前角色的 context_window(models.toml);registry 不可用/未声明时兜底。"""
        try:
            from openbimagent.providers.registry import get_default_registry

            window = get_default_registry().model_for_role(self.role).context_window
            return int(window) if window else DEFAULT_CONTEXT_WINDOW
        except Exception:  # noqa: BLE001 — 离线/无 registry 一律走兜底窗口
            return DEFAULT_CONTEXT_WINDOW

    def _maybe_compact(self) -> None:
        """超预算即压缩:保留 system + 首条任务锚点 + 最近 N 条,中段摘要化(失败回退确定性骨架)。

        纪律(与 Codex auto-compaction 对齐):压缩动作与摘要哈希写 session 树可审计;
        原文不删(session JSONL 全量留痕),仅上下文内回放被替换。
        """
        window = self._context_window()
        if self._estimate_tokens() <= int(window * CONTEXT_BUDGET_RATIO):
            return
        anchor = self.messages[:2]  # system + 首条 user(任务锚点,永不压)
        tail = self.messages[-COMPACT_KEEP_RECENT:] if len(self.messages) > COMPACT_KEEP_RECENT else []
        middle_end = len(self.messages) - len(tail) if tail else len(self.messages)
        middle = self.messages[2:middle_end]
        if not middle:
            return
        digest = self._summarize_middle(middle)
        digest_sha = hashlib.sha256(digest.encode()).hexdigest()
        marker = (
            f"[context-compaction] 已压缩 {len(middle)} 条早期消息为摘要"
            f"(digest_sha256={digest_sha[:12]}…;原文留 session 树不回放)"
        )
        self.messages = [
            *anchor,
            {"role": "assistant", "content": marker},
            {"role": "user", "content": f"[早期上下文摘要]\n{digest}"},
            *tail,
        ]
        # 硬上限保底:仍超则从旧到新丢(tail 内最旧优先)
        hard_cap = int(window * CONTEXT_HARD_CAP_RATIO)
        while self._estimate_tokens() > hard_cap and len(self.messages) > len(anchor) + 2:
            del self.messages[len(anchor) + 2]
        self.session.append_new(
            EventType.MESSAGE,
            {"role": "assistant", "content": marker, "compacted_messages": len(middle), "digest_sha256": digest_sha},
        )

    def _summarize_middle(self, middle: list[dict[str, Any]]) -> str:
        """中段消息摘要:先构造确定性骨架(必带),registry 可用则请轻量角色凝练,失败回退骨架。"""
        lines: list[str] = []
        for msg in middle:
            role = msg.get("role", "?")
            content = msg.get("content") or ""
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            head = " ".join(content.split())[:160]
            tool_names = ""
            if msg.get("tool_calls"):
                tool_names = " [tools: " + ",".join(tc.get("function", {}).get("name", "?") for tc in msg["tool_calls"]) + "]"
            lines.append(f"- {role}{tool_names}: {head}")
        skeleton = f"共 {len(middle)} 条早期消息;骨架:\n" + "\n".join(lines[:40])
        try:
            resp = self.chat_fn(
                role="clarify",
                messages=[
                    {
                        "role": "system",
                        "content": "你是上下文压缩器。把早期对话骨架凝练为不超过 400 字的决策性摘要:"
                        "保留任务目标、已做决策、关键工具结论、未完成事项;不编造、不评注。",
                    },
                    {"role": "user", "content": skeleton},
                ],
                tools=None,
                cancel_event=None,
            )
            text, _, _ = _normalize_response(resp)
            if text.strip():
                return text.strip()
        except Exception:  # noqa: BLE001 — 离线/无 key/摘要失败一律回退确定性骨架
            pass
        return skeleton[:1600]

    def run(self, user_input: str, *, cancel_event: threading.Event | None = None) -> str:
        """执行一轮任务,返回最终助手文本;全程事件写 session 树。

        每次模型调用前经 ``_maybe_compact`` 执行上下文预算检查(COMPONENTS §5)。
        """
        self.session.append_new(EventType.MESSAGE, {"role": "user", "content": user_input})
        self.messages.append({"role": "user", "content": user_input})
        self._cancel_event = cancel_event
        content = ""
        for step in range(self.max_steps):
            if cancel_event is not None and cancel_event.is_set():
                self._checkpoint(step, "cancelled")
                return content
            if self.steer_callback is not None:
                for instruction in self.steer_callback():
                    steer_message = f"[steer] {instruction}"
                    self.session.append_new(
                        EventType.MESSAGE,
                        {"role": "user", "content": steer_message, "steer": True},
                    )
                    self.messages.append({"role": "user", "content": steer_message})
            self._maybe_compact()
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
                    {
                        "toolCallId": tc["id"],
                        "toolName": tc["name"],
                        "args_summary": _summarize_tool_args(tc["arguments"]),
                        "args_sha256": _hash_tool_args(tc["arguments"]),
                    }
                    for tc in tool_calls
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
        self.session.append_new(
            EventType.TOOL_CALL,
            {
                "toolCallId": tc["id"],
                "toolName": name,
                "args_summary": _summarize_tool_args(args),
                "args_sha256": _hash_tool_args(args),
                "phase": "call",
            },
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
        """权限审批门:未挂载工具、typed 写操作和自由脚本先过白名单/ceiling，再执行。"""
        if name not in self.tools:
            return _tool_result("denied", f"工具 {name} 未挂载到当前 AgentLoop。", {"permission": "tool_not_mounted"})
        perm_key = _permission_key(name, args)
        perm = check_permission(perm_key, self.permission_rules)
        # typed execute_plan 是宿主写操作，权限 ceiling 不允许角色配置降到 allow。
        if name == "mcp_call" and args.get("tool") == "execute_plan" and perm is Permission.ALLOW:
            perm = Permission.ASK
        approval_granted = False
        if perm is Permission.DENY:
            return _tool_result("denied", f"工具 {perm_key} 被权限规则拒绝(deny)。", {"permission": "deny"})
        if perm is Permission.ASK:
            approved = (
                self.approval_request_callback(name, perm_key, args, self._cancel_event)
                if self.approval_request_callback is not None
                else self.approval_callback(name, args)
            )
            if not approved:
                return _tool_result("rejected", f"工具 {perm_key} 被用户拒绝。", {"permission": "rejected"})
            approval_granted = True
        try:
            if name == "mcp_call" and args.get("tool") == "execute_plan" and not approval_granted:
                return _tool_result("rejected", "typed execute_plan 未获得显式审批。", {"permission": "approval_required"})
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
        """通过已注入的 MCP client 执行治理入口；typed plan 是唯一建模写路径。"""
        server = str(args.get("server", ""))
        tool = str(args.get("tool", ""))
        client = self.mcp_clients.get(server)
        if client is None:
            raise RuntimeError(f"未配置 MCP server: {server}")
        if tool in {"execute_code", "execute_vs_code"}:
            raise PermissionError("AgentLoop 只允许 typed execute_plan，拒绝自由脚本执行")
        if tool == "execute_plan":
            plan = args.get("plan")
            if not isinstance(plan, dict):
                raise ValueError("typed execute_plan 必须提供 plan 对象")
            output_path = args.get("output_path")
            if not output_path:
                raise ValueError("typed execute_plan 必须提供 output_path")
            approved = bool(args.get("approved", False))
            idempotency_key = str(plan.get("idempotency_key", ""))
            canonical_sha256 = str(plan.get("canonical_sha256", ""))
            if not idempotency_key or not canonical_sha256:
                raise ValueError("typed execute_plan 必须携带 canonical_sha256 和 idempotency_key")
            cache_key = (server, idempotency_key, canonical_sha256)
            cached = self._mcp_result_cache.get(cache_key)
            if cached is not None:
                return _tool_result("ok", "复用同一 typed plan 的既有 receipt(未重复宿主副作用)。", cached)
            result = _run_async(
                client.execute_plan(plan, output_path=output_path, approved=approved)
            )
            public = _safe_public_result(result)
            self._mcp_result_cache[cache_key] = public
            return _tool_result("ok", _compact_result(public), public)
        if tool not in {"ping", "describe_capabilities"}:
            raise PermissionError(f"MCP 工具 {server}.{tool} 不在 AgentLoop 治理白名单")
        method = getattr(client, "health_check" if tool == "ping" else "describe_capabilities", None)
        if method is None:
            raise RuntimeError(f"MCP client 不支持 {tool}")
        result = _run_async(method())
        return _tool_result("ok", _compact_result(result), _safe_public_result(result))

    def _tool_vision_check(self, args: dict[str, Any]) -> dict[str, Any]:
        """调用只读视觉 critic；评分事件由 checker 写入 session，禁止返回几何修改能力。"""
        if self.vision_checker is None:
            raise RuntimeError("未配置 vision_checker")
        image_path = self._resolve(args["image_path"])
        if not image_path.is_file():
            raise FileNotFoundError(f"截图不存在: {image_path}")
        phase = str(args["phase"])
        if phase not in {"scad", "blender"}:
            raise ValueError(f"vision phase 非法: {phase}")
        result = self.vision_checker({
            "image_path": str(image_path),
            "phase": phase,
            "camera_view": str(args.get("camera_view") or "viewport"),
            "session": self.session,
        })
        if not isinstance(result, dict):
            raise TypeError("vision_checker 必须返回 dict")
        if result.get("geometry_patch") or result.get("execute_code"):
            raise PermissionError("critic 只判不改，视觉结果不得携带几何修改或执行代码")
        return _tool_result(
            str(result.get("status", "ok")),
            str(result.get("llm_view", _compact_result(result))),
            _safe_public_result(result),
        )

    def _tool_subagent(self, args: dict[str, Any]) -> dict[str, Any]:
        """派发或管理受控 child Session；模型不能指定 model/tools/permissions。"""
        from openbimagent.orchestrator.contracts import ExecutionMode, SubagentRequest
        from openbimagent.orchestrator.runtime import SubagentRuntimeError

        if self.depth > 0:
            raise SubagentRuntimeError("子代理禁嵌套：child AgentLoop 不能继续派发")
        if self.subagent_runtime is None:
            raise SubagentRuntimeError("未配置 SubagentRuntime，不能执行 subagent 工具")
        action = str(args.get("action") or "dispatch")
        if action == "status":
            handle = self.subagent_runtime.status(str(args["request_id"]))
            data = handle.model_dump(mode="json")
            return _tool_result("ok", f"{handle.request_id}: {handle.status.value}", data)
        if action == "cancel":
            accepted = self.subagent_runtime.cancel(str(args["request_id"]))
            status = self.subagent_runtime.status(str(args["request_id"]))
            data = {**status.model_dump(mode="json"), "cancel_accepted": accepted}
            return _tool_result("ok", f"cancel_accepted={accepted}; status={status.status.value}", data)
        if action == "join":
            envelope = self.subagent_runtime.join(str(args["request_id"]), timeout_s=args.get("timeout_s"))
            status = "ok" if envelope.status.value == "completed" else "error"
            return _tool_result(status, envelope.llm_summary(), envelope.ui_dict())
        if action == "resume":
            handle, receipt = self.subagent_runtime.resume(
                str(args["request_id"]),
                instruction=str(args["instruction"]),
                idempotency_key=str(args["idempotency_key"]),
                requested_by=str(args.get("requested_by") or "parent-agent"),
            )
            data = {
                "handle": handle.model_dump(mode="json"),
                "resume_receipt": receipt.model_dump(mode="json"),
            }
            return _tool_result(
                "ok",
                f"resumed as new attempt: request_id={handle.request_id}; attempt={handle.attempt_number}",
                data,
            )
        if action == "steer":
            receipt = self.subagent_runtime.steer(
                str(args["request_id"]),
                instruction=str(args["instruction"]),
                requested_by=str(args.get("requested_by") or "parent-agent"),
            )
            return _tool_result(
                "ok",
                f"steer accepted: steer_id={receipt.steer_id}",
                receipt.model_dump(mode="json"),
            )
        if action != "dispatch":
            raise SubagentRuntimeError(f"未知 subagent action: {action}")

        request = SubagentRequest.create(
            parent_session_id=self.session.session_id,
            role=str(args["role"]),
            task=str(args["task"]),
            context_mode=args.get("context_mode", "isolated"),
            execution_mode=args.get("execution_mode", "foreground"),
            artifact_contract=args.get("artifact_contract", "summary-v1"),
        )
        if request.execution_mode is ExecutionMode.BACKGROUND:
            handle = self.subagent_runtime.submit(request, parent_session=self.session)
            data = handle.model_dump(mode="json")
            return _tool_result(
                "ok",
                f"queued: request_id={handle.request_id}; agent_id={handle.agent_id}; child_session={handle.child_session_path}",
                data,
            )
        envelope = self.subagent_runtime.run(request, parent_session=self.session)
        status = "ok" if envelope.status.value == "completed" else "error"
        return _tool_result(status, envelope.llm_summary(), envelope.ui_dict())

    def _tool_deliver(self, args: dict[str, Any]) -> dict[str, Any]:
        """G2 确定性交付：统一 Artifact Manifest、路径/hash/Domain Gate 与幂等门禁。"""
        from openbimagent.deliver.manifest import commit_delivery_manifest

        result = commit_delivery_manifest(
            workdir=self.workdir,
            artifacts=list(args["artifacts"]),
            idempotency_key=str(args["idempotency_key"]),
            domain_gate_status=str(args["domain_gate_status"]),
            request_id=self.session.session_id,
            source_attempt_id=str(args["source_attempt_id"]),
            lineage_id=args.get("lineage_id"),
            attempt_number=args.get("attempt_number"),
            resumed_from_request_id=args.get("resumed_from_request_id"),
        )
        manifest = result.manifest
        return _tool_result(
            "ok",
            (
                f"delivery manifest {'reused' if result.reused else 'committed'}: "
                f"records={len(manifest.records)} path={result.manifest_path}"
            ),
            {
                "manifest_path": str(result.manifest_path),
                "manifest_version": manifest.manifest_version,
                "idempotency_key": manifest.idempotency_key,
                "semantic_sha256": manifest.semantic_sha256,
                "record_count": len(manifest.records),
                "reused": result.reused,
                "status": manifest.status.value,
            },
        )

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


def _run_async(awaitable: Any) -> Any:
    """在同步 AgentLoop 中运行 async MCP/vision handler；已有事件循环时拒绝嵌套。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise RuntimeError("AgentLoop 同步工具执行不能嵌套运行中的 asyncio event loop")


def _safe_public_result(value: Any) -> dict[str, Any]:
    """结果 UI 视图只保留可序列化公开字段，不落原始调用参数。"""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items() if k not in {"code", "arguments", "token", "secret"}}
    return {"value": str(value)}


def _compact_result(value: Any) -> str:
    """供模型回灌的紧凑结果视图；避免把长响应原样塞回上下文。"""
    public = _safe_public_result(value)
    text = json.dumps(public, ensure_ascii=False, default=str, separators=(",", ":"))
    return text[:MAX_BASH_OUTPUT_CHARS] + ("...[截断]" if len(text) > MAX_BASH_OUTPUT_CHARS else "")


def _tool_result(status: str, llm_view: str, ui_view: dict[str, Any]) -> dict[str, Any]:
    """工具结果双视图信封(ARCH §0 原则 5):llm_view 回灌模型,ui_view 落 session 供 UI。"""
    return {"status": status, "llm_view": llm_view, "ui_view": ui_view}


def _canonical_tool_args(args: dict[str, Any]) -> bytes:
    return json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _hash_tool_args(args: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_tool_args(args)).hexdigest()


def _summarize_tool_args(args: dict[str, Any]) -> str:
    """Session 只记录参数结构；原始值仅在当前进程内用于工具执行。"""
    return json.dumps(
        {str(key): type(value).__name__ for key, value in sorted(args.items())},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )[:500]


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
