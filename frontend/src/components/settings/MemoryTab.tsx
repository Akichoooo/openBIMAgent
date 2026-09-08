import React, { useEffect, useState } from "react"
import { api } from "@/services/api"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import { Brain, Plus, Search, Trash2, CheckCircle2, FileText, AlertCircle } from "lucide-react"

import { MemoryEntry } from "@/services/api"

export const MemoryTab: React.FC = () => {
  const [items, setItems] = useState<MemoryEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [searchTerm, setSearchTerm] = useState("")
  const [newMemory, setNewMemory] = useState("")
  const [targetFile, setTargetFile] = useState<"user" | "memory">("user")
  const [confirmWrite, setConfirmWrite] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [deletingKey, setDeletingKey] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(null)

  const loadMemory = async () => {
    setLoading(true)
    try {
      const res = await api.getMemory()
      setItems(res.items || [])
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadMemory()
  }, [])

  const handleAddMemory = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newMemory.trim()) return
    setSubmitting(true)
    setMsg(null)
    try {
      await api.recordMemory(newMemory.trim(), confirmWrite, targetFile)
      setNewMemory("")
      setMsg({ type: "success", text: "已成功记录长期记忆条目" })
      await loadMemory()
    } catch (e: any) {
      setMsg({ type: "error", text: e.message || "写入失败" })
    } finally {
      setSubmitting(false)
    }
  }

  const handleDeleteMemory = async (entry: MemoryEntry) => {
    if (!entry.line) return
    const key = `${entry.source}-${entry.line}`
    setDeletingKey(key)
    try {
      await api.deleteMemory(entry.source, entry.line, confirmWrite)
      setMsg({ type: "success", text: `已删除行 #${entry.line}` })
      await loadMemory()
    } catch (e: any) {
      setMsg({ type: "error", text: e.message || "删除失败" })
    } finally {
      setDeletingKey(null)
    }
  }

  const filteredItems = items.filter((item) => {
    const kw = (searchTerm || "").trim().toLowerCase()
    if (!kw) return true
    return (item.text || "").toLowerCase().includes(kw)
  })

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-base font-medium">长期记忆审计 (Long-term Memory)</h3>
        <p className="text-xs text-muted-foreground mt-1">
          openBIMAgent 会跨会话持久化工程设计偏好、规范约束与用户指令习惯（MEMORY.md / USER.md）。
        </p>
      </div>

      {/* 新增记录卡片 */}
      <div className="rounded-xl border border-border/70 p-4 bg-muted/20 space-y-3">
        <div className="flex items-center space-x-2 text-sm font-medium">
          <Brain className="h-4 w-4 text-primary" />
          <span>录入新的长期工程经验或偏好</span>
        </div>

        <form onSubmit={handleAddMemory} className="space-y-3">
          <div className="flex gap-2">
            <select
              value={targetFile}
              onChange={(e) => setTargetFile(e.target.value as any)}
              className="h-9 px-2.5 rounded-md border border-border bg-background text-xs"
            >
              <option value="user">USER.md (用户偏好)</option>
              <option value="memory">MEMORY.md (工程知识)</option>
            </select>
            <Input
              value={newMemory}
              onChange={(e) => setNewMemory(e.target.value)}
              placeholder="例如：本项目所有重力雨水管均默认采用 HDPE 缠绕结构壁管，坡度不得小于 0.003..."
              className="text-xs h-9 flex-1"
            />
            <Button type="submit" size="sm" disabled={submitting || !newMemory.trim()} className="h-9 px-4 text-xs shrink-0">
              <Plus className="h-3.5 w-3.5 mr-1" />
              写入记忆
            </Button>
          </div>

          <div className="flex items-center justify-between text-xs text-muted-foreground pt-1">
            <div className="flex items-center space-x-2">
              <Switch checked={confirmWrite} onCheckedChange={setConfirmWrite} id="confirm-memory" />
              <label htmlFor="confirm-memory" className="cursor-pointer select-none">
                写入前进行一致性与冲突检测 (Confirm Write)
              </label>
            </div>
            {msg && (
              <span
                className={`text-xs flex items-center gap-1 ${
                  msg.type === "success" ? "text-emerald-500" : "text-rose-500"
                }`}
              >
                {msg.type === "success" ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertCircle className="h-3.5 w-3.5" />}
                {msg.text}
              </span>
            )}
          </div>
        </form>
      </div>

      {/* 搜索与记忆清单 */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <span className="text-sm font-medium">已生效的记忆条目</span>
            <Badge variant="secondary" className="text-[10px] font-mono px-1.5 py-0">
              {items.length}
            </Badge>
          </div>
          <div className="relative w-56">
            <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
            <Input
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="搜索记忆关键字..."
              className="pl-8 h-8 text-xs"
            />
          </div>
        </div>

        {loading ? (
          <div className="py-8 text-center text-xs text-muted-foreground">正在加载记忆空间...</div>
        ) : filteredItems.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border/70 p-6 text-center text-xs text-muted-foreground bg-muted/10">
            <FileText className="h-6 w-6 mx-auto mb-2 text-muted-foreground/40" />
            <p>暂无匹配的长期记忆条目</p>
          </div>
        ) : (
          <div className="space-y-2 max-h-[380px] overflow-y-auto pr-1">
            {filteredItems.map((item, idx) => (
              <div
                key={`${item.source}-${item.line}-${idx}`}
                className="group flex items-start justify-between p-3 rounded-lg border border-border/60 bg-card hover:border-border hover:bg-muted/30 transition-all text-xs"
              >
                <div className="flex items-start space-x-2.5 flex-1 min-w-0 pr-2">
                  <div className="mt-1 w-1.5 h-1.5 rounded-full bg-primary/70 shrink-0" />
                  <div className="space-y-1 min-w-0 flex-1">
                    <p className="text-foreground leading-relaxed break-words">{item.text}</p>
                    <div className="flex items-center gap-1.5">
                      <Badge variant="outline" className="text-[9px] px-1 py-0 text-muted-foreground">
                        {item.source === "user" ? "USER.md" : "MEMORY.md"}
                      </Badge>
                      {item.line > 0 && (
                        <span className="text-[10px] text-muted-foreground/70 font-mono">
                          L{item.line}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                {item.line > 0 && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => handleDeleteMemory(item)}
                    disabled={deletingKey === `${item.source}-${item.line}`}
                    className="h-7 w-7 p-0 text-muted-foreground hover:text-rose-500 shrink-0 opacity-80 hover:opacity-100"
                    title="删除此记忆"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
