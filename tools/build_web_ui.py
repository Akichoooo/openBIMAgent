"""一次性构建脚本：把 ui/prototype-j-franken.html 装配为新的 server/web_ui.py。

变更要点：
- vendor 路径 ./vendor/ → /static/vendor/（StaticFiles 挂载；J 版仅需 franken + motion）
- 页尾注入集成态接线脚本：全部真实端点驱动（功能打通，非演示）
  · GET  demo/municipal-pipeline → 3D 渲染数据（动态取景）
  · GET  demo/rule-tree / demo/runtime-info
  · GET  sessions（X-Request-ID 必带）→ 真实会话列表；GET sessions/{id}/events → 线程渲染
  · POST runs → 新建任务真跑 pipeline；GET runs/active 轮询 + 事件实时追加
  · POST demo/export-blender（HITL confirm=true 走 prompt 策略门）
  · POST plugins/invoke（能力控制台）
  · GET/PUT settings/llm（设置页真实读写）
  · POST/GET uploads（附件真实落盘）
  · composer 普通文本 → 真实调度 demo/municipal-pipeline 追加回合
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # 仓库根（脚本位于 tools/）
html = (ROOT / "ui/prototype-j-franken.html").read_text(encoding="utf-8")

assert '"""' not in html
html = html.replace("./vendor/", "/static/vendor/")

# 🟡 审核修复：剔除原型假逻辑/假数据（生产态由真实 API 驱动；J 单文件版保留 mock 供离线评审）
def _cut(start: str, end: str, keep_end: bool = True) -> None:
    global html
    i = html.index(start)
    j = html.index(end, i)
    html = html[:i] + (html[j:] if keep_end else html[j + len(end):])

# 假流式回合/重放/假导出/假发送（bootstrap 提供真实版本）
_cut("async function runTurn()", "function askConfirm")
_cut("async function doExport(){", "function rejectExport")
_cut("function sendMsg(){\n  const v=ta.value.trim();if(!v)return;ta.value='';slash.classList.remove('show');\n  const sc=$('thScroll');", "/* panel & tabs */")
# mock 会话/规则/插件/IR 死数据与同步写入
_cut("/* sessions list */", "/* init（支持 #plan / #prof / #step2 直达深链，便于审核截图） */")
_cut("const RULES=[", "/* ================================================================\n   视口渲染器")
# /solve 斜杠命令在 bootstrap 重定义为真实调度（共享版引用已删除的 replayAll）
html = html.replace(
    "else if(c==='/solve'){replayAll();toast('重新调度 solver:self_healing');}",
    "else if(c==='/solve'){ta.value='重新调度自愈求解器';sendMsg();}",
)
# J 版原始文件引用的是 CDN → 替换为本地 vendor（离线化）
html = html.replace(
    '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/franken-ui@2.1.2/dist/css/core.min.css">',
    '<link rel="stylesheet" href="/static/vendor/franken/core.min.css">',
)
html = html.replace(
    '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/franken-ui@2.1.2/dist/css/utilities.min.css">',
    '<link rel="stylesheet" href="/static/vendor/franken/utilities.min.css">',
)
# franken components JS：J 版交互为自绘实现，无需引入（其 dist/index.js 为 Tailwind 插件入口，浏览器不可用）
html = html.replace(
    "<script type=\"module\">import 'https://cdn.jsdelivr.net/npm/franken-ui@2.1.2/dist/components/index.js';</script>\n",
    "",
)
html = html.replace(
    """<script type="module">
  import { animate, stagger } from 'https://cdn.jsdelivr.net/npm/motion@latest/+esm';
  window.__M = { animate, stagger };
</script>""",
    """<script src="/static/vendor/motion/motion.js"></script>
<script>window.__M={animate:window.Motion&&window.Motion.animate,stagger:window.Motion&&window.Motion.stagger};</script>""",
)
assert "cdn.jsdelivr.net" not in html, "still has CDN refs"

