import React, { useEffect, useState } from "react"
import { api, SessionItem, ArchiveRecord } from "@/services/api"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import {
  Archive,
  ArchiveRestore,
  ExternalLink,
  Trash2,
  Package,
  Search,
  FileCheck,
  FileCode,
  Calendar,
  AlertTriangle,
} from "lucide-react"

interface ArchiveTabProps {
  onSelectSession?: (sessionId: string) => void
  onCloseSettings?: () => void
}

export const ArchiveTab: React.FC<ArchiveTabProps> = ({ onSelectSession, onCloseSettings }) => {
  const [subTab, setSubTab] = useState<"sessions" | "deliverables">("sessions")

  // Archived Sessions state
  const [sessions, setSessions] = useState<SessionItem[]>([])
  const [loadingSessions, setLoadingSessions] = useState(false)
  const [searchSession, setSearchSession] = useState("")

  // Deliverables Archive state
  const [deliverables, setDeliverables] = useState<ArchiveRecord[]>([])
  const [loadingDeliverables, setLoadingDeliverables] = useState(false)

  // Action states
  const [busyId, setBusyId] = useState<string | null>(null)

  const loadSessions = async () => {
    setLoadingSessions(true)
    try {
      const all = await api.listSessions()
      const archived = all.filter((s) => s.archived === true)
      setSessions(archived)
    } catch (e) {
      console.error(e)
    } finally {
      setLoadingSessions(false)
    }
  }

  const loadDeliverables = async () => {
    setLoadingDeliverables(true)
    try {
      const res = await api.listArchive().catch(() => ({ items: [] }))
      setDeliverables(res.items || [])
    } catch (e) {
      console.error(e)
    } finally {
      setLoadingDeliverables(false)
    }
  }

  useEffect(() => {
    loadSessions()
    loadDeliverables()
  }, [])

  const handleUnarchiveSession = async (sessionId: string) => {
    setBusyId(sessionId)
    try {
      await api.setSessionArchived(sessionId, false)
      await loadSessions()
    } catch (e) {
      console.error(e)
    } finally {
      setBusyId(null)
    }
  }

  const handleDeleteSession = async (sessionId: string) => {
    if (!confirm("确定要彻底删除该归档会话吗？此操作不可逆。")) return
    setBusyId(sessionId)
    try {
      await api.deleteSession(sessionId)
      await loadSessions()
    } catch (e) {
      console.error(e)
    } finally {
      setBusyId(null)
    }
  }

  const handleViewSession = (sessionId: string) => {
    if (onSelectSession) {
      onSelectSession(sessionId)
    }
    if (onCloseSettings) {
      onCloseSettings()
    }
  }

  const filteredSessions = sessions.filter((s) => {
    const kw = (searchSession || "").trim().toLowerCase()
    if (!kw) return true
    const matchTitle = (s.title || "").toLowerCase().includes(kw)
    const matchId = (s.session_id || "").toLowerCase().includes(kw)
    return matchTitle || matchId
  })

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-base font-medium">归档与工程资产管理 (Archive & Deliverables)</h3>
        <p className="text-xs text-muted-foreground mt-1">
          管理已完成的工程会话归档，以及历次任务执行所封包生成的正式工程交付物快照（IFC / 报告 / 拓扑清单）。
        </p>
      </div>

      <Tabs value={subTab} onValueChange={(v) => setSubTab(v as any)} className="w-full">
        <TabsList className="w-full max-w-sm grid grid-cols-2">
          <TabsTrigger value="sessions" className="text-xs flex items-center gap-1.5">
            <Archive className="h-3.5 w-3.5" />
            已归档会话 ({sessions.length})
          </TabsTrigger>
          <TabsTrigger value="deliverables" className="text-xs flex items-center gap-1.5">
            <Package className="h-3.5 w-3.5" />
            交付物快照 ({deliverables.length})
          </TabsTrigger>
        </TabsList>

        {/* 选项卡 1：已归档会话 */}
        <TabsContent value="sessions" className="space-y-4 pt-3">
          <div className="flex items-center justify-between">
            <p className="text-xs text-muted-foreground">
              归档后的会话不会出现在左侧主工作台列表，您可随时恢复到工作台或直接载入查看。
            </p>
            <div className="relative w-52 shrink-0">
              <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                value={searchSession}
                onChange={(e) => setSearchSession(e.target.value)}
                placeholder="搜索已归档会话..."
                className="pl-8 h-8 text-xs"
              />
            </div>
          </div>

          {loadingSessions ? (
            <div className="py-8 text-center text-xs text-muted-foreground">正在加载归档会话...</div>
          ) : filteredSessions.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border/70 p-8 text-center text-xs text-muted-foreground bg-muted/10">
              <Archive className="h-8 w-8 mx-auto mb-2 text-muted-foreground/30" />
              <p className="font-medium text-foreground">归档箱为空</p>
              <p className="mt-1">当左侧会话不再需要活跃编辑时，可在会话右侧菜单中选择“归档会话”。</p>
            </div>
          ) : (
            <div className="space-y-2.5 max-h-[420px] overflow-y-auto pr-1">
              {filteredSessions.map((s) => (
                <div
                  key={s.session_id}
                  className="flex items-center justify-between p-3.5 rounded-xl border border-border/70 bg-card/60 hover:bg-muted/30 transition-all text-xs"
                >
                  <div className="space-y-1 max-w-[65%]">
                    <div className="flex items-center space-x-2">
                      <span className="font-medium text-foreground text-sm truncate">{s.title || "未命名会话"}</span>
                      {s.playbook && (
                        <Badge variant="secondary" className="text-[10px] font-normal px-1.5 py-0">
                          {s.playbook}
                        </Badge>
                      )}
                    </div>
                    <div className="flex items-center space-x-3 text-[11px] text-muted-foreground">
                      <span className="font-mono">{s.session_id.slice(0, 16)}...</span>
                      <span>•</span>
                      <span>{s.event_count || 0} 条操作记录</span>
                      {s.archived_at && (
                        <>
                          <span>•</span>
                          <span className="flex items-center gap-1">
                            <Calendar className="h-3 w-3" />
                            归档于: {new Date(s.archived_at).toLocaleDateString()}
                          </span>
                        </>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center space-x-1.5 shrink-0">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleViewSession(s.session_id)}
                      className="h-8 px-2.5 text-xs gap-1"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                      查看
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busyId === s.session_id}
                      onClick={() => handleUnarchiveSession(s.session_id)}
                      className="h-8 px-2.5 text-xs gap-1 text-primary hover:text-primary hover:bg-primary/10"
                    >
                      <ArchiveRestore className="h-3.5 w-3.5" />
                      恢复
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={busyId === s.session_id}
                      onClick={() => handleDeleteSession(s.session_id)}
                      className="h-8 px-2 text-xs text-rose-500 hover:text-rose-600 hover:bg-rose-500/10"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </TabsContent>

        {/* 选项卡 2：工程交付物快照 */}
        <TabsContent value="deliverables" className="space-y-4 pt-3">
          <p className="text-xs text-muted-foreground">
            工程自愈求解管道在完成阶段自动打包的交付文件包，包含标准 IFC 几何体、IFC-JSON 与校验报告。
          </p>

          {loadingDeliverables ? (
            <div className="py-8 text-center text-xs text-muted-foreground">正在加载交付快照...</div>
          ) : deliverables.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border/70 p-8 text-center text-xs text-muted-foreground bg-muted/10">
              <Package className="h-8 w-8 mx-auto mb-2 text-muted-foreground/30" />
              <p className="font-medium text-foreground">暂无工程交付包快照</p>
              <p className="mt-1">当工作台完成一次完整的市政管网生成与碰撞自愈后，将自动在此封包存盘。</p>
            </div>
          ) : (
            <div className="space-y-3 max-h-[420px] overflow-y-auto pr-1">
              {deliverables.map((item, idx) => (
                <div
                  key={idx}
                  className="p-4 rounded-xl border border-border/70 bg-card/60 space-y-2.5 text-xs"
                >
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="flex items-center space-x-2">
                        <span className="font-medium text-sm text-foreground">
                          {item.brief || "市政管线自愈求解包"}
                        </span>
                        <Badge variant="outline" className="text-[10px] font-mono">
                          {item.session_id.slice(0, 8)}
                        </Badge>
                      </div>
                      <p className="text-[11px] text-muted-foreground mt-0.5">
                        归档包路径: <code className="font-mono">{item.pack}</code>
                      </p>
                    </div>
                    <Badge variant="secondary" className="text-[10px]">
                      {new Date(item.archived_at).toLocaleString()}
                    </Badge>
                  </div>

                  {item.files && item.files.length > 0 && (
                    <div className="rounded-lg bg-muted/40 p-2.5 space-y-1">
                      <p className="text-[11px] font-medium text-muted-foreground mb-1">文件清单:</p>
                      <div className="grid grid-cols-2 gap-1.5">
                        {item.files.map((f, fIdx) => (
                          <div key={fIdx} className="flex items-center justify-between text-[11px] text-foreground/80">
                            <span className="flex items-center gap-1 truncate max-w-[180px]">
                              <FileCode className="h-3 w-3 text-primary/70 shrink-0" />
                              {f.name}
                            </span>
                            <span className="text-[10px] text-muted-foreground font-mono">
                              {(f.size / 1024).toFixed(1)} KB
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}
