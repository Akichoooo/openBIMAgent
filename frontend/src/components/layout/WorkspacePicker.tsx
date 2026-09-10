import React, { useEffect, useState } from "react"
import { api, WorkspaceItem } from "@/services/api"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Folder,
  ChevronDown,
  Check,
  X,
  Search,
  FolderPlus,
  Loader2,
} from "lucide-react"
import { toast } from "sonner"

interface WorkspacePickerProps {
  onChanged?: () => void
  onWorkspaceCreated?: (wsId: string) => void
}

export const WorkspacePicker: React.FC<WorkspacePickerProps> = ({ onChanged, onWorkspaceCreated }) => {
  const [open, setOpen] = useState(false)
  const [current, setCurrent] = useState<string | null>(null)
  const [items, setItems] = useState<WorkspaceItem[]>([])
  const [filter, setFilter] = useState("")

  // 新建/导入工程文件夹对话框状态
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [newName, setNewName] = useState("")
  const [newPath, setNewPath] = useState("")
  const [isSubmitting, setIsSubmitting] = useState(false)

  const load = async () => {
    try {
      const res = await api.listWorkspaces()
      const list = res.items || []
      setItems(list)
      if (res.current) {
        setCurrent(res.current)
      } else if (list.length > 0) {
        setCurrent(list[0].id)
        api.setCurrentWorkspace(list[0].id).catch(() => {})
      } else {
        setCurrent(null)
      }
    } catch (e) {
      console.error(e)
    }
  }

  // 计算默认存储根目录
  const getBaseDir = () => {
    if (items.length > 0 && items[0].path) {
      const p = items[0].path.replace(/[\\/][^\\/]+$/, "")
      return p ? `${p}\\workspaces` : "./workspaces"
    }
    return "./workspaces"
  }

  const handleOpenCreateModal = () => {
    setOpen(false)
    setNewName("")
    setNewPath("")
    setCreateModalOpen(true)
  }

  const handleNameChange = (val: string) => {
    setNewName(val)
    if (!newPath || newPath.startsWith(getBaseDir())) {
      const sanitized = val.trim().replace(/[\\/:*?"<>|]/g, "_")
      setNewPath(sanitized ? `${getBaseDir()}\\${sanitized}` : "")
    }
  }

  const handleCreateWorkspace = async (e?: React.FormEvent) => {
    e?.preventDefault()
    const trimmedName = newName.trim()
    let trimmedPath = newPath.trim()
    if (!trimmedName) {
      toast.error("请输入工程文件夹名称")
      return
    }
    if (!trimmedPath) {
      trimmedPath = `${getBaseDir()}\\${trimmedName.replace(/[\\/:*?"<>|]/g, "_")}`
    }

    setIsSubmitting(true)
    try {
      const res = await api.createWorkspace(trimmedName, trimmedPath)
      const createdItem = res.item
      if (createdItem?.id) {
        await api.setCurrentWorkspace(createdItem.id)
      }
      setCreateModalOpen(false)
      toast.success(`已创建并切换至工程文件夹: ${trimmedName}`)
      notifyChanged()
      await load()
      if (createdItem?.id) {
        onWorkspaceCreated?.(createdItem.id)
      }
    } catch (err: any) {
      toast.error("创建工程文件夹失败: " + (err.message || "未知错误"))
    } finally {
      setIsSubmitting(false)
    }
  }

  useEffect(() => {
    load()
    const handler = () => load()
    window.addEventListener("workspace-changed", handler)
    return () => window.removeEventListener("workspace-changed", handler)
  }, [])

  const notifyChanged = () => {
    window.dispatchEvent(new CustomEvent("workspace-changed"))
    onChanged?.()
  }

  const handleSelect = async (id: string | null) => {
    try {
      await api.setCurrentWorkspace(id)
      setOpen(false)
      notifyChanged()
      await load()
    } catch (e: any) {
      toast.error("切换项目失败: " + e.message)
    }
  }

  const handleRemove = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    const target = items.find((it) => it.id === id)
    if (!confirm(`确定从工程列表移除「${target?.name || id}」吗？（不会删除本地磁盘实际文件）`)) return
    try {
      await api.deleteWorkspace(id)
      toast.success("已从列表移除工程: " + (target?.name || id))
      notifyChanged()
      await load()
    } catch (err: any) {
      toast.error("移除失败: " + err.message)
    }
  }

  const filtered = items.filter(
    (it) =>
      !filter.trim() ||
      it.name.toLowerCase().includes(filter.trim().toLowerCase()) ||
      it.path.toLowerCase().includes(filter.trim().toLowerCase())
  )

  const currentItem = items.find((it) => it.id === current) || items[0]

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            className="w-full h-9 flex items-center justify-between px-2.5 rounded-xl border border-neutral-200/90 dark:border-neutral-800 bg-white/90 dark:bg-neutral-900/90 hover:bg-neutral-100 dark:hover:bg-neutral-800/80 text-foreground transition-all shadow-xs text-xs font-medium cursor-pointer"
            title="选择工程项目 / 切换文件夹"
          >
            <div className="flex items-center space-x-2 min-w-0">
              <Folder className="h-4 w-4 text-violet-600 dark:text-violet-400 shrink-0" />
              <span className="truncate">{currentItem ? currentItem.name : "openBIMAgent"}</span>
            </div>
            <ChevronDown className="h-3.5 w-3.5 text-neutral-400 shrink-0 ml-1" />
          </button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-76 p-0 overflow-hidden shadow-lg">
          {/* 搜索框 */}
          <div className="p-2 border-b border-border/50">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="搜索工程项目..."
                className="h-8 pl-8 text-xs"
              />
            </div>
          </div>

          {/* 新建工程文件夹行动项 */}
          <div className="p-1 border-b border-border/40 bg-neutral-50/50 dark:bg-neutral-900/40">
            <button
              onClick={handleOpenCreateModal}
              className="w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-xs font-medium text-violet-700 dark:text-violet-300 hover:bg-violet-100/70 dark:hover:bg-violet-950/60 transition-colors cursor-pointer group"
            >
              <div className="flex items-center space-x-2">
                <FolderPlus className="h-4 w-4 text-violet-600 dark:text-violet-400 group-hover:scale-110 transition-transform" />
                <span>新建工程文件夹...</span>
              </div>
              <span className="text-[10px] text-violet-500/80 font-mono">New</span>
            </button>
          </div>

          {/* 最近工程列表 */}
          <ScrollArea className="max-h-56">
            <div className="p-1">
              {filtered.length === 0 ? (
                <div className="py-5 text-center text-xs text-muted-foreground">
                  {items.length === 0
                    ? "暂无工程文件夹，可点击上方新建"
                    : "无匹配项目"}
                </div>
              ) : (
                filtered.map((it) => (
                  <div
                    key={it.id}
                    onClick={() => handleSelect(it.id)}
                    className="group flex items-center justify-between px-2.5 py-2 rounded-lg text-xs cursor-pointer hover:bg-muted/60 transition-colors"
                  >
                    <div className="flex items-center space-x-2 min-w-0 flex-1">
                      <Folder className="h-4 w-4 text-violet-500/80 shrink-0" />
                      <div className="min-w-0">
                        <div className="truncate font-medium text-foreground">{it.name}</div>
                        <div className="truncate text-[10px] text-muted-foreground/70 font-mono">
                          {it.path}
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center space-x-1 shrink-0">
                      {current === it.id && <Check className="h-3.5 w-3.5 text-emerald-500" />}
                      <button
                        onClick={(e) => handleRemove(it.id, e)}
                        className="p-0.5 rounded opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-rose-500 transition-opacity"
                        title="从列表移除（不删磁盘文件）"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </ScrollArea>
        </PopoverContent>
      </Popover>

      {/* 新建/打开工程文件夹对话框 */}
      <Dialog open={createModalOpen} onOpenChange={setCreateModalOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base font-semibold">
              <FolderPlus className="h-5 w-5 text-violet-600 dark:text-violet-400" />
              新建或打开工程文件夹
            </DialogTitle>
            <DialogDescription className="text-xs text-muted-foreground">
              指定本地文件夹作为工程工作区。所有 BIM 结构化建模会话、图纸交付产物均归属于该工程。
            </DialogDescription>
          </DialogHeader>

          <form onSubmit={handleCreateWorkspace} className="space-y-4 py-2">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-foreground">
                工程名称 <span className="text-rose-500">*</span>
              </label>
              <Input
                autoFocus
                value={newName}
                onChange={(e) => handleNameChange(e.target.value)}
                placeholder="例如：住宅洋房-示范区 / 市政管网一期"
                className="h-8 text-xs"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-foreground">
                本地磁盘存储路径
              </label>
              <Input
                value={newPath}
                onChange={(e) => setNewPath(e.target.value)}
                placeholder="支持输入/粘贴本地绝对路径；目录不存在时将自动创建"
                className="h-8 text-xs font-mono text-[11px]"
              />
              <p className="text-[10px] text-muted-foreground">
                若输入已有文件夹路径，将直接登记为工程工作区；若输入新路径，将自动新建文件夹。
              </p>
            </div>

            <DialogFooter className="gap-2 sm:gap-0 pt-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setCreateModalOpen(false)}
                className="h-8 text-xs"
              >
                取消
              </Button>
              <Button
                type="submit"
                size="sm"
                disabled={!newName.trim() || isSubmitting}
                className="h-8 text-xs bg-violet-600 hover:bg-violet-700 text-white"
              >
                {isSubmitting ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                    正在创建...
                  </>
                ) : (
                  "创建并进入"
                )}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  )
}
