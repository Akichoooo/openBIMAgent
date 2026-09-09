import React, { useState, useEffect } from "react"
import { api, SessionItem } from "@/services/api"
import { Header } from "@/components/layout/Header"
import { Sidebar, getSessionTitle } from "@/components/layout/Sidebar"
import { CanvasViewport } from "@/components/viewport/CanvasViewport"
import { TraceTimeline } from "@/components/trace/TraceTimeline"
import { ChatThread } from "@/components/chat/ChatThread"
import { SettingsDialog } from "@/components/settings/SettingsDialog"
import { NewTaskDialog } from "@/components/layout/NewTaskDialog"
import { Toaster } from "@/components/ui/sonner"
import { toast } from "sonner"
import { CommandPalette, type PaletteItem } from "@/components/chat/CommandPalette"
import { ErrorBoundary } from "@/components/chat/ErrorBoundary"
import { AuditDialog } from "@/components/chat/AuditDialog"
import { BottomTerminalPanel } from "@/components/layout/BottomTerminalPanel"

export default function App() {
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null)
  const [currentSession, setCurrentSession] = useState<SessionItem | null>(null)
  const [viewMode, setViewMode] = useState<"3d" | "plan" | "prof" | "trace">("3d")
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsTab, setSettingsTab] = useState<string>("appearance")
  const [settingsModel, setSettingsModel] = useState<string | undefined>(undefined)

  // 智能体全局运行状态 (与 StatusBar、BottomTerminalPanel 联动)
  const [agentStatus, setAgentStatus] = useState({
    running: false,
    currentModel: "",
    pendingApprovals: 0,
  })

  // 三栏布局与中央底部终端折叠控制 (对标 VS Code 范式)
  const [showLeftSidebar, setShowLeftSidebar] = useState<boolean>(() => {
    const saved = localStorage.getItem("wb_panel_left")
    return saved !== null ? saved === "true" : true
  })
  const [showBottomTerminal, setShowBottomTerminal] = useState<boolean>(() => {
    const saved = localStorage.getItem("wb_panel_bottom")
    return saved !== null ? saved === "true" : false
  })
  const [showRightChat, setShowRightChat] = useState<boolean>(() => {
    const saved = localStorage.getItem("wb_panel_right")
    return saved !== null ? saved === "true" : true
  })
  // 全宽对话工坊焦点模式 (对标图二 Box-Agent 主工作台模式，与分屏 3D 视口一键互切)
  const [chatFocusMode, setChatFocusMode] = useState<boolean>(() => {
    const saved = localStorage.getItem("wb_chat_focus")
    return saved === "true"
  })

  const toggleChatFocus = () => {
    setChatFocusMode((prev) => {
      const next = !prev
      localStorage.setItem("wb_chat_focus", String(next))
      return next
    })
  }
  const [sessionEvents, setSessionEvents] = useState<any[]>([])

  const toggleLeftSidebar = () => {
    setShowLeftSidebar((prev) => {
      const next = !prev
      localStorage.setItem("wb_panel_left", String(next))
      return next
    })
  }

  const toggleBottomTerminal = () => {
    setShowBottomTerminal((prev) => {
      const next = !prev
      localStorage.setItem("wb_panel_bottom", String(next))
      return next
    })
  }

  const toggleRightChat = () => {
    setShowRightChat((prev) => {
      const next = !prev
      localStorage.setItem("wb_panel_right", String(next))
      return next
    })
  }

  // 左右边栏拖拽调宽与本地持久化 (工业级 CAD/BIM 工作台范式)
  const [sidebarWidth, setSidebarWidth] = useState<number>(() => {
    const saved = localStorage.getItem("wb_sidebar_width")
    return saved ? Math.max(200, Math.min(460, parseInt(saved, 10))) : 260
  })
  const [chatWidth, setChatWidth] = useState<number>(() => {
    const saved = localStorage.getItem("wb_chat_width")
    return saved ? Math.max(360, Math.min(760, parseInt(saved, 10))) : 460
  })

  useEffect(() => {
    localStorage.setItem("wb_sidebar_width", String(sidebarWidth))
  }, [sidebarWidth])

  useEffect(() => {
    localStorage.setItem("wb_chat_width", String(chatWidth))
  }, [chatWidth])

  const startDraggingLeft = (e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startW = sidebarWidth

    const onMouseMove = (moveEvent: MouseEvent) => {
      const delta = moveEvent.clientX - startX
      const newW = Math.max(200, Math.min(460, startW + delta))
      setSidebarWidth(newW)
    }

    const onMouseUp = () => {
      window.removeEventListener("mousemove", onMouseMove)
      window.removeEventListener("mouseup", onMouseUp)
      document.body.style.removeProperty("cursor")
      document.body.style.removeProperty("user-select")
    }

    document.body.style.cursor = "col-resize"
    document.body.style.userSelect = "none"
    window.addEventListener("mousemove", onMouseMove)
    window.addEventListener("mouseup", onMouseUp)
  }

  const startDraggingRight = (e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startW = chatWidth

    const onMouseMove = (moveEvent: MouseEvent) => {
      const delta = startX - moveEvent.clientX
      const newW = Math.max(360, Math.min(760, startW + delta))
      setChatWidth(newW)
    }

    const onMouseUp = () => {
      window.removeEventListener("mousemove", onMouseMove)
      window.removeEventListener("mouseup", onMouseUp)
      document.body.style.removeProperty("cursor")
      document.body.style.removeProperty("user-select")
    }

    document.body.style.cursor = "col-resize"
    document.body.style.userSelect = "none"
    window.addEventListener("mousemove", onMouseMove)
    window.addEventListener("mouseup", onMouseUp)
  }

  const handleOpenSettings = (tab = "appearance", modelId?: string) => {
    setSettingsTab(tab)
    setSettingsModel(modelId)
    setSettingsOpen(true)
  }
  const [newTaskOpen, setNewTaskOpen] = useState(false)
  const [refreshTrigger, setRefreshTrigger] = useState(0)
  const [irData, setIrData] = useState<any>(null)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [auditOpen, setAuditOpen] = useState(false)
  // Context 预算(ChatThread 上报 → Header 预算条;pi-mono 范式)
  const [ctxUsage, setCtxUsage] = useState({ used: 0, ctx: 0 })
  const [paletteSessions, setPaletteSessions] = useState<SessionItem[]>([])

  // Theme management
  const [isDark, setIsDark] = useState<boolean>(() => {
    const saved = localStorage.getItem("wb_theme")
    if (saved) return saved === "dark"
    return window.matchMedia("(prefers-color-scheme: dark)").matches
  })

  useEffect(() => {
    if (isDark) {
      document.documentElement.classList.add("dark")
      document.documentElement.setAttribute("data-theme", "dark")
      localStorage.setItem("wb_theme", "dark")
    } else {
      document.documentElement.classList.remove("dark")
      document.documentElement.setAttribute("data-theme", "light")
      localStorage.setItem("wb_theme", "light")
    }
  }, [isDark])

  const toggleTheme = () => {
    setIsDark((prev) => !prev)
  }

  // 容错:网络断开/恢复提示(离线时 agent 调用可能失败)
  useEffect(() => {
    const on = () => toast.success("网络已恢复")
    const off = () => toast.error("网络已断开：agent 调用可能失败，视图为本地缓存")
    window.addEventListener("online", on)
    window.addEventListener("offline", off)
    return () => {
      window.removeEventListener("online", on)
      window.removeEventListener("offline", off)
    }
  }, [])

  // Load initial session if available
  useEffect(() => {
    const init = async () => {
      try {
        const sessions = await api.listSessions()
        const active = sessions.filter((s) => !s.archived)
        if (active.length > 0 && !currentSessionId) {
          setCurrentSessionId(active[0].session_id)
          setCurrentSession(active[0])
        }
      } catch (e) {
        console.error("Failed to load initial session:", e)
      }
    }
    init()
  }, [])

  // When currentSessionId changes, update currentSession object
  useEffect(() => {
    if (!currentSessionId) return
    api.listSessions().then((list) => {
      const found = list.find((s) => s.session_id === currentSessionId)
      if (found) setCurrentSession(found)
    })
  }, [currentSessionId, refreshTrigger])

  // 创建纯净空白新对话（对标 Cursor/Codex：不跑 batch pipeline，不塞假圆柱）
  const handleNewChat = async () => {
    try {
      const ws = await api.listWorkspaces().catch(() => ({ current: null }))
      const newSess = await api.createSession({
        title: "新工程对话",
        playbook: currentSession?.playbook || "municipal_utility",
        workspace: ws.current || undefined,
      })
      setCurrentSessionId(newSess.session_id)
      setCurrentSession({
        session_id: newSess.session_id,
        title: newSess.title,
        playbook: newSess.playbook,
        created_at: new Date().toISOString(),
        last_active: new Date().toISOString(),
        event_count: 0,
        workspace: newSess.workspace,
      })
      setIrData(null)
      setRefreshTrigger((c) => c + 1)
      toast.success("已创建新对话")
    } catch (err: any) {
      toast.error("新建对话失败: " + err.message)
    }
  }

  // Load 3D IR geometry whenever session changes（纯净模式：仅在有真实产物时投影，绝不自动插入占位圆柱）
  useEffect(() => {
    if (!currentSessionId) {
      setIrData(null)
      return
    }
    api.getSessionArtifact(currentSessionId, "compiled_utility_ir.json")
      .then((res) => {
        if (res && res.data) {
          setIrData(res.data)
        } else {
          setIrData(null)
        }
      })
      .catch(() => {
        setIrData(null)
      })
  }, [currentSessionId, refreshTrigger])

  // Global keyboard shortcuts (⌘K for new chat, ⌘, for settings)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault()
        handleNewChat()
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "p") {
        e.preventDefault()
        setPaletteOpen((o) => !o)
      } else if ((e.metaKey || e.ctrlKey) && e.key === ",") {
        e.preventDefault()
        handleOpenSettings("general")
      }
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [])

  // 工作区切换：刷新数据；若当前会话不属于新工作区则自动切到可见会话
  useEffect(() => {
    const handler = async () => {
      setRefreshTrigger((c) => c + 1)
      try {
        const [sessions, ws] = await Promise.all([api.listSessions(), api.listWorkspaces()])
        const visible = sessions
          .filter((s) => !s.archived)
          .filter((s) => (ws.current ? s.workspace === ws.current : !s.workspace))
        if (!visible.some((s) => s.session_id === currentSessionId)) {
          setCurrentSessionId(visible[0]?.session_id ?? null)
          setCurrentSession(visible[0] ?? null)
        }
      } catch {}
    }
    window.addEventListener("workspace-changed", handler)
    return () => window.removeEventListener("workspace-changed", handler)
  }, [currentSessionId])

  const handleSelectSession = (sessionId: string) => {
    setCurrentSessionId(sessionId)
  }

  const handleTaskStarted = (sessionId: string) => {
    setCurrentSessionId(sessionId)
    setRefreshTrigger((c) => c + 1)
  }

  // ⌘P 打开时加载会话列表(会话组条目)
  useEffect(() => {
    if (paletteOpen)
      api
        .listSessions()
        .then((s) => setPaletteSessions(s.filter((x) => !x.archived).slice(0, 8)))
        .catch(() => {})
  }, [paletteOpen])

  const paletteItems: PaletteItem[] = [
    ...["/compact", "/reflect", "/render", "/scad", "/vision", "/solve", "/ir", "/evidence"].map((c) => ({
      group: "命令",
      label: c,
      hint: "斜杠命令",
      run: () => window.dispatchEvent(new CustomEvent("wb-command", { detail: c })),
    })),
    ...([
      { v: "3d" as const, l: "视图:3D 视口" },
      { v: "plan" as const, l: "视图:平面" },
      { v: "prof" as const, l: "视图:剖面" },
      { v: "trace" as const, l: "视图:证据链轨迹" },
    ].map((x) => ({ group: "导航", label: x.l, hint: "切换视图", run: () => setViewMode(x.v) }))),
    { group: "导航", label: "打开设置", hint: "⌘,", run: () => handleOpenSettings("appearance") },
    { group: "导航", label: "新建工程任务", hint: "⌘K", run: () => setNewTaskOpen(true) },
    ...paletteSessions.map((s) => ({
      group: "会话",
      label: getSessionTitle(s),
      hint: "跳转会话",
      run: () => setCurrentSessionId(s.session_id),
    })),
  ]

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-background text-foreground">
      {/* 顶栏 Header */}
      <Header
        activeSessionTitle={getSessionTitle(currentSession)}
        currentPlaybook={currentSession?.playbook}
        currentMode={currentSession?.mode}
        onDisciplineChange={(newPlaybook) => {
          if (currentSession) {
            setCurrentSession({ ...currentSession, playbook: newPlaybook })
          }
          if (currentSessionId) {
            api.updateSession(currentSessionId, { playbook: newPlaybook }).then(() => {
              setRefreshTrigger((prev) => prev + 1)
            }).catch(() => {})
          }
        }}
        onModeChange={(newMode) => {
          if (currentSession) {
            setCurrentSession({ ...currentSession, mode: newMode })
          }
          if (currentSessionId) {
            api.updateSession(currentSessionId, { mode: newMode }).then(() => {
              setRefreshTrigger((prev) => prev + 1)
            }).catch(() => {})
          }
        }}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        onOpenSettings={(tab) => handleOpenSettings(tab || "appearance")}
        onOpenAudit={() => setAuditOpen(true)}
        usedTokens={ctxUsage.used}
        contextWindow={ctxUsage.ctx}
        isDark={isDark}
        onToggleTheme={toggleTheme}
        showLeftSidebar={showLeftSidebar}
        onToggleLeftSidebar={toggleLeftSidebar}
        showBottomTerminal={showBottomTerminal}
        onToggleBottomTerminal={toggleBottomTerminal}
        showRightChat={showRightChat}
        onToggleRightChat={toggleRightChat}
        chatFocusMode={chatFocusMode}
        onToggleChatFocus={toggleChatFocus}
      />

      {/* 主工作台三栏布局(ErrorBoundary 容错:子树崩溃隔离不白屏) */}
      <ErrorBoundary>
      <div className="flex flex-1 min-h-0 min-w-0 overflow-hidden">
        {/* 左侧：任务与会话列表 (支持拖拽调宽与一键折叠) */}
        <div style={{ width: sidebarWidth }} className={showLeftSidebar ? "h-full shrink-0 flex" : "hidden"}>
          <Sidebar
            currentSessionId={currentSessionId}
            onSelectSession={handleSelectSession}
            onOpenSettings={(tab) => handleOpenSettings(tab || "appearance")}
            onOpenNewTask={handleNewChat}
            onNewChat={handleNewChat}
            refreshTrigger={refreshTrigger}
            isDark={isDark}
            onToggleTheme={toggleTheme}
            width={sidebarWidth}
          />
        </div>

        {/* 左侧拖拽分割手柄 (Draggable Splitter Handle) */}
        {showLeftSidebar && (
          <div
            onMouseDown={startDraggingLeft}
            onDoubleClick={() => setSidebarWidth(260)}
            className="w-1 hover:w-1.5 bg-neutral-200/50 dark:bg-neutral-800/50 hover:bg-violet-500/80 active:bg-violet-600 transition-all cursor-col-resize z-20 select-none relative group shrink-0"
            title="按住拖拽调整左侧栏宽度，双击复位 (260px)"
          >
            <div className="absolute inset-y-0 -left-1.5 -right-1.5 cursor-col-resize" />
          </div>
        )}

        {/* 中央：3D/2D 画布视口舞台 / 证据链轨迹 / 全宽对话工坊 (图二模式) */}
        <main className="flex-1 flex flex-col min-w-0 min-h-0 bg-background relative overflow-hidden">
          {chatFocusMode ? (
            <div className="flex-1 min-h-0 min-w-0 flex flex-col overflow-hidden bg-background">
              <ChatThread
                sessionId={currentSessionId}
                sessionTitle={getSessionTitle(currentSession)}
                playbook={currentSession?.playbook || "municipal_utility"}
                irData={irData}
                onOpenSettings={handleOpenSettings}
                onUsageChange={(used, ctx) => setCtxUsage({ used, ctx })}
                onEventsChange={setSessionEvents}
                onStatusChange={setAgentStatus}
                onSessionCreated={(newId) => {
                  setCurrentSessionId(newId)
                  setRefreshTrigger((c) => c + 1)
                }}
                isExpanded={true}
                onToggleExpand={toggleChatFocus}
              />
            </div>
          ) : (
            <div className="flex-1 min-h-0 min-w-0 relative overflow-hidden">
              {viewMode === "trace" ? (
                <TraceTimeline sessionId={currentSessionId} />
              ) : (
                <CanvasViewport
                  viewMode={viewMode}
                  onViewModeChange={(m) => setViewMode(m)}
                  irData={irData}
                  isDark={isDark}
                />
              )}
            </div>
          )}

          {/* 下展开是中间的 Terminal (对标 VS Code / Cursor 底部面板范式) */}
          {showBottomTerminal && (
            <BottomTerminalPanel
              events={sessionEvents}
              sessionId={currentSessionId}
              running={agentStatus.running}
              sessionTitle={getSessionTitle(currentSession)}
              onClose={() => setShowBottomTerminal(false)}
            />
          )}
        </main>

        {/* 右侧拖拽分割手柄 (Draggable Splitter Handle) */}
        {showRightChat && !chatFocusMode && (
          <div
            onMouseDown={startDraggingRight}
            onDoubleClick={() => setChatWidth(460)}
            className="w-1 hover:w-1.5 bg-neutral-200/50 dark:bg-neutral-800/50 hover:bg-violet-500/80 active:bg-violet-600 transition-all cursor-col-resize z-20 select-none relative group shrink-0"
            title="按住拖拽调整对话栏宽度，双击复位 (460px)"
          >
            <div className="absolute inset-y-0 -left-1.5 -right-1.5 cursor-col-resize" />
          </div>
        )}

        {/* 右侧：智能体对话流与 Composer (支持拖拽调宽与一键折叠；全宽模式时隐藏以避免重复) */}
        <div style={{ width: chatWidth }} className={showRightChat && !chatFocusMode ? "h-full shrink-0 flex" : "hidden"}>
          <ChatThread
            sessionId={currentSessionId}
            sessionTitle={getSessionTitle(currentSession)}
            playbook={currentSession?.playbook || "municipal_utility"}
            irData={irData}
            onOpenSettings={handleOpenSettings}
            onUsageChange={(used, ctx) => setCtxUsage({ used, ctx })}
            onEventsChange={setSessionEvents}
            onStatusChange={setAgentStatus}
            onSessionCreated={(newId) => {
              setCurrentSessionId(newId)
              setRefreshTrigger((c) => c + 1)
            }}
            isExpanded={false}
            onToggleExpand={toggleChatFocus}
            width={chatWidth}
          />
        </div>
      </div>
      </ErrorBoundary>

      {/* 现代双栏设置弹层 (对标 Cursor / OpenDesign 范式) */}
      <SettingsDialog
        open={settingsOpen}
        onOpenChange={(open) => {
          setSettingsOpen(open)
          if (!open) {
            setSettingsModel(undefined)
          }
        }}
        initialTab={settingsTab}
        initialModelEdit={settingsModel}
        onClearInitialModelEdit={() => setSettingsModel(undefined)}
        onSelectSession={(sid) => {
          setCurrentSessionId(sid)
          setRefreshTrigger((c) => c + 1)
        }}
      />

      {/* 新建工程任务弹层 (⌘K) */}
      <NewTaskDialog
        open={newTaskOpen}
        onOpenChange={setNewTaskOpen}
        onTaskStarted={handleTaskStarted}
      />

      {/* ⌘P 全局命令面板(命令/导航/会话三组) */}
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} items={paletteItems} />

      {/* 审计日志弹层(安全操作留痕) */}
      <AuditDialog open={auditOpen} onOpenChange={setAuditOpen} />

      {/* 全局 toast 通知（sonner）：审批/上传/发送失败等反馈的唯一出口 */}
      <Toaster theme={isDark ? "dark" : "light"} />
    </div>
  )
}
