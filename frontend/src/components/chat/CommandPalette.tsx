/**
 * CommandPalette：⌘P 全局命令面板（命令/导航/会话三组，模糊搜索 + ↑↓ + 回车）。
 *
 * 命令组通过 window 事件 wb-command 派发给 ChatThread 执行；导航/会话组回调 App。
 * 对标 Claude Code / Cursor 的 ⌘P 统一入口范式。
 */
import React, { useEffect, useMemo, useRef, useState } from "react"
import { Search, Terminal, Compass, MessagesSquare } from "lucide-react"

export interface PaletteItem {
  group: string
  label: string
  hint?: string
  run: () => void
}

export const CommandPalette: React.FC<{
  open: boolean
  onClose: () => void
  items: PaletteItem[]
}> = ({ open, onClose, items }) => {
  const [q, setQ] = useState("")
  const [idx, setIdx] = useState(0)
  const inputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    if (open) {
      setQ("")
      setIdx(0)
      setTimeout(() => inputRef.current?.focus(), 30)
    }
  }, [open])

  const filtered = useMemo(
    () => items.filter((it) => (it.label + (it.hint || "")).toLowerCase().includes(q.toLowerCase())),
    [items, q]
  )

  if (!open) return null
  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center pt-[15vh] bg-black/40" onClick={onClose}>
      <div
        className="w-[520px] max-w-[90vw] rounded-xl border border-border/70 bg-popover shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-3 py-2 border-b border-border/50">
          <Search className="h-4 w-4 text-muted-foreground" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => {
              setQ(e.target.value)
              setIdx(0)
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault()
                setIdx((i) => Math.min(filtered.length - 1, i + 1))
              } else if (e.key === "ArrowUp") {
                e.preventDefault()
                setIdx((i) => Math.max(0, i - 1))
              } else if (e.key === "Enter") {
                e.preventDefault()
                filtered[idx]?.run()
                onClose()
              } else if (e.key === "Escape") onClose()
            }}
            placeholder="搜索命令 / 视图 / 会话…"
            className="flex-1 bg-transparent border-none outline-none text-sm text-foreground placeholder:text-muted-foreground/60"
          />
        </div>
        <div className="max-h-[320px] overflow-y-auto p-1">
          {filtered.map((it, i) => (
            <button
              key={`${it.group}:${it.label}`}
              type="button"
              onClick={() => {
                it.run()
                onClose()
              }}
              onMouseEnter={() => setIdx(i)}
              className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-left text-xs ${
                i === idx ? "bg-primary/10 text-primary" : "text-foreground hover:bg-muted/60"
              }`}
            >
              {it.group === "命令" ? (
                <Terminal className="h-3.5 w-3.5 shrink-0" />
              ) : it.group === "导航" ? (
                <Compass className="h-3.5 w-3.5 shrink-0" />
              ) : (
                <MessagesSquare className="h-3.5 w-3.5 shrink-0" />
              )}
              <span className="truncate flex-1">{it.label}</span>
              {it.hint && <span className="text-[10px] text-muted-foreground shrink-0">{it.hint}</span>}
            </button>
          ))}
          {filtered.length === 0 && (
            <div className="px-2 py-3 text-xs text-muted-foreground text-center">无匹配</div>
          )}
        </div>
      </div>
    </div>
  )
}
