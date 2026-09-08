import React, { useState, useEffect } from "react"
import { api, WorkspaceItem } from "@/services/api"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Sparkles,
  Folder,
  ShieldCheck,
  Compass,
  Hammer,
  MessageSquare,
  Eye,
  PlusCircle,
} from "lucide-react"

interface NewTaskDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onTaskStarted: (sessionId: string) => void
}

export const NewTaskDialog: React.FC<NewTaskDialogProps> = ({
  open,
  onOpenChange,
  onTaskStarted,
}) => {
  const [brief, setBrief] = useState("")
  const [playbook, setPlaybook] = useState("municipal_utility")
  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([])
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string>("none")
  const [isCustomFolder, setIsCustomFolder] = useState(false)
  const [customPath, setCustomPath] = useState("")
  const [customName, setCustomName] = useState("")
  const [mode, setMode] = useState<"plan" | "agent" | "ask">("plan")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    const load = async () => {
      try {
        const res = await api.listWorkspaces()
        setWorkspaces(res.items || [])
        if (res.current) {
          setSelectedWorkspaceId(res.current)
          const cur = res.items.find((it) => it.id === res.current)
          if (cur?.execution_mode) setMode(cur.execution_mode as any)
        } else {
          setSelectedWorkspaceId("none")
        }
      } catch (e) {
        console.error(e)
      }
    }
    load()
  }, [open])

  const handleWorkspaceChange = (val: string) => {
    setSelectedWorkspaceId(val)
    if (val === "custom") {
      setIsCustomFolder(true)
    } else {
      setIsCustomFolder(false)
      if (val !== "none") {
        const target = workspaces.find((w) => w.id === val)
        if (target?.execution_mode) setMode(target.execution_mode as any)
      }
    }
  }

  const handleSubmit = async () => {
    if (!brief.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      let wsId: string | undefined = undefined
      if (isCustomFolder && customPath.trim()) {
        const created = await api.createWorkspace(customName.trim(), customPath.trim())
        wsId = created?.item?.id
      } else if (selectedWorkspaceId !== "none" && selectedWorkspaceId !== "custom") {
        wsId = selectedWorkspaceId
      }

      if (wsId) {
        await api.setCurrentWorkspace(wsId)
        window.dispatchEvent(new CustomEvent("workspace-changed"))
      }

      const res = await api.startRun(brief.trim(), playbook, mode as any, wsId)
      if (res?.session_id) {
        onOpenChange(false)
        setBrief("")
        onTaskStarted(res.session_id)
      } else {
        setError("未能获取新建会话 ID")
      }
    } catch (e: any) {
      setError(e.message || "新建任务启动失败")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-base flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" />
            新建数字化工程任务
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3.5 py-2">
          {/* 项目 / 文件夹选择 */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-foreground">
              所属工程项目 / 文件夹 (Project Folder)
            </label>
            <Select value={selectedWorkspaceId} onValueChange={handleWorkspaceChange}>
              <SelectTrigger className="text-xs h-9">
                <SelectValue placeholder="选择所属工程项目目录" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none" className="text-xs">
                  不在特定项目中工作（全局根目录）
                </SelectItem>
                {workspaces.map((w) => (
                  <SelectItem key={w.id} value={w.id} className="text-xs">
                    📁 {w.name} ({w.path})
                  </SelectItem>
                ))}
                <SelectItem value="custom" className="text-xs text-primary font-medium">
                  + 指定新项目文件夹...
                </SelectItem>
              </SelectContent>
            </Select>

            {isCustomFolder && (
              <div className="p-2.5 rounded-lg border border-border/60 bg-muted/30 space-y-2 mt-1.5">
                <div className="space-y-1">
                  <label className="text-[11px] font-medium text-foreground">文件夹绝对路径</label>
                  <Input
                    value={customPath}
                    onChange={(e) => setCustomPath(e.target.value)}
                    placeholder="例如: D:\workSpace\my-bim-project"
                    className="h-8 text-xs font-mono"
                    autoFocus
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-[11px] font-medium text-foreground">项目名称（可选）</label>
                  <Input
                    value={customName}
                    onChange={(e) => setCustomName(e.target.value)}
                    placeholder="缺省取文件夹名"
                    className="h-8 text-xs"
                  />
                </div>
              </div>
            )}
          </div>

          {/* 工作交互模式 (Ask / Plan / Build) */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-foreground">
              工作交互模式 (Interaction Mode)
            </label>
            <Select value={mode} onValueChange={(m: any) => setMode(m)}>
              <SelectTrigger className="text-xs h-9">
                <SelectValue placeholder="选择工作模式" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="plan" className="text-xs">
                  <div className="flex items-center space-x-2">
                    <Compass className="h-3.5 w-3.5 text-violet-500" />
                    <span className="font-medium">Plan 规划与追问模式</span>
                    <span className="text-muted-foreground text-[11px]">（推荐：先出方案并主动追问不确定参数）</span>
                  </div>
                </SelectItem>
                <SelectItem value="agent" className="text-xs">
                  <div className="flex items-center space-x-2">
                    <Hammer className="h-3.5 w-3.5 text-amber-500" />
                    <span className="font-medium">Build 建模与自愈模式</span>
                    <span className="text-muted-foreground text-[11px]">（敏捷放样：自动执行几何生成与自愈）</span>
                  </div>
                </SelectItem>
                <SelectItem value="ask" className="text-xs">
                  <div className="flex items-center space-x-2">
                    <MessageSquare className="h-3.5 w-3.5 text-sky-500" />
                    <span className="font-medium">Ask 方案答疑模式</span>
                    <span className="text-muted-foreground text-[11px]">（只读分析：查阅规范、审图答疑，不改动模型）</span>
                  </div>
                </SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* 工程专业领域 (合并 Playbook 与规范约束) */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-foreground">
              工程专业领域 (Engineering Discipline)
            </label>
            <Select value={playbook} onValueChange={setPlaybook}>
              <SelectTrigger className="text-xs h-9">
                <SelectValue placeholder="选择工程专业领域" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="municipal_utility" className="text-xs">
                  市政给排水工程 (Municipal Utility · 绑定 GB 50289 规范)
                </SelectItem>
                <SelectItem value="residential_building" className="text-xs">
                  住宅洋房建筑方案 (Residential Villa · 绑定 GB 50016 规范)
                </SelectItem>
                <SelectItem value="industrial_steel" className="text-xs">
                  工业厂房与钢结构 (Industrial Steel · 绑定 GB 50017 规范)
                </SelectItem>
                <SelectItem value="district_planning" className="text-xs">
                  园区与街区规划 (District Planning · 绑定 GB 50180 规范)
                </SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* 任务意图 Brief */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-foreground">
              工程设计意图 (Task Brief)
            </label>
            <Textarea
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
              placeholder="例如：对东侧商业地块进行 DN400 重力污水主管线放样，自动避让 1# 燃气干管与既有建筑地下室并收敛满足 MU-CLEAR-001..."
              className="text-xs min-h-[90px] leading-relaxed"
            />
          </div>

          {error && <p className="text-xs text-rose-500">{error}</p>}
        </div>

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            size="sm"
            disabled={submitting || !brief.trim()}
            onClick={handleSubmit}
            className="gap-1.5"
          >
            <Sparkles className="h-3.5 w-3.5" />
            {submitting ? "正在初始化智能体..." : "启动工程管道"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
