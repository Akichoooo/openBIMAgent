import React, { useState, useEffect } from "react"
import { api, SessionItem } from "@/services/api"
import { WorkspacePicker } from "./WorkspacePicker"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import {
  Plus,
  Pin,
  Archive,
  MoreVertical,
  Edit2,
  GitFork,
  Trash2,
  Copy,
  Settings,
  Folder,
  MessageSquare,
  Sparkles,
  RotateCw,
  FileText,
  Sun,
  Moon,
} from "lucide-react"
import { toast } from "sonner"

interface SidebarProps {
  currentSessionId: string | null
  onSelectSession: (sessionId: string) => void
  onOpenSettings: (tab?: string) => void
  onOpenNewTask: () => void
  onNewChat?: (playbook?: string) => void
  refreshTrigger?: number
  isDark?: boolean
  onToggleTheme?: () => void
  width?: number
  style?: React.CSSProperties
  runningSessionId?: string | null
}

export function getSessionTitle(s?: SessionItem | null): string {
  if (!s) return "未命名工程会话"
  let t = s.title as any
  if (typeof t === "object" && t !== null) {
    t = t.title || t.name || t.zh || t.en || ""
  }
  if (typeof t === "string") {
    const trimmed = t.trim()
    if (trimmed && trimmed !== "[object Object]") return trimmed
  }
  if (s.playbook === "municipal_utility" || s.playbook?.includes("municipal")) return "市政综合管网规划"
  if (s.playbook === "single_asset_hero" || s.playbook?.includes("single")) return "单体资产高精建模"
  return s.session_id ? `工程任务 ${s.session_id.slice(0, 8)}` : "未命名工程会话"
}

