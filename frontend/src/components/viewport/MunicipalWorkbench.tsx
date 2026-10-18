import React, { useEffect, useRef, useState } from "react"
import { api, type MunicipalState, type MunicipalVisionConfig, type MunicipalVisualIssue, type MunicipalVisualReport } from "@/services/api"
import { Button } from "@/components/ui/button"
import { LockKeyhole, UnlockKeyhole, Plus, Trash2, ChevronDown, ChevronUp } from "lucide-react"

const fieldClass = "w-full min-w-0 rounded-md border border-input bg-background px-2 py-1.5 text-sm tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"

const observationLabels: Record<string, string> = {
  locatable_suspicion: "可定位视觉疑点",
  insufficient_evidence: "证据不足/遮挡",
  parameter_check: "建议检查参数",
}

const evidenceSourceLabels: Record<string, string> = {
  visual_observation: "图像观察",
  deterministic_reference: "引用确定性检查（非独立视觉发现）",
  uncertain: "来源不确定",
}

function focusParameter(path: string) {
  const input = document.getElementsByName(path)[0] as HTMLInputElement | undefined
  input?.scrollIntoView({ block: "center", behavior: "smooth" })
  input?.focus()
}

function visualFailureGuidance(report: MunicipalVisualReport, config: MunicipalVisionConfig | null): string {
  const env = report.vision_config?.credential_env || config?.credential_env || "对应 API key"
  switch (report.failure_code) {
    case "credentials_missing":
      return `配置入口：现有「设置 → 模型与供应商」补充 ${env}（会写入 .env），或检查 .env 的 OPENBIMAGENT_PROFILE。三视图和参数草稿已保留。`
    case "model_not_vision_capable":
      return "配置入口：config/models.toml 中 critic_scad 或其降级链需绑定至少一个声明 vision 的模型；当前不会把文本模型当作视觉模型调用。"
    case "timeout":
      return "本次视觉调用已超过有界时限，未得到可审阅结果；三视图保留。可稍后手动重新检查，不会自动反复调用。"
    case "rate_limited":
      return "视觉服务返回 429 限流，本次未得到结果；三视图保留。请稍后手动重新检查，不会自动反复调用。"
    case "response_invalid":
      return "模型已返回但不符合结构化契约，未应用任何建议；三视图保留，可重新检查。"
    case "stale_evidence":
      return "视觉结果与当前版本不一致，已拒绝。请对最新版本重新执行视觉检查。"
    case "provider_error":
      return "请检查模型端点与网络连通性；三视图和参数草稿已保留，不会显示虚构通过或评分。"
    case "render_failed":
      return "请检查 OpenSCAD 路径（OPENSCAD_PATH）与模型几何；参数草稿保留，视觉模型未调用。"
    case "missing_compiled_ir":
      return "请先补齐节点坐标、标高和管段尺寸并保存，再执行视觉检查。"
    case "configuration_error":
      return "请检查 config/models.toml、OPENBIMAGENT_PROFILE 和当前 profile 的角色绑定。"
    default:
      return report.error || "视觉检查未完成；确定性检查仍是唯一工程判定来源。"
  }
}

function diagnosticsMs(value: unknown): string {
  return typeof value === "number" ? `${Math.round(value)} ms` : "—"
}

function DiagnosticsDetails({ report, config }: { report: MunicipalVisualReport; config: MunicipalVisionConfig | null }) {
  const diagnostics = report.diagnostics
  const attempts = diagnostics?.attempts || []
  const request = diagnostics?.request
  return <details className="rounded-md border border-border p-2 text-xs text-muted-foreground">
    <summary className="cursor-pointer">视觉调用详情（模型/provider/端点/计时/传输事件）</summary>
    <div className="mt-2 space-y-1">
      <p>profile {report.profile || config?.profile || "—"} · 配置模型 {report.model_requested || config?.configured_model || "—"}</p>
      <p>实际尝试模型 {report.model_attempted || report.model_alias || "—"} · 视觉链首选 {report.model_effective || config?.effective_model || "—"} · 服务返回 {report.model || diagnostics?.response_model || "未知"}</p>
      <p>provider {report.provider || diagnostics?.selected_provider || config?.provider || "—"} · 端点 {report.endpoint || diagnostics?.selected_endpoint || config?.endpoint || "—"}</p>
      {request && <p>请求：{request.image_count ?? "?"} 张图 · 文本 {request.text_chars ?? "?"} 字符 · 上下文 {request.context_chars ?? "?"} 字符 · schema {request.response_schema_chars ?? "?"} 字符 · reasoning 档位 {request.reasoning_level || "—"}</p>}
      {!!request?.images?.length && <p>图片：{request.images.map(image => `${image.view_id || "?"} ${image.width_px || "?"}×${image.height_px || "?"} ${image.encoded_bytes ?? "?"} B`).join(" · ")}</p>}
      <p>业务结论：{diagnostics?.business_outcome || (report.vlm_called ? "已调用" : "未调用")}；诊断事件不等于有效视觉报告。</p>
      {attempts.length === 0 ? <p>没有传输尝试记录。</p> : <ol className="space-y-1">{attempts.map((attempt, index) => <li key={index} className="break-words">
        <span className="font-medium">#{attempt.retry_index ?? index} {attempt.model || "—"}</span> · {attempt.provider || "—"} · {attempt.endpoint || "—"}
        <span className="block">请求已发出：{attempt.request_sent ? "是" : "否"} · HTTP {attempt.http_status ?? "未知"} · finish {attempt.finish_reason || "未知"} · 正文 {attempt.content_chars ?? "?"} 字符 · reasoning {attempt.reasoning_chars ?? "?"} 字符 · usage {attempt.usage_present ? "已返回" : "未知"}</span>
        <span className="block">响应头 {diagnosticsMs(attempt.headers_at_ms)} · 首个流事件 {diagnosticsMs(attempt.first_event_at_ms)} · 首正文 {diagnosticsMs(attempt.first_content_at_ms)} · 结束 {diagnosticsMs(attempt.ended_at_ms)}</span>
        <span className="block">aborted：{attempt.aborted ? "是" : "否"} · 原因 {attempt.cancel_reason || "—"} · 关闭已请求：{attempt.close_requested ? "是" : "否"} · 后台线程退出：{attempt.worker_exited == null ? "未记录" : (attempt.worker_exited ? "是" : "否")}</span>
        {attempt.error_type && <span className="block text-destructive">{attempt.error_type}：{attempt.error}</span>}
      </li>)}</ol>}
      <p>截止时间 {diagnostics?.deadline_s ?? config?.timeout_s ?? "?"} 秒。超时只表示已请求中止；后台线程是否退出以上述记录为准，不宣称所有 provider 都能被硬中断。</p>
    </div>
  </details>
}

