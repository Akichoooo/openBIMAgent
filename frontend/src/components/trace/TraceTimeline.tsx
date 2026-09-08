import React, { useState, useEffect } from "react"
import { api, SessionEvent } from "@/services/api"
import { Badge } from "@/components/ui/badge"
import {
  MessageSquare,
  Wrench,
  Package,
  Gauge,
  Bot,
  CheckCircle2,
  XCircle,
  GitCommit,
  Camera,
  Image as ImageIcon,
  Award,
  FileCode,
  Activity,
  type LucideIcon,
} from "lucide-react"

interface TraceTimelineProps {
  sessionId: string | null
}

interface EventMeta {
  icon: LucideIcon
  color: string
  ring: string
  label: string
}

// 事件类型 → 图标/配色/标签（证据链叙事：指令→工具→工件→评分→子代理→交付）
function eventMeta(ev: SessionEvent): EventMeta {
  const p = ev.payload || {}
  const ct = p.customType
  if (ev.type === "message") {
    return p.role === "user"
      ? { icon: MessageSquare, color: "text-sky-500", ring: "border-sky-500/40 bg-sky-500/10", label: "用户指令" }
      : { icon: Bot, color: "text-primary", ring: "border-primary/40 bg-primary/10", label: "智能体回复" }
  }
  if (ev.type === "tool_call") {
    return { icon: Wrench, color: "text-violet-500", ring: "border-violet-500/40 bg-violet-500/10", label: p.toolName || "工具调用" }
  }
  switch (ct) {
    case "artifact_committed":
      return { icon: Package, color: "text-emerald-500", ring: "border-emerald-500/40 bg-emerald-500/10", label: "工件提交" }
    case "score":
      return { icon: Gauge, color: "text-amber-500", ring: "border-amber-500/40 bg-amber-500/10", label: "六维评分" }
    case "patch":
      return { icon: GitCommit, color: "text-orange-500", ring: "border-orange-500/40 bg-orange-500/10", label: "代码补丁" }
    case "snapshot":
      return { icon: Camera, color: "text-slate-400", ring: "border-slate-400/40 bg-slate-400/10", label: "操作前快照" }
    case "screenshot":
      return { icon: ImageIcon, color: "text-slate-400", ring: "border-slate-400/40 bg-slate-400/10", label: "渲染截图" }
    case "delivery_receipt":
      return { icon: Award, color: "text-emerald-500", ring: "border-emerald-500/40 bg-emerald-500/10", label: "交付回执" }
    case "subagent_created":
    case "subagent_started":
      return { icon: Bot, color: "text-blue-500", ring: "border-blue-500/40 bg-blue-500/10", label: "子代理派发" }
    case "subagent_completed":
      return { icon: CheckCircle2, color: "text-emerald-500", ring: "border-emerald-500/40 bg-emerald-500/10", label: "子代理完成" }
    case "subagent_failed":
    case "subagent_cancelled":
      return { icon: XCircle, color: "text-rose-500", ring: "border-rose-500/40 bg-rose-500/10", label: "子代理失败" }
    default:
      return { icon: Activity, color: "text-muted-foreground", ring: "border-border bg-muted/30", label: ct || "事件" }
  }
}

// 事件详情（防御式访问 payload；工件显示 sha256 短链，评分显示六维）
function eventDetail(ev: SessionEvent): React.ReactNode {
  const p = ev.payload || {}
  if (ev.type === "message") {
    return <span className="line-clamp-2 whitespace-pre-wrap">{p.content}</span>
  }
  if (ev.type === "tool_call") {
    const preview = p.result_ui_view || p.result_llm_view || p.args_summary
    return preview ? (
      <span className="font-mono text-[10px] line-clamp-2">{preview}</span>
    ) : null
  }
  if (p.customType === "artifact_committed" && p.artifact) {
    const a = p.artifact
    return (
      <div className="flex flex-wrap items-center gap-1.5 font-mono text-[10px]">
        <span className="text-foreground">{a.kind || "artifact"}</span>
        {a.sha256 && (
          <Badge variant="outline" className="text-[9px] font-mono px-1 py-0">
            sha256:{String(a.sha256).slice(0, 12)}
          </Badge>
        )}
        {a.status && <span className="text-muted-foreground/70">{a.status}</span>}
      </div>
    )
  }
  if (p.customType === "score") {
    const scores = p.rubric_scores || p.scores
    return (
      <div className="flex flex-wrap gap-1.5 text-[10px] font-mono">
        {(p.overall_score != null || p.overall != null) && (
          <Badge className="bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30 text-[9px] px-1 py-0">
            总分 {p.overall_score ?? p.overall}
          </Badge>
        )}
        {scores && typeof scores === "object"
          ? Object.entries(scores).map(([k, v]) => (
              <span key={k} className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                {k}:{String(v)}
              </span>
            ))
          : null}
      </div>
    )
  }
  if (typeof p.customType === "string" && p.customType.startsWith("subagent")) {
    return (
      <span className="font-mono text-[10px] text-muted-foreground">
        {p.role ? `role=${p.role}` : ""} {p.agent_id ? `agent=${String(p.agent_id).slice(0, 8)}` : ""}
      </span>
    )
  }
  return null
}

