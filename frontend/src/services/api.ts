/**
 * openBIMAgent 类型化后端 API 客户端
 */

export interface SessionItem {
  session_id: string
  title: string
  playbook?: string | null
  mode?: string | null
  created_at: string
  last_active: string
  event_count: number
  archived?: boolean
  archived_at?: string
  workspace?: string
}

export interface SessionEvent {
  id: string
  type: string
  created_at: string
  payload?: any
}

export interface ProviderModel {
  id: string
  name: string
  context_length?: number
  context_window?: number
  max_tokens?: number
  max_output_tokens?: number
  input_types?: string[]
  output_types?: string[]
  supports_function_calling?: boolean
  capabilities?: string[]
  status?: string
}

export interface ProviderItem {
  id: string
  name: string
  enabled: boolean
  base_url: string
  api_format: string
  api_key: string
  key_set: boolean
  models: ProviderModel[]
}

export interface ModelsSettingsData {
  providers: ProviderItem[]
  current: string
}

export interface ApprovalItem {
  id: string
  operation: string
  created_at: string
  status: string
  params?: Record<string, any>
  decision?: string
  instruction?: string
}

export interface UsageRecordItem {
  ts: string
  model: string
  source: string
  session_id?: string | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  latency_ms?: number | null
  usage_reported?: boolean
}

export interface UsageSummary {
  total: {
    calls: number
    total_tokens: number
    prompt_tokens: number
    completion_tokens: number
    cost_usd?: number
  }
  by_model?: Record<string, {
    calls: number
    total_tokens: number
    prompt_tokens: number
    completion_tokens: number
    cost_usd?: number
  }>
  by_source?: Record<string, number>
  daily?: Array<{
    date: string
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
    calls: number
  }>
  history?: Array<{
    date: string
    prompt_tokens: number
    completion_tokens: number
    calls: number
  }>
  recent?: UsageRecordItem[]
  logged_calls?: number
}

export interface ArchiveRecord {
  session_id: string
  brief?: string
  pack: string
  archived_at: string
  files: Array<{ name: string; size: number }>
}

export interface HostStatus {
  id: string
  name: string
  status: "up" | "down" | "restarting" | "external"
  last_seen?: string
  error?: string
}

export interface RuleItem {
  id: string
  category: string
  title: string
  description?: string
  severity: "BLOCKER" | "WARN" | "INFO"
  condition?: string
}

export interface UploadItem {
  id?: string
  name: string
  size: number
  sha256: string
  uploaded_at: string
}

export interface MemoryEntry {
  line: number
  text: string
  source: "user" | "memory"
}

export interface WorkspaceItem {
  id: string
  name: string
  path: string
  created_at?: number
  last_opened?: number
  session_count?: number
  execution_mode?: "agent" | "yolo" | "plan"
  outside_file_access?: "allow" | "ask" | "deny"
  terminal_auto_exec?: "proceed" | "ask"
  artifact_review_policy?: "proceed" | "ask"
  folders?: string[]
}

export interface PluginInfo {
  name: string
  version?: string
  description?: string
  capabilities?: string[]
  [key: string]: any
}

function rid(): string {
  return "fe-" + Math.random().toString(36).slice(2, 10)
}

/** 工作台令牌：优先 window.__WB_TOKEN（后端伺服 dist 时注入），回退 localStorage 缓存；vite dev 下可为空 */
export function getWorkbenchToken(): string {
  let token = typeof window !== "undefined" ? window.__WB_TOKEN || "" : ""
  if (!token && typeof localStorage !== "undefined") {
    token = localStorage.getItem("openbimagent_token") || ""
  }
  return token
}

if (typeof window !== "undefined" && window.__WB_TOKEN) {
  try {
    localStorage.setItem("openbimagent_token", window.__WB_TOKEN)
  } catch {}
}

/**
 * 会话事件 SSE 流地址。EventSource 无法设置请求头，生产环境鉴权（Bearer + X-Request-ID）
 * 由后端改为接受 ?token= 查询参数；无令牌（vite dev）时保持裸地址。
 */
export function buildSessionEventsStreamUrl(sessionId: string): string {
  const base = `/api/v1/sessions/${encodeURIComponent(sessionId)}/events/stream`
  const token = getWorkbenchToken()
  return token ? `${base}?token=${encodeURIComponent(token)}` : base
}