function geometryStatusLabel(status: string | undefined): string {
  switch (status) {
    case "verified": return "通过（实测网格顶点的尺寸与闭合性都已验证）"
    case "dimension_verified_topology_unverified": return "尺寸通过 · 闭合性未验证（宿主无面读回 API）"
    case "failed": return "未通过（实测与 IR 不一致）"
    case "unverified": return "未验证（宿主未返回可核对几何）"
    case "not_applicable": return "不适用（无直管实体）"
    case "not_checked": return "未检查"
    default: return "未验证"
  }
}

function dimensionStatusLabel(status: string | undefined): string {
  switch (status) {
    case "verified": return "已通过"
    case "failed": return "未通过"
    case "unverified": return "未验证"
    default: return "未验证"
  }
}

function topologyStatusLabel(status: string | undefined): string {
  switch (status) {
    case "verified": return "已验证（面/边读回）"
    case "failed": return "未通过"
    case "unverified": return "未验证（宿主未返回面/边数据）"
    default: return "未验证"
  }
}

function IssueCard({ issue, deterministicRuleIds }: { issue: MunicipalVisualIssue; deterministicRuleIds?: Set<string> }) {
  const kind = issue.observation_kind || "locatable_suspicion"
  const source = issue.evidence_source || "visual_observation"
  const sharesDeterministicRule = source === "visual_observation"
    && (issue.constraint_ids || []).some(id => deterministicRuleIds?.has(id))
  return <div className="rounded-md border border-border p-2 space-y-1">
    <p><span className="font-medium">{observationLabels[kind] || kind}</span> · {evidenceSourceLabels[source] || source} · {issue.object_ids?.join("、") || "未命名对象"} · {issue.view_id || "未给视图"} · {issue.severity || "info"}</p>
    <p className="break-words">{issue.description}</p>
    <p className="text-xs text-muted-foreground break-words">可见证据：{issue.evidence}</p>
    <p className="text-xs text-muted-foreground break-words">不确定性：{issue.uncertainty}</p>
    <p className="text-xs break-words">建议：{issue.suggestion}</p>
    {issue.constraint_ids?.length
      ? <p className="text-xs text-muted-foreground">关联规则：{issue.constraint_ids.join("、")}</p>
      : <p className="text-xs text-muted-foreground">未关联具体规则：这是视觉疑点，不是工程判定。</p>}
    {sharesDeterministicRule && <p className="text-xs text-muted-foreground">关联规则也出现在本轮确定性检查中；仅凭此条不能认定独立视觉发现。</p>}
    {!!issue.parameter_paths?.length && <div className="flex flex-wrap gap-2">
      {issue.parameter_paths.map(path => <button key={path} type="button" className="text-xs text-primary underline underline-offset-4" onClick={() => focusParameter(path)}>检查参数 {path}</button>)}
    </div>}
  </div>
}

function emptyCandidate(sessionId: string) {
  return {
    protocol_version: "0.1", request_id: sessionId,
    coordinate_reference: { crs_id: "local-project", origin: { x_m: 0, y_m: 0, z_m: 0 }, horizontal_unit: "m", vertical_unit: "m", vertical_datum: null },
    system_id: "wastewater-1", system_name: "重力污水管网",
    nodes: ["MH-1", "MH-2"].map(node_id => ({ node_id, node_type: "manhole", x_m: null, y_m: null, ground_elevation_m: null, invert_anchor_m: null, connection_radius_m: null })),
    segments: [{ segment_id: "P-1", start_node_id: "MH-1", end_node_id: "MH-2", diameter_mm: 300, material: "concrete",
      design_slope: null, surface_context: null, inner_diameter_mm: null, outer_diameter_mm: null }],
    collision_context: null,
  }
}

