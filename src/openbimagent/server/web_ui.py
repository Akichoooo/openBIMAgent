"""M2 P6 Web Console: openBIMAgent 数字化工程工作台（方案 J 集成版 · 功能打通）。

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
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

_STATIC_DIR = Path(__file__).resolve().parent / "static"

PAGE = r"""<!DOCTYPE html>
<html lang="zh" class="dark uk-theme-zinc">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>openBIMAgent · 方案 J（Codex × 3D · Franken UI 版）</title>
<!-- Franken UI 2.1.2（MIT）：core.min.css = shadcn 系 HSL 主题 token + uk-* 组件样式；utilities.min.css = 预编译工具类 -->
<link rel="stylesheet" href="/static/vendor/franken/core.min.css">
<link rel="stylesheet" href="/static/vendor/franken/utilities.min.css">
<!-- Motion（motion.dev，MIT）：入场/展开动效，离线自动降级 -->
<script src="/static/vendor/motion/motion.js"></script>
<script>
window.__M={animate:window.Motion&&window.Motion.animate,stagger:window.Motion&&window.Motion.stagger};
(function(){
  try{
    var t=localStorage.getItem('wb_theme')||'dark';
    if(t==='system') t=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';
    document.documentElement.setAttribute('data-theme', t);
    if(t==='light') document.documentElement.classList.remove('dark');
    else document.documentElement.classList.add('dark');
  }catch(e){}
})();
</script>
<style>
/* ============ 布局 token 映射：默认深色 Obsidian (franken zinc-dark) ============ */
:root{
  --bg:hsl(var(--background));
  --bg1:hsl(var(--card));
  --bg2:hsl(var(--muted) / .55);
  --bg3:hsl(var(--accent));
  --line:hsl(var(--border) / .55);
  --line2:hsl(var(--border));
  --ink:hsl(var(--foreground));
  --ink2:hsl(var(--muted-foreground));
  --ink3:hsl(var(--muted-foreground) / .6);
  --acc:hsl(var(--primary));
  --acc-fg:hsl(var(--primary-foreground));
  --acc-dim:hsl(var(--primary) / .12);
  --grn:#3fb68b; --grn-dim:rgba(63,182,139,.13);
  --red:hsl(var(--destructive)); --red-dim:hsl(var(--destructive) / .12);
  --amb:#d9a13f; --amb-dim:rgba(217,161,63,.13);
  --mono:ui-monospace,'Cascadia Code','JetBrains Mono',Consolas,monospace;
  --sans:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;
  /* 统一圆角 token：--r 全局唯一入口（此前引用未定义的 --radius，所有 var(--r) 圆角静默变直角）
     8px 与 .fin/弹窗内 8-10px 半径族一致；改这一处即全站换肤 */
  --r:8px;
}

/* ============ 白底黑字皮肤 (Light Theme · 图五) ============ */
html[data-theme="light"] {
  --bg: #ffffff;
  --bg1: #f8fafc;
  --bg2: #f1f5f9;
  --bg3: #e2e8f0;
  --line: #e2e8f0;
  --line2: #cbd5e1;
  --ink: #0f172a;
  --ink2: #475569;
  --ink3: #94a3b8;
  --acc: #0284c7;
  --acc-fg: #ffffff;
  --acc-dim: rgba(2, 132, 199, 0.12);
  --grn: #16a34a;
  --grn-dim: rgba(22, 163, 74, 0.12);
  --red: #dc2626;
  --red-dim: rgba(220, 38, 38, 0.12);
  --amb: #d97706;
  --amb-dim: rgba(217, 119, 6, 0.12);
}
html[data-theme="light"] body {
  background: var(--bg);
  color: var(--ink);
}
html[data-theme="light"] ::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 5px;
  border: 2px solid #ffffff;
}
html[data-theme="light"] .viewport {
  background: radial-gradient(1200px 600px at 60% 20%, #f1f5f9 0%, #ffffff 70%);
}
html[data-theme="light"] .hud-chip,
html[data-theme="light"] .vt {
  background: rgba(255, 255, 255, 0.88);
  border-color: #cbd5e1;
  color: #334155;
}
html[data-theme="light"] .vt:hover {
  color: #0f172a;
  border-color: #94a3b8;
}
html[data-theme="light"] .vt::before {
  background: #ffffff;
  border-color: #cbd5e1;
  color: #0f172a;
  box-shadow: 0 4px 14px rgba(0,0,0,0.08);
}
html[data-theme="light"] .vp-tl {
  background: rgba(255, 255, 255, 0.92);
  border-color: #cbd5e1;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08);
}
html[data-theme="light"] .msg-you {
  background: #e0f2fe;
  color: #0369a1;
  border: 1px solid #bae6fd;
}
html[data-theme="light"] .agentline {
  color: #1e293b;
}
html[data-theme="light"] .fin {
  background: #ffffff;
  color: #0f172a;
  border-color: #cbd5e1;
}
html[data-theme="light"] .fin:focus {
  border-color: #0284c7;
  background: #ffffff;
}
html[data-theme="light"] .modal {
  background: #ffffff;
  border-color: #cbd5e1;
  box-shadow: 0 20px 50px rgba(0,0,0,0.15);
  color: #0f172a;
}
html[data-theme="light"] .modal p {
  color: #475569;
}
html[data-theme="light"] .modal-mask,
html[data-theme="light"] .setpage {
  background: rgba(15, 23, 42, 0.35);
}
html[data-theme="light"] .setpage-in {
  background: #ffffff;
  border-color: #cbd5e1;
  box-shadow: 0 25px 60px rgba(0,0,0,0.18);
}
html[data-theme="light"] .setnav {
  background: #f8fafc;
  border-color: #e2e8f0;
}
html[data-theme="light"] .setmain {
  background: #ffffff;
}
html[data-theme="light"] .sethead {
  border-color: #e2e8f0;
}
html[data-theme="light"] .sess-pop,
html[data-theme="light"] .sl-menu,
html[data-theme="light"] .slash,
html[data-theme="light"] #mdlMenu {
  background: #ffffff;
  border-color: #cbd5e1;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.12);
}
html[data-theme="light"] #sessHover {
  background: #ffffff;
  border-color: #cbd5e1;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.12);
}

html[data-theme="light"] .sess-pop-submenu {
  background: #ffffff;
  border-color: #cbd5e1;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.12);
}
html[data-theme="light"] .rule {
  background: #ffffff;
  border-color: #e2e8f0;
}
html[data-theme="light"] .rule:hover {
  border-color: #cbd5e1;
  box-shadow: 0 2px 8px rgba(0,0,0,.04);
}
html[data-theme="light"] .prov-detail {
  background: #ffffff;
  border-color: #e2e8f0;
}
html[data-theme="light"] .prov-mdl {
  background: #f8fafc;
  border-color: #e2e8f0;
}
html[data-theme="light"] .tool {
  background: #ffffff;
  border-color: #e2e8f0;
}
html[data-theme="light"] .tool-b {
  background: #f8fafc;
  color: #334155;
}
html[data-theme="light"] .hitl {
  background: #fffbeb;
  border-color: #fde68a;
}
html[data-theme="light"] .seg {
  background: #f1f5f9;
}
html[data-theme="light"] .seg button.on {
  background: #ffffff;
  color: #0f172a;
  box-shadow: 0 1px 3px rgba(0,0,0,.1);
}

*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{font:13px/1.55 var(--sans);background:var(--bg);color:var(--ink);display:flex;overflow:hidden}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-thumb;background:#2a323e;border-radius:5px;border:2px solid var(--bg)}::-webkit-scrollbar-track{background:transparent}
button{font:inherit;color:inherit;background:none;border:none;cursor:pointer}
.dot{width:7px;height:7px;border-radius:50%;flex:none;display:inline-block}
.dot.g{background:var(--grn)}.dot.y{background:var(--amb)}.dot.b{background:var(--acc)}
.mono{font-family:var(--mono)}

/* 1. icon rail */
.rail{width:52px;flex:none;background:var(--bg1);border-right:1px solid var(--line);display:flex;flex-direction:column;align-items:center;padding:10px 0;gap:4px;z-index:30}
.rail .logo{width:32px;height:32px;border-radius:9px;background:hsl(var(--primary));color:hsl(var(--primary-foreground));font-weight:800;font-size:13px;display:flex;align-items:center;justify-content:center;margin-bottom:10px}
.rb{width:36px;height:36px;border-radius:9px;color:var(--ink2);display:flex;align-items:center;justify-content:center;position:relative}
.rb:hover{background:var(--bg3);color:var(--ink)}
.rb.on{background:var(--bg3);color:var(--ink)}
.rb.on::before{content:'';position:absolute;left:-8px;top:9px;bottom:9px;width:3px;border-radius:2px;background:var(--acc)}
.rb svg{width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
.rail .sp{flex:1}

/* 2. sidebar */
.sidebar{width:248px;flex:none;background:var(--bg1);border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0}
.sidebar.collapsed{display:none}
.sb-h{padding:12px 12px 8px;display:flex;align-items:center;gap:8px}
.sb-h .t{font-weight:650;font-size:13.5px}
.sb-h .v{font:10px var(--mono);color:var(--ink2);border:1px solid var(--line);border-radius:5px;padding:1px 5px}
.sb-toggle{width:26px;height:26px;flex:none;display:flex;align-items:center;justify-content:center;border:1px solid var(--line2);border-radius:7px;background:var(--bg2);color:var(--ink2);cursor:pointer}
.sb-toggle:hover{background:var(--bg3);color:var(--ink)}
.sb-toggle svg{width:14px;height:14px}
.newtask{margin:2px 12px 10px;display:flex;align-items:center;gap:8px;padding:8px 11px;border:1px solid var(--line2);border-radius:var(--r);color:var(--ink);font-size:12.5px;transition:background .12s}
.newtask:hover{background:var(--bg3)}
.newtask kbd{margin-left:auto;font:10px var(--mono);color:var(--ink2);border:1px solid var(--line);border-radius:4px;padding:1px 5px}
.sb-sec{padding:4px 16px 6px;font:10.5px var(--mono);letter-spacing:.08em;color:var(--ink3);text-transform:uppercase}
.sb-list{flex:1;overflow-y:auto;overflow-x:hidden;padding:0 8px 8px;box-sizing:border-box}
/* playbook 文件夹分组（ZCode 项目折叠语义；折叠态 localStorage 持久化） */
.fold{display:flex;align-items:center;gap:6px;padding:7px 8px 4px;font:11px var(--mono);color:var(--ink2);cursor:pointer;user-select:none;max-width:100%;box-sizing:border-box}
.fold:hover{color:var(--ink)}
.fold .chev{font-size:9px;color:var(--ink3);transition:transform .15s}
.fold.closed .chev{transform:rotate(-90deg)}
.fold .cnt{margin-left:auto;font-size:9.5px;color:var(--ink3)}
.fold-body{max-width:100%;box-sizing:border-box}
.fold-body.closed{display:none}
/* 会话单行（图三/图四：悬浮 3 按钮 · 磨砂虚化 · 置顶徽标） */
.sess{display:flex;align-items:center;gap:6px;padding:7px 10px;border-radius:8px;cursor:pointer;position:relative;max-width:100%;box-sizing:border-box}
.sess:hover{background:var(--bg2)}
.sess.on{background:var(--bg3)}
.sess .pin-badge{font-size:11px;line-height:1;margin-right:2px;flex:none}
.sess .tt{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:1;transition:padding-right .12s}
.sess:hover .tt{padding-right:74px}
.sess .meta{font:10.5px var(--mono);color:var(--ink3);margin-top:2px}
/* 会话悬浮 3 按钮（图四：算符无独立卡片底色/边框，选中与未选中均仅在悬浮时显示） */
.sess-acts{display:none;position:absolute;right:6px;top:50%;transform:translateY(-50%);align-items:center;gap:2px;background:transparent!important;border:none!important;box-shadow:none!important;backdrop-filter:none!important;-webkit-backdrop-filter:none!important;z-index:5}
.sess:hover .sess-acts{display:flex}
.sess-act-btn{width:22px;height:22px;border-radius:4px;display:flex;align-items:center;justify-content:center;color:var(--ink3);background:transparent;border:none;cursor:pointer;transition:all .12s}
.sess-act-btn:hover{background:rgba(128,128,128,.16);color:var(--ink)}
.sess-act-btn svg{width:14px;height:14px;pointer-events:none}
/* 会话上下文菜单（图四：重命名、分支、归档、复制、删除；绝无「标记未读」） */
.sess-pop{display:none;position:fixed;background:var(--bg1);border:1px solid var(--line2);border-radius:12px;padding:5px;box-shadow:0 14px 44px rgba(0,0,0,.35);z-index:300;min-width:150px;backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px)}
.sess-pop.show{display:flex;flex-direction:column;gap:2px}
.sess-pop-it{display:flex;align-items:center;gap:8px;padding:6px 10px;border-radius:7px;font-size:12.5px;color:var(--ink);cursor:pointer;user-select:none}
.sess-pop-it:hover{background:var(--bg2)}
.sess-pop-it svg{width:14px;height:14px;stroke:currentColor;fill:none;flex:none}
.sess-pop-it.del{color:var(--red)}
.sess-pop-it.del:hover{background:var(--red-dim);color:var(--red)}
.sess-pop-sub{position:relative}
.sess-pop-submenu{display:none;position:absolute;left:calc(100% + 4px);top:-4px;background:var(--bg1);border:1px solid var(--line2);border-radius:10px;padding:4px;min-width:130px;box-shadow:0 12px 36px rgba(0,0,0,.3)}
.sess-pop-sub:hover .sess-pop-submenu{display:flex;flex-direction:column;gap:2px}
/* 会话悬浮详情卡（图5 语义：项目/状态/更新时间悬浮展示） */
#sessHover{position:fixed;z-index:70;display:none;min-width:210px;max-width:280px;background:var(--bg3);border:1px solid var(--line2);border-radius:10px;box-shadow:0 14px 44px rgba(0,0,0,.5);padding:10px 12px;font-size:11.5px;color:var(--ink2);pointer-events:none}
#sessHover .ht{font-size:12.5px;color:var(--ink);font-weight:600;margin-bottom:5px;word-break:break-all}
#sessHover .hr{display:flex;gap:7px;margin-top:3px;font:10.5px var(--mono)}
#sessHover .hr .k{color:var(--ink3)}
.sb-foot{border-top:1px solid var(--line);padding:10px 12px;display:flex;flex-direction:column;gap:8px}
.hosts{display:flex;gap:6px}
.host{flex:1;display:flex;align-items:center;gap:6px;font:10.5px var(--mono);color:var(--ink2);border:1px solid var(--line);border-radius:7px;padding:5px 8px}
.mchip{display:flex;align-items:center;gap:8px;padding:7px 12px;border:1px solid transparent;border-radius:9999px;background:transparent;color:var(--ink2);font:12.5px var(--sans);cursor:pointer;width:100%;transition:all .15s}
.mchip:hover{background:var(--bg2);border-color:var(--line2);color:var(--ink)}
.mchip svg{width:14px;height:14px;color:var(--ink2);transition:color .15s}
.mchip:hover svg{color:var(--ink)}
.mchip .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500}
.mchip .car{margin-left:auto;color:var(--ink3)}
/* ================================================================
   现代化模型设置（对齐截图）：供应商 CRUD + 模型卡片 + 测速探针 + 弹窗
   ================================================================ */