/** 事件是否暗示 compiled_utility_ir 产物已新建/更新（驱动 3D 视口免手动刷新） */
export function eventSuggestsIrUpdate(ev: SessionEvent): boolean {
  const p = ev.payload || {}
  if (p.ir || p.result_ir) return true
  if (p.customType === "artifact_committed" && p.artifact) {
    const a = p.artifact
    const hay = `${a.kind || ""} ${a.path || ""} ${a.relative_path || ""}`.toLowerCase()
    if (hay.includes("compiled_utility_ir")) return true
  }
  if (ev.type === "tool_call") {
    const views = [p.args_summary, p.result_llm_view, p.result_ui_view]
    for (const v of views) {
      const s = typeof v === "string" ? v : v ? JSON.stringify(v) : ""
      if (s.toLowerCase().includes("compiled_utility_ir")) return true
    }
  }
  return false
}

export interface RunActivity {
  role?: string
  tool?: string
  at?: string
}

// ---- 子代理生命周期（customType 见 backend session/schema.py CustomType）----

const SUBAGENT_CREATED_TYPES = new Set(["subagent_created", "subagent_started"])
const SUBAGENT_TERMINAL_TYPES = new Set(["subagent_completed", "subagent_failed", "subagent_cancelled"])

/** 事件是否是审批信号（approval_requested/decided → 立即刷新 HITL 卡片，不等 3s 轮询） */
export function isApprovalSignal(ev: SessionEvent): boolean {
  const ct = ev.payload?.customType
  return ct === "approval_requested" || ct === "approval_decided"
}

/** 从 subagent 工具调用 payload 提取 request_id（result_ui_view 结构化优先，llm_view/args 文本兜底） */
export function extractSubagentRequestId(payload: any): string | undefined {
  if (!payload) return undefined
  const ui = payload.result_ui_view
  if (ui && typeof ui === "object") {
    const rid = ui.request_id || ui.handle?.request_id
    if (typeof rid === "string" && rid) return rid
  }
  const text = `${payload.result_llm_view || ""} ${payload.args_summary || ""}`
  const m = text.match(/request_id=([0-9A-Za-z-]+)/)
  return m?.[1]
}

/** 工具卡视图模型：phase=call/result 事件对按 toolCallId 配对；requestId 对上生命周期事件时携带终态 */
export interface ToolCardModel {
  callEv: SessionEvent
  resultEv?: SessionEvent
  pending: boolean
  role?: string
  requestId?: string
  terminalStatus?: "completed" | "failed" | "cancelled"
  receipt?: boolean
}

/** 内联时间线标记（未与工具卡关联上的子代理终态/交付回执） */
export interface LifecycleMarkerModel {
  ev: SessionEvent
  status: "completed" | "failed" | "cancelled" | "delivery"
  role?: string
  receipt?: boolean
  error?: string
}

export type ThreadItem =
  | { kind: "message"; ev: SessionEvent }
  | { kind: "tool"; card: ToolCardModel }
  | { kind: "marker"; marker: LifecycleMarkerModel }

function isResultPhase(p: any): boolean {
  return p.phase === "result" || p.status != null || p.result_llm_view != null || p.result_ui_view != null
}

/**
 * 把会话事件流编译为主编排视图渲染序列：
 * message 原样；tool_call 按 toolCallId 配对成卡（未配对 result 的卡为进行中）；
 * subagent 生命周期事件先尝试按 request_id 认领工具卡（卡翻终态徽章，不再渲染标记），
 * 认领不上的渲染为内联时间线标记；delivery_receipt 认领失败时折叠进终态标记。
 */