function formatRelativeTime(dateStr?: string): string {
  if (!dateStr) return "now"
  const d = new Date(dateStr).getTime()
  if (isNaN(d)) return "now"
  const diffSec = Math.floor((Date.now() - d) / 1000)
  if (diffSec < 60) return "now"
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m`
  const diffHours = Math.floor(diffMin / 60)
  if (diffHours < 24) return `${diffHours}h`
  const diffDays = Math.floor(diffHours / 24)
  if (diffDays === 1) return "1d"
  if (diffDays < 7) return `${diffDays}d`
  if (diffDays < 30) return `${Math.floor(diffDays / 7)}w`
  return new Date(dateStr).toLocaleDateString()
}

const PLAYBOOK_LABELS: Record<string, string> = {
  municipal_utility: "市政给排水管网",
  single_asset_hero: "单体资产建模",
  edo_cyberpunk_district: "Edo 街区规划",
  general: "常规任务",
}

export const Sidebar: React.FC<SidebarProps> = ({
  currentSessionId,
  onSelectSession,
  onOpenSettings,
  onOpenNewTask,
  onNewChat,
  refreshTrigger,
  isDark,
  onToggleTheme,
  width,
  style,
  runningSessionId,
}) => {
  const [sessions, setSessions] = useState<SessionItem[]>([])
  const [loading, setLoading] = useState(false)
  const [pinnedIds, setPinnedIds] = useState<string[]>([])
  const [currentWorkspaceId, setCurrentWorkspaceId] = useState<string | null>(null)

  // 折叠目录状态 (对齐图二: 点击目录折叠/展开)
  const [collapsedFolders, setCollapsedFolders] = useState<Record<string, boolean>>(() => {
    try {
      const saved = localStorage.getItem("openbim_collapsed_folders")
      return saved ? JSON.parse(saved) : {}
    } catch {
      return {}
    }
  })

  const toggleFolderCollapse = (key: string) => {
    setCollapsedFolders((prev) => {
      const next = { ...prev, [key]: !prev[key] }
      try {
        localStorage.setItem("openbim_collapsed_folders", JSON.stringify(next))
      } catch {}
      return next
    })
  }

  // 自定义目录显示名称 (对齐图三: 目录编辑功能)
  const [customFolderLabels, setCustomFolderLabels] = useState<Record<string, string>>(() => {
    try {
      const saved = localStorage.getItem("openbim_folder_labels")
      return saved ? JSON.parse(saved) : {}
    } catch {
      return {}
    }
  })

  // 目录编辑弹窗状态
  const [folderEditDialogOpen, setFolderEditDialogOpen] = useState(false)
  const [editingFolderKey, setEditingFolderKey] = useState<string | null>(null)
  const [editingFolderTitle, setEditingFolderTitle] = useState("")

  const handleOpenEditFolder = (key: string, currentTitle: string) => {
    setEditingFolderKey(key)
    setEditingFolderTitle(currentTitle)
    setFolderEditDialogOpen(true)
  }

  const handleSaveEditFolder = () => {
    if (!editingFolderKey || !editingFolderTitle.trim()) return
    const next = { ...customFolderLabels, [editingFolderKey]: editingFolderTitle.trim() }
    setCustomFolderLabels(next)
    try {
      localStorage.setItem("openbim_folder_labels", JSON.stringify(next))
    } catch {}
    setFolderEditDialogOpen(false)
    toast.success("已更新目录名称")
  }

  // Rename Dialog
  const [renameDialogOpen, setRenameDialogOpen] = useState(false)
  const [renamingSession, setRenamingSession] = useState<{ id: string; title: string } | null>(null)
  const [newTitle, setNewTitle] = useState("")
  const [menuOpenSessionId, setMenuOpenSessionId] = useState<string | null>(null)

  const loadSessions = async () => {
    setLoading(true)
    try {
      const list = await api.listSessions()
      // Exclude archived sessions from sidebar
      setSessions(list.filter((s) => !s.archived))
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  // Load pinned IDs from localStorage
  useEffect(() => {
    try {
      const saved = localStorage.getItem("openbim_pinned_sessions")
      if (saved) setPinnedIds(JSON.parse(saved))
    } catch {}
    loadSessions()
  }, [refreshTrigger])

  const loadWorkspace = async () => {
    try {
      const res = await api.listWorkspaces()
      setCurrentWorkspaceId(res.current)
    } catch {}
  }

  // 工作区切换：重取当前工作区并刷新会话
  useEffect(() => {
    loadWorkspace()
    const handler = () => {
      loadWorkspace()
      loadSessions()
    }
    window.addEventListener("workspace-changed", handler)
    return () => window.removeEventListener("workspace-changed", handler)
  }, [])

  const togglePin = (sessionId: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    const next = pinnedIds.includes(sessionId)
      ? pinnedIds.filter((id) => id !== sessionId)
      : [sessionId, ...pinnedIds]
    setPinnedIds(next)
    try {
      localStorage.setItem("openbim_pinned_sessions", JSON.stringify(next))
    } catch {}
  }

  const handleArchive = async (sessionId: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    try {
      await api.setSessionArchived(sessionId, true)
      await loadSessions()
    } catch (e) {
      console.error(e)
    }
  }

  const handleDelete = async (sessionId: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    if (!confirm("确定彻底删除该会话吗？")) return
    try {
      await api.deleteSession(sessionId)
      await loadSessions()
    } catch (e) {
      console.error(e)
    }
  }

  const handleFork = async (sessionId: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    try {
      const res = await api.forkSession(sessionId)
      await loadSessions()
      if (res?.session_id) {
        onSelectSession(res.session_id)
      }
    } catch (e) {
      console.error(e)
    }
  }

  const handleCopyId = (sessionId: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    navigator.clipboard?.writeText(sessionId)
  }

  const openRename = (session: SessionItem, e?: React.MouseEvent) => {
    e?.stopPropagation()
    const safeTitle = getSessionTitle(session)
    setRenamingSession({ id: session.session_id, title: safeTitle })
    setNewTitle(safeTitle)
    setRenameDialogOpen(true)
  }

  const submitRename = async () => {
    if (!renamingSession || !newTitle.trim()) return
    try {
      await api.renameSession(renamingSession.id, newTitle.trim())
      setRenameDialogOpen(false)
      await loadSessions()
    } catch (e) {
      console.error(e)
    }
  }

  // 按当前工作区过滤（「不在项目中工作」时仅显示无归属会话）
  const visibleSessions = sessions.filter((s) =>
    currentWorkspaceId ? s.workspace === currentWorkspaceId : !s.workspace
  )

  // Group sessions by playbook
  const groupedSessions = visibleSessions.reduce<Record<string, SessionItem[]>>((acc, session) => {
    const key = session.playbook || "general"
    if (!acc[key]) acc[key] = []
    acc[key].push(session)
    return acc
  }, {})

  // Sort: pinned first
  const sortSessions = (list: SessionItem[]) => {
    return [...list].sort((a, b) => {
      const aPinned = pinnedIds.includes(a.session_id)
      const bPinned = pinnedIds.includes(b.session_id)
      if (aPinned && !bPinned) return -1
      if (!aPinned && bPinned) return 1
      return new Date(b.last_active || 0).getTime() - new Date(a.last_active || 0).getTime()
    })
  }

  return (
    <aside
      style={{ width: width ? `${width}px` : undefined, ...style }}
      className="shrink-0 border-r border-neutral-200/80 dark:border-neutral-800 bg-neutral-50/70 dark:bg-neutral-950/70 flex flex-col justify-between select-none h-full min-h-0 overflow-hidden"
    >
      {/* 侧边栏顶部整合栏：工程文件夹切换 + 新建对话快捷入口 */}
      <div className="p-2.5 pb-2 shrink-0 flex items-center gap-1.5">
        <div className="flex-1 min-w-0">
          <WorkspacePicker onWorkspaceCreated={() => onNewChat?.()} />
        </div>
        <button
          onClick={() => (onNewChat ? onNewChat() : onOpenNewTask())}
          className="shrink-0 h-9 w-9 flex items-center justify-center rounded-xl bg-violet-600 hover:bg-violet-700 active:scale-95 text-white shadow-xs transition-all cursor-pointer group"
          title="新建对话 (⌘K)"
          aria-label="新建对话"
        >
          <Plus className="h-4 w-4 stroke-[2.5] group-hover:rotate-90 transition-transform duration-200" />
        </button>
      </div>

      {/* 任务列表分类条 (包含刷新按钮，对齐图三) */}
      <div className="px-3 pt-1 pb-1 flex items-center justify-between text-[11px] font-semibold text-neutral-500 dark:text-neutral-400 shrink-0">
        <div className="flex items-center space-x-1.5">
          <span>任务</span>
          <span className="font-mono text-[10px] text-neutral-400">({visibleSessions.length})</span>
        </div>
        <button
          onClick={() => {
            loadWorkspace()
            loadSessions()
            toast.success("已刷新工程会话与工作区")
          }}
          className="p-1 rounded text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 hover:bg-neutral-200/60 dark:hover:bg-neutral-800 transition-colors cursor-pointer"
          title="刷新任务列表"
        >
          <RotateCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {/* 中部：会话列表（完全杜绝左右滑动条，纯净纵向滚动） */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden p-2 space-y-3 min-h-0">
        {loading && sessions.length === 0 ? (
          <div className="py-6 text-center text-xs text-neutral-400">正在同步会话...</div>
        ) : visibleSessions.length === 0 ? (
          <div className="py-8 text-center text-xs text-neutral-400">
            <MessageSquare className="h-6 w-6 mx-auto mb-2 text-neutral-300 dark:text-neutral-600" />
            <p>{currentWorkspaceId ? "当前项目暂无会话" : "暂无活跃工程会话"}</p>
          </div>
        ) : (
          Object.entries(groupedSessions).map(([folderKey, folderSessions]) => {
            const sorted = sortSessions(folderSessions)
            const folderLabel = customFolderLabels[folderKey] || PLAYBOOK_LABELS[folderKey] || folderKey
            const isCollapsed = !!collapsedFolders[folderKey]

            return (
              <div key={folderKey} className="space-y-1">
                {/* 文件夹分组标题（对齐图二/图三：点击折叠展开，支持三点菜单与新建会话） */}
                <div
                  onClick={() => toggleFolderCollapse(folderKey)}
                  className="flex items-center justify-between px-1.5 py-1 text-xs font-semibold text-neutral-700 dark:text-neutral-200 cursor-pointer hover:bg-neutral-200/50 dark:hover:bg-neutral-800/50 rounded-lg transition-colors group/folder select-none"
                >
                  <div className="flex items-center space-x-1.5 truncate min-w-0 flex-1">
                    <Folder className="h-3.5 w-3.5 text-neutral-500 dark:text-neutral-400 shrink-0" />
                    <span className="truncate">{folderLabel}</span>
                  </div>
                  <div
                    className="flex items-center space-x-1 shrink-0 text-neutral-400"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {/* 目录操作下拉菜单 (对齐图三: 复制工程名称 / 工程设置 / 编辑目录) */}
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <button
                          className="p-0.5 rounded opacity-70 group-hover/folder:opacity-100 hover:bg-neutral-200/70 dark:hover:bg-neutral-800 hover:text-neutral-800 dark:hover:text-neutral-100 transition-all cursor-pointer"
                          title="目录操作"
                        >
                          <MoreVertical className="h-3 w-3" />
                        </button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-44 text-xs shadow-md">
                        <DropdownMenuItem
                          onClick={() => {
                            navigator.clipboard?.writeText(folderLabel)
                            toast.success("已复制工程名称: " + folderLabel)
                          }}
                          className="cursor-pointer"
                        >
                          <Copy className="h-3.5 w-3.5 mr-2 text-neutral-500" />
                          复制工程名称
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onClick={() => onOpenSettings?.("general")}
                          className="cursor-pointer"
                        >
                          <Settings className="h-3.5 w-3.5 mr-2 text-neutral-500" />
                          工程设置
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onClick={() => handleOpenEditFolder(folderKey, folderLabel)}
                          className="cursor-pointer"
                        >
                          <Edit2 className="h-3.5 w-3.5 mr-2 text-neutral-500" />
                          编辑目录名称
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>

                    {/* 新建任务 (+) */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        if (onNewChat) {
                          onNewChat(folderKey)
                        } else {
                          onOpenNewTask()
                        }
                      }}
                      className="p-0.5 rounded opacity-70 group-hover/folder:opacity-100 hover:bg-neutral-200/70 dark:hover:bg-neutral-800 hover:text-neutral-800 dark:hover:text-neutral-100 transition-all cursor-pointer"
                      title="在此工程目录下新建对话"
                    >
                      <Plus className="h-3 w-3" />
                    </button>
                  </div>
                </div>

                {/* 会话行清单（对齐图二：在文件夹下方缩进，具备树形层级感，支持折叠展开） */}
                {!isCollapsed && (
                  <div className="space-y-1 ml-2.5 pl-2 border-l border-neutral-200/70 dark:border-neutral-800/70">
                    {sorted.map((s) => {
                      const isActive = currentSessionId === s.session_id
                      const isPinned = pinnedIds.includes(s.session_id)
                      const isRunning = runningSessionId === s.session_id

                      return (
                        <div
                          key={s.session_id}
                          onClick={() => onSelectSession(s.session_id)}
                          className={`group relative flex items-center justify-between px-2.5 py-1.5 rounded-lg text-xs cursor-pointer transition-all ${
                            isActive
                              ? "bg-neutral-200/80 dark:bg-neutral-800/80 text-neutral-900 dark:text-neutral-100 font-medium"
                              : "text-neutral-600 dark:text-neutral-400 hover:text-neutral-900 dark:hover:text-neutral-100 hover:bg-neutral-100/80 dark:hover:bg-neutral-800/60"
                          }`}
                        >
                          {/* 会话名称与置顶徽标 */}
                          <div className="flex items-center space-x-1.5 min-w-0 flex-1 pr-12">
                            {isPinned && (
                              <Pin className="h-3 w-3 text-amber-500 fill-amber-500 shrink-0 rotate-45" />
                            )}
                            <span className="truncate text-xs">
                              {getSessionTitle(s)}
                            </span>
                          </div>

                          {/* 右侧：正在运行状态 (带缺口旋转圆圈，对齐图二) 或 相对时间 (1h) */}
                          {isRunning ? (
                            <div className="flex items-center shrink-0 pr-0.5" title="正在执行任务...">
                              <div className="h-3.5 w-3.5 rounded-full border-[1.5px] border-neutral-300 dark:border-neutral-600 border-t-neutral-800 dark:border-t-neutral-100 animate-spin" />
                            </div>
                          ) : (
                            <span className="text-[10px] text-neutral-400 group-hover:opacity-0 transition-opacity shrink-0">
                              {formatRelativeTime(s.last_active)}
                            </span>
                          )}

                          {/* 悬浮操作图标 (永久 flex 占位 + 柔和透明度显隐，彻底杜绝 display:none 导致关闭退出动画时回闪到左上角) */}
                          <div className={`absolute right-1 top-1/2 -translate-y-1/2 flex items-center space-x-0.5 bg-white/95 dark:bg-neutral-900/95 py-0.5 px-1 rounded-md shadow-xs border border-neutral-200/60 dark:border-neutral-800/60 transition-opacity duration-150 ${
                            menuOpenSessionId === s.session_id
                              ? "opacity-100 pointer-events-auto"
                              : "opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto"
                          }`}>
                            <button
                              onClick={(e) => togglePin(s.session_id, e)}
                              className={`p-1 rounded hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 transition-colors ${
                                isPinned ? "text-amber-500" : ""
                              }`}
                              title={isPinned ? "取消置顶" : "置顶会话"}
                            >
                              <Pin className="h-3.5 w-3.5" />
                            </button>

                            <button
                              onClick={(e) => handleArchive(s.session_id, e)}
                              className="p-1 rounded hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 transition-colors"
                              title="移入归档"
                            >
                              <Archive className="h-3.5 w-3.5" />
                            </button>

                            <DropdownMenu
                              open={menuOpenSessionId === s.session_id}
                              onOpenChange={(open) => {
                                if (open) {
                                  setMenuOpenSessionId(s.session_id)
                                } else {
                                  setTimeout(() => {
                                    setMenuOpenSessionId((prev) => (prev === s.session_id ? null : prev))
                                  }, 150)
                                }
                              }}
                            >
                              <DropdownMenuTrigger asChild>
                                <button
                                  onClick={(e) => e.stopPropagation()}
                                  className={`p-1 rounded hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 transition-colors ${
                                    menuOpenSessionId === s.session_id ? "bg-neutral-200/80 dark:bg-neutral-800 text-neutral-800 dark:text-neutral-100" : ""
                                  }`}
                                  title="更多操作"
                                >
                                  <MoreVertical className="h-3.5 w-3.5" />
                                </button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end" side="bottom" sideOffset={4} className="w-40 text-xs">
                                <DropdownMenuItem
                                  onClick={(e) => openRename(s, e as any)}
                                  className="cursor-pointer"
                                >
                                  <Edit2 className="h-3.5 w-3.5 mr-2" />
                                  重命名
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  onClick={(e) => handleFork(s.session_id, e as any)}
                                  className="cursor-pointer"
                                >
                                  <GitFork className="h-3.5 w-3.5 mr-2" />
                                  分支会话 (Fork)
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  onClick={(e) => handleCopyId(s.session_id, e as any)}
                                  className="cursor-pointer"
                                >
                                  <Copy className="h-3.5 w-3.5 mr-2" />
                                  复制会话 ID
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  onClick={(e) => handleArchive(s.session_id, e as any)}
                                  className="cursor-pointer"
                                >
                                  <Archive className="h-3.5 w-3.5 mr-2" />
                                  移入归档
                                </DropdownMenuItem>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem
                                  onClick={(e) => handleDelete(s.session_id, e as any)}
                                  className="text-rose-500 hover:text-rose-600 cursor-pointer"
                                >
                                  <Trash2 className="h-3.5 w-3.5 mr-2" />
                                  删除会话
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })
        )}
      </div>

      {/* 底部：设置入口与暗黑/明亮主题切换 */}
      <div className="border-t border-neutral-200/80 dark:border-neutral-800 shrink-0 p-2 bg-neutral-50/50 dark:bg-neutral-950/50 flex items-center gap-1">
        <button
          onClick={() => onOpenSettings("appearance")}
          className="flex-1 flex items-center justify-between px-2.5 py-2 rounded-lg text-neutral-600 dark:text-neutral-300 hover:text-neutral-900 dark:hover:text-neutral-100 hover:bg-neutral-200/60 dark:hover:bg-neutral-800/60 transition-colors text-xs font-medium group cursor-pointer"
        >
          <div className="flex items-center space-x-2">
            <Settings className="h-4 w-4 text-neutral-500 group-hover:text-neutral-700 dark:group-hover:text-neutral-200 transition-colors" />
            <span className="font-medium">设置</span>
          </div>

          <div className="flex items-center space-x-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" title="本地微内核运行环境正常" />
            <span className="text-[10px] text-neutral-400 font-mono">⌘,</span>
          </div>
        </button>

        {onToggleTheme && (
          <button
            onClick={onToggleTheme}
            className="h-8 w-8 rounded-lg flex items-center justify-center text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100 hover:bg-neutral-200/60 dark:hover:bg-neutral-800/60 transition-colors cursor-pointer shrink-0"
            title={isDark ? "切换为明亮浅色主题" : "切换为暗黑主题"}
          >
            {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
        )}
      </div>

      {/* 重命名会话弹窗 */}
      <Dialog open={renameDialogOpen} onOpenChange={setRenameDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base">重命名工程会话</DialogTitle>
          </DialogHeader>
          <div className="py-2">
            <Input
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="请输入新的会话标题"
              className="text-xs"
              autoFocus
              onKeyDown={(e) => e.key === "Enter" && submitRename()}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setRenameDialogOpen(false)}>
              取消
            </Button>
            <Button size="sm" onClick={submitRename} className="bg-violet-600 hover:bg-violet-700 text-white">
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 编辑目录名称弹窗 (对齐图三) */}
      <Dialog open={folderEditDialogOpen} onOpenChange={setFolderEditDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base">编辑目录名称</DialogTitle>
          </DialogHeader>
          <div className="py-2">
            <Input
              value={editingFolderTitle}
              onChange={(e) => setEditingFolderTitle(e.target.value)}
              placeholder="请输入目录显示名称"
              className="text-xs"
              autoFocus
              onKeyDown={(e) => e.key === "Enter" && handleSaveEditFolder()}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setFolderEditDialogOpen(false)}>
              取消
            </Button>
            <Button size="sm" onClick={handleSaveEditFolder} className="bg-violet-600 hover:bg-violet-700 text-white">
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </aside>
  )
}
