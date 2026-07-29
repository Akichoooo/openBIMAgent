"""CLI 入口:run / sessions / tree / export 子命令 + HITL 斜杠命令 + 审批门。

对应文档:
- docs/architecture/M0_PLAN.md 阶段 4(端到端 + HITL)
- docs/architecture/ARCHITECTURE.md §6.5 HITL 基座(斜杠命令集、审批门、checkpoint)

用法:
    uv run python -m openbimagent run --playbook domain_packs/single_asset_hero/playbook.md
    uv run python -m openbimagent sessions
    uv run python -m openbimagent tree <session_id> <event_id>
    uv run python -m openbimagent export <session_id> [out_path]

run 子命令:跑全流程(assembly.run_pipeline);每批打印 HTML 验收页路径;
MCP 写操作 / execute_code / deliver 前确认(y/N,--yes 跳过);Ctrl+C 落 checkpoint。
run 结束(或中断)后进入 HITL REPL,接受斜杠命令:/sessions /tree /undo /redo
/retry /compact /model /export /exit(/undo /redo /retry /compact /model 为 M1 桩)。
"""

from __future__ import annotations

import argparse
import atexit
import os
from pathlib import Path
from typing import Any

from openbimagent.assembly.batch_executor import ApprovalFn
from openbimagent.assembly.pipeline import PipelineResult, run_pipeline
from openbimagent.session.schema import EventType, SessionEvent
from openbimagent.session.store import SessionStore

DEFAULT_OUT = Path("./out")
DEFAULT_SESSIONS_DIR = DEFAULT_OUT / "sessions"


def _load_dotenv() -> None:
    """从项目根 .env 加载环境变量(不覆盖已存在的;不引入第三方依赖)。

    registry.py 依赖 os.environ 读 api_key,但 `uv run` 不自动加载 .env,
    故在 CLI 入口显式加载,确保 AGENTROUTER_API_KEY 等就位。
    解析逻辑与 tools/probe_agentrouter.py 的 load_env 一致(已验证可用)。
    """
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val


def main(argv: list[str] | None = None) -> int:
    """CLI 入口:返回退出码(0=成功,非 0=失败/中断)。"""
    _load_dotenv()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "run":
        return _cmd_run(args)
    if args.cmd == "sessions":
        return _cmd_sessions(args)
    if args.cmd == "tree":
        return _cmd_tree(args)
    if args.cmd == "export":
        return _cmd_export(args)
    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openbimagent", description="openBIMAgent CLI(M0)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="跑全流程:load playbook → clarify → plan → orchestrate → deliver")
    run_p.add_argument("--playbook", required=True, type=Path, help="playbook.md 路径")
    run_p.add_argument("--out", default=DEFAULT_OUT, type=Path, help="产物根目录(默认 ./out)")
    run_p.add_argument("--sessions-dir", default=None, type=Path, help="sessions 目录(默认 <out>/sessions)")
    run_p.add_argument("--yes", action="store_true", help="跳过所有审批门(自动化场景)")
    run_p.add_argument("--no-blender", action="store_true", help="不连 Blender(离线冒烟/测试)")
    run_p.add_argument("--no-registry", action="store_true",
                       help="不加载 LLM registry(走确定性模板 + MockCritic;Blender 仍连,用于离线取证)")
    run_p.add_argument("--blender-port", type=int, default=9876, help="Blender MCP socket 端口")
    run_p.add_argument("--blender-transport", choices=["stdio", "socket"], default="stdio",
                       help="Blender MCP 传输层(stdio 主 / socket 回退)")
    run_p.add_argument("--profile", default=None, help="providers profile(OPENBIMAGENT_PROFILE)")
    run_p.add_argument("--cameras", nargs="*", default=None, help="Blender batch_render 相机列表")
    run_p.add_argument("--turntable-target", default=None, help="Blender turntable 目标对象名")
    run_p.add_argument("--turntable-frames", type=int, default=4, help="turntable 帧数")
    run_p.add_argument("--image-size", type=int, default=512, help="渲染图尺寸")
    run_p.add_argument("--no-hitl", action="store_true", help="run 结束后不进 HITL REPL(脚本场景)")
    run_p.add_argument("--session", default=None, help="续跑已有会话 id(分支会话;/tree fork 后续跑)")

    sess_p = sub.add_parser("sessions", help="列出多会话(/sessions 斜杠命令的 CLI 形态)")
    sess_p.add_argument("--sessions-dir", default=DEFAULT_SESSIONS_DIR, type=Path)

    tree_p = sub.add_parser("tree", help="回退到某事件(分支;/tree <id> 的 CLI 形态)")
    tree_p.add_argument("session_id", help="会话 id(JSONL 文件名,无后缀)")
    tree_p.add_argument("event_id", nargs="?", default=None, help="回退到的事件 id(缺省交互式选择)")
    tree_p.add_argument("--sessions-dir", default=DEFAULT_SESSIONS_DIR, type=Path)
    tree_p.add_argument("--title", default=None, help="新会话标题")

    exp_p = sub.add_parser("export", help="导出会话 JSONL(/export 的 CLI 形态)")
    exp_p.add_argument("session_id", help="会话 id")
    exp_p.add_argument("out_path", nargs="?", default=None, type=Path, help="导出路径(默认 <id>.jsonl)")
    exp_p.add_argument("--sessions-dir", default=DEFAULT_SESSIONS_DIR, type=Path)

    return parser


