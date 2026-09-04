"""构建：ui/workbench.html（唯一权威 UI 源）→ src/openbimagent/server/web_ui.py。

纪律（2026-09-04 并行漂移教训后确立）：
- UI 只改 ui/workbench.html，改完跑本脚本重建：python tools/build_web_ui.py
- 禁止直改 web_ui.py（生成物）；禁止再开第二份原型源文件
- 构建后建议：uv run pytest tests/test_m2_fastapi.py -q + 起服目检
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # 仓库根（脚本位于 tools/）
html = (ROOT / "ui" / "workbench.html").read_text(encoding="utf-8")

assert '"""' not in html, "页面内容不得包含三引号（PAGE 用 r-string 承载）"
assert "__WB_TOKEN__" in html, "页面必须包含 __WB_TOKEN__ 注入占位"

NEW_PY = '''"""M2 P6 Web Console: openBIMAgent 数字化工程工作台（方案 J 集成版 · 功能打通）。

布局：Codex 风格 × 3D 视口英雄区（ui/workbench.html 为唯一权威 UI 源，tools/build_web_ui.py 构建）。
- 组件栈：Franken UI 2.1.2 shadcn zinc token 皮肤 + Motion 动效
- 库文件 vendor 到 server/static/vendor/（MIT 许可），经 /static 挂载，完全离线可用
- 页尾集成态接线脚本消费真实端点（运行/会话/设置/上传/调度/导出全部功能打通）：
  runs（POST/GET active）、sessions + sessions/{id}/events、demo/municipal-pipeline、
  demo/rule-tree、demo/runtime-info、plugins、plugins/invoke、demo/export-blender、
  settings/llm（GET/PUT）、settings/models、uploads（GET/POST/DELETE）、skills、memory
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

_STATIC_DIR = Path(__file__).resolve().parent / "static"

PAGE = r"""__PAGE_CONTENT__"""


def add_web_ui(app: FastAPI, token: str | None = None) -> None:
    """挂载 /static 静态资源（vendor 组件库）并注册 / 工作台页面。

    token 注入所伺服页面（window.__WB_TOKEN），前端变更请求据此携带 Bearer
    （对齐 server/auth.py 守卫；token 不出现在任何 API 响应体中）。
    """
    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="web-ui-static")

    @app.get("/", include_in_schema=False)
    async def _web_ui(request: Request) -> HTMLResponse:
        return HTMLResponse(content=PAGE.replace("__WB_TOKEN__", token or ""), status_code=200)
'''

NEW_PY = NEW_PY.replace("__PAGE_CONTENT__", html)
(ROOT / "src/openbimagent/server/web_ui.py").write_text(NEW_PY, encoding="utf-8")
print("web_ui.py written:", len(NEW_PY), "bytes")