.ms-header{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:14px}
.ms-title{font-size:18px;font-weight:700;color:var(--ink)}
.ms-subtitle{font-size:12.5px;color:var(--ink3);margin-top:3px}
.ms-refresh-btn{width:32px;height:32px;border-radius:7px;border:1px solid var(--line2);background:var(--bg2);color:var(--ink2);cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s}
.ms-refresh-btn:hover{background:var(--bg3);color:var(--ink)}
.ms-refresh-btn svg{width:16px;height:16px}
.ms-container{display:flex;border:1px solid var(--line2);border-radius:16px;background:var(--bg1);min-height:500px;overflow:hidden}
.ms-sidebar{width:220px;flex:none;border-right:1px solid var(--line2);background:var(--bg2);padding:12px 10px;display:flex;flex-direction:column;gap:4px;overflow-y:auto}
.ms-sidebar-header{display:flex;align-items:center;justify-content:space-between;padding:4px 8px 8px}
.ms-sidebar-title{font-size:11.5px;font-weight:600;color:var(--ink3);letter-spacing:0.4px;text-transform:uppercase}
.ms-side-add-btn{width:24px;height:24px;border-radius:9999px;border:1px solid var(--line2);background:var(--bg1);color:var(--ink2);cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .12s}
.ms-side-add-btn:hover{background:var(--bg3);color:var(--ink)}
.ms-side-add-btn svg{width:13px;height:13px}
.ms-prov-list{display:flex;flex-direction:column;gap:3px}
.ms-prov-it{display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:9999px;cursor:pointer;font-size:13px;color:var(--ink);border:1px solid transparent;background:transparent;transition:all .15s}
.ms-prov-it:hover{background:var(--bg3);border-color:var(--line2)}
.ms-prov-it.on{background:var(--bg1);border-color:var(--line2);box-shadow:0 1px 4px rgba(0,0,0,.06);font-weight:600}
.ms-prov-icon{width:16px;height:16px;color:var(--ink2);flex:none;display:flex;align-items:center;justify-content:center}
.ms-prov-icon svg{width:15px;height:15px}
.ms-prov-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ms-status-dot{width:7px;height:7px;border-radius:50%;flex:none;transition:all .2s}
.ms-status-dot.on{background:#10b981;box-shadow:0 0 6px rgba(16,185,129,.45)}
.ms-status-dot.off{background:#9ca3af}
.ms-add-prov-btn{display:flex;align-items:center;gap:6px;padding:8px 14px;border-radius:9999px;border:1px dashed transparent;background:transparent;color:var(--ink2);cursor:pointer;font-size:12.5px;justify-content:center;margin-top:8px;transition:all .15s}
.ms-add-prov-btn:hover{border-color:var(--line2);color:var(--ink);background:var(--bg3)}
.ms-add-prov-btn svg{width:15px;height:15px}

.ms-main{flex:1;padding:20px 24px;overflow-y:auto;display:flex;flex-direction:column;gap:16px}
.ms-detail-head{display:flex;align-items:center;justify-content:space-between;padding-bottom:12px;border-bottom:1px solid var(--line)}
.ms-detail-title-row{display:flex;align-items:center;gap:10px}
.ms-detail-name{font-size:17px;font-weight:650;color:var(--ink)}
.ms-icon-btn{background:none;border:none;padding:5px;cursor:pointer;color:var(--ink3);display:flex;align-items:center;justify-content:center;border-radius:9999px;transition:all .12s}
.ms-icon-btn:hover{color:var(--ink);background:var(--bg2)}
.ms-icon-btn svg{width:15px;height:15px}
.ms-toggle-pill{display:inline-flex;border:1px solid var(--line2);border-radius:9999px;padding:2px;gap:2px;background:var(--bg2)}
.ms-toggle-btn{border:none;background:none;font-size:12px;padding:3px 12px;border-radius:9999px;cursor:pointer;color:var(--ink3);transition:all .15s}
.ms-toggle-btn.on-active{background:var(--grn);color:#04140d;font-weight:550;box-shadow:0 1px 4px rgba(63,182,139,.35)}
.ms-toggle-btn.off-active{background:var(--bg3);color:var(--ink2);font-weight:550}
.ms-del-prov-btn{background:none;border:none;padding:6px;cursor:pointer;color:var(--ink3);display:flex;align-items:center;justify-content:center;border-radius:9999px;transition:all .15s}
.ms-del-prov-btn:hover{color:var(--red);background:var(--red-dim,rgba(239,68,68,.1))}
.ms-del-prov-btn svg{width:16px;height:16px}

.ms-config-card{background:var(--bg2);border:1px solid var(--line2);border-radius:14px;padding:16px;display:flex;flex-direction:column;gap:12px}
.ms-form-item{display:flex;flex-direction:column;gap:5px}
.ms-form-item label{font-size:12px;font-weight:550;color:var(--ink2)}
.ms-input-box{position:relative;display:flex;align-items:center}
.ms-input-box .fin{width:100%;padding-right:36px;border-radius:10px}
.ms-eye-toggle{position:absolute;right:8px;background:none;border:none;cursor:pointer;padding:4px;color:var(--ink3);display:flex;align-items:center;justify-content:center;border-radius:9999px}
.ms-eye-toggle:hover{color:var(--ink)}
.ms-eye-toggle svg{width:16px;height:16px}

.ms-sec-header{display:flex;align-items:center;justify-content:space-between;margin-top:4px}
.ms-sec-title{font-size:13px;font-weight:650;color:var(--ink);display:flex;align-items:center;gap:6px}
.ms-sec-count{font-size:11px;font-weight:500;color:var(--ink3);background:var(--bg3);padding:1px 7px;border-radius:9999px}
.ms-add-mdl-head-btn{display:inline-flex;align-items:center;gap:5px;padding:6px 14px;border-radius:9999px;background:var(--acc);color:var(--acc-fg);border:none;font-size:12px;font-weight:500;cursor:pointer;transition:all .15s}
.ms-add-mdl-head-btn:hover{opacity:.85}
.ms-add-mdl-head-btn svg{width:13px;height:13px}

.ms-mdl-list{display:flex;flex-direction:column;gap:6px;margin-top:8px}
.ms-mdl-card{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border:1px solid transparent;border-radius:12px;background:transparent;transition:all .15s}
.ms-mdl-card:hover{background:var(--bg2);border-color:var(--line2);box-shadow:0 1px 4px rgba(0,0,0,.04)}
html[data-theme="dark"] .ms-mdl-card:hover{background:var(--bg3);border-color:var(--line2)}
.ms-mdl-name{font-size:13px;font-weight:500;color:var(--ink);letter-spacing:.2px}
.ms-mdl-acts{display:flex;align-items:center;gap:6px}
.ms-cap-badge{font-size:11px;color:var(--ink2);background:var(--bg2);border:1px solid var(--line2);border-radius:9999px;padding:2px 8px;font-weight:600}
html[data-theme="dark"] .ms-cap-badge{background:var(--bg3)}
.ms-act-btn{position:relative;width:28px;height:28px;border-radius:9999px;border:none;background:transparent;color:var(--ink3);cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .12s}
.ms-act-btn:hover{color:var(--ink);background:var(--bg2)}
.ms-act-btn.del:hover{color:var(--red);background:var(--red-dim,rgba(239,68,68,.1))}
.ms-act-btn svg{width:15px;height:15px}
.ms-act-btn[data-tip]:hover::after{content:attr(data-tip);position:absolute;bottom:calc(100% + 6px);left:50%;transform:translateX(-50%);background:#18181b;color:#ffffff;font-size:11px;padding:3px 8px;border-radius:6px;white-space:nowrap;pointer-events:none;z-index:100;box-shadow:0 4px 12px rgba(0,0,0,.15);font-family:var(--sans)}
/* 模态弹窗 */
.ms-modal-mask{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);backdrop-filter:blur(4px);z-index:200;align-items:center;justify-content:center}
.ms-modal-mask.show{display:flex}
.ms-modal-card{width:440px;max-width:92vw;background:var(--bg1);border:1px solid var(--line2);border-radius:14px;box-shadow:0 20px 50px rgba(0,0,0,.3);padding:22px 24px;display:flex;flex-direction:column;gap:14px;animation:popIn .18s cubic-bezier(0.16,1,0.3,1)}
.ms-modal-head{display:flex;align-items:center;justify-content:space-between;font-size:16px;font-weight:650;color:var(--ink)}
.ms-modal-head h4{margin:0;font-size:16px;font-weight:650}
.ms-modal-close{background:none;border:none;cursor:pointer;color:var(--ink3);padding:4px;display:flex;align-items:center;border-radius:4px;transition:all .12s}
.ms-modal-close:hover{color:var(--ink);background:var(--bg2)}
.ms-modal-close svg{width:16px;height:16px}
.ms-form-group{display:flex;flex-direction:column;gap:5px}
.ms-form-label{font-size:12px;color:var(--ink2);font-weight:500}
.ms-chip-group{display:flex;align-items:center;gap:8px;margin-top:2px}
.ms-chip{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border-radius:8px;border:1px solid var(--line2);background:var(--bg0,#ffffff);font-size:12.5px;color:var(--ink);cursor:pointer;user-select:none;transition:all .12s}
html[data-theme="dark"] .ms-chip{background:var(--bg2)}
.ms-chip.locked{background:var(--bg2);opacity:.9;cursor:default}
.ms-chip.locked .ms-chk-icon{width:14px;height:14px;color:var(--ink)}
.ms-chip.locked .ms-lock-icon{width:13px;height:13px;color:var(--ink3);margin-left:2px}
.ms-modal-foot{display:flex;align-items:center;justify-content:flex-end;gap:10px;margin-top:6px;padding-top:12px;border-top:1px solid var(--line)}
.ms-btn-cancel{background:none;border:none;color:var(--ink2);cursor:pointer;padding:7px 16px;font-size:13px;border-radius:8px;transition:all .12s}
.ms-btn-cancel:hover{color:var(--ink);background:var(--bg2)}
.ms-btn-save{background:var(--acc);color:var(--acc-fg);border:none;border-radius:8px;padding:7px 22px;font-size:13px;font-weight:550;cursor:pointer;transition:opacity .15s}
.ms-btn-save:hover{opacity:.88}

/* composer 模型窄菜单（紧贴模型芯片上方，对齐图二：宽 175px，二级向右展开） */
.mdl-wrap{position:relative;display:inline-flex;align-items:center}
#mdlMenu{display:none;position:absolute;left:0;right:auto;bottom:calc(100% + 6px);width:175px;min-width:160px;max-width:200px;background:var(--bg1);border:1px solid var(--line2);border-radius:12px;box-shadow:0 14px 44px rgba(0,0,0,.35);z-index:100;padding:6px;overflow:visible}
#mdlMenu.show{display:block}
#mdlMenu .sl{display:flex;align-items:center;justify-content:space-between;padding:7px 10px;border-radius:8px;cursor:pointer;font-size:12.5px;color:var(--ink);gap:6px}
#mdlMenu .sl:hover{background:var(--bg2)}
#mdlMenu .sl.on{background:var(--bg2)}
#mdlMenu .sl .c{font:12.5px var(--sans);color:var(--ink);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#mdlMenu .sl .chk{color:var(--grn);font-size:11px;font-weight:700}
#mdlMenu .sl .arr{color:var(--ink3);font-size:13px;line-height:1}
#mdlMenu .sl-divider{height:1px;background:var(--line);margin:4px 2px}
#mdlMenu .sl-manage{padding:7px 10px;border-radius:8px;font-size:12.5px;color:var(--ink);cursor:pointer;display:flex;align-items:center}
#mdlMenu .sl-manage:hover{background:var(--bg2)}
#mdlMenu .sl-menu{display:none;position:absolute;left:calc(100% + 6px);right:auto;top:-6px;min-width:175px;max-width:240px;max-height:320px;overflow-y:auto;background:var(--bg1);border:1px solid var(--line2);border-radius:12px;box-shadow:0 14px 44px rgba(0,0,0,.35);z-index:105;padding:6px}
#mdlMenu .sl.sub{position:relative}
#mdlMenu .sl.sub:hover>.sl-menu{display:block}
#mdlMenu .sl-menu .sl{padding:7px 10px;display:flex;align-items:center;gap:6px}
#mdlMenu .sl-menu .sl .c{font:12px var(--mono);color:var(--ink);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* 外观设置面板样式（图五） */
.set-appr-wrap{display:flex;flex-direction:column;gap:16px}
.set-appr-sec{display:flex;flex-direction:column;gap:10px;padding-bottom:16px;border-bottom:1px solid var(--line)}
.set-appr-title{font-size:14px;font-weight:600;color:var(--ink)}
.set-appr-desc{font-size:12px;color:var(--ink2);margin-top:-6px}
.set-appr-row{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:6px 0}
.set-appr-label .lbl{font-size:13px;font-weight:550;color:var(--ink)}
.set-appr-label .sub{font-size:11.5px;color:var(--ink3);margin-top:2px}
.set-appr-ctrl{flex:none;display:flex;align-items:center}
.set-appr-ctrl .set-select{width:170px;padding:6px 10px;border-radius:8px;font-size:12.5px}

/* 规则树全中文专业看板 */
.rule-filter-bar{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}
.rule-filter-btn{font-size:11.5px;padding:4px 10px;border-radius:6px;border:1px solid var(--line2);background:var(--bg2);color:var(--ink2);cursor:pointer}
.rule-filter-btn:hover{background:var(--bg3);color:var(--ink)}
.rule-filter-btn.on{background:var(--acc);color:var(--acc-fg);border-color:var(--acc)}
.rule-search-box{display:flex;align-items:center;gap:8px;margin-bottom:10px}
.rule-card{border:1px solid var(--line2);border-radius:9px;padding:10px 12px;background:var(--bg1);margin-bottom:8px;transition:border-color .15s}
.rule-card:hover{border-color:var(--line);box-shadow:0 2px 8px rgba(0,0,0,.08)}
.rule-card-hd{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.rule-card-title{font-size:13px;font-weight:600;color:var(--ink);flex:1}
.rule-badge{font:10px var(--mono);padding:2px 6px;border-radius:4px;border:1px solid var(--line2)}
.rule-badge.hard{background:var(--red-dim);color:var(--red);border-color:transparent}
.rule-badge.ok{background:var(--grn-dim);color:var(--grn);border-color:transparent}
.rule-card-spec{font:11px var(--mono);color:var(--acc);margin-bottom:4px}
.rule-card-desc{font-size:12px;color:var(--ink2);line-height:1.5;margin-bottom:6px}
.rule-card-ft{display:flex;align-items:center;gap:12px;font:11px var(--mono);color:var(--ink3);border-top:1px dashed var(--line);padding-top:6px}

/* 3. stage */
.stage{flex:1;min-width:0;display:flex;flex-direction:column;background:var(--bg)}
.st-top{height:48px;flex:none;display:flex;align-items:center;gap:10px;padding:0 14px;border-bottom:1px solid var(--line);background:var(--bg1)}
.crumb{font-size:13px;color:var(--ink2);display:flex;align-items:center;gap:7px;min-width:0}
.crumb b{color:var(--ink);font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pill{display:inline-flex;align-items:center;gap:6px;font:10.5px var(--mono);padding:3px 9px;border-radius:999px;border:1px solid var(--line2);color:var(--ink2)}
.pill.g{border-color:rgba(63,182,139,.4);color:var(--grn);background:var(--grn-dim)}
.st-top .sp{flex:1}
.seg{display:flex;background:var(--bg3);border-radius:var(--r);padding:2px;gap:1px}
/* 顶栏「导出 CAD」下拉（HITL 交付入口；从 composer 下方 chips 迁来） */
.exp-wrap{position:relative;display:inline-flex}
.exp-pill{cursor:pointer;gap:5px}
.exp-pill:hover{border-color:var(--line2);color:var(--ink)}
.exp-pill .arr{font-size:9px;color:var(--ink3);margin-left:1px}
.exp-pill.no-ir{opacity:.45;cursor:default;pointer-events:none}
.exp-menu{display:none;position:absolute;top:calc(100% + 6px);right:0;min-width:230px;background:var(--bg1);border:1px solid var(--line2);border-radius:12px;box-shadow:0 14px 44px rgba(0,0,0,.35);z-index:120;padding:6px}
.exp-menu.show{display:block;animation:popIn .15s cubic-bezier(.16,1,.3,1)}
.exp-menu .it{display:flex;align-items:center;gap:9px;width:100%;padding:8px 10px;border-radius:8px;font-size:12.5px;color:var(--ink);cursor:pointer;text-align:left}
.exp-menu .it:hover{background:var(--bg2)}
.exp-menu .it svg{color:var(--ink2);flex:none}
.exp-menu .nm{font-weight:550}
.exp-menu .fmt{margin-left:auto;font:10px var(--mono);color:var(--ink3)}
.seg button{padding:5px 12px;border-radius:6px;font-size:12px;color:var(--ink2)}
.seg button.on{background:var(--bg1);color:var(--ink);box-shadow:0 1px 4px rgba(0,0,0,.35)}
.ghostbtn{display:inline-flex;align-items:center;gap:6px;padding:6px 11px;border:1px solid var(--line2);border-radius:var(--r);font-size:12px;color:var(--ink2)}
.ghostbtn:hover{background:var(--bg3);color:var(--ink)}

.viewport{flex:1;position:relative;min-height:0;background:radial-gradient(1200px 600px at 60% 20%,hsl(var(--muted) / .35) 0%,var(--bg) 70%)}
#gl{position:absolute;inset:0;width:100%;height:100%;display:block;cursor:grab}
#gl.drag{cursor:grabbing}
.vp-hud{position:absolute;top:12px;left:14px;display:flex;gap:8px;align-items:center;pointer-events:none}
.hud-chip{pointer-events:auto;display:flex;align-items:center;gap:6px;background:hsl(var(--background) / .72);backdrop-filter:blur(8px);border:1px solid var(--line);border-radius:8px;padding:5px 10px;font:10.5px var(--mono);color:var(--ink2)}
.vp-tools{position:absolute;top:12px;right:14px;display:flex;flex-direction:column;gap:6px}
.vt{width:32px;height:32px;border-radius:8px;background:hsl(var(--background) / .72);backdrop-filter:blur(8px);border:1px solid var(--line);color:var(--ink2);display:flex;align-items:center;justify-content:center;position:relative}
.vt:hover{color:var(--ink);border-color:var(--line2)}
.vt.on{color:var(--acc);border-color:hsl(var(--primary) / .45);background:var(--acc-dim)}
.vt.off-dis{opacity:.28;pointer-events:none;cursor:default} /* 当前视图下无语义的工具（如平面图的垂直夸大） */
.vt svg{width:16px;height:16px;stroke:currentColor;fill:none;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
.vt::before{content:attr(data-tip);position:absolute;right:calc(100% + 8px);top:50%;transform:translateY(-50%);background:hsl(var(--background) / .92);backdrop-filter:blur(8px);border:1px solid var(--line2);color:var(--ink);font:11px var(--mono);padding:4px 8px;border-radius:6px;white-space:nowrap;pointer-events:none;opacity:0;transition:opacity .15s ease;box-shadow:0 4px 14px rgba(0,0,0,.4);z-index:40}
.vt:hover::before{opacity:1}
.vp-scale{position:absolute;left:14px;bottom:64px;font:10px var(--mono);color:var(--ink3);display:flex;flex-direction:column;gap:3px}
.vp-scale .bar{width:80px;height:4px;border-left:1.5px solid var(--ink3);border-right:1.5px solid var(--ink3);border-bottom:1.5px solid var(--ink3)}
.vp-tl{position:absolute;left:50%;transform:translateX(-50%);bottom:12px;display:flex;align-items:center;gap:10px;background:hsl(var(--background) / .8);backdrop-filter:blur(10px);border:1px solid var(--line2);border-radius:12px;padding:8px 12px;min-width:480px}
.play{width:28px;height:28px;border-radius:50%;background:var(--acc);color:var(--acc-fg);display:flex;align-items:center;justify-content:center;flex:none}
.play:hover{opacity:.9}
.play svg{width:13px;height:13px;fill:currentColor}
.tl-steps{display:flex;align-items:center;gap:0;flex:1}
/* 自愈回放按钮（时间线 HUD 自带；从 composer 下方 chips 迁来） */
.tl-play{width:24px;height:24px;flex:none;border-radius:7px;color:var(--ink2);display:flex;align-items:center;justify-content:center;transition:all .15s}
.tl-play:hover{background:var(--bg3);color:var(--ink)}
.tl-play:active{transform:scale(.92)}
.tl-s{display:flex;align-items:center;gap:7px;flex:none;cursor:pointer;padding:3px 2px}
.tl-s .n{width:19px;height:19px;border-radius:50%;border:1.5px solid var(--ink3);color:var(--ink3);display:flex;align-items:center;justify-content:center;font:9.5px var(--mono);flex:none}
.tl-s .lb{font-size:11px;color:var(--ink2);white-space:nowrap}
.tl-s .sub{font:9.5px var(--mono);color:var(--ink3);white-space:nowrap}
.tl-s.on .n{border-color:var(--acc);color:var(--acc);background:var(--acc-dim)}
.tl-s.on .lb{color:var(--ink)}
.tl-s.ok .n{border-color:var(--grn);color:var(--grn)}
.tl-s.bad .n{border-color:var(--red);color:var(--red)}
.tl-link{flex:1;height:1.5px;background:var(--line2);min-width:22px}
.tl-note{font:10px var(--mono);color:var(--ink3);max-width:170px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* 4. thread */
.thread{width:408px;flex:none;border-left:1px solid var(--line);background:var(--bg1);display:flex;flex-direction:column;min-height:0;transition:width .18s ease}
.thread.collapsed{width:0 !important;min-width:0 !important;overflow:hidden !important;border-left:none !important}
.th-tabs{height:44px;flex:none;display:flex;align-items:center;gap:6px;padding:0 12px;border-bottom:1px solid var(--line)}
.th-sess-info{display:flex;align-items:center;gap:6px;font-size:12.5px;min-width:0;flex:1;overflow:hidden;padding-right:6px}
.th-pkg{font-size:10.5px;color:var(--ink2);font-family:var(--mono);flex:none;border:1px solid var(--line2);border-radius:4px;padding:1px 6px;background:var(--bg2)}
.th-sep{color:var(--ink3);flex:none;font-size:11px}
.th-title{color:var(--ink);font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;font-size:12.5px}
.th-tabs .sp{flex:1}
.collapse-th{color:var(--ink3);padding:6px;border-radius:7px;display:flex;align-items:center;justify-content:center;flex:none}
.collapse-th:hover{background:var(--bg3);color:var(--ink)}
.th-scroll{flex:1;overflow-y:auto;padding:16px 14px 10px;display:flex;flex-direction:column;gap:14px}
.msg-you{align-self:flex-end;max-width:88%;background:var(--bg3);border:1px solid var(--line);border-radius:14px 14px 4px 14px;padding:9px 13px;font-size:13px}
.agentline{font-size:13px;color:var(--ink);line-height:1.65}
.agentline .dim{color:var(--ink2)}
.tool{border:1px solid var(--line);border-radius:var(--r);background:var(--bg2);overflow:hidden}
.tool-h{display:flex;align-items:center;gap:9px;padding:8px 11px;cursor:pointer;user-select:none}
.tool-h:hover{background:var(--bg3)}
.tool-h .ic{width:22px;height:22px;border-radius:6px;display:flex;align-items:center;justify-content:center;flex:none;font:10px var(--mono)}
.ic.solver{background:var(--acc-dim);color:var(--acc)}
.ic.rules{background:var(--grn-dim);color:var(--grn)}
.ic.host{background:var(--amb-dim);color:var(--amb)}
.tool-h .nm{font:11.5px var(--mono);color:var(--ink)}
.tool-h .st{margin-left:auto;font:10.5px var(--mono);color:var(--ink3);display:flex;align-items:center;gap:6px}
.st.ok{color:var(--grn)}.st.run{color:var(--acc)}.st.bad{color:var(--red)}
.tool-h .car{color:var(--ink3);font-size:10px;transition:transform .15s}
.tool.closed .car{transform:rotate(-90deg)}
.tool-b{border-top:1px solid var(--line);padding:9px 12px;font:11px var(--mono);color:var(--ink2);line-height:1.7;overflow:hidden}
.tool.closed .tool-b{display:none}
.tool-b .k{color:var(--ink3)}
.tool-b .ok{color:var(--grn)}.tool-b .bad{color:var(--red)}.tool-b .hl{color:var(--acc)}
.spin{width:11px;height:11px;border:1.5px solid var(--acc);border-top-color:transparent;border-radius:50%;animation:sp .7s linear infinite;display:inline-block}
@keyframes sp{to{transform:rotate(360deg)}}
.art{border:1px solid var(--line);border-radius:var(--r);background:var(--bg2);padding:11px 12px;display:flex;flex-direction:column;gap:8px}
.art-t{display:flex;align-items:center;gap:8px;font-size:12.5px;font-weight:600}
.art-t .tag{font:9.5px var(--mono);padding:2px 7px;border-radius:6px;background:var(--acc-dim);color:var(--acc)}
.art .hash{font:10.5px var(--mono);color:var(--ink3)}
.art .row{display:flex;gap:8px}
.abtn{flex:1;text-align:center;padding:6px 10px;border:1px solid var(--line2);border-radius:var(--r);font-size:11.5px;color:var(--ink2)}
.abtn:hover{background:var(--bg3);color:var(--ink)}
.mini-stat{display:flex;gap:12px;font:10.5px var(--mono);color:var(--ink2)}
.mini-stat b{color:var(--ink);font-weight:600}
.hitl{border:1px solid rgba(217,161,63,.4);background:linear-gradient(180deg,rgba(217,161,63,.07),rgba(217,161,63,.02));border-radius:var(--r);padding:12px}
.hitl .hd{display:flex;align-items:center;gap:8px;font-size:12.5px;font-weight:650}
.hitl .hd .tag{font:9.5px var(--mono);padding:2px 7px;border-radius:6px;background:var(--amb-dim);color:var(--amb)}
.hitl .ds{font-size:12px;color:var(--ink2);margin:7px 0 10px;line-height:1.6}
.hitl .ds code{font:10.5px var(--mono);color:var(--amb)}
.hitl .row{display:flex;gap:8px}
.approve{flex:1;background:var(--grn);color:#04140d;font-weight:650;padding:7px;border-radius:var(--r)}
.approve:hover{opacity:.92}
.approve:disabled{background:var(--bg3);color:var(--ink3);cursor:default}
.reject{padding:7px 14px;border:1px solid var(--line2);border-radius:var(--r);color:var(--ink2)}
.reject:hover{background:var(--red-dim);color:var(--red);border-color:hsl(var(--destructive) / .4)}
.rcpt{border:1px solid rgba(63,182,139,.4);background:var(--grn-dim);border-radius:var(--r);padding:11px 12px;font:11px var(--mono);color:var(--ink2);line-height:1.8}
.rcpt .hd{color:var(--grn);font-weight:700;font-size:12px;display:flex;gap:7px;align-items:center}
.rcpt .k{color:var(--ink3)}
.insp{display:none;flex:1;overflow-y:auto;padding:14px;flex-direction:column;gap:12px}
.thread.ins-mode .th-scroll{display:none}
.thread.ins-mode .insp{display:flex}
.insp-sec-t{font:10.5px var(--mono);letter-spacing:.08em;color:var(--ink3);text-transform:uppercase;margin-bottom:6px}
.rule{border:1px solid var(--line);border-radius:8px;background:var(--bg2);padding:8px 10px;margin-bottom:6px}
.rule .rh{display:flex;align-items:center;gap:8px}
.rule .rid{font:11px var(--mono);color:var(--ink)}
.rule .rst{margin-left:auto;font:9.5px var(--mono);color:var(--grn)}
.rule .rd{font-size:11px;color:var(--ink2);margin-top:3px}
.irpre{font:10.5px var(--mono);color:var(--ink2);background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;line-height:1.6;overflow-x:auto;white-space:pre}
/* 用量仪表盘（真实调用流水账聚合图形化；无数据时整组隐藏） */
.usg-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}
.usg-card{border:1px solid var(--line2);border-radius:10px;background:var(--bg1);padding:12px 14px}
.usg-k{font:10px var(--mono);letter-spacing:.06em;color:var(--ink3);text-transform:uppercase;margin-bottom:5px}
.usg-v{font:20px var(--mono);font-weight:600;color:var(--ink);line-height:1}
.usg-sub{font:10px var(--mono);color:var(--ink3);margin-top:5px}
.usg-sec{border:1px solid var(--line2);border-radius:10px;background:var(--bg1);padding:12px 14px;margin-bottom:12px}
.usg-sec-t{font:10.5px var(--mono);letter-spacing:.08em;color:var(--ink3);text-transform:uppercase;margin-bottom:10px;display:flex;align-items:center;gap:6px}
.usg-sec-t svg{flex:none}
.usg-bars{display:flex;align-items:flex-end;gap:5px;height:120px;padding:0 2px}
.usg-bar{flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;min-width:0;cursor:default}
.usg-bar .col{width:100%;max-width:26px;background:linear-gradient(180deg,var(--acc),hsl(var(--primary)/.45));border-radius:3px 3px 0 0;min-height:2px;transition:height .4s cubic-bezier(.2,.8,.2,1),opacity .15s;position:relative}
.usg-bar .col span{position:absolute;top:-16px;left:50%;transform:translateX(-50%);font:9px var(--mono);color:var(--ink2);background:var(--bg3);border:1px solid var(--line2);border-radius:4px;padding:1px 5px;white-space:nowrap;opacity:0;pointer-events:none;transition:opacity .15s;z-index:5}
.usg-bar:hover .col{opacity:.85}
.usg-bar:hover .col span{opacity:1}
.usg-bar .lbl{font:8.5px var(--mono);color:var(--ink3);white-space:nowrap}
.usg-bar.empty .col{background:var(--bg3);min-height:2px}
.usg-share{display:flex;flex-direction:column;gap:8px}
.usg-share-row{display:flex;flex-direction:column;gap:4px}
.usg-share-hd{display:flex;align-items:baseline;gap:8px;font:11px var(--mono)}
.usg-share-hd .nm{color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.usg-share-hd .pc{color:var(--ink2);white-space:nowrap}
.usg-track{height:6px;border-radius:3px;background:var(--bg3);overflow:hidden}
.usg-fill{height:100%;border-radius:3px;background:var(--acc);transition:width .5s cubic-bezier(.2,.8,.2,1)}
.usg-tbl{width:100%;border-collapse:collapse;font:10.5px var(--mono)}
.usg-tbl th{text-align:left;color:var(--ink3);font-weight:500;padding:4px 6px;border-bottom:1px solid var(--line2);white-space:nowrap}
.usg-tbl td{padding:5px 6px;border-bottom:1px solid var(--line);color:var(--ink2);white-space:nowrap}
.usg-tbl td:first-child{color:var(--ink)}
.usg-tbl .tk-out{color:var(--grn)}
.usg-src{font:9.5px var(--mono);color:var(--ink3);border:1px solid var(--line2);border-radius:4px;padding:0 5px}
/* 工具集预设三档卡（选中态高亮；403 调用门后端执行） */
.ts-cards{display:flex;flex-direction:column;gap:8px;margin-top:10px}
.ts-card{display:flex;align-items:flex-start;gap:10px;text-align:left;padding:11px 13px;border:1px solid var(--line2);border-radius:12px;background:var(--bg1);cursor:pointer;transition:all .15s}
.ts-card:hover{border-color:var(--line)}
.ts-card svg{flex:none;margin-top:2px;color:var(--ink2)}
.ts-card .nm{font-size:12.5px;font-weight:600;color:var(--ink)}
.ts-card .ds{font-size:11px;color:var(--ink3);margin-top:3px;line-height:1.5}
.ts-card.on{border-color:hsl(var(--primary) / .55);background:var(--acc-dim)}
.ts-card.on svg{color:hsl(var(--primary))}
.ts-card.on .nm{color:hsl(var(--primary))}
.ts-card .nm::after{content:'✓';margin-left:6px;color:var(--grn);font-size:11px}
.ts-card:not(.on) .nm::after{content:''}

/* MCP 服务器卡（ZCode 式：状态点 + 名称 + 状态徽章 + 传输信息 + 动作） */
.mcp-card{border:1px solid var(--line2);border-radius:12px;background:var(--bg1);padding:12px 14px;margin-bottom:8px;transition:border-color .15s}
.mcp-card:hover{border-color:var(--line)}
.mcp-hd{display:flex;align-items:center;gap:9px}
.mcp-dot{width:8px;height:8px;border-radius:50%;flex:none}
.mcp-dot.up{background:var(--grn);box-shadow:0 0 6px var(--grn)}
.mcp-dot.down{background:var(--red)}
.mcp-dot.restarting{background:var(--amb);animation:spin 1s linear infinite}
.mcp-dot.external{background:var(--ink3)}
.mcp-dot.env{background:hsl(var(--primary) / .55)}
.mcp-name{font-size:13px;font-weight:600;color:var(--ink);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mcp-badge{font:10px var(--mono);padding:2px 8px;border-radius:999px;white-space:nowrap}
.mcp-badge.up{background:var(--grn-dim);color:var(--grn)}
.mcp-badge.down{background:var(--red-dim);color:var(--red)}
.mcp-badge.restarting{background:var(--amb-dim);color:var(--amb)}
.mcp-badge.external{background:var(--bg3);color:var(--ink2)}
.mcp-badge.env{background:var(--acc-dim);color:hsl(var(--primary))}
.mcp-sub{font:10.5px var(--mono);color:var(--ink2);margin-top:6px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.mcp-sub .kv{display:inline-flex;align-items:center;gap:4px}
.mcp-sub .kv svg{width:12px;height:12px;color:var(--ink3);flex:none}
.mcp-detail{font-size:11px;color:var(--ink3);margin-top:5px;line-height:1.5}
.mcp-tools{margin-top:8px;display:flex;gap:5px;flex-wrap:wrap}
.mcp-tool{font:9.5px var(--mono);color:var(--ink2);background:var(--bg2);border:1px solid var(--line);border-radius:5px;padding:1px 6px}
.mcp-acts{display:flex;gap:6px;margin-top:9px}
/* ============ MCP 配置页（表格 + 高级配置，对齐宿主设置页截图） ============ */
.mcp-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:10px}
.mcpx-title{font-size:15px;font-weight:700;color:var(--ink)}
.mcpx-path{font:10.5px var(--mono);color:var(--ink3);margin-top:4px}
.mcp-head-btns{display:flex;gap:8px;flex:none}
.mcpx-btn{display:inline-flex;align-items:center;gap:5px;padding:6px 14px;border-radius:8px;font-size:12px;border:1px solid var(--line2);background:var(--bg1);color:var(--ink2);cursor:pointer;transition:all .15s;white-space:nowrap}
.mcpx-btn:hover{background:var(--bg3);color:var(--ink);border-color:var(--line)}
.mcpx-btn.primary{background:hsl(var(--primary));color:hsl(var(--primary-foreground));border-color:transparent;font-weight:600}
.mcpx-btn.primary:hover{filter:brightness(1.08);background:hsl(var(--primary))}
.mcpx-card{border:1px solid var(--line2);border-radius:12px;background:var(--bg1);padding:14px 16px;margin-bottom:12px}
.mcpx-card-hd{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}
.mcpx-card-t{font-size:13px;font-weight:600;color:var(--ink)}
.mcpx-hint{font-size:10.5px;color:var(--ink3)}
.mcpt{width:100%;border-collapse:collapse;font-size:12px}
.mcpt th{text-align:left;color:var(--ink3);font-weight:500;font-size:11px;padding:6px 8px;border-bottom:1px solid var(--line2);white-space:nowrap}
.mcpt td{padding:9px 8px;border-bottom:1px solid var(--line);color:var(--ink2);vertical-align:middle}
.mcpt tr:last-child td{border-bottom:none}
.mcpt tr:hover td{background:var(--bg2)}
.mcpt-name{color:var(--ink);font-weight:600;white-space:nowrap}
.mcpt-type{font:10.5px var(--mono);white-space:nowrap}
.mcpt-ops{white-space:nowrap;text-align:right}
.mcpt-dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:1px}
.mcpt-dot.up{background:var(--grn);box-shadow:0 0 5px var(--grn)}
.mcpt-dot.down{background:var(--red)}
.mcpt-dot.restarting{background:var(--amb)}
.mcpt-btn{padding:3px 10px;border-radius:6px;font-size:11px;border:1px solid var(--line2);background:var(--bg1);color:var(--ink3);cursor:pointer;transition:all .15s;margin-left:6px}
.mcpt-btn:hover{color:var(--ink);border-color:var(--line);background:var(--bg3)}
.mcpt-btn.danger:hover{color:var(--red);border-color:rgba(239,68,68,.3);background:var(--red-dim)}
.mcpt-builtin{color:var(--ink3);font-size:11px}
.mcpx-json{width:100%;min-height:200px;resize:vertical;background:var(--bg);border:1px solid var(--line2);border-radius:8px;color:var(--ink);font:11.5px/1.7 var(--mono);padding:10px 12px;outline:none}
.mcpx-json:focus{border-color:hsl(var(--primary) / .5)}
/* 启用/停用开关（对齐截图紫色 toggle） */
.mcpt-switch{position:relative;display:inline-block;width:34px;height:19px;vertical-align:middle;cursor:pointer}
.mcpt-switch input{opacity:0;width:0;height:0;position:absolute}
.mcpt-slider{position:absolute;inset:0;background:var(--bg3);border:1px solid var(--line2);border-radius:999px;transition:all .18s}
.mcpt-slider::before{content:'';position:absolute;width:13px;height:13px;left:2px;top:2px;background:var(--ink3);border-radius:50%;transition:all .18s}
.mcpt-switch input:checked + .mcpt-slider{background:hsl(var(--primary) / .85);border-color:transparent}
.mcpt-switch input:checked + .mcpt-slider::before{transform:translateX(15px);background:#fff}
.mcpx-modal-mask{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:300;align-items:center;justify-content:center}
.mcpx-modal-mask.show{display:flex}
.mcpx-modal{width:400px;max-width:92vw;background:var(--bg1);border:1px solid var(--line2);border-radius:14px;padding:18px 20px;box-shadow:0 12px 40px rgba(0,0,0,.3)}
.mcpx-modal-t{font-size:14px;font-weight:700;color:var(--ink);margin-bottom:12px}
.mcpx-lab{display:flex;flex-direction:column;gap:4px;font-size:11px;color:var(--ink2);margin-bottom:10px}
.mcpx-lab .fin{width:100%}
.mcpx-modal-acts{display:flex;justify-content:flex-end;gap:8px;margin-top:6px}
/* 策略门表（capability_policies 图形化） */
.pol-tbl{width:100%;border-collapse:collapse;font:10.5px var(--mono)}
.pol-tbl th{text-align:left;color:var(--ink3);font-weight:500;padding:5px 8px;border-bottom:1px solid var(--line2);white-space:nowrap}
.pol-tbl td{padding:6px 8px;border-bottom:1px solid var(--line);color:var(--ink2);vertical-align:top}
.pol-tbl td:first-child{color:var(--ink);white-space:nowrap}
.pol-tbl .pol-pat{color:var(--acc)}
.pol-decision{font-size:9.5px;padding:1px 7px;border-radius:999px;background:var(--amb-dim);color:var(--amb);border:1px solid rgba(217,161,63,.35);white-space:nowrap}
.cons{display:flex;flex-direction:column;gap:7px}
.cons select,.cons textarea{background:var(--bg);border:1px solid var(--line2);border-radius:8px;color:var(--ink);font:11px var(--mono);padding:7px 9px;outline:none}
.cons textarea{min-height:52px;resize:vertical}
.cons-out{font:10.5px var(--mono);color:var(--grn);background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:9px;white-space:pre-wrap;line-height:1.6}
/* 归档页面（已归档会话 + 工程交付快照） */
.arch-tabs-bar{display:inline-flex;padding:3px;background:var(--bg2);border:1px solid var(--line2);border-radius:10px;margin-bottom:14px;gap:3px}
.arch-tab{display:flex;align-items:center;gap:6px;padding:6px 14px;border-radius:7px;font-size:12.5px;color:var(--ink2);cursor:pointer;user-select:none;transition:all .15s}
.arch-tab:hover{color:var(--ink)}
.arch-tab.on{background:var(--bg1);color:var(--ink);box-shadow:0 1px 4px rgba(0,0,0,.15);font-weight:600}
.arch-badge{font:10px var(--mono);background:var(--bg3);color:var(--ink2);padding:1px 6px;border-radius:999px}
.arch-tab.on .arch-badge{background:hsl(var(--primary) / .15);color:hsl(var(--primary))}
.arch-desc{font-size:12px;color:var(--ink3);margin-bottom:14px;line-height:1.5}
.arch-list{display:flex;flex-direction:column;gap:8px}
.arch-sess-card{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 16px;background:var(--bg2);border:1px solid var(--line2);border-radius:12px;transition:all .15s}
.arch-sess-card:hover{background:var(--bg3);border-color:var(--line)}
.arch-sess-main{flex:1;min-width:0}
.arch-sess-title{font-size:13.5px;font-weight:600;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-bottom:4px}
.arch-sess-meta{font:11px var(--mono);color:var(--ink3);display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.arch-tag{background:var(--grn-dim);color:var(--grn);padding:1px 6px;border-radius:4px;font-size:10px}
.arch-sess-acts{display:flex;align-items:center;gap:6px;flex:none}
.arch-btn{display:flex;align-items:center;gap:4px;padding:5px 10px;border-radius:7px;font-size:11.5px;border:1px solid var(--line2);background:var(--bg1);color:var(--ink2);cursor:pointer;transition:all .15s}
.arch-btn:hover{background:var(--bg3);color:var(--ink);border-color:var(--line)}
.arch-btn.primary{background:hsl(var(--primary) / .12);color:hsl(var(--primary));border-color:hsl(var(--primary) / .3)}
.arch-btn.primary:hover{background:hsl(var(--primary) / .2)}
.arch-btn.danger:hover{background:var(--red-dim);color:var(--red);border-color:rgba(239,68,68,.3)}
.arch-empty{text-align:center;padding:48px 20px;border:1px dashed var(--line2);border-radius:12px;color:var(--ink3)}
.arch-empty svg{margin-bottom:10px;opacity:.6}
.arch-empty-t{font-size:13.5px;font-weight:600;color:var(--ink2);margin-bottom:4px}
.arch-empty-d{font-size:11.5px;max-width:380px;margin:0 auto;line-height:1.6}
.composer{flex:none;padding:10px 12px 12px;border-top:1px solid var(--line)}
.cmp{border:1px solid var(--line2);border-radius:14px;background:var(--bg2);padding:9px 11px 7px;position:relative}
.cmp:focus-within{border-color:hsl(var(--primary) / .5)}
.cmp textarea{width:100%;background:none;border:none;outline:none;resize:none;color:var(--ink);font:13px/1.5 inherit;display:block;min-height:19.5px;max-height:132px;overflow-y:auto}
.cmp textarea::placeholder{color:var(--ink3)}
.cmp-row{display:flex;align-items:center;gap:4px;margin-top:5px}
/* composer 附件行（上传成功的 chip 落这里；空时整行不占位） */
.cmp-attach{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
.cmp-attach:empty{display:none}
.cmp-attach .chip{font-size:10.5px}
.cbtn{width:27px;height:27px;border-radius:7px;color:var(--ink3);display:flex;align-items:center;justify-content:center}
.cbtn:hover{background:var(--bg3);color:var(--ink)}
.cbtn svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
.mdl{font:10.5px var(--mono);color:var(--ink3);padding:4px 7px;border-radius:7px}
.mdl:hover{background:var(--bg3);color:var(--ink2)}
.send{margin-left:auto;width:28px;height:28px;border-radius:50%;background:var(--acc);color:var(--acc-fg);display:flex;align-items:center;justify-content:center}
.send:hover{opacity:.9}
.send svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.chip{font:10.5px var(--mono);color:var(--ink2);border:1px solid var(--line);border-radius:999px;padding:3px 10px}
.chip:hover{background:var(--bg3);color:var(--ink)}
.slash{position:absolute;left:10px;right:10px;bottom:calc(100% + 6px);background:var(--bg3);border:1px solid var(--line2);border-radius:10px;box-shadow:0 14px 44px rgba(0,0,0,.5);display:none;overflow:hidden;z-index:40}
.slash.show{display:block}
.sl{display:flex;align-items:center;gap:10px;padding:8px 12px;cursor:pointer}
.sl:hover{background:var(--bg2)}
.sl .c{font:11px var(--mono);color:var(--acc);width:92px;flex:none}
.sl .d{font-size:11.5px;color:var(--ink2)}
.setpop{display:none;position:absolute;left:64px;bottom:14px;width:300px;background:var(--bg1);border:1px solid var(--line2);border-radius:14px;box-shadow:0 18px 60px rgba(0,0,0,.55);z-index:60;overflow:hidden}
.setpop.show{display:block}
.sp-sec{padding:11px 14px;border-bottom:1px solid var(--line)}
.sp-sec:last-child{border-bottom:none}
.sp-t{font:10.5px var(--mono);letter-spacing:.08em;color:var(--ink3);text-transform:uppercase;margin-bottom:8px}
.sp-row{display:flex;align-items:center;gap:8px;font-size:12px;padding:4px 0;color:var(--ink2)}
.sp-row .v{margin-left:auto;font:11px var(--mono);color:var(--ink)}
.opt{display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:7px;cursor:pointer;font:12px var(--mono)}
.opt:hover{background:var(--bg3)}
.opt.on{color:var(--acc)}
.modal-mask{display:none;position:fixed;inset:0;background:rgba(4,6,9,.6);backdrop-filter:blur(3px);z-index:205;align-items:center;justify-content:center}
.modal-mask.show{display:flex}
.modal{width:420px;background:var(--bg1);border:1px solid var(--line2);border-radius:16px;padding:18px;box-shadow:0 24px 80px rgba(0,0,0,.6)}
.modal h3{font-size:14px;margin-bottom:8px}
.modal p{font-size:12.5px;color:var(--ink2);line-height:1.65}
.modal pre{font:10.5px var(--mono);background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:9px;margin:10px 0;color:var(--amb);white-space:pre-wrap}
.modal .row{display:flex;gap:8px;margin-top:14px}
.modal .row button{flex:1;padding:8px;border-radius:var(--r);font-weight:600;font-size:12.5px}
.m-ok{background:var(--grn);color:#04140d}.m-ok:hover{opacity:.92}
.m-no{border:1px solid var(--line2);color:var(--ink2)}.m-no:hover{background:var(--bg3)}
/* 全局主行动按钮（统一 .btn-pri：替代散落的 Tailwind 类按钮，主行动一律主题色） */
.btn-pri{flex:1;display:inline-flex;align-items:center;justify-content:center;gap:6px;background:var(--acc);color:var(--acc-fg);border:none;border-radius:var(--r);padding:8px 14px;font-size:12.5px;font-weight:600;cursor:pointer;transition:opacity .15s}
.btn-pri:hover{opacity:.88}
.toast{position:fixed;top:16px;left:50%;transform:translateX(-50%) translateY(-8px);background:var(--bg3);border:1px solid var(--line2);border-radius:10px;padding:8px 14px;font-size:12px;color:var(--ink);opacity:0;pointer-events:none;transition:all .25s;z-index:400}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
/* 审批待决角标（挂 .th-tabs；#tabConv 元素已不存在，勿再引用） */
.bdg{font:10px var(--mono);color:var(--amb);background:var(--amb-dim);border:1px solid rgba(217,161,63,.4);border-radius:999px;padding:1px 8px;white-space:nowrap}
/* 自绘 confirm/prompt 弹窗（替换原生对话框；#cfmInput 仅 prompt 模式显示）
   z-index 210：必须盖过设置页 .setpage(90) 与模型弹窗 .ms-modal-mask(200)，
   否则在「模型设置/归档」等设置分区内触发删除时弹窗被整页遮住、看似点击无反应 */
#cfmMask{z-index:210}
#cfmMask .modal{width:400px}
#cfmInput{display:none}
/* 发送按钮禁用态（空输入；运行中由 stopBtn 接管语义） */
.send.disabled{opacity:.4;cursor:default;pointer-events:none}
.fl{display:block;font-size:11.5px;color:var(--ink2);margin:7px 0}
.fin{display:block;width:100%;margin-top:3px;background:var(--bg);border:1px solid var(--line2);border-radius:8px;color:var(--ink);font:11.5px var(--mono);padding:7px 9px;outline:none}
.fin:focus{border-color:hsl(var(--primary) / .5)}
/* 表单提示/空态统一组件（各分区共用，替代散落的内联 style 空态写法） */
.hint{font-size:11px;color:var(--ink3);line-height:1.55;margin-top:4px}
.hint code{font:10px var(--mono);color:var(--ink2);background:var(--bg2);border:1px solid var(--line);border-radius:4px;padding:0 4px}
.emptybox{text-align:center;padding:30px 20px;font-size:12px;color:var(--ink3);border:1px dashed var(--line2);border-radius:10px;line-height:1.6;background:var(--bg1)}
.emptybox .et{display:block;font-size:13px;font-weight:600;color:var(--ink2);margin-bottom:5px}
.emptybox .ed{display:block;font-size:11.5px;max-width:400px;margin:0 auto}
.fk{font:9.5px var(--mono);color:var(--grn);margin-left:6px}
/* 设置全屏页（ZCode 式：左分区导航 + 右内容独立滚动，关闭入口恒在） */
.setpage{display:none;position:fixed;inset:0;background:var(--bg);z-index:90}
.setpage.show{display:block}
.setpage-in{display:flex;height:100%}
.setnav{width:196px;flex:none;border-right:1px solid var(--line);padding:16px 10px;display:flex;flex-direction:column;gap:2px;background:var(--bg1)}
.setnav-title{font-size:14px;font-weight:600;padding:0 10px 12px}
.setnav-grp{font:9.5px var(--mono);color:var(--ink3);padding:14px 10px 4px;letter-spacing:.08em}
.setnav-it{display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:8px;color:var(--ink2);cursor:pointer;font-size:12.5px;text-align:left;background:none;border:none;width:100%}
.setnav-it:hover{background:var(--bg2);color:var(--ink)}
.setnav-it.on{background:var(--bg3);color:var(--ink)}
.setmain{flex:1;min-width:0;display:flex;flex-direction:column;min-height:0}
.sethead{display:flex;align-items:center;padding:14px 28px;border-bottom:1px solid var(--line)}
.sethead h3{font-size:15px;font-weight:600}
.setclose{margin-left:auto;width:30px;height:30px;border-radius:8px;border:1px solid var(--line2);background:var(--bg2);color:var(--ink2);cursor:pointer;font-size:13px}
.setclose:hover{background:var(--bg3);color:var(--ink)}
.setbody{flex:1;overflow-y:auto;padding:20px 28px;min-height:0}
.setbody section{max-width:680px}
.setdesc{font-size:12px;color:var(--ink2);line-height:1.65;margin-bottom:6px}
.setdesc code{font:10px var(--mono);color:var(--amb)}
#approveBtn:disabled{opacity:.5;cursor:default;pointer-events:none}
@media (max-width:1180px){.sidebar{display:none}}
</style>
</head>
<body>

<!-- ================= 1. icon rail（已删除：低频功能全部并入任务栏/检查器/设置，对标 ZCode 单侧栏） ================= -->

<!-- ================= 2. sidebar ================= -->
<aside class="sidebar" id="sidebar">
  <div class="sb-h">
    <button class="sb-toggle" onclick="toggleSidebar()" title="切换侧边栏 Ctrl+B"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9.5 4v16"/></svg></button>
    <span class="t">任务</span>
  </div>
  <button class="newtask" onclick="newTaskModal()"><svg width="13" height="13" viewBox="0 0 24 24" stroke="currentColor" fill="none" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>新任务<kbd>⌘K</kbd></button>
  <div class="sb-list" id="sessList"></div>
  <div class="sb-foot">
    <button class="mchip" onclick="toggleSettings(event)" title="设置：模型与 API · 工具集 · 宿主 · 记忆"><svg width="13" height="13" viewBox="0 0 24 24" stroke="currentColor" fill="none" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 00-.14-1.4l2.1-1.63-2-3.46-2.48 1a7 7 0 00-2.42-1.4L13.7 2h-3.4l-.36 2.6a7 7 0 00-2.42 1.4l-2.48-1-2 3.46L5.14 10.6A7 7 0 005 12c0 .48.05.94.14 1.4l-2.1 1.63 2 3.46 2.48-1a7 7 0 002.42 1.4l.36 2.6h3.4l.36-2.6a7 7 0 002.42-1.4l2.48 1 2-3.46-2.1-1.63c.09-.46.14-.92.14-1.4z"/></svg><span class="nm">设置</span></button>
  </div>
</aside>

<!-- ================= 3. stage ================= -->
<main class="stage">
  <div class="st-top">
    <button class="ghostbtn" id="sbExpandBtn" onclick="toggleSidebar()" title="展开侧边栏 (Ctrl+B)" style="display:none;margin-right:2px"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9.5 4v16"/></svg></button>
    <div class="crumb" id="stageCrumb"><span style="color:var(--ink2);font-size:12px" id="stageLabel">视口</span><span style="color:var(--ink3)">/</span><b id="stageModelName">等待模型数据</b></div>
    <span class="pill g" id="stagePill" style="display:none"><span class="dot g" id="stageDot"></span><span id="stagePillText">未收敛</span></span>
    <div class="sp"></div>
    <!-- 导出 CAD（下拉：Blender / Vectorworks；HITL 审批门）——从 composer 下方的 chips 迁来，保持对话框下方干净 -->
    <div class="exp-wrap">
      <button class="pill exp-pill" onclick="toggleExpMenu(event)" title="导出当前 IR 至 CAD 宿主（HITL 审批门）">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        导出 CAD <span class="arr">▾</span>
      </button>
      <div class="exp-menu" id="expMenu">
        <button class="it" onclick="askConfirm('blender')">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M21 16V8l-9-5-9 5v8l9 5 9-5z"/><path d="M3.3 7.9 12 12.8l8.7-4.9"/><line x1="12" y1="22" x2="12" y2="12.8"/></svg>
          <span class="nm">Blender</span><span class="fmt">.blend · 在线宿主</span>
        </button>
        <button class="it" onclick="askConfirm('vectorworks')">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M12 19l7-7 3 3-7 7-3-3z"/><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/><path d="M2 2l7.586 7.586"/><circle cx="11" cy="11" r="2"/></svg>
          <span class="nm">Vectorworks</span><span class="fmt">.vwx · 外部 runner</span>
        </button>
      </div>
    </div>
    <div class="seg" id="viewSeg">
      <button class="on" data-v="3d" onclick="setView('3d',this)">3D</button>
      <button data-v="plan" onclick="setView('plan',this)">平面</button>
      <button data-v="prof" onclick="setView('prof',this)">纵断面</button>
    </div>
    <button class="ghostbtn" onclick="toggleThread()" id="thToggle" title="展开会话面板" style="display:none"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M14.5 4v16"/></svg></button>
  </div>

  <div class="viewport">
    <canvas id="gl"></canvas>

    <div class="vp-hud">
      <div class="hud-chip"><span class="dot b"></span>CompiledUtilityIR v1</div>
      <div class="hud-chip mono" id="hudStat">加载模型数据…</div>
      <div class="hud-chip mono" id="hudCam"></div>
    </div>

    <div class="vp-tools">
      <button class="vt on" id="tgGrid" data-tip="地面网格" title="地面网格" onclick="toggleOpt('grid',this)"><svg viewBox="0 0 24 24"><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/></svg></button>
      <button class="vt on" id="tgEx" data-tip="垂直夸大 ×3" title="垂直夸大 ×3（3D 高程放大 3 倍；纵断面为制图式纵向 ×20）" onclick="toggleOpt('exag',this)"><svg viewBox="0 0 24 24"><path d="M12 3v18M8 7l4-4 4 4M8 17l4 4 4-4"/></svg></button>
      <button class="vt" id="tgSpin" data-tip="自动旋转" title="自动旋转" onclick="toggleOpt('spin',this)"><svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 11-3-6.7"/><path d="M21 3v6h-6"/></svg></button>
      <button class="vt" id="tgReset" data-tip="复位视角" title="复位视角" onclick="resetCam()"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="7"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/></svg></button>
      <button class="vt" id="tgFull" data-tip="视口全屏" title="视口全屏" onclick="toggleFullscreen()"><svg viewBox="0 0 24 24"><path d="M8 3H5a2 2 0 00-2 2v3m18 0V5a2 2 0 00-2-2h-3m0 18h3a2 2 0 002-2v-3M3 16v3a2 2 0 002 2h3"/></svg></button>
    </div>

    <div class="vp-scale"><div class="bar"></div><span id="scaleLbl">—</span></div>

    <div class="vp-tl">
      <button class="tl-play" id="tlPlayBtn" data-tip="自愈回放" title="自愈回放：iter 0 碰撞 → 膨胀重路由 → 收敛" onclick="playTimeline()"><svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M6 4l14 8-14 8z"/></svg></button>
      <div class="tl-steps" id="tlSteps"></div>
      <div class="tl-note" id="tlNote">初始 A* 路径 · 碰撞检测中</div>
    </div>
  </div>
</main>

<!-- ================= 4. thread ================= -->
<section class="thread" id="thread">
  <div class="th-tabs">
    <div class="th-sess-info" id="thSessInfo">
      <span class="th-pkg" id="thPkgName">会话</span>
      <span class="th-sep">/</span>
      <span class="th-title" id="thSessTitle" title="会话标题">会话面板</span>
    </div>
    <div class="sp"></div>
    <button class="chip" id="stopBtn" style="display:none;color:var(--red);border-color:var(--red)" title="停止当前运行（拒绝待决审批门，线程在门处安全退出）" onclick="stopCurrentRun()">⏹ 停止</button>
    <button class="collapse-th" onclick="toggleThread()" title="折叠对话面板"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M14.5 4v16"/></svg></button>
  </div>

  <!-- conversation -->
  <div class="th-scroll" id="thScroll">
    <div class="agentline" id="welcomeLine">就绪。<span class="dim">新建任务真跑 pipeline（离线走确定性模板），或直接在下方输入工程指令调度自愈求解器。会话事件、规则树、IR、导出全部来自真实端点。</span></div>

    <div class="art" id="artIR" style="display:none">
      <div class="art-t"><span class="tag">工件</span>CompiledUtilityIR v1</div>
      <div class="mini-stat"></div>
      <div class="row">
        <button class="abtn" onclick="downloadIR()">下载 JSON</button>
        <button class="abtn" onclick="openInspector('ir')">在检查器查看</button>
      </div>
    </div>

    <div class="hitl" id="hitl" style="display:none">
      <div class="hd"><span class="tag">HITL · prompt 策略门</span>等待人工确认</div>
      <div class="ds">能力 <code>cad_host:blender.execute</code> 被策略表标记为 <b>prompt</b>：将向授权根目录写入真实产物（typed plan，对象数由执行回执回填）。确认后执行。</div>
      <div class="row">
        <button id="approveBtn" onclick="askConfirm('blender')" class="btn-pri">批准导出</button>
        <button class="reject" onclick="rejectExport()">拒绝</button>
      </div>
    </div>

    <div class="tool closed" id="tool3" style="display:none">
      <div class="tool-h" onclick="toggleTool(this)">
        <span class="ic host">⬡</span>
        <span class="nm" id="tool3Nm">cad_host:blender.execute</span>
        <span class="st" id="tool3St"></span>
        <span class="car">▾</span>
      </div>
      <div class="tool-b">
        <div><span class="k">plan&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span>typed execution plan · plan_sha256 <span class="hl" id="tool3Plan">执行后回填</span></div>
        <div><span class="k">host&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span><span id="tool3Host">—</span></div>
        <div><span class="k">scope&nbsp;&nbsp;&nbsp;&nbsp;</span>受控作用域 · 授权根写盘</div>
        <div><span class="k">snapshot</span>语义快照 ↔ IR 哈希绑定（sidecar 随产物落盘）</div>
      </div>
    </div>

    <div class="rcpt" id="rcpt" style="display:none"></div>

    <div class="agentline" id="doneLine" style="display:none">已交付。<span class="dim">右侧视口为本次 IR 的真实几何回放：红色为初始碰撞路径，绿色为自愈绕行。</span></div>
  </div>

  <!-- inspector（已迁入设置页「数据与审查」分区：规则树/插件/IR/上传/用量/归档；openInspector(p) 改开设置对应区） -->

  <!-- composer -->
  <div class="composer">
    <div class="cmp">
      <textarea id="cmpTa" rows="1" placeholder="与基线模型对话，或输入 / 调用命令（/solve 调度自愈求解器）…"></textarea>
      <div class="cmp-attach" id="cmpAttach"></div>
      <div class="slash" id="slash">
        <div class="sl" onclick="pickCmd('/solve')"><span class="c">/solve</span><span class="d">重新调度自愈求解器</span></div>
        <div class="sl" onclick="pickCmd('/rules')"><span class="c">/rules</span><span class="d">打开 GB 50289 规则树</span></div>
        <div class="sl" onclick="pickCmd('/ir')"><span class="c">/ir</span><span class="d">查看 Compiled IR</span></div>
        <div class="sl" onclick="pickCmd('/export')"><span class="c">/export</span><span class="d">导出至 CAD 宿主（HITL）</span></div>
        <div class="sl" onclick="pickCmd('/recall')"><span class="c">/recall</span><span class="d">会话全文检索（FTS5，用法 /recall 关键词）</span></div>
        <div class="sl" onclick="pickCmd('/skills')"><span class="c">/skills</span><span class="d">技能库（SKILL.md 清单与调用）</span></div>
      </div>
      <div class="cmp-row">
        <button class="cbtn" title="上传设计条件与资料 (DXF / IFC / GIS / 规范)" onclick="$('fileInput').click()"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></button>
        <div class="mdl-wrap">
          <button class="mdl" id="mdlChip" title="切换模型；管理进设置" onclick="toggleModelMenu(event)">gpt-5.6-terra ▾</button>
          <div class="slash" id="mdlMenu"></div>
        </div>
        <button class="send" onclick="sendMsg()" title="发送"><svg viewBox="0 0 24 24"><path d="M12 19V5M5 12l7-7 7 7"/></svg></button>
      </div>
    </div>
  </div>
</section>

<!-- settings popover（已删除：setPop 为原型假数据残留——假模型列表/假连接绿灯；真实设置在 #setMask 弹层） -->

<!-- HITL confirm modal（宿主由 askConfirm(host) 参数化：blender / vectorworks） -->
<div class="modal-mask" id="modalMask">
  <div class="modal">
    <h3 id="expModalTitle">确认执行 cad_host:blender.execute？</h3>
    <p id="expModalDesc">该能力被策略表标记为 <b>prompt</b>。执行将向授权根目录写入真实产物，动作不可自动撤销。</p>
    <pre id="expModalPre">POST /api/v1/demo/export-blender
{ "confirm": true }</pre>
    <div class="row">
      <button class="m-no" onclick="closeModal()">取消</button>
      <button class="btn-pri" onclick="doExport()">确认执行</button>
    </div>
  </div>
</div>

<div class="toast" id="toastEl"></div>

<!-- 自绘确认/输入对话框（替换原生 confirm/prompt；风格与 .modal 同源） -->
<div class="modal-mask" id="cfmMask">
  <div class="modal">
    <h3 id="cfmTitle">确认</h3>
    <p id="cfmText" style="white-space:pre-wrap"></p>
    <input class="fin" id="cfmInput" autocomplete="off">
    <div class="row">
      <button class="m-no" id="cfmNo">取消</button>
      <button class="m-ok" id="cfmYes">确定</button>
    </div>
  </div>
</div>

<!-- 新建任务弹层（真实 POST /api/v1/runs） -->
<div class="modal-mask" id="runMask">
  <div class="modal" style="width:460px">
    <h3>新建任务 · 真跑 pipeline</h3>
    <p>后台真实执行 Clarify → Planner → Orchestrator → Deliver；离线走确定性模板 + MockCritic，配置 LLM 后走真实模型。单并发。</p>
    <textarea id="runBrief" class="fin" rows="3" style="resize:vertical" placeholder="工程指令，如：沿走廊生成 DN400 污水重力管，避让东侧建筑物"></textarea>
    <label class="fl">Domain Pack
      <select id="runPlaybook" class="fin">
        <option value="municipal_utility">municipal_utility（市政管网主线）</option>
        <option value="single_asset_hero">single_asset_hero（单体资产）</option>
      </select>
    </label>
    <div class="row">
      <button class="m-no" onclick="$('runMask').classList.remove('show')">取消</button>
      <button class="btn-pri" style="flex:1" onclick="startRun()">启动运行</button>
    </div>
  </div>
</div>

<!-- 设置弹层（真实读写 /api/v1/settings/llm） -->
<div class="setpage" id="setMask">
  <div class="setpage-in">
    <aside class="setnav">
      <div class="setnav-title">设置</div>
      <div class="setnav-grp">基础设置</div>
      <button class="setnav-it" data-sec="general" onclick="setSection('general')">常规</button>
      <button class="setnav-it on" data-sec="appearance" onclick="setSection('appearance')">外观</button>
      <button class="setnav-it" data-sec="models" onclick="setSection('models')">模型设置</button>
      <div class="setnav-grp">AGENT 能力</div>
      <button class="setnav-it" data-sec="toolset" onclick="setSection('toolset')">工具集预设</button>
      <button class="setnav-it" data-sec="memory" onclick="setSection('memory')">记忆</button>
      <button class="setnav-it" data-sec="skills" onclick="setSection('skills')">技能</button>
      <div class="setnav-grp">连接</div>
      <button class="setnav-it" data-sec="mcp" onclick="setSection('mcp')">MCP 服务器</button>
      <div class="setnav-grp">数据与审查</div>
      <button class="setnav-it" data-sec="rules" onclick="setSection('rules')">规则树</button>
      <button class="setnav-it" data-sec="plugins" onclick="setSection('plugins')">插件与能力</button>
      <button class="setnav-it" data-sec="ir" onclick="setSection('ir')">IR 查看</button>
      <button class="setnav-it" data-sec="uploads" onclick="setSection('uploads')">上传</button>
      <button class="setnav-it" data-sec="usage" onclick="setSection('usage')">用量</button>
      <button class="setnav-it" data-sec="archive" onclick="setSection('archive')">归档</button>
      <div style="flex:1"></div>
      <div class="setnav-grp" style="padding-bottom:8px">Esc 关闭</div>
    </aside>
    <div class="setmain">
      <div class="sethead"><h3 id="setTitle">外观设置</h3><button class="setclose" onclick="toggleSettings()" title="关闭 (Esc)">✕</button></div>
      <div class="setbody">
        <section data-setsec="general" style="display:none">
          <p class="setdesc">本实例的<b>运行环境只读体检表</b>——当前连的 API 端点、对话用的基线模型、微内核插件/能力规模、规则集指纹。排查"对话怎么调不通 / 规则是否更新"先看这里（全部实时取自真实端点，非缓存）。</p>
          <div style="display:flex;flex-direction:column;gap:10px;margin-top:10px">
            <label class="fl">API 端点<input class="fin" id="genEndpoint" value="…" readonly></label>
            <label class="fl">LLM 基线<input class="fin" id="genLLM" value="…" readonly></label>
            <label class="fl">微内核<input class="fin" id="genKernel" value="…" readonly></label>
            <label class="fl">规则集协议<input class="fin" id="genRules" value="…" readonly></label>
          </div>
        </section>
        <section data-setsec="appearance">
          <div class="set-appr-wrap">
            <div class="set-appr-sec">
              <div class="set-appr-title">界面设置</div>
              <div class="set-appr-desc">设置应用主题和界面文字大小。</div>
              <div class="set-appr-row">
                <div class="set-appr-label">
                  <div class="lbl">界面主题</div>
                  <div class="sub">选择浅色（纯白黑字）、深色或跟随系统主题。</div>
                </div>
                <div class="set-appr-ctrl">
                  <select id="selTheme" class="fin set-select" onchange="applyThemeSetting(this.value)">
                    <option value="light">☀️ 浅色 (纯白黑字)</option>
                    <option value="dark" selected>🌙 深色 (Obsidian)</option>
                    <option value="system">🖥 跟随系统</option>
                  </select>
                </div>
              </div>
              <div class="set-appr-row">
                <div class="set-appr-label">
                  <div class="lbl">界面字号</div>
                  <div class="sub">调整应用界面的文字大小，图标和布局尺寸不受影响。</div>
                </div>
                <div class="set-appr-ctrl">
                  <select id="selFontSize" class="fin set-select" onchange="applyFontSizeSetting(this.value)">
                    <option value="12px">12 px</option>
                    <option value="13px" selected>13 px (默认)</option>
                    <option value="14px">14 px</option>
                    <option value="15px">15 px</option>
                  </select>
                </div>
              </div>
            </div>

            <div class="set-appr-sec" style="border-bottom:none">
              <div class="set-appr-title">主题快速切换</div>
              <div style="display:flex;gap:12px;margin-top:6px">
                <div onclick="applyThemeSetting('light')" style="flex:1;padding:12px;border:2px solid var(--line2);border-radius:10px;cursor:pointer;background:#ffffff;color:#0f172a;box-shadow:0 2px 8px rgba(0,0,0,.08)">
                  <div style="font-weight:650;font-size:13px">☀️ 浅色 (纯白黑字)</div>
                  <div style="font-size:11.5px;color:#64748b;margin-top:4px">高对比度白底黑字，适配日光办公与工程图纸底色</div>
                </div>
                <div onclick="applyThemeSetting('dark')" style="flex:1;padding:12px;border:2px solid var(--line2);border-radius:10px;cursor:pointer;background:#18181b;color:#f4f4f5;box-shadow:0 2px 8px rgba(0,0,0,.3)">
                  <div style="font-weight:650;font-size:13px">🌙 深色 (Obsidian)</div>
                  <div style="font-size:11.5px;color:#a1a1aa;margin-top:4px">Franken UI Zinc 风格夜间主题，沉浸式三维视口</div>
                </div>
              </div>
            </div>
          </div>
        </section>
        <section data-setsec="models" style="display:none">
          <div class="ms-header">
            <div class="ms-header-left">
              <div class="ms-title">模型设置</div>
              <div class="ms-subtitle">管理模型供应商与 API 参数，配置后可在聊天时选择使用。</div>
            </div>
            <button class="ms-refresh-btn" onclick="loadProviders()" title="刷新模型配置">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
            </button>
          </div>
          <div class="ms-container">
            <div class="ms-sidebar">
              <div class="ms-sidebar-header">
                <span class="ms-sidebar-title">供应商列表</span>
                <button class="ms-side-add-btn" onclick="openAddProvModal()" title="添加供应商">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                </button>
              </div>
              <div id="msProvList" class="ms-prov-list"></div>
              <button class="ms-add-prov-btn" onclick="openAddProvModal()">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                <span>添加供应商</span>
              </button>
            </div>
            <div class="ms-main" id="msDetail">
              <div class="emptybox"><span class="et">选择供应商</span><span class="ed">从左侧列表选择一个供应商进行配置，或添加自定义供应商</span></div>
            </div>
          </div>
        </section>
        <section data-setsec="toolset" style="display:none">
          <p class="setdesc">限制 Agent 能调用哪些能力的<b>安全档位</b>：选低档位后，被滤掉的能力在清单里不可见、直接调用返回 403。即时生效，无需保存——适合演示或交给别人操作时收窄风险面。</p>
          <div class="ts-cards" id="tsCards">
            <button class="ts-card" data-v="full" onclick="saveToolset('full');markToolset('full')">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 019 9"/><path d="M12 7v5l3 3"/></svg>
              <span class="nm">full · 全部能力</span>
              <span class="ds">求解器 + 规则核验 + CAD 写盘 + 外部 MCP——开发调试用，交付动作有审批门兜底</span>
            </button>
            <button class="ts-card" data-v="modeling" onclick="saveToolset('modeling');markToolset('modeling')">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M21 16V8l-9-5-9 5v8l9 5 9-5z"/><path d="M3.3 7.9 12 12.8l8.7-4.9"/><line x1="12" y1="22" x2="12" y2="12.8"/></svg>
              <span class="nm">modeling · 建模档</span>
              <span class="ds">solver + cad_host——可生成管线并导出 CAD，但不开放其余能力</span>
            </button>
            <button class="ts-card" data-v="minimal" onclick="saveToolset('minimal');markToolset('minimal')">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 12h6"/></svg>
              <span class="nm">minimal · 最小档</span>
              <span class="ds">仅 solver——只算不写盘，任何宿主导出一律 403，给别人演示最安全</span>
            </button>
          </div>
          <div class="hint" id="tsHint" style="margin-top:8px">当前档位加载中…</div>
        </section>
        <section data-setsec="memory" style="display:none">
          <p class="setdesc"><b>跨会话长期记忆</b>：Agent 在这里记住你的项目偏好和常用约定（存 <code>memory/MEMORY.md</code> 和 <code>USER.md</code>，不入库）。新开任务时相关片段会自动注入上下文——所以它"越用越懂你"。查看免费；写入/删除都要经你确认（上面的策略门里那两条 memory 记录就是这个）。</p>
          <div id="memList" style="max-height:280px;overflow-y:auto;font:10.5px var(--mono);color:var(--ink2);background:var(--bg2);border:1px solid var(--line);border-radius:8px;padding:8px 10px">加载中…</div>
          <div class="row" style="margin-top:8px">
            <select id="memFile" class="fin" style="width:110px;flex:none">
              <option value="memory">MEMORY</option>
              <option value="user">USER</option>
            </select>
            <input id="memEntry" class="fin" style="flex:1" placeholder="新记忆条目（写入即持久化，需确认）">
            <button class="m-no" onclick="recordMemory()">写入</button>
          </div>
        </section>
        <section data-setsec="skills" style="display:none">
          <p class="setdesc"><b>技能 = 可复用的操作手册</b>（SKILL.md 文件，Markdown 写法）。Agent 执行任务时按需取用：平时只看目录不占上下文，用到才读正文（"渐进披露"）。下面是当前生效清单；对话里输 <code>/skills</code> 可直接调用；每次成功交付后系统自动沉淀"候选技能"，需你在 <code>/skills</code> 面板人工批准才转正——不自动生效。</p>
          <div id="setSkillsList"><div class="emptybox">加载中…</div></div>
        </section>
        <section data-setsec="mcp" style="display:none">
          <div class="mcp-head">
            <div>
              <div class="mcpx-title">MCP 配置</div>
              <div class="mcpx-path">配置文件路径：<span id="mcpCfgPath">…</span></div>
            </div>
            <div class="mcp-head-btns">
              <button class="mcpx-btn" onclick="mcpResetDefault()">恢复默认</button>
              <button class="mcpx-btn primary" onclick="mcpSave()">保存 MCP</button>
              <button class="mcpx-btn" onclick="mcpRefreshStatus()" title="重新探测内置宿主与第三方 server 状态">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>
                刷新状态
              </button>
              <button class="mcpx-btn" onclick="mcpOpenAddModal()">新增</button>
            </div>
          </div>
          <div class="mcpx-card">
            <div class="mcpx-card-hd">
              <span class="mcpx-card-t">MCP Servers</span>
              <span class="mcpx-hint" id="mcpTableHint"></span>
            </div>
            <table class="mcpt">
              <thead><tr><th>名称</th><th>状态</th><th>类型</th><th style="text-align:right">操作</th></tr></thead>
              <tbody id="mcpTableBody"><tr><td colspan="4" class="mcpt-builtin">加载中…</td></tr></tbody>
            </table>
          </div>
          <div class="mcpx-card">
            <div class="mcpx-card-hd">
              <span class="mcpx-card-t">高级配置</span>
              <span class="mcpx-hint">mcpServers JSON 格式 · 支持 description/alwaysLoad/disabled 元数据 · 保存后重启服务挂载生效</span>
            </div>
            <textarea id="mcpJson" class="mcpx-json" spellcheck="false" placeholder='{"mcpServers": {"my-server": {"url": "https://example.com/mcp"}}}'></textarea>
          </div>
        </section>
        <!-- MCP 新增/编辑弹窗 -->
        <div class="mcpx-modal-mask" id="mcpModalMask">
          <div class="mcpx-modal">
            <div class="mcpx-modal-t" id="mcpModalTitle">新增 MCP Server</div>
            <label class="mcpx-lab">名称（slug，字母/数字/连字符）
              <input id="mcpModalName" class="fin" placeholder="my-server">
            </label>
            <label class="mcpx-lab">类型
              <select id="mcpModalType" class="fin" onchange="mcpModalSyncType()">
                <option value="stdio">STDIO（本地命令）</option>
                <option value="url">URL（远程 HTTP）</option>
              </select>
            </label>
            <div id="mcpModalStdioFields">
              <label class="mcpx-lab">命令
                <input id="mcpModalCmd" class="fin" placeholder="npx">
              </label>
              <label class="mcpx-lab">参数（JSON 数组）
                <input id="mcpModalArgs" class="fin" placeholder='["-y","@mcp/server-xxx"]'>
              </label>
            </div>
            <div id="mcpModalUrlFields" style="display:none">
              <label class="mcpx-lab">URL
                <input id="mcpModalUrl" class="fin" placeholder="https://example.com/mcp">
              </label>
            </div>
            <div class="mcpx-modal-acts">
              <button class="mcpx-btn" onclick="mcpCloseModal()">取消</button>
              <button class="mcpx-btn primary" onclick="mcpSubmitModal()">确定</button>
            </div>
          </div>
        </div>
        <section data-setsec="rules" style="display:none">
          <div class="insp-sec-t">市政管网规则树 · GB 50289-2016（白盒约束求解硬门禁 · 自检 33/33）</div>
          <p class="setdesc" style="margin-bottom:12px">这是管线的<b>合规门禁</b>：每次生成/自愈求解后，系统用这 12 条国家规范逐条核验几何（净距、覆土、管径），违规会在审批前自动拦截并给出行文依据。想调整规则：改 domain pack 的规则集文件（<code>out/municipal_rule_set.json</code> 为当前生效副本），重启后生效——规则只拦"生成结果违规"，不会破坏你的既有项目；若某条规则误伤，可在 pack 内调整或删除该条后重跑自愈。</p>
          <div class="rule-search-box">
            <input id="ruleSearchInput" class="fin" placeholder="输入关键词搜索规则条文、管线类别或国标编号…" oninput="filterRules()" style="flex:1">
          </div>
          <div class="rule-filter-bar" id="ruleFilterBar">
            <button class="rule-filter-btn on" onclick="setRuleFilter('',this)">全部 (12)</button>
            <button class="rule-filter-btn" onclick="setRuleFilter('建筑基础',this)">建筑基础</button>
            <button class="rule-filter-btn" onclick="setRuleFilter('给水管网',this)">给水管网</button>
            <button class="rule-filter-btn" onclick="setRuleFilter('燃气管网',this)">燃气管网</button>
            <button class="rule-filter-btn" onclick="setRuleFilter('弱电通信',this)">弱电通信</button>
            <button class="rule-filter-btn" onclick="setRuleFilter('强电电力',this)">强电电力</button>
          </div>
          <div id="ruleList"></div>
        </section>
        <section data-setsec="plugins" style="display:none">
          <div class="insp-sec-t">微内核插件 · 能力清单与策略门</div>
          <p class="setdesc">本系统是<b>微内核插件架构</b>：求解器、规则库、CAD 宿主驱动都是插件，对外暴露"能力"（capability，如 <code>solver:self_healing</code>）。下方清单是当前启用的插件及其能力；策略门表列出哪些能力在执行前<b>必须人工确认</b>（写盘、删数据、外部进程——fail-closed，默认拒绝直到你批准）。</p>
          <div id="plgList"></div>
          <div class="insp-sec-t" style="margin-top:12px">能力调度控制台</div>
          <p class="setdesc">直接调度任意能力的高级入口：选能力 → 填 payload JSON（可留空 <code>{}</code>）→ 运行。带 prompt 策略的能力按 <code>confirm=true</code> 语义发起（不再二次弹窗）；系统满载时返回 503 + 错误码 -32001（背压保护，稍后重试）。</p>
          <div class="cons">
            <select id="capSel">
              <option>solver:self_healing</option><option>solver:grid_route</option><option>solver:hydraulic</option>
              <option>rules:gb50289.verify</option><option>cad_host:blender.execute</option><option>cad_host:vectorworks.execute</option>
            </select>
            <textarea placeholder='payload JSON（可选，如 {"confirm": true} 或求解器入参）'>{}</textarea>
            <button onclick="invokeCap()" class="btn-pri" style="align-self:flex-start">运行（经 registry.invoke）</button>
            <div class="cons-out">结果为结构化 JSON 回执（converged/violations/receipt），直接回显于此</div>
          </div>
        </section>
        <section data-setsec="ir" style="display:none">
          <div class="insp-sec-t">CompiledUtilityIR v1（不可变 · 哈希寻址）</div>
          <p class="setdesc"><b>IR = Intermediate Representation（中间表示）</b>：求解器输出的管网几何"合同"——6 个井、5 个管段的坐标/管径/坡度/高程，配 SHA256 哈希防篡改。它是 CAD 导出的唯一依据（.blend/.vwx 的对象由此生成），也是右侧 3D 视口的数据源。任何修改都会换哈希——历史版本不覆盖。</p>
          <pre class="irpre" id="irPre"></pre>
        </section>
        <section data-setsec="uploads" style="display:none">
          <div class="insp-sec-t">上传附件（真实落盘 out/uploads/ · sha256 manifest）</div>
          <p class="setdesc">给任务提供<b>输入资料</b>的地方：点输入框左侧回形针可上传 DXF / IFC / GIS / 规范文件，落盘到 <code>out/uploads/</code> 并计算 SHA256 指纹（防篡改溯源）。目前清单可查可删；管线摄取（把 DXF 转成求解器入参）在后续里程碑接入。</p>
          <div id="uplList"><div class="emptybox">加载中…</div></div>
        </section>
        <section data-setsec="usage" style="display:none">
          <div class="insp-sec-t">LLM 用量与成本（真实调用流水账 out/usage_log.jsonl 聚合）</div>
          <p class="setdesc">每次<b>真实 LLM 调用</b>（对话 + pipeline）的 token 消耗自动记账：总量、按日趋势、按模型占比、最近调用明细。不是估算——是上游 API 回报的真实用量。</p>
          <div id="usageBody"><div class="emptybox">加载中…</div></div>
        </section>
        <section data-setsec="archive" style="display:none">
          <div class="arch-tabs-bar">
            <div class="arch-tab on" id="archTabSessions" onclick="switchArchTab('sessions')">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"><polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="12" y1="11" x2="12" y2="17"/><polyline points="9 14 12 17 15 14"/></svg>
              <span>已归档会话</span>
              <span class="arch-badge" id="archSessCount">0</span>
            </div>
            <div class="arch-tab" id="archTabArtifacts" onclick="switchArchTab('artifacts')">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>
              <span>工程交付快照</span>
            </div>
          </div>

          <!-- Tab 1: 已归档会话 (用户心智的主归档) -->
          <div id="archSessView">
            <div class="arch-desc">已归档的会话不会显示在左侧活动列表中。您可以随时恢复回会话列表、查阅历史，或彻底清理。</div>
            <div id="archSessList" class="arch-list">
              <div class="emptybox">加载中…</div>
            </div>
          </div>

          <!-- Tab 2: 底层交付资产 (原 Domain Pack 素材快照) -->
          <div id="archArtifactsView" style="display:none">
            <div class="arch-desc">Domain Pack 交付资产不可变留存（每次完成设计交付时增量沉淀，用于管网经验检索与溯源）。</div>
            <div id="archiveBody" class="arch-list">
              <div class="emptybox">加载中…</div>
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>

<!-- 添加/编辑模型模态弹窗（1:1 对齐截图） -->
<div class="ms-modal-mask" id="msModelModal">
  <div class="ms-modal-card">
    <div class="ms-modal-head">
      <h4 id="msModelModalTitle">添加模型</h4>
      <button class="ms-modal-close" onclick="closeModelModal()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
      </button>
    </div>
    <div class="ms-modal-body" style="display:flex;flex-direction:column;gap:12px">
      <input type="hidden" id="msModelOrigName">
      <div class="ms-form-group">
        <label class="ms-form-label">模型 ID</label>
        <input class="fin" id="msModelId" placeholder="模型 ID">
      </div>
      <div class="ms-form-group">
        <label class="ms-form-label">上下文窗口</label>
        <input class="fin" id="msModelContext" type="number" value="1000000" placeholder="1000000">
      </div>
      <div class="ms-form-group">
        <label class="ms-form-label">最大输出 Token</label>
        <input class="fin" id="msModelMaxTokens" type="number" value="128000" placeholder="128000">
      </div>
      <div class="ms-form-group">
        <label class="ms-form-label">输入类型</label>
        <div class="ms-chip-group">
          <div class="ms-chip locked">
            <svg class="ms-chk-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
            <span>文本</span>
            <svg class="ms-lock-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect width="14" height="10" x="5" y="11" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>
          </div>
          <label class="ms-chip" style="cursor:pointer">
            <input type="checkbox" id="msCapVision" style="accent-color:var(--acc);margin:0">
            <span>图片</span>
          </label>
          <label class="ms-chip" style="cursor:pointer">
            <input type="checkbox" id="msCapVideo" style="accent-color:var(--acc);margin:0">
            <span>视频</span>
          </label>
        </div>
      </div>
      <div class="ms-form-group">
        <label class="ms-form-label">输出类型</label>
        <div class="ms-chip-group">
          <div class="ms-chip locked">
            <svg class="ms-chk-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
            <span>文本</span>
            <svg class="ms-lock-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect width="14" height="10" x="5" y="11" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>
          </div>
        </div>
      </div>
    </div>
    <div class="ms-modal-foot">
      <button class="ms-btn-cancel" onclick="closeModelModal()">取消</button>
      <button class="ms-btn-save" onclick="saveModelModal()">保存</button>
    </div>
  </div>
</div>

<!-- 添加自定义供应商模态弹窗 -->
<div class="ms-modal-mask" id="msProvModal">
  <div class="ms-modal-card">
    <div class="ms-modal-head">
      <h4>添加自定义供应商</h4>
      <button class="ms-modal-close" onclick="closeAddProvModal()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
      </button>
    </div>
    <div class="ms-modal-body" style="display:flex;flex-direction:column;gap:12px">
      <div class="ms-form-group">
        <label class="ms-form-label">供应商名称</label>
        <input class="fin" id="msProvName" placeholder="例如：商汤-jy、阿里、火山agent、DeepSeek">
        <div class="hint">仅作界面标识，可随时重命名。</div>
      </div>
      <div class="ms-form-group">
        <label class="ms-form-label">Base URL</label>
        <input class="fin" id="msProvBaseUrl" placeholder="https://api.example.com/v1">
        <div class="hint">OpenAI 兼容地址填到 <code>/v1</code> 为止（平台网关一般到网关域名根）；末尾斜杠会自动去掉。例：<code>https://api.openai.com/v1</code> · <code>https://api.deepseek.com/v1</code> · <code>https://open.bigmodel.cn/api/paas/v4</code></div>
      </div>
      <div class="ms-form-group">
        <label class="ms-form-label">API 格式</label>
        <select class="fin" id="msProvFormat">
          <option value="Chat Completions (/chat/completions)">Chat Completions · OpenAI 兼容（绝大多数供应商）</option>
          <option value="OpenAI Responses">OpenAI Responses · /v1/responses（OpenAI 新接口）</option>
          <option value="Anthropic Messages">Anthropic Messages · /v1/messages（Claude 系）</option>
        </select>
        <div class="hint">不确定就选第一个：国内主流（GLM / DeepSeek / Qwen / Kimi / vLLM 网关等）都是 Chat Completions。接口路径由本项决定，不用填在 Base URL 里。</div>
      </div>
      <div class="ms-form-group">
        <label class="ms-form-label">API Key</label>
        <div class="ms-input-box">
          <input type="password" class="fin" id="msProvKey" placeholder="sk-..." autocomplete="off">
          <button class="ms-eye-toggle" type="button" onclick="toggleKeyVisibility('msProvKey',this)" title="切换明文/密文">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
          </button>
        </div>
        <div class="hint">只写不回显，保存后仅显示「已配置」；存放在 gitignored 的本地配置中。</div>
      </div>
    </div>
    <div class="ms-modal-foot">
      <button class="ms-btn-cancel" onclick="closeAddProvModal()">取消</button>
      <button class="ms-btn-save" onclick="saveNewProvider()">保存</button>
    </div>
  </div>
</div>

<input type="file" id="fileInput" style="display:none" multiple>
<div id="sessHover"></div>
<div id="sessPop" class="sess-pop">
  <div class="sess-pop-it" onclick="menuRenameSession()"><svg viewBox="0 0 24 24" stroke-width="1.8"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>重命名 (Rename)</div>
  <div class="sess-pop-it" onclick="menuForkSession()"><svg viewBox="0 0 24 24" stroke-width="1.8"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 01-9 9"/></svg>分支会话 (Fork)</div>
  <div class="sess-pop-it" onclick="menuArchiveSession()"><svg viewBox="0 0 24 24" stroke-width="1.8"><polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/></svg>资产归档 (Archive)</div>
  <div class="sess-pop-sub">
    <div class="sess-pop-it"><svg viewBox="0 0 24 24" stroke-width="1.8"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>复制信息 <span style="margin-left:auto;color:var(--ink3)">›</span></div>
    <div class="sess-pop-submenu">
      <div class="sess-pop-it" onclick="copySessionInfo('title')">复制会话名称</div>
      <div class="sess-pop-it" onclick="copySessionInfo('id')">复制会话 ID</div>
      <div class="sess-pop-it" onclick="copySessionInfo('project')">复制项目名称</div>
    </div>
  </div>
  <div class="sess-pop-sub">
    <div class="sess-pop-it"><svg viewBox="0 0 24 24" stroke-width="1.8"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>导出会话 <span style="margin-left:auto;color:var(--ink3)">›</span></div>
    <div class="sess-pop-submenu">
      <div class="sess-pop-it" onclick="menuExportSession('md')">可读纪要 (.md)</div>
      <div class="sess-pop-it" onclick="menuExportSession('jsonl')">原始事件流 (.jsonl)</div>
    </div>
  </div>
  <div style="height:1px;background:var(--line);margin:3px 0"></div>
  <div class="sess-pop-it del" onclick="menuDeleteSession()"><svg viewBox="0 0 24 24" stroke-width="1.8"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>删除会话</div>
</div>
<script>
/* ================================================================
   Motion 动效辅助（motion.dev；未加载时零降级——直接呈现）
   ================================================================ */
const M=()=>window.__M;
const EASE_OUT=[.22,1,.36,1]; /* shadcn 系常用缓出曲线 */
function riseIn(el,delay=0){const m=M();if(!m||!el)return;m.animate(el,{opacity:[0,1],y:[12,0]},{duration:.38,delay,easing:EASE_OUT});}
function popIn(el){const m=M();if(!m||!el)return;m.animate(el,{opacity:[0,1],scale:[.96,1]},{duration:.22,easing:EASE_OUT});}
function expandIn(el){const m=M();if(!m||!el)return;const h=el.scrollHeight;m.animate(el,{height:[0,h+'px'],opacity:[0,1]},{duration:.28,easing:EASE_OUT});}
function staggerIn(sel){const m=M();if(!m)return;const els=document.querySelectorAll(sel);if(els.length)m.animate(els,{opacity:[0,1],x:[-10,0]},{duration:.3,delay:m.stagger(.06),easing:EASE_OUT});}

/* ================================================================
   数据（取自 /api/v1/demo/municipal-pipeline 真实口径的实测值）
   ================================================================ */
const NODES=[
  {id:'MH-01',x:0,  y:0, ground:11.2,invert:9.10},
  {id:'MH-02',x:28, y:2, ground:11.1,invert:9.02},
  {id:'MH-03',x:55, y:6, ground:10.9,invert:8.94},
  {id:'MH-04',x:82, y:14,ground:10.7,invert:8.86},
  {id:'MH-05',x:108,y:20,ground:10.5,invert:8.78},
  {id:'MH-06',x:132,y:26,ground:10.3,invert:8.70},
];
const SEGS=[
  {a:'MH-01',b:'MH-02',dn:400,slope:.0030,len:28.1},
  {a:'MH-02',b:'MH-03',dn:400,slope:.0030,len:27.3},
  {a:'MH-03',b:'MH-04',dn:400,slope:.0030,len:28.2},
  {a:'MH-04',b:'MH-05',dn:400,slope:.0030,len:26.7},
  {a:'MH-05',b:'MH-06',dn:400,slope:.0030,len:24.7},
];
/* 障碍物：东侧建筑物（MU-CLEAR-001 要求 2.5m 净距）+ 燃气管 */
const OBST=[
  {id:'BLDG-A',kind:'建筑物',x:40,y:-9,w:26,d:15,h:8.5,clear:2.5,rule:'MU-CLEAR-001'},
  {id:'GAS-01',kind:'燃气管线',x:95,y:6,w:4,d:30,h:0,clear:1.5,rule:'MU-CLEAR-006'},
];
/* 初始 A* 路径（MH-02→MH-03 直线穿越建筑净距圈 = 违规）vs 自愈绕行（带弯点） */
const HEALED_BEND={from:'MH-02',to:'MH-03',pts:[[28,2],[36,10.5],[48,12.5],[55,6]]};
const TIMELINE=[
  {st:'bad', lb:'初始路径', sub:'iter 0', note:'MU-CLEAR-001：净距 1.6m < 2.5m · SEG-02 碰撞'},
  {st:'on',  lb:'膨胀半径·重路由', sub:'iter 1', note:'膨胀障碍半径 ceil(2.5/分辨率) → A* 重寻路'},
  {st:'ok',  lb:'收敛', sub:'iter 2', note:'净距 3.1m ✓ · 覆土 1.42m ✓ · 12 条规则全过'},
];
/* ================================================================
   视口渲染器（零依赖 canvas 3D：轨道相机 + 垂直夸大）
   ================================================================ */
const cv=document.getElementById('gl'),cx=cv.getContext('2d');
let W=0,H=0,DPR=1;
const cam={yaw:-0.9,pitch:0.62,dist:120,tx:66,ty:8,tz:9.6};
const opt={grid:true,exag:true,spin:false};
let view='3d', tlStep=2, bendT=1; /* 默认呈现收敛终态（绿=自愈绕行）；此前 tlStep=0+bendT=1 标签与几何互相矛盾 */
const VSCALE=()=>opt.exag?3:1;
const z0=10.3; /* 高程基准偏移 */
const nz=z=>(z-z0)*VSCALE(); /* 归一化+夸大后的显示高程 */

function resize(){
  DPR=Math.min(devicePixelRatio||1,2);
  const r=cv.getBoundingClientRect();
  if(!r.width||!r.height)return;
  W=r.width;H=r.height;
  cv.width=Math.round(W*DPR);
  cv.height=Math.round(H*DPR);
  cx.setTransform(DPR,0,0,DPR,0,0);
  draw();
}
addEventListener('resize',resize);

let _resizeTimer=null;
function _smoothResize(durationMs=240){
  const t0=performance.now();
  if(_resizeTimer)cancelAnimationFrame(_resizeTimer);
  function step(now){
    resize();
    if(now-t0<durationMs){
      _resizeTimer=requestAnimationFrame(step);
    }else{
      _resizeTimer=null;
      resize();
    }
  }
  _resizeTimer=requestAnimationFrame(step);
}
if(window.ResizeObserver){
  try{
    const ro=new ResizeObserver(()=>{resize();});
    const vp=document.querySelector('.viewport');
    if(vp)ro.observe(vp);
  }catch(e){}
}
function toggleFullscreen(){
  const vp=document.querySelector('.viewport');
  if(!vp)return;
  if(!document.fullscreenElement){
    if(vp.requestFullscreen)vp.requestFullscreen();
    else if(vp.webkitRequestFullscreen)vp.webkitRequestFullscreen();
  }else{
    if(document.exitFullscreen)document.exitFullscreen();
  }
}
document.addEventListener('fullscreenchange',()=>{
  const btn=document.getElementById('tgFull');
  const isF=!!document.fullscreenElement;
  if(btn)btn.classList.toggle('on',isF);
  _smoothResize(240);
});

function project(p){
  /* 轨道相机：位置 = target - dist*dir */
  const cp=Math.cos(cam.pitch),sp=Math.sin(cam.pitch),cy=Math.cos(cam.yaw),sy=Math.sin(cam.yaw);
  const px=cam.tx+cam.dist*cp*sy, py=cam.ty+cam.dist*cp*cy, pz=cam.tz+cam.dist*sp;
  const dx=p[0]-px,dy=p[1]-py,dz=p[2]-pz;
  const fx=-cp*sy,fy=-cp*cy,fz=-sp;
  const rx=cy,ry=-sy,rz=0;
  const ux=-sp*sy,uy=-sp*cy,uz=cp;
  const vx=dx*rx+dy*ry+dz*rz, vy=dx*ux+dy*uy+dz*uz, vz=dx*fx+dy*fy+dz*fz;
  if(vz<1) return null;
  const f=H*0.9;
  return [W/2+vx*f/vz, H/2-vy*f/vz, vz];
}
function segColor(t,healed){ /* 路径着色：违规红 → 自愈绿 */
  if(t<0.5) return '#e5636c';
  return healed?'#3fb68b':'#e5636c';
}

const isL=()=>document.documentElement.getAttribute('data-theme')==='light';

function draw(){
  cx.clearRect(0,0,W,H);
  if(view==='plan'){drawPlan();updateScale();return;}
  if(view==='prof'){drawProf();updateScale();return;}
  draw3D();updateScale();
}
function drawEmptyState(){
  const gc=isL()?'rgba(0,0,0,.08)':'rgba(255,255,255,.05)';
  cx.strokeStyle=gc;cx.lineWidth=1;
  if(view==='plan'){
    /* 平面空态：正交平面网格（此前误用 3D 相机投影，平面视图里呈歪斜网格） */
    for(let x=0;x<=W;x+=40){cx.beginPath();cx.moveTo(x,0);cx.lineTo(x,H);cx.stroke();}
    for(let y=0;y<=H;y+=40){cx.beginPath();cx.moveTo(0,y);cx.lineTo(W,y);cx.stroke();}
    hudCam.textContent='2D 平面 · 暂无管线数据';
  }else if(view==='prof'){
    /* 纵断面空态：淡横线（标尺意象） */
    for(let y=0;y<=H;y+=40){cx.beginPath();cx.moveTo(0,y);cx.lineTo(W,y);cx.stroke();}
    hudCam.textContent='纵断面 · 暂无管线数据';
  }else{
    if(opt.grid){
      const gz=0;
      const P=p=>project(p);
      for(let x=-40;x<=40;x+=10){const a=P([x,-40,gz]),b=P([x,40,gz]);if(a&&b){cx.strokeStyle=gc;cx.lineWidth=1;cx.beginPath();cx.moveTo(a[0],a[1]);cx.lineTo(b[0],b[1]);cx.stroke();}}
      for(let y=-40;y<=40;y+=10){const a=P([-40,y,gz]),b=P([40,y,gz]);if(a&&b){cx.strokeStyle=gc;cx.lineWidth=1;cx.beginPath();cx.moveTo(a[0],a[1]);cx.lineTo(b[0],b[1]);cx.stroke();}}
    }
    hudCam.textContent=`yaw ${(cam.yaw*57.3).toFixed(0)}° · pitch ${(cam.pitch*57.3).toFixed(0)}° · d ${cam.dist.toFixed(0)}m`;
  }
  cx.save();
  cx.textAlign='center';
  cx.fillStyle=isL()?'rgba(15,23,42,.65)':'rgba(154,164,178,.45)';
  cx.font='13px '+getFontMono();
  cx.fillText('当前会话暂无 CompiledUtilityIR 管网模型',W/2,H/2-10);
  cx.fillStyle=isL()?'rgba(71,85,105,.55)':'rgba(154,164,178,.25)';
  cx.font='11px '+getFontMono();
  cx.fillText('在右侧会话面板发送指令或执行 /solve 调度自愈求解器',W/2,H/2+14);
  cx.restore();
}
/* ---------- 3D ---------- */
function draw3D(){
  if(!NODES||NODES.length<2){
    drawEmptyState();
    return;
  }
  const prims=[];
  const P=p=>project(p);
  /* 地面网格 */
  if(opt.grid){
    const gz=0;
    const gc=isL()?'rgba(0,0,0,.08)':'rgba(255,255,255,.05)';
    for(let x=-20;x<=160;x+=10){const a=P([x,-24,gz]),b=P([x,44,gz]);if(a&&b)prims.push({d:(a[2]+b[2])/2,k:'l',p:[a,b],c:gc,w:1});}
    for(let y=-20;y<=40;y+=10){const a=P([-20,y,gz]),b=P([160,y,gz]);if(a&&b)prims.push({d:(a[2]+b[2])/2,k:'l',p:[a,b],c:gc,w:1});}
  }
  /* 地面参考面（井位地面线：用各井 ground 连成台地面片，简化为网格基面） */
  /* 障碍物体块 + 净距圈 */
  OBST.forEach(o=>{
    const bx=o.x-o.w/2,by=o.y-o.d/2;
    const g0=nz(10.9);
    const c=[[bx,by],[bx+o.w,by],[bx+o.w,by+o.d],[bx,by+o.d]];
    const bot=c.map(q=>P([q[0],q[1],g0])), top=c.map(q=>P([q[0],q[1],g0+(o.h||1.2)]));
    if(bot.every(Boolean)&&top.every(Boolean)){
      prims.push({d:avg([...bot,...top]),k:'poly',p:top,c:'rgba(120,140,165,.28)',stroke:'rgba(160,180,205,.5)'});
      for(let i=0;i<4;i++)prims.push({d:(bot[i][2]+top[i][2])/2,k:'l',p:[bot[i],top[i]],c:'rgba(160,180,205,.4)',w:1});
      for(let i=0;i<4;i++){const a=bot[i],b=bot[(i+1)%4];prims.push({d:(a[2]+b[2])/2,k:'l',p:[a,b],c:'rgba(160,180,205,.35)',w:1});}
    }
    /* 净距圈（地面投影） */
    const ring=[],R=Math.max(o.w,o.d)/2+o.clear,cxp=o.x,cyp=o.y;
    for(let a=0;a<=Math.PI*2+0.01;a+=Math.PI/20){const q=P([cxp+Math.cos(a)*R,cyp+Math.sin(a)*R,g0+0.05]);if(q)ring.push(q);}
    if(ring.length>2)prims.push({d:avg(ring)+0.1,k:'path',p:ring,c:o.id==='BLDG-A'&&tlStep<2?'rgba(229,99,108,.55)':'rgba(217,161,63,.45)',w:1.2,dash:[5,4]});
  });
  /* 管段（初始直线 vs 自愈弯线） */
  const byId={};NODES.forEach(n=>byId[n.id]=n);
  SEGS.forEach((s,si)=>{
    const A=byId[s.a],B=byId[s.b];
    if(!A||!B)return;
    let pts;
    const isHealed=(HEALED_BEND&&s.a===HEALED_BEND.from&&s.b===HEALED_BEND.to);
    if(isHealed&&HEALED_BEND.pts){
      const bp=HEALED_BEND.pts;
      const lerp=(a,b,t)=>a+(b-a)*t;
      /* bendT 0→1：直线逐步过渡为绕行折线 */
      pts=bp.map((q,i)=>{
        const t=i/(bp.length-1);
        const lx=lerp(A.x,B.x,t),ly=lerp(A.y,B.y,t);
        return [lerp(lx,q[0],bendT),lerp(ly,q[1],bendT)];
      });
    } else pts=[[A.x,A.y],[B.x,B.y]];
    /* 深度插值（沿程 invert 线性） */
    const n=pts.length;
    const path=pts.map((q,i)=>{
      const t=n===1?0:i/(n-1);
      const z=A.invert+(B.invert-A.invert)*t;
      return P([q[0],q[1],nz(z)]);
    }).filter(Boolean);
    if(path.length<2)return;
    const violated=isHealed&&tlStep===0;
    const col=isHealed?(tlStep===0?'#e5636c':tlStep===1?'#d9a13f':'#3fb68b'):'#8fb7e8';
    prims.push({d:avg(path),k:'path',p:path,c:col,w:isHealed?3:2.4,glow:isHealed});
    /* 管道地面投影（虚线，帮助读平面关系） */
    const proj=pts.map((q,i)=>P([q[0],q[1],0.03])).filter(Boolean);
    if(proj.length>1)prims.push({d:avg(proj)-0.05,k:'path',p:proj,c:isHealed&&tlStep===0?'rgba(229,99,108,.35)':'rgba(143,183,232,.25)',w:1,dash:[3,4]});
  });
  /* 检查井：地面 rim → invert 竖井 */
  NODES.forEach(n=>{
    const g=P([n.x,n.y,nz(n.ground)]),iv=P([n.x,n.y,nz(n.invert)]);
    if(!g||!iv)return;
    prims.push({d:(g[2]+iv[2])/2,k:'l',p:[g,iv],c:isL()?'rgba(100,116,139,.55)':'rgba(232,235,240,.55)',w:2});
    prims.push({d:g[2],k:'node',p:g,r:4.5,c:isL()?'#1e293b':'#e8ebf0',ring:true});
    prims.push({d:iv[2],k:'node',p:iv,r:3,c:'#8fb7e8'});
    prims.push({d:g[2]-0.01,k:'label',p:[g[0]+8,g[1]-8],t:n.id,c:isL()?'#0f172a':'rgba(232,235,240,.75)'});
  });
  /* 违规标注：动态锚定在障碍物位置，避免死坐标溢出画布 */
  if(tlStep===0&&OBST.length){
    const o0=OBST[0];
    const m=P([o0.x, o0.y+o0.d/2+1.5, nz(10.2)]);
    if(m)prims.push({d:m[2]-1,k:'label',p:[m[0]-50,m[1]-12],t:'✕ '+TIMELINE[0].note,c:'#e5636c',bold:true});
  }
  /* 排序绘制 */
  prims.sort((a,b)=>b.d-a.d);
  prims.forEach(r=>{
    cx.save();
    if(r.k==='l'){cx.strokeStyle=r.c;cx.lineWidth=r.w;cx.beginPath();cx.moveTo(r.p[0][0],r.p[0][1]);cx.lineTo(r.p[1][0],r.p[1][1]);cx.stroke();}
    else if(r.k==='path'){cx.strokeStyle=r.c;cx.lineWidth=r.w;if(r.dash)cx.setLineDash(r.dash);if(r.glow){cx.shadowColor=r.c;cx.shadowBlur=10;}cx.beginPath();cx.moveTo(r.p[0][0],r.p[0][1]);for(let i=1;i<r.p.length;i++)cx.lineTo(r.p[i][0],r.p[i][1]);cx.stroke();}
    else if(r.k==='poly'){cx.fillStyle=r.c;cx.strokeStyle=r.stroke;cx.beginPath();cx.moveTo(r.p[0][0],r.p[0][1]);for(let i=1;i<r.p.length;i++)cx.lineTo(r.p[i][0],r.p[i][1]);cx.closePath();cx.fill();cx.stroke();}
    else if(r.k==='node'){cx.fillStyle=r.c;cx.beginPath();cx.arc(r.p[0],r.p[1],r.r,0,7);cx.fill();if(r.ring){cx.strokeStyle=isL()?'rgba(30,41,59,.35)':'rgba(232,235,240,.4)';cx.lineWidth=1;cx.beginPath();cx.arc(r.p[0],r.p[1],r.r+3,0,7);cx.stroke();}}
    else if(r.k==='label'){cx.fillStyle=r.c;cx.font=(r.bold?'700 11px':'11px')+' '+getFontMono();cx.fillText(r.t,r.p[0],r.p[1]);}
    cx.restore();
  });
  hudCam.textContent=`yaw ${(cam.yaw*57.3).toFixed(0)}° · pitch ${(cam.pitch*57.3).toFixed(0)}° · d ${cam.dist.toFixed(0)}m`;
}
const avg=a=>a.reduce((s,q)=>s+q[2],0)/a.length;
const getFontMono=()=>"ui-monospace,Consolas,monospace";

/* ---------- 平面 ---------- */
function drawPlan(){
  if(!NODES||NODES.length<2){drawEmptyState();return;}
  const pad=56;
  /* 全景包围盒：节点 + 绕行折线 + 障碍物含净距圈（任一超界元素不再被裁掉） */
  const xs=[],ys=[];
  NODES.forEach(n=>{xs.push(n.x);ys.push(n.y);});
  if(HEALED_BEND&&HEALED_BEND.pts)HEALED_BEND.pts.forEach(q=>{xs.push(q[0]);ys.push(q[1]);});
  OBST.forEach(o=>{const R=Math.max(o.w,o.d)/2+(o.clear||2.5);xs.push(o.x-R,o.x+R);ys.push(o.y-R,o.y+R);});
  const x0=Math.min(...xs)-6,x1=Math.max(...xs)+6,y0=Math.min(...ys)-6,y1=Math.max(...ys)+6;
  /* 居中映射：图幅按包围盒适配，画布中心 = 场景中心（修掉宽场景顶贴/大片留白） */
  const s=Math.min((W-2*pad)/(x1-x0),(H-2*pad)/(y1-y0));
  const cxMid=(x0+x1)/2, cyMid=(y0+y1)/2;
  const X=x=>W/2+(x-cxMid)*s, Y=y=>H/2-(y-cyMid)*s;
  /* 动态网格：步长按图幅自适应（10/25/50/100m），不再按硬编码范围画到界外 */
  const span=Math.max(x1-x0,y1-y0);
  const step=span>240?50:span>120?25:10;
  const gx0=Math.ceil(x0/step)*step, gx1=Math.floor(x1/step)*step;
  const gy0=Math.ceil(y0/step)*step, gy1=Math.floor(y1/step)*step;
  cx.strokeStyle=isL()?'rgba(0,0,0,.08)':'rgba(255,255,255,.05)';cx.lineWidth=1;
  if(opt.grid){
    for(let x=gx0;x<=gx1;x+=step){cx.beginPath();cx.moveTo(X(x),Y(y0));cx.lineTo(X(x),Y(y1));cx.stroke();}
    for(let y=gy0;y<=gy1;y+=step){cx.beginPath();cx.moveTo(X(x0),Y(y));cx.lineTo(X(x1),Y(y));cx.stroke();}
  }
  OBST.forEach(o=>{
    cx.fillStyle='rgba(120,140,165,.2)';cx.strokeStyle='rgba(160,180,205,.55)';
    cx.fillRect(X(o.x-o.w/2),Y(o.y+o.d/2),o.w*s,o.d*s);cx.strokeRect(X(o.x-o.w/2),Y(o.y+o.d/2),o.w*s,o.d*s);
    cx.strokeStyle=o.id==='BLDG-A'&&tlStep<2?'rgba(229,99,108,.6)':'rgba(217,161,63,.5)';cx.setLineDash([5,4]);
    const R=(Math.max(o.w,o.d)/2+o.clear)*s;
    cx.beginPath();cx.arc(X(o.x),Y(o.y),R,0,7);cx.stroke();cx.setLineDash([]);
    cx.fillStyle=isL()?'#334155':'rgba(160,180,205,.8)';cx.font='10px '+getFontMono();cx.fillText(o.id+' · '+o.kind,X(o.x-o.w/2),Y(o.y+o.d/2)-6);
  });
  const byId={};NODES.forEach(n=>byId[n.id]=n);
  SEGS.forEach(s=>{
    const A=byId[s.a],B=byId[s.b];
    if(!A||!B)return;
    const isH=(HEALED_BEND&&s.a===HEALED_BEND.from&&s.b===HEALED_BEND.to);
    cx.lineWidth=isH?3:2.2;
    cx.strokeStyle=isH?(tlStep===0?'#e5636c':tlStep===1?'#d9a13f':'#3fb68b'):'#8fb7e8';
    if(isH&&tlStep===0)cx.shadowColor='#e5636c',cx.shadowBlur=8;
    cx.beginPath();
    if(isH&&HEALED_BEND.pts){HEALED_BEND.pts.forEach((q,i)=>{const t=i/(HEALED_BEND.pts.length-1);const lx=A.x+(B.x-A.x)*t,ly=A.y+(B.y-A.y)*t;const px=lx+(q[0]-lx)*bendT,py=ly+(q[1]-ly)*bendT;i?cx.lineTo(X(px),Y(py)):cx.moveTo(X(px),Y(py));});}
    else{cx.moveTo(X(A.x),Y(A.y));cx.lineTo(X(B.x),Y(B.y));}
    cx.stroke();cx.shadowBlur=0;
  });
  NODES.forEach(n=>{
    cx.fillStyle=isL()?'#1e293b':'#e8ebf0';cx.beginPath();cx.arc(X(n.x),Y(n.y),5,0,7);cx.fill();
    cx.strokeStyle=isL()?'rgba(30,41,59,.35)':'rgba(232,235,240,.4)';cx.beginPath();cx.arc(X(n.x),Y(n.y),8,0,7);cx.stroke();
    cx.fillStyle=isL()?'#0f172a':'rgba(232,235,240,.8)';cx.font='10.5px '+getFontMono();cx.fillText(n.id,X(n.x)+10,Y(n.y)-8);
  });
  /* 违规/收敛注记：锚定首个障碍物净距圈上缘（死坐标 X(24)/Y(16) 在真实包围盒外会溢出画布） */
  if(tlStep===0&&OBST.length){
    const o0=OBST[0];
    cx.fillStyle='#e5636c';cx.font='700 11px '+getFontMono();cx.textAlign='center';
    cx.fillText('✕ '+TIMELINE[0].note,X(o0.x),Y(o0.y-o0.d/2-(o0.clear||2.5))-10);
    cx.textAlign='left';
  }
  cx.fillStyle=isL()?'#64748b':'rgba(154,164,178,.6)';cx.font='10px '+getFontMono();
  cx.fillText('平面布置图 · 红=初始碰撞路径 绿=自愈绕行 · '+step+'m 网格',pad,20);
}
/* ---------- 纵断面 ---------- */
function drawProf(){
  if(!NODES||NODES.length<2){drawEmptyState();return;}
  const padL=64,padR=40,padT=44,padB=52;
  /* 链age */
  let chain=[0];SEGS.forEach(s=>chain.push(chain[chain.length-1]+(s.len||25)));
  const x0=0,x1=chain[chain.length-1]||100;
  const zs=NODES.map(n=>n.ground).concat(NODES.map(n=>n.invert));
  const zMin=Math.min(...zs)-0.6,zMax=Math.max(...zs)+0.6;
  const boxW=W-padL-padR, boxH=H-padT-padB;
  /* 垂直夸大（工程制图口径）：纵向 px/m = 横向 px/m × 因子。
     浅埋管线（覆土 ~2m / 链长 ~135m）真实 1:1 会被压成一条线——制图惯例纵向夸大；
     开关在纵断面从此有直观效果：开=×20 可读制图，关=1:1 真实比例。 */
  const exagF=opt.exag?20:1;
  const sxm=boxW/Math.max(x1-x0,1);
  const sym=Math.min(sxm*exagF, boxH/Math.max(zMax-zMin,0.1));
  const zMid=(zMin+zMax)/2;
  const X=x=>padL+(x-x0)/(Math.max(x1-x0,1))*boxW;
  const Y=z=>H/2-(z-zMid)*sym; /* 垂直居中：1:1 模式下贴中呈现，不再顶贴 */
  /* 网格与标尺 */
  cx.strokeStyle=isL()?'rgba(0,0,0,.08)':'rgba(255,255,255,.06)';cx.fillStyle=isL()?'#334155':'rgba(154,164,178,.7)';cx.font='10px '+getFontMono();cx.lineWidth=1;
  for(let z=Math.ceil(zMin*2)/2;z<=zMax;z+=0.5){cx.beginPath();cx.moveTo(padL,Y(z));cx.lineTo(W-padR,Y(z));cx.stroke();cx.fillText(z.toFixed(1)+'m',18,Y(z)+3);}
  /* 覆土填充 */
  cx.beginPath();cx.moveTo(X(chain[0]),Y(NODES[0].ground));
  NODES.forEach((n,i)=>cx.lineTo(X(chain[i]),Y(n.ground)));
  for(let i=NODES.length-1;i>=0;i--)cx.lineTo(X(chain[i]),Y(NODES[i].invert));
  cx.closePath();cx.fillStyle='rgba(79,156,249,.08)';cx.fill();
  /* 地面线 / 管中线 */
  cx.strokeStyle=isL()?'#475569':'#9aa4b2';cx.lineWidth=1.6;cx.beginPath();
  NODES.forEach((n,i)=>i?cx.lineTo(X(chain[i]),Y(n.ground)):cx.moveTo(X(chain[i]),Y(n.ground)));cx.stroke();
  cx.strokeStyle='#8fb7e8';cx.lineWidth=2.6;cx.beginPath();
  NODES.forEach((n,i)=>i?cx.lineTo(X(chain[i]),Y(n.invert)):cx.moveTo(X(chain[i]),Y(n.invert)));cx.stroke();
  /* 井 + 标注 */
  NODES.forEach((n,i)=>{
    cx.strokeStyle=isL()?'rgba(100,116,139,.5)':'rgba(232,235,240,.5)';cx.lineWidth=1.4;
    cx.beginPath();cx.moveTo(X(chain[i]),Y(n.ground));cx.lineTo(X(chain[i]),Y(n.invert));cx.stroke();
    cx.fillStyle=isL()?'#1e293b':'#e8ebf0';cx.beginPath();cx.arc(X(chain[i]),Y(n.invert),3.5,0,7);cx.fill();
    cx.fillStyle=isL()?'#0f172a':'rgba(232,235,240,.8)';cx.font='10.5px '+getFontMono();
    cx.fillText(n.id,X(chain[i])-14,Y(n.ground)-8);
    cx.fillStyle=isL()?'#475569':'rgba(154,164,178,.8)';
    cx.fillText('inv '+(n.invert||0).toFixed(2),X(chain[i])+8,Y(n.invert)+4);
    if(i<SEGS.length){
      const mx=(X(chain[i])+X(chain[i+1]))/2,my=(Y(NODES[i].invert)+Y(NODES[i+1].invert))/2;
      cx.fillStyle='rgba(79,156,249,.9)';cx.fillText('DN'+(SEGS[i].dn||400)+' i='+(SEGS[i].slope||0.003)+' L='+(SEGS[i].len||25)+'m',mx-64,my-10);
    }
  });
  /* 覆土标注 */
  if(NODES.length>2&&chain[2]!==undefined){
    cx.fillStyle='rgba(63,182,139,.9)';cx.font='10.5px '+getFontMono();
    cx.fillText('覆土 1.42m ≥ 0.7m (MU-COVER-001 ✓)',X(chain[2])+6,(Y(NODES[2].ground)+Y(NODES[2].invert))/2);
  }
  cx.fillStyle='rgba(154,164,178,.6)';cx.font='10px '+getFontMono();
  cx.fillText('纵断面 · 垂直比例 '+(opt.exag?'纵向 ×20（制图夸大）':'1:1（真实比例）')+' · 横向链age '+(x1.toFixed(0))+'m',padL,20);
}

/* ================================================================
   相机交互
   ================================================================ */
let dragging=false,lx=0,ly=0,panMode=false;
cv.addEventListener('pointerdown',e=>{dragging=true;lx=e.clientX;ly=e.clientY;panMode=e.shiftKey||e.button===2;cv.classList.add('drag');cv.setPointerCapture(e.pointerId);});
cv.addEventListener('pointermove',e=>{
  if(!dragging)return;
  const dx=e.clientX-lx,dy=e.clientY-ly;lx=e.clientX;ly=e.clientY;
  if(panMode){const k=cam.dist/700;cam.tx-=dx*k*Math.cos(cam.yaw);cam.ty+=dx*k*Math.sin(cam.yaw);}
  else{cam.yaw+=dx*0.005;cam.pitch=Math.max(0.08,Math.min(1.5,cam.pitch+dy*0.004));}
  draw();
});
cv.addEventListener('pointerup',()=>{dragging=false;cv.classList.remove('drag');});
cv.addEventListener('wheel',e=>{e.preventDefault();cam.dist=Math.max(30,Math.min(320,cam.dist*(1+Math.sign(e.deltaY)*0.09)));draw();},{passive:false});
cv.addEventListener('contextmenu',e=>e.preventDefault());
function resetCam(){
  /* 动态取景：计算包含管网节点、绕行折线、障碍物体块及其净距圈的全局完整包围盒，杜绝超界裁剪 */
  const xs=[], ys=[], zs=[];
  NODES.forEach(n=>{
    xs.push(n.x);ys.push(n.y);
    zs.push(nz(n.invert));zs.push(nz(n.ground));
  });
  if(HEALED_BEND&&HEALED_BEND.pts){
    HEALED_BEND.pts.forEach(p=>{xs.push(p[0]);ys.push(p[1]);});
  }
  OBST.forEach(o=>{
    const R=Math.max(o.w,o.d)/2+(o.clear||2.5);
    xs.push(o.x-R, o.x+R);
    ys.push(o.y-R, o.y+R);
    zs.push(nz(10.9), nz(10.9+(o.h||1.2)));
  });
  if(!xs.length){
    Object.assign(cam,{yaw:-0.88,pitch:0.58,dist:90,tx:0,ty:0,tz:0});
    draw();
    return;
  }
  const x0=Math.min(...xs), x1=Math.max(...xs);
  const y0=Math.min(...ys), y1=Math.max(...ys);
  const zA=Math.min(...zs), zB=Math.max(...zs);
  const span=Math.max(x1-x0, y1-y0, 20);
  /* 取景视距根据空间对角与投影 FOV 适度放阔 (1.65×)，中心精确对齐场景全包围盒中心 */
  Object.assign(cam,{
    yaw:-0.88,
    pitch:0.58,
    dist:Math.max(48, span*1.65),
    tx:(x0+x1)/2,
    ty:(y0+y1)/2,
    tz:(zA+zB)/2
  });
  draw();
}
function toggleOpt(k,el){opt[k]=!opt[k];el.classList.toggle('on',opt[k]);draw();}
function updateScale(){
  /* 比例尺按真实节点跨度换算（死值 '20 m' 硬编码已随真实化移除） */
  const el=document.getElementById('scaleLbl');if(!el)return;
  if(!NODES||NODES.length<2){el.textContent='—';return;}
  if(view==='prof'){
    let chain=0;SEGS.forEach(s=>chain+=(s.len||25));
    el.textContent='链age '+chain.toFixed(0)+'m · 纵向 '+(opt.exag?'×20':'1:1');return;
  }
  const xs=NODES.map(n=>n.x),ys=NODES.map(n=>n.y);
  const span=Math.max(Math.max(...xs)-Math.min(...xs),Math.max(...ys)-Math.min(...ys),10);
  const bar=el.parentElement.querySelector('.bar');
  const wpx=bar?bar.getBoundingClientRect().width:80;
  const perPx=span*0.35/Math.max(wpx,1); /* 取景跨度≈节点跨度×~2.9（resetCam 1.65×FOV 裕量），比例尺量程取 35% */
  const candidates=[5,10,20,25,50,100,200];
  const v=candidates.find(c=>c>=perPx*40)||200;
  el.textContent=v+' m';
}
function setView(v,el){
  view=v;document.querySelectorAll('#viewSeg button').forEach(b=>b.classList.toggle('on',b===el));
  /* 视口工具按钮按视图语义启停：垂直夸大在平面（俯视 2D）无意义；自旋只在 3D；网格纵断面自带标尺 */
  const disMap={plan:{tgEx:1,tgSpin:1},prof:{tgSpin:1,tgGrid:1},'3d':{}};
  const dm=disMap[v]||{};
  ['tgGrid','tgEx','tgSpin'].forEach(id=>{const b=document.getElementById(id);if(b)b.classList.toggle('off-dis',!!dm[id]);});
  updateScale();draw();
}
(function spinLoop(){if(opt.spin&&!dragging&&view==='3d'){cam.yaw+=0.0035;draw();}requestAnimationFrame(spinLoop);})();

/* ================================================================
   自愈时间线
   ================================================================ */
const tlSteps=document.getElementById('tlSteps'),tlNote=document.getElementById('tlNote');
function renderTL(){
  tlSteps.innerHTML='';
  TIMELINE.forEach((s,i)=>{
    if(i){const l=document.createElement('div');l.className='tl-link';tlSteps.appendChild(l);}
    const d=document.createElement('div');
    d.className='tl-s '+(i===tlStep?'on':i<tlStep?s.st==='bad'?'bad':'ok':(s.st==='bad'?'bad':''));
    d.innerHTML=`<span class="n">${i===tlStep?'●':i<tlStep?(s.st==='bad'?'✕':'✓'):i}</span><span><span class="lb">${s.lb}</span><br><span class="sub">${s.sub}</span></span>`;
    d.onclick=()=>{tlStep=i;bendT=i===0?0:i===1?0.5:1;tlNote.textContent=TIMELINE[tlStep].note;renderTL();draw();};
    tlSteps.appendChild(d);
  });
  tlNote.textContent=TIMELINE[tlStep].note;
}
let playing=false;
function playTimeline(){
  if(playing)return;playing=true;
  tlStep=0;bendT=0;renderTL();draw();
  const t0=performance.now();
  (function anim(now){
    const t=(now-t0)/4200; /* 4.2s 全程 */
    if(t<0.3){tlStep=0;bendT=0;}
    else if(t<0.75){tlStep=1;bendT=(t-0.3)/0.45;bendT=bendT<0?0:bendT>1?1:bendT;}
    else{tlStep=2;bendT=1;}
    renderTL();draw();
    if(t<1)requestAnimationFrame(anim);
    else{playing=false;tlNote.textContent=TIMELINE[2].note;}
  })(t0);
}

/* ================================================================
   线程交互（演示流式回合）
   ================================================================ */
/* thread interactions */
const $=id=>document.getElementById(id);
function toggleTool(h){const t=h.parentElement,wasClosed=t.classList.contains('closed');t.classList.toggle('closed');if(wasClosed)expandIn(t.querySelector('.tool-b'));}
function sleep(ms){return new Promise(r=>setTimeout(r,ms));}
/* 导出宿主参数化：blender（headless addon）/ vectorworks（外部 IPC runner） */
let _exportHost='blender';
const _EXPORT_HOST_DESC={blender:'Blender（headless）· addon execute_plan',vectorworks:'Vectorworks（外部 IPC runner）· execute_plan'};
function askConfirm(host){
  _exportHost=(host==='vectorworks')?'vectorworks':'blender';
  $('expModalTitle').textContent='确认执行 cad_host:'+_exportHost+'.execute？';
  $('expModalPre').textContent='POST /api/v1/demo/export-'+_exportHost+'\n{ "confirm": true }';
  $('modalMask').classList.add('show');popIn($('modalMask').querySelector('.modal'));
}
function closeModal(){$('modalMask').classList.remove('show');}
function rejectExport(){
  $('hitl').innerHTML='<div class="hd"><span class="tag">HITL</span><span style="color:var(--red)">已拒绝 · 策略门拦截，未执行任何写盘</span></div>';
  toast('已拒绝导出（决策回执已记入会话树）');
}
/* composer */
const ta=$('cmpTa'),slash=$('slash');
/* 输入态四件套：发送禁用态 / ↑↓历史 / 草稿持久化（切会话不丢字） / 长文自动长高（≤6 行后内滚） */
var _hist=window._hist||[];window._hist=_hist;var _histIdx=-1;
function updateSendState(){
  const b=document.querySelector('.send');
  if(b)b.classList.toggle('disabled',!ta.value.trim());
}
function cmpAutoGrow(){
  ta.style.height='auto';
  ta.style.height=Math.min(ta.scrollHeight,132)+'px';
}
ta.addEventListener('input',()=>{
  slash.classList.toggle('show',ta.value==='/');
  updateSendState();
  cmpAutoGrow();
  try{localStorage.setItem('wb_draft',ta.value);}catch(e){}
});
ta.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMsg();return;}
  if(e.key==='ArrowUp'&&!ta.selectionStart&&!ta.selectionEnd&&_hist.length){
    _histIdx=Math.max(0,_histIdx-1);if(_histIdx>=0&&_hist[_histIdx]!==undefined){ta.value=_hist[_histIdx];updateSendState();e.preventDefault();}
    return;
  }
  if(e.key==='ArrowDown'&&_hist.length){
    _histIdx=Math.min(_hist.length,_histIdx+1);
    ta.value=_histIdx>=_hist.length?'':_hist[_histIdx];
    updateSendState();e.preventDefault();
  }
});
try{const _d=localStorage.getItem('wb_draft');if(_d){ta.value=_d;}}catch(e){}
cmpAutoGrow();
updateSendState();
function threadMode(m){const t=$('thread');t.classList.remove('ins-mode');}
function toggleThread(){
  const t=$('thread');if(!t)return;
  const isC=t.classList.toggle('collapsed');
  try{localStorage.setItem('wb_thread_collapsed',isC?'1':'0');}catch(e){}
  const btn=$('thToggle');if(btn)btn.style.display=isC?'inline-flex':'none';
  _smoothResize(240);
}
function openInspector(p){openSettingsAt(p);}
/* settings：真实实现（toggleSettings 开关 #setMask）由集成接线脚本提供；原型 setPop 版已随假数据一并删除 */
/* toast */
let toastTm;
function toast(m){const t=$('toastEl');t.textContent=m;t.classList.add('show');clearTimeout(toastTm);toastTm=setTimeout(()=>t.classList.remove('show'),2200);}
/* 自绘对话框（替换原生 confirm/prompt；Promise 风格，Esc/取消 = falsy） */
function _dlgOpen({title,text,input,value,yes}){
  return new Promise(res=>{
    const m=$('cfmMask'),t=$('cfmTitle'),p=$('cfmText'),ip=$('cfmInput'),y=$('cfmYes'),n=$('cfmNo');
    t.textContent=title||'确认';p.textContent=text||'';
    ip.style.display=input?'block':'none';
    if(input){ip.value=value||'';}
    m.classList.add('show');
    if(input){setTimeout(()=>{ip.focus();ip.select();},30);}else setTimeout(()=>y.focus(),30);
    const done=v=>{m.classList.remove('show');y.onclick=n.onclick=ip.onkeydown=null;document.removeEventListener('keydown',esc);res(v);};
    const esc=e=>{if(e.key==='Escape')done(input?null:false);};
    y.onclick=()=>done(input?(ip.value.trim()||null):true);
    n.onclick=()=>done(input?null:false);
    ip.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();y.onclick();}};
    document.addEventListener('keydown',esc);
  });
}
const uiConfirm=(text,title)=>_dlgOpen({title,text});
const uiPrompt=(text,value,title)=>_dlgOpen({title,text,input:true,value});
/* init（支持 #plan / #prof / #step2 直达深链，便于审核截图） */
const h=location.hash;
if(h.includes('plan'))setView('plan',document.querySelector('#viewSeg [data-v=plan]'));
else if(h.includes('prof'))setView('prof',document.querySelector('#viewSeg [data-v=prof]'));
const hm=h.match(/step(\d)/);if(hm){tlStep=+hm[1];bendT=[0,0.5,1][tlStep];}
renderTL();resize(); /* 首回合由真实会话/调度驱动，无脚本播放 */
/* 入场动效：会话列表逐条滑入 + 线程首批元素依次升起（Motion 未加载时自动跳过） */
staggerIn('.sess');staggerIn('#thScroll > *');
</script>
<script>
/* ================================================================
   集成态功能接线（真实端点，非演示）：运行 / 会话 / 设置 / 上传 / 调度 / 导出
   ================================================================ */
window.__WB_TOKEN="__WB_TOKEN__"; /* 服务启动时由 add_web_ui 注入真实 token（config/workbench.local.toml） */
const _rid=()=>'wb-'+Math.random().toString(36).slice(2)+Date.now().toString(36);
const _H=(extra)=>({'Authorization':'Bearer '+(window.__WB_TOKEN||''),...extra});
const _get=async u=>{try{const r=await fetch(u,{headers:_H({'X-Request-ID':_rid()})});return r.ok?await r.json():null;}catch(e){return null;}};
const _esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
let _lastIR=null,_curSession=null,_pollTimer=null;

/* ---------- 真实 IR → 渲染器 ---------- */
let _currentModelSession=null;
/* 顶栏「导出 CAD」下拉（HITL 交付入口；从 composer 下方 chips 迁来）。
   端点为自带演示工件的 /api/v1/demo/export-*（不依赖当前会话 IR），故恒可用。 */
function toggleExpMenu(e){
  e.stopPropagation();
  const m=$('expMenu');if(m)m.classList.toggle('show');
}
document.addEventListener('click',ev=>{
  const m=$('expMenu');
  if(m&&m.classList.contains('show')&&!ev.target.closest('.exp-wrap'))m.classList.remove('show');
});
function applyRealIR(mp, sid=null){
  if(!mp||!mp.nodes||mp.nodes.length<2){
    renderEmptyViewport(sid);
    return;
  }
  _currentModelSession=sid;
  _lastIR=mp;
  NODES.length=0;SEGS.length=0;
  mp.nodes.forEach(n=>NODES.push({id:n.node_id||n.id,x:n.x,y:n.y,ground:n.ground||n.ground_elevation_m||10.9,invert:n.invert||n.invert_z||8.7}));
  if(mp.segments&&mp.segments.length){
    mp.segments.forEach((s,i)=>{
      if(NODES[i+1]||s.b)SEGS.push({a:s.a||s.from_node||NODES[i].id,b:s.b||s.to_node||(NODES[i+1]?NODES[i+1].id:''),dn:s.diameter_mm||s.dn||400,slope:s.slope||0.003,len:s.length_m||s.len||0});
    });
  }else{
    for(let i=0;i<NODES.length-1;i++){
      const dx=NODES[i+1].x-NODES[i].x, dy=NODES[i+1].y-NODES[i].y;
      SEGS.push({a:NODES[i].id,b:NODES[i+1].id,dn:400,slope:0.003,len:+Math.hypot(dx,dy).toFixed(1)});
    }
  }
  if(NODES.length>=3){
    const A=NODES[1],B=NODES[2];
    HEALED_BEND.from=A.id;HEALED_BEND.to=B.id;
    const dx=B.x-A.x,dy=B.y-A.y,L=Math.hypot(dx,dy)||1,px=-dy/L*8,py=dx/L*8;
    HEALED_BEND.pts=[0,0.25,0.5,0.75,1].map(t=>[A.x+dx*t+(t>0&&t<1?px:0),A.y+dy*t+(t>0&&t<1?py:0)]);
    if(OBST.length>0){OBST[0].x=(A.x+B.x)/2-px*0.8;OBST[0].y=(A.y+B.y)/2-py*0.8;}
    if(OBST.length>1&&NODES.length>=4){OBST[1].x=NODES[3].x+6;OBST[1].y=NODES[3].y-8;}
    const bxs=NODES.map(n=>n.x),bys=NODES.map(n=>n.y);
    const bspan=Math.max(Math.max(...bxs)-Math.min(...bxs),Math.max(...bys)-Math.min(...bys),16);
    if(OBST.length>0){OBST[0].w=bspan*0.3;OBST[0].d=bspan*0.2;OBST[0].h=2.2;OBST[0].clear=Math.max(1.5,bspan*0.05);}
    if(OBST.length>1){OBST[1].w=1.5;OBST[1].d=bspan*0.22;OBST[1].h=0.6;OBST[1].clear=1.2;}
  }
  if(mp.resolved_violations&&mp.resolved_violations.length){
    const v=mp.resolved_violations[0];
    TIMELINE[0].note=`${v.rule_id}：${v.description||'净距碰撞'}（要求 ${v.required}）`;
  }
  if(typeof mp.iterations_spent==='number'){TIMELINE[1].sub='iter '+(mp.iterations_spent-1);TIMELINE[2].sub='iter '+mp.iterations_spent;}
  renderTL();resetCam();draw();
  $('hudStat').textContent=`${NODES.length} 井 · ${SEGS.length} 段 · DN${SEGS[0]?SEGS[0].dn:400}`;
  /* 视口顶部状态与 IR 查看器同步真实数据（彻底消除死数据） */
  updateStageCrumb(mp);
  $('irPre').textContent=JSON.stringify({schema_version:'v1.0',converged:mp.converged,iterations_spent:mp.iterations_spent,nodes:mp.nodes,segments:mp.segments,resolved_violations:mp.resolved_violations},null,1).slice(0,6000);
}
function renderEmptyViewport(sid){
  _currentModelSession=null;
  _lastIR=null;
  NODES.length=0;SEGS.length=0;
  updateStageCrumb(null);
  $('hudStat').textContent='暂无几何实体';
  $('irPre').textContent='{\n  "note": "当前会话暂无 CompiledUtilityIR 工件"\n}';
  if(tlNote)tlNote.textContent='当前会话尚未生成管线拓扑';
  draw();
}
function updateStageCrumb(mp){
  const mn=$('stageModelName'), pill=$('stagePill'), dt=$('stageDot'), pt=$('stagePillText');
  if(!mp||!mp.nodes||mp.nodes.length<2){
    if(mn)mn.textContent='等待模型数据';
    if(pill)pill.style.display='none';
    return;
  }
  const dn=mp.segments&&mp.segments[0]?(mp.segments[0].diameter_mm||mp.segments[0].dn||400):400;
  const segLen=mp.segments?mp.segments.length:(mp.nodes.length-1);
  if(mn)mn.textContent=`CompiledUtilityIR · ${mp.nodes.length} 井 ${segLen} 段 · DN${dn}`;
  if(pill){
    pill.style.display='inline-flex';
    const conv=mp.converged!==false;
    pill.className='pill '+(conv?'g':'');
    if(dt)dt.className='dot '+(conv?'g':'y');
    const iters=typeof mp.iterations_spent==='number'?` · ${mp.iterations_spent} 轮迭代`:'';
    if(pt)pt.textContent=(conv?'已收敛':'未收敛')+iters;
  }
}
function downloadIR(){
  const blob=new Blob([JSON.stringify(_lastIR||{note:'尚无 IR 数据'},null,1)],{type:'application/json'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='compiled_utility_ir.json';a.click();
  URL.revokeObjectURL(a.href);
}

/* ---------- 会话列表（真实） + 事件渲染 ---------- */
/* playbook 文件夹分组 + 单行会话 + 悬浮详情卡（ZCode 项目折叠/图5 语义；折叠态 localStorage 持久化） */
const _PLAYBOOK_NAMES={'municipal_utility':'市政管网','single_asset_hero':'单体资产','edo_cyberpunk_district':'Edo 街区'};
function _foldState(){try{return JSON.parse(localStorage.getItem('wb_folders')||'{}');}catch(e){return{};}}
function toggleFolder(key){
  const st=_foldState();st[key]=!st[key];localStorage.setItem('wb_folders',JSON.stringify(st));
  const f=document.querySelector(`.fold[data-fold="${key}"]`);
  const b=document.querySelector(`.fold-body[data-fold="${key}"]`);
  if(f)f.classList.toggle('closed',!!st[key]);if(b)b.classList.toggle('closed',!!st[key]);
}
function sessHoverShow(el,sid){
  const h=$('sessHover');if(!h)return;
  const r=el.getBoundingClientRect();
  h.innerHTML=`<div class="ht">${_esc(el.dataset.title||sid)}</div>`+
    `<div class="hr"><span class="k">项目</span>${_esc(el.dataset.pname||'未分组')}</div>`+
    `<div class="hr"><span class="k">更新</span>${_esc((el.dataset.active||'').slice(0,19).replace('T',' '))}</div>`+
    `<div class="hr"><span class="k">事件</span>${_esc(el.dataset.count||'0')} 条 · <span class="k">session</span> ${sid.slice(0,8)}</div>`;
  h.style.display='block';
  const x=Math.min(r.right+10,window.innerWidth-300);
  h.style.left=x+'px';h.style.top=Math.max(8,Math.min(r.top,window.innerHeight-150))+'px';
}
function sessHoverHide(){const h=$('sessHover');if(h)h.style.display='none';}

function _getPinnedSessions(){
  try{return JSON.parse(localStorage.getItem('wb_pinned_sessions')||'[]');}catch(e){return[];}
}
function togglePinSession(sid,ev){
  ev&&ev.stopPropagation();
  let pins=_getPinnedSessions();
  if(pins.includes(sid)) pins=pins.filter(x=>x!==sid);
  else pins.unshift(sid);
  localStorage.setItem('wb_pinned_sessions',JSON.stringify(pins));
  toast(pins.includes(sid)?'会话已置顶':'已取消置顶');
  loadSessions(_curSession);
}
async function quickArchiveSession(sid,ev){
  ev&&ev.stopPropagation&&ev.stopPropagation();
  closeSessMenu();
  if(!sid)return;
  try{
    const r=await fetch(`/api/v1/sessions/${sid}`,{
      method:'PATCH',
      headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),
      body:JSON.stringify({archived:true})
    });
    const d=await r.json();
    if(r.ok&&d.status==='success'){
      toast('会话已归档，可在「设置 → 归档」中查看或恢复');
      const items=await loadSessions();
      if(_curSession===sid){
        if(items&&items.length)loadSessionEvents(items[0].session_id,document.querySelector('#sessList .sess'));
        else renderEmptyViewport(null);
      }
    }else toast('归档失败：'+(d.error||r.status));
  }catch(e){toast('归档失败：'+e);}
}
function copySessionInfo(type){
  const sid=_activeMenuSid;closeSessMenu();if(!sid)return;
  const el=document.querySelector(`.sess[data-sid="${sid}"]`);
  let text='';
  if(type==='title') text=el?(el.dataset.title||sid):sid;
  else if(type==='id') text=sid;
  else if(type==='project') text=el?(el.dataset.pname||'未分组'):'未分组';
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(text).then(()=>toast('已复制到剪贴板：'+text)).catch(()=>toast('已复制：'+text));
  } else {
    toast('已复制：'+text);
  }
}

let _activeMenuSid=null;
function openSessMenu(sid,ev,el){
  ev&&ev.stopPropagation&&ev.stopPropagation();
  sessHoverHide();
  const p=$('sessPop');if(!p)return;
  if(p.classList.contains('show')&&_activeMenuSid===sid){closeSessMenu();return;}
  _activeMenuSid=sid;
  const btn=el||(ev&&(ev.currentTarget||(ev.target&&ev.target.closest&&ev.target.closest('.sess-act-btn'))||ev.target));
  if(!btn||!btn.getBoundingClientRect)return;
  const r=btn.getBoundingClientRect();
  p.style.top=Math.min(r.bottom+4,window.innerHeight-220)+'px';
  p.style.left=Math.min(r.left,window.innerWidth-180)+'px';
  p.classList.add('show');
}
function closeSessMenu(){
  const p=$('sessPop');if(p)p.classList.remove('show');
  _activeMenuSid=null;
}
document.addEventListener('click',e=>{
  if(!e.target.closest('#sessPop')&&!e.target.closest('.sess-act-btn'))closeSessMenu();
});
async function menuRenameSession(){
  const sid=_activeMenuSid;closeSessMenu();if(!sid)return;
  const el=document.querySelector(`.sess[data-sid="${sid}"]`);
  const oldTitle=el?(el.dataset.title||sid):sid;
  const newTitle=await uiPrompt('重命名会话：',oldTitle,'重命名会话');
  if(!newTitle||newTitle.trim()===oldTitle)return;
  try{
    const r=await fetch(`/api/v1/sessions/${sid}`,{method:'PATCH',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({title:newTitle.trim()})});
    const d=await r.json();
    if(r.ok&&d.status==='success'){
      toast('会话已重命名');
      loadSessions(sid);
      if(_curSession===sid){
        const titleEl=$('thSessTitle');if(titleEl){titleEl.textContent=newTitle.trim();titleEl.title=newTitle.trim();}
      }
    }else toast('重命名失败：'+(d.error||r.status));
  }catch(e){toast('重命名失败：'+e);}
}
function menuForkSession(){
  const sid=_activeMenuSid;closeSessMenu();if(!sid)return;
  forkSession(sid);
}
async function menuArchiveSession(){
  const sid=_activeMenuSid;closeSessMenu();if(!sid)return;
  await quickArchiveSession(sid);
}
async function menuDeleteSession(){
  const sid=_activeMenuSid;closeSessMenu();if(!sid)return;
  const el=document.querySelector(`.sess[data-sid="${sid}"]`);
  const title=el?(el.dataset.title||sid):sid;
  if(!(await uiConfirm(`确认删除会话「${title}」？此操作不可撤销。`,'删除会话')))return;
  try{
    const r=await fetch(`/api/v1/sessions/${sid}`,{method:'DELETE',headers:_H({'X-Request-ID':_rid()})});
    const d=await r.json();
    if(r.ok&&d.status==='success'){
      toast('会话已删除');
      const items=await loadSessions();
      if(_curSession===sid){
        if(items&&items.length)loadSessionEvents(items[0].session_id,document.querySelector('#sessList .sess'));
        else renderEmptyViewport(null);
      }
    }else toast('删除失败：'+(d.error||r.status));
  }catch(e){toast('删除失败：'+e);}
}
/* 导出会话（.md 可读纪要 / .jsonl 原始事件流；浏览器直接下载） */
function menuExportSession(fmt){
  const sid=_activeMenuSid;closeSessMenu();if(!sid)return;
  const a=document.createElement('a');
  a.href=`/api/v1/sessions/${sid}/export?fmt=${fmt}`;
  a.download='';
  document.body.appendChild(a);a.click();a.remove();
  toast(fmt==='md'?'纪要导出中…':'事件流导出中…');
}

async function loadSessions(selectId){
  const ss=await _get('/api/v1/sessions');
  const allItems=ss&&ss.ok&&ss.data?ss.data.items:[];
  const items=allItems.filter(s=>!s.archived);
  const list=$('sessList');
  if(!items.length){list.innerHTML='<div class="emptybox"><span class="et">暂无活动会话</span><span class="ed">点「新任务」开始，或在设置 → 归档中恢复已归档会话</span></div>';return items;}
  /* 按 playbook 分组成可折叠文件夹 */
  const groups={};
  for(const s of items){const k=s.playbook||'';(groups[k]=groups[k]||[]).push(s);}
  const st=_foldState();
  const pins=_getPinnedSessions();
  const keys=Object.keys(groups).sort((a,b)=>(a==='')-(b==='')||a.localeCompare(b));
  let html='';
  for(const k of keys){
    /* playbook 可能是路径形态（domain_packs/<pack>/playbook.md 等）：取包目录名再映射友好名 */
    const parts=(k||'').split(/[\\/]/).filter(Boolean);
    let kbase=parts.pop()||'';
    if(kbase.endsWith('.md')&&parts.length)kbase=parts.pop();  /* playbook.md → 取其父目录（包名） */
    const pname=kbase?(_PLAYBOOK_NAMES[kbase]||kbase):'未分组';
    const closed=!!st[k||'__none__'];
    /* 置顶优先排在前面 */
    groups[k].sort((a,b)=>{
      const aPin=pins.includes(a.session_id)?1:0;
      const bPin=pins.includes(b.session_id)?1:0;
      return bPin-aPin;
    });
    html+=`<div class="fold${closed?' closed':''}" data-fold="${_esc(k||'__none__')}" onclick="toggleFolder('${k||'__none__'}')"><span class="chev">▾</span><span style="color:var(--ink3)">▤</span>${_esc(pname)}<span class="cnt">${groups[k].length}</span></div>`;
    html+=`<div class="fold-body${closed?' closed':''}" data-fold="${_esc(k||'__none__')}">`+
      groups[k].map((s,i)=>{
        const on=selectId?s.session_id===selectId:(k===keys[0]&&i===0);
        const isPin=pins.includes(s.session_id);
        return `<div class="sess${on?' on':''}${isPin?' is-pinned':''}" data-sid="${s.session_id}" data-title="${_esc(s.title||s.session_id)}" data-pname="${_esc(pname)}" data-active="${_esc(s.last_active||'')}" data-count="${s.event_count||0}" `+
          `onclick="loadSessionEvents('${s.session_id}',this)" onmouseenter="sessHoverShow(this,'${s.session_id}')" onmouseleave="sessHoverHide()">`+
          (isPin?'<span class="pin-badge" title="已置顶">📌</span>':'')+
          `<div class="tt" title="${_esc(s.title||s.session_id)}">${_esc(s.title||s.session_id)}</div>`+
          `<div class="sess-acts" onclick="event.stopPropagation()">`+
            `<button class="sess-act-btn" title="更多操作" onclick="openSessMenu('${s.session_id}',event,this)"><svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></svg></button>`+
            `<button class="sess-act-btn${isPin?' is-pinned':''}" title="${isPin?'取消置顶':'置顶会话'}" onclick="togglePinSession('${s.session_id}',event)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17v5"/><path d="M9 4h6"/><path d="M10 4v5l-2 3v2h8v-2l-2-3V4"/></svg></button>`+
            `<button class="sess-act-btn" title="归档会话" onclick="quickArchiveSession('${s.session_id}',event)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="12" y1="11" x2="12" y2="17"/><polyline points="9 14 12 17 15 14"/></svg></button>`+
          `</div>`+
          `</div>`;
      }).join('')+`</div>`;
  }
  list.innerHTML=html;
  return items;
}
const _KEEP=['welcomeLine','artIR','hitl','tool3','rcpt','doneLine'];  /* tool2 已随演示流硬编码移除 */
async function loadSessionEvents(sid,el){
  _curSession=sid;
  _lastEventsSig='';  /* 切会话重置签名，强制全量重渲染 */
  document.querySelectorAll('#sessList .sess').forEach(e=>e.classList.toggle('on',e.dataset.sid===sid));
  const elSess=el||document.querySelector(`#sessList .sess[data-sid="${sid}"]`);
  const pname=elSess?(elSess.dataset.pname||'未分组'):'未分组';
  const title=elSess?(elSess.dataset.title||sid):sid;
  const pkgEl=$('thPkgName'), titleEl=$('thSessTitle');
  if(pkgEl)pkgEl.textContent=pname;
  if(titleEl){titleEl.textContent=title;titleEl.title=title;}
  pollLiveIR(sid);
  const d=await _get(`/api/v1/sessions/${sid}/events?tail=300`);
  if(!d||!d.events){toast('会话事件读取失败');return;}
  renderEvents(d.events);
}
let _lastEventsSig='';
function renderEvents(events){
  const sc=$('thScroll');
  /* 流式修复：内容签名未变不重建（消除轮询/ SSE 重复渲染的闪烁跳动）；
     滚动钉底：仅当用户本来就在底部附近才跟随滚动，翻历史不被拽回 */
  const sig=events.length+':'+(events.length?String(events[events.length-1].id||''):'');
  if(sig===_lastEventsSig&&sc.childElementCount)return;
  _lastEventsSig=sig;
  const nearBottom=sc.scrollHeight-sc.scrollTop-sc.clientHeight<80;
  const keep=_KEEP.map(id=>$(id)).filter(Boolean)
    .concat([...sc.children].filter(e=>e.id&&e.id.startsWith('appr-'))); /* 审批卡不被事件刷新抹掉 */
  sc.innerHTML='';
  let html='';
  for(const e of events){
    const p=e.payload||{};
    if(e.type==='message'){
      html+=p.role==='user'?`<div class="msg-you">${_esc(p.content||'')}</div>`:`<div class="agentline">${_esc(p.content||'')}</div>`;
    }else if(e.type==='tool_call'){
      const body=_esc((p.result_ui_view||p.result_llm_view||p.args_summary||'').slice(0,600));
      html+=`<div class="tool closed"><div class="tool-h" onclick="toggleTool(this)"><span class="ic solver">⚙</span><span class="nm">${_esc(p.toolName||'tool')}</span><span class="st ok">${p.phase||''}</span><span class="car">▾</span></div><div class="tool-b">${body||'—'}</div></div>`;
    }else if(e.type==='custom'){
      html+=`<div class="agentline" style="font-size:11px;color:var(--ink3)">◆ ${_esc(p.customType||'custom')}</div>`;
    }
  }
  if(!html)html='<div class="agentline"><span class="dim">（此会话暂无可视事件）</span></div>';
  sc.innerHTML=html;
  keep.forEach(k=>sc.appendChild(k));
  if(nearBottom)sc.scrollTop=sc.scrollHeight;
}

/* ---------- 新建任务：真实 POST /api/v1/runs + 轮询 ---------- */
function newTaskModal(){$('runMask').classList.add('show');popIn($('runMask').querySelector('.modal'));$('runBrief').focus();}
async function startRun(){
  const brief=$('runBrief').value.trim();
  if(!brief){toast('请填写工程指令');return;}
  $('runMask').classList.remove('show');
  try{
    const r=await fetch('/api/v1/runs',{method:'POST',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({brief,playbook:$('runPlaybook').value})});
    const d=await r.json();
    if(!r.ok){toast(d.error||('启动失败 '+r.status));return;}
    toast('任务已启动 · session '+d.session_id.slice(0,8));
    const items=await loadSessions(d.session_id);
    loadSessionEvents(d.session_id);
    streamSession(d.session_id);
    pollRun(d.session_id);
  }catch(e){toast('启动失败：'+e);}
}
/* SSE 实时跟随（P1）：回放后持续推送新增；断开/结束回退轮询 */
let _evtSrc=null;
function streamSession(sid){
  if(_evtSrc){_evtSrc.close();_evtSrc=null;}
  const buf=[];
  const es=new EventSource(`/api/v1/sessions/${sid}/events/stream`);
  _evtSrc=es;
  es.onmessage=m=>{try{buf.push(JSON.parse(m.data));}catch(e){return;} renderEvents(buf);};
  es.onerror=()=>{es.close();if(_evtSrc===es)_evtSrc=null;};
}
async function forkSession(sid,ev){
  ev&&ev.stopPropagation();
  try{
    const r=await fetch(`/api/v1/sessions/${sid}/fork`,{method:'POST',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({})});
    const d=await r.json();
    if(r.ok){toast('已创建分支会话');loadSessions(d.session_id);}
    else toast('分支失败：'+(d.error||r.status));
  }catch(e){toast('分支失败：'+e);}
}
function pollRun(sid){
  if(_pollTimer)clearInterval(_pollTimer);
  const sb=$('stopBtn');if(sb)sb.style.display='inline-flex';
  _pollTimer=setInterval(async()=>{
    /* 缺陷六：视口流式生长——运行中轮询工件 sha，变化即重渲染 */
    pollLiveIR(sid);
    const d=await _get('/api/v1/runs/active');
    const run=d&&d.runs?d.runs.find(r=>r.session_id===sid):(d&&d.run);
    if(!_evtSrc&&_curSession===sid)loadSessionEvents(sid); /* SSE 断开时回退轮询 */
    if(run&&!run.active){
      clearInterval(_pollTimer);_pollTimer=null;
      if(_evtSrc){_evtSrc.close();_evtSrc=null;}
      const sb2=$('stopBtn');if(sb2)sb2.style.display='none';
      pollLiveIR(sid);
      loadSessionEvents(sid);
      toast(run.error?('运行结束（有错误）：'+run.error):'任务完成 · 事件已落 session');
      loadSessions(sid);
    }
  },2500);
}
/* 停止运行（Codex/ZCode 的 stop 语义）：拒绝待决审批门，线程在门处安全退出 */
async function stopCurrentRun(){
  /* 语义分流：正在流式对话 → 中断流（已生成内容保留并落盘）；否则 → 拒绝审批门停止运行 */
  if(_chatAbort){_chatAbort.abort();return;}
  const sid=_curSession;
  if(!sid){toast('当前无会话');return;}
  try{
    const r=await fetch(`/api/v1/runs/${sid}/stop`,{method:'POST',headers:_H({'X-Request-ID':_rid()})});
    const d=await r.json();
    if(r.ok&&d.status==='success')toast('已请求停止（审批门按拒绝退出，'+d.woken_approvals+' 票据被唤醒）');
    else toast(d.error||'停止失败 '+r.status);
  }catch(e){toast('停止失败：'+e);}
}
/* 缺陷六：运行工件的流式渲染（CompiledUtilityIR 变化即生长） */
const _liveSha={};
async function pollLiveIR(sid){
  if(!sid)return;
  const a=await _get(`/api/v1/runs/artifact?session=${encodeURIComponent(sid)}&name=compiled_utility_ir.json`);
  if(a&&a.status==='success'&&a.data&&a.data.nodes&&a.data.nodes.length>=2){
    if(_currentModelSession!==sid||a.sha256!==_liveSha[sid]){
      _liveSha[sid]=a.sha256;
      applyCompiledIR(a.data, sid);
    }
  }else{
    if(_currentModelSession!==sid){
      renderEmptyViewport(sid);
    }
  }
}
function applyCompiledIR(ir, sid=null){
  /* CompiledUtilityIR v1 schema → 渲染器数据（applyRealIR 的工件形态适配） */
  const nodes=(ir.nodes||[]).map(n=>({node_id:n.node_id,x:n.position.x_m,y:n.position.y_m,ground:n.ground_elevation_m,invert_z:n.position.z_m}));
  if(nodes.length<2){renderEmptyViewport(sid);return;}
  const segs=(ir.segments||[]).map(s=>({diameter_mm:s.diameter_mm,slope:s.slope,length_m:s.horizontal_length_m,a:s.from_node,b:s.to_node}));
  applyRealIR({converged:ir.converged!==false,iterations_spent:ir.iterations_spent||2,nodes,segments:segs}, sid);
}

/* ---------- 初始装载 ---------- */
async function loadRuntimeInfo(){
  const ri=await _get('/api/v1/demo/runtime-info');
  /* 模型名只落在 composer 的 .mdl 芯片上（侧栏模型行已删，切换/管理走芯片下拉与设置） */
  if(ri&&ri.llm&&ri.llm.model){document.querySelectorAll('.mdl').forEach(e=>{e.textContent=ri.llm.model+' ▾';});}
}
/* ---------- 宿主状态（MCP 卡式呈现：状态点 + 徽章 + 传输信息 + 动作） ---------- */
const _HOST_META={
  blender:{name:'Blender MCP',desc:'在线 CAD 宿主 · 导出 .blend',transport:'TCP',addr:'127.0.0.1:9876'},
  vectorworks:{name:'Vectorworks IPC',desc:'外部 runner 宿主 · 导出 .vwx',transport:'外部 IPC',addr:'supervisor 不探测（external）'},
};
const _HOST_STATE_I18N={up:'运行中',down:'未连接',external:'外部运行',restarting:'重启中'};
async function refreshHosts(){
  /* 旧宿主卡片容器已并入 MCP 表格：这里直接刷新表格（含真实探测状态） */
  const el=$('mcpTableBody');if(!el)return;
  const hs=await _get('/api/v1/hosts');
  mcpRenderTable(hs&&hs.hosts?hs.hosts:[]);
}
const _RULE_I18N={
  'MU-CLEAR-001:building': {
    title: '建（构）筑物外墙基础安全净距',
    category: '建筑基础',
    spec: '《城市工程管线综合规划规范 GB 50289-2016》§4.1.9 表 4.1.9',
    desc: '地下排水/给水管道与建筑物外墙基础的最小水平净距，防止管道沉降开裂或建筑物基础淘空。',
    val: '≥ 2.50 m'
  },
  'MU-CLEAR-005:water:d_gt_200': {
    title: '给水干管水平避让净距 (DN>200mm)',
    category: '给水管网',
    spec: '《城市工程管线综合规划规范 GB 50289-2016》§4.1.9 表 4.1.9',
    desc: '市政给水主管径大于200mm时与排水分流管线的净距约束，保障供水水质与维修工作面。',
    val: '≥ 1.50 m'
  },
  'MU-CLEAR-005:water:d_le_200': {
    title: '给水支管水平避让净距 (DN≤200mm)',
    category: '给水管网',
    spec: '《城市工程管线综合规划规范 GB 50289-2016》§4.1.9 表 4.1.9',
    desc: '次干管及接户给水支管与排水管的安全间距。',
    val: '≥ 1.00 m'
  },
  'MU-CLEAR-006:gas:low': {
    title: '低压燃气管道水平净距 (P≤0.01MPa)',
    category: '燃气管网',
    spec: '《城市工程管线综合规划规范 GB 50289-2016》§4.1.9',
    desc: '庭院及小区低压燃气管与下水管道最小水平净距，严禁燃气泄露串入雨污水井。',
    val: '≥ 1.00 m'
  },
  'MU-CLEAR-006:gas:medium_a': {
    title: '中压A燃气管道水平净距 (0.2<P≤0.4MPa)',
    category: '燃气管网',
    spec: '《城市工程管线综合规划规范 GB 50289-2016》§4.1.9',
    desc: '中压A天然气主管线强制隔离间距。',
    val: '≥ 1.20 m'
  },
  'MU-CLEAR-006:gas:medium_b': {
    title: '中压B燃气管道水平净距 (0.01<P≤0.2MPa)',
    category: '燃气管网',
    spec: '《城市工程管线综合规划规范 GB 50289-2016》§4.1.9',
    desc: '市政中压B级输气管线隔离净距。',
    val: '≥ 1.20 m'
  },
  'MU-CLEAR-006:gas:sub_high_a': {
    title: '次高压A燃气管水平净距 (0.8<P≤1.6MPa)',
    category: '燃气管网',
    spec: '《城市工程管线综合规划规范 GB 50289-2016》§4.1.9',
    desc: '高风险次高压燃气管线强制安全防护走廊，硬性碰撞阻断。',
    val: '≥ 2.00 m'
  },
  'MU-CLEAR-006:gas:sub_high_b': {
    title: '次高压B燃气管水平净距 (0.4<P≤0.8MPa)',
    category: '燃气管网',
    spec: '《城市工程管线综合规划规范 GB 50289-2016》§4.1.9',
    desc: '次高压B城市干线高压燃气管硬性隔离净距。',
    val: '≥ 1.50 m'
  },
  'MU-CLEAR-007:telecom:direct_buried': {
    title: '直埋通信光缆/电缆水平净距',
    category: '弱电通信',
    spec: '《城市工程管线综合规划规范 GB 50289-2016》§4.1.9',
    desc: '直埋通信与排水管道防淘空及抢修作业面安全间距。',
    val: '≥ 1.00 m'
  },
  'MU-CLEAR-007:telecom:duct': {
    title: '管道式通信管孔群水平净距',
    category: '弱电通信',
    spec: '《城市工程管线综合规划规范 GB 50289-2016》§4.1.9',
    desc: '多孔排管通信通道与重力流管网净距。',
    val: '≥ 1.00 m'
  },
  'MU-CLEAR-008:power:direct_buried': {
    title: '直埋强电电力电缆水平净距',
    category: '强电电力',
    spec: '《城市工程管线综合规划规范 GB 50289-2016》§4.1.9',
    desc: '高低压电力电缆与排水管防电化腐蚀及绝缘安全净距。',
    val: '≥ 0.50 m'
  },
  'MU-CLEAR-008:power:protective_conduit': {
    title: '穿排管/保护管电力电缆水平净距',
    category: '强电电力',
    spec: '《城市工程管线综合规划规范 GB 50289-2016》§4.1.9',
    desc: '加装混凝土或钢保护管的强电管束与排水管最小净距。',
    val: '≥ 0.50 m'
  }
};
let _allRules=[];
let _currentRuleFilter='';
let _currentRuleKw='';

function renderRuleList(){
  const container=$('ruleList');if(!container)return;
  if(!_allRules.length){
    container.innerHTML='<div style="font-size:11px;color:var(--ink3)">无规则数据</div>';
    return;
  }
  const filtered=_allRules.filter(r=>{
    const info=_RULE_I18N[r.rule_key]||{title:r.rule_key,category:r.obstacle_category||'其他',spec:r.source_clause||'',desc:''};
    if(_currentRuleFilter&&info.category!==_currentRuleFilter) return false;
    if(_currentRuleKw){
      const kw=_currentRuleKw.toLowerCase();
      const matchKey=(r.rule_key||'').toLowerCase().includes(kw);
      const matchTitle=(info.title||'').toLowerCase().includes(kw);
      const matchSpec=(info.spec||'').toLowerCase().includes(kw);
      const matchDesc=(info.desc||'').toLowerCase().includes(kw);
      if(!matchKey&&!matchTitle&&!matchSpec&&!matchDesc) return false;
    }
    return true;
  });

  if(!filtered.length){
    container.innerHTML='<div style="font-size:12px;color:var(--ink3);padding:16px 0;text-align:center">未找到匹配的规则条目</div>';
    return;
  }

  container.innerHTML=filtered.map(r=>{
    const info=_RULE_I18N[r.rule_key]||{
      title: r.rule_key,
      category: r.obstacle_category||'管线',
      spec: r.source_clause||'GB 50289-2016',
      desc: '国家标准管线综合净距规定',
      val: `≥ ${r.required_clearance_m} m`
    };
    const isHard = r.enforcement==='production'||r.confidence==='high';
    return `<div class="rule-card">`+
      `<div class="rule-card-hd">`+
        `<span class="rule-badge ${isHard?'hard':'ok'}">${isHard?'强条 (Hard Gate)':'优选 (Soft)'}</span>`+
        `<span class="rule-badge ok">✓ 自检通过</span>`+
        `<div class="rule-card-title">${_esc(info.title)}</div>`+
        `<span style="font:12px var(--mono);font-weight:700;color:var(--ink)">${_esc(info.val)}</span>`+
      `</div>`+
      `<div class="rule-card-spec">${_esc(info.spec)}</div>`+
      `<div class="rule-card-desc">${_esc(info.desc)}</div>`+
      `<div class="rule-card-ft">`+
        `<span>内部规则代码: <code>${_esc(r.rule_key)}</code></span>`+
        `<span style="margin-left:auto">避让对象: <b>${_esc(info.category)}</b> (${_esc(r.obstacle_category||'—')})</span>`+
      `</div>`+
    `</div>`;
  }).join('');
}

function setRuleFilter(cat, btn){
  _currentRuleFilter=cat;
  document.querySelectorAll('#ruleFilterBar .rule-filter-btn').forEach(b=>b.classList.toggle('on',b===btn));
  renderRuleList();
}

function filterRules(){
  const inp=$('ruleSearchInput');
  _currentRuleKw=inp?inp.value.trim():'';
  renderRuleList();
}

function applyThemeSetting(val){
  localStorage.setItem('wb_theme', val);
  const sel=$('selTheme');if(sel)sel.value=val;
  if(val==='system'){
    const isDark=window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
    if(isDark) document.documentElement.classList.add('dark');
    else document.documentElement.classList.remove('dark');
  } else {
    document.documentElement.setAttribute('data-theme', val);
    if(val==='dark') document.documentElement.classList.add('dark');
    else document.documentElement.classList.remove('dark');
  }
  draw();
  toast('外观主题已切换为：'+(val==='light'?'浅色 (纯白黑字)':val==='dark'?'深色 (Obsidian)':'跟随系统'));
}

function applyFontSizeSetting(val){
  localStorage.setItem('wb_font_size', val);
  document.body.style.fontSize=val;
  const sel=$('selFontSize');if(sel)sel.value=val;
  toast('界面字号已设为：'+val);
}

(async function bootstrapReal(){
  try{
    if(localStorage.getItem('wb_sidebar_collapsed')==='1'){
      const s=$('sidebar');if(s)s.classList.add('collapsed');
      const b=$('sbExpandBtn');if(b)b.style.display='inline-flex';
    }
    if(localStorage.getItem('wb_thread_collapsed')==='1'){
      const t=$('thread');if(t)t.classList.add('collapsed');
      const b=$('thToggle');if(b)b.style.display='inline-flex';
    }
    const savedFs=localStorage.getItem('wb_font_size');
    if(savedFs) document.body.style.fontSize=savedFs;
  }catch(e){}
  loadRuntimeInfo();
  refreshHosts();
  const rt=await _get('/api/v1/demo/rule-tree');
  if(rt&&rt.rules){
    _allRules=rt.rules;
    renderRuleList();
  }
  const plg=await _get('/api/v1/plugins');
  if(plg&&plg.capabilities_map){
    $('capSel').innerHTML=Object.keys(plg.capabilities_map).map(c=>`<option value="${c}">${c}</option>`).join('');
    /* 插件面板：分组卡 + 策略门表（此前 JSON 串直接糊墙，可读性差） */
    const byPlugin={};
    Object.entries(plg.capabilities_map).forEach(([cap,pid])=>{(byPlugin[pid]=byPlugin[pid]||[]).push(cap);});
    const policies=plg.capability_policies||[];
    $('plgList').innerHTML=Object.entries(byPlugin).map(([pid,caps])=>
      `<div class="mcp-card"><div class="mcp-hd"><span class="mcp-dot up"></span><span class="mcp-name">${_esc(pid)}</span>`+
      `<span class="mcp-badge up">ACTIVE · ${caps.length} 能力</span></div>`+
      `<div class="mcp-tools">${caps.map(c=>`<span class="mcp-tool">${_esc(c)}</span>`).join('')}</div></div>`).join('')+
      (policies.length?`<div class="mcp-card"><div class="mcp-hd"><span class="mcp-name" style="font-size:12px">策略门（执行前须人工确认的能力）</span><span class="mcp-badge restarting">${policies.length} 条 prompt</span></div>`+
      `<div style="margin-top:8px;overflow-x:auto"><table class="pol-tbl"><thead><tr><th>能力模式</th><th>决策</th><th>为什么需要确认</th></tr></thead><tbody>`+
      policies.map(p=>`<tr><td class="pol-pat">${_esc(p.pattern)}</td><td><span class="pol-decision">${_esc(p.decision)}</span></td><td style="white-space:normal">${_esc(p.justification||'')}</td></tr>`).join('')+
      `</tbody></table></div></div>`:'');
  }
  /* 页面刷新时优先装载真实会话并按工件渲染视口；若无任何会话则加载演示作为引导 */
  loadUploads();
  const items=await loadSessions();
  const act=await _get('/api/v1/runs/active');
  const liveRun=act&&act.runs?act.runs.find(r=>r.active):(act&&act.run&&act.run.active?act.run:null);
  if(liveRun){loadSessionEvents(liveRun.session_id);streamSession(liveRun.session_id);pollRun(liveRun.session_id);}
  else if(items&&items.length){loadSessionEvents(items[0].session_id,document.querySelector('#sessList .sess'));}
  else{applyRealIR(await _get('/api/v1/demo/municipal-pipeline'), 'demo');}
})();

/* ---------- 设置：ZCode 式全屏设置页（左分区导航 + 右内容独立滚动，Esc/✕ 恒可关） ---------- */
const _SET_SECTIONS={general:'常规',appearance:'外观',models:'模型设置',toolset:'工具集预设',memory:'记忆',skills:'技能',mcp:'MCP 服务器',
  rules:'规则树',plugins:'插件与能力',ir:'IR 查看',uploads:'上传',usage:'用量',archive:'归档'};
function setSection(name){
  document.querySelectorAll('#setMask .setnav-it').forEach(b=>b.classList.toggle('on',b.dataset.sec===name));
  document.querySelectorAll('#setMask [data-setsec]').forEach(s=>s.style.display=s.dataset.setsec===name?'':'none');
  const t=$('setTitle');if(t&&_SET_SECTIONS[name])t.textContent=_SET_SECTIONS[name];
  if(name==='models')loadProviders();
  if(name==='archive')loadArchive();
  if(name==='usage')loadUsage();
  if(name==='uploads')loadUploads();
}
/* 检查器入口（线程内「在检查器查看」、斜杠命令、chips）→ 打开设置页对应「数据与审查」分区 */
function openSettingsAt(sec){
  const p=$('setMask');
  if(!p.classList.contains('show')){
    loadSettings();
    p.classList.add('show');
    const m=M();const box=p.querySelector('.setpage-in');if(m&&box)m.animate(box,{opacity:[0,1]},{duration:.18,easing:EASE_OUT});
  }
  setSection(_SET_SECTIONS[sec]?sec:'rules');
  if(sec==='usage')loadUsage();
  if(sec==='archive')loadArchive();
  if(sec==='uploads')loadUploads();
}
/* 侧栏折叠（Ctrl+B；与右栏折叠同一图标语义） */
function toggleSidebar(){
  const s=$('sidebar');if(!s)return;
  const isC=s.classList.toggle('collapsed');
  try{localStorage.setItem('wb_sidebar_collapsed',isC?'1':'0');}catch(e){}
  const btn=$('sbExpandBtn');if(btn)btn.style.display=isC?'inline-flex':'none';
  _smoothResize(240);
}
document.addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='b'){e.preventDefault();toggleSidebar();}
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();newTaskModal();}
});
function toggleSettings(e){
  e&&e.stopPropagation();
  const p=$('setMask');
  if(p.classList.contains('show')){p.classList.remove('show');return;}
  loadSettings();
  setSection('appearance');
  p.classList.add('show');
  const m=M();const box=p.querySelector('.setpage-in');if(m&&box)m.animate(box,{opacity:[0,1]},{duration:.18,easing:EASE_OUT});
}
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){const p=$('setMask');if(p&&p.classList.contains('show'))p.classList.remove('show');}
});
/* 常规区：实时真值（API 端点 / LLM 基线 / 微内核统计 / 规则集协议） */
async function loadGeneral(){
  const ge=$('genEndpoint');if(!ge)return;
  ge.value=location.origin+'/api/v1';
  const ri=await _get('/api/v1/demo/runtime-info');
  if(ri){
    $('genLLM').value=(ri.llm&&ri.llm.configured)?(ri.llm.model+' @ '+ri.llm.base_url):'未配置（离线确定性模板）';
    $('genKernel').value=ri.registry.plugins+' 插件 · '+ri.registry.capabilities+' 能力 · '+ri.registry.policies+' 策略门';
  }else{$('genLLM').value='读取失败';$('genKernel').value='读取失败';}
  const rt=await _get('/api/v1/demo/rule-tree');
  $('genRules').value=(rt&&rt.rules&&rt.rules.length)?
    (rt.protocol_version||'v?')+' · compiler '+(rt.compiler_version||'?')+' · '+rt.rules.length+' 条 · sha '+String(rt.canonical_sha256||'').slice(0,12)+'…':'读取失败';
}
async function loadSettings(){
  const th=localStorage.getItem('wb_theme')||'dark';
  const selTh=$('selTheme');if(selTh)selTh.value=th;
  const fs=localStorage.getItem('wb_font_size')||'13px';
  const selFs=$('selFontSize');if(selFs)selFs.value=fs;
  const ts=await _get('/api/v1/toolset');
  if(ts&&ts.current)markToolset(ts.current);
  loadProviders();
  loadMemory();
  refreshHosts();
  loadGeneral();
  mcpLoadPage();
  /* 技能与 MCP 服务器（卡片式清单） */
  const sk=await _get('/api/v1/skills');
  if(sk){
    $('setSkillsList').innerHTML=sk.skills.length?
      sk.skills.map(s=>`<div class="mcp-card"><div class="mcp-hd"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" style="flex:none;color:var(--acc)"><path d="M14 3v5h5"/><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M9 13h6M9 17h6"/></svg><span class="mcp-name" style="font-size:12.5px">${_esc(s.name)}</span><span class="mcp-badge up">${_esc(s.source)}</span></div><div class="mcp-detail">${_esc(s.description)}</div></div>`).join('')+
      (sk.candidates.length?`<div class="mcp-card" style="border-style:dashed"><div class="mcp-hd"><span class="mcp-name" style="font-size:12px">自蒸馏候选</span><span class="mcp-badge restarting">${sk.candidates.length} 个待批准</span></div><div class="mcp-detail">成功交付后自动沉淀的经验候选——人工批准（对话 <code>/skills</code> 面板）后才转正，永不自动生效</div></div>`:'')
      :'<div class="emptybox"><span class="et">无已生效技能</span><span class="ed">技能是 Markdown 操作手册（SKILL.md）；成功交付后会自动沉淀候选，批准后出现在这里</span></div>';
  }
  mcpLoadPage();
}
/* ---------- MCP 配置页（表格 + 高级配置，接线 /api/v1/settings/mcp 与 /api/v1/hosts） ---------- */
let _mcpData={config:{},tools:{}};
let _mcpModalEditName=null;
async function mcpLoadPage(){
  const d=await _get('/api/v1/settings/mcp');
  if(d&&d.status==='success'){
    _mcpData={config:d.config||{},tools:d.tools||{}};
    $('mcpCfgPath').textContent=d.path||'未知';
  }
  mcpRenderTable();
  if(document.activeElement!==$('mcpJson')){mcpSyncJsonArea();}
}
function mcpTypeOf(entry){
  if(!entry||typeof entry!=='object')return 'STDIO';
  return entry.url?'URL':'STDIO';
}
function mcpRenderTable(hostStates){
  const body=$('mcpTableBody');if(!body)return;
  const rows=[];
  /* 内置 CAD 宿主也入表（真实探测状态来自 /api/v1/hosts） */
  (hostStates||[]).forEach(h=>{
    const nm=h.id==='blender'?'Blender MCP':(h.id==='vectorworks'?'Vectorworks IPC':h.label||h.id);
    rows.push(`<tr><td class="mcpt-name"><span class="mcpt-dot ${h.state||'down'}"></span>${_esc(nm)}</td>`+
      `<td>${_esc(_HOST_STATE_I18N[h.state]||h.state||'—')}</td>`+
      `<td class="mcpt-type">内置 · ${_esc(_HOST_META[h.id]?.transport||'')}</td>`+
      `<td class="mcpt-ops"><span class="mcpt-builtin">内置宿主</span></td></tr>`);
  });
  Object.entries(_mcpData.config).forEach(([name,entry])=>{
    const tools=_mcpData.tools[name]||[];
    const up=tools.length>0;
    const disabled=entry.disabled===true;
    const url=mcpTypeOf(entry)==='URL';
    rows.push(`<tr><td class="mcpt-name">${_esc(name)}${tools.length?` <span class="mcpt-builtin">${tools.length} tools</span>`:''}</td>`+
      `<td><label class="mcpt-switch" title="${disabled?'已停用（保存并重启后不挂载）':'已启用'}"><input type="checkbox" ${disabled?'':'checked'} onchange="mcpToggleServer('${_esc(name)}',this.checked)"><span class="mcpt-slider"></span></label></td>`+
      `<td class="mcpt-type">${url?'URL':'STDIO'}</td>`+
      `<td class="mcpt-ops"><button class="mcpt-btn" onclick="mcpEditEntry('${_esc(name)}')">编辑</button>`+
      `<button class="mcpt-btn danger" onclick="mcpDeleteEntry('${_esc(name)}')">删除</button></td></tr>`);
  });
  body.innerHTML=rows.length?rows.join(''):'<tr><td colspan="4" class="mcpt-builtin">暂无任何 server——点右上「新增」添加，或在下方高级配置中粘贴 JSON</td></tr>';
  const n=Object.keys(_mcpData.config).length;
  const hint=$('mcpTableHint');
  if(hint)hint.textContent=n?`${n} 个第三方 server · ${Object.values(_mcpData.config).filter(e=>e.disabled).length} 个停用 · 保存并重启后挂载生效`:'';
}
async function mcpRefreshStatus(){
  const btn=event&&event.currentTarget;if(btn)btn.disabled=true;
  try{
    await mcpLoadPage();
    const hs=await _get('/api/v1/hosts');
    mcpRenderTable(hs&&hs.hosts?hs.hosts:[]);
    refreshHosts();
    toast('已刷新宿主与 server 状态');
  }finally{if(btn)btn.disabled=false;}
}
async function mcpSave(){
  const cfg=mcpParseJsonArea();
  if(cfg===null)return;
  for(const k of Object.keys(cfg)){
    if(!/^[a-z0-9][a-z0-9\-_]{0,31}$/.test(k)){toast(`server 名非法（需小写 slug）：${k}`);return;}
    const v=cfg[k];
    if(!v||typeof v!=='object'||Array.isArray(v)||(!v.url&&!v.command&&!v.disabled)){toast(`server ${k} 缺少 url 或 command`);return;}
  }
  const r=await fetch('/api/v1/settings/mcp',{method:'PUT',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({config:cfg})});
  const d=await r.json().catch(()=>({}));
  if(r.ok&&d.status==='success'){
    _mcpData.config=cfg;
    mcpRenderTable();
    toast('MCP 配置已保存（重启服务后挂载生效）');
  }else{toast('保存失败：'+(d.error||r.status));}
}
async function mcpResetDefault(){
  if(!confirm('恢复默认将清空本地 MCP 配置文件并回读环境变量默认值，确定？'))return;
  const r=await fetch('/api/v1/settings/mcp',{method:'DELETE',headers:_H({'X-Request-ID':_rid()})});
  const d=await r.json().catch(()=>({}));
  if(r.ok&&d.status==='success'){
    _mcpData.config=d.config||{};
    mcpSyncJsonArea();
    mcpRenderTable();
    toast('已恢复默认');
  }else{toast('恢复失败：'+(d.error||r.status));}
}
function mcpOpenAddModal(){_mcpModalEditName=null;$('mcpModalTitle').textContent='新增 MCP Server';$('mcpModalName').value='';$('mcpModalType').value='stdio';$('mcpModalCmd').value='';$('mcpModalArgs').value='';$('mcpModalUrl').value='';mcpModalSyncType();$('mcpModalMask').classList.add('show');}
function mcpEditEntry(name){
  const entry=_mcpData.config[name];if(!entry)return;
  _mcpModalEditName=name;
  $('mcpModalTitle').textContent='编辑 MCP Server';
  $('mcpModalName').value=name;$('mcpModalName').disabled=true;
  if(entry.url){$('mcpModalType').value='url';$('mcpModalUrl').value=entry.url;}
  else{$('mcpModalType').value='stdio';$('mcpModalCmd').value=entry.command||'';$('mcpModalArgs').value=JSON.stringify(entry.args||[]);}
  mcpModalSyncType();$('mcpModalMask').classList.add('show');
}
function mcpModalSyncType(){
  const t=$('mcpModalType').value;
  $('mcpModalStdioFields').style.display=t==='stdio'?'':'none';
  $('mcpModalUrlFields').style.display=t==='url'?'':'none';
  $('mcpModalName').disabled=!!_mcpModalEditName;
}
function mcpCloseModal(){$('mcpModalMask').classList.remove('show');}
function mcpSubmitModal(){
  const name=$('mcpModalName').value.trim();
  if(!/^[a-z0-9][a-z0-9\-_]{0,31}$/.test(name)){toast('名称需为小写 slug（字母/数字/连字符，≤32 字符）');return;}
  let entry;
  if($('mcpModalType').value==='url'){
    const url=$('mcpModalUrl').value.trim();
    if(!/^https?:\/\//.test(url)){toast('URL 需以 http(s):// 开头');return;}
    entry={url};
  }else{
    const cmd=$('mcpModalCmd').value.trim();
    if(!cmd){toast('命令不能为空');return;}
    let args=[];
    try{args=$('mcpModalArgs').value.trim()?JSON.parse($('mcpModalArgs').value):[];}
    catch(e){toast('参数 JSON 数组解析失败');return;}
    if(!Array.isArray(args)){toast('参数必须是 JSON 数组');return;}
    entry={command:cmd,args};
  }
  if(!_mcpModalEditName)delete _mcpData.config[name];
  _mcpData.config[name]=entry;
  mcpSyncJsonArea();
  mcpRenderTable();
  mcpCloseModal();
  toast(_mcpModalEditName?'已更新（记得点「保存 MCP」持久化）':'已添加（记得点「保存 MCP」持久化）');
  _mcpModalEditName=null;
}
async function mcpDeleteEntry(name){
  if(!confirm(`删除 MCP server「${name}」？（保存后生效）`))return;
  delete _mcpData.config[name];
  mcpSyncJsonArea();
  mcpRenderTable();
  toast('已删除（记得点「保存 MCP」持久化）');
}
function mcpToggleServer(name,on){
  if(!_mcpData.config[name])return;
  if(on)delete _mcpData.config[name].disabled;
  else _mcpData.config[name].disabled=true;
  mcpSyncJsonArea();
  mcpRenderTable();
  toast(on?`已启用 ${name}（保存并重启后挂载）`:`已停用 ${name}（保存并重启后不挂载）`);
}
/* 高级配置区统一 mcpServers 包装格式（对齐截图）；后端两种格式都收 */
function mcpSyncJsonArea(){
  const ta=$('mcpJson');
  if(ta)ta.value=JSON.stringify({mcpServers:_mcpData.config},null,2);
}
function mcpParseJsonArea(){
  let cfg;
  try{cfg=JSON.parse($('mcpJson').value||'{}');}
  catch(e){toast('JSON 解析失败：'+e.message);return null;}
  if(cfg&&typeof cfg==='object'&&cfg.mcpServers&&typeof cfg.mcpServers==='object')cfg=cfg.mcpServers;
  if(!cfg||typeof cfg!=='object'||Array.isArray(cfg)){toast('高级配置必须是对象或含 mcpServers 包装');return null;}
  return cfg;
}
/* ---------- 现代化模型设置：供应商与模型管理（对齐图二、三、四） ---------- */
let _modelData = { presets: [], custom: [], providers: [], current: '' };
let _curProvId = null;
let _activeModalProvId = null;
let _activeModalModelName = null;

function formatContextBadge(val){
  const n = parseInt(val, 10);
  if(!n) return '1M';
  if(n >= 1000000) return Math.round(n / 1000000) + 'M';
  if(n >= 1000) return Math.round(n / 1000) + 'K';
  return String(n);
}

function toggleKeyVisibility(inpId, btn){
  const inp = $(inpId);
  if(!inp) return;
  const isPass = inp.type === 'password';
  inp.type = isPass ? 'text' : 'password';
  btn.innerHTML = isPass ?
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><line x1="2" y1="2" x2="22" y2="22"/></svg>` :
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>`;
}

async function loadProviders(selectProvId){
  const d = await _get('/api/v1/settings/models');
  if(!d) return;
  _modelData = d;
  const provs = d.providers || d.custom || [];
  const cur = d.current || '';

  const list = $('msProvList');
  if(list){
    list.innerHTML = provs.map(p => {
      const isSel = _curProvId ? p.id === _curProvId : false;
      const dotCls = p.enabled !== false ? 'on' : 'off';
      return `<div class="ms-prov-it${isSel ? ' on' : ''}" onclick="selectProvider('${p.id}')">` +
        `<div class="ms-prov-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="m21 16-9 5-9-5V8l9-5 9 5Z"/><path d="m3 8 9 5 9-5"/><path d="M12 13v8"/></svg></div>` +
        `<div class="ms-prov-name">${_esc(p.name)}</div>` +
        `<div class="ms-status-dot ${dotCls}"></div>` +
      `</div>`;
    }).join('');
  }

  const targetId = selectProvId || _curProvId || (provs[0] ? provs[0].id : null);
  if(targetId) selectProvider(targetId);
}

function selectProvider(provId){
  _curProvId = provId;
  document.querySelectorAll('#msProvList .ms-prov-it').forEach(el => {
    el.classList.toggle('on', (el.getAttribute('onclick')||'').includes(`'${provId}'`));
  });
  renderProvDetail(provId);
}

function renderProvDetail(provId){
  _curProvId = provId;
  const p = (_modelData.providers || _modelData.custom || []).find(x => x.id === provId);
  const el = $('msDetail');
  if(!el) return;
  if(!p){
    el.innerHTML = '<div style="font-size:12px;color:var(--ink3);text-align:center;padding:40px 0">请从左侧选择供应商</div>';
    return;
  }
  const isEnabled = p.enabled !== false;
  const models = p.models || [];

  let html = `
    <div class="ms-detail-head">
      <div class="ms-detail-title-row">
        <span class="ms-detail-name">${_esc(p.name)}</span>
        <button class="ms-icon-btn" onclick="renameProvider('${p.id}')" title="重命名供应商"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg></button>
        <div class="ms-toggle-pill">
          <button class="ms-toggle-btn${isEnabled ? ' on-active' : ''}" onclick="setProviderEnabled('${p.id}', true)">已启用</button>
          <button class="ms-toggle-btn${!isEnabled ? ' off-active' : ''}" onclick="setProviderEnabled('${p.id}', false)">禁用</button>
        </div>
      </div>
      <button class="ms-del-prov-btn" onclick="deleteProvider('${p.id}')" title="删除供应商"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg></button>
    </div>

    <div class="ms-config-card">
      <div class="ms-form-item">
        <label>Base URL</label>
        <input class="fin" id="msCurBaseUrl" value="${_esc(p.base_url || '')}" placeholder="https://api.openai.com/v1" onblur="saveProviderField('${p.id}', 'base_url', this.value)">
        <div class="hint">OpenAI 兼容地址填到 <code>/v1</code> 为止，末尾斜杠自动去除；对话端点会按此拼接 chat 接口。</div>
      </div>

      <div class="ms-form-item">
        <label>API 格式</label>
        <select class="fin" id="msCurApiFormat" onchange="saveProviderField('${p.id}', 'api_format', this.value)">
          <option value="Chat Completions (/chat/completions)"${(p.api_format||'').includes('Chat Completions') ? ' selected' : ''}>Chat Completions · OpenAI 兼容（绝大多数供应商）</option>
          <option value="OpenAI Responses"${(p.api_format||'').includes('Responses') ? ' selected' : ''}>OpenAI Responses · /v1/responses（OpenAI 新接口）</option>
          <option value="Anthropic Messages"${(p.api_format||'').includes('Anthropic') ? ' selected' : ''}>Anthropic Messages · /v1/messages（Claude 系）</option>
        </select>
        <div class="hint">不确定就选第一个：国内主流（GLM / DeepSeek / Qwen / Kimi / vLLM 网关等）都是 Chat Completions。</div>
      </div>

      <div class="ms-form-item">
        <label>API Key</label>
        <div class="ms-input-box">
          <input type="password" class="fin" id="msCurApiKey" value="${_esc(p.api_key || '')}" placeholder="sk-..." autocomplete="off" onblur="saveProviderField('${p.id}', 'api_key', this.value)">
          <button class="ms-eye-toggle" type="button" onclick="toggleKeyVisibility('msCurApiKey', this)" title="切换明文/密文">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
          </button>
        </div>
      </div>
    </div>

    <div>
      <div class="ms-sec-header">
        <div class="ms-sec-title">模型列表 <span class="ms-sec-count">${models.length}</span></div>
        <button class="ms-add-mdl-head-btn" onclick="openAddModelModal('${p.id}')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          <span>添加模型</span>
        </button>
      </div>
      <div class="ms-mdl-list">
  `;

  if(models.length){
    html += models.map(m => {
      const badge = formatContextBadge(m.context_window);
      return `
        <div class="ms-mdl-card">
          <div class="ms-mdl-name">${_esc(m.name)}</div>
          <div class="ms-mdl-acts">
            <span class="ms-cap-badge">${_esc(badge)}</span>
            <button class="ms-act-btn" data-tip="测试连通性" onclick="probeModel('${p.id}', '${_esc(m.name)}', this)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="m9 2 2 2"/><path d="m14 7 2 2"/><path d="M18 9a3 3 0 0 0-3-3L9 12a3 3 0 0 0 0 4.24l2.76 2.76A3 3 0 0 0 16 19l6-6a3 3 0 0 0-3-3z"/><path d="m2 22 5.5-5.5"/></svg>
            </button>
            <button class="ms-act-btn" data-tip="编辑模型" onclick="openEditModelModal('${p.id}', '${_esc(m.name)}')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
            </button>
            <button class="ms-act-btn del" data-tip="删除模型" onclick="deleteModel('${p.id}', '${_esc(m.name)}')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
            </button>
          </div>
        </div>
      `;
    }).join('');
  } else {
    html += '<div class="emptybox"><span class="et">暂无配置模型</span><span class="ed">点击右上角「+ 添加模型」为该供应商添加模型</span></div>';
  }

  html += `
      </div>
    </div>
  `;

  el.innerHTML = html;
}



async function saveProviderField(provId, field, value){
  try{
    const r = await fetch(`/api/v1/settings/providers/${provId}`, {
      method: 'PATCH',
      headers: _H({'Content-Type': 'application/json', 'X-Request-ID': _rid()}),
      body: JSON.stringify({[field]: value.trim()})
    });
    const d = await r.json();
    if(r.ok && d.status === 'success'){
      toast('配置已自动保存');
    }
  }catch(e){}
}

async function setProviderEnabled(provId, enabled){
  try{
    const r = await fetch(`/api/v1/settings/providers/${provId}`, {
      method: 'PATCH',
      headers: _H({'Content-Type': 'application/json', 'X-Request-ID': _rid()}),
      body: JSON.stringify({enabled})
    });
    const d = await r.json();
    if(r.ok && d.status === 'success'){
      toast(enabled ? '供应商已启用' : '供应商已禁用');
      loadProviders(provId);
    }
  }catch(e){toast('操作失败：' + e);}
}

async function renameProvider(provId){
  const p = (_modelData.providers || _modelData.custom || []).find(x => x.id === provId);
  if(!p) return;
  const newName = await uiPrompt('重命名供应商：', p.name, '重命名供应商');
  if(!newName || newName.trim() === p.name) return;
  try{
    const r = await fetch(`/api/v1/settings/providers/${provId}`, {
      method: 'PATCH',
      headers: _H({'Content-Type': 'application/json', 'X-Request-ID': _rid()}),
      body: JSON.stringify({name: newName.trim()})
    });
    const d = await r.json();
    if(r.ok && d.status === 'success'){
      toast('已重命名供应商');
      loadProviders(provId);
    }
  }catch(e){toast('重命名失败：' + e);}
}

async function deleteProvider(provId){
  const p = (_modelData.providers || _modelData.custom || []).find(x => x.id === provId);
  if(!p) return;
  if(!(await uiConfirm(`确认删除供应商「${p.name}」及其所有模型？此操作不可撤销。`,'删除供应商'))) return;
  try{
    const r = await fetch(`/api/v1/settings/providers/${provId}`, {
      method: 'DELETE',
      headers: _H({'X-Request-ID': _rid()})
    });
    const d = await r.json();
    if(r.ok && d.status === 'success'){
      toast('供应商已删除');
      _curProvId = null;
      loadProviders();
    }
  }catch(e){toast('删除失败：' + e);}
}

async function probeModel(provId, modelName, btn){
  const orig = btn.innerHTML;
  btn.innerHTML = `<svg class="spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"/><path d="M12 2a10 10 0 0 1 10 10" stroke-opacity="0.75"/></svg>`;
  try{
    const r = await fetch(`/api/v1/settings/providers/${provId}/probe`, {
      method: 'POST',
      headers: _H({'Content-Type': 'application/json', 'X-Request-ID': _rid()}),
      body: JSON.stringify({model: modelName})
    });
    const d = await r.json();
    btn.innerHTML = orig;
    if(d.status === 'success'){
      toast(`✓ ${modelName} 连通正常 (${d.latency_ms}ms)`);
    } else {
      toast(`✗ ${modelName} ${d.error || '连通失败'}`);
    }
  }catch(e){
    btn.innerHTML = orig;
    toast(`✗ ${modelName} 探测异常：` + e);
  }
}

function openAddModelModal(provId){
  _activeModalProvId = provId;
  _activeModalModelName = null;
  $('msModelModalTitle').textContent = '添加模型';
  $('msModelOrigName').value = '';
  $('msModelId').value = '';
  $('msModelContext').value = '1000000';
  $('msModelMaxTokens').value = '128000';
  $('msCapVision').checked = false;
  $('msCapVideo').checked = false;
  $('msModelModal').classList.add('show');
  $('msModelId').focus();
}

function openEditModelModal(provId, modelName){
  _activeModalProvId = provId;
  _activeModalModelName = modelName;
  const p = (_modelData.providers || _modelData.custom || []).find(x => x.id === provId);
  const m = (p && p.models || []).find(x => x.name === modelName);
  $('msModelModalTitle').textContent = '编辑模型';
  $('msModelOrigName').value = modelName;
  $('msModelId').value = modelName;
  $('msModelContext').value = m ? (m.context_window || 1000000) : 1000000;
  $('msModelMaxTokens').value = m ? (m.max_tokens || 128000) : 128000;
  const caps = (m && m.capabilities) || [];
  $('msCapVision').checked = caps.includes('vision');
  $('msCapVideo').checked = caps.includes('video');
  $('msModelModal').classList.add('show');
  $('msModelId').focus();
}

function closeModelModal(){
  $('msModelModal').classList.remove('show');
}

async function saveModelModal(){
  const provId = _activeModalProvId;
  const origName = $('msModelOrigName').value.trim();
  const idVal = $('msModelId').value.trim();
  if(!idVal){toast('请输入模型 ID');return;}
  const context = parseInt($('msModelContext').value, 10) || 1000000;
  const maxTok = parseInt($('msModelMaxTokens').value, 10) || 128000;
  const caps = ['tools'];
  if($('msCapVision').checked) caps.push('vision');
  if($('msCapVideo').checked) caps.push('video');

  try{
    let r;
    if(origName){
      r = await fetch(`/api/v1/settings/providers/${provId}/models/${encodeURIComponent(origName)}`, {
        method: 'PATCH',
        headers: _H({'Content-Type': 'application/json', 'X-Request-ID': _rid()}),
        body: JSON.stringify({new_name: idVal, context_window: context, max_tokens: maxTok, capabilities: caps})
      });
    } else {
      r = await fetch(`/api/v1/settings/providers/${provId}/models`, {
        method: 'POST',
        headers: _H({'Content-Type': 'application/json', 'X-Request-ID': _rid()}),
        body: JSON.stringify({name: idVal, context_window: context, max_tokens: maxTok, capabilities: caps})
      });
    }
    const d = await r.json();
    if(r.ok && d.status === 'success'){
      closeModelModal();
      toast('模型已保存');
      loadProviders(provId);
    } else {
      toast('保存失败：' + (d.error || r.status));
    }
  }catch(e){toast('保存失败：' + e);}
}

async function deleteModel(provId, modelName){
  if(!(await uiConfirm(`确认删除模型「${modelName}」？`,'删除模型'))) return;
  try{
    const r = await fetch(`/api/v1/settings/providers/${provId}/models/${encodeURIComponent(modelName)}`, {
      method: 'DELETE',
      headers: _H({'X-Request-ID': _rid()})
    });
    const d = await r.json();
    if(r.ok && d.status === 'success'){
      toast('模型已删除');
      loadProviders(provId);
    } else {
      toast('删除失败：' + (d.error || r.status));
    }
  }catch(e){toast('删除失败：' + e);}
}

function openAddProvModal(){
  $('msProvName').value = '';
  $('msProvBaseUrl').value = '';
  $('msProvFormat').value = 'Chat Completions (/chat/completions)';
  $('msProvKey').value = '';
  $('msProvModal').classList.add('show');
  $('msProvName').focus();
}

function closeAddProvModal(){
  $('msProvModal').classList.remove('show');
}

async function saveNewProvider(){
  const name = $('msProvName').value.trim();
  if(!name){toast('请输入供应商名称');return;}
  const base_url = $('msProvBaseUrl').value.trim();
  const api_format = $('msProvFormat').value;
  const api_key = $('msProvKey').value.trim();
  if(!base_url){toast('请填写 Base URL（如 https://api.openai.com/v1）');return;}
  if(!/^https?:\/\//.test(base_url)){toast('Base URL 须以 http:// 或 https:// 开头');return;}
  if(!api_key){toast('请填写 API Key（供应商控制台获取，只写不回显）');return;}

  try{
    const r = await fetch('/api/v1/settings/providers', {
      method: 'POST',
      headers: _H({'Content-Type': 'application/json', 'X-Request-ID': _rid()}),
      body: JSON.stringify({name, base_url, api_format, api_key, enabled: true})
    });
    const d = await r.json();
    if(r.ok && d.status === 'success'){
      closeAddProvModal();
      toast('已添加供应商：' + name);
      loadProviders(d.provider.id);
    } else {
      toast('添加失败：' + (d.error || r.status));
    }
  }catch(e){toast('添加失败：' + e);}
}
/* ---------- 长期记忆（P0-4：读取免费；写入/删除都走 prompt 策略门 confirm 语义） ---------- */
async function loadMemory(){
  const d=await _get('/api/v1/memory');if(!d)return;
  const rows=[...d.memory.map(e=>({k:'M',...e})),...d.user.map(e=>({k:'U',...e}))];
  $('memList').innerHTML=rows.length?rows.map(r=>
    `<div style="display:flex;gap:6px;align-items:flex-start"><span style="color:${r.k==='M'?'var(--acc)':'var(--amb)'};flex:none">[${r.k}]</span>`+
    `<span style="flex:1;min-width:0;word-break:break-all">${_esc(r.text)}</span>`+
    `<span class="forkbtn" title="删除此条记忆（策略门确认）" onclick="delMemory('${r.k==='M'?'memory':'user'}',${r.line})">✕</span></div>`).join('')
    :'<div class="emptybox"><span class="et">暂无长期记忆</span><span class="ed">写入需逐条确认，文件存 memory/（gitignored），片段自动注入新任务上下文</span></div>';
}
async function delMemory(file,line){
  if(!(await uiConfirm(`确认删除该记忆条目？（${file} 第 ${line} 行，不可撤销）`,'删除记忆')))return;
  try{
    const r=await fetch('/api/v1/memory/delete',{method:'POST',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({file,line,confirm:true})});
    const d=await r.json();
    if(r.ok&&d.status==='success'){toast('记忆条目已删除');loadMemory();}
    else toast('删除失败：'+(d.error||r.status));
  }catch(e){toast('删除失败：'+e);}
}
async function recordMemory(){
  const entry=$('memEntry').value.trim();if(!entry){toast('记忆条目不能为空');return;}
  if(!(await uiConfirm(`确认写入长期记忆（跨会话持久化）？\n\n${entry}`,'写入记忆')))return;
  try{
    const r=await fetch('/api/v1/memory/record',{method:'POST',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({entry,file:$('memFile').value,confirm:true})});
    const d=await r.json();
    if(r.ok&&d.status==='success'){$('memEntry').value='';toast('记忆已写入（'+d.recorded.file+'）');loadMemory();}
    else toast('写入被拒：'+(d.error||r.status));
  }catch(e){toast('写入失败：'+e);}
}

/* ---------- Composer 模型窄菜单：对齐图二（窄菜单 · 无多余说明 · 管理模型） ---------- */
async function toggleModelMenu(e){
  e&&e.stopPropagation();
  const m=$('mdlMenu');
  if(m.classList.contains('show')){m.classList.remove('show');return;}
  const d=await _get('/api/v1/settings/models');
  if(!d){toast('模型清单读取失败');return;}
  const cur=d.current||'';
  const rows=(d.providers||[]).filter(p=>p.enabled!==false&&p.models&&p.models.length).map(p=>{
    const hasCur=p.models.some(x=>x.name===cur);
    const sub=p.models.map(x=>
      `<div class="sl${x.name===cur?' on':''}" onclick="switchModel('${_esc(x.name)}')">`+
      `<span class="c">${_esc(x.name)}</span>`+
      (x.name===cur?'<span class="chk">✓</span>':'')+
      `</div>`).join('');
    return `<div class="sl sub${hasCur?' on':''}"><span class="c">${_esc(p.name)}</span>`+
      (hasCur?'<span class="chk">✓</span>':'')+
      `<span class="arr">›</span>`+
      `<div class="sl-menu">${sub}</div></div>`;
  }).join('');
  m.innerHTML=(rows||'<div class="sl"><span class="c" style="color:var(--ink3)">无可用模型</span></div>')+
    `<div class="sl-divider"></div>`+
    `<div class="sl-manage" onclick="$('mdlMenu').classList.remove('show');openSettingsAt('models')">`+
    `<span>管理模型</span></div>`;
  m.classList.add('show');
  popIn(m);
}
async function switchModel(name){
  $('mdlMenu').classList.remove('show');
  try{
    const r=await fetch('/api/v1/settings/llm',{method:'PUT',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({model:name})});
    const d=await r.json();
    if(r.ok&&d.status==='success'){toast(d.warning||('已切换基线模型：'+name));loadRuntimeInfo();}
    else toast('切换失败：'+(d.error||r.status));
  }catch(e){toast('切换失败：'+e);}
}
document.addEventListener('click',e=>{
  const m=$('mdlMenu');
  if(m&&m.classList.contains('show')&&!m.contains(e.target)&&e.target.id!=='mdlChip')m.classList.remove('show');
});
/* ---------- 工具集预设：三档卡选中即生效（403 调用门后端执行） ---------- */
function markToolset(v){
  document.querySelectorAll('.ts-card').forEach(c=>c.classList.toggle('on',c.dataset.v===v));
  const hint=$('tsHint');
  if(hint)hint.textContent='当前档位：'+v+'（被滤能力清单不可见、调用 403；即时生效）';
}
async function saveToolset(name){
  try{
    const r=await fetch('/api/v1/toolset',{method:'PUT',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({name})});
    const d=await r.json().catch(()=>({}));
    toast(r.ok?('工具集已切换：'+name+'（清单+invoke 双层即时生效）'):('切换失败：'+(d.error||r.status)));
  }catch(e){toast('切换失败：'+e);}
}
/* ---------- 上传：真实落盘 out/uploads/ ---------- */
$('fileInput').addEventListener('change',async e=>{
  for(const f of e.target.files){
    try{
      const r=await fetch('/api/v1/uploads?name='+encodeURIComponent(f.name),{method:'POST',headers:_H({'Content-Type':'application/octet-stream','X-Request-ID':_rid()}),body:f});
      const d=await r.json();
      if(r.ok&&d.status==='success'){
        toast(`已上传：${d.item.name}（${d.item.size} B）`);
        $('cmpAttach').insertAdjacentHTML('beforeend',
          `<button class="chip" title="sha256 ${d.item.sha256.slice(0,12)}…" onclick="openInspector('uploads')">📎 ${_esc(d.item.name)}</button>`);
        loadUploads();
      }else toast('上传失败：'+(d.error||r.status));
    }catch(err){toast('上传失败：'+err);}
  }
  e.target.value='';
});
async function loadUploads(){
  const d=await _get('/api/v1/uploads');
  if(!d)return;
  $('uplList').innerHTML=d.items.length?d.items.map(it=>
    `<div class="rule"><div class="rh"><span class="rid">${it.name}</span><span class="rst">${it.size} B</span>`+
    `<span class="forkbtn" title="删除此附件（文件+manifest 条目）" onclick="delUpload('${_esc(it.id)}')">✕</span></div>`+
    `<div class="rd">${it.id}<br>sha256 <b class="mono">${it.sha256.slice(0,16)}…</b> · ${it.uploaded_at}</div></div>`).join('')
    :'<div class="emptybox"><span class="et">暂无上传附件</span><span class="ed">composer 回形针按钮可上传 DXF / IFC / GIS / 规范文件（落盘 out/uploads/ + sha256 manifest）</span></div>';
}
async function delUpload(id){
  if(!(await uiConfirm(`确认删除附件？\n\n${id}`,'删除附件')))return;
  try{
    const r=await fetch(`/api/v1/uploads/${encodeURIComponent(id)}`,{method:'DELETE',headers:_H({'X-Request-ID':_rid()})});
    const d=await r.json();
    if(r.ok&&d.status==='success'){toast('附件已删除');loadUploads();}
    else toast('删除失败：'+(d.error||r.status));
  }catch(e){toast('删除失败：'+e);}
}

/* ---------- 能力控制台：真实 invoke ---------- */
async function invokeCap(){
  const cap=$('capSel').value;
  const out=document.querySelector('.cons-out');
  let payload={};
  try{payload=JSON.parse(document.querySelector('.cons textarea').value||'{}');}catch(e){out.textContent='payload JSON 解析失败：'+e;return;}
  out.textContent='invoke '+cap+' …';
  try{
    const r=await fetch('/api/v1/plugins/invoke',{method:'POST',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({capability:cap,payload,confirm:true})});
    const d=await r.json();
    out.textContent=JSON.stringify(d,null,1).slice(0,2000);
  }catch(e){out.textContent='invoke 失败：'+e;}
}

/* ---------- HITL 导出：真实 POST /api/v1/demo/export-{blender|vectorworks} ---------- */
async function doExport(){
  closeModal();
  const host=_exportHost;
  $('tool3Nm').textContent='cad_host:'+host+'.execute';
  $('tool3Host').textContent=_EXPORT_HOST_DESC[host]||host;
  $('tool3Plan').textContent='执行后回填';
  const b=$('approveBtn');b.disabled=true;b.textContent='执行中…';
  $('tool3').style.display='';riseIn($('tool3'));$('tool3').classList.remove('closed');
  $('tool3St').className='st run';$('tool3St').innerHTML='<span class="spin"></span>executing…';
  try{
    const r=await fetch('/api/v1/demo/export-'+host,{method:'POST',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({confirm:true})});
    const d=await r.json();
    if(!r.ok)throw new Error((d.detail&&d.detail.error&&d.detail.error.message)||d.detail||r.status);
    if(d.status==='error')throw new Error(d.error||'导出失败');
    const rc=d.receipt||d;
    $('tool3St').className='st ok';$('tool3St').textContent='✓ '+(rc.elapsed_ms??'—')+' ms · '+(rc.status||'completed');
    if(rc.plan_sha256)$('tool3Plan').textContent=String(rc.plan_sha256).slice(0,16)+'…';
    $('tool3').classList.add('closed');
    $('rcpt').innerHTML=`<div class="hd">✓ 交付回执 · receipt=${rc.status||'completed'}</div>`+
      `<div><span class="k">objects&nbsp;&nbsp;&nbsp;&nbsp;</span>${rc.objects??'—'}&nbsp;&nbsp;<span class="k">output_bytes&nbsp;</span>${rc.output_bytes??'—'}&nbsp;&nbsp;<span class="k">elapsed&nbsp;</span>${rc.elapsed_ms??'—'} ms</div>`+
      `<div><span class="k">output&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span>${rc.output_path||'—'}</div>`+
      `<div><span class="k">plan_sha&nbsp;&nbsp;&nbsp;</span>${String(rc.plan_sha256||'—').slice(0,16)}… · sidecar ↔ IR 哈希绑定</div>`;
    $('rcpt').style.display='';riseIn($('rcpt'));$('doneLine').style.display='';riseIn($('doneLine'),.1);
    toast('交付完成：'+(rc.output_path||''));
  }catch(e){
    $('tool3St').className='st bad';$('tool3St').textContent='✗ '+e.message;
    toast('导出被拦截或失败：'+e.message);
  }
}

/* ---------- 审批中心：轮询待决票据 → 动态 HITL 卡 ---------- */
const _apprShown=new Set();
async function pollApprovals(){
  const d=await _get('/api/v1/approvals');
  if(!d)return;
  /* 角标挂到线程头会话信息区（原 #tabConv 已在信息架构重构中移除；空引用会中断后续审批卡渲染） */
  const host=$('thSessInfo');
  let badge=host?host.querySelector('.bdg'):null;
  if(d.count>0){
    if(host){
      if(!badge){badge=document.createElement('span');badge.className='bdg';host.appendChild(badge);}
      badge.textContent=d.count+' 待批';
      badge.style.cssText='margin-left:6px;padding:1px 7px;border-radius:8px;font:10px var(--mono);background:var(--amb-dim);color:var(--amb)';
      badge.title='有审批门待决（卡片在对话流中）';
    }
  }else if(badge){badge.remove();}
  for(const it of d.items){
    if(_apprShown.has(it.id))continue;
    _apprShown.add(it.id);
    showApprovalCard(it);
  }
}
function showApprovalCard(it){
  const sc=$('thScroll');
  const card=document.createElement('div');
  card.className='hitl';card.id='appr-'+it.id;
  if(it.expired){
    /* 缺陷四：重启遗留票据——运行线程已死，仅可显式作废 */
    card.innerHTML=`<div class="hd"><span class="tag" style="background:var(--red-dim);color:var(--red)">审批门 · ${_esc(it.operation)} · 已过期</span>进程重启遗留<span style="margin-left:auto;font:10px var(--mono);color:var(--ink3)">请求于 ${(it.requested_at||'').slice(0,16).replace('T',' ')}</span></div>`+
      `<div class="ds">该票据所属的运行线程已随进程重启终止，<b>不可放行</b>。请作废以清理注册表。</div>`+
      `<div class="row"><button class="reject" style="flex:1" onclick="decideApproval('${it.id}','rejected')">作废</button></div>`;
  }else{
    card.innerHTML=`<div class="hd"><span class="tag">审批门 · ${_esc(it.operation)}</span>等待人工决策<span style="margin-left:auto;font:10px var(--mono);color:var(--ink3)">已挂起 ${Math.round(it.waiting_s||0)}s</span></div>`+
      `<div class="ds">运行会话 <code>${it.session_id.slice(0,8)}</code> 触达审批门（pipeline 线程已阻塞，决策前不会继续）。参数：<br><code style="color:var(--ink2);word-break:break-all">${_esc(JSON.stringify(it.params)).slice(0,300)}</code></div>`+
      `<input class="fin" id="instr-${it.id}" placeholder="附带指令（可选，写入决策回执 · steer 语义）" style="margin-bottom:8px">`+
      `<div class="row"><button class="btn-pri" style="flex:1" onclick="decideApproval('${it.id}','approved')">批准</button>`+
      `<button class="reject" onclick="decideApproval('${it.id}','rejected')">拒绝</button></div>`;
  }
  sc.appendChild(card);riseIn(card);sc.scrollTop=sc.scrollHeight;
  toast('审批门待决：'+it.operation);
}
async function decideApproval(id,decision){
  try{
    const instrEl=$('instr-'+id);
    const instr=instrEl&&instrEl.value.trim();
    const r=await fetch(`/api/v1/approvals/${id}/decide`,{method:'POST',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({decision,actor:'human:web-operator',...(instr?{instruction:instr}:{})})});
    const d=await r.json().catch(()=>({}));
    const card=$('appr-'+id);
    if(r.ok&&card){
      const label=d.decision==='expired_discarded'?'✕ 已作废（过期票据已清理）':(decision==='approved'?'✓ 已批准 · 运行继续':'✕ 已拒绝 · 运行将 ESCALATE');
      card.innerHTML=`<div class="hd"><span class="tag">审批门</span><span style="color:${decision==='approved'?'var(--grn)':'var(--red)'}">${label} · 决策回执已落 session</span></div>`;
      toast(label);
    }else toast('决策提交失败：'+(d.error||r.status));
  }catch(e){toast('决策提交失败：'+e);}
}
setInterval(pollApprovals,3000);pollApprovals();

/* ---------- 用量（真实调用流水账 → 图形化仪表盘）与归档面板 ---------- */
const _fmtTok=n=>n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(1)+'k':String(n||0);
const _fmtLat=ms=>ms==null?'—':ms>=1000?(ms/1000).toFixed(1)+'s':ms+'ms';
async function loadUsage(){
  const d=await _get('/api/v1/usage');
  const u=d&&d.usage;
  const el=$('usageBody');
  if(!u){el.innerHTML='<div class="emptybox"><span class="et">暂无真实调用记录</span><span class="ed">在对话里发一条消息，或跑一次 CLI pipeline——真实消耗会自动出现在这里的图表中</span></div>';return;}
  /* 兼容旧 usage_summary.json 快照（无 daily/recent 字段） */
  const daily=u.daily||[],recent=u.recent||[],byModel=u.by_model||{};
  const tot=u.total||{calls:0,prompt_tokens:0,completion_tokens:0,total_tokens:0};
  const days=daily.length?daily:[{date:'今天',total_tokens:tot.total_tokens,prompt_tokens:tot.prompt_tokens,completion_tokens:tot.completion_tokens}];
  const max=Math.max(1,...days.map(x=>x.total_tokens||0));
  const grid=document.createElement('div');grid.className='usg-grid';
  grid.innerHTML=
    `<div class="usg-card"><div class="usg-k">总 tokens</div><div class="usg-v">${_fmtTok(tot.total_tokens)}</div><div class="usg-sub">in ${_fmtTok(tot.prompt_tokens)} · out ${_fmtTok(tot.completion_tokens)}</div></div>`+
    `<div class="usg-card"><div class="usg-k">调用次数</div><div class="usg-v">${tot.calls}</div><div class="usg-sub">${Object.keys(byModel).length} 个模型</div></div>`+
    `<div class="usg-card"><div class="usg-k">入榜模型</div><div class="usg-v">${_esc(Object.keys(byModel)[0]||'—')}</div><div class="usg-sub">${Object.keys(byModel)[0]?'占比 '+Math.round((byModel[Object.keys(byModel)[0]].total_tokens/Math.max(1,tot.total_tokens))*100)+'%':'尚未产生用量'}</div></div>`+
    `<div class="usg-card"><div class="usg-k">近 14 日</div><div class="usg-v">${daily.filter(x=>x.total_tokens>0).length} 天有消耗</div><div class="usg-sub">${daily.length?'峰值 '+_fmtTok(max)+' tok':''}</div></div>`;
  el.innerHTML='';
  el.appendChild(grid);
  /* 按日趋势柱状图（总高=tot；上段亮色=输入、下段=输出，hover 显示 in/out/tot） */
  const chart=document.createElement('div');chart.className='usg-sec';
  chart.innerHTML='<div class="usg-sec-t"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8"><line x1="3" y1="20" x2="23" y2="20"/><rect x="5" y="12" width="4" height="8"/><rect x="11" y="6" width="4" height="14"/><rect x="17" y="10" width="4" height="10"/></svg>按日 token 趋势（近 14 天 · 上段=输入 / 下段=输出）</div>';
  const bars=document.createElement('div');bars.className='usg-bars';
  bars.innerHTML=days.map(x=>{
    const t=x.total_tokens||0,h=Math.round(t/max*96),p=x.prompt_tokens||0,c=x.completion_tokens||0;
    const ph=t?Math.round(p/t*100):0;
    const grad=t?`background:linear-gradient(180deg,var(--acc) 0 ${ph}%,hsl(var(--primary) / .45) ${ph}% 100%);`:'';
    return `<div class="usg-bar${t?'':' empty'}"><div class="col" style="height:${Math.max(2,h)}px;${grad}"><span>${t?_fmtTok(t)+' tok · in '+_fmtTok(p)+' / out '+_fmtTok(c):'无消耗'}</span></div><div class="lbl">${(x.date||'').slice(5)}</div></div>`;
  }).join('');
  chart.appendChild(bars);el.appendChild(chart);
  /* 模型占比条（stacked 单轨；宽=tokens 占比） */
  if(Object.keys(byModel).length){
    const share=document.createElement('div');share.className='usg-sec';
    share.innerHTML='<div class="usg-sec-t"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M12 3v9l6 4"/></svg>模型占比（按 tokens）</div>';
    const track=document.createElement('div');track.className='usg-track';track.style.height='8px';
    const cols=['hsl(var(--primary))','var(--grn)','var(--amb)','var(--red)','hsl(var(--primary)/.5)'];
    track.innerHTML=Object.values(byModel).map((v,i)=>`<span style="display:inline-block;height:100%;width:${(v.total_tokens/Math.max(1,tot.total_tokens))*100}%;background:${cols[i%cols.length]};float:left"></span>`).join('');
    share.appendChild(track);
    const rows=document.createElement('div');rows.className='usg-share';rows.style.marginTop='10px';
    rows.innerHTML=Object.entries(byModel).map(([m,v],i)=>{
      const pc=tot.total_tokens?Math.round(v.total_tokens/tot.total_tokens*100):0;
      return `<div class="usg-share-row"><div class="usg-share-hd"><span class="nm" title="${_esc(m)}">${_esc(m)}</span><span class="pc">${_fmtTok(v.total_tokens)} tok · ${v.calls} 次 · ${pc}%</span></div><div class="usg-track"><div class="usg-fill" style="width:${pc}%;background:${cols[i%cols.length]}"></div></div></div>`;
    }).join('');
    share.appendChild(rows);el.appendChild(share);
  }
  /* 最近调用明细（时间/模型/来源/in/out/耗时） */
  if(recent.length){
    const tbl=document.createElement('div');tbl.className='usg-sec';
    tbl.innerHTML=`<div class="usg-sec-t"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>最近调用（${recent.length}）</div>`+
      '<div style="overflow-x:auto"><table class="usg-tbl"><thead><tr><th>时间</th><th>模型</th><th>来源</th><th>in</th><th>out</th><th>耗时</th></tr></thead><tbody>'+
      recent.map(r=>`<tr><td>${_esc(String(r.ts||'').replace('T',' ').slice(5,16))}</td><td>${_esc(r.model)}</td><td><span class="usg-src">${_esc(r.source)}</span></td><td>${_fmtTok(r.prompt_tokens)}</td><td class="tk-out">${_fmtTok(r.completion_tokens)}</td><td>${_fmtLat(r.latency_ms)}</td></tr>`).join('')+
      '</tbody></table></div>';
    el.appendChild(tbl);
  }
}
/* ---------- 归档管理（已归档会话 + 工程交付快照） ---------- */
function switchArchTab(tab){
  const isSess = tab === 'sessions';
  const tabS = $('archTabSessions');
  const tabA = $('archTabArtifacts');
  const viewS = $('archSessView');
  const viewA = $('archArtifactsView');
  if(tabS) tabS.classList.toggle('on', isSess);
  if(tabA) tabA.classList.toggle('on', !isSess);
  if(viewS) viewS.style.display = isSess ? 'block' : 'none';
  if(viewA) viewA.style.display = isSess ? 'none' : 'block';
}

async function unarchiveSession(sid){
  try{
    const r=await fetch(`/api/v1/sessions/${sid}`,{
      method:'PATCH',
      headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),
      body:JSON.stringify({archived:false})
    });
    const d=await r.json();
    if(r.ok&&d.status==='success'){
      toast('已恢复会话至左侧列表');
      await loadArchive();
      await loadSessions(sid);
    }else toast('恢复失败：'+(d.error||r.status));
  }catch(e){toast('恢复失败：'+e);}
}

async function openArchivedSession(sid){
  toggleSettings();
  await loadSessions(sid);
  loadSessionEvents(sid);
}

async function deleteArchivedSession(sid, title){
  if(!(await uiConfirm(`确认永久删除已归档会话「${title}」？此操作不可撤销。`,'删除归档会话')))return;
  try{
    const r=await fetch(`/api/v1/sessions/${sid}`,{method:'DELETE',headers:_H({'X-Request-ID':_rid()})});
    const d=await r.json();
    if(r.ok&&d.status==='success'){
      toast('会话已彻底删除');
      await loadArchive();
      await loadSessions();
    }else toast('删除失败：'+(d.error||r.status));
  }catch(e){toast('删除失败：'+e);}
}

async function loadArchive(){
  // 1. 加载并渲染已归档会话
  try{
    const ss=await _get('/api/v1/sessions');
    const allItems=ss&&ss.ok&&ss.data?ss.data.items:[];
    const archived=allItems.filter(s=>s.archived);
    const countEl=$('archSessCount');
    if(countEl) countEl.textContent=archived.length;
    const listEl=$('archSessList');
    if(listEl){
      if(!archived.length){
        listEl.innerHTML=`
          <div class="arch-empty">
            <svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.6"><polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="12" y1="11" x2="12" y2="17"/><polyline points="9 14 12 17 15 14"/></svg>
            <div class="arch-empty-t">暂无已归档会话</div>
            <div class="arch-empty-d">鼠标悬停在左侧会话上点击「📥」或在三点菜单中选择「归档」，可将已完成或暂不使用的对话收纳至此，保持工作台整洁清爽。</div>
          </div>`;
      }else{
        listEl.innerHTML=archived.map(s=>{
          const pname=s.playbook?(_PLAYBOOK_NAMES[s.playbook]||s.playbook):'';
          const timeStr=s.archived_at?s.archived_at.slice(0,19).replace('T',' '):(s.last_active||'').slice(0,19).replace('T',' ');
          return `
            <div class="arch-sess-card">
              <div class="arch-sess-main">
                <div class="arch-sess-title" title="${_esc(s.title||s.session_id)}">${_esc(s.title||s.session_id)}</div>
                <div class="arch-sess-meta">
                  <span>归档于 ${timeStr}</span>
                  <span>·</span>
                  <span>${s.event_count||0} 条消息/事件</span>
                  ${pname?`<span>·</span><span class="arch-tag">${_esc(pname)}</span>`:''}
                </div>
              </div>
              <div class="arch-sess-acts">
                <button class="arch-btn primary" onclick="unarchiveSession('${s.session_id}')" title="恢复到左侧边栏会话列表">
                  <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
                  <span>恢复</span>
                </button>
                <button class="arch-btn" onclick="openArchivedSession('${s.session_id}')" title="打开此会话查阅历史">
                  <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
                  <span>查看</span>
                </button>
                <button class="arch-btn danger" onclick="deleteArchivedSession('${s.session_id}','${_esc(s.title||s.session_id)}')" title="彻底删除此会话">
                  <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
                  <span>删除</span>
                </button>
              </div>
            </div>`;
        }).join('');
      }
    }
  }catch(e){
    console.error('loadArchivedSessions failed:', e);
  }

  // 2. 加载底层工程交付产物快照
  try{
    const d=await _get('/api/v1/archive');
    const bodyEl=$('archiveBody');
    if(bodyEl){
      bodyEl.innerHTML=(d&&d.items&&d.items.length)?d.items.map(it=>
        `<div class="rule"><div class="rh"><span class="rid">${_esc(it.brief||it.session_id)}</span><span class="rst">${_esc(it.pack)}</span></div>`+
        `<div class="rd">${(it.archived_at||'').slice(0,19).replace('T',' ')} · ${it.files.map(f=>`${f.name} (${f.size}B)`).join('、')}</div></div>`).join('')
      :'<div class="emptybox"><span class="et">暂无交付产物记录</span><span class="ed">完成管网生成交付后，工件快照会自动沉淀到这里</span></div>';
    }
  }catch(e){
    console.error('loadDeliverables failed:', e);
  }
}

/* ---------- Composer：普通文本 → /api/v1/chat 真实 LLM 对话；/solve → 自愈求解器演示调度 ---------- */
/* _hist/_histIdx 声明在 composer 监听块（首个 script），此处仅复用 */
function pushHistory(v){if(v&&_hist[_hist.length-1]!==v)_hist.push(v);if(_hist.length>50)_hist.shift();_histIdx=_hist.length;}
async function sendMsg(){
  const v=ta.value.trim();if(!v)return;ta.value='';slash.classList.remove('show');updateSendState();cmpAutoGrow();pushHistory(v);
  try{localStorage.setItem('wb_draft','');}catch(e){}
  if(v.startsWith('/recall')){doRecall(v.slice(7).trim());return;}
  if(v==='/solve'||v.startsWith('/solve ')){solveDemo();return;}
  if(v.startsWith('/')){
    const cmd=v.split(/\s+/)[0];
    if(['/rules','/ir','/export','/skills','/solve'].includes(cmd)){pickCmd(cmd);return;}
    const sc0=$('thScroll');
    sc0.insertAdjacentHTML('beforeend',
      `<div class="msg-you"></div><div class="agentline">未知命令 <code>${_esc(cmd)}</code>（可用：/solve /rules /ir /export /recall /skills）</div>`);
    const ms=sc0.querySelectorAll('.msg-you');ms[ms.length-1].textContent=v;
    sc0.scrollTop=sc0.scrollHeight;return;
  }
  sendChat(v);
}
/* 对话主循环（流式）：POST /api/v1/chat/stream → SSE 逐字渲染（delta=正文 reasoning=思考链）
   对照 ZCode/Codex：思考态 → 逐字正文 + 光标 → 完成即成对落盘（服务端单一事实源） */
const _CURSOR_CSS=document.createElement('style');
_CURSOR_CSS.textContent='.chat-cursor{display:inline-block;width:7px;height:14px;background:var(--acc);border-radius:2px;margin-left:2px;vertical-align:-2px;animation:curBlink 0.9s steps(1) infinite}@keyframes curBlink{50%{opacity:0}}';
document.head.appendChild(_CURSOR_CSS);
var _chatAbort=null; /* 跨函数可见：停止按钮接管流中断（hoisted 全局，与 _hist 同法） */
async function sendChat(v){
  const sc=$('thScroll');
  sc.insertAdjacentHTML('beforeend',`<div class="msg-you"></div>`+
    `<div class="agentline" id="chatThinking"><span class="spin"></span> <span class="dim" id="chatThinkTxt">模型思考中…</span></div>`);
  const msgs=sc.querySelectorAll('.msg-you');msgs[msgs.length-1].textContent=v;
  const kids=sc.children;riseIn(kids[kids.length-2]);riseIn(kids[kids.length-1],.08);
  sc.scrollTop=sc.scrollHeight;
  const sid=_curSession;
  _chatAbort=new AbortController();
  const stopBtn=$('stopBtn');
  if(stopBtn){stopBtn.style.display='inline-flex';stopBtn.title='停止生成（已生成内容保留并落盘）';}
  let sawContent=false;
  try{
    const r=await fetch('/api/v1/chat/stream',{method:'POST',signal:_chatAbort.signal,
      headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),
      body:JSON.stringify({message:v,session_id:sid||undefined})});
    /* 非 2xx：结构化错误一次性回显（与非流式同口径） */
    if(!r.ok){
      const d=await r.json().catch(()=>({}));
      $('chatThinking')&&$('chatThinking').remove();
      const hint422=(r.status===422);
      sc.insertAdjacentHTML('beforeend',
        `<div class="agentline">${_esc(d.error||('对话失败 '+r.status))}${hint422?' <button class="chip" onclick="openSettingsAt(\'models\')">去配置模型 →</button>':''}</div>`);
      sc.scrollTop=sc.scrollHeight;return;
    }
    const reader=r.body.getReader(),dec=new TextDecoder();
    let buf='',reasoning='',thinkEl=$('chatThinking'),txtEl=$('chatThinkTxt');
    /* 渲染升级：首个正文分片把思考行替换为正文行（带光标） */
    const toBody=()=>{
      if(!thinkEl)return;
      const row=document.createElement('div');row.className='agentline';row.id='chatBody';
      row.innerHTML='<span id="chatTxt"></span><span class="chat-cursor"></span>';
      thinkEl.replaceWith(row);thinkEl=null;
      sc.scrollTop=sc.scrollHeight;
    };
    const handle=sse=>{
      const m=sse.match(/^event: (.+)\ndata: (.+)$/s);
      if(!m)return;
      let d;try{d=JSON.parse(m[2]);}catch(e){return;}
      if(m[1]==='reasoning'){
        reasoning+=d.text;
        if(txtEl&&!sawContent)txtEl.textContent='思考中：'+(reasoning.length>60?reasoning.slice(-60)+'…':reasoning);
        sc.scrollTop=sc.scrollHeight;
      }else if(m[1]==='delta'){
        sawContent=true;toBody();
        const t=$('chatTxt');if(t){t.textContent+=d.text;sc.scrollTop=sc.scrollHeight;}
      }else if(m[1]==='usage'){
        if(d.prompt_tokens||d.completion_tokens)toast('tokens in '+(d.prompt_tokens||0)+' · out '+(d.completion_tokens||0)+(d.latency_ms!=null?' · '+_fmtLat(d.latency_ms):''));
      }else if(m[1]==='error'){
        toBody();
        const cur=$('chatBody');
        if(cur&&sawContent){ /* 中途断流：保留已见正文，追加错误注记 */ }
        if(!sawContent&&cur)cur.remove();
        $('chatThinking')&&$('chatThinking').remove();
        sc.insertAdjacentHTML('beforeend',`<div class="agentline" style="color:var(--red)">${_esc(d.error||'流式对话失败')}</div>`);
        sc.scrollTop=sc.scrollHeight;
      }
      /* done：服务端已落事件（含中断保留语义）——当前会话回读渲染，单一事实源 */
      if(m[1]==='done'&&sid===_curSession&&sid){setTimeout(()=>loadSessionEvents(sid),80);}
    };
    for(;;){
      const {done,value}=await reader.read();
      if(done)break;
      buf+=dec.decode(value,{stream:true});
      let idx;
      while((idx=buf.indexOf('\n\n'))>=0){handle(buf.slice(0,idx));buf=buf.slice(idx+2);}
    }
    if(!sawContent){ /* 空流兜底（后端已发 error 事件的情形之外） */
      $('chatThinking')&&$('chatThinking').remove();
    }else{
      const cur=document.querySelector('#chatBody .chat-cursor');if(cur)cur.remove();
    }
  }catch(e){
    if(e.name==='AbortError'){
      /* 用户停止：保留已渲染内容，标注中断（服务端已把部分内容落盘） */
      $('chatThinking')&&$('chatThinking').remove();
      const cur=document.querySelector('#chatBody .chat-cursor');if(cur)cur.remove();
      sc.insertAdjacentHTML('beforeend','<div class="agentline" style="color:var(--amb)">⏹ 已停止（已生成内容保留并落盘）</div>');
      sc.scrollTop=sc.scrollHeight;
    }else{
      $('chatThinking')&&$('chatThinking').remove();
      sc.insertAdjacentHTML('beforeend',`<div class="agentline" style="color:var(--red)">对话请求失败：${_esc(String(e))}</div>`);
      sc.scrollTop=sc.scrollHeight;
    }
  }finally{
    if(stopBtn){stopBtn.style.display='none';stopBtn.title='停止当前运行（拒绝待决审批门，线程在门处安全退出）';}
    _chatAbort=null;
  }
}
/* /solve：确定性自愈求解器调度（演示主线；真实 pipeline 走「新任务」） */
async function solveDemo(){
  const sc=$('thScroll');
  sc.insertAdjacentHTML('beforeend',`<div class="msg-you"></div>`+
    `<div class="tool"><div class="tool-h" onclick="toggleTool(this)"><span class="ic solver">Σ</span><span class="nm">solver:self_healing</span>`+
    `<span class="st run" id="dynSt"><span class="spin"></span>running…</span><span class="car">▾</span></div>`+
    `<div class="tool-b" id="dynB"><div>经 registry.invoke 调度内置市政自愈场景（真实求解中…）</div></div></div>`);
  const msgs=sc.querySelectorAll('.msg-you');msgs[msgs.length-1].textContent='/solve（调度自愈求解器）';
  const kids=sc.children;riseIn(kids[kids.length-2]);riseIn(kids[kids.length-1],.08);
  sc.scrollTop=sc.scrollHeight;
  playTimeline();
  try{
    const d=await _get('/api/v1/demo/municipal-pipeline');
    if(!d||d.status!=='success')throw new Error((d&&d.error)||'调度失败');
    $('dynSt').className='st ok';$('dynSt').textContent=`✓ converged=${d.converged} · ${d.iterations_spent} 轮`;
    $('dynB').insertAdjacentHTML('beforeend',
      `<div><span class="k">result&nbsp;&nbsp;</span>converged=<span class="ok">${d.converged}</span> · iterations=${d.iterations_spent} · nodes=${d.nodes.length} · segments=${d.segments.length}</div>`+
      `<div><span class="k">resolved</span> ${(d.resolved_violations||[]).map(x=>`<span class="bad">${x.rule_id}</span> ${x.required} → <span class="ok">合规</span>`).join('<br>')||'无违规'}</div>`);
    applyRealIR(d);
    $('artIR').querySelector('.mini-stat').innerHTML=`<span><b>${d.nodes.length}</b> 检查井</span><span><b>${d.segments.length}</b> 管段</span><span><b>DN${d.segments[0]?d.segments[0].diameter_mm:400}</b></span><span><b>${d.segments[0]?d.segments[0].slope:0.003}</b> 坡度</span>`;
    $('artIR').style.display='';riseIn($('artIR'));
    $('hitl').style.display='';riseIn($('hitl'));
    sc.scrollTop=sc.scrollHeight;
  }catch(e){
    $('dynSt').className='st bad';$('dynSt').textContent='✗ '+e.message;
  }
}

/* ---------- 斜杠命令补全：/recall（FTS5 检索）与 /skills（技能库） ---------- */
function pickCmd(c){slash.classList.remove('show');ta.value='';updateSendState();
  if(c==='/rules')openInspector('rules');
  else if(c==='/ir')openInspector('ir');
  else if(c==='/export')askConfirm('blender');
  else if(c==='/solve'){solveDemo();}
  else if(c==='/recall'){ta.value='/recall ';ta.focus();updateSendState();toast('输入 /recall 关键词 检索历史会话（FTS5）');}
  else if(c==='/skills'){listSkills();}
}
async function doRecall(q){
  const sc=$('thScroll');
  if(!q){toast('用法：/recall 关键词');return;}
  sc.insertAdjacentHTML('beforeend','<div class="msg-you"></div>');
  const msgs=sc.querySelectorAll('.msg-you');msgs[msgs.length-1].textContent='/recall '+q;
  const d=await _get('/api/v1/sessions/search?q='+encodeURIComponent(q)+'&limit=8');
  const hits=(d&&d.items)||[];
  sc.insertAdjacentHTML('beforeend',
    `<div class="agentline">会话全文检索「${_esc(q)}」：<b>${hits.length}</b> 条命中（FTS5 · 可溯源）</div>`+
    hits.map(h=>`<div class="tool"><div class="tool-b">`+
      `<div><span class="k">session</span> <code>${h.session_id.slice(0,8)}</code> · ${(h.ts||'').slice(0,16).replace('T',' ')} · ${_esc(h.type||'')}</div>`+
      `<div style="margin:3px 0">${_esc(h.snippet||'')}</div>`+
      `<div><a href="javascript:loadSessionEvents('${h.session_id}')" style="color:var(--acc)">打开会话 →</a></div>`+
    `</div></div>`).join(''));
  sc.scrollTop=sc.scrollHeight;
}
async function listSkills(){
  const d=await _get('/api/v1/skills');if(!d)return;
  threadMode('conv');
  const sc=$('thScroll');
  sc.insertAdjacentHTML('beforeend',
    `<div class="agentline">技能库（SKILL.md · 渐进披露）：<b>${d.skills.length}</b> 个已生效 · <b>${d.candidates.length}</b> 个待批准候选</div>`+
    d.skills.map(s=>`<div class="tool"><div class="tool-h" onclick="toggleTool(this)"><span class="ic skill">✦</span><span class="nm">${_esc(s.name)}</span>`+
      `<span class="st ok">${_esc(s.source)}</span><span class="car">▾</span></div>`+
      `<div class="tool-b"><div>${_esc(s.description)}${s.when_to_use?`<div><span class="k">适用</span> ${_esc(s.when_to_use)}</div>`:''}</div>`+
      `<div style="margin-top:5px"><button class="chip" onclick="invokeSkill('${_esc(s.name)}')">调用（披露正文）</button></div></div></div>`).join('')+
    (d.candidates.length?`<div class="agentline" style="color:var(--amb)">自蒸馏候选（fail-closed：须人工批准才生效）</div>`+
      d.candidates.map(c=>`<div class="tool"><div class="tool-b"><div><code>${_esc(c)}</code></div>`+
        `<div style="margin-top:5px;display:flex;gap:6px"><button class="chip" onclick="approveSkill('${_esc(c)}')">批准转正</button><button class="chip" style="color:var(--red)" onclick="discardSkill('${_esc(c)}')">丢弃</button></div></div></div>`).join(''):''));
  sc.scrollTop=sc.scrollHeight;
}
async function invokeSkill(name){
  try{
    const r=await fetch('/api/v1/skills/invoke',{method:'POST',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({name})});
    const d=await r.json();
    if(!r.ok||d.status!=='success'){toast('调用失败：'+(d.error||r.status));return;}
    const sc=$('thScroll');
    sc.insertAdjacentHTML('beforeend',`<div class="tool"><div class="tool-h" onclick="toggleTool(this)"><span class="ic skill">✦</span><span class="nm">skill:${_esc(name)}</span>`+
      `<span class="st ok">已披露正文</span><span class="car">▾</span></div>`+
      `<div class="tool-b"><pre style="white-space:pre-wrap;font:11px var(--mono);color:var(--ink2)">${_esc(d.skill.body)}</pre></div></div>`);
    sc.scrollTop=sc.scrollHeight;
  }catch(e){toast('调用失败：'+e);}
}
async function approveSkill(file){
  try{
    const r=await fetch('/api/v1/skills/candidates/approve',{method:'POST',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({file})});
    const d=await r.json();
    if(r.ok&&d.status==='success'){toast('候选已转正：'+d.approved);listSkills();}
    else toast('批准失败：'+(d.error||r.status));
  }catch(e){toast('批准失败：'+e);}
}
async function discardSkill(file){
  if(!confirm(`确认丢弃该自蒸馏候选？\n\n${file}\n（候选文件将被删除，不可恢复）`))return;
  try{
    const r=await fetch('/api/v1/skills/candidates/discard',{method:'POST',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({file})});
    const d=await r.json();
    if(r.ok&&d.status==='success'){toast('候选已丢弃');listSkills();}
    else toast('丢弃失败：'+(d.error||r.status));
  }catch(e){toast('丢弃失败：'+e);}
}
/* ---------- 宿主有界重启（P0-3 supervisor） ---------- */
async function restartHost(id){
  try{
    const r=await fetch(`/api/v1/hosts/${id}/restart`,{method:'POST',headers:_H({'X-Request-ID':_rid()})});
    const d=await r.json();
    if(r.ok&&d.status==='success')toast('已发起重启：'+d.host.id+'（退避拉起中，稍后自动刷新状态）');
    else toast('重启被拒：'+(d.error||r.status));
  }catch(e){toast('重启失败：'+e);}
  setTimeout(refreshHosts,4000);  /* 退避窗口后刷新宿主状态 */
}
/* 深链直达（审核/演示）：#skills 打开技能库；#recall=关键词 触发 FTS5 检索；#settings 打开设置、#settings=分区 直达指定分区（延迟执行，避免被首轮会话渲染覆盖） */
setTimeout(()=>{
  if(location.hash.includes('skills'))listSkills();
  const _rc=location.hash.match(/recall=([^&]+)/);if(_rc)doRecall(decodeURIComponent(_rc[1]));
  const _sh=location.hash.match(/settings(?:=([a-z]+))?/);
  if(_sh){if(_sh[1]&&_SET_SECTIONS[_sh[1]])openSettingsAt(_sh[1]);else toggleSettings();}
},900);
</script>

</body>
</html>
"""


_FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


def add_web_ui(app: FastAPI, token: str | None = None) -> None:
    """挂载静态资源并注册 / 工作台页面。

    若检测到现代 Shadcn UI 前端产物 (frontend/dist/index.html)，优先挂载并伺服现代 SPA 应用；
    否则平滑回退至单文件 Franken UI 工作台。
    token 注入所伺服页面（window.__WB_TOKEN），前端变更请求据此携带 Bearer。
    """
    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="web-ui-static")

    if (_FRONTEND_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="frontend-dist-assets")

    @app.get("/favicon.svg", include_in_schema=False)
    @app.get("/favicon.ico", include_in_schema=False)
    async def _favicon() -> Response:
        fav = _FRONTEND_DIST / "favicon.svg"
        if fav.is_file():
            return Response(content=fav.read_bytes(), media_type="image/svg+xml")
        return Response(status_code=204)

    @app.get("/", include_in_schema=False)
    async def _web_ui(request: Request) -> HTMLResponse:
        dist_index = _FRONTEND_DIST / "index.html"
        if dist_index.is_file():
            html = dist_index.read_text(encoding="utf-8")
            html = html.replace('window.__WB_TOKEN = window.__WB_TOKEN || "";', f'window.__WB_TOKEN = "{token or ""}";')
            return HTMLResponse(content=html, status_code=200)
        return HTMLResponse(content=PAGE.replace("__WB_TOKEN__", token or ""), status_code=200)

