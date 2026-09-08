/**
 * PlanSteps：spec/plan 步骤展示 + 做完勾选 + 可编辑 + Given-When-Then 验收（SDD 范式）。
 *
 * 展示态：勾选语义三级——绿勾=完成且验收过 / 黄勾=完成但验收未过 / 空圈=待做；进度 n/m + 进度条。
 * 编辑态：步骤标题可改/增删，每步骤可填 Given-When-Then 验收标准；确认后作为 spec 传回(onConfirm)。
 * 数据源：agent 回复的 markdown checklist（`- [ ]`/`- [x]`）自动解析。
 */
import React, { useState } from "react"
import { CheckCircle2, Circle, ListChecks, Pencil, Plus, Trash2, Check, X, AlertCircle } from "lucide-react"

export interface PlanAcceptance {
  given: string
  when: string
  then: string
}
export interface PlanStep {
  title: string
  done: boolean
  verified?: boolean
  acceptance?: PlanAcceptance
}

/** 从文本解析 markdown checklist 为步骤；不足 2 步视为非计划文本返回 null。 */
export function parsePlanSteps(text: string): PlanStep[] | null {
  const steps: PlanStep[] = []
  for (const ln of String(text || "").split("\n")) {
    const m = ln.match(/^\s*[-*]\s+\[( |x|X)\]\s+(.+)$/)
    if (m) steps.push({ done: m[1].toLowerCase() === "x", title: m[2].trim() })
  }
  return steps.length >= 2 ? steps : null
}

const EMPTY_ACC: PlanAcceptance = { given: "", when: "", then: "" }

export const PlanSteps: React.FC<{ steps: PlanStep[]; onConfirm?: (steps: PlanStep[]) => void }> = ({
  steps,
  onConfirm,
}) => {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<PlanStep[]>(steps)
  const [showAcc, setShowAcc] = useState<Record<number, boolean>>({})
  const done = steps.filter((s) => s.done).length
  const total = Math.max(1, steps.length)

  const startEdit = () => {
    setDraft(steps.map((s) => ({ ...s, acceptance: s.acceptance || { ...EMPTY_ACC } })))
    setEditing(true)
  }
  const confirmEdit = () => {
    setEditing(false)
    onConfirm?.(draft)
  }

  if (editing) {
    return (
      <div className="rounded-xl border border-primary/40 bg-card/60 p-2.5 space-y-2 text-xs shadow-xs">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-1.5 font-medium text-foreground">
            <Pencil className="h-3.5 w-3.5 text-primary" />
            编辑执行计划（Spec）
          </span>
          <span className="flex gap-1">
            <button
              type="button"
              onClick={confirmEdit}
              className="flex items-center gap-1 px-2 py-1 rounded bg-primary text-primary-foreground text-[10px]"
            >
              <Check className="h-3 w-3" /> 确认
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="flex items-center gap-1 px-2 py-1 rounded border border-border/60 text-[10px] text-muted-foreground"
            >
              <X className="h-3 w-3" /> 取消
            </button>
          </span>
        </div>
        {draft.map((s, i) => (
          <div key={i} className="space-y-1 rounded-lg border border-border/50 bg-background/40 p-1.5">
            <div className="flex items-center gap-1">
              <input
                value={s.title}
                onChange={(e) => setDraft((d) => d.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))}
                className="flex-1 bg-transparent border-none outline-none text-xs text-foreground"
              />
              <button
                type="button"
                title="验收标准(Given-When-Then)"
                onClick={() => setShowAcc((p) => ({ ...p, [i]: !p[i] }))}
                className="p-1 rounded text-muted-foreground hover:text-primary"
              >
                <ListChecks className="h-3 w-3" />
              </button>
              <button
                type="button"
                title="删除步骤"
                onClick={() => setDraft((d) => d.filter((_, j) => j !== i))}
                className="p-1 rounded text-muted-foreground hover:text-rose-500"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
            {showAcc[i] && (
              <div className="space-y-1 pl-1 border-l-2 border-primary/30">
                {(["given", "when", "then"] as const).map((k) => (
                  <div key={k} className="flex items-center gap-1">
                    <span className="text-[10px] font-mono text-muted-foreground w-10 shrink-0">{k}</span>
                    <input
                      value={(s.acceptance || EMPTY_ACC)[k]}
                      onChange={(e) =>
                        setDraft((d) =>
                          d.map((x, j) =>
                            j === i
                              ? { ...x, acceptance: { ...(x.acceptance || EMPTY_ACC), [k]: e.target.value } }
                              : x
                          )
                        )
                      }
                      placeholder={
                        k === "given" ? "前提(如 管径 DN300)" : k === "when" ? "动作(如 重力求解)" : "验收(如 覆土≥0.7)"
                      }
                      className="flex-1 bg-transparent border-none outline-none text-[10px] text-foreground placeholder:text-muted-foreground/50"
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
        <button
          type="button"
          onClick={() => setDraft((d) => [...d, { title: "", done: false, acceptance: { ...EMPTY_ACC } }])}
          className="flex items-center gap-1 text-[10px] text-primary hover:underline"
        >
          <Plus className="h-3 w-3" /> 添加步骤
        </button>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-border/60 bg-card/60 p-2.5 space-y-1.5 text-xs shadow-xs">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 font-medium text-foreground">
          <ListChecks className="h-3.5 w-3.5 text-primary" />
          执行计划
        </span>
        <span className="flex items-center gap-1.5">
          <span className="font-mono text-[10px] text-muted-foreground">
            进度 {done}/{steps.length}
          </span>
          <button
            type="button"
            title="编辑计划/验收标准"
            onClick={startEdit}
            className="p-1 rounded text-muted-foreground hover:text-primary hover:bg-primary/10"
          >
            <Pencil className="h-3 w-3" />
          </button>
        </span>
      </div>
      <div className="h-1 rounded-full bg-muted/60 overflow-hidden">
        <div
          className="h-full bg-primary/70 rounded-full transition-all"
          style={{ width: `${(done / total) * 100}%` }}
        />
      </div>
      <ul className="space-y-1">
        {steps.map((s, i) => (
          <li key={i} className="flex items-start gap-1.5">
            {s.done && s.verified !== false ? (
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0 mt-0.5" />
            ) : s.done ? (
              <AlertCircle className="h-3.5 w-3.5 text-amber-500 shrink-0 mt-0.5" />
            ) : (
              <Circle className="h-3.5 w-3.5 text-muted-foreground/60 shrink-0 mt-0.5" />
            )}
            <span className={s.done ? "text-muted-foreground line-through" : "text-foreground"}>{s.title}</span>
            {s.acceptance && s.acceptance.then && (
              <span
                className="text-[10px] font-mono text-muted-foreground/70 truncate"
                title={`Given ${s.acceptance.given} / When ${s.acceptance.when} / Then ${s.acceptance.then}`}
              >
                · 验收: {s.acceptance.then}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