# ---------- run 子命令 ----------


def _cmd_run(args: argparse.Namespace) -> int:
    """跑全流程,结束后进 HITL REPL(除非 --no-hitl)。"""
    registry_raw = _load_registry(args.profile) if (not args.no_blender and not args.no_registry) else None
    registry = _UsageTrackingRegistry(registry_raw) if registry_raw is not None else None
    blender_client = None if args.no_blender else _make_blender_client(args)
    scad_critic, render_critic = _make_critics(registry)

    approval_fn = _make_cli_approval(args.yes) if not args.yes else None

    # usage 落盘一次性封装 + atexit 兜底(M0 冒烟教训:崩溃时 usage_summary.json 丢失,token 只能反推估算)。
    # 正常结束/中断路径显式落盘并置 done;atexit 仅在未落盘时补落(session_id 不可得则为 null)。
    usage_dump_state: dict[str, bool] = {"done": False}

    def _dump_usage(session: Any) -> None:
        if registry is None:
            return
        _dump_usage_report(registry, args.out, session)
        usage_dump_state["done"] = True

    if registry is not None:
        atexit.register(_dump_usage_on_exit, registry, args.out, usage_dump_state)

    print(_fmt_phase("load_playbook", f"playbook={args.playbook}"))

    def on_phase(name: str, payload: dict[str, Any]) -> None:
        print(_fmt_phase(name, payload.get("note", "")))

    def on_html_report(html_path: Path, batch_label: str) -> None:
        print(f"[HTML 验收页] batch={batch_label} → {html_path}")

    try:
        _install_sigbreak_bridge()
        result = run_pipeline(
            playbook_path=args.playbook,
            out_dir=args.out,
            registry=registry,
            blender_client=blender_client,
            scad_critic=scad_critic,
            render_critic=render_critic,
            input_func=input,
            approval_fn=approval_fn,
            on_html_report=on_html_report,
            on_phase=on_phase,
            sessions_dir=args.sessions_dir,
            yes=args.yes,
            cameras=args.cameras,
            turntable_target=args.turntable_target,
            turntable_frames=args.turntable_frames,
            image_size=args.image_size,
            session_id=args.session,
        )
    except KeyboardInterrupt:
        # pipeline 内部已落 checkpoint;兜底
        print("\n[中断] Ctrl+C,已落 checkpoint,可用 /tree 回退续跑")
        _dump_usage(None)
        return 130

    _print_run_summary(result)

    _dump_usage(result.session)

    if not args.no_hitl and result.session is not None:
        _hitl_repl(result, args.sessions_dir or (args.out / "sessions"))

    return 0 if (result.ok and not result.interrupted) else 1