BOOTSTRAP = r"""
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
function applyRealIR(mp){
  if(!mp||!mp.nodes||mp.nodes.length<2)return;
  _lastIR=mp;
  NODES.length=0;SEGS.length=0;
  mp.nodes.forEach(n=>NODES.push({id:n.node_id,x:n.x,y:n.y,ground:n.ground,invert:n.invert_z}));
  (mp.segments||[]).forEach((s,i)=>{if(NODES[i+1])SEGS.push({a:NODES[i].id,b:NODES[i+1].id,dn:s.diameter_mm||400,slope:s.slope||0.003,len:s.length_m||0});});
  if(NODES.length>=3){
    const A=NODES[1],B=NODES[2];
    HEALED_BEND.from=A.id;HEALED_BEND.to=B.id;
    const dx=B.x-A.x,dy=B.y-A.y,L=Math.hypot(dx,dy)||1,px=-dy/L*8,py=dx/L*8;
    HEALED_BEND.pts=[0,0.25,0.5,0.75,1].map(t=>[A.x+dx*t+(t>0&&t<1?px:0),A.y+dy*t+(t>0&&t<1?py:0)]);
    OBST[0].x=(A.x+B.x)/2-px*0.8;OBST[0].y=(A.y+B.y)/2-py*0.8;
    if(NODES.length>=4){OBST[1].x=NODES[3].x+6;OBST[1].y=NODES[3].y-8;}
    const bxs=NODES.map(n=>n.x),bys=NODES.map(n=>n.y);
    const bspan=Math.max(Math.max(...bxs)-Math.min(...bxs),Math.max(...bys)-Math.min(...bys),16);
    OBST[0].w=bspan*0.3;OBST[0].d=bspan*0.2;OBST[0].h=2.2;OBST[0].clear=Math.max(1.5,bspan*0.05);
    OBST[1].w=1.5;OBST[1].d=bspan*0.22;OBST[1].h=0.6;OBST[1].clear=1.2;
  }
  if(mp.resolved_violations&&mp.resolved_violations.length){
    const v=mp.resolved_violations[0];
    TIMELINE[0].note=`${v.rule_id}：${v.description||'净距碰撞'}（要求 ${v.required}）`;
  }
  if(typeof mp.iterations_spent==='number'){TIMELINE[1].sub='iter '+(mp.iterations_spent-1);TIMELINE[2].sub='iter '+mp.iterations_spent;}
  renderTL();resetCam();draw();
  $('hudStat').textContent=`${NODES.length} 井 · ${SEGS.length} 段 · DN${SEGS[0]?SEGS[0].dn:400}`;
  /* 🟡 审核修复：面包屑与 IR 查看器同步真实数据（不再死数据） */
  const crumb=document.querySelector('.crumb b');if(crumb)crumb.textContent=`CompiledIR · ${NODES.length} 井 ${SEGS.length} 段 · DN${SEGS[0]?SEGS[0].dn:400}`;
  const pill=document.querySelector('.st-top .pill');if(pill&&typeof mp.iterations_spent==='number')pill.innerHTML=`<span class="dot g"></span>${mp.converged?'已收敛':'未收敛'} · ${mp.iterations_spent} 轮迭代`;
  $('irPre').textContent=JSON.stringify({schema_version:'v1.0',converged:mp.converged,iterations_spent:mp.iterations_spent,nodes:mp.nodes,segments:mp.segments,resolved_violations:mp.resolved_violations},null,1).slice(0,6000);
}
function downloadIR(){
  const blob=new Blob([JSON.stringify(_lastIR||{note:'尚无 IR 数据'},null,1)],{type:'application/json'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='compiled_utility_ir.json';a.click();
  URL.revokeObjectURL(a.href);
}

/* ---------- 会话列表（真实） + 事件渲染 ---------- */
async function loadSessions(selectId){
  const ss=await _get('/api/v1/sessions');
  const items=ss&&ss.ok&&ss.data?ss.data.items:[];
  const list=$('sessList');
  if(!items.length){list.innerHTML='<div style="padding:8px 12px;font-size:11px;color:var(--ink3)">暂无会话 · 点「新任务」真跑一次</div>';return;}
  list.innerHTML=items.map((s,i)=>`<div class="sess${(selectId?s.session_id===selectId:i===0)?' on':''}" data-sid="${s.session_id}" onclick="loadSessionEvents('${s.session_id}',this)"><span class="dot g"></span><div><div class="tt">${_esc(s.title||s.session_id)}</div><div class="meta">${(s.last_active||'').slice(0,16).replace('T',' ')} · ${s.event_count||0} 事件</div></div><span class="forkbtn" title="从此会话分支（/tree fork）" onclick="forkSession('${s.session_id}',event)">⑂</span></div>`).join('');
  return items;
}
const _KEEP=['welcomeLine','tool2','artIR','hitl','tool3','rcpt','doneLine'];
async function loadSessionEvents(sid,el){
  _curSession=sid;
  document.querySelectorAll('#sessList .sess').forEach(e=>e.classList.toggle('on',e.dataset.sid===sid));
  const d=await _get(`/api/v1/sessions/${sid}/events?tail=300`);
  if(!d||!d.events){toast('会话事件读取失败');return;}
  renderEvents(d.events);
}
function renderEvents(events){
  const sc=$('thScroll');
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
  sc.scrollTop=sc.scrollHeight;
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
  _pollTimer=setInterval(async()=>{
    /* 缺陷六：视口流式生长——运行中轮询工件 sha，变化即重渲染 */
    pollLiveIR(sid);
    const d=await _get('/api/v1/runs/active');
    const run=d&&d.runs?d.runs.find(r=>r.session_id===sid):(d&&d.run);
    if(!_evtSrc&&_curSession===sid)loadSessionEvents(sid); /* SSE 断开时回退轮询 */
    if(run&&!run.active){
      clearInterval(_pollTimer);_pollTimer=null;
      if(_evtSrc){_evtSrc.close();_evtSrc=null;}
      pollLiveIR(sid);
      loadSessionEvents(sid);
      toast(run.error?('运行结束（有错误）：'+run.error):'任务完成 · 事件已落 session');
      loadSessions(sid);
    }
  },2500);
}
/* 缺陷六：运行工件的流式渲染（CompiledUtilityIR 变化即生长） */
const _liveSha={};
async function pollLiveIR(sid){
  const a=await _get(`/api/v1/runs/artifact?session=${encodeURIComponent(sid)}&name=compiled_utility_ir.json`);
  if(a&&a.sha256&&a.sha256!==_liveSha[sid]){
    _liveSha[sid]=a.sha256;
    if(a.data&&a.data.nodes)applyCompiledIR(a.data);
  }
}
function applyCompiledIR(ir){
  /* CompiledUtilityIR v1 schema → 渲染器数据（applyRealIR 的工件形态适配） */
  const nodes=(ir.nodes||[]).map(n=>({node_id:n.node_id,x:n.position.x_m,y:n.position.y_m,ground:n.ground_elevation_m,invert_z:n.position.z_m}));
  if(nodes.length<2)return;
  applyRealIR({converged:true,nodes,segments:(ir.segments||[]).map(s=>({diameter_mm:s.diameter_mm,slope:s.slope,length_m:s.horizontal_length_m}))});
}

/* ---------- 初始装载 ---------- */
async function loadRuntimeInfo(){
  const ri=await _get('/api/v1/demo/runtime-info');
  /* 模型名只落在 composer 的 .mdl 芯片上（侧栏模型行已删，切换/管理走芯片下拉与设置） */
  if(ri&&ri.llm&&ri.llm.model){document.querySelectorAll('.mdl').forEach(e=>{e.textContent=ri.llm.model+' ▾';});}
}
/* ---------- 宿主状态（P0-3 supervisor；渲染进设置弹层 #hostSettings，侧栏芯片已删） ---------- */
async function refreshHosts(){
  const el=$('hostSettings');if(!el)return;
  const hs=await _get('/api/v1/hosts');
  if(!hs||!hs.hosts){el.innerHTML='<div style="font-size:11px;color:var(--ink3)">读取失败</div>';return;}
  el.innerHTML=hs.hosts.map(h=>{
    const color=h.state==='up'?'var(--grn)':h.state==='restarting'?'var(--amb)':h.state==='external'?'var(--ink3)':'var(--red)';
    return `<div class="rule"><div class="rh"><span class="rid"><span style="display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px;background:${color}"></span>${_esc(h.label)}</span>`+
      `<span class="rst">${_esc(h.state)}${h.restart_count?` · 重启×${h.restart_count}`:''}</span></div>`+
      (h.detail?`<div class="rd">${_esc(h.detail)}</div>`:'')+
      (h.id==='blender'&&h.state==='down'&&h.restartable?`<div style="margin-top:4px"><button class="chip" onclick="restartHost('blender')">重启（有界退避）</button></div>`:'')+
      `</div>`;
  }).join('');
}
(async function bootstrapReal(){
  loadRuntimeInfo();
  refreshHosts();
  const rt=await _get('/api/v1/demo/rule-tree');
  if(rt&&rt.rules){
    $('ruleList').innerHTML=rt.rules.map(r=>`<div class="rule"><div class="rh"><span class="rid">${r.rule_key}</span><span class="rst">${r.self_test_match?'✓':'·'} ${r.enforcement||''}</span></div><div class="rd">${r.obstacle_category||''} · 净距 <b class="mono">${r.required_clearance_m}m</b>${r.clause?' · '+r.clause:''}</div></div>`).join('');
  }
  const pl=await _get('/api/v1/plugins');
  if(pl&&pl.capabilities_map){
    $('capSel').innerHTML=Object.keys(pl.capabilities_map).map(c=>`<option value="${c}">${c}</option>`).join('');
    /* 🟡 审核修复：插件面板接真实清单（此前永远显示 mock） */
    const byPlugin={};
    Object.entries(pl.capabilities_map).forEach(([cap,pid])=>{(byPlugin[pid]=byPlugin[pid]||[]).push(cap);});
    const policies=pl.capability_policies||[];
    $('plgList').innerHTML=Object.entries(byPlugin).map(([pid,caps])=>
      `<div class="plg"><div class="pid">${_esc(pid)} <span style="color:var(--grn);font-size:9.5px">ACTIVE</span></div>`+
      `<div class="pds">${caps.length} 能力：<span class="mono" style="color:var(--acc)">${caps.map(_esc).join(' · ')}</span></div></div>`).join('')+
      (policies.length?`<div class="plg"><div class="pid">capability_policies <span style="color:var(--amb);font-size:9.5px">${policies.length} 条策略</span></div><div class="pds mono" style="font-size:10px">${policies.map(p=>_esc(JSON.stringify(p)).slice(0,120)).join('<br>')}</div></div>`:'');
  }
  /* 宿主状态改由 refreshHosts() 渲染进设置弹层（侧栏底部芯片已随布局收敛删除） */
  applyRealIR(await _get('/api/v1/demo/municipal-pipeline'));
  loadUploads();
  const items=await loadSessions();
  /* 页面刷新时若有进行中的运行，恢复轮询 */
  const act=await _get('/api/v1/runs/active');
  const liveRun=act&&act.runs?act.runs.find(r=>r.active):(act&&act.run&&act.run.active?act.run:null);
  if(liveRun){loadSessionEvents(liveRun.session_id);streamSession(liveRun.session_id);pollRun(liveRun.session_id);}
  else if(items&&items.length){loadSessionEvents(items[0].session_id,document.querySelector('#sessList .sess'));}
})();

/* ---------- 设置：真实读写 /api/v1/settings/llm ---------- */
function toggleSettings(e){
  e&&e.stopPropagation();
  loadSettings();
  $('setMask').classList.add('show');
  popIn($('setMask').querySelector('.modal'));
}
async function loadSettings(){
  const d=await _get('/api/v1/settings/llm');
  if(!d)return;
  $('setModel').value=d.baseline.model||'';
  $('setBase').value=d.baseline.base_url||'';
  $('setKey').value='';
  $('setKeyState').textContent=d.baseline.api_key_set?'●已配置（留空则保留）':'○未设置';
  $('provKeys').innerHTML=d.provider_keys.map(p=>
    `<label class="fl">${p.env} <span class="fk" style="color:${p.key_set?'var(--grn)':'var(--ink3)'}">${p.key_set?'●已配置':'○未设置'}</span>`+
    `<input type="password" class="fin" data-env="${p.env}" placeholder="（留空不修改）" autocomplete="off"></label>`).join('');
  const ts=await _get('/api/v1/toolset');
  if(ts&&ts.current)$('setToolset').value=ts.current;
  loadMemory();
  refreshHosts();
}
/* ---------- 长期记忆（P0-4：读取免费；写入走 prompt 策略门 confirm 语义） ---------- */
async function loadMemory(){
  const d=await _get('/api/v1/memory');if(!d)return;
  const rows=[...d.memory.map(l=>['M',l]),...d.user.map(l=>['U',l])];
  $('memList').innerHTML=rows.length?rows.map(([k,l])=>`<div><span style="color:${k==='M'?'var(--acc)':'var(--amb)'}">[${k}]</span> ${_esc(l)}</div>`).join('')
    :'<div style="color:var(--ink3)">暂无长期记忆（写入需逐条确认，文件存 memory/）</div>';
}
async function recordMemory(){
  const entry=$('memEntry').value.trim();if(!entry){toast('记忆条目不能为空');return;}
  if(!confirm(`确认写入长期记忆（跨会话持久化）？\n\n${entry}`))return;
  try{
    const r=await fetch('/api/v1/memory/record',{method:'POST',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({entry,file:$('memFile').value,confirm:true})});
    const d=await r.json();
    if(r.ok&&d.status==='success'){$('memEntry').value='';toast('记忆已写入（'+d.recorded.file+'）');loadMemory();}
    else toast('写入被拒：'+(d.error||r.status));
  }catch(e){toast('写入失败：'+e);}
}

/* ---------- Composer 模型芯片：点击=切换（真实 models.toml 清单）+「管理模型」进设置（对齐 ZCode） ---------- */
async function toggleModelMenu(e){
  e&&e.stopPropagation();
  const m=$('mdlMenu');
  if(m.classList.contains('show')){m.classList.remove('show');return;}
  const d=await _get('/api/v1/settings/models');
  if(!d){toast('模型清单读取失败');return;}
  const cur=d.current||'';
  const rows=(d.models||[]).map(mm=>
    `<div class="sl" onclick="switchModel('${_esc(mm.name)}')">`+
    `<span class="c" style="font:11px var(--mono)">${_esc(mm.name)}</span>`+
    `<span class="d">${_esc(mm.provider)}${(mm.capabilities||[]).includes('vision')?' · vision':''}</span>`+
    (mm.name===cur?'<span style="margin-left:auto;color:var(--grn)">✓ 当前</span>':'')+
    `</div>`).join('');
  m.innerHTML=(rows||'<div class="sl"><span class="d">models.toml 无可用模型</span></div>')+
    `<div class="sl" style="border-top:1px solid var(--line)" onclick="$('mdlMenu').classList.remove('show');toggleSettings()">`+
    `<span class="c">管理模型</span><span class="d">API key / base_url 在设置中配置</span></div>`;
  m.classList.add('show');
  popIn(m);
}
async function switchModel(name){
  $('mdlMenu').classList.remove('show');
  try{
    const r=await fetch('/api/v1/settings/llm',{method:'PUT',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({model:name})});
    const d=await r.json();
    if(r.ok&&d.status==='success'){toast('已切换基线模型：'+name);loadRuntimeInfo();}
    else toast('切换失败：'+(d.error||r.status));
  }catch(e){toast('切换失败：'+e);}
}
document.addEventListener('click',e=>{
  const m=$('mdlMenu');
  if(m&&m.classList.contains('show')&&!m.contains(e.target)&&e.target.id!=='mdlChip')m.classList.remove('show');
});
async function saveSettings(){
  const body={model:$('setModel').value.trim(),base_url:$('setBase').value.trim()};
  if($('setKey').value.trim())body.api_key=$('setKey').value.trim();
  const pk={};
  document.querySelectorAll('#provKeys input[data-env]').forEach(i=>{if(i.value.trim())pk[i.dataset.env]=i.value.trim();});
  if(Object.keys(pk).length)body.provider_keys=pk;
  try{
    const r=await fetch('/api/v1/settings/llm',{method:'PUT',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify(body)});
    const d=await r.json();
    if(r.ok&&d.status==='success'){
      const ts=$('setToolset').value;
      const tr=await fetch('/api/v1/toolset',{method:'PUT',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({name:ts})});
      const td=await tr.json().catch(()=>({}));
      toast(tr.ok?'设置已保存（llm_baseline.local.toml · 工具集='+ts+'）':'LLM 已保存，工具集切换失败：'+(td.error||tr.status));
      $('setMask').classList.remove('show');
      loadRuntimeInfo();
    }else toast('保存失败：'+(d.error||r.status));
  }catch(e){toast('保存失败：'+e);}
}

/* ---------- 上传：真实落盘 out/uploads/ ---------- */
$('fileInput').addEventListener('change',async e=>{
  for(const f of e.target.files){
    try{
      const r=await fetch('/api/v1/uploads?name='+encodeURIComponent(f.name),{method:'POST',headers:_H({'Content-Type':'application/octet-stream','X-Request-ID':_rid()}),body:f});
      const d=await r.json();
      if(r.ok&&d.status==='success'){
        toast(`已上传：${d.item.name}（${d.item.size} B）`);
        document.querySelector('.chips').insertAdjacentHTML('beforeend',
          `<button class="chip" title="sha256 ${d.item.sha256.slice(0,12)}…" onclick="openInspector('uploads')">📎 ${d.item.name}</button>`);
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
    `<div class="rule"><div class="rh"><span class="rid">${it.name}</span><span class="rst">${it.size} B</span></div>`+
    `<div class="rd">${it.id}<br>sha256 <b class="mono">${it.sha256.slice(0,16)}…</b> · ${it.uploaded_at}</div></div>`).join('')
    :'<div style="font-size:11px;color:var(--ink3)">暂无上传</div>';
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

/* ---------- HITL 导出：真实 POST /api/v1/demo/export-blender ---------- */
async function doExport(){
  closeModal();
  const b=$('approveBtn');b.disabled=true;b.textContent='执行中…';
  $('tool3').style.display='';riseIn($('tool3'));$('tool3').classList.remove('closed');
  $('tool3St').className='st run';$('tool3St').innerHTML='<span class="spin"></span>executing…';
  try{
    const r=await fetch('/api/v1/demo/export-blender',{method:'POST',headers:_H({'Content-Type':'application/json','X-Request-ID':_rid()}),body:JSON.stringify({confirm:true})});
    const d=await r.json();
    if(!r.ok)throw new Error((d.detail&&d.detail.error&&d.detail.error.message)||d.detail||r.status);
    const rc=d.receipt||d;
    $('tool3St').className='st ok';$('tool3St').textContent='✓ '+(rc.elapsed_ms??'—')+' ms · '+(rc.status||'completed');
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
  const tab=$('tabConv');
  let badge=tab.querySelector('.bdg');
  if(d.count>0){
    if(!badge){badge=document.createElement('span');badge.className='bdg';tab.appendChild(badge);}
    badge.textContent=d.count+' 待批';
    badge.style.background='var(--amb-dim)';badge.style.color='var(--amb)';
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
      `<div class="row"><button class="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground" style="flex:1" onclick="decideApproval('${it.id}','approved')">批准</button>`+
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

/* ---------- 用量（P3）与归档（P2）面板 ---------- */
async function loadUsage(){
  const d=await _get('/api/v1/usage');
  const u=d&&d.usage;
  $('usageBody').innerHTML=u?
    `<div class="mini-stat" style="margin-bottom:8px"><span><b>${u.total.calls}</b> 调用</span><span><b>${u.total.total_tokens}</b> tok</span><span><b>${u.total.prompt_tokens}</b> in</span><span><b>${u.total.completion_tokens}</b> out</span></div>`+
    (Object.entries(u.by_model||{}).map(([m,v])=>`<div class="rule"><div class="rh"><span class="rid">${_esc(m)}</span><span class="rst">${v.total_tokens||0} tok</span></div><div class="rd">${v.calls||0} 次调用 · in ${v.prompt_tokens||0} · out ${v.completion_tokens||0}${v.cost_usd!=null?' · $'+v.cost_usd:''}</div></div>`).join('')||'<div style="font-size:11px;color:var(--ink3)">尚无分模型数据</div>')
  :'<div style="font-size:11px;color:var(--ink3)">暂无用量记录（离线模板运行不消耗 LLM）</div>';
}
async function loadArchive(){
  const d=await _get('/api/v1/archive');
  if(!d)return;
  $('archiveBody').innerHTML=d.items.length?d.items.map(it=>
    `<div class="rule"><div class="rh"><span class="rid">${_esc(it.brief||it.session_id)}</span><span class="rst">${_esc(it.pack)}</span></div>`+
    `<div class="rd">${(it.archived_at||'').slice(0,19).replace('T',' ')} · ${it.files.map(f=>`${f.name} (${f.size}B)`).join('、')}</div></div>`).join('')
  :'<div style="font-size:11px;color:var(--ink3)">暂无归档（完成任务交付后自动写入）</div>';
}

/* ---------- Composer：普通文本 → 真实调度自愈求解器并追加回合 ---------- */
async function sendMsg(){
  const v=ta.value.trim();if(!v)return;ta.value='';slash.classList.remove('show');
  if(v.startsWith('/recall')){doRecall(v.slice(7).trim());return;}
  const sc=$('thScroll');
  sc.insertAdjacentHTML('beforeend',`<div class="msg-you"></div>`+
    `<div class="tool"><div class="tool-h" onclick="toggleTool(this)"><span class="ic solver">Σ</span><span class="nm">solver:self_healing</span>`+
    `<span class="st run" id="dynSt"><span class="spin"></span>running…</span><span class="car">▾</span></div>`+
    `<div class="tool-b" id="dynB"><div>经 registry.invoke 调度内置市政自愈场景（真实求解中…）</div></div></div>`);
  const msgs=sc.querySelectorAll('.msg-you');msgs[msgs.length-1].textContent=v;
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
function pickCmd(c){slash.classList.remove('show');ta.value='';
  if(c==='/rules')openInspector('rules');
  else if(c==='/ir')openInspector('ir');
  else if(c==='/export')askConfirm();
  else if(c==='/solve'){ta.value='重新调度自愈求解器';sendMsg();}
  else if(c==='/recall'){ta.value='/recall ';ta.focus();toast('输入 /recall 关键词 检索历史会话（FTS5）');}
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
        `<div style="margin-top:5px"><button class="chip" onclick="approveSkill('${_esc(c)}')">批准转正</button></div></div></div>`).join(''):''));
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
/* 深链直达（审核/演示）：#skills 打开技能库；#recall=关键词 触发 FTS5 检索；#settings 打开设置（延迟执行，避免被首轮会话渲染覆盖） */
setTimeout(()=>{
  if(location.hash.includes('skills'))listSkills();
  const _rc=location.hash.match(/recall=([^&]+)/);if(_rc)doRecall(decodeURIComponent(_rc[1]));
  if(location.hash.includes('settings'))toggleSettings();
},900);
</script>
"""

