import React, { useState, useEffect, useRef } from "react"
import { api, SessionEvent, ApprovalItem, ModelsSettingsData, buildSessionEventsStreamUrl, deriveRunActivity, deriveThreadView, deriveRunWrapUp, isApprovalSignal } from "@/services/api"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Send,
  Wrench,
  ChevronDown,
  ChevronUp,
  ChevronRight,
  Paperclip,
  CheckCircle2,
  XCircle,
  FileCode,
  Sparkles,
  Bot,
  ShieldCheck,
  StopCircle,
  Square,
  Download,
  Mic,
  MicOff,
  Slash,
  History,
  GitBranch,
  X,
  Check,
  MessageSquare,
  ArrowLeft,
  MoreHorizontal,
  FileText,
  Maximize2,
  Minimize2,
  Copy,
  RotateCcw,
  RefreshCw,
  Compass,
  Hammer,
  PackageCheck,
  Folder,
  Laptop,
  Code2,
  Loader2,
  Ban,
} from "lucide-react"
import { toast } from "sonner"
import { ModelPicker, type ReasoningLevel } from "./ModelPicker"
import { PlanSteps, parsePlanSteps } from "./PlanSteps"
import { DiffView } from "./DiffView"
import { ChatHeroWelcome } from "./ChatHeroWelcome"

// 工程领域 Playbook 选项
const PLAYBOOK_OPTIONS = [
  { id: "municipal_utility", label: "市政给排水管网", desc: "重力流管网放样、标高碰撞自愈与管件布尔求交" },
  { id: "single_asset_hero", label: "单体资产建模", desc: "高精度单体建筑、桥梁、塔架参数化几何生成" },
  { id: "edo_cyberpunk_district", label: "Edo 街区规划", desc: "多地块建筑群拓扑生成与空间路网协同" },
  { id: "general", label: "常规工程任务", desc: "通用参数化 CAD 脚本编写、图纸校验与审图答疑" },
]

// 斜杠命令:通用(agent 标配)+ 建模专用(匹配 BIM 需求)
const SLASH_COMMANDS = [
  { cmd: "/compact", desc: "上下文压缩说明(服务端自动执行)" },
  { cmd: "/clear", desc: "清空当前输入与流式态" },
  { cmd: "/model", desc: "切换模型(下方芯片)" },
  { cmd: "/effort", desc: "切换思考档位(off~max)" },
  { cmd: "/render", desc: "触发 Blender 渲染环(美学精检)" },
  { cmd: "/scad", desc: "触发 OpenSCAD 结构快检环" },
  { cmd: "/vision", desc: "六维视觉评分(critic_render)" },
  { cmd: "/solve", desc: "市政管网水力/路由求解" },
  { cmd: "/ir", desc: "查看 CompiledUtilityIR 中间表示" },
  { cmd: "/evidence", desc: "查看证据链轨迹" },
  { cmd: "/reflect", desc: "反思本会话教训→写记忆(下次检索注入)" },
  { cmd: "/help", desc: "显示命令帮助" },
]

const fmtK = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n))

const formatMessageTime = (ts?: string | number) => {
  if (!ts) return ""
  try {
    const d = new Date(ts)
    if (isNaN(d.getTime())) return ""
    return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })
  } catch {
    return ""
  }
}

const SUBAGENT_ROLES: Record<string, { title: string; desc: string; badgeColor: string; icon: React.FC<{ className?: string }> }> = {
  orchestrator: { title: "总控架构师", desc: "宏观方案编排与任务调度", badgeColor: "border-violet-500/30 text-violet-500 bg-violet-500/10", icon: Bot },
  planner: { title: "管网规划员", desc: "方案推演与标高规划求解", badgeColor: "border-sky-500/30 text-sky-500 bg-sky-500/10", icon: Compass },
  modeler: { title: "几何建模员", desc: "三维放样与管网构件生成", badgeColor: "border-amber-500/30 text-amber-500 bg-amber-500/10", icon: Hammer },
  critic_scad: { title: "结构质检员", desc: "网格流形核验与碰撞规避", badgeColor: "border-emerald-500/30 text-emerald-500 bg-emerald-500/10", icon: ShieldCheck },
  critic_render: { title: "渲染质检员", desc: "六维视觉评分与美学评估", badgeColor: "border-rose-500/30 text-rose-500 bg-rose-500/10", icon: Sparkles },
  researcher: { title: "国标检索员", desc: "GB/行业规范条文实时比对", badgeColor: "border-indigo-500/30 text-indigo-500 bg-indigo-500/10", icon: FileCode },
  deliver: { title: "出图交付员", desc: "CAD/IFC 工程真机导出协同", badgeColor: "border-teal-500/30 text-teal-500 bg-teal-500/10", icon: PackageCheck },
}

interface ChatThreadProps {
  sessionId: string | null
  sessionTitle?: string
  playbook?: string
  irData?: any
  onOpenSettings?: (tab?: string, modelId?: string) => void
  onUsageChange?: (used: number, ctx: number) => void
  onEventsChange?: (events: SessionEvent[]) => void
  onStatusChange?: (status: { running: boolean; currentModel: string; pendingApprovals: number }) => void
  onSessionCreated?: (sessionId: string) => void
  isExpanded?: boolean
  onToggleExpand?: () => void
  width?: number
  style?: React.CSSProperties
}