def _print_run_summary(result: PipelineResult) -> None:
    """打印 run 总结果摘要。"""
    print("\n" + "=" * 60)
    if result.interrupted:
        print("[中断] Ctrl+C,已落 checkpoint 事件到 session,可续跑")
    elif result.ok:
        print("[完成] 全流程成功(orchestrator ok + deliver ok)")
    else:
        print("[失败] 流程未完成")
    if result.error:
        print(f"  error: {result.error}")
    if result.plan_run is not None:
        escalated = list(result.plan_run.escalated)
        print(f"  batches: ok={result.plan_run.ok} escalated={escalated}")
    if result.delivery is not None:
        print(f"  deliver: ok={result.delivery.ok} accepted={result.delivery.accepted} "
              f"missing={list(result.delivery.missing)}")
    if result.session is not None:
        print(f"  session: {result.session.session_id}")
    if result.artifacts_dir is not None:
        print(f"  artifacts: {result.artifacts_dir}")
    print("=" * 60)


# ---------- HITL REPL(斜杠命令) ----------


def _hitl_repl(result: PipelineResult, sessions_dir: Path) -> None:
    """run 结束后进 HITL REPL;斜杠命令:/sessions /tree /undo /redo /retry /compact /model /export /exit。"""
    print("\n[HITL] 输入斜杠命令(/help 查看列表,/exit 退出):")
    ctx = {"result": result, "sessions_dir": Path(sessions_dir)}
    while True:
        try:
            line = input("openbimagent> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[HITL] 退出")
            return
        if not line:
            continue
        if not line.startswith("/"):
            print("[HITL] 未知输入(斜杠命令以 / 开头,/help 查看列表)")
            continue
        cmd, *rest = line.split(maxsplit=1)
        arg = rest[0].strip() if rest else ""
        if cmd in ("/exit", "/quit"):
            return
        try:
            keep = _handle_slash(cmd, arg, ctx)
        except Exception as exc:
            print(f"[HITL] 命令失败: {exc}")
            keep = True
        if not keep:
            return


def _handle_slash(cmd: str, arg: str, ctx: dict[str, Any]) -> bool:
    """处理斜杠命令;返回 True 继续 REPL,False 退出。"""
    sessions_dir = ctx["sessions_dir"]
    if cmd == "/help":
        print(_SLASH_HELP)
        return True
    if cmd == "/sessions":
        entries = SessionStore.list_sessions(sessions_dir)
        if not entries:
            print(f"(无会话,sessions_dir={sessions_dir})")
            return True
        for e in entries:
            print(f"  {e.get('id', '?')[:8]}  {e.get('title', '?')}  "
                  f"events={e.get('event_count', 0)}  last={e.get('last_active', '?')[:19]}")
        return True
    if cmd == "/tree":
        if not arg:
            print("用法:/tree <event_id>(从当前会话分支到该事件)")
            return True
        result = ctx["result"]
        if result is None or result.session is None:
            print("(无当前会话)")
            return True
        try:
            new_store = result.session.fork(arg, title=f"{result.session.session_id} 的分支")
            print(f"  新会话: {new_store.session_id}({new_store.path})")
        except ValueError as exc:
            print(f"  事件 id 不存在: {exc}")
        return True
    if cmd == "/export":
        out_path = Path(arg) if arg else None
        result = ctx["result"]
        if result is None or result.session is None:
            print("(无当前会话)")
            return True
        target = out_path or (Path(".") / f"{result.session.session_id}.jsonl")
        result.session.export_jsonl(target)
        print(f"  导出: {target}")
        return True
    if cmd in ("/undo", "/redo", "/retry", "/compact", "/model", "/fork", "/diff"):
        print(f"[M1 桩] {cmd} 暂未实现(M0 只提供 /sessions /tree /export /exit);"
              "交互式 undo/redo/retry/compact/model/fork/diff 留 M1 TUI")
        return True
    print(f"[HITL] 未知命令: {cmd}(/help 查看列表)")
    return True