html = html.replace("</body>", BOOTSTRAP + "\n</body>")

NEW_PY = '''"""M2 P6 Web Console: openBIMAgent 数字化工程工作台（方案 J 集成版 · 功能打通）。

布局：Codex 风格 × 3D 视口英雄区（ui/prototype-j-franken.html 迁入）。
- 组件栈：Franken UI 2.1.2 shadcn zinc token 皮肤 + Motion 动效
- 库文件 vendor 到 server/static/vendor/（MIT 许可），经 /static 挂载，完全离线可用
- 页尾集成态接线脚本消费真实端点（运行/会话/设置/上传/调度/导出全部功能打通）：
  runs（POST/GET active）、sessions + sessions/{id}/events、demo/municipal-pipeline、
  demo/rule-tree、demo/runtime-info、plugins、plugins/invoke、demo/export-blender、
  settings/llm（GET/PUT）、uploads（GET/POST）
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

# 测试断言同步（J 版页面特征：franken vendor 引用）
test = ROOT / "tests/test_m2_fastapi.py"
t = test.read_text(encoding="utf-8")
for old, new in [('"three.min.js"', '"/static/vendor/franken"'), ('"/static/vendor/shoelace"', '"/static/vendor/franken"')]:
    if old in t:
        t = t.replace(old, new)
        test.write_text(t, encoding="utf-8")
        print(f"test assertion updated: {old} -> {new}")
        break
else:
    print("test assertion already current, skipped")