export function MunicipalWorkbench({ sessionId, sourceIr, onModelChange }: { sessionId: string | null; sourceIr?: any; onModelChange: (ir: any) => void }) {
  const [state, setState] = useState<MunicipalState | null>(null)
  const [draft, setDraft] = useState<Record<string, any> | null>(null)
  const [open, setOpen] = useState(true)
  const [busy, setBusy] = useState("")
  const [error, setError] = useState("")
  const [dirty, setDirty] = useState(false)
  const [reason, setReason] = useState("correction")
  const [reviewed, setReviewed] = useState(false)
  const [exportHost, setExportHost] = useState<"vectorworks" | "blender" | "">("")
  const [visionConfig, setVisionConfig] = useState<MunicipalVisionConfig | null>(null)
  const currentSession = useRef(sessionId)
  currentSession.current = sessionId
  const modelCallback = useRef(onModelChange)
  modelCallback.current = onModelChange

  function accept(next: MunicipalState) {
    setState(next); setDraft(structuredClone(next.candidate)); setDirty(false); setReviewed(false); setExportHost("")
    modelCallback.current(next.validation.compiled_ir ? { ...next.validation.compiled_ir, preview_collision_context: next.candidate.collision_context } : null)
    window.dispatchEvent(new CustomEvent("municipal-updated", { detail: { sessionId: next.session_id, state: next } }))
  }

  async function load() {
    if (!sessionId) return
    const sid = sessionId
    setBusy("加载任务"); setError("")
    try {
      const result = await api.getMunicipal(sid)
      if (currentSession.current !== sid) return
      if (result.state) accept(result.state)
      else { setState(null); setDraft(emptyCandidate(sid)); setDirty(true) }
    } catch (e: any) { if (currentSession.current === sid) setError(e.message) }
    finally { if (currentSession.current === sid) setBusy("") }
  }

  useEffect(() => {
    setState(null); setDraft(null); setDirty(false); setReviewed(false); setError(""); setExportHost("")
    void load()
  }, [sessionId])

  useEffect(() => {
    let active = true
    api.getMunicipalVisionConfig()
      .then(config => { if (active) setVisionConfig(config) })
      .catch((e: any) => { if (active) setVisionConfig({ status: "configuration_error", message: e.message }) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    const expand = () => setOpen(true)
    window.addEventListener("municipal-expand", expand)
    return () => window.removeEventListener("municipal-expand", expand)
  }, [])

  function edit(group: string, index: number, key: string, value: any) {
    setDraft(prev => { if (!prev) return prev; const next = structuredClone(prev); next[group][index][key] = value; return next })
    setDirty(true); setReviewed(false); setExportHost("")
  }
  function setRoot(key: string, value: any) {
    setDraft(prev => ({ ...prev, [key]: value })); setDirty(true); setReviewed(false); setExportHost("")
  }
  const bound = { base_revision: state?.revision || 0, candidate_sha256: state?.candidate_sha256 }
  async function action(name: string, label: string, extra: Record<string, any> = {}) {
    if (!sessionId || busy) return
    const sid = sessionId
    setBusy(label); setError("")
    try {
      const result = name === "save"
        ? await api.saveMunicipal(sid, { ...bound, candidate: draft, source: reason })
        : await api.municipalAction(sid, name, { ...bound, ...extra })
      if (currentSession.current === sid) accept(result.state)
    } catch (e: any) { if (currentSession.current === sid) setError(e.message) }
    finally { if (currentSession.current === sid) setBusy("") }
  }
  const locked = (path: string) => state?.locks.some(item => item.path === path) || false
  async function retryIfcOnly() {
    if (!sessionId || !state || busy) return
    const sid = sessionId
    setBusy("重试 IFC"); setError("")
    try {
      const result = await api.retryIfc(sid, state)
      if (currentSession.current === sid) accept(result.state)
    } catch (e: any) { if (currentSession.current === sid) setError(e.message) }
    finally { if (currentSession.current === sid) setBusy("") }
  }
  const hasObjectLock = (group: string, index: number) => state?.locks.some(item => item.path.startsWith(`/${group}/${index}/`)) || false
  function numeric(group: string, index: number, key: string, label: string, optional = false) {
    const path = `/${group}/${index}/${key}`
    return <label className="min-w-0 space-y-1" key={key}>
      <span className="block text-xs text-muted-foreground">{label}{optional ? "（可空）" : ""}</span>
      <span className="flex gap-1">
        <input name={path} aria-label={`${draft?.[group][index].node_id || draft?.[group][index].segment_id} ${label}`} type="number" step="any"
          value={draft?.[group][index][key] ?? ""} disabled={!!busy || locked(path)} className={fieldClass}
          onChange={e => edit(group, index, key, e.target.value === "" ? null : Number(e.target.value))} />
        <button type="button" aria-label={`${locked(path) ? "解锁" : "锁定"}${label}`} aria-pressed={locked(path)}
          title={dirty ? "保存后可以锁定参数" : `${locked(path) ? "解锁" : "锁定"}${label}`}
          disabled={!state || dirty || !!busy} onClick={() => action("lock", "更新参数锁", { path, locked: !locked(path) })}
          className="rounded px-1 text-muted-foreground hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40">
          {locked(path) ? <LockKeyhole size={14} /> : <UnlockKeyhole size={14} />}
        </button>
      </span>
    </label>
  }
  function remove(group: string, index: number) {
    if (state?.locks.some(item => item.path.startsWith(`/${group}/`))) {
      setError("这一组包含锁定参数，删除会改变对象顺序。请先解锁。"); return
    }
    setRoot(group, draft?.[group].filter((_: any, i: number) => i !== index))
  }
  function addNode() {
    let n = (draft?.nodes.length || 0) + 1
    while (draft?.nodes.some((node: any) => node.node_id === `MH-${n}`)) n++
    setRoot("nodes", [...(draft?.nodes || []), { node_id: `MH-${n}`, node_type: "manhole", x_m: null, y_m: null, ground_elevation_m: null, invert_anchor_m: null, connection_radius_m: null }])
  }
  function addSegment() {
    let n = (draft?.segments.length || 0) + 1
    while (draft?.segments.some((seg: any) => seg.segment_id === `P-${n}`)) n++
    setRoot("segments", [...(draft?.segments || []), { segment_id: `P-${n}`, start_node_id: "", end_node_id: "", diameter_mm: 300,
      material: "concrete", design_slope: null, surface_context: null, inner_diameter_mm: null, outer_diameter_mm: null }])
  }
  const issues = state?.validation.issues || []
  const problems = issues.filter(i => i.status !== "pass" && i.severity !== "info")
  const outsideScope = issues.filter(i => i.status !== "pass" && i.severity === "info")
  const visual = state?.visual || null
  const visualIssues = visual?.issues || []
  const visualPatches = visual?.patches || []
  const deterministicRuleIds = new Set(issues.filter(issue => issue.status !== "pass" && issue.rule_id).map(issue => issue.rule_id))
  const visualDone = !!visual && visual.status !== "unavailable"
  const visualProposalApplied = state?.history.at(-1)?.action === "repair" && state.history.at(-1)?.source === "visual_proposal"
  const visualPatchTouchesLock = visualPatches.some(p => state?.locks.some(lock =>
    p.path === lock.path || p.path.startsWith(lock.path.replace(/\/$/, "") + "/") || lock.path.startsWith(p.path.replace(/\/$/, "") + "/")))
  const ready = !!state?.validation.executable && !dirty
  const deliveryReceipt = state?.delivery?.receipt
  const deliveryGeometryStatus = state?.delivery?.geometry_status || deliveryReceipt?.geometry_status || "unverified"
  const deliverySemanticVerified = state?.delivery?.semantic_verified
    ?? deliveryReceipt?.semantic_comparison_report?.status === "PASS"
  const deliveryGeometryObjects = deliveryReceipt?.geometry_snapshot?.objects || []
  const deliveryGeometryCaveats: string[] = deliveryReceipt?.geometry_caveats || []
  const deliveryIfc = deliveryReceipt?.ifc
  const ifcGeneration = deliveryIfc?.generation
  const ifcChecks = deliveryIfc?.checks
  const ifcFailed = deliveryIfc?.status === "failed"
  const ifcVerified = deliveryIfc?.status === "verified"
  const ifcSegments: any[] = ifcChecks?.geometry?.segments || []
  const canRetryIfc = !!state?.confirmation && !!deliveryReceipt && ["completed", "partial"].includes(state?.delivery?.status || "")
  const ifcDownloadUrl = sessionId ? api.municipalIfcDownloadUrl(sessionId) : ""
  const previousDelivery = state ? [...state.history, state.delivery].filter(item => item.host === exportHost && item.revision === state.revision && item.status).at(-1) : undefined
  const retryDelivery = ["failed", "interrupted"].includes(previousDelivery?.status || "")
  return <section className="border-t border-border bg-background flex flex-col min-h-0 shrink-0" aria-label="管网检查与修复">
    <button className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
      aria-expanded={open} onClick={() => setOpen(!open)}>
      <span className="text-sm font-semibold">管网检查与修复 <span className="ml-2 font-normal text-muted-foreground">{dirty ? "有未保存修改" : state ? `版本 ${state.revision} · ${ready ? "可人工复核" : "待处理"}` : "补充工程条件"}</span></span>
      {open ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
    </button>
    {open && <div className="max-h-[62vh] overflow-y-auto px-4 pb-4 space-y-5 text-sm">
      {!sessionId ? <p className="text-muted-foreground">先新建或选择一个市政工程对话，再补充坐标、标高和管材尺寸。</p> : <>
        <p className="max-w-prose text-muted-foreground">小型重力污水网：填写条件 → 检查问题 → 审阅修复 → 锁定参数 → 确认导出。坐标和标高单位为米；DN 是名义管径，实际内外径请按管材资料填写。</p>
        {!state && sourceIr?.segments?.length > 0 && <Button variant="outline" disabled={!!busy} onClick={() => action("import-ir", "导入对话生成的管网", { compiled_ir: sourceIr })}>从当前对话模型开始（替换未保存表单）</Button>}
        {error && <div role="alert" className="rounded-md border border-destructive p-3 space-y-2"><p className="break-words">{error}</p>
          <Button size="sm" variant="outline" disabled={!!busy} onClick={load}>重新加载已保存版本</Button>
          <p className="text-xs text-muted-foreground">重新加载会舍弃表单中未保存的修改。</p></div>}
        {busy && <p role="status" aria-live="polite" className="text-muted-foreground">{busy}中，请稍候…</p>}
        {draft && <fieldset disabled={!!busy} className="space-y-5 disabled:opacity-70">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="space-y-1"><span className="block text-xs text-muted-foreground">工程名称</span><input className={fieldClass} value={draft.system_name || ""} onChange={e => setRoot("system_name", e.target.value)} /></label>
            <label className="space-y-1"><span className="block text-xs text-muted-foreground">高程基准（例如项目采用的高程基准）</span><input className={fieldClass} value={draft.coordinate_reference?.vertical_datum || ""}
              onChange={e => setRoot("coordinate_reference", { ...draft.coordinate_reference, vertical_datum: e.target.value || null })} /></label>
          </div>
          <div className="space-y-3">
            <div className="flex justify-between items-center"><h3 className="font-medium">节点与标高</h3><Button size="sm" variant="outline" onClick={addNode}><Plus size={14} />添加节点</Button></div>
            <p className="text-xs text-muted-foreground">起点必须给管底标高；下游留空时按坡度推算。三条及以上管段相接的节点请选择「汇流节点」。</p>
            {draft.nodes.map((node: any, i: number) => <div key={i} className="space-y-2 border-b border-border pb-3">
              <div className="flex flex-wrap items-center gap-2">
                <input aria-label={`节点 ${i + 1} 编号`} className={`${fieldClass} max-w-28`} value={node.node_id} disabled={hasObjectLock("nodes", i)} onChange={e => edit("nodes", i, "node_id", e.target.value)} />
                <select aria-label={`${node.node_id} 节点类型`} className={`${fieldClass} max-w-36`} value={node.node_type} onChange={e => edit("nodes", i, "node_type", e.target.value)}>
                  <option value="manhole">检查井</option><option value="junction">汇流节点</option><option value="outlet">出口</option><option value="inlet">入口</option><option value="terminal">末端</option>
                </select>
                <Button size="sm" variant="ghost" aria-label={`删除节点 ${node.node_id}`} onClick={() => remove("nodes", i)}><Trash2 size={14} /></Button>
              </div>
              <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">{numeric("nodes", i, "x_m", "X 坐标 (m)")}{numeric("nodes", i, "y_m", "Y 坐标 (m)")}{numeric("nodes", i, "ground_elevation_m", "地面标高 (m)")}{numeric("nodes", i, "invert_anchor_m", "管底标高 (m)", true)}{numeric("nodes", i, "connection_radius_m", "连接区域半径 (m)", true)}</div>
            </div>)}
          </div>
          <p className="text-xs text-muted-foreground">连接区域半径需依据井或管件的实际连接范围填写；仅在共用节点附近豁免预期连接重叠，不是全管段免检，也不代表检查井尺寸已建模。相邻管段连接范围不明确时会提示待补充。</p>
          <div className="space-y-3">
            <div className="flex justify-between items-center"><h3 className="font-medium">管段与尺寸</h3><Button size="sm" variant="outline" onClick={addSegment}><Plus size={14} />添加管段</Button></div>
            {draft.segments.map((seg: any, i: number) => <div key={i} className="space-y-2 border-b border-border pb-3">
              <div className="flex flex-wrap items-center gap-2">
                <input aria-label={`管段 ${i + 1} 编号`} className={`${fieldClass} max-w-24`} value={seg.segment_id} disabled={hasObjectLock("segments", i)} onChange={e => edit("segments", i, "segment_id", e.target.value)} />
                {(["start_node_id", "end_node_id"] as const).map((key, j) => <label key={key} className="flex items-center gap-1"><span className="text-xs text-muted-foreground">{j === 0 ? "上游" : "下游"}</span><select aria-label={`${seg.segment_id} ${j === 0 ? "上游" : "下游"}`} className={`${fieldClass} max-w-28`} value={seg[key]} onChange={e => edit("segments", i, key, e.target.value)}><option value="">选择节点</option>{draft.nodes.map((n: any) => <option key={n.node_id} value={n.node_id}>{n.node_id}</option>)}</select></label>)}
                <span className="text-xs text-muted-foreground">DN300 · 混凝土</span>
                <Button size="sm" variant="ghost" aria-label={`删除管段 ${seg.segment_id}`} onClick={() => remove("segments", i)}><Trash2 size={14} /></Button>
              </div>
              <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">{numeric("segments", i, "design_slope", "坡度 (m/m)")}{numeric("segments", i, "inner_diameter_mm", "实际内径 (mm)")}{numeric("segments", i, "outer_diameter_mm", "实际外径 (mm)")}
                <label className="space-y-1"><span className="block text-xs text-muted-foreground">地表条件</span><select className={fieldClass} value={seg.surface_context || ""} onChange={e => edit("segments", i, "surface_context", e.target.value || null)}><option value="">请选择</option><option value="driveway">车行道</option><option value="sidewalk">人行道</option></select></label>
              </div>
            </div>)}
          </div>
          <details className="space-y-3"><summary className="cursor-pointer font-medium focus-visible:ring-2 focus-visible:ring-ring">障碍物与检查范围</summary>
            <p className="text-xs text-muted-foreground">没有现场资料时保持未确认，不会把缺失的障碍物当作“无碰撞”。下方建筑物按轴对齐实体盒检查；复杂外形需要单独建模。</p>
            <label className="flex items-start gap-2"><input type="checkbox" className="mt-1" checked={draft.collision_context?.coverage === "complete"}
              onChange={e => setRoot("collision_context", e.target.checked ? { coverage: "complete", obstacles: draft.collision_context?.obstacles || [] } : null)} />我已核对当前检查范围内的障碍物清单（空清单表示已确认无障碍物）</label>
            {draft.collision_context && <>
              {(draft.collision_context.obstacles || []).map((obs: any, i: number) => <div key={i} className="space-y-2 border-b pb-3">
                <div className="flex items-center justify-between"><span>{obs.obstacle_id} · {obs.kind === "aabb" ? "建筑实体盒" : "既有管线"}</span><Button size="sm" variant="ghost" aria-label={`删除障碍物 ${obs.obstacle_id}`} onClick={() => setRoot("collision_context", { ...draft.collision_context, obstacles: draft.collision_context.obstacles.filter((_: any, j: number) => i !== j) })}><Trash2 size={14} /></Button></div>
                {obs.kind === "aabb" ? <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">{["min_corner", "max_corner"].flatMap(corner => ["x_m", "y_m", "z_m"].map(axis => <label key={`${corner}-${axis}`} className="space-y-1"><span className="text-xs text-muted-foreground">{corner === "min_corner" ? "最小" : "最大"} {axis[0].toUpperCase()} (m)</span><input className={fieldClass} type="number" step="any" value={obs[corner][axis] ?? ""} onChange={e => { const context = structuredClone(draft.collision_context); context.obstacles[i][corner][axis] = e.target.value === "" ? null : Number(e.target.value); setRoot("collision_context", context) }} /></label>))}</div> : <p className="text-xs text-muted-foreground">已保留导入的既有管线参数；当前表单仅支持编辑盒状障碍物。</p>}
              </div>)}
              <Button size="sm" variant="outline" onClick={() => setRoot("collision_context", { ...draft.collision_context, obstacles: [...draft.collision_context.obstacles, { obstacle_id: `building-${crypto.randomUUID().slice(0, 8)}`, kind: "aabb", category: "building", min_corner: { x_m: null, y_m: null, z_m: null }, max_corner: { x_m: null, y_m: null, z_m: null } }] })}><Plus size={14} />添加建筑实体盒</Button>
            </>}
          </details>
          <div className="flex flex-wrap items-end gap-3">
            <label className="space-y-1"><span className="block text-xs text-muted-foreground">本次修改原因</span><select className={fieldClass} value={reason} onChange={e => setReason(e.target.value)}><option value="correction">纠正错误</option><option value="preference">调整个人偏好</option><option value="requirement_change">改变需求或设计目标</option></select></label>
            <Button onClick={() => action("save", "保存并检查")}>保存并检查</Button>
            <Button variant="outline" disabled={dirty || !state} onClick={() => action("propose", "生成修复建议")}>生成修复建议</Button>
            <Button variant="outline" disabled={dirty || !state?.validation.compiled_ir} onClick={() => action("visual", "渲染并视觉检查")}>视觉检查</Button>
          </div>
        </fieldset>}
        {state && <div className="space-y-4">
          <div className="space-y-2"><h3 className="font-medium">检查结果{dirty ? "（对应已保存版本）" : ""}</h3>
            <p className="text-xs text-muted-foreground">几何与领域规则判断数值；视觉检查提供可定位的疑点。未检查项不算通过，也不代表已完成水力或工程合规审查。</p>
            {problems.length === 0 ? <p>当前检查范围内没有待处理问题。</p> : <ul className="divide-y divide-border">{problems.map((issue: any, i: number) => <li key={i} className="py-2 space-y-1">
              <p><span className="font-medium">{issue.status === "fail" ? "需修正" : "待补充"}</span> · {issue.object_ids?.join("、") || "管网"} · {issue.message || issue.detail || issue.evidence || issue.check_name}</p>
              {issue.measured_value != null && <p className="text-xs text-muted-foreground">计算值 {String(issue.measured_value)} {issue.unit} · 要求 {String(issue.required_value ?? "待核实")}</p>}
              <p className="text-xs text-muted-foreground break-words">{issue.rule_id}{issue.field_path ? ` · ${issue.field_path}` : ""}</p>
              {issue.evidence?.source_clause && <p className="text-xs text-muted-foreground break-words">依据：{issue.evidence.source_clause}</p>}
              {issue.field_path && <button className="text-xs text-primary underline underline-offset-4" onClick={() => { const input = document.getElementsByName(issue.field_path)[0]; input?.scrollIntoView({ block: "center", behavior: "smooth" }); input?.focus() }}>定位参数</button>}
            </li>)}</ul>}
          </div>
          {state.proposal && <div className="space-y-2"><h3 className="font-medium">建议修改</h3>{state.proposal.patches.length ? <>
            <ul className="space-y-2">{state.proposal.patches.map((p, i) => <li key={i} className="break-words"><span className="font-medium">{p.path}</span>：{String(p.old_value)} → {String(p.value)}<p className="text-xs text-muted-foreground">{p.reason}</p></li>)}</ul>
            <Button variant="outline" disabled={dirty || !!busy} onClick={() => action("apply", "应用修复并复查", { proposal_id: state.proposal?.proposal_id })}>应用这些修改并复查</Button>
          </> : <p className="text-muted-foreground">没有可安全自动修改的参数。请补充缺失条件、处理锁定冲突或人工调整。</p>}</div>}
          <div className="space-y-2"><h3 className="font-medium">视觉检查</h3>
            <p className="text-xs text-muted-foreground">当前检查：版本 {state.revision}{dirty ? "（表单有未保存修改，结果对应已保存版本）" : ""}；先生成三视图，再检查可定位疑点/证据不足/建议参数和有限补丁。确定性检查仍是工程判定来源。</p>
            <div className="rounded-md border border-border p-2 space-y-1 text-xs text-muted-foreground">
              <p>角色 critic_scad · profile {visionConfig?.profile || "未读取"} · 配置模型 {visionConfig?.configured_model || "—"}</p>
              {visionConfig?.effective_model && visionConfig.effective_model !== visionConfig.configured_model && <p>实际首选模型：{visionConfig.effective_model}（原绑定模型无视觉能力或不可用）</p>}
              <p>凭据变量 {visionConfig?.credential_env || "—"}：{visionConfig ? (visionConfig.credential_present ? "已配置（不显示值）" : "未配置") : "未读取"}；配置声明视觉能力：{visionConfig ? (visionConfig.configured_vision ? "是" : "否") : "未读取"}（声明不等于本轮真实调用成功）</p>
              <p>OpenSCAD：{visionConfig?.openscad?.available ? "可用" : "不可用或未找到"}；单次有界重试：{visionConfig?.retry_max ?? 1} 次/模型，截止 {visionConfig?.timeout_s ?? 90} 秒。provider、端点和传输诊断见下方详情。</p>
              {visionConfig && visionConfig.status !== "ready" && <p className="text-destructive break-words">{visionConfig.message || `配置状态：${visionConfig.status || "不可用"}`}</p>}
            </div>
            {visualProposalApplied && <p className="rounded-md border border-border p-2 text-xs">视觉建议已应用并通过完整确定性复核；旧视觉结果与人工确认已失效。请重新执行视觉检查，确认疑点是否消除。</p>}
            <p className="text-muted-foreground">{visual ? visual.summary || visual.error || `已记录视觉结果（${visual.status || "待复核"}）` : "尚未执行。视觉检查会先生成轴测/正视/俯视图，再调用配置的视觉模型。"}</p>
            {visual && <div className="text-xs text-muted-foreground space-y-0.5">
              <p>三视图：{visual.preview ? `已生成（版本 ${visual.revision ?? state.revision}）` : "未生成或不可用"}</p>
              <p>视觉模型：{visual.vlm_called ? (visual.failure_code ? "已调用，但未得到可审阅结果" : "已调用成功") : "未调用"}{visual.model ? ` · 服务返回模型 ${visual.model}` : ""}{visual.model_attempted && visual.model_attempted !== visual.model ? ` · 实际尝试别名 ${visual.model_attempted}` : ""}</p>
              <p>耗时：总 {visual.duration_ms ?? "未知"} ms · 视觉调用 {visual.vlm_duration_ms ?? "未知"} ms · 渲染 {visual.render_duration_ms ?? "未知"} ms（不依赖 usage 是否返回）</p>
              {visual.usage?.total_tokens != null && <p>用量：{visual.usage.total_tokens} tokens（输入 {visual.usage.prompt_tokens ?? "?"} / 输出 {visual.usage.completion_tokens ?? "?"}）</p>}
            </div>}
            {visual && <DiagnosticsDetails report={visual} config={visionConfig} />}
            {visual?.error && <p role="alert" className="break-words text-destructive">{visual.error}</p>}
            {visual?.failure_code && <p className="text-xs text-muted-foreground break-words">{visualFailureGuidance(visual, visionConfig)}</p>}
            {visual?.status === "observed" && <p className="text-xs text-muted-foreground">本次视觉检查未返回可定位疑点。这不等于工程合规通过；确定性检查仍是唯一工程判定来源。</p>}
            {!!visualIssues.length && <div className="space-y-2">{visualIssues.map((item, i) => <IssueCard key={i} issue={item} deterministicRuleIds={deterministicRuleIds} />)}</div>}
            {visual?.preview && <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">{["iso", "front", "top"].map((view, i) => <figure key={view}><img className="w-full rounded border border-border" src={`/api/v1/municipal/${encodeURIComponent(sessionId || "")}/preview/${view}?revision=${visual.revision ?? state.revision}`} alt={`${["轴测", "正视", "俯视"][i]}图，版本 ${visual.revision ?? state.revision}`} /><figcaption className="text-xs text-muted-foreground">{["轴测", "正视", "俯视"][i]} · 已保存版本 {visual.revision ?? state.revision}</figcaption></figure>)}</div>}
            {!!visualPatches.length && <div className="rounded-md border border-border p-2 space-y-1">
              <p className="font-medium">视觉建议的有限修改（尚未应用）</p>
              <ul className="space-y-1 text-xs">{visualPatches.map((p, i) => <li key={i} className="break-words"><span className="font-medium">{p.path}</span>：{String(p.old_value)} → {String(p.value)}<span className="block text-muted-foreground">{p.reason}</span></li>)}</ul>
              {visualPatchTouchesLock && <p className="text-xs text-destructive">该建议触碰锁定参数，后端会拒绝应用；请先明确解锁并重新检查。</p>}
              <Button variant="outline" disabled={dirty || !!busy || visualPatchTouchesLock} onClick={() => action("propose", "展示视觉修复建议", { source: "visual" })}>审阅视觉修改建议</Button>
              <p className="text-xs text-muted-foreground">应用后会重新运行完整确定性校验；旧视觉结果和人工确认失效，必须再次视觉检查才能声称视觉疑点已修复。</p>
            </div>}
          </div>
          <div className="border-t border-border pt-4 space-y-3">
            {!!outsideScope.length && <div className="text-xs text-muted-foreground"><p>本轮未检查范围（不计为通过）：</p><ul className="list-disc pl-4">{outsideScope.map((issue: any, i: number) => <li key={i}>{issue.detail}</li>)}</ul></div>}
            <label className="flex items-start gap-2"><input type="checkbox" className="mt-1" checked={reviewed} disabled={!ready || !!busy} onChange={e => setReviewed(e.target.checked)} /><span>我已核对当前参数、问题列表及未检查范围{!visualDone ? "，知悉本版尚未完成视觉检查" : "，已查看视觉结果与不确定性"}，确认这一版可交给宿主执行。</span></label>
            <div className="flex flex-wrap gap-2"><Button disabled={!ready || !reviewed || !!busy} onClick={() => action("confirm", "确认当前版本", { reviewed: true, review_token: state.review_token })}>确认版本 {state.revision}</Button>
              <Button variant="outline" disabled={dirty || !state.confirmation || !!busy} onClick={() => setExportHost("vectorworks")}>导出至 Vectorworks</Button>
              <Button variant="ghost" disabled={dirty || !state.confirmation || !!busy} onClick={() => setExportHost("blender")}>导出至 Blender</Button></div>
            {!ready && <p className="text-xs text-muted-foreground">先保存修改并解决硬约束问题，才能确认和导出。</p>}
            {exportHost && <div className="rounded-md border border-border p-3 space-y-2">
              <p>将当前确认的版本 {state.revision} 交给 {exportHost === "vectorworks" ? "Vectorworks" : "Blender"}。已完成的同版本宿主请求会返回原回执；新执行使用独立文件名。</p>
              <p className="text-xs text-muted-foreground">{exportHost === "vectorworks"
                ? "本轮 Vectorworks 仅支持直管内外表面网格实体（外壁、内壁、两端环面）；节点/检查井仍是示意对象，不支持弯头或三通实体。几何读回按实测网格顶点核对尺寸；闭合性（面数/重复面/朝向/流形边）无法在 Vectorworks 上读回验证。执行前需：Vectorworks 已运行并加载 runner、当前为未命名空白文档、文档单位为米、输出在授权根内。"
                : "Blender 使用独立后台进程生成管道外形；检查井仍是示意对象。"} 交付分三层：宿主结果、IFC 生成、IFC 检查。IFC 为 IR 派生的直管空腔拉伸实体（IfcCircleHollowProfileDef + IfcExtrudedAreaSolid），不是 Vectorworks 原生导出；宿主 partial 若仅因闭合性未验证，不需要重新执行 CAD。</p>
              {retryDelivery && <p>前次执行未完成或结果未知。请先核对已有文件及宿主文档，确认需要重新执行；本次可能生成另一份宿主模型。</p>}
              <div className="flex gap-2"><Button disabled={!!busy} onClick={() => action("export", "宿主执行与结果检查", { host: exportHost, confirm: true, ...(retryDelivery ? { retry_of: previousDelivery?.execution_id } : {}) })}>{retryDelivery ? "已核对宿主，重新执行" : "确认执行"}</Button><Button variant="ghost" onClick={() => setExportHost("")}>取消</Button></div>
            </div>}
            {state.delivery.status && <div role="status" className="break-words space-y-1">
              <p>{state.delivery.status === "completed" ? "宿主实体、语义和几何读回均通过；IFC 结果见下方分层。" : state.delivery.status === "partial" ? "宿主文件已生成（或部分生成）；宿主、IFC 生成与 IFC 检查分层见下。" : state.delivery.status === "running" ? "宿主正在执行…" : "宿主执行未完成。"} {state.delivery.error || state.delivery.receipt?.errors?.join("；")}</p>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs">
                <p>宿主结果：{state.delivery.status === "completed" ? "完成" : state.delivery.status === "partial" ? "部分完成" : state.delivery.status === "running" ? "执行中" : "未完成"}</p>
                <p>语义读回：{deliverySemanticVerified ? "通过" : deliveryReceipt?.semantic_comparison_report ? "未通过" : "未验证"}</p>
                <p>几何读回：{geometryStatusLabel(deliveryGeometryStatus)}</p>
              </div>
              {deliveryReceipt?.output_path && <p className="text-xs">输出：{deliveryReceipt.output_path}</p>}
              {!!deliveryGeometryObjects.length && <details className="text-xs text-muted-foreground">
                <summary className="cursor-pointer">几何读回详情（按构件 ID）</summary>
                <ul className="mt-1 space-y-1">{deliveryGeometryObjects.map((item: any) => <li key={item.stable_id} className="break-words">
                  <span className="font-medium">{item.stable_id}</span>：{geometryStatusLabel(item.status)}
                  {item.measured && <span> · 实测内径 {item.measured.inner_diameter_mm ?? "未读取"} mm · 外径 {item.measured.outer_diameter_mm ?? "未读取"} mm · 长度 {item.measured.length_m ?? "未读取"} m · 网格顶点 {item.measured.vertex_count ?? "未读取"}</span>}
                  {item.dimension_status && <span> · 尺寸 {dimensionStatusLabel(item.dimension_status)} · 闭合性 {topologyStatusLabel(item.topology_status)}</span>}
                  {!!item.failures?.length && <span className="block text-destructive">{item.failures.join("；")}</span>}
                </li>)}</ul>
              </details>}
              {!!deliveryGeometryCaveats.length && <details className="text-xs text-muted-foreground">
                <summary className="cursor-pointer">几何验证限制（{deliveryGeometryCaveats.length} 条）</summary>
                <ul className="mt-1 list-disc pl-4 space-y-1">{deliveryGeometryCaveats.map((item, i) => <li key={i} className="break-words">{item}</li>)}</ul>
              </details>}
              {!!deliveryReceipt?.geometry_failures?.length && <p className="text-xs text-destructive">几何失败：{deliveryReceipt.geometry_failures.flat?.().join("；") || String(deliveryReceipt.geometry_failures)}</p>}
              {deliveryIfc && <div className="rounded-md border border-border p-3 space-y-2 text-xs">
                <p className="font-medium">IFC 交付（IR 派生，非 Vectorworks 原生导出）</p>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <p>生成：{ifcGeneration?.status === "generated" ? "已生成" : ifcGeneration?.status === "failed" ? "生成失败" : "未执行"}</p>
                  <p>模式检查：{ifcChecks?.ifc_schema?.status === "pass" ? `通过（${ifcChecks.ifc_schema.schema}）` : ifcChecks?.ifc_schema?.status === "fail" ? "未通过" : "未执行"}</p>
                  <p>IDS/语义：{ifcChecks?.ids_semantic?.status === "pass" ? `通过（${ifcChecks.ids_semantic.findings ?? 0} 项）` : ifcChecks?.ids_semantic?.status === "fail" ? "未通过" : "未执行"}</p>
                  <p>几何读回：{ifcChecks?.geometry?.status === "pass" ? `通过（${ifcChecks.geometry.checked_segments ?? 0} 段）` : ifcChecks?.geometry?.status === "fail" ? "未通过" : "未执行"}</p>
                  <p>领域规则：{ifcChecks?.domain_rules?.status || "未执行"}{ifcChecks?.domain_rules?.production_verification === "review_required" ? "（review_required）" : ""}</p>
                </div>
                {!!ifcChecks?.ids_semantic?.scope && <p className="text-muted-foreground">{ifcChecks.ids_semantic.scope}</p>}
                {!!ifcChecks?.domain_rules?.scope && <p className="text-muted-foreground">{ifcChecks.domain_rules.scope}</p>}
                {!!ifcChecks?.ifc_schema?.scope && <details className="text-muted-foreground">
                  <summary className="cursor-pointer">模式校验范围与问题（{ifcChecks.ifc_schema.issue_count ?? 0} 个错误）</summary>
                  <p className="break-words">{ifcChecks.ifc_schema.validator}：{ifcChecks.ifc_schema.scope}</p>
                  {!!ifcChecks.ifc_schema.duplicate_global_ids?.length && <p className="text-destructive break-all">重复 GlobalId：{ifcChecks.ifc_schema.duplicate_global_ids.join("、")}</p>}
                  {!!ifcChecks.ifc_schema.invalid_global_ids?.length && <p className="text-destructive break-all">非法 GlobalId：{ifcChecks.ifc_schema.invalid_global_ids.join("、")}</p>}
                  {!!ifcChecks.ifc_schema.issues?.length && <ul className="mt-1 list-disc pl-4 space-y-1">{ifcChecks.ifc_schema.issues.slice(0, 10).map((item: any, i: number) => <li key={i} className="break-words">[{item.level}] {item.message}{item.instance ? `（${item.instance}）` : ""}</li>)}</ul>}
                </details>}
                {ifcGeneration?.status === "generated" && <div className="flex flex-wrap items-center gap-2">
                  <a className="underline" href={ifcDownloadUrl} download>下载 IFC</a>
                  <details className="text-muted-foreground"><summary className="cursor-pointer">哈希与文件</summary>
                    <p className="break-all">SHA-256：{ifcGeneration.ifc_sha256}</p>
                    <p className="break-all">文件：{ifcGeneration.ifc_path}（{ifcGeneration.ifc_bytes ?? "?"} 字节）</p>
                    {ifcGeneration.report_path && <p className="break-all">IDS 报告：{ifcGeneration.report_path}</p>}
                    {ifcGeneration.geometry_report_path && <p className="break-all">几何报告：{ifcGeneration.geometry_report_path}</p>}
                  </details>
                </div>}
                {!!ifcSegments.length && <details className="text-muted-foreground">
                  <summary className="cursor-pointer">IFC 实测几何（{ifcSegments.length} 段）</summary>
                  <ul className="mt-1 space-y-1">{ifcSegments.map((item: any) => <li key={item.stable_id} className="break-words">
                    <span className="font-medium">{item.stable_id}</span>：内径 {item.inner_diameter_mm} mm · 外径 {item.outer_diameter_mm} mm · 三维长度 {item.length_m} m · 体积一致性相对差 {item.volume_consistency_relative_error} · 离散误差 {item.discretization_relative_error}
                  </li>)}</ul>
                  <p className="mt-1">离散误差来自圆周多边形化，不是工程容差；宿主 topology=unverified 不会因 IFC 通过而变成通过。</p>
                </details>}
                {ifcFailed && <div className="space-y-1">
                  <p className="text-destructive break-words">IFC 未通过：{deliveryIfc?.error || ifcGeneration?.error || "见报告"}</p>
                  {!!deliveryIfc?.affected_ids?.length && <p className="text-muted-foreground">涉及构件：{deliveryIfc.affected_ids.join("、")}</p>}
                  <p className="text-muted-foreground">IFC 失败不需要重新建模或重新执行 CAD：可复用当前宿主回执单独重试 IFC，不会再次调用宿主，也不会覆盖宿主文件。</p>
                  <Button size="sm" variant="outline" disabled={!!busy || !canRetryIfc} onClick={retryIfcOnly}>重试 IFC</Button>
                </div>}
                {ifcVerified && <p className="text-muted-foreground">IFC 文件已保存并重新打开验证；该结果不改变宿主闭合性未验证的结论。</p>}
              </div>}
            </div>}
          </div>
          <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">修改记录</summary><ol className="mt-2 space-y-1">{state.history.slice(-12).map((entry: any, i: number) => <li key={i}>版本 {entry.revision} · {entry.action} · {entry.source || entry.actor || "系统"} · {entry.at}</li>)}</ol><p className="mt-2">完整条件保存在当前任务中；节点与参数修改会使旧确认失效。</p></details>
        </div>}
      </>}
    </div>}
  </section>
}
