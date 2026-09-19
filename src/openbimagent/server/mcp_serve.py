"""A5:把 openBIMAgent 的只读能力面暴露为 MCP server(openclaw mcp serve 双向语义)。

让任意 MCP 客户端(Claude Code / Cursor / grok 等)能直接查询本仓库的会话与
Vectorworks API 索引,无需复制代码——免费接入编码生态。

暴露的工具(全部只读、离线、无副作用):
- list_sessions:列出会话(session_id/title/playbook/事件数);
- session_summary:会话结构化摘要(无 LLM,E4 summarize_no_llm);
- lookup_vs_api:按函数名子串查询 Vectorworks vs_index 签名(懒发现);
- mcp_health:工程声明的宿主与治理白名单。

启动:`python -m openbimagent.server.mcp_serve` 或 CLI `oba mcp-serve`(stdio)。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("openbimagent-mcp")


def _sessions_dir() -> Path:
    import os

    override = os.environ.get("OPENBIMAGENT_SESSIONS_DIR")
    return Path(override) if override else Path("out") / "sessions"


@mcp.tool()
def list_sessions() -> str:
    """列出本机 openBIMAgent 会话(session_id / title / playbook / event_count)。"""
    from openbimagent.session.store import SessionStore

    entries = SessionStore.list_sessions(_sessions_dir())
    return json.dumps(
        [
            {
                "session_id": e.get("id"),
                "title": e.get("title"),
                "playbook": e.get("playbook"),
                "event_count": e.get("event_count"),
                "last_active": e.get("last_active"),
            }
            for e in entries[:50]
        ],
        ensure_ascii=False,
    )


@mcp.tool()
def session_summary(session_id: str) -> str:
    """会话结构化摘要(零 LLM 调用,E4):事件/工具计数、用户消息摘录、trivial 标记。"""
    from openbimagent.session.store import SessionStore

    path = _sessions_dir() / f"{session_id}.jsonl"
    if not path.is_file():
        return json.dumps({"status": "not_found", "session_id": session_id}, ensure_ascii=False)
    summary: dict[str, Any] = SessionStore(path).summarize_no_llm()
    return json.dumps(summary, ensure_ascii=False)


@mcp.tool()
def lookup_vs_api(query: str, limit: int = 5) -> str:
    """按函数名子串查询 Vectorworks vs_index 签名(2865 条离线索引,懒发现)。"""
    from openbimagent.mcp_clients.vectorworks import lookup_vs_signatures

    bounded = max(1, min(int(limit), 10))
    matches = lookup_vs_signatures(query, limit=bounded)
    return json.dumps({"query": query, "matches": matches}, ensure_ascii=False)


@mcp.tool()
def mcp_health() -> str:
    """宿主清单与 AgentLoop 治理白名单(不触发活体探针)。"""
    from openbimagent.core.loop import TOOL_NAMES

    return json.dumps(
        {
            "declared_hosts": ["blender", "vectorworks"],
            "loop_mcp_governance": ["execute_plan", "ping", "describe_capabilities", "lookup_api"],
            "tools": list(TOOL_NAMES),
        },
        ensure_ascii=False,
    )


def main() -> None:
    """stdio 传输启动(供 IDE/编码 agent 的 MCP client 直连)。"""
    mcp.run()


if __name__ == "__main__":
    main()