export function deriveThreadView(events: SessionEvent[]): ThreadItem[] {
  const roleByReq = new Map<string, string>()
  const terminalByReq = new Map<string, { status: "completed" | "failed" | "cancelled"; ev: SessionEvent; error?: string }>()
  const receiptByReq = new Map<string, SessionEvent>()
  for (const ev of events) {
    const p = ev.payload || {}
    const ct = p.customType
    const rid = p.request_id ? String(p.request_id) : ""
    if (!rid) continue
    if (SUBAGENT_CREATED_TYPES.has(ct)) {
      if (p.role && !roleByReq.has(rid)) roleByReq.set(rid, String(p.role).toLowerCase())
    } else if (SUBAGENT_TERMINAL_TYPES.has(ct)) {
      terminalByReq.set(rid, {
        status: ct.replace("subagent_", "") as "completed" | "failed" | "cancelled",
        ev,
        error: p.error?.message,
      })
    } else if (ct === "delivery_receipt") {
      receiptByReq.set(rid, ev)
    }
  }

  const cards = new Map<string, ToolCardModel>()
  const cardOrder: ToolCardModel[] = []
  for (const ev of events) {
    if (ev.type !== "tool_call") continue
    const p = ev.payload || {}
    const key = String(p.toolCallId || ev.id)
    let card = cards.get(key)
    if (!card) {
      card = { callEv: ev, pending: true }
      cards.set(key, card)
      cardOrder.push(card)
    }
    if (isResultPhase(p)) {
      card.resultEv = ev
      card.pending = false
    } else {
      card.callEv = ev
    }
  }

  const claimed = new Set<string>()
  for (const card of cardOrder) {
    const merged = { ...(card.callEv.payload || {}), ...(card.resultEv?.payload || {}) }
    const isSub = merged.toolName === "subagent" || !!merged.agent_role || !!merged.agent || !!merged.batch
    if (!isSub) continue
    const rid = extractSubagentRequestId(merged)
    if (!rid) continue
    card.requestId = rid
    claimed.add(rid)
    const argsRole = String(merged.args_summary || "").match(/role=([a-z_]+)/i)?.[1]
    card.role =
      String(merged.agent_role || merged.agent || merged.batch || argsRole || "").toLowerCase() ||
      roleByReq.get(rid)
    const t = terminalByReq.get(rid)
    if (t) card.terminalStatus = t.status
    if (receiptByReq.has(rid)) card.receipt = true
  }

  const items: ThreadItem[] = []
  const emitted = new Set<ToolCardModel>()
  for (const ev of events) {
    if (ev.type === "message") {
      items.push({ kind: "message", ev })
      continue
    }
    if (ev.type === "tool_call") {
      const p = ev.payload || {}
      const card = cards.get(String(p.toolCallId || ev.id))
      if (card && !emitted.has(card)) {
        emitted.add(card)
        items.push({ kind: "tool", card })
      }
      continue
    }
    if (ev.type === "custom") {
      const p = ev.payload || {}
      const ct = p.customType
      const rid = p.request_id ? String(p.request_id) : ""
      if (SUBAGENT_TERMINAL_TYPES.has(ct)) {
        if (rid && claimed.has(rid)) continue // 卡已翻终态徽章
        const t = terminalByReq.get(rid)
        items.push({
          kind: "marker",
          marker: {
            ev,
            status: (t?.status || "completed") as "completed" | "failed" | "cancelled",
            role: roleByReq.get(rid),
            receipt: receiptByReq.has(rid),
            error: t?.error,
          },
        })
      } else if (ct === "delivery_receipt") {
        if (rid && claimed.has(rid)) continue // 卡内已标注交付
        if (rid && terminalByReq.has(rid)) continue // 折叠进终态标记
        items.push({ kind: "marker", marker: { ev, status: "delivery", role: roleByReq.get(rid), receipt: true } })
      }
    }
  }
  return items
}

/** 运行收尾统计：窗口内已配对 result 的工具调用次数 + 用时秒数（数据全部来自已有事件） */
export function deriveRunWrapUp(
  events: SessionEvent[],
  sinceMs: number,
  untilMs: number
): { toolCalls: number; seconds: number } {
  let toolCalls = 0
  for (const ev of events) {
    if (ev.type !== "tool_call") continue
    if (!isResultPhase(ev.payload || {})) continue
    const t = Date.parse(ev.created_at || "")
    if (!Number.isNaN(t) && t < sinceMs - 5000) continue // 容忍服务端时钟轻微提前
    toolCalls++
  }
  return { toolCalls, seconds: Math.max(0, Math.round((untilMs - sinceMs) / 1000)) }
}

/** 从事件流尾部推导当前活动：最近工具调用的子代理角色 + 工具名（状态行 "modeler · bash" 数据源）。
 *  已达终态的子代理调用不再视为当前活动，继续向前寻找；custom 生命周期事件不算活动。 */
