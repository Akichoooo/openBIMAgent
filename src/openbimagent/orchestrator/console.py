"""P1f 本地 Operator Console：只读 Control Plane + Runtime IPC 写代理。

Console 只绑定 IPv4 loopback，不获取 Runtime lease。浏览器只与本地 HTTP 服务交互；
Runtime IPC bearer token 始终由服务端 RuntimeIpcClient 读取，绝不发送给浏览器。
"""

from __future__ import annotations

import json
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openbimagent.orchestrator.actor import ActorRef
from openbimagent.orchestrator.control_plane import ControlPlaneError, ReadOnlyControlPlane
from openbimagent.orchestrator.ipc import IpcError, RuntimeIpcClient

CONSOLE_PROTOCOL_VERSION = "1.0"
CONSOLE_HOST = "127.0.0.1"
CONSOLE_MAX_BODY_BYTES = 64 * 1024
CONSOLE_MAX_CONCURRENT_CLIENTS = 16
CONSOLE_DEFAULT_TIMEOUT_S = 5.0


class ConsoleError(RuntimeError):
    """Operator Console 配置、协议或控制代理错误。"""


class ConsoleControlRequest(BaseModel):
    """浏览器可提交的最小写控制契约；ActorRef 由服务端固定注入。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: str = Field(pattern=r"^(approval\.approve|approval\.reject|attempt\.resume|attempt\.steer|attempt\.cancel|runtime\.ping)$")
    resource_id: str | None = Field(default=None, max_length=200)
    instruction: str | None = Field(default=None, max_length=20_000)
    reason: str = Field(default="", max_length=1_000)
    idempotency_key: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:@/-]+$")

    @model_validator(mode="after")
    def _operation_fields(self) -> "ConsoleControlRequest":
        if self.operation != "runtime.ping" and not (self.resource_id or "").strip():
            raise ValueError(f"{self.operation} 需要 resource_id")
        if self.operation in {"attempt.resume", "attempt.steer"} and not (self.instruction or "").strip():
            raise ValueError(f"{self.operation} 需要 instruction")
        if self.instruction is not None and self.operation not in {"attempt.resume", "attempt.steer"}:
            raise ValueError(f"{self.operation} 不接受 instruction")
        if self.reason and self.operation not in {"approval.approve", "approval.reject"}:
            raise ValueError(f"{self.operation} 不接受 reason")
        return self


class OperatorConsoleService:
    """组合只读投影和 IPC 客户端，不暴露认证秘密。"""

    def __init__(
        self,
        sessions_dir: Path,
        *,
        actor: ActorRef,
        ipc_timeout_s: float = CONSOLE_DEFAULT_TIMEOUT_S,
        control_plane: ReadOnlyControlPlane | None = None,
        ipc_client: RuntimeIpcClient | None = None,
    ) -> None:
        self.sessions_dir = Path(sessions_dir).resolve()
        self.actor = actor
        self.control_plane = control_plane or ReadOnlyControlPlane(self.sessions_dir)
        self.ipc_client = ipc_client or RuntimeIpcClient(self.sessions_dir, timeout_s=ipc_timeout_s)

    def snapshot(self) -> dict[str, Any]:
        """返回隐私收敛的完整操作视图，不包含 task/instruction/token 原文。"""
        return {
            "protocol_version": CONSOLE_PROTOCOL_VERSION,
            "attempts": [item.model_dump(mode="json") for item in self.control_plane.list_attempts()],
            "approvals": [item.model_dump(mode="json") for item in self.control_plane.list_approvals()],
            "resumes": [item.model_dump(mode="json") for item in self.control_plane.list_resumes()],
            "steers": [item.model_dump(mode="json") for item in self.control_plane.list_steers()],
        }

    def control(self, request: ConsoleControlRequest) -> dict[str, Any]:
        operation_map = {
            "runtime.ping": "ping",
            "approval.approve": "approval.decide",
            "approval.reject": "approval.decide",
            "attempt.resume": "attempt.resume",
            "attempt.steer": "attempt.steer",
            "attempt.cancel": "attempt.cancel",
        }
        resource_id = request.resource_id or ""
        if request.operation.startswith("approval."):
            payload = {
                "approval_id": resource_id,
                "approved": request.operation == "approval.approve",
                "reason": request.reason,
            }
        elif request.operation == "attempt.resume":
            payload = {"source_request_id": resource_id, "instruction": request.instruction}
        elif request.operation == "attempt.steer":
            payload = {"request_id": resource_id, "instruction": request.instruction}
        elif request.operation == "attempt.cancel":
            payload = {"request_id": resource_id}
        else:
            payload = {}
        return self.ipc_client.call(
            operation_map[request.operation],  # type: ignore[arg-type]
            actor=self.actor,
            idempotency_key=request.idempotency_key,
            payload=payload,
        )


class _ConsoleHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], application: "OperatorConsoleServer") -> None:
        self.application = application
        self._slots = threading.BoundedSemaphore(CONSOLE_MAX_CONCURRENT_CLIENTS)
        super().__init__(address, _ConsoleHandler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            try:
                request.close()
            finally:
                return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class OperatorConsoleServer:
    """loopback-only HTTP 服务；严格同源、CSRF、消息上限和安全响应头。"""

    def __init__(
        self,
        service: OperatorConsoleService,
        *,
        host: str = CONSOLE_HOST,
        port: int = 0,
    ) -> None:
        if host != CONSOLE_HOST:
            raise ValueError("Operator Console v1 只允许绑定 127.0.0.1")
        if not 0 <= port <= 65535:
            raise ValueError("port 必须在 0..65535")
        self.service = service
        self.host = host
        self.port = port
        self.csrf_token = secrets.token_urlsafe(32)
        self.csp_nonce = secrets.token_urlsafe(18)
        self._server: _ConsoleHttpServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def url(self) -> str:
        if self._server is None:
            raise ConsoleError("Operator Console 尚未启动")
        return f"http://{self.host}:{self._server.server_port}/"

    @property
    def allowed_origins(self) -> frozenset[str]:
        if self._server is None:
            return frozenset()
        port = self._server.server_port
        return frozenset({f"http://127.0.0.1:{port}", f"http://localhost:{port}"})

    @property
    def allowed_hosts(self) -> frozenset[str]:
        if self._server is None:
            return frozenset()
        port = self._server.server_port
        return frozenset({f"127.0.0.1:{port}", f"localhost:{port}"})

    def start(self) -> str:
        with self._lock:
            if self._server is not None:
                return self.url
            self._server = _ConsoleHttpServer((self.host, self.port), self)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="openbim-operator-console",
                daemon=True,
            )
            self._thread.start()
            return self.url

    def stop(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)

    def html(self) -> bytes:
        return _CONSOLE_HTML.replace("__CSP_NONCE__", self.csp_nonce).encode("utf-8")


class _ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "openBIMAgentOperatorConsole/1.0"
    sys_version = ""

    @property
    def app(self) -> OperatorConsoleServer:
        return self.server.application  # type: ignore[attr-defined,no-any-return]

    def do_GET(self) -> None:  # noqa: N802
        if not self._valid_host():
            self._json_error(HTTPStatus.BAD_REQUEST, "InvalidHost", "Host 不在本地 Console 白名单")
            return
        path = urlsplit(self.path).path
        if path == "/":
            self._send(HTTPStatus.OK, self.app.html(), "text/html; charset=utf-8")
            return
        if path == "/api/v1/bootstrap":
            self._json(
                HTTPStatus.OK,
                {
                    "protocol_version": CONSOLE_PROTOCOL_VERSION,
                    "csrf_token": self.app.csrf_token,
                    "actor": self.app.service.actor.model_dump(mode="json"),
                },
            )
            return
        if path == "/api/v1/snapshot":
            try:
                self._json(HTTPStatus.OK, self.app.service.snapshot())
            except (ControlPlaneError, ValueError, OSError) as exc:
                self._json_error(HTTPStatus.CONFLICT, type(exc).__name__, str(exc))
            return
        self._json_error(HTTPStatus.NOT_FOUND, "NotFound", "资源不存在")

    def do_POST(self) -> None:  # noqa: N802
        if not self._valid_host():
            self._json_error(HTTPStatus.BAD_REQUEST, "InvalidHost", "Host 不在本地 Console 白名单")
            return
        if urlsplit(self.path).path != "/api/v1/control":
            self._json_error(HTTPStatus.NOT_FOUND, "NotFound", "资源不存在")
            return
        if self.headers.get("Origin") not in self.app.allowed_origins:
            self._json_error(HTTPStatus.FORBIDDEN, "InvalidOrigin", "写请求必须来自 Console 同源页面")
            return
        if not secrets.compare_digest(self.headers.get("X-OpenBIM-CSRF", ""), self.app.csrf_token):
            self._json_error(HTTPStatus.FORBIDDEN, "InvalidCsrf", "CSRF token 无效")
            return
        if self.headers.get_content_type() != "application/json":
            self._json_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "InvalidContentType", "仅接受 application/json")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if length < 0 or length > CONSOLE_MAX_BODY_BYTES:
            self._json_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "InvalidContentLength", "请求体为空或超过 64 KiB")
            return
        try:
            raw = self.rfile.read(length)
            request = ConsoleControlRequest.model_validate_json(raw)
            result = self.app.service.control(request)
        except (ValueError, IpcError, ConsoleError) as exc:
            status = HTTPStatus.BAD_GATEWAY if isinstance(exc, IpcError) else HTTPStatus.BAD_REQUEST
            self._json_error(status, type(exc).__name__, str(exc))
            return
        self._json(HTTPStatus.OK, {"ok": True, "result": result})

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._json_error(HTTPStatus.METHOD_NOT_ALLOWED, "CorsDisabled", "Operator Console 不提供 CORS")

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _valid_host(self) -> bool:
        return self.headers.get("Host", "") in self.app.allowed_hosts

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _json_error(self, status: HTTPStatus, code: str, message: str) -> None:
        self._json(status, {"ok": False, "error_code": code, "error_message": message[:2000]})

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            f"default-src 'none'; script-src 'nonce-{self.app.csp_nonce}'; "
            f"style-src 'nonce-{self.app.csp_nonce}'; connect-src 'self'; img-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)


_CONSOLE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>openBIMAgent Operator Console</title>
<style nonce="__CSP_NONCE__">
:root{color-scheme:dark;--bg:#11151b;--panel:#1a2029;--line:#303947;--text:#eef3f8;--muted:#9ba8b7;--blue:#5aa7ff;--red:#ff6b7a;--green:#52d39a;--amber:#f2bd5d}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}header{display:flex;justify-content:space-between;align-items:center;padding:18px 24px;border-bottom:1px solid var(--line);position:sticky;top:0;background:#11151bf2;backdrop-filter:blur(8px);z-index:2}h1{font-size:18px;margin:0}button,input,select,textarea{font:inherit}button{border:1px solid var(--line);background:#242c37;color:var(--text);border-radius:7px;padding:7px 11px;cursor:pointer}button:hover{border-color:var(--blue)}button.danger{color:#ffd8dc;border-color:#7e3942}.layout{display:grid;grid-template-columns:220px 1fr;min-height:calc(100vh - 65px)}nav{border-right:1px solid var(--line);padding:16px}nav button{display:block;width:100%;margin-bottom:8px;text-align:left}.main{padding:20px;min-width:0}.meta{color:var(--muted);font-size:12px}.stats{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:12px;margin:16px 0}.stat,.card,.dialog{background:var(--panel);border:1px solid var(--line);border-radius:10px}.stat{padding:14px}.stat b{display:block;font-size:24px}.section{display:none}.section.active{display:block}.card{padding:14px;margin:10px 0;overflow:auto}.row{display:flex;gap:12px;align-items:center;justify-content:space-between}.id{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;color:#b9d8ff;word-break:break-all}.badge{border:1px solid var(--line);border-radius:999px;padding:2px 8px;font-size:12px}.pending,.running,.queued{color:var(--amber)}.completed,.approved,.applied{color:var(--green)}.failed,.cancelled,.rejected{color:var(--red)}.actions{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}dialog{color:var(--text);width:min(620px,calc(100% - 32px));padding:18px}.field{display:grid;gap:5px;margin:12px 0}.field input,.field textarea{width:100%;background:#11151b;color:var(--text);border:1px solid var(--line);border-radius:6px;padding:9px}.field textarea{min-height:100px}.toast{position:fixed;right:20px;bottom:20px;max-width:420px;padding:12px 16px;background:#202936;border:1px solid var(--line);border-radius:8px;display:none;white-space:pre-wrap}.empty{color:var(--muted);padding:25px;text-align:center}@media(max-width:800px){.layout{grid-template-columns:1fr}nav{display:flex;overflow:auto;border-right:0;border-bottom:1px solid var(--line)}nav button{width:auto;margin:0 6px 0 0;white-space:nowrap}.stats{grid-template-columns:repeat(2,1fr)}}
</style></head><body><header><div><h1>openBIMAgent Operator Console</h1><div id="actor" class="meta">正在初始化...</div></div><button id="refresh">刷新</button></header><div class="layout"><nav id="nav"><button data-view="overview">概览</button><button data-view="attempts">Attempts</button><button data-view="approvals">Approvals</button><button data-view="resumes">Resumes</button><button data-view="steers">Steers</button></nav><main class="main"><section id="overview" class="section active"><h2>运行控制面</h2><div id="stats" class="stats"></div><div class="card"><div class="row"><div><b>Runtime IPC</b><div class="meta">由服务端代理，token 不进入浏览器</div></div><button data-action="runtime.ping">Ping</button></div></div><div id="recent"></div></section><section id="attempts" class="section"><h2>Attempts</h2><div id="attempt-list"></div></section><section id="approvals" class="section"><h2>Approvals</h2><div id="approval-list"></div></section><section id="resumes" class="section"><h2>Resumes</h2><div id="resume-list"></div></section><section id="steers" class="section"><h2>Steers</h2><div id="steer-list"></div></section></main></div><dialog id="control-dialog" class="dialog"><form method="dialog"><div class="row"><h3 id="dialog-title">控制操作</h3><button value="cancel">关闭</button></div><input id="operation" type="hidden"><input id="resource-id" type="hidden"><label class="field" id="instruction-field"><span>指令</span><textarea id="instruction" maxlength="20000"></textarea></label><label class="field" id="reason-field"><span>理由</span><input id="reason" maxlength="1000"></label><label class="field"><span>幂等键</span><input id="idempotency" required maxlength="200"></label><div class="actions"><button id="submit-control" value="default">确认提交</button></div></form></dialog><div id="toast" class="toast"></div>
<script nonce="__CSP_NONCE__">
const state={csrf:"",actor:null,data:{attempts:[],approvals:[],resumes:[],steers:[]}};const $=s=>document.querySelector(s);const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));const badge=v=>`<span class="badge ${esc(v)}">${esc(v||"-")}</span>`;function toast(msg,bad=false){const el=$("#toast");el.textContent=msg;el.style.display="block";el.style.borderColor=bad?"#7e3942":"#35674f";setTimeout(()=>el.style.display="none",5000)}async function get(url){const r=await fetch(url,{cache:"no-store"});const j=await r.json();if(!r.ok)throw new Error(j.error_message||r.statusText);return j}async function init(){const b=await get("/api/v1/bootstrap");state.csrf=b.csrf_token;state.actor=b.actor;$("#actor").textContent=`Actor: ${b.actor.actor_id} (${b.actor.actor_type})`;await refresh()}async function refresh(){try{state.data=await get("/api/v1/snapshot");render()}catch(e){toast(`刷新失败: ${e.message}`,true)}}function render(){const d=state.data;$("#stats").innerHTML=[["Attempts",d.attempts.length],["Pending approvals",d.approvals.filter(x=>x.pending).length],["Resumes",d.resumes.length],["Steers",d.steers.length]].map(x=>`<div class="stat"><span class="meta">${x[0]}</span><b>${x[1]}</b></div>`).join("");const attempt=x=>`<div class="card"><div class="row"><div><div class="id">${esc(x.request_id)}</div><div class="meta">${esc(x.role)} · lineage ${esc(x.lineage_id)} · attempt ${x.attempt_number}</div></div>${badge(x.status)}</div><div class="meta">phase=${esc(x.phase)} · artifacts=${x.artifact_count} · ${esc(x.updated_at)}</div><div class="actions"><button data-action="attempt.resume" data-id="${esc(x.request_id)}">Resume</button>${["queued","running"].includes(x.status)?`<button data-action="attempt.steer" data-id="${esc(x.request_id)}">Steer</button><button class="danger" data-action="attempt.cancel" data-id="${esc(x.request_id)}">Cancel</button>`:""}</div></div>`;const approval=x=>`<div class="card"><div class="row"><div><div class="id">${esc(x.approval_id)}</div><b>${esc(x.tool_name)}</b> <span class="meta">${esc(x.args_summary)}</span></div>${badge(x.pending?"pending":x.decision)}</div>${x.pending?`<div class="actions"><button data-action="approval.approve" data-id="${esc(x.approval_id)}">Approve</button><button class="danger" data-action="approval.reject" data-id="${esc(x.approval_id)}">Reject</button></div>`:""}</div>`;const simple=(x,id,status,meta)=>`<div class="card"><div class="row"><div class="id">${esc(x[id])}</div>${badge(status)}</div><div class="meta">${esc(meta)}</div></div>`;$("#attempt-list").innerHTML=d.attempts.map(attempt).join("")||'<div class="empty">暂无 Attempt</div>';$("#approval-list").innerHTML=d.approvals.map(approval).join("")||'<div class="empty">暂无 Approval</div>';$("#resume-list").innerHTML=d.resumes.map(x=>simple(x,"resume_id",x.receipt_id?"created":"pending",`${x.source_request_id} → ${x.new_request_id}`)).join("")||'<div class="empty">暂无 Resume</div>';$("#steer-list").innerHTML=d.steers.map(x=>simple(x,"steer_id",x.latest_status||"pending",`${x.request_id} · ${x.latest_reason||""}`)).join("")||'<div class="empty">暂无 Steer</div>';$("#recent").innerHTML=d.attempts.slice(-5).reverse().map(attempt).join("")}function openControl(op,id=""){if(op==="runtime.ping")return submit({operation:op,resource_id:null,instruction:null,reason:"",idempotency_key:`console:ping:${Date.now()}`});$("#operation").value=op;$("#resource-id").value=id;$("#dialog-title").textContent=`${op} · ${id}`;$("#instruction-field").style.display=["attempt.resume","attempt.steer"].includes(op)?"grid":"none";$("#reason-field").style.display=op.startsWith("approval.")?"grid":"none";$("#instruction").value="";$("#reason").value="";$("#idempotency").value=`console:${op}:${id}:${Date.now()}`;$("#control-dialog").showModal()}async function submit(body){try{const r=await fetch("/api/v1/control",{method:"POST",headers:{"Content-Type":"application/json","X-OpenBIM-CSRF":state.csrf},body:JSON.stringify(body)});const j=await r.json();if(!r.ok)throw new Error(j.error_message||r.statusText);toast(JSON.stringify(j.result,null,2));await refresh()}catch(e){toast(`控制失败: ${e.message}`,true)}}document.addEventListener("click",e=>{const b=e.target.closest("button");if(!b)return;if(b.dataset.view){document.querySelectorAll(".section").forEach(x=>x.classList.remove("active"));$("#"+b.dataset.view).classList.add("active")}if(b.dataset.action)openControl(b.dataset.action,b.dataset.id||"")});$("#refresh").onclick=refresh;$("#submit-control").onclick=e=>{e.preventDefault();const op=$("#operation").value;submit({operation:op,resource_id:$("#resource-id").value,instruction:["attempt.resume","attempt.steer"].includes(op)?$("#instruction").value:null,reason:op.startsWith("approval.")?$("#reason").value:"",idempotency_key:$("#idempotency").value});$("#control-dialog").close()};init();setInterval(refresh,3000);
</script></body></html>"""


__all__ = [
    "CONSOLE_DEFAULT_TIMEOUT_S",
    "CONSOLE_HOST",
    "CONSOLE_MAX_BODY_BYTES",
    "CONSOLE_MAX_CONCURRENT_CLIENTS",
    "CONSOLE_PROTOCOL_VERSION",
    "ConsoleControlRequest",
    "ConsoleError",
    "OperatorConsoleServer",
    "OperatorConsoleService",
]