_SLASH_HELP = """\
[HITL 斜杠命令]
  /sessions              列出多会话
  /tree <event_id>       从当前会话分支到该事件(回退续跑)
  /export [out_path]     导出当前会话 JSONL
  /help                  显示本帮助
  /exit | /quit          退出 HITL REPL
[M1 桩(暂未实现)]
  /undo /redo /retry /compact /model /fork /diff"""


# ---------- sessions / tree / export 子命令 ----------


def _cmd_sessions(args: argparse.Namespace) -> int:
    entries = SessionStore.list_sessions(args.sessions_dir)
    if not entries:
        print(f"(无会话,sessions_dir={args.sessions_dir})")
        return 0
    print(f"会话列表({len(entries)} 个,按 last_active 倒序):")
    for e in entries:
        print(f"  {e.get('id', '?')[:8]}  {e.get('title', '?')}  "
              f"events={e.get('event_count', 0)}  playbook={e.get('playbook', '-')}  "
              f"last={e.get('last_active', '?')[:19]}")
    return 0


def _format_event_for_tree(event: SessionEvent, index: int) -> str:
    """将事件格式化为 /tree 命令的友好展示。

    格式:`1. [message] assistant: 您想做什么资产?(abc12345)`
    - message:role + content 前 50 字符(超长加 ...)
    - tool_call:toolName + args_summary 前 30 字符
    - custom:customType
    - 事件 id 显示前 8 位(便于用户输入)
    """
    eid = event.id[:8]
    etype = event.type.value
    if event.type is EventType.MESSAGE:
        role = getattr(event.payload, "role", "?")
        content = getattr(event.payload, "content", "")
        summary = content[:50] + ("..." if len(content) > 50 else "")
        return f"{index}. [{etype}] {role}: {summary}({eid})"
    if event.type is EventType.TOOL_CALL:
        tool_name = getattr(event.payload, "toolName", "?")
        args = getattr(event.payload, "args_summary", "")
        summary = args[:30] + ("..." if len(args) > 30 else "")
        return f"{index}. [{etype}] {tool_name}: {summary}({eid})"
    if event.type is EventType.CUSTOM:
        custom_type = getattr(event.payload, "customType", "?")
        return f"{index}. [{etype}] {custom_type}({eid})"
    return f"{index}. [{etype}]({eid})"


def _resolve_tree_choice(chain: list[SessionEvent], choice: str) -> str | None:
    """解析 /tree 交互选择:序号(1-based)或事件 id 前 8 位前缀。

    返回匹配的事件 id;无匹配/多匹配返回 None。
    """
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(chain):
            return chain[idx].id
        return None
    matches = [e.id for e in chain if e.id.startswith(choice)]
    if len(matches) == 1:
        return matches[0]
    return None  # 无匹配或多匹配


