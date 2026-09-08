import React, { useEffect, useState } from "react"
import { api, WorkspaceItem } from "@/services/api"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Folder,
  ChevronDown,
  Check,
  X,
  MessageSquareOff,
  Search,
} from "lucide-react"
import { toast } from "sonner"

interface WorkspacePickerProps {
  onChanged?: () => void
}

export const WorkspacePicker: React.FC<WorkspacePickerProps> = ({ onChanged }) => {
  const [open, setOpen] = useState(false)
  const [current, setCurrent] = useState<string | null>(null)
  const [items, setItems] = useState<WorkspaceItem[]>([])
  const [filter, setFilter] = useState("")

  const load = async () => {
    try {
      const res = await api.listWorkspaces()
      setCurrent(res.current)
      setItems(res.items)
    } catch (e) {
      console.error(e)
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
    try {
      await api.deleteWorkspace(id)
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

  const currentItem = items.find((it) => it.id === current)

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          className="w-full flex items-center justify-between px-3 py-2 rounded-xl border border-border/80 bg-background/80 hover:bg-muted/60 text-foreground transition-all shadow-sm text-xs font-medium"
          title="选择项目 / 工作区"
        >
          <div className="flex items-center space-x-2 min-w-0">
            <Folder className="h-4 w-4 text-primary shrink-0" />
            <span className="truncate">{currentItem ? currentItem.name : "选择项目"}</span>
          </div>
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 p-0 overflow-hidden">
        {/* 搜索框 */}
        <div className="p-2 border-b border-border/50">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="搜索工作区项目"
              className="h-8 pl-8 text-xs"
            />
          </div>
        </div>

        {/* 最近工作区列表 */}
        <ScrollArea className="max-h-56">
          <div className="p-1">
            {filtered.length === 0 ? (
              <div className="py-5 text-center text-xs text-muted-foreground">
                {items.length === 0
                  ? "暂无项目，可在「新建工程任务」中指定文件夹"
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
                    <Folder className="h-4 w-4 text-primary/70 shrink-0" />
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

        <Separator />

        {/* 操作行 */}
        <div className="p-1">
          <button
            onClick={() => handleSelect(null)}
            className="w-full flex items-center justify-between px-2.5 py-2 rounded-lg text-xs text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
          >
            <div className="flex items-center space-x-2">
              <MessageSquareOff className="h-4 w-4" />
              <span>不在项目中工作</span>
            </div>
            {current === null && <Check className="h-3.5 w-3.5 text-emerald-500" />}
          </button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
