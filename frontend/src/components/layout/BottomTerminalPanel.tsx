import React, { useEffect, useMemo, useRef, useState } from "react"
import {
  Terminal as TermIcon,
  Trash2,
  X,
  Maximize2,
  Minimize2,
  FileCode,
  Send,
  HelpCircle,
  Play,
  CornerDownLeft,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { api } from "@/services/api"
import { toast } from "sonner"

interface TermLine {
  ts: string
  kind: "tool" | "host" | "solver" | "approval" | "sys" | "error"
  text: string
}

interface TerminalEntry {
  ts: string
  type: "input" | "stdout" | "stderr" | "info" | "success"
  text: string
}

interface BottomTerminalPanelProps {
  events: any[]
  onClose: () => void
  sessionId?: string | null
  running?: boolean
  sessionTitle?: string
}

/**
 * VS Code 范式中央底部控制台 (双 Tab：交互式 Terminal + 实时 Output 流)
 * 支持交互式命令输入、斜杠命令、运行中实时插话纠偏 (Steering)、历史翻阅与视窗最大化
 */
export const BottomTerminalPanel: React.FC<BottomTerminalPanelProps> = ({
  events,
  onClose,
  sessionId,
  running = false,
  sessionTitle,
}) => {
  const [activeTab, setActiveTab] = useState<"terminal" | "output">("terminal")
  const [isMaximized, setIsMaximized] = useState(false)
  const [clearedAt, setClearedAt] = useState(0)
  const [cmdInput, setCmdInput] = useState("")

  // 命令历史导航 (↑ / ↓ 翻阅)
  const [history, setHistory] = useState<string[]>([])
  const [historyIdx, setHistoryIdx] = useState<number>(-1)

  // 终端命令行输出记录
  const [terminalEntries, setTerminalEntries] = useState<TerminalEntry[]>([
    {
      ts: new Date().toTimeString().slice(0, 8),
      type: "info",
      text: "openBIMAgent Interactive Terminal v1.2 [Ready]",
    },
    {
      ts: new Date().toTimeString().slice(0, 8),
      type: "info",
      text: "输入 'help' 查看内置工程指令，或输入任意指令进行实时交互与决策纠偏。",
    },
  ])

  const terminalScrollRef = useRef<HTMLDivElement | null>(null)
  const outputScrollRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)

  // 1. Output 日志行解析 (智能体底层透明化运行流)
  const outputLines = useMemo<TermLine[]>(() => {
    const out: TermLine[] = []
    events.forEach((ev, idx) => {
      if (idx < clearedAt) return
      const ts =
        String(ev.timestamp || ev.created_at || "").slice(11, 19) ||
        new Date().toTimeString().slice(0, 8)
      const p = ev.payload || {}
      if (ev.type === "tool_call") {
        const tool = p.tool || p.name || "?"
        const status = p.status || (p.error ? "error" : "call")
        const detail =
          typeof p.result === "string"
            ? p.result.slice(0, 240)
            : p.output
            ? String(p.output).slice(0, 240)
            : ""
        out.push({
          ts,
          kind: tool === "bash" || tool === "mcp_call" ? "host" : p.error ? "error" : "tool",
          text: `[工具] ${tool} ➔ ${status}${detail ? " · " + detail : ""}`,
        })
      } else if (ev.type === "custom") {
        const ct = p.customType || p.custom_type || ""
        if (ct === "score") {
          const d = p.data || p
          out.push({
            ts,
            kind: "solver",
            text: `[求解] 视觉评审迭代 iter=${d.iter ?? "?"} 得分=${d.overall ?? d.score ?? "?"}`,
          })
        } else if (ct === "steer_requested") {
          out.push({ ts, kind: "sys", text: `[插话纠偏] ${String(p.instruction || "").slice(0, 160)}` })
        } else if (ct === "approval_requested" || ct === "approval_decided") {
          out.push({
            ts,
            kind: "approval",
            text: `[审批门] ${ct === "approval_requested" ? "发起审批请求" : "审批决议已提交"} · ${
              p.operation || p.approval_id || ""
            }`,
          })
        } else if (ct === "delivery_receipt" || ct === "artifact_committed") {
          out.push({ ts, kind: "sys", text: `[交付] 构件工件落盘 ${ct}` })
        } else {
          out.push({ ts, kind: "sys", text: `[事件] ${ct}: ${JSON.stringify(p).slice(0, 160)}` })
        }
      } else if (ev.type === "error") {
        out.push({ ts, kind: "error", text: `[异常] ${p.message || JSON.stringify(p)}` })
      }
    })
    return out
  }, [events, clearedAt])

  // 自动滚屏
  useEffect(() => {
    if (activeTab === "terminal" && terminalScrollRef.current) {
      terminalScrollRef.current.scrollTop = terminalScrollRef.current.scrollHeight
    } else if (activeTab === "output" && outputScrollRef.current) {
      outputScrollRef.current.scrollTop = outputScrollRef.current.scrollHeight
    }
  }, [terminalEntries, outputLines, activeTab])

  // 2. 命令行执行处理
  const handleExecute = async () => {
    const cmd = cmdInput.trim()
    if (!cmd) return

    const now = new Date().toTimeString().slice(0, 8)
    // 记录用户输入
    setTerminalEntries((prev) => [...prev, { ts: now, type: "input", text: cmd }])
    setHistory((prev) => [...prev, cmd])
    setHistoryIdx(-1)
    setCmdInput("")

    // 内置命令判断
    const lower = cmd.toLowerCase()
    if (lower === "clear" || lower === "cls") {
      setTerminalEntries([])
      return
    }

    if (lower === "help") {
      setTerminalEntries((prev) => [
        ...prev,
        {
          ts: now,
          type: "info",
          text: `openBIMAgent 可用命令列表:
  /solve <需求描述>   - 触发市政管网水力放样与碰撞自愈求解
  /scad              - 运行 OpenSCAD 结构快检环
  /vision            - 触发六维视觉评审 (critic_render)
  /ir                - 查看并审查 CompiledUtilityIR 中间表示
  /evidence          - 切换到证据链轨迹图
  /reflect           - 反思当前会话教训并写入长期记忆
  /steer <纠偏文本>   - 运行中即时插话纠偏 (Devin 范式 mid-run steering)
  clear / cls        - 清空终端屏幕
  help               - 显示本帮助信息
提示: 后台任务运行中输入任意文本将自动作为【纠偏指令】注入决策链。`,
        },
      ])
      return
    }

    // 若当前正在运行：自动作为即时插话纠偏 (Mid-run Steering) 注入
    if (running && sessionId) {
      try {
        const steerText = cmd.startsWith("/steer ") ? cmd.slice(7).trim() : cmd
        const res = await api.steerRun(sessionId, steerText)
        setTerminalEntries((prev) => [
          ...prev,
          {
            ts: now,
            type: "success",
            text: `[纠偏注入成功] 已注入运行决策链（当前待决纠偏: ${res.pending} 条），智能体在下一个审批门或工具执行处将消费此指令`,
          },
        ])
        toast.success("纠偏指令已送达智能体")
      } catch (e: any) {
        setTerminalEntries((prev) => [
          ...prev,
          { ts: now, type: "stderr", text: `[纠偏注入失败] ${e.message}` },
        ])
      }
      return
    }

    // 斜杠命令分发
    if (cmd.startsWith("/")) {
      window.dispatchEvent(new CustomEvent("wb-command", { detail: cmd }))
      setTerminalEntries((prev) => [
        ...prev,
        { ts: now, type: "stdout", text: `[系统] 已触发斜杠命令: ${cmd}` },
      ])
      return
    }

    // 常规自然语言或任务指令：作为工程任务启动
    if (cmd) {
      try {
        setTerminalEntries((prev) => [
          ...prev,
          { ts: now, type: "stdout", text: `[调度] 正在向智能体下发任务: "${cmd}" ...` },
        ])
        await api.startRun(cmd, "municipal_utility", "agent")
        setTerminalEntries((prev) => [
          ...prev,
          { ts: now, type: "success", text: `[调度成功] 任务已启动，可在上方视口查看实时几何渲染，或切换至【输出】选项卡查看工具日志` },
        ])
      } catch (e: any) {
        setTerminalEntries((prev) => [
          ...prev,
          { ts: now, type: "stderr", text: `[启动失败] ${e.message}` },
        ])
      }
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault()
      handleExecute()
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      if (history.length === 0) return
      const nextIdx = historyIdx === -1 ? history.length - 1 : Math.max(0, historyIdx - 1)
      setHistoryIdx(nextIdx)
      setCmdInput(history[nextIdx] || "")
    } else if (e.key === "ArrowDown") {
      e.preventDefault()
      if (historyIdx === -1) return
      const nextIdx = historyIdx + 1
      if (nextIdx >= history.length) {
        setHistoryIdx(-1)
        setCmdInput("")
      } else {
        setHistoryIdx(nextIdx)
        setCmdInput(history[nextIdx] || "")
      }
    }
  }

  const kindBadge: Record<TermLine["kind"], { label: string; cls: string }> = {
    tool: { label: "TOOL", cls: "bg-sky-500/15 text-sky-400 border border-sky-500/30" },
    host: { label: "HOST", cls: "bg-amber-500/15 text-amber-400 border border-amber-500/30" },
    solver: { label: "SOLVE", cls: "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30" },
    approval: { label: "GATE", cls: "bg-violet-500/15 text-violet-400 border border-violet-500/30" },
    sys: { label: "SYS", cls: "bg-zinc-500/15 text-zinc-400 border border-zinc-500/30" },
    error: { label: "ERR", cls: "bg-rose-500/15 text-rose-400 border border-rose-500/30" },
  }

  return (
    <div
      className={`w-full shrink-0 border-t border-border/80 bg-zinc-950 text-zinc-100 flex flex-col select-none font-mono shadow-inner z-20 transition-all duration-200 ${
        isMaximized ? "h-[420px]" : "h-60"
      }`}
    >
      {/* 1. 顶栏 Header: VS Code 经典 Tab 导航与右侧操作按钮 */}
      <div className="h-8 px-2.5 border-b border-zinc-800 bg-zinc-900/90 flex items-center justify-between shrink-0">
        {/* 左侧 Tabs 切换 */}
        <div className="flex items-center space-x-1">
          <button
            type="button"
            onClick={() => setActiveTab("terminal")}
            className={`flex items-center gap-1.5 px-2.5 py-1 text-xs rounded transition-all font-sans font-medium ${
              activeTab === "terminal"
                ? "bg-zinc-800 text-white shadow-xs"
                : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50"
            }`}
          >
            <TermIcon className="h-3 w-3 text-emerald-400" />
            <span>终端 (Terminal)</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("output")}
            className={`flex items-center gap-1.5 px-2.5 py-1 text-xs rounded transition-all font-sans font-medium ${
              activeTab === "output"
                ? "bg-zinc-800 text-white shadow-xs"
                : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50"
            }`}
          >
            <FileCode className="h-3 w-3 text-sky-400" />
            <span>输出 (Output)</span>
            <span className="px-1 py-0.2 rounded text-[10px] bg-zinc-800 text-zinc-400 font-mono">
              {outputLines.length}
            </span>
          </button>

          {running && (
            <span className="ml-2 px-1.5 py-0.2 rounded text-[10px] bg-amber-500/15 text-amber-400 border border-amber-500/30 flex items-center gap-1 font-sans">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse" />
              任务运行中 · 支持实时纠偏
            </span>
          )}
        </div>

        {/* 右侧操作按钮 */}
        <div className="flex items-center space-x-1 shrink-0">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              if (activeTab === "terminal") setTerminalEntries([])
              else setClearedAt(events.length)
            }}
            className="h-6 px-2 text-[11px] text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 gap-1 rounded"
            title="清空当前控制台内容 (clear)"
          >
            <Trash2 className="h-3 w-3" />
            <span className="hidden md:inline font-sans">清空</span>
          </Button>

          <Button
            variant="ghost"
            size="sm"
            onClick={() => setIsMaximized(!isMaximized)}
            className="h-6 w-6 p-0 text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 rounded"
            title={isMaximized ? "还原面板高度" : "最大化面板高度"}
          >
            {isMaximized ? <Minimize2 className="h-3 w-3" /> : <Maximize2 className="h-3 w-3" />}
          </Button>

          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            className="h-6 w-6 p-0 text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 rounded"
            title="收起底部面板"
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* 2. 主体区：Tab 1 - 交互式 Terminal */}
      {activeTab === "terminal" && (
        <div className="flex-1 flex flex-col min-h-0 bg-black/95">
          {/* 终端历史输出记录 */}
          <div
            ref={terminalScrollRef}
            className="flex-1 overflow-y-auto p-3 font-mono text-[11.5px] leading-relaxed space-y-1 select-text"
          >
            {terminalEntries.map((e, idx) => {
              if (e.type === "input") {
                return (
                  <div key={idx} className="flex items-center gap-2 text-zinc-200 pt-1">
                    <span className="text-emerald-400 font-bold select-none text-[10px]">
                      openbim &gt;
                    </span>
                    <span className="font-semibold text-white">{e.text}</span>
                  </div>
                )
              }
              if (e.type === "info") {
                return (
                  <div key={idx} className="text-zinc-500 whitespace-pre-wrap leading-relaxed py-0.5">
                    {e.text}
                  </div>
                )
              }
              if (e.type === "success") {
                return (
                  <div key={idx} className="text-emerald-400 whitespace-pre-wrap leading-relaxed">
                    {e.text}
                  </div>
                )
              }
              if (e.type === "stderr") {
                return (
                  <div key={idx} className="text-rose-400 whitespace-pre-wrap leading-relaxed">
                    {e.text}
                  </div>
                )
              }
              return (
                <div key={idx} className="text-zinc-300 whitespace-pre-wrap leading-relaxed">
                  {e.text}
                </div>
              )
            })}
          </div>

          {/* 命令行输入提示符 (带光标与回车执行) */}
          <div className="h-9 px-3 border-t border-zinc-800/80 bg-zinc-950 flex items-center gap-2 shrink-0">
            <div className="flex items-center gap-1 text-[11px] font-mono shrink-0 select-none">
              <span className="text-emerald-400 font-bold">openbim</span>
              <span className="text-zinc-500 text-[10px]">
                {running ? "[纠偏]" : "[就绪]"}
              </span>
              <span className="text-sky-400 font-bold">&gt;</span>
            </div>

            <input
              ref={inputRef}
              type="text"
              value={cmdInput}
              onChange={(e) => setCmdInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                running
                  ? "任务运行中：输入指令按回车直接注入智能体进行即时纠偏 (Steer)..."
                  : "输入工程指令、斜杠命令 (如 /solve, /scad, /vision, help) 或按回车执行..."
              }
              className="flex-1 bg-transparent border-none outline-none font-mono text-[11.5px] text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:ring-0 p-0"
              autoFocus
            />

            <Button
              variant="ghost"
              size="sm"
              disabled={!cmdInput.trim()}
              onClick={handleExecute}
              className="h-6 px-2 text-[10px] text-zinc-400 hover:text-white hover:bg-zinc-800 gap-1 rounded font-sans"
              title="回车执行"
            >
              <CornerDownLeft className="h-3 w-3" />
              <span>执行</span>
            </Button>
          </div>
        </div>
      )}

      {/* 3. 主体区：Tab 2 - 只读透明化 Output 流 */}
      {activeTab === "output" && (
        <div
          ref={outputScrollRef}
          className="flex-1 overflow-y-auto p-3 font-mono text-[11px] leading-relaxed space-y-1 bg-black/95 select-text"
        >
          {outputLines.length === 0 ? (
            <div className="py-12 text-center text-zinc-500 font-sans text-xs space-y-1">
              <p>等待运行事件…</p>
              <p className="text-[11px] text-zinc-600">
                智能体调用求解器、OpenSCAD 快检、解析 IFC 或进入人机审批门时将实时流式输出到此处
              </p>
            </div>
          ) : (
            outputLines.map((l, i) => {
              const badge = kindBadge[l.kind] || kindBadge.sys
              return (
                <div
                  key={i}
                  className="flex items-start gap-2.5 py-0.5 group hover:bg-white/[0.03] px-1 rounded"
                >
                  <span className="text-zinc-600 shrink-0 select-none text-[10px] pt-0.5">
                    {l.ts}
                  </span>
                  <span
                    className={`text-[9px] px-1 py-0 rounded font-semibold shrink-0 select-none ${badge.cls}`}
                  >
                    {badge.label}
                  </span>
                  <span className="text-zinc-300 break-all leading-relaxed font-mono flex-1">
                    {l.text}
                  </span>
                </div>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}