def _cmd_tree(args: argparse.Namespace) -> int:
    store = _open_session(args.sessions_dir, args.session_id)
    if store is None:
        return 1
    target_event_id = args.event_id
    if target_event_id is None:
        # 交互式选择回退点
        try:
            chain = store.get_event_chain()
        except ValueError as exc:
            print(f"获取事件链失败: {exc}")
            return 1
        if not chain:
            print("(会话为空,无可回退事件)")
            return 1
        print("可回退的事件:")
        for i, event in enumerate(chain, start=1):
            print(_format_event_for_tree(event, i))
        try:
            choice = input("\n请选择回退点(输入序号或事件 id 前 8 位): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return 1
        if not choice:
            print("未选择,已取消")
            return 1
        target_event_id = _resolve_tree_choice(chain, choice)
        if target_event_id is None:
            print(f"选择无效: {choice}(无匹配/多匹配,请输入序号或唯一 id 前 8 位)")
            return 1
    try:
        new_store = store.fork(target_event_id, title=args.title)
    except ValueError as exc:
        print(f"事件 id 不存在: {exc}")
        return 1
    event_count = sum(1 for line in new_store.path.read_text(encoding="utf-8").splitlines() if line.strip())
    print("\n✅ 已创建新会话(分支):")
    print(f"Session ID: {new_store.session_id}")
    print(f"Title: {new_store._title}")
    print(f"已复制 {event_count} 个事件")
    print("\n继续执行:")
    print(f"python -m openbimagent run --playbook <playbook> --session {new_store.session_id} "
          f"--sessions-dir {args.sessions_dir}")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    store = _open_session(args.sessions_dir, args.session_id)
    if store is None:
        return 1
    out_path = args.out_path or (Path(".") / f"{args.session_id}.jsonl")
    store.export_jsonl(out_path)
    print(f"导出: {out_path}")
    return 0


def _open_session(sessions_dir: Path, session_id: str) -> SessionStore | None:
    """按 session_id(完整或前缀)打开会话;找不到打印错误返回 None。"""
    sessions_dir = Path(sessions_dir)
    # 完整 id 匹配
    target = sessions_dir / f"{session_id}.jsonl"
    if target.is_file():
        return SessionStore(target)
    # 前缀匹配
    matches = list(sessions_dir.glob(f"{session_id}*.jsonl"))
    if len(matches) == 1:
        return SessionStore(matches[0])
    if not matches:
        print(f"会话不存在: {session_id}(sessions_dir={sessions_dir})")
        return None
    print(f"会话 id 前缀匹配多个:{[m.stem for m in matches]}")
    return None


# ---------- 依赖装配 ----------


def _load_registry(profile: str | None) -> Any:
    """加载 providers registry;失败返回 None(走确定性模板)。"""
    try:
        from openbimagent.providers.registry import get_default_registry

        return get_default_registry(profile=profile)
    except Exception as exc:
        print(f"[警告] providers registry 加载失败,走确定性模板:{exc}")
        return None


def _make_blender_client(args: argparse.Namespace) -> Any:
    """构造 BlenderMCPClient;失败返回 None(走无 Blender 路径)。"""
    try:
        from openbimagent.mcp_clients.blender import BlenderMCPClient

        if args.blender_transport == "socket":
            client = BlenderMCPClient.transport_socket(port=args.blender_port)
        else:
            client = BlenderMCPClient.transport_stdio(port=args.blender_port)
        return client
    except Exception as exc:
        print(f"[警告] Blender client 构造失败,走无 Blender 路径:{exc}")
        return None


def _make_critics(registry: Any) -> tuple[Any, Any]:
    """构造 SCAD / Blender critic;registry 非空走 VLMCritic(真实 VLM,judge 与生成分家)。

    M0 冒烟:registry(test profile)非空时用 VLMCritic(critic_scad/critic_render = gpt-5.5),
    经 providers.registry.chat 走 agentrouter vision 通道;VLMCritic 构造失败回退 MockCritic 不阻断。
    registry 为空(离线/--no-blender)走 MockCritic(测试默认)。
    """
    if registry is not None:
        try:
            from openbimagent.vision.critic import VLMCritic

            scad_critic = VLMCritic(registry, role="critic_scad")
            render_critic = VLMCritic(registry, role="critic_render")
            return scad_critic, render_critic
        except Exception as exc:
            print(f"[警告] VLMCritic 构造失败,回退 MockCritic:{exc}")
    from openbimagent.vision.rubric import MockCritic

    scad_critic = MockCritic([9.0], critic_model="mock-scad")
    render_critic = MockCritic([9.0], critic_model="mock-render")
    return scad_critic, render_critic


class _UsageTrackingRegistry:
    """包装 ModelRegistry:拦截 chat() 记录 usage(角色/模型/token);验收 f token 消耗统计。

    透明委托:除 chat() 外所有属性访问转发给内部 registry(builder/critic/planner 无感)。
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.usage_log: list[dict[str, Any]] = []

    def chat(self, role: str, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        result = self._inner.chat(role, messages, **kwargs)
        try:
            usage = result.get("usage") or {}
            self.usage_log.append({
                "role": role,
                "model": result.get("model_resolved", "unknown"),
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
            })
        except Exception:
            pass
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _dump_usage_on_exit(registry: "_UsageTrackingRegistry", out_dir: Path, state: dict[str, bool]) -> None:
    """atexit 兜底:进程异常退出(未捕获异常/崩溃)也落盘已记录的 usage;已显式落盘则跳过。"""
    if state.get("done"):
        return
    try:
        _dump_usage_report(registry, out_dir, None)
    except Exception:
        pass  # 退出阶段任何失败都不抛(atexit 中异常无意义)


def _install_sigbreak_bridge() -> None:
    """Windows 中断桥:把 SIGBREAK(Ctrl+Break / CTRL_BREAK_EVENT)转抛 KeyboardInterrupt。

    背景(012 冒烟实证):驱动脚本以 CREATE_NEW_PROCESS_GROUP 启动子进程时,
    Windows 会**禁用该进程的 CTRL_C 处理器**(SIGINT 被忽略),CTRL_C_EVENT 投递无效;
    只有 CTRL_BREAK_EVENT 可达。CPython 把它映射为 SIGBREAK,默认不抛 KeyboardInterrupt。
    装上桥后,pipeline 的 `except KeyboardInterrupt` → checkpoint → /tree 续跑路径即生效。
    非 Windows 无 SIGBREAK,直接跳过(POSIX 的 Ctrl+C/SIGINT 原生可用)。
    """
    import signal

    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is None:
        return

    def _raise_keyboard_interrupt(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(sigbreak, _raise_keyboard_interrupt)


def _dump_usage_report(registry: "_UsageTrackingRegistry", out_dir: Path, session: Any) -> None:
    """把 usage_log 落盘到 <out>/usage_summary.json + 打印汇总(验收 f:token 消耗统计)。"""
    import json

    log = registry.usage_log
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "usage_summary.json"
    by_model: dict[str, dict[str, int]] = {}
    total_prompt = total_completion = total_total = 0
    for entry in log:
        m = entry["model"]
        slot = by_model.setdefault(m, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
        slot["calls"] += 1
        slot["prompt_tokens"] += entry["prompt_tokens"]
        slot["completion_tokens"] += entry["completion_tokens"]
        slot["total_tokens"] += entry["total_tokens"]
        total_prompt += entry["prompt_tokens"]
        total_completion += entry["completion_tokens"]
        total_total += entry["total_tokens"]
    report = {
        "calls": log,
        "by_model": by_model,
        "total": {"calls": len(log), "prompt_tokens": total_prompt,
                  "completion_tokens": total_completion, "total_tokens": total_total},
        "session_id": session.session_id if session is not None else None,
    }
    summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not log:
        print(f"[token] 无 LLM 调用记录(可能全程走确定性模板);明细 → {summary_path}")
        return
    print(f"[token] {len(log)} 次 LLM 调用,prompt={total_prompt} completion={total_completion} total={total_total}")
    print(f"[token] 明细 → {summary_path}")


def _make_cli_approval(yes: bool) -> ApprovalFn:
    """构造 CLI 审批门函数:y/N 询问;yes=True 跳过(但 _cmd_run 已保证 yes 时不调用)。"""

    def approve(operation: str, params: dict[str, Any]) -> bool:
        prompt = f"[审批门] {operation} params={params}\n允许? (y/N) "
        try:
            answer = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("y", "yes")

    return approve


def _fmt_phase(name: str, note: str) -> str:
    """格式化阶段日志为单行。"""
    return f"[{name}] {note}" if note else f"[{name}]"


__all__ = ["main"]