export const ChatThread: React.FC<ChatThreadProps> = ({
  sessionId,
  sessionTitle = "未选择会话",
  playbook,
  irData,
  onOpenSettings,
  onUsageChange,
  onEventsChange,
  onStatusChange,
  onSessionCreated,
  isExpanded = false,
  onToggleExpand,
  width,
  style,
}) => {
  const [events, setEvents] = useState<SessionEvent[]>([])
  const [loading, setLoading] = useState(false)
  const [inputText, setInputText] = useState("")
  const [sending, setSending] = useState(false)
  const [approvals, setApprovals] = useState<ApprovalItem[]>([])
  const [decidingId, setDecidingId] = useState<string | null>(null)
  const [showIR, setShowIR] = useState(false)
  const [runActive, setRunActive] = useState(false)
  const [stopping, setStopping] = useState(false)

  // 算子权限等级 (与 ChatHeroWelcome / ToolsetTab 联动)
  const [toolsetPreset, setToolsetPreset] = useState<"minimal" | "modeling" | "full">("modeling")
  useEffect(() => {
    const refreshToolset = () => {
      api.getToolset().then((res) => {
        if (res && res.preset) setToolsetPreset(res.preset)
      }).catch(() => {})
    }
    refreshToolset()
    window.addEventListener("wb-toolset-change", refreshToolset)
    return () => window.removeEventListener("wb-toolset-change", refreshToolset)
  }, [])
  const handleToolsetPresetChange = async (preset: "minimal" | "modeling" | "full") => {
    setToolsetPreset(preset)
    try {
      await api.setToolset(preset)
      window.dispatchEvent(new CustomEvent("wb-toolset-change"))
      toast.success(`求解模式已设为: ${preset === "minimal" ? "Ask 方案答疑" : preset === "full" ? "Build 直接建模" : "Plan 规划推演"}`)
    } catch (e: any) {
      toast.error("设置失败: " + e.message)
    }
  }

  // Current model selection
  const [modelsData, setModelsData] = useState<ModelsSettingsData | null>(null)
  const [currentModel, setCurrentModel] = useState<string>("")
  // 思考模式(统一 5 档)+ 上下文窗口 + 已用 token(用量条)+ 语音 + 斜杠命令
  const [effort, setEffort] = useState<ReasoningLevel>("medium")
  const [contextWindow, setContextWindow] = useState<number>(128000)
  const [usedTokens, setUsedTokens] = useState<number>(0)
  // 实时估算/解析会话上下文已用 Token 数量
  useEffect(() => {
    let count = 0
    events.forEach((ev) => {
      const p = ev.payload || {}
      if (p.usage?.total_tokens) {
        count = Math.max(count, p.usage.total_tokens)
      } else if (p.content && typeof p.content === "string") {
        count += Math.ceil(p.content.length * 0.8)
      }
    })
    setUsedTokens(count)
  }, [events])

  // Context 预算比率与百分比 (驱动发送键外围环形进度条)
  const ctxRatio = Math.min(1, Math.max(0, usedTokens / Math.max(contextWindow, 1)))
  const ctxPct = Math.round(ctxRatio * 100)
  // 服务端自动压缩预算比 (/api/v1/context → compaction.context_budget_ratio,缺省 0.8)
  const [budgetRatio, setBudgetRatio] = useState(0.8)
  useEffect(() => {
    api
      .getContextInfo()
      .then((res) => {
        const r = res?.compaction?.context_budget_ratio
        if (typeof r === "number" && r > 0 && r <= 1) setBudgetRatio(r)
      })
      .catch(() => {})
  }, [])
  const budgetPct = Math.round(budgetRatio * 100)

  // Context 预算上报 App → Header 预算条(pi-mono 范式)
  useEffect(() => {
    onUsageChange?.(usedTokens, contextWindow)
  }, [usedTokens, contextWindow, onUsageChange])

  // 运行事件上报 App → 中央底部终端 (VS Code 范式)
  useEffect(() => {
    onEventsChange?.(events)
  }, [events, onEventsChange])

  // 运行状态与待审批数上报 App → 全局状态栏 StatusBar (VS Code 范式)
  useEffect(() => {
    onStatusChange?.({
      running: runActive || sending,
      currentModel,
      pendingApprovals: approvals.length,
    })
  }, [runActive, sending, currentModel, approvals.length, onStatusChange])
  const [voiceOn, setVoiceOn] = useState(false)
  const [showCommands, setShowCommands] = useState(false)
  // 长 assistant 消息折叠(默认收起显示总结)+ 展开态
  const [expandedMsgs, setExpandedMsgs] = useState<Record<string, boolean>>({})
  // 运行中继续发送 → 排队(当前任务完成后依次自动发送)
  const [queuedMsgs, setQueuedMsgs] = useState<string[]>([])
  const [showQueuedList, setShowQueuedList] = useState(false)
  // 确认后的执行计划 spec(可编辑+Given-When-Then 验收)
  const [planSpec, setPlanSpec] = useState<any[] | null>(null)
  // 权限模式:plan只读(拦截写工具)/agent审批(默认HITL)/yolo自主(跳过审批)
  const [mode, setMode] = useState<"plan" | "agent" | "yolo">(() => {
    const saved = localStorage.getItem("wb_mode")
    return saved === "plan" || saved === "yolo" ? saved : "agent"
  })
  useEffect(() => localStorage.setItem("wb_mode", mode), [mode])
  // @mention 引用(@ir/@rule/@file/@memory)+ 选中引用列表
  const [mentionOpen, setMentionOpen] = useState(false)
  const [mentionQuery, setMentionQuery] = useState("")
  const [refs, setRefs] = useState<Array<{ kind: string; name: string }>>([])

  // Collapsed tool calls
  const [collapsedTools, setCollapsedTools] = useState<Record<string, boolean>>({})

  // 流式对话：正在生成的 assistant 消息（打字机效果）+ 中断控制器
  const [streamingMsg, setStreamingMsg] = useState<{ content: string; reasoning: string } | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const scrollRef = useRef<HTMLDivElement | null>(null)
  const currentPlaybookObj = PLAYBOOK_OPTIONS.find((p) => p.id === playbook) || PLAYBOOK_OPTIONS[0]
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  // Load events
  const loadEvents = async () => {
    if (!sessionId) {
      setEvents([])
      return
    }
    try {
      const evs = await api.getSessionEvents(sessionId)
      setEvents((prev) => {
        if (prev.length === evs.length) {
          const lastPrev = prev[prev.length - 1]
          const lastEvs = evs[evs.length - 1]
          if (lastPrev?.id === lastEvs?.id && JSON.stringify(lastPrev?.payload) === JSON.stringify(lastEvs?.payload)) {
            return prev
          }
        }
        return evs
      })
    } catch (e) {
      console.error(e)
    }
  }

  // Load approvals
  const loadApprovals = async () => {
    try {
      const res = await api.listApprovals().catch(() => [])
      setApprovals(res.filter((a) => a.status === "pending"))
    } catch (e) {
      console.error(e)
    }
  }

  // Load models config
  const loadModels = async () => {
    try {
      const res = await api.getModelsSettings().catch(() => null)
      if (res) {
        setModelsData(res)
        const hasModels = (res.providers || []).some(
          (p) => (p.models || []).length > 0 && p.enabled !== false
        )
        if (hasModels && res.current) {
          setCurrentModel(res.current)
        } else {
          setCurrentModel("")
        }
      }
    } catch (e) {
      console.error(e)
    }
  }

  // 运行状态查询：驱动「停止运行」按钮显隐（SSE 终态/事件到达时也会即时调用，避免 10s 轮询滞后）
  const checkRunActive = async () => {
    if (!sessionId) {
      setRunActive(false)
      return
    }
    try {
      const res = await api.getActiveRun()
      const runs: any[] = res?.runs || (res?.run ? [res.run] : [])
      setRunActive(runs.some((r) => r.active && r.session_id === sessionId))
    } catch {
      setRunActive(false)
    }
  }

  // 事件加载：优先 SSE 实时跟随（/events/stream，EventSource 无法设请求头 → ?token= 鉴权），
  // 断开回退 3s 轮询；兜底期间每 ~30s 尝试恢复 SSE，onopen 成功后停轮询（断流自愈）
  useEffect(() => {
    loadEvents()
    loadApprovals()
    loadModels()

    if (!sessionId) {
      const timer = setInterval(loadApprovals, 3000)
      return () => clearInterval(timer)
    }

    let disposed = false
    let fallbackTimer: ReturnType<typeof setInterval> | null = null
    let retryTimer: ReturnType<typeof setInterval> | null = null
    let es: EventSource | null = null

    const stopFallback = () => {
      if (fallbackTimer) clearInterval(fallbackTimer)
      fallbackTimer = null
    }
    const stopRetry = () => {
      if (retryTimer) clearInterval(retryTimer)
      retryTimer = null
    }
    const startFallback = () => {
      if (!fallbackTimer) {
        fallbackTimer = setInterval(() => {
          loadEvents()
          loadApprovals()
        }, 3000)
      }
      if (!retryTimer) {
        retryTimer = setInterval(connect, 30000)
      }
    }

    function connect() {
      if (disposed) return
      try {
        es = new EventSource(buildSessionEventsStreamUrl(sessionId!))
        es.onopen = () => {
          // SSE 恢复成功：停轮询与重试
          stopFallback()
          stopRetry()
        }
        es.onmessage = (msg) => {
          try {
            const ev = JSON.parse(msg.data) as SessionEvent
            setEvents((prev) =>
              prev.some((p) => p.id && p.id === ev.id) ? prev : [...prev, ev]
            )
            // 审批请求/裁决信号：立即刷新 HITL 决策门卡片,不等 3s 轮询
            if (isApprovalSignal(ev)) loadApprovals()
          } catch {}
        }
        es.onerror = () => {
          // 运行结束后服务端主动关流（或鉴权失败/网络断开）：立即排空尾部事件并刷新运行态，
          // 回退 3s 轮询兜底，避免尾事件延迟与「running」状态滞留
          es?.close()
          es = null
          if (disposed) return
          loadEvents()
          checkRunActive()
          startFallback()
        }
      } catch {
        startFallback()
      }
    }
    connect()

    const approvalTimer = setInterval(loadApprovals, 3000)
    return () => {
      disposed = true
      es?.close()
      stopFallback()
      stopRetry()
      clearInterval(approvalTimer)
    }
  }, [sessionId])

  // 运行状态轮询：初次 + 10s 周期 + 新事件到达即时复查
  useEffect(() => {
    if (!sessionId) {
      setRunActive(false)
      return
    }
    checkRunActive()
    const timer = setInterval(checkRunActive, 10000)
    return () => clearInterval(timer)
  }, [sessionId, events.length])

  const handleStopRun = async () => {
    if (!sessionId || stopping) return
    setStopping(true)
    try {
      await api.stopRun(sessionId)
      toast.success("已发送停止指令：运行将在下一个审批门处安全中止")
      stoppedRef.current = true
      setRunActive(false)
      await loadEvents()
    } catch (e: any) {
      toast.error("停止失败: " + e.message)
    } finally {
      setStopping(false)
    }
  }

  // 实时活动状态行（Claude Code / Devin 风格）：当前子代理角色 · 当前工具 · 已运行时长
  const busy = sending || runActive
  const activity = React.useMemo(() => deriveRunActivity(events), [events])
  const threadItems = React.useMemo(() => deriveThreadView(events), [events])
  const [activityNow, setActivityNow] = useState(() => Date.now())
  const runStartedRef = useRef<number | null>(null)
  useEffect(() => {
    if (!busy) return
    if (runStartedRef.current == null) runStartedRef.current = Date.now()
    setActivityNow(Date.now())
    const t = setInterval(() => setActivityNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [busy])
  const fmtElapsed = (sec: number) =>
    sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`
  const elapsedSec =
    busy && runStartedRef.current != null
      ? Math.max(0, Math.floor((activityNow - runStartedRef.current) / 1000))
      : 0
  const activityRoleTitle = activity.role
    ? SUBAGENT_ROLES[activity.role]?.title || (activity.role === "assistant" ? "智能体" : activity.role)
    : ""
  const activityText = busy
    ? sending
      ? `模型流式推理中... · ${fmtElapsed(elapsedSec)}`
      : [activityRoleTitle || activity.tool ? "" : "工程求解器运算中...", activityRoleTitle, activity.tool, fmtElapsed(elapsedSec)]
          .filter(Boolean)
          .join(" · ")
    : ""

  // 运行收尾行：run active→idle 时从已有事件推导工具调用次数与用时；被中断的运行显示「已中断」
  const [wrapUp, setWrapUp] = useState<{ interrupted: boolean; toolCalls: number; seconds: number } | null>(null)
  const stoppedRef = useRef(false)
  const prevRunActiveRef = useRef(false)
  useEffect(() => {
    if (runActive && !prevRunActiveRef.current) setWrapUp(null) // 新运行开始,清掉上一次收尾行
    if (!runActive && prevRunActiveRef.current) {
      const until = Date.now()
      const stats = deriveRunWrapUp(events, runStartedRef.current ?? until, until)
      setWrapUp({ interrupted: stoppedRef.current, ...stats })
      stoppedRef.current = false
    }
    prevRunActiveRef.current = runActive
  }, [runActive, events])

  // 会话切换：重置活动计时/收尾行/停止标记等瞬态,避免跨会话残留
  useEffect(() => {
    runStartedRef.current = null
    prevRunActiveRef.current = false
    stoppedRef.current = false
    setWrapUp(null)
    setStopping(false)
  }, [sessionId])

  // Esc 全局打断（对齐现有打断按钮 handler；输入框聚焦时不劫持按键）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || !busy) return
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.isContentEditable)) return
      e.preventDefault()
      handleStopAll()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy])

  // 记录用户是否处于视口底部（若用户向上翻阅查阅历史，轮询/新事件绝不强制滚到底部打扰用户）
  const isNearBottomRef = useRef(true)
  const handleScroll = () => {
    if (!scrollRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current
    isNearBottomRef.current = scrollHeight - scrollTop - clientHeight < 120
  }

  // 智能平滑贴底：仅在处于底部或流式生成时滚动
  useEffect(() => {
    if (scrollRef.current && (isNearBottomRef.current || streamingMsg)) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [events.length, approvals.length, streamingMsg])

  // 运行中插话纠偏(Devin 范式 mid-run steering):Ctrl/Cmd+Enter 注入指令,不打断当前任务
  const handleSteer = async () => {
    const text = inputText.trim()
    if (!text || !sessionId || !runActive) return
    try {
      const res = await api.steerRun(sessionId, text)
      setInputText("")
      toast.success(`纠偏指令已注入（待决 ${res.pending} 条）`, {
        description: "已写会话流+审计留痕；审批门与后续角色可消费",
      })
      await loadEvents()
    } catch (e: any) {
      toast.error("插话失败: " + e.message)
    }
  }

  const handleSendMessage = async (e?: React.FormEvent, overrideText?: string) => {
    e?.preventDefault()
    const text = (overrideText || inputText).trim()
    if (!text) return
    // 运行中(sending || runActive)→ 排队而非丢弃;当前任务完成后依次自动发送(引导式续接)
    if ((sending || runActive) && !overrideText) {
      setQueuedMsgs((q) => [...q, text])
      setInputText("")
      toast.info(`已加入排队：当前任务完成后自动执行 (第 ${queuedMsgs.length + 1} 条)`)
      return
    }
    setInputText("")
    setShowIR(false)
    setSending(true)

    // 乐观插入用户消息（即时反馈；done 后 loadEvents 用后端落盘的真实事件覆盖）
    const fakeEvent: SessionEvent = {
      id: "opt-" + Date.now(),
      type: "message",
      created_at: new Date().toISOString(),
      payload: { role: "user", content: text },
    }
    setEvents((prev) => [...prev, fakeEvent])

    // /solve 明确指令走 batch pipeline 批量求解运行（非普通对话）
    if (text.startsWith("/solve")) {
      try {
        await api.startRun(text, playbook || "municipal_utility", mode)
        await loadEvents()
      } catch (err: any) {
        console.error(err)
        toast.error("任务启动失败: " + err.message)
      } finally {
        setSending(false)
      }
      return
    }

    // 若当前无 session_id，先自动建会话并通知父级
    let effectiveSessionId = sessionId
    if (!effectiveSessionId) {
      try {
        const created = await api.createSession({
          title: text.slice(0, 30),
          playbook: playbook || "municipal_utility",
        })
        effectiveSessionId = created.session_id
        onSessionCreated?.(created.session_id)
      } catch (e: any) {
        console.error("自动建会话失败:", e)
      }
    }

    // 普通对话 → 流式打字机（对齐 Codex/ZCode：思考态 + 逐字正文 + 光标 + 可中断）
    setStreamingMsg({ content: "", reasoning: "" })
    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      await api.streamChat(
        text,
        effectiveSessionId || undefined,
        {
          onDelta: (t) =>
            setStreamingMsg((prev) => (prev ? { ...prev, content: prev.content + t } : prev)),
          onReasoning: (t) =>
            setStreamingMsg((prev) => (prev ? { ...prev, reasoning: prev.reasoning + t } : prev)),
          onUsage: (u) => setUsedTokens((prev) => prev + (u.total_tokens || u.completion_tokens || 0)),
          onError: (m) => toast.error(m),
        },
        ctrl.signal,
        effort,
        mode,
        refs
      )
      // done：后端已成对落盘 user+assistant，拉真实事件替换乐观/流式态
      if (effectiveSessionId) {
        const evs = await api.getSessionEvents(effectiveSessionId)
        setEvents(evs)
      } else {
        await loadEvents()
      }
    } catch (err: any) {
      if (err?.name !== "AbortError") {
        console.error(err)
        toast.error("消息发送失败: " + err.message)
      }
    } finally {
      setStreamingMsg(null)
      abortRef.current = null
      setSending(false)
    }
  }

  // 停止流式生成：中断上游 fetch；已生成内容由后端 worker finally 成对落盘，不丢
  const handleStopStream = () => {
    abortRef.current?.abort()
  }

  // 全局打断：流式对话中断 + 后台求解管线安全中止
  const handleStopAll = async () => {
    if (sending) {
      handleStopStream()
    }
    if (runActive && sessionId) {
      await handleStopRun()
    }
  }

  // 排队消息:当前发送结束(sending=false 且非 runActive)后依次自动发送
  useEffect(() => {
    if (sending || runActive || queuedMsgs.length === 0) return
    const [next, ...rest] = queuedMsgs
    setQueuedMsgs(rest)
    handleSendMessage(undefined, next)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sending, runActive, queuedMsgs])

  // 时间旅行:回滚到某事件(截断之后)/ 从此分叉新会话(模仿 Claude Code / Cursor)
  const handleRewind = async (eventId: string) => {
    if (!sessionId) return
    try {
      const res = await api.rewindSession(sessionId, eventId)
      toast.success(
        `已回滚：移除 ${res.removed} 条后续事件` +
          (res.restored_files ? `，恢复 ${res.restored_files} 个文件` : "")
      )
      await loadEvents()
    } catch (e: any) {
      toast.error("回滚失败: " + e.message)
    }
  }
  const handleFork = async (eventId: string) => {
    if (!sessionId) return
    try {
      const res = await api.forkSession(sessionId, undefined, eventId)
      // 权限重验(Claude 范式):fork 新会话不继承高危 mode,重置为审批
      setMode("agent")
      toast.success(`已分叉新会话: ${String(res.session_id || "").slice(0, 8)}（权限重置为审批）`)
    } catch (e: any) {
      toast.error("分叉失败: " + e.message)
    }
  }

  const handleRegenerateFromAssistant = async (assistantIdx: number) => {
    if (!sessionId) return
    let userMsg: any = null
    let userMsgIdx = -1
    for (let i = assistantIdx - 1; i >= 0; i--) {
      if (events[i]?.type === "message" && events[i]?.payload?.role === "user") {
        userMsg = events[i]
        userMsgIdx = i
        break
      }
    }
    if (!userMsg) {
      toast.error("未找到对应的提问消息")
      return
    }
    const userEventId = userMsg.id || String(userMsgIdx)
    const promptText = String(userMsg.payload?.content || "")
    try {
      toast.loading("正在重新生成...", { id: "regen" })
      await api.rewindSession(sessionId, userEventId)
      await loadEvents()
      toast.dismiss("regen")
      await handleSendMessage(undefined, promptText)
    } catch (e: any) {
      toast.error("重新生成失败: " + e.message, { id: "regen" })
    }
  }

  const handleApproval = async (id: string, decision: "approved" | "rejected") => {
    setDecidingId(id)
    try {
      await api.decideApproval(id, decision)
      toast.success(decision === "approved" ? "已批准执行" : "已拒绝执行")
      await loadApprovals()
      await loadEvents()
    } catch (e: any) {
      toast.error("审批决策提交失败: " + e.message)
    } finally {
      setDecidingId(null)
    }
  }

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    try {
      toast.loading(`正在上传 ${file.name}...`, { id: "chat-upload" })
      await api.uploadFile(file)
      toast.success(`文件 ${file.name} 上传成功`, { id: "chat-upload" })
      await loadEvents()
    } catch (err: any) {
      toast.error("上传失败: " + err.message, { id: "chat-upload" })
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }

  const toggleToolCollapse = (id: string) => {
    setCollapsedTools((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  const handleSelectModel = (modelId: string) => {
    setCurrentModel(modelId)
    let ctx = 128000
    if (modelsData?.providers) {
      for (const p of modelsData.providers) {
        const m = (p.models || []).find((x) => x.name === modelId || x.id === modelId)
        if (m) {
          ctx = m.context_window ?? m.context_length ?? 128000
          break
        }
      }
    }
    setContextWindow(ctx)
    if (modelsData) {
      api.saveModelsSettings({ ...modelsData, current: modelId }).catch(() => {})
    }
  }

  // @mention 异步候选(@rule/@file/@memory);@ir 从 irData 同步提取
  const [mentionExtra, setMentionExtra] = useState<Array<{ kind: string; name: string }>>([])
  useEffect(() => {
    Promise.all([
      api.getRuleTree().catch(() => ({ rules: [] })),
      api.listUploads().catch(() => ({ uploads: [] })),
      api.getMemory().catch(() => ({ items: [] })),
    ]).then(([rt, up, mem]) => {
      const ex: Array<{ kind: string; name: string }> = []
      ;((rt as any).rules || []).forEach((r: any) => ex.push({ kind: "rule", name: String(r.title || r.id || "") }))
      ;((up as any).uploads || []).forEach((u: any) => ex.push({ kind: "file", name: String(u.name || "") }))
      ;((mem as any).items || []).slice(0, 20).forEach((m: any) =>
        ex.push({ kind: "memory", name: String(m.text || "").slice(0, 40) })
      )
      setMentionExtra(ex.filter((x) => x.name))
    })
  }, [])
  const mentionItems = React.useMemo(() => {
    const items: Array<{ kind: string; name: string }> = []
    ;((irData?.nodes || []) as Array<{ node_id?: string }>).forEach((n) =>
      n.node_id && items.push({ kind: "ir", name: String(n.node_id) })
    )
    ;((irData?.segments || []) as Array<{ segment_id?: string }>).forEach((s) =>
      s.segment_id && items.push({ kind: "ir", name: String(s.segment_id) })
    )
    return [...items, ...mentionExtra]
  }, [irData, mentionExtra])

  const runCommand = (cmd: string) => {
    switch (cmd) {
      case "/clear":
        setInputText("")
        setStreamingMsg(null)
        toast.success("已清空输入")
        break
      case "/compact":
        // 后端无手动压缩端点：上下文压缩由服务端按预算自动执行（保留最近分组+总结历史），
        // 前端 token 计数仅为本地估算，不做伪造的乘法修改
        toast.info("上下文压缩由服务端自动执行", {
          description: `用量接近上下文预算时后端自动总结历史并释放窗口。当前估算用量 ${fmtK(usedTokens)} / ${fmtK(contextWindow)} tokens (${ctxPct}%)`,
        })
        break
      case "/model":
      case "/effort":
        toast.info(`请在输入框下方芯片切换${cmd === "/model" ? "模型" : "思考档位"}`)
        break
      case "/ir":
        setShowIR(true)
        break
      case "/reflect": {
        // Reflexion:总结本会话过程教训 → 写 episodic memory(跨任务检索复用)
        const lessons = events
          .slice(-8)
          .map((ev) => {
            const p = ev.payload || {}
            if (ev.type === "tool_call") return `工具 ${p.toolName || "solver"}: ${p.phase || "ok"}`
            if (ev.type === "message" && p.role === "assistant") return String(p.content || "").slice(0, 60)
            return ""
          })
          .filter(Boolean)
          .join("; ")
        const reflection = `[reflect ${new Date().toISOString().slice(0, 10)}] 会话=${sessionTitle} 模型=${currentModel} 思考=${effort} 过程: ${lessons.slice(0, 300)}`
        api
          .recordMemory(reflection)
          .then(() => toast.success("反思已写入记忆：下次同类任务将检索注入"))
          .catch((e) => toast.error("反思写入失败: " + e.message))
        break
      }
      case "/help":
        toast.info("可用命令: " + SLASH_COMMANDS.map((c) => c.cmd).join(" "))
        break
      default:
        api
          .startRun(cmd, playbook || "municipal_utility")
          .then(() => loadEvents())
          .catch((e) => toast.error("命令执行失败: " + e.message))
        break
    }
  }

  // ⌘P 命令面板派发的命令(window 事件)→ 本地 runCommand 执行
  useEffect(() => {
    const handler = (e: Event) => runCommand(String((e as CustomEvent).detail || ""))
    window.addEventListener("wb-command", handler)
    return () => window.removeEventListener("wb-command", handler)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const toggleVoice = () => {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
    if (!SR) {
      toast.error("当前浏览器不支持语音识别 (Web Speech API)")
      return
    }
    if (voiceOn) {
      setVoiceOn(false)
      return
    }
    const rec = new SR()
    rec.lang = "zh-CN"
    rec.interimResults = true
    rec.onresult = (e: any) => {
      const txt = Array.from(e.results).map((r: any) => r[0].transcript).join("")
      setInputText((prev) => prev + txt)
    }
    rec.onend = () => setVoiceOn(false)
    rec.onerror = () => {
      setVoiceOn(false)
      toast.error("语音识别出错")
    }
    rec.start()
    setVoiceOn(true)
  }

  return (
    <div
      style={!isExpanded ? { width: width ? `${width}px` : undefined, ...style } : style}
      className={`${isExpanded ? "w-full flex-1" : "shrink-0 border-l border-neutral-200/80 dark:border-neutral-800"} bg-card/30 flex flex-col h-full min-h-0 select-none`}
    >
      {/* 线程顶部信息条 */}
      <div className="h-12 border-b border-border/70 px-3.5 flex items-center justify-between shrink-0 bg-background/50">
        <div className="flex items-center space-x-2 min-w-0 flex-1 pr-2">
          <Bot className="h-4 w-4 text-primary shrink-0" />
          <span className="text-xs font-semibold truncate text-foreground">
            {sessionTitle}
          </span>
          {playbook && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0 shrink-0 font-mono">
              {playbook}
            </Badge>
          )}
        </div>

        <div className="flex items-center space-x-1.5 shrink-0">
          {runActive && (
            <button
              onClick={handleStopRun}
              disabled={stopping}
              className="px-2 py-1 rounded text-[11px] font-medium text-rose-500 border border-rose-500/40 hover:bg-rose-500/10 transition-colors flex items-center gap-1"
              title="停止当前运行（在下一个审批门处安全中止）"
            >
              <StopCircle className="h-3.5 w-3.5" />
              {stopping ? "停止中" : "停止"}
            </button>
          )}

          {/* IR 查看切换按钮（对话模式下呈现；IR 模式由卡片内单一返回按钮控制） */}
          {!showIR && (
            <button
              onClick={() => setShowIR(true)}
              className="px-2 py-1 rounded-lg text-[11px] font-mono text-muted-foreground hover:text-foreground hover:bg-muted/70 border border-border/60 transition-colors flex items-center gap-1"
              title="查看 CompiledUtilityIR 规范数据"
            >
              <FileCode className="h-3.5 w-3.5 text-primary" />
              <span>IR</span>
            </button>
          )}

          {/* 更多会话操作（包含导出会话纪要与事件流，杜绝与顶部工件导出混淆） */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                title="会话记录操作"
              >
                <MoreHorizontal className="h-3.5 w-3.5" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48 text-xs">
              <div className="px-2 py-1 text-[10px] font-medium text-muted-foreground">
                会话历史记录导出
              </div>
              <DropdownMenuItem
                onClick={() =>
                  sessionId &&
                  api.exportSession(sessionId, "md").catch((e) => toast.error("导出失败: " + e.message))
                }
                className="cursor-pointer"
              >
                <FileText className="h-3.5 w-3.5 mr-2 text-primary" />
                导出 Markdown 纪要
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() =>
                  sessionId &&
                  api.exportSession(sessionId, "jsonl").catch((e) => toast.error("导出失败: " + e.message))
                }
                className="cursor-pointer"
              >
                <FileCode className="h-3.5 w-3.5 mr-2 text-primary" />
                导出 JSONL 原始事件流
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          {/* 展开全宽主台 / 分屏视口切换按钮 */}
          {onToggleExpand && (
            <button
              onClick={onToggleExpand}
              className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              title={isExpanded ? "收起为分屏视口" : "展开为全宽对话工坊"}
            >
              {isExpanded ? (
                <Minimize2 className="h-3.5 w-3.5" />
              ) : (
                <Maximize2 className="h-3.5 w-3.5" />
              )}
            </button>
          )}
        </div>
      </div>

      {/* 消息与工具事件滚动区 */}
      <div ref={scrollRef} onScroll={handleScroll} className="flex-1 overflow-y-auto p-3.5 space-y-3 min-h-0">
        {showIR ? (
          <div className="rounded-xl border border-border/70 bg-muted/20 p-3.5 text-xs font-mono space-y-3">
            <div className="flex items-center justify-between pb-2 border-b border-border/60">
              <div className="flex items-center space-x-1.5">
                <FileCode className="h-3.5 w-3.5 text-primary" />
                <span className="font-semibold text-foreground font-sans text-xs">CompiledUtilityIR 规范数据</span>
                <span className="text-[10px] text-muted-foreground font-mono">v1.0</span>
              </div>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setShowIR(false)}
                className="h-6 px-2 text-xs text-primary hover:text-primary hover:bg-primary/10 gap-1 font-sans"
                title="返回对话"
              >
                <ArrowLeft className="h-3 w-3" />
                返回对话
              </Button>
            </div>

            {irData ? (
              <>
                {(() => {
                  const oldEv = events.find((ev) => (ev.payload || {}).ir || (ev.payload || {}).result_ir)
                  const oldIr = oldEv ? (oldEv.payload.ir || oldEv.payload.result_ir) : null
                  return oldIr ? (
                     <div className="rounded-lg border border-border/60 bg-background/40 p-2 font-sans">
                      <div className="text-[10px] font-medium text-foreground mb-1">IR 变更审查（逐块 accept/reject）</div>
                      <DiffView oldObj={oldIr} newObj={irData} />
                    </div>
                  ) : null
                })()}
                <pre className="text-[10px] text-foreground/90 overflow-x-auto whitespace-pre leading-relaxed max-h-[500px]">
                  {JSON.stringify(irData, null, 2)}
                </pre>
              </>
            ) : (
              <div className="py-12 text-center text-muted-foreground text-xs font-sans space-y-2">
                <div className="w-10 h-10 rounded-full bg-muted/60 flex items-center justify-center mx-auto text-muted-foreground/60 mb-1">
                  <FileCode className="h-5 w-5" />
                </div>
                <p className="font-medium text-foreground">当前会话暂无 CompiledUtilityIR 产物</p>
                <p className="text-[11px] text-muted-foreground/70 max-w-xs mx-auto leading-relaxed">
                  发起工程任务求解后此处将呈现标准化 IR 数据结构
                </p>
              </div>
            )}
          </div>
        ) : events.length === 0 ? (
          <div className="flex-1 flex flex-col items-center justify-center p-8 select-none my-auto">
            <div className="w-12 h-12 rounded-2xl bg-neutral-100 dark:bg-neutral-800/60 border border-neutral-200/80 dark:border-neutral-700/60 flex items-center justify-center text-neutral-400 dark:text-neutral-500 shadow-2xs mb-4">
              <Sparkles className="h-6 w-6 stroke-[1.4]" />
            </div>
            <h1 className="text-2xl font-bold tracking-tight text-neutral-900 dark:text-neutral-100 font-sans">
              你想构建什么？
            </h1>
          </div>
        ) : (
          <>
            {/* 初始引导 */}
            <div className="p-3 rounded-xl border border-border/60 bg-muted/20 text-xs text-muted-foreground space-y-1">
              <div className="flex items-center space-x-1.5 text-foreground font-medium">
                <Sparkles className="h-3.5 w-3.5 text-primary" />
                <span>市政自愈智能体已就绪</span>
              </div>
              <p className="leading-relaxed">
                输入工程设计指令（如“重力雨水管网放样与碰撞自愈”），智能体将自动调用 Headless 几何管道并实时投影到左侧视口。
              </p>
            </div>

            {/* 动态事件渲染（deriveThreadView：消息原样 / tool_call 按 toolCallId 配对成卡 / 子代理生命周期内联标记） */}
            {threadItems.map((item, idx) => {
              if (item.kind === "marker") {
                const mk = item.marker
                const mkRole = mk.role
                  ? SUBAGENT_ROLES[mk.role]?.title || mk.role
                  : "子代理"
                const MkIcon =
                  mk.status === "completed" ? CheckCircle2
                  : mk.status === "failed" ? XCircle
                  : mk.status === "cancelled" ? Ban
                  : PackageCheck
                const mkColor =
                  mk.status === "completed" || mk.status === "delivery" ? "text-emerald-500"
                  : mk.status === "failed" ? "text-rose-500"
                  : "text-muted-foreground"
                return (
                  <div key={mk.ev.id || `mk-${idx}`} className="flex items-center gap-1.5 px-1 text-[10px] font-mono text-muted-foreground/80 select-none">
                    <MkIcon className={`h-3 w-3 shrink-0 ${mkColor}`} />
                    <span>
                      {mk.status === "delivery"
                        ? `${mkRole} 交付清单已提交`
                        : mk.status === "completed"
                        ? `${mkRole} 完成${mk.receipt ? " · 交付清单已提交" : ""}`
                        : mk.status === "failed"
                        ? `${mkRole} 失败${mk.error ? ` · ${String(mk.error).slice(0, 60)}` : ""}`
                        : `${mkRole} 已取消`}
                    </span>
                    <span className="ml-auto text-[9px] text-muted-foreground/50">
                      {formatMessageTime(mk.ev.created_at)}
                    </span>
                  </div>
                )
              }
              const ev = item.kind === "tool" ? (item.card.resultEv ?? item.card.callEv) : item.ev
              const p = item.kind === "tool"
                ? { ...(item.card.callEv.payload || {}), ...(item.card.resultEv?.payload || {}) }
                : ev.payload || {}
              if (item.kind === "message") {
                const isUser = p.role === "user"
                const content = String(p.content || "")
                const msgKey = ev.id || String(idx)
                const long = !isUser && content.length > 200
                const expanded = !!expandedMsgs[msgKey]
                const shown = long && !expanded ? content.slice(0, 80).replace(/\s+\S*$/, "") + " …" : content
                const usage = !isUser ? (p.usage || null) : null
                const msgTime = formatMessageTime(ev.created_at || (p as any).timestamp || (p as any).created_at)

                return (
                  <div
                    key={msgKey}
                    className={`group relative flex flex-col ${isUser ? "items-end" : "items-start"} gap-1`}
                  >
                    <div
                      className={`max-w-[85%] rounded-2xl px-3.5 py-2 text-xs leading-relaxed ${
                        isUser
                          ? "bg-primary text-primary-foreground font-normal rounded-tr-none shadow-sm"
                          : "bg-muted/60 text-foreground border border-border/60 rounded-tl-none shadow-xs"
                      }`}
                    >
                      {(() => {
                        const steps = !isUser ? parsePlanSteps(content) : null
                        if (steps) {
                          const rest = content.replace(/^\s*[-*]\s+\[[ xX]\]\s+.*$/gm, "").trim()
                          return (
                            <div className="space-y-1.5">
                              <PlanSteps
                                steps={steps}
                                onConfirm={(sp) => {
                                  setPlanSpec(sp)
                                  toast.success(`计划已确认(${sp.length} 步)，将作为 spec 执行`)
                                }}
                              />
                              {rest && <span className="whitespace-pre-wrap">{rest}</span>}
                            </div>
                          )
                        }
                        return (
                          <>
                            <span className="whitespace-pre-wrap">{shown}</span>
                            {long && (
                              <button
                                type="button"
                                onClick={() => setExpandedMsgs((prev) => ({ ...prev, [msgKey]: !expanded }))}
                                className="ml-1.5 text-[10px] font-medium text-primary hover:underline shrink-0 align-bottom"
                              >
                                {expanded ? "收起" : "展开全文"}
                              </button>
                            )}
                          </>
                        )
                      })()}

                      {/* 模型回答用量底标 */}
                      {usage && (usage.total_tokens || usage.completion_tokens) && (
                        <div className="mt-1.5 pt-1 border-t border-border/40 text-[10px] font-mono text-muted-foreground/80 flex items-center gap-2">
                          <span>{usage.total_tokens || usage.completion_tokens} tokens</span>
                          {usage.latency_ms != null && <span>{Math.round(usage.latency_ms)}ms</span>}
                          {usage.cost_usd != null && <span>${usage.cost_usd.toFixed(4)}</span>}
                        </div>
                      )}
                    </div>

                    {/* 消息气泡外部工具栏 (1:1 响应用户指示：位于对话框外的右下/左下方) */}
                    {isUser ? (
                      /* 用户消息外部右下角：时间 + 复制 + 回滚(撤销后续) + 分叉 */
                      <div className="flex items-center gap-2 text-[10px] text-muted-foreground font-mono select-none px-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        {msgTime && <span>{msgTime}</span>}
                        <button
                          type="button"
                          title="复制提问"
                          onClick={() => {
                            navigator.clipboard?.writeText(content)
                            toast.success("已复制提问")
                          }}
                          className="p-1 rounded hover:bg-muted hover:text-foreground text-muted-foreground transition-colors cursor-pointer"
                        >
                          <Copy className="h-3 w-3" />
                        </button>
                        <button
                          type="button"
                          title="回滚到此处 (撤销此问题及之后的所有生成和修改)"
                          onClick={() => handleRewind(msgKey)}
                          className="p-1 rounded hover:bg-muted hover:text-amber-500 text-muted-foreground transition-colors cursor-pointer"
                        >
                          <RotateCcw className="h-3 w-3" />
                        </button>
                        <button
                          type="button"
                          title="从此分叉新分支会话 (Fork)"
                          onClick={() => handleFork(msgKey)}
                          className="p-1 rounded hover:bg-muted hover:text-primary text-muted-foreground transition-colors cursor-pointer"
                        >
                          <GitBranch className="h-3 w-3" />
                        </button>
                      </div>
                    ) : (
                      /* 模型回答外部左下方：时间 + 复制 + 重新生成 + 分叉 */
                      <div className="flex items-center gap-2 text-[10px] text-muted-foreground font-mono select-none px-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        {msgTime && <span>{msgTime}</span>}
                        <button
                          type="button"
                          title="复制回答"
                          onClick={() => {
                            navigator.clipboard?.writeText(content)
                            toast.success("已复制回答内容")
                          }}
                          className="p-1 rounded hover:bg-muted hover:text-foreground text-muted-foreground transition-colors cursor-pointer"
                        >
                          <Copy className="h-3 w-3" />
                        </button>
                        <button
                          type="button"
                          title="重新生成此回答"
                          onClick={() => handleRegenerateFromAssistant(idx)}
                          className="p-1 rounded hover:bg-muted hover:text-emerald-500 text-muted-foreground transition-colors cursor-pointer"
                        >
                          <RefreshCw className="h-3 w-3" />
                        </button>
                        <button
                          type="button"
                          title="从此分叉新分支会话 (Fork)"
                          onClick={() => handleFork(msgKey)}
                          className="p-1 rounded hover:bg-muted hover:text-primary text-muted-foreground transition-colors cursor-pointer"
                        >
                          <GitBranch className="h-3 w-3" />
                        </button>
                      </div>
                    )}
                  </div>
                )
              } else if (item.kind === "tool") {
                const card = item.card
                const toolId = String(p.toolCallId || card.callEv.id || idx)
                const isCollapsed = collapsedTools[toolId]
                const preview =
                  p.result_ui_view || p.result_llm_view || p.args_summary || (card.pending ? "算子执行中..." : "算子执行完毕")
                const isSubAgent = p.toolName === "subagent" || !!p.agent_role || !!p.agent || !!card.requestId
                const rawRole = (card.role || p.agent_role || p.agent || p.batch || (p.toolName === "subagent" ? "planner" : "")).toLowerCase()
                const subRole = SUBAGENT_ROLES[rawRole] || (isSubAgent ? {
                  title: `子代理 · ${rawRole || "协同任务"}`,
                  desc: "专业工程子代理协同计算",
                  badgeColor: "border-sky-500/30 text-sky-500 bg-sky-500/10",
                  icon: Bot,
                } : null)
                // 进行中:未配对 result 的卡显示耗时秒数(秒级跳动由 activityNow 驱动)
                const pendingSec = card.pending
                  ? Math.max(0, Math.floor(((busy ? activityNow : Date.now()) - (Date.parse(card.callEv.created_at || "") || Date.now())) / 1000))
                  : 0
                // 子代理终态(request_id 关联生命周期事件)优先于 judge verdict
                const terminalBadge = card.terminalStatus
                  ? card.terminalStatus === "completed"
                    ? { text: "COMPLETED", cls: "text-emerald-500 border-emerald-500/30 bg-emerald-500/5" }
                    : card.terminalStatus === "failed"
                    ? { text: "FAILED", cls: "text-rose-500 border-rose-500/30 bg-rose-500/5" }
                    : { text: "CANCELLED", cls: "text-muted-foreground border-border bg-muted/30" }
                  : null

                if (isSubAgent && subRole) {
                  const RoleIcon = subRole.icon
                  const verdict = terminalBadge?.text || p.verdict || (card.pending ? "RUNNING" : p.status === "ok" ? "DONE" : p.status?.toUpperCase() || p.phase || "DONE")
                  return (
                    <div
                      key={toolId}
                      className={`rounded-xl border overflow-hidden text-xs shadow-xs ${
                        card.pending ? "border-sky-500/50 bg-card/80 animate-pulse" : "border-border/70 bg-card/80"
                      }`}
                    >
                      <div
                        onClick={() => toggleToolCollapse(toolId)}
                        className="flex items-center justify-between p-2.5 cursor-pointer hover:bg-muted/40 transition-colors"
                      >
                        <div className="flex items-center space-x-2.5">
                          <div className="p-1.5 rounded-lg bg-primary/10 text-primary">
                            {card.pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RoleIcon className="h-4 w-4" />}
                          </div>
                          <div>
                            <div className="flex items-center space-x-2">
                              <span className="font-semibold text-foreground">{subRole.title}</span>
                              <Badge
                                variant="outline"
                                className={`text-[10px] font-mono px-1.5 py-0 ${
                                  terminalBadge
                                    ? terminalBadge.cls
                                    : verdict === "PASS"
                                    ? "text-emerald-500 border-emerald-500/30 bg-emerald-500/5"
                                    : verdict === "FIX"
                                    ? "text-amber-500 border-amber-500/30 bg-amber-500/5"
                                    : verdict === "ESCALATE"
                                    ? "text-rose-500 border-rose-500/30 bg-rose-500/5"
                                    : "text-sky-500 border-sky-500/30 bg-sky-500/5"
                                }`}
                              >
                                {verdict}
                              </Badge>
                              {card.pending && (
                                <span className="text-[10px] font-mono text-sky-500/80">{pendingSec}s</span>
                              )}
                            </div>
                            <div className="text-[10px] text-muted-foreground">
                              {subRole.desc}
                              {card.receipt && <span className="text-emerald-500/80"> · 交付清单已提交</span>}
                            </div>
                          </div>
                        </div>
                        {isCollapsed ? (
                          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                        ) : (
                          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
                        )}
                      </div>

                      {!isCollapsed && (
                        <div className="p-3 pt-0 border-t border-border/40 font-mono text-[11px] text-muted-foreground bg-muted/15 leading-relaxed max-h-48 overflow-y-auto space-y-1">
                          {(p.child_session_id || p.child_session_path) && (
                            <div className="text-[10px] text-muted-foreground/70">
                              子会话 ID: {p.child_session_id || p.child_session_path}
                            </div>
                          )}
                          <div className="whitespace-pre-wrap">{typeof preview === "string" ? preview : JSON.stringify(preview)}</div>
                        </div>
                      )}
                    </div>
                  )
                }

                return (
                  <div
                    key={toolId}
                    className={`rounded-xl border overflow-hidden text-xs shadow-xs ${
                      card.pending ? "border-sky-500/50 bg-card/60 animate-pulse" : "border-border/60 bg-card/60"
                    }`}
                  >
                    <div
                      onClick={() => toggleToolCollapse(toolId)}
                      className="flex items-center justify-between p-2.5 cursor-pointer hover:bg-muted/40 transition-colors"
                    >
                      <div className="flex items-center space-x-2">
                        <div className="p-1 rounded-md bg-primary/10 text-primary">
                          {card.pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wrench className="h-3.5 w-3.5" />}
                        </div>
                        <span className="font-mono font-medium text-foreground">
                          {p.toolName || "geometry_solver"}
                        </span>
                        {card.pending ? (
                          <>
                            <Badge variant="outline" className="text-[10px] font-mono px-1 py-0 text-sky-500 border-sky-500/30 bg-sky-500/5">
                              RUNNING
                            </Badge>
                            <span className="text-[10px] font-mono text-sky-500/80">{pendingSec}s</span>
                          </>
                        ) : (
                          <Badge variant="outline" className={`text-[10px] font-mono px-1 py-0 ${
                            p.status === "error" || p.status === "denied" || p.status === "rejected"
                              ? "text-rose-500 border-rose-500/30"
                              : "text-emerald-500 border-emerald-500/30"
                          }`}>
                            {p.status?.toUpperCase() || "OK"}
                          </Badge>
                        )}
                      </div>
                      {isCollapsed ? (
                        <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                      ) : (
                        <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
                      )}
                    </div>

                    {!isCollapsed && (
                      <div className="p-2.5 pt-0 border-t border-border/40 font-mono text-[11px] text-muted-foreground bg-muted/10 leading-relaxed max-h-40 overflow-y-auto">
                        {typeof preview === "string" ? preview : JSON.stringify(preview)}
                      </div>
                    )}
                  </div>
                )
              }
              return null
            })}

            {/* 运行收尾行：active→idle 时从事件推导（被中断的运行显示「已中断」） */}
            {wrapUp && !runActive && (
              <div className="flex items-center justify-center gap-1.5 pt-1 text-[10px] font-mono text-muted-foreground/70 select-none">
                {wrapUp.interrupted ? (
                  <Ban className="h-3 w-3 text-rose-500/70" />
                ) : (
                  <CheckCircle2 className="h-3 w-3 text-emerald-500/70" />
                )}
                <span>
                  {wrapUp.interrupted ? "已中断" : "已完成"} · {wrapUp.toolCalls} 次工具调用 · 用时 {fmtElapsed(wrapUp.seconds)}
                </span>
              </div>
            )}

            {/* 流式生成中的 assistant 气泡：思维链（斜体）+ 逐字正文 + 闪烁光标 */}
            {streamingMsg && (
              <div className="flex justify-start">
                <div className="max-w-[85%] rounded-2xl px-3.5 py-2 text-xs leading-relaxed bg-muted/60 text-foreground border border-border/60 rounded-tl-none shadow-xs">
                  {streamingMsg.reasoning && (
                    <div className="mb-1.5 text-[10px] text-muted-foreground/80 italic border-l-2 border-primary/30 pl-2 whitespace-pre-wrap">
                      {streamingMsg.reasoning}
                    </div>
                  )}
                  <span className="whitespace-pre-wrap">{streamingMsg.content}</span>
                  <span className="inline-block w-1.5 h-3 ml-0.5 bg-primary/70 animate-pulse align-text-bottom" />
                </div>
              </div>
            )}

            {/* 人类在回路 HITL 决策门卡片 */}
            {approvals.map((appr) => (
              <div
                key={appr.id}
                className="p-3.5 rounded-xl border border-amber-500/40 bg-amber-500/5 space-y-2.5 text-xs shadow-sm"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <ShieldCheck className="h-4 w-4 text-amber-500" />
                    <span className="font-semibold text-foreground">工程审批决策门 (HITL)</span>
                  </div>
                  <Badge className="bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30 text-[10px]">
                    待裁决
                  </Badge>
                </div>

                <p className="text-muted-foreground leading-relaxed">
                  智能体请求执行 <code className="font-mono text-primary">{appr.operation}</code>
                  ，该操作将修改管线标高与覆土参数。
                </p>

                <div className="flex items-center space-x-2 pt-1">
                  <Button
                    size="sm"
                    disabled={decidingId === appr.id}
                    onClick={() => handleApproval(appr.id, "approved")}
                    className="flex-1 h-7 text-xs bg-emerald-600 hover:bg-emerald-700 text-white gap-1"
                  >
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    批准放行
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={decidingId === appr.id}
                    onClick={() => handleApproval(appr.id, "rejected")}
                    className="h-7 px-3 text-xs text-rose-500 hover:text-rose-600 hover:bg-rose-500/10 gap-1"
                  >
                    <XCircle className="h-3.5 w-3.5" />
                    拒绝
                  </Button>
                </div>
              </div>
            ))}
          </>
        )}
      </div>

      {/* 底部输入器 Composer (统一贴底对话框，对齐图二与图一环境条) */}
      <div className="p-3 border-t border-border/70 bg-background/50 space-y-2 shrink-0">
        {/* 上下文用量细条 (Claude Code 范式)：已用占比 + 服务端自动压缩阈值 */}
        <div
          className="flex items-center gap-2 px-0.5 select-none"
          title={`上下文占用 ${fmtK(usedTokens)} / ${fmtK(contextWindow)} (${ctxPct}%)；服务端将在 ${budgetPct}% 处自动压缩历史`}
        >
          <div className="flex-1 h-1 rounded-full bg-muted/60 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                ctxPct >= budgetPct ? "bg-rose-500" : ctxRatio >= budgetRatio * 0.75 ? "bg-amber-500" : "bg-primary/70"
              }`}
              style={{ width: `${ctxPct}%` }}
            />
          </div>
          <span className="text-[9px] font-mono text-muted-foreground/60 shrink-0">
            {fmtK(usedTokens)}/{fmtK(contextWindow)} · 自动压缩于 {budgetPct}%
          </span>
        </div>

        {/* Queued Messages 排队条 (对齐现代 Agent IDE 范式) */}
        {queuedMsgs.length > 0 && (
          <div className="rounded-xl border border-border/80 bg-background/95 backdrop-blur-md shadow-xs overflow-hidden transition-all text-xs">
            <div
              onClick={() => setShowQueuedList(!showQueuedList)}
              className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-muted/40 transition-colors select-none"
            >
              <div className="flex items-center gap-2">
                <span className="font-medium text-foreground">Queued Messages</span>
                <span className="min-w-4 h-4 px-1 rounded-full bg-muted text-foreground text-[10px] font-mono flex items-center justify-center font-semibold">
                  {queuedMsgs.length}
                </span>
                <span className="text-[11px] text-muted-foreground">Sends after agent finishes working</span>
              </div>
              <ChevronUp
                className={`h-3.5 w-3.5 text-muted-foreground transition-transform duration-200 ${
                  showQueuedList ? "rotate-180" : ""
                }`}
              />
            </div>
            {showQueuedList && (
              <div className="p-2 pt-1 space-y-1.5 border-t border-border/40 bg-muted/20">
                {queuedMsgs.map((q, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg bg-background border border-border/60 text-xs shadow-xs"
                  >
                    <span className="font-mono text-muted-foreground text-[11px] shrink-0">#{idx + 1}</span>
                    <span className="truncate flex-1 text-foreground">{q}</span>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation()
                        setQueuedMsgs((list) => list.filter((_, i) => i !== idx))
                      }}
                      className="text-muted-foreground hover:text-rose-500 p-0.5 rounded transition-colors"
                      title="移除"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                ))}
                <div className="flex justify-end pt-1">
                  <button
                    type="button"
                    onClick={() => setQueuedMsgs([])}
                    className="text-[11px] text-muted-foreground hover:text-rose-500 transition-colors cursor-pointer"
                  >
                    清空全部队列
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* 1 task running 指示条 */}
        {(sending || runActive) && (
          <div className="rounded-xl border border-border/80 bg-background/95 backdrop-blur-md shadow-xs px-3 py-2 flex items-center justify-between text-xs select-none">
            <div className="flex items-center gap-2">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-500 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
              </span>
              <span className="font-medium text-foreground">1 task running</span>
              <span className="text-[11px] text-muted-foreground font-mono">
                {activityText || "工程求解器运算中..."}
              </span>
            </div>
            <button
              type="button"
              onClick={handleStopAll}
              className="text-[11px] font-medium text-rose-500 hover:text-rose-600 bg-rose-500/10 hover:bg-rose-500/20 px-2 py-0.5 rounded-md transition-colors cursor-pointer"
              title="打断中止"
            >
              打断中止
            </button>
          </div>
        )}

        <div className="relative rounded-2xl border border-border/70 bg-card/80 dark:bg-neutral-900/90 focus-within:border-primary/80 focus-within:ring-1 focus-within:ring-primary/40 transition-all shadow-sm p-2.5 space-y-2">
          <textarea
            value={inputText}
            onChange={(e) => {
              const v = e.target.value
              setInputText(v)
              setShowCommands(v.startsWith("/"))
              const at = v.lastIndexOf("@")
              if (at >= 0 && !v.slice(at).includes(" ")) {
                setMentionOpen(true)
                setMentionQuery(v.slice(at + 1).toLowerCase())
              } else {
                setMentionOpen(false)
              }
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault()
                if (runActive) handleSteer()
                else handleSendMessage()
                return
              }
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault()
                handleSendMessage()
              }
            }}
            placeholder={
              (sending || runActive)
                ? "运行中：输入要求按 Enter 自动排队 · 右下红点随时打断..."
                : "描述您的 BIM 建模意图或自愈需求（Shift+Enter 换行）..."
            }
            rows={2}
            className="w-full bg-transparent border-none outline-none resize-none text-xs text-foreground placeholder:text-muted-foreground/60 leading-relaxed min-h-[48px]"
          />

          {/* 斜杠命令面板(输入 / 触发,模糊过滤) */}
          {showCommands && (
            <div className="absolute left-2 right-2 bottom-full mb-1 rounded-xl border border-border/70 bg-popover shadow-lg z-40 max-h-56 overflow-y-auto p-1 text-xs">
              {SLASH_COMMANDS.filter((c) => inputText === "/" || c.cmd.startsWith(inputText.split(" ")[0])).map((c) => (
                <button
                  key={c.cmd}
                  type="button"
                  onClick={() => {
                    setInputText("")
                    setShowCommands(false)
                    runCommand(c.cmd)
                  }}
                  className="w-full flex items-center justify-between px-2 py-1.5 rounded-lg hover:bg-muted/60 cursor-pointer text-left"
                >
                  <span className="font-mono text-primary shrink-0">{c.cmd}</span>
                  <span className="text-muted-foreground truncate ml-2">{c.desc}</span>
                </button>
              ))}
            </div>
          )}

          {/* 已选 @mention 引用芯片 */}
          {refs.length > 0 && (
            <div className="flex flex-wrap gap-1 px-0.5">
              {refs.map((r, i) => (
                <span
                  key={i}
                  className="flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-primary/10 text-primary text-[10px] font-mono"
                >
                  @{r.kind}:{r.name}
                  <button
                    type="button"
                    onClick={() => setRefs((x) => x.filter((_, j) => j !== i))}
                    className="hover:text-rose-500"
                  >
                    <X className="h-2.5 w-2.5" />
                  </button>
                </span>
              ))}
            </div>
          )}

          {/* @mention 引用面板(@ir/@rule/@file/@memory 模糊搜索) */}
          {mentionOpen && (
            <div className="absolute left-2 right-2 bottom-full mb-1 rounded-xl border border-border/70 bg-popover shadow-lg z-40 max-h-56 overflow-y-auto p-1 text-xs">
              {mentionItems
                .filter((it) => it.name.toLowerCase().includes(mentionQuery))
                .slice(0, 12)
                .map((it) => (
                  <button
                    key={`${it.kind}:${it.name}`}
                    type="button"
                    onClick={() => {
                      setRefs((r) => [...r, { kind: it.kind, name: it.name }])
                      setInputText((t) => t.replace(/@\S*$/, `@${it.name} `))
                      setMentionOpen(false)
                    }}
                    className="w-full flex items-center justify-between px-2 py-1.5 rounded-lg hover:bg-muted/60 cursor-pointer text-left"
                  >
                    <span className="font-mono text-primary shrink-0">@{it.kind}</span>
                    <span className="truncate ml-2 text-foreground">{it.name}</span>
                  </button>
                ))}
              {mentionItems.filter((it) => it.name.toLowerCase().includes(mentionQuery)).length === 0 && (
                <div className="px-2 py-1.5 text-muted-foreground">无匹配引用</div>
              )}
            </div>
          )}

          <div className="flex items-center justify-between gap-2 pt-1.5 border-t border-border/40">
            {/* 左侧：附件 + 语音 */}
            <div className="flex items-center gap-1 shrink-0">
              {/* 附件上传 */}
              <input
                type="file"
                ref={fileInputRef}
                onChange={handleFileUpload}
                className="hidden"
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors shrink-0"
                title="上传工程底图 CAD/IFC"
              >
                <Paperclip className="h-3.5 w-3.5" />
              </button>

              {/* 语音输入(Web Speech API,中文识别) */}
              <button
                type="button"
                onClick={toggleVoice}
                className={`p-1.5 rounded-lg transition-colors shrink-0 ${voiceOn ? "text-rose-500 bg-rose-500/10 animate-pulse" : "text-muted-foreground hover:text-foreground hover:bg-muted/60"}`}
                title={voiceOn ? "停止语音输入" : "语音输入(中文识别)"}
              >
                {voiceOn ? <MicOff className="h-3.5 w-3.5" /> : <Mic className="h-3.5 w-3.5" />}
              </button>
            </div>

            {/* 右侧：模型选择器 + 发送/停止按钮 (宽敞排布、纯净优雅、永不挤折行) */}
            <div className="flex items-center gap-1.5 shrink-0">
              {/* 模型+思考+上下文 三合一选择器 */}
              <ModelPicker
                modelsData={modelsData}
                currentModel={currentModel}
                onSelectModel={handleSelectModel}
                effort={effort}
                onEffortChange={setEffort}
                contextWindow={contextWindow}
                onContextWindowChange={setContextWindow}
                onOpenSettings={onOpenSettings}
              />

              {/* 运行中输入文字时呈现的快捷「排队」胶囊按钮 */}
              {(sending || runActive) && inputText.trim() && (
                <button
                  type="button"
                  onClick={handleSendMessage}
                  className="px-2.5 py-1 rounded-full bg-primary text-primary-foreground hover:bg-primary/90 text-xs font-medium shadow-xs transition-all cursor-pointer shrink-0 animate-in fade-in"
                  title="加入消息排队队列 (Enter 亦可自动排队)"
                >
                  排队
                </button>
              )}

              {/* 发送/打断停止按钮 + 环形上下文进度条 */}
              <div
                className="relative w-8 h-8 flex items-center justify-center shrink-0"
                title={`上下文占用: ${fmtK(usedTokens)} / ${fmtK(contextWindow)} (${ctxPct}%)\n${
                  (sending || runActive) ? "点击立即打断中止任务" : "按 Enter 发送指令"
                }`}
              >
                {/* 环形上下文进度条 SVG */}
                <svg className="absolute inset-0 w-8 h-8 pointer-events-none -rotate-90" viewBox="0 0 32 32">
                  {/* 背景轨道 */}
                  <circle
                    cx="16"
                    cy="16"
                    r="13"
                    stroke="currentColor"
                    strokeWidth="2"
                    fill="none"
                    className="text-muted/30 dark:text-zinc-800"
                  />
                  {/* 进度动态环 */}
                  <circle
                    cx="16"
                    cy="16"
                    r="13"
                    stroke="currentColor"
                    strokeWidth="2.5"
                    fill="none"
                    strokeDasharray={81.68}
                    strokeDashoffset={81.68 * (1 - ctxRatio)}
                    strokeLinecap="round"
                    className={`transition-all duration-500 ${
                      ctxRatio >= 0.85
                        ? "text-rose-500"
                        : ctxRatio >= 0.65
                        ? "text-amber-500"
                        : ctxRatio > 0
                        ? "text-primary"
                        : "text-transparent"
                    }`}
                  />
                </svg>

                {/* 核心发送 / 打断停止按钮 */}
                {(sending || runActive) ? (
                  <button
                    type="button"
                    onClick={handleStopAll}
                    className="h-6 w-6 rounded-full bg-rose-500 text-white hover:bg-rose-600 flex items-center justify-center shadow-xs transition-all cursor-pointer z-10 hover:scale-110 active:scale-95 animate-pulse"
                    title="立即打断中止当前任务 (Stop)"
                  >
                    <Square className="h-2.5 w-2.5 fill-current" />
                  </button>
                ) : (
                  <button
                    type="button"
                    disabled={!inputText.trim()}
                    onClick={handleSendMessage}
                    className={`h-6 w-6 rounded-full flex items-center justify-center shadow-xs transition-all z-10 ${
                      inputText.trim()
                        ? "bg-primary text-primary-foreground hover:bg-primary/90 cursor-pointer scale-105"
                        : "bg-muted/70 text-muted-foreground/50 cursor-not-allowed"
                    }`}
                    title="发送 (Enter)"
                  >
                    <Send className="h-3 w-3" />
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
