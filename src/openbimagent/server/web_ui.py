"""M2 P6 Web 技术验证：基于 M2 API 的本地管理界面。

内嵌 HTML/JS 单页应用，通过 M2 ReadOnly API 展示会话、attempts、approvals。
不持有 Runtime lease，不读取 IPC token，不构造 Runtime。
只监听 127.0.0.1。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from openbimagent.server.readonly_http import M2ReadonlyHttpAdapter
from openbimagent.server.sse_endpoint import M2SseStreamBudget, add_sse_endpoint
from openbimagent.server.service import M2ReadOnlyService

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>openBIMAgent M2 Console</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #0d1117; color: #c9d1d9; padding: 20px; }
h1 { font-size: 1.5rem; margin-bottom: 1rem; color: #58a6ff; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
.card h3 { color: #58a6ff; margin-bottom: 8px; font-size: 1rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #21262d; }
th { color: #8b949e; font-weight: 500; }
tr:hover { background: #1c2128; }
.status { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 500; }
.status-ok { background: #1b4332; color: #7ee787; }
.status-fail { background: #3d1515; color: #ff7b72; }
.status-pending { background: #3d2e00; color: #d29922; }
.actions { margin-top: 12px; display: flex; gap: 8px; }
button { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; padding: 6px 16px; border-radius: 6px; cursor: pointer; font-size: 0.85rem; }
button:hover { background: #30363d; }
pre { background: #0d1117; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 0.8rem; color: #8b949e; }
a { color: #58a6ff; text-decoration: none; }
a:hover { text-decoration: underline; }
</style>
</head>
<body>
<h1>openBIMAgent M2 Console</h1>
<div class="card">
  <h3>System Health</h3>
  <div id="health">Loading...</div>
</div>
<div class="card">
  <h3>Sessions</h3>
  <div id="sessions">Loading...</div>
</div>
<div class="card">
  <h3>Attempts</h3>
  <div id="attempts">Loading...</div>
</div>
<script>
const API = '';
const REQ = { headers: {'X-Request-ID': 'web-ui-' + Math.random().toString(36).slice(2)} };
async function fetchJSON(url) {
  const resp = await fetch(url, REQ);
  return resp.json();
}
function show(el, html) { document.getElementById(el).innerHTML = html; }
async function load() {
  try {
    const h = await fetchJSON(API + '/api/v1/health');
    show('health',
      `<table><tr><th>Status</th><td><span class="status status-ok">${h.data.status}</span></td></tr>
       <tr><th>Mode</th><td>${h.data.mode}</td></tr>
       <tr><th>Service</th><td>${h.data.service}</td></tr>
       <tr><th>Version</th><td>${h.data.service_version}</td></tr>
       <tr><th>API Protocol</th><td>${h.data.api_protocol_version}</td></tr></table>`);
  } catch(e) { show('health', '<span class="status status-fail">Error: ' + e.message + '</span>'); }
  try {
    const s = await fetchJSON(API + '/api/v1/sessions');
    const items = s.data?.items || [];
    if (!items.length) { show('sessions', '<p>No sessions</p>'); return; }
    show('sessions',
      `<table><tr><th>ID</th><th>Title</th><th>Events</th><th>Last Active</th></tr>
       ${items.map(i => `<tr><td><a href="#" onclick="loadSession('${i.id}')">${i.id?.slice(0,12)}</a></td><td>${i.title||'-'}</td><td>${i.event_count||0}</td><td>${(i.last_active||'').slice(0,19)}</td></tr>`).join('')}</table>`);
  } catch(e) { show('sessions', '<span class="status status-fail">Error: ' + e.message + '</span>'); }
  try {
    const a = await fetchJSON(API + '/api/v1/attempts');
    const items = a.data?.items || [];
    if (!items.length) { show('attempts', '<p>No attempts</p>'); return; }
    show('attempts',
      `<table><tr><th>Request ID</th><th>Status</th><th>Agent</th><th>Lineage</th></tr>
       ${items.map(i => `<tr><td>${(i.request_id||'').slice(0,16)}</td><td><span class="status status-${i.status === 'completed' ? 'ok' : 'pending'}">${i.status||'-'}</span></td><td>${i.agent_id||'-'}</td><td>${(i.lineage_id||'').slice(0,12)}</td></tr>`).join('')}</table>`);
  } catch(e) { show('attempts', '<span class="status status-fail">Error: ' + e.message + '</span>'); }
}
async function loadSession(id) {
  try {
    const ev = await fetchJSON(API + '/api/v1/sessions/' + id + '/events');
    const data = Array.isArray(ev) ? ev : [];
    show('sessions', `<pre>${JSON.stringify(data.slice(0,20), null, 2)}</pre><p><a href="#" onclick="load()">Back to list</a></p>`);
  } catch(e) { show('sessions', '<span class="status status-fail">Error: ' + e.message + '</span>'); }
}
load();
</script>
</body>
</html>"""


def add_web_ui(app: FastAPI) -> None:
    @app.get("/", include_in_schema=False)
    async def _web_ui(request: Request) -> HTMLResponse:
        return HTMLResponse(content=PAGE, status_code=200)