export function deriveRunActivity(events: SessionEvent[]): RunActivity {
  const terminalReqs = new Set<string>()
  for (const ev of events) {
    const p = ev.payload || {}
    if (SUBAGENT_TERMINAL_TYPES.has(p.customType) && p.request_id) {
      terminalReqs.add(String(p.request_id))
    }
  }
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i]
    const p = ev.payload || {}
    if (ev.type === "tool_call") {
      const rid = extractSubagentRequestId(p)
      if (rid && terminalReqs.has(rid)) continue // 该子代理已终态,活动行不停留在它身上
      const role = String(p.agent_role || p.agent || p.batch || "").toLowerCase() || undefined
      return { role, tool: p.toolName || undefined, at: ev.created_at }
    }
    if (ev.type === "message" && p.role === "assistant") {
      return { role: "assistant", at: ev.created_at }
    }
  }
  return {}
}

function getHeaders(custom: Record<string, string> = {}): Record<string, string> {
  const token = getWorkbenchToken()
  const base: Record<string, string> = {
    "X-Request-ID": rid(),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }
  return {
    ...base,
    ...custom,
  }
}

async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const headers = getHeaders((options.headers as Record<string, string>) || {})
  const resp = await fetch(url, {
    ...options,
    headers,
  })
  if (!resp.ok) {
    const text = await resp.text().catch(() => "")
    let errDetail = text
    try {
      const j = JSON.parse(text)
      errDetail = j.error || j.message || text
    } catch {
      // fallback
    }
    throw new Error(errDetail || `HTTP ${resp.status} ${resp.statusText}`)
  }
  return resp.json()
}

