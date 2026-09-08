import React, { useEffect, useMemo, useRef, useState } from "react"
import { Terminal as TermIcon, ChevronDown, ChevronUp, Trash2 } from "lucide-react"

interface TermLine {
  ts: string
  kind: "tool" | "host" | "solver" | "approval" | "sys"
  text: string
}

interface Props {
  events: any[]
}

/** Live Runner Terminal(Devin/DSH 范式):迷你终端抽屉,实时流式显示调度/宿主/求解器日志 */
export const TerminalDrawer: React.FC<Props> = ({ events }) => {
  const [open, setOpen] = useState(false)
  const [clearedAt, setClearedAt] = useState(0)
  const scrollRef = useRef<HTMLDivElement | null>(null)

  const lines = useMemo<TermLine[]>(() => {
    const out: TermLine[] = []
    events.forEach((ev, idx) => {
      if (idx < clearedAt) return
      const ts = String(ev.timestamp || "").slice(11, 19)
      const p = ev.payload || {}
      if (ev.type === "tool_call") {
        const tool = p.tool || p.name || "?"
        const status = p.status || (p.error ? "error" : "call")
        const detail =
          typeof p.result === "string"
            ? p.result.slice(0, 160)
            : p.output
            ? String(p.output).slice(0, 160)
            : ""
        out.push({
          ts,
          kind: tool === "bash" || tool === "mcp_call" ? "host" : "tool",
          text: `${tool} -> ${status}${detail ? " · " + detail : ""}`,
        })
      } else if (ev.type === "custom") {
        const ct = p.customType || p.custom_type || ""
        if (ct === "score") {
          const d = p.data || p
          out.push({ ts, kind: "solver", text: `视觉评审 iter=${d.iter ?? "?"} score=${d.overall ?? d.score ?? "?"}` })
        } else if (ct === "steer_requested") {
          out.push({ ts, kind: "sys", text: `[steer] ${String(p.instruction || "").slice(0, 120)}` })
        } else if (ct === "approval_requested" || ct === "approval_decided") {
          out.push({ ts, kind: "approval", text: `${ct} · ${p.operation || p.approval_id || ""}` })
        } else if (ct === "delivery_receipt" || ct === "artifact_committed") {
          out.push({ ts, kind: "sys", text: `交付 ${ct}` })
        }
      }
    })
    return out
  }, [events, clearedAt])

  useEffect(() => {
    if (open && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [lines, open])

  const kindColor: Record<TermLine["kind"], string> = {
    tool: "text-sky-400",
    host: "text-amber-400",
    solver: "text-emerald-400",
    approval: "text-violet-400",
    sys: "text-zinc-400",
  }

  return (
    <div className="shrink-0 border-t border-border/70 bg-background/95">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-1.5 px-3 py-1 text-[10px] font-mono text-muted-foreground hover:text-foreground"
        title="Live Runner Terminal:调度/宿主/求解器实时日志"
      >
        <TermIcon className="h-3 w-3" />
        <span>Runner Terminal · {lines.length} 行</span>
        {open ? <ChevronDown className="h-3 w-3 ml-auto" /> : <ChevronUp className="h-3 w-3 ml-auto" />}
      </button>
      {open && (
        <div className="relative">
          <div
            ref={scrollRef}
            className="h-36 overflow-y-auto px-3 pb-2 font-mono text-[10.5px] leading-relaxed bg-black/90"
          >
            {lines.length === 0 ? (
              <div className="text-zinc-500 pt-2">
                等待运行事件…（工具调用 / 宿主执行 / 求解器收敛将实时输出到这里）
              </div>
            ) : (
              lines.map((l, i) => (
                <div key={i} className="flex gap-2">
                  <span className="text-zinc-600 shrink-0">{l.ts}</span>
                  <span className={kindColor[l.kind] + " break-all"}>{l.text}</span>
                </div>
              ))
            )}
          </div>
          <button
            type="button"
            onClick={() => setClearedAt(events.length)}
            className="absolute top-1 right-2 p-1 rounded text-zinc-500 hover:text-zinc-200"
            title="清空终端"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        </div>
      )}
    </div>
  )
}