function EmptyHint({ text, sub }: { text: string; sub?: string }) {
  return (
    <div className="py-16 text-center">
      <Activity className="h-8 w-8 mx-auto mb-3 text-muted-foreground/30" />
      <p className="text-xs text-muted-foreground">{text}</p>
      {sub && <p className="text-[11px] text-muted-foreground/60 mt-1">{sub}</p>}
    </div>
  )
}

/**
 * 证据链 / 执行轨迹时间线（方案 C）。
 *
 * 把 session JSONL 事件树渲染为可审计的垂直时间线：用户指令 → 工具调用 → 工件提交
 * （sha256 短链）→ 六维评分 → 子代理生命周期 → 交付回执。既是 UI 补强，又是 A1
 * trajectory_accuracy / evidence_chain 的直观呈现（答辩可展示"每一步都有据可查"）。
 */
export const TraceTimeline: React.FC<TraceTimelineProps> = ({ sessionId }) => {
  const [events, setEvents] = useState<SessionEvent[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!sessionId) {
      setEvents([])
      return
    }
    setLoading(true)
    api
      .getSessionEvents(sessionId, 500)
      .then((evs) => setEvents(evs))
      .catch(() => setEvents([]))
      .finally(() => setLoading(false))
  }, [sessionId])

  return (
    <div className="w-full h-full overflow-y-auto bg-background">
      <div className="max-w-3xl mx-auto p-4">
        {/* 标题条 */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <FileCode className="h-4 w-4 text-primary" />
            <span className="text-sm font-semibold text-foreground">证据链 / 执行轨迹</span>
            <span className="text-[10px] text-muted-foreground/70 font-mono">
              session JSONL 事件树 · 全程可审计
            </span>
          </div>
          <Badge variant="secondary" className="text-[10px] font-mono shrink-0">
            {events.length} 事件
          </Badge>
        </div>

        {!sessionId ? (
          <EmptyHint text="未选择会话" />
        ) : loading && events.length === 0 ? (
          <EmptyHint text="正在加载轨迹…" />
        ) : events.length === 0 ? (
          <EmptyHint text="该会话暂无事件轨迹" sub="发起工程任务后，此处展示可审计的执行证据链" />
        ) : (
          <div className="relative">
            {/* 垂直连线 */}
            <div className="absolute left-4 top-2 bottom-2 w-px bg-border/60" />
            <div className="space-y-2.5">
              {events.map((ev, idx) => {
                const meta = eventMeta(ev)
                const Icon = meta.icon
                const detail = eventDetail(ev)
                return (
                  <div key={ev.id || idx} className="relative flex items-start gap-3">
                    <div
                      className={`relative z-10 w-8 h-8 shrink-0 rounded-full border flex items-center justify-center ${meta.ring}`}
                    >
                      <Icon className={`h-4 w-4 ${meta.color}`} />
                    </div>
                    <div className="flex-1 min-w-0 rounded-lg border border-border/60 bg-card/40 px-3 py-2">
                      <div className="flex items-center justify-between gap-2 mb-0.5">
                        <span className={`text-[11px] font-medium truncate ${meta.color}`}>{meta.label}</span>
                        <span className="text-[9px] font-mono text-muted-foreground/60 shrink-0">
                          {ev.created_at ? new Date(ev.created_at).toLocaleTimeString() : ""}
                        </span>
                      </div>
                      {detail && (
                        <div className="text-[11px] text-muted-foreground leading-relaxed">{detail}</div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