export const api = {
  // Sessions
  async listSessions(): Promise<SessionItem[]> {
    const res = await request<any>("/api/v1/sessions")
    if (res && res.data && Array.isArray(res.data.items)) {
      return res.data.items
    }
    return Array.isArray(res) ? res : []
  },

  async createSession(params?: { title?: string; playbook?: string; workspace?: string }): Promise<{ session_id: string; title: string; playbook: string; workspace?: string }> {
    return request("/api/v1/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params || {}),
    })
  },

  async getSessionEvents(sessionId: string, tail = 300): Promise<SessionEvent[]> {
    const res = await request<any>(`/api/v1/sessions/${sessionId}/events?tail=${tail}`)
    return res.events || []
  },

  async renameSession(sessionId: string, title: string): Promise<any> {
    return request(`/api/v1/sessions/${sessionId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    })
  },

  async updateSession(
    sessionId: string,
    data: { title?: string; archived?: boolean; playbook?: string; mode?: string }
  ): Promise<any> {
    return request(`/api/v1/sessions/${sessionId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    })
  },

  async setSessionArchived(sessionId: string, archived: boolean): Promise<any> {
    return request(`/api/v1/sessions/${sessionId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ archived }),
    })
  },

  async deleteSession(sessionId: string): Promise<any> {
    return request(`/api/v1/sessions/${sessionId}`, {
      method: "DELETE",
    })
  },

  async forkSession(sessionId: string, title?: string, fromEventId?: string): Promise<{ session_id: string }> {
    return request(`/api/v1/sessions/${sessionId}/fork`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, ...(fromEventId ? { from_event_id: fromEventId } : {}) }),
    })
  },

  async rewindSession(
    sessionId: string,
    afterEventId: string,
    restoreFiles = true
  ): Promise<{ removed: number; restored_files?: number }> {
    return request(`/api/v1/sessions/${sessionId}/rewind`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ after_event_id: afterEventId, restore_files: restoreFiles }),
    })
  },

  /** 运行中插话纠偏(Devin 范式 mid-run steering):指令写会话流+审计留痕 */
  async steerRun(sessionId: string, instruction: string): Promise<{ pending: number }> {
    return request(`/api/v1/runs/${sessionId}/steer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instruction }),
    })
  },

  /** 导出自愈轨迹为 SFT/DPO 微调格式(仅成功交付案例) */
  async exportTraining(fmt: "sft" | "dpo"): Promise<{ format: string; count: number; items: any[] }> {
    return request(`/api/v1/training/export?fmt=${fmt}`)
  },

  // Runs
  async startRun(
    brief: string,
    playbook?: string,
    mode?: string,
    workspaceId?: string
  ): Promise<any> {
    return request("/api/v1/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        brief,
        playbook,
        ...(mode ? { mode } : {}),
        ...(workspaceId ? { workspace_id: workspaceId } : {}),
      }),
    })
  },

  async getActiveRun(): Promise<any> {
    return request("/api/v1/runs/active")
  },

  async stopRun(sessionId: string): Promise<any> {
    return request(`/api/v1/runs/${sessionId}/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    })
  },

  // Session export & search
  async exportSession(sessionId: string, fmt: "md" | "jsonl"): Promise<void> {
    const resp = await fetch(
      `/api/v1/sessions/${sessionId}/export?fmt=${fmt}`,
      { headers: getHeaders() }
    )
    if (!resp.ok) {
      const text = await resp.text().catch(() => "")
      throw new Error(text || `HTTP ${resp.status}`)
    }
    const blob = await resp.blob()
    const dispo = resp.headers.get("Content-Disposition") || ""
    const match = dispo.match(/filename="?([^";]+)"?/)
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = match?.[1] || `session-${sessionId.slice(0, 8)}.${fmt}`
    a.click()
    URL.revokeObjectURL(url)
  },

  async searchSessions(q: string, limit = 10): Promise<any[]> {
    const res = await request<any>(
      `/api/v1/sessions/search?q=${encodeURIComponent(q)}&limit=${limit}`
    )
    return res.items || []
  },

  // Workspaces
  async listWorkspaces(): Promise<{ current: string | null; items: WorkspaceItem[] }> {
    const res = await request<any>("/api/v1/workspaces")
    return { current: res.current ?? null, items: res.items || [] }
  },

  async createWorkspace(name: string, path: string): Promise<{ item: WorkspaceItem }> {
    return request("/api/v1/workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, path }),
    })
  },

  async setCurrentWorkspace(id: string | null): Promise<any> {
    return request("/api/v1/workspaces/current", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    })
  },

  async deleteWorkspace(id: string): Promise<any> {
    return request(`/api/v1/workspaces/${id}`, { method: "DELETE" })
  },

  async updateWorkspace(
    id: string,
    data: Partial<WorkspaceItem>
  ): Promise<{ item: WorkspaceItem }> {
    return request(`/api/v1/workspaces/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    })
  },

  // CAD export (HITL prompt-gated)
  async exportCad(host: "blender" | "vectorworks"): Promise<any> {
    return request(`/api/v1/demo/export-${host}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    })
  },

  // MCP settings
  async getMcpSettings(): Promise<any> {
    return request("/api/v1/settings/mcp")
  },

  async saveMcpSettings(config: Record<string, any>): Promise<any> {
    return request("/api/v1/settings/mcp", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config }),
    })
  },

  async resetMcpSettings(): Promise<any> {
    return request("/api/v1/settings/mcp", { method: "DELETE" })
  },

  // Plugins
  async listPlugins(): Promise<any> {
    return request("/api/v1/plugins")
  },

  async invokePlugin(capability: string, payload: Record<string, any> = {}, confirm = false): Promise<any> {
    return request("/api/v1/plugins/invoke", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ capability, payload, confirm }),
    })
  },

  // Approvals
  async listApprovals(): Promise<ApprovalItem[]> {
    const res = await request<any>("/api/v1/approvals")
    return res.approvals || (res.data && res.data.items) || []
  },

  async decideApproval(
    id: string,
    decision: "approved" | "rejected",
    instruction?: string
  ): Promise<any> {
    return request(`/api/v1/approvals/${id}/decide`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision,
        actor: "human:web-operator",
        ...(instruction ? { instruction } : {}),
      }),
    })
  },

  // Models & Providers
  async getModelsSettings(): Promise<ModelsSettingsData> {
    return request<ModelsSettingsData>("/api/v1/settings/models")
  },

  async saveModelsSettings(data: ModelsSettingsData): Promise<any> {
    return request("/api/v1/settings/models", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    })
  },

  async probeModel(providerId?: string, modelId?: string, extra?: { base_url?: string; api_key?: string }): Promise<{ latency_ms: number; status: string; models?: string[]; error?: string }> {
    const endpoint = providerId ? `/api/v1/settings/providers/${providerId}/probe` : `/api/v1/settings/providers/probe`
    return request(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_id: modelId, ...extra }),
    })
  },

  async fetchProviderModels(params: { providerId?: string; base_url?: string; api_key?: string }): Promise<{ status: string; models: Array<{ id: string; name: string; input_types?: string[] }>; latency_ms?: number; count?: number; error?: string }> {
    const endpoint = params.providerId ? `/api/v1/settings/providers/${params.providerId}/fetch_models` : `/api/v1/settings/providers/fetch_models`
    return request(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    })
  },

  async deleteProvider(providerId: string): Promise<any> {
    return request(`/api/v1/settings/providers/${providerId}`, {
      method: "DELETE",
    })
  },

  // Toolset
  async getToolset(): Promise<{ preset: "minimal" | "modeling" | "full" }> {
    const res = await request<any>("/api/v1/toolset")
    return { preset: res.current || res.preset || "modeling" }
  },

  async setToolset(preset: "minimal" | "modeling" | "full"): Promise<any> {
    return request("/api/v1/toolset", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: preset, preset }),
    })
  },

  // Supervisor Hosts
  async listHosts(): Promise<HostStatus[]> {
    const res = await request<any>("/api/v1/hosts")
    const hosts = res.hosts || []
    return hosts.map((h: any) => ({
      id: h.id,
      name: h.name || h.label || h.id,
      status: h.status || h.state || (h.connected ? "up" : "down"),
      last_seen: h.last_seen || (h.last_probe_at ? new Date(h.last_probe_at * 1000).toLocaleTimeString() : undefined),
      error: h.error || h.detail,
    }))
  },

  async restartHost(hostId: string): Promise<any> {
    return request(`/api/v1/hosts/${hostId}/restart`, {
      method: "POST",
    })
  },

  // Memory
  async getMemory(): Promise<{ items: MemoryEntry[] }> {
    const res = await request<any>("/api/v1/memory")
    const items: MemoryEntry[] = []
    if (res) {
      if (Array.isArray(res.user)) {
        res.user.forEach((it: any) => {
          if (typeof it === "string") {
            items.push({ line: 0, text: it, source: "user" })
          } else if (it && typeof it.text === "string") {
            items.push({ line: it.line || 0, text: it.text, source: "user" })
          }
        })
      }
      if (Array.isArray(res.memory)) {
        res.memory.forEach((it: any) => {
          if (typeof it === "string") {
            items.push({ line: 0, text: it, source: "memory" })
          } else if (it && typeof it.text === "string") {
            items.push({ line: it.line || 0, text: it.text, source: "memory" })
          }
        })
      }
    }
    return { items }
  },

  async recordMemory(text: string, confirm = true, file: "user" | "memory" = "user"): Promise<any> {
    return request("/api/v1/memory/record", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entry: text, text, file, confirm }),
    })
  },

  async deleteMemory(file: "user" | "memory", line: number, confirm = true): Promise<any> {
    return request("/api/v1/memory/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file, line, confirm }),
    })
  },

  // Skills
  async listSkills(): Promise<{ skills: any[]; candidates: any[] }> {
    return request("/api/v1/skills")
  },

  async approveSkillCandidate(candidateId: string): Promise<any> {
    const raw = typeof candidateId === "object" ? ((candidateId as any).file || (candidateId as any).id || "") : String(candidateId || "")
    const file = raw.endsWith(".md") ? raw : `${raw}.md`
    return request("/api/v1/skills/candidates/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file, id: raw }),
    })
  },

  async discardSkillCandidate(candidateId: string): Promise<any> {
    const raw = typeof candidateId === "object" ? ((candidateId as any).file || (candidateId as any).id || "") : String(candidateId || "")
    const file = raw.endsWith(".md") ? raw : `${raw}.md`
    return request("/api/v1/skills/candidates/discard", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file, id: raw }),
    })
  },

  // Archive (Deliverables)
  async listArchive(): Promise<{ items: ArchiveRecord[] }> {
    return request("/api/v1/archive")
  },

  // Usage
  async getUsage(): Promise<{ usage: UsageSummary }> {
    return request("/api/v1/usage")
  },

  // Rules
  async getRuleTree(): Promise<{ rules: RuleItem[] }> {
    return request("/api/v1/demo/rule-tree")
  },

  // Demo Pipeline Solver
  async invokeMunicipalPipeline(): Promise<any> {
    return request("/api/v1/demo/municipal-pipeline")
  },

  // Uploads
  async listUploads(): Promise<{ uploads: UploadItem[] }> {
    const res = await request<any>("/api/v1/uploads")
    const rawList = res.items || res.uploads || []
    return {
      uploads: rawList.map((it: any) => ({
        id: it.id,
        name: it.name || it.id,
        size: it.size || 0,
        sha256: it.sha256 || "",
        uploaded_at: it.uploaded_at || "",
      })),
    }
  },

  async uploadFile(file: File): Promise<any> {
    const buffer = await file.arrayBuffer()
    const headers = getHeaders({
      "Content-Type": "application/octet-stream",
      "X-Filename": encodeURIComponent(file.name),
    })
    const res = await fetch(`/api/v1/uploads?name=${encodeURIComponent(file.name)}`, {
      method: "POST",
      headers,
      body: buffer,
    })
    return res.json()
  },

  async deleteUpload(itemId: string): Promise<any> {
    return request(`/api/v1/uploads/${encodeURIComponent(itemId)}`, {
      method: "DELETE",
    })
  },

  // Chat & Stream
  async sendMessage(message: string, sessionId?: string): Promise<{ reply: string; reasoning?: string }> {
    return request("/api/v1/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId }),
    })
  },

  /**
   * 流式对话：POST /api/v1/chat/stream（SSE 序列 meta → reasoning*|delta* → usage? → done|error）。
   * 用 fetch + ReadableStream 按 \n\n 分帧解析，回调增量文本；signal 支持中断（停止按钮）。
   * 非流式 sendMessage 保留为降级回退。后端帧格式见 server/chat.py::_sse。
   */
  async streamChat(
    message: string,
    sessionId: string | undefined,
    handlers: {
      onMeta?: (meta: { model?: string; session_id?: string | null }) => void
      onDelta?: (text: string) => void
      onReasoning?: (text: string) => void
      onUsage?: (usage: {
        prompt_tokens?: number
        completion_tokens?: number
        total_tokens?: number
        latency_ms?: number
      }) => void
      onDone?: (info: { session_id?: string | null; persisted?: boolean }) => void
      onError?: (message: string) => void
    },
    signal?: AbortSignal,
    effort?: string,
    mode?: string,
    refs?: Array<{ kind: string; name: string }>
  ): Promise<void> {
    const resp = await fetch("/api/v1/chat/stream", {
      method: "POST",
      headers: getHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        message,
        session_id: sessionId,
        ...(effort ? { effort } : {}),
        ...(mode ? { mode } : {}),
        ...(refs && refs.length ? { refs } : {}),
      }),
      signal,
    })
    if (!resp.ok || !resp.body) {
      // 4xx/5xx（如 422 未配置基线 LLM）返回 JSONResponse 而非 SSE，解析 error 文案
      const text = await resp.text().catch(() => "")
      let msg = text
      try {
        const j = JSON.parse(text)
        msg = j.error || text
      } catch {
        // 保留原始文本
      }
      throw new Error(msg || `HTTP ${resp.status} ${resp.statusText}`)
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const frames = buffer.split("\n\n")
      buffer = frames.pop() ?? "" // 末尾不完整帧留待下次拼接
      for (const frame of frames) {
        if (!frame.trim()) continue
        let event = "message"
        let dataStr = ""
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim()
          else if (line.startsWith("data:")) dataStr += line.slice(5).trim()
        }
        let data: any = {}
        try {
          data = dataStr ? JSON.parse(dataStr) : {}
        } catch {
          // 忽略非 JSON 心跳帧
        }
        switch (event) {
          case "meta":
            handlers.onMeta?.(data)
            break
          case "delta":
            handlers.onDelta?.(data.text ?? "")
            break
          case "reasoning":
            handlers.onReasoning?.(data.text ?? "")
            break
          case "usage":
            handlers.onUsage?.(data)
            break
          case "done":
            handlers.onDone?.(data)
            break
          case "error":
            handlers.onError?.(data.error ?? "流式对话出错")
            break
        }
      }
    }
  },

  async getSessionArtifact(sessionId: string, name = "compiled_utility_ir.json"): Promise<any> {
    return request(`/api/v1/runs/artifact?session=${encodeURIComponent(sessionId)}&name=${encodeURIComponent(name)}`)
  },

  // Runtime Info
  async getRuntimeInfo(): Promise<any> {
    return request("/api/v1/demo/runtime-info")
  },

  // 上下文构成（含服务端自动压缩预算 context_budget_ratio）
  async getContextInfo(): Promise<{ compaction?: { context_budget_ratio?: number } }> {
    return request("/api/v1/context")
  },

  // Audit 审计日志(安全操作留痕,倒序)
  async getAudit(tail = 100): Promise<{ items: any[] }> {
    return request(`/api/v1/audit?tail=${tail}`)
  },
}
