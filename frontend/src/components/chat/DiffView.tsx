/**
 * DiffView：IR/JSON 字段级 diff 审查（模仿 Cursor inline diff 逐块 accept/reject）。
 *
 * 对比 old/new 两个对象，递归渲染 added(+)/removed(-)/changed(~) 行；
 * 每变更块可 accept(用新值)/reject(留旧值)，供 HITL 审查模型修改。
 */
import React, { useState } from "react"
import { Check, X } from "lucide-react"

export interface DiffEntry {
  path: string
  kind: "added" | "removed" | "changed"
  oldV?: unknown
  newV?: unknown
}

/** 递归字段级 diff；对象向下钻，数组/标量整体比较。 */
export function jsonDiff(oldObj: any, newObj: any, prefix = ""): DiffEntry[] {
  const out: DiffEntry[] = []
  const o = oldObj || {}
  const n = newObj || {}
  const keys = new Set([...Object.keys(o), ...Object.keys(n)])
  for (const k of keys) {
    const path = prefix ? `${prefix}.${k}` : k
    const ov = o[k]
    const nv = n[k]
    if (!(k in o)) out.push({ path, kind: "added", newV: nv })
    else if (!(k in n)) out.push({ path, kind: "removed", oldV: ov })
    else if (typeof ov === "object" && typeof nv === "object" && ov && nv && !Array.isArray(ov) && !Array.isArray(nv))
      out.push(...jsonDiff(ov, nv, path))
    else if (JSON.stringify(ov) !== JSON.stringify(nv)) out.push({ path, kind: "changed", oldV: ov, newV: nv })
  }
  return out
}

export const DiffView: React.FC<{ oldObj: any; newObj: any }> = ({ oldObj, newObj }) => {
  const entries = jsonDiff(oldObj, newObj)
  const [decisions, setDecisions] = useState<Record<string, "accept" | "reject">>({})
  if (entries.length === 0) {
    return <div className="text-[11px] text-muted-foreground py-2">无差异（IR 未修改）</div>
  }
  return (
    <div className="space-y-1 text-[11px] font-mono">
      {entries.slice(0, 50).map((e) => (
        <div
          key={e.path}
          className={`flex items-center justify-between gap-2 rounded px-1.5 py-1 ${
            e.kind === "added"
              ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
              : e.kind === "removed"
                ? "bg-rose-500/10 text-rose-600 dark:text-rose-400"
                : "bg-amber-500/10 text-amber-600 dark:text-amber-400"
          }`}
        >
          <span className="truncate flex-1">
            {e.kind === "added" ? "+ " : e.kind === "removed" ? "- " : "~ "}
            {e.path}
            {e.kind === "changed" && (
              <span className="text-muted-foreground">
                {" "}
                : {String(e.oldV)} → {String(e.newV)}
              </span>
            )}
            {e.kind === "added" && <span> : {String(e.newV)}</span>}
            {e.kind === "removed" && <span> : {String(e.oldV)}</span>}
          </span>
          <span className="flex gap-0.5 shrink-0">
            <button
              type="button"
              title="接受新值"
              onClick={() => setDecisions((d) => ({ ...d, [e.path]: "accept" }))}
              className={`p-0.5 rounded ${decisions[e.path] === "accept" ? "text-emerald-500 bg-emerald-500/20" : "text-muted-foreground hover:text-emerald-500"}`}
            >
              <Check className="h-3 w-3" />
            </button>
            <button
              type="button"
              title="拒绝(留旧值)"
              onClick={() => setDecisions((d) => ({ ...d, [e.path]: "reject" }))}
              className={`p-0.5 rounded ${decisions[e.path] === "reject" ? "text-rose-500 bg-rose-500/20" : "text-muted-foreground hover:text-rose-500"}`}
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        </div>
      ))}
      {entries.length > 50 && (
        <div className="text-muted-foreground">… 其余 {entries.length - 50} 处差异省略</div>
      )}
    </div>
  )
}
