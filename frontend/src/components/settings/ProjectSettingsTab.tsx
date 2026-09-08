import React, { useState, useEffect } from "react"
import { api, WorkspaceItem } from "@/services/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Folder,
  Plus,
  X,
  Edit2,
  Check,
  ShieldCheck,
  Hammer,
  Eye,
  Lock,
} from "lucide-react"
import { toast } from "sonner"

interface ProjectSettingsTabProps {
  workspace: WorkspaceItem
  onUpdated?: () => void
}

export const ProjectSettingsTab: React.FC<ProjectSettingsTabProps> = ({
  workspace,
  onUpdated,
}) => {
  const [editingName, setEditingName] = useState(false)
  const [name, setName] = useState(workspace.name)
  const [executionMode, setExecutionMode] = useState<"agent" | "yolo" | "plan">(
    workspace.execution_mode || "agent"
  )
  const [outsideFileAccess, setOutsideFileAccess] = useState<"allow" | "ask" | "deny">(
    workspace.outside_file_access || "allow"
  )
  const [terminalAutoExec, setTerminalAutoExec] = useState<"proceed" | "ask">(
    workspace.terminal_auto_exec || "ask"
  )
  const [artifactReviewPolicy, setArtifactReviewPolicy] = useState<"proceed" | "ask">(
    workspace.artifact_review_policy || "proceed"
  )
  const [folders, setFolders] = useState<string[]>(
    workspace.folders && workspace.folders.length > 0 ? workspace.folders : [workspace.path]
  )
  const [showAddFolder, setShowAddFolder] = useState(false)
  const [newFolderPath, setNewFolderPath] = useState("")
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setName(workspace.name)
    setExecutionMode(workspace.execution_mode || "agent")
    setOutsideFileAccess(workspace.outside_file_access || "allow")
    setTerminalAutoExec(workspace.terminal_auto_exec || "ask")
    setArtifactReviewPolicy(workspace.artifact_review_policy || "proceed")
    setFolders(
      workspace.folders && workspace.folders.length > 0 ? workspace.folders : [workspace.path]
    )
  }, [workspace])

  const saveChanges = async (updates: Partial<WorkspaceItem>) => {
    setSaving(true)
    try {
      await api.updateWorkspace(workspace.id, updates)
      toast.success("项目配置已保存")
      onUpdated?.()
      window.dispatchEvent(new CustomEvent("workspace-changed"))
    } catch (e: any) {
      toast.error("保存失败: " + e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleSaveName = async () => {
    if (!name.trim() || name === workspace.name) {
      setEditingName(false)
      setName(workspace.name)
      return
    }
    await saveChanges({ name: name.trim() })
    setEditingName(false)
  }

  const handleModeChange = async (val: "agent" | "yolo" | "plan") => {
    setExecutionMode(val)
    await saveChanges({ execution_mode: val })
  }

  const handleOutsideAccessChange = async (val: "allow" | "ask" | "deny") => {
    setOutsideFileAccess(val)
    await saveChanges({ outside_file_access: val })
  }

  const handleTerminalExecChange = async (val: "proceed" | "ask") => {
    setTerminalAutoExec(val)
    await saveChanges({ terminal_auto_exec: val })
  }

  const handleArtifactReviewChange = async (val: "proceed" | "ask") => {
    setArtifactReviewPolicy(val)
    await saveChanges({ artifact_review_policy: val })
  }

  const handleAddFolder = async () => {
    if (!newFolderPath.trim()) return
    const updated = [...folders, newFolderPath.trim()]
    setFolders(updated)
    setNewFolderPath("")
    setShowAddFolder(false)
    await saveChanges({ folders: updated })
  }

  const handleRemoveFolder = async (index: number) => {
    if (folders.length <= 1) {
      toast.error("项目至少需要保留一个目录")
      return
    }
    const updated = folders.filter((_, i) => i !== index)
    setFolders(updated)
    await saveChanges({ folders: updated })
  }

  return (
    <div className="space-y-8 max-w-3xl pb-12">
      {/* 顶部标题栏 (图二范式) */}
      <div className="space-y-1">
        <div className="flex items-center space-x-2">
          {editingName ? (
            <div className="flex items-center space-x-2">
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="h-8 text-base font-semibold w-64"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSaveName()
                  if (e.key === "Escape") {
                    setName(workspace.name)
                    setEditingName(false)
                  }
                }}
              />
              <Button size="sm" variant="ghost" onClick={handleSaveName} className="h-8 px-2">
                <Check className="h-4 w-4 text-emerald-500" />
              </Button>
            </div>
          ) : (
            <div className="flex items-center space-x-2 group">
              <h2 className="text-xl font-bold tracking-tight text-foreground">{workspace.name}</h2>
              <button
                onClick={() => setEditingName(true)}
                className="p-1 rounded opacity-60 hover:opacity-100 hover:bg-muted text-muted-foreground transition-all"
                title="重命名项目"
              >
                <Edit2 className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          管理项目工作目录、智能体执行策略与本地安全权限。
        </p>
      </div>

      {/* Folders 分组 */}
      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-foreground">Folders（工程目录）</h3>
        <div className="space-y-2 rounded-xl border border-border/70 bg-card/40 p-2.5">
          {folders.map((f, i) => (
            <div
              key={i}
              className="flex items-center justify-between px-3 py-2 rounded-lg bg-background/80 border border-border/50 text-xs group"
            >
              <div className="flex items-center space-x-2.5 min-w-0">
                <Folder className="h-4 w-4 text-primary shrink-0" />
                <span className="font-mono text-foreground truncate">{f}</span>
              </div>
              <button
                onClick={() => handleRemoveFolder(i)}
                className="text-muted-foreground/60 hover:text-rose-500 p-1 rounded transition-colors"
                title="移除该目录"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}

          {showAddFolder ? (
            <div className="flex items-center space-x-2 p-1">
              <Input
                value={newFolderPath}
                onChange={(e) => setNewFolderPath(e.target.value)}
                placeholder="输入本地目录绝对路径，如 D:\workSpace\sub-module"
                className="h-8 text-xs font-mono"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleAddFolder()
                  if (e.key === "Escape") setShowAddFolder(false)
                }}
              />
              <Button size="sm" onClick={handleAddFolder} className="h-8 text-xs">
                添加
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setShowAddFolder(false)}
                className="h-8 text-xs"
              >
                取消
              </Button>
            </div>
          ) : (
            <button
              onClick={() => setShowAddFolder(true)}
              className="w-full py-2 flex items-center justify-center space-x-1.5 rounded-lg border border-dashed border-border/80 hover:border-primary/60 hover:bg-muted/30 text-xs text-muted-foreground hover:text-foreground transition-all"
            >
              <Plus className="h-3.5 w-3.5" />
              <span>+ Add Folder（添加关联目录）</span>
            </button>
          )}
        </div>
      </div>

      {/* Agent Settings 分组 (图二重点: 自主执行还是审批) */}
      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-foreground">Agent Settings（智能体执行预设）</h3>
        <div className="divide-y divide-border/50 rounded-xl border border-border/70 bg-card/40 overflow-hidden">
          {/* 重点: Security Preset / 执行模式 */}
          <div className="flex items-center justify-between p-4 hover:bg-muted/20 transition-colors">
            <div className="space-y-0.5 max-w-[65%]">
              <div className="text-xs font-medium text-foreground flex items-center gap-1.5">
                {executionMode === "agent" ? (
                  <ShieldCheck className="h-3.5 w-3.5 text-emerald-500" />
                ) : executionMode === "yolo" ? (
                  <Hammer className="h-3.5 w-3.5 text-rose-500" />
                ) : (
                  <Eye className="h-3.5 w-3.5 text-sky-500" />
                )}
                <span>Security Preset（安全与执行模式）</span>
              </div>
              <p className="text-[11px] text-muted-foreground leading-relaxed">
                控制智能体在该项目下的执行权限：审批模式需人工确认关键操作，自主执行自动放行。
              </p>
            </div>
            <Select value={executionMode} onValueChange={handleModeChange}>
              <SelectTrigger className="w-52 h-8 text-xs">
                <SelectValue placeholder="选择执行模式" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="agent" className="text-xs">
                  <div className="flex items-center space-x-2">
                    <ShieldCheck className="h-3.5 w-3.5 text-emerald-500" />
                    <span>审批模式 (推荐安全)</span>
                  </div>
                </SelectItem>
                <SelectItem value="yolo" className="text-xs text-rose-500">
                  <div className="flex items-center space-x-2">
                    <Hammer className="h-3.5 w-3.5 text-rose-500" />
                    <span>自主执行 (Autonomous)</span>
                  </div>
                </SelectItem>
                <SelectItem value="plan" className="text-xs text-sky-500">
                  <div className="flex items-center space-x-2">
                    <Eye className="h-3.5 w-3.5 text-sky-500" />
                    <span>只读方案 (Plan-only)</span>
                  </div>
                </SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Outside of folders file access policy */}
          <div className="flex items-center justify-between p-4 hover:bg-muted/20 transition-colors">
            <div className="space-y-0.5 max-w-[65%]">
              <div className="text-xs font-medium text-foreground">
                Outside of folders file access policy（跨目录文件访问策略）
              </div>
              <p className="text-[11px] text-muted-foreground leading-relaxed">
                配置智能体访问该工作目录以外的磁盘文件时的安全约束。
              </p>
            </div>
            <Select value={outsideFileAccess} onValueChange={handleOutsideAccessChange}>
              <SelectTrigger className="w-52 h-8 text-xs">
                <SelectValue placeholder="选择策略" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="allow" className="text-xs">Allow（允许跨目录读写）</SelectItem>
                <SelectItem value="ask" className="text-xs">Ask（跨目录必须审批）</SelectItem>
                <SelectItem value="deny" className="text-xs">Deny（严禁越界访问）</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Terminal Command Auto Execution */}
          <div className="flex items-center justify-between p-4 hover:bg-muted/20 transition-colors">
            <div className="space-y-0.5 max-w-[65%]">
              <div className="text-xs font-medium text-foreground">
                Terminal Command Auto Execution（终端命令自动执行）
              </div>
              <p className="text-[11px] text-muted-foreground leading-relaxed">
                控制终端 Shell/Python 执行命令在运行前是否必须经过人工批准。
              </p>
            </div>
            <Select value={terminalAutoExec} onValueChange={handleTerminalExecChange}>
              <SelectTrigger className="w-52 h-8 text-xs">
                <SelectValue placeholder="选择执行策略" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ask" className="text-xs">Always Ask（总是人工审批）</SelectItem>
                <SelectItem value="proceed" className="text-xs">Always Proceed（自动直接放行）</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>

      {/* Agent Behavior 分组 */}
      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-foreground">Agent Behavior（智能体行为）</h3>
        <div className="rounded-xl border border-border/70 bg-card/40 p-4 hover:bg-muted/20 transition-colors flex items-center justify-between">
          <div className="space-y-0.5 max-w-[65%]">
            <div className="text-xs font-medium text-foreground">
              Artifact Review Policy（产物与文档审查）
            </div>
            <p className="text-[11px] text-muted-foreground leading-relaxed">
              智能体生成交付文档、IFC/CAD 工件或代码改动时是否要求审查确认。
            </p>
          </div>
          <Select value={artifactReviewPolicy} onValueChange={handleArtifactReviewChange}>
            <SelectTrigger className="w-52 h-8 text-xs">
              <SelectValue placeholder="选择审查策略" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="proceed" className="text-xs">Always Proceed（直接产出归档）</SelectItem>
              <SelectItem value="ask" className="text-xs">Always Ask（必须弹窗确认）</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Local Permissions 分组 */}
      <div className="space-y-3">
        <div className="space-y-0.5">
          <h3 className="text-sm font-semibold text-foreground">Local Permissions（本地权限规则）</h3>
          <p className="text-[11px] text-muted-foreground">
            工作区继承全局权限规则，可单独设置黑白名单。
          </p>
        </div>
        <div className="rounded-xl border border-border/70 bg-card/40 p-4 flex items-center justify-between">
          <div className="space-y-0.5">
            <div className="text-xs font-medium text-foreground flex items-center gap-1.5">
              <Lock className="h-3.5 w-3.5 text-muted-foreground" />
              <span>File Access Rules（文件读写路径白名单）</span>
            </div>
            <p className="text-[11px] text-muted-foreground">
              当前项目已授权根目录以及 out/ 产物缓存区，保护系统敏感目录。
            </p>
          </div>
          <Button
            size="sm"
            variant="outline"
            className="text-xs h-8"
            onClick={() => toast.info(`当前项目授权目录：${workspace.path}`)}
          >
            Open
          </Button>
        </div>
      </div>
    </div>
  )
}
