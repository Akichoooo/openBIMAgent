import React, { useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog"
import { GeneralTab } from "./GeneralTab"
import { AppearanceTab } from "./AppearanceTab"
import { ModelsTab } from "./ModelsTab"
import { ToolsetTab } from "./ToolsetTab"
import { MemoryTab } from "./MemoryTab"
import { SkillsTab } from "./SkillsTab"
import { ArchiveTab } from "./ArchiveTab"
import { UsageTab } from "./UsageTab"
import { RulesTab } from "./RulesTab"
import { McpTab } from "./McpTab"
import { PluginsTab } from "./PluginsTab"
import { UploadsTab } from "./UploadsTab"
import { ProjectSettingsTab } from "./ProjectSettingsTab"
import { api, WorkspaceItem } from "@/services/api"
import {
  Settings,
  Palette,
  Cpu,
  Wrench,
  Brain,
  BookOpen,
  Archive,
  BarChart3,
  ShieldCheck,
  Cable,
  Upload,
  Folder,
  ChevronLeft,
  type LucideIcon,
} from "lucide-react"

interface SettingsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  initialTab?: string
  initialModelEdit?: string
  onClearInitialModelEdit?: () => void
  onSelectSession?: (sessionId: string) => void
}

type TabKey =
  | "general"
  | "appearance"
  | "models"
  | "toolset"
  | "memory"
  | "skills"
  | "mcp"
  | "plugins"
  | "archive"
  | "usage"
  | "rules"
  | "uploads"

interface NavItem {
  id: TabKey
  label: string
  icon: LucideIcon
}

interface NavGroup {
  group?: string
  items: NavItem[]
}

// 规整后的认知三大支柱体系
const navGroups: NavGroup[] = [
  {
    items: [
      { id: "general", label: "常规设置", icon: Settings },
      { id: "appearance", label: "外观", icon: Palette },
      { id: "usage", label: "资源用量", icon: BarChart3 },
    ],
  },
  {
    group: "能力与工具",
    items: [
      { id: "models", label: "模型服务", icon: Cpu },
      { id: "mcp", label: "MCP 工具", icon: Cable },
      { id: "skills", label: "技能目录 (Skills)", icon: BookOpen },
      { id: "toolset", label: "执行模式与权限", icon: Wrench },
    ],
  },
  {
    group: "工程规程与数据",
    items: [
      { id: "rules", label: "工程规范与刚性约束", icon: ShieldCheck },
      { id: "memory", label: "记忆中心", icon: Brain },
      { id: "archive", label: "存储管理", icon: Archive },
      { id: "uploads", label: "工程图纸与附件", icon: Upload },
    ],
  },
]

export const SettingsDialog: React.FC<SettingsDialogProps> = ({
  open,
  onOpenChange,
  initialTab = "general",
  initialModelEdit,
  onClearInitialModelEdit,
  onSelectSession,
}) => {
  const [activeTab, setActiveTab] = useState<TabKey>((initialTab as TabKey) || "general")
  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([])
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null)

  React.useEffect(() => {
    if (initialTab && open) {
      setActiveTab(initialTab as TabKey)
      setSelectedWorkspaceId(null)
    }
  }, [initialTab, open])

  const loadWorkspaces = async () => {
    try {
      const res = await api.listWorkspaces()
      setWorkspaces(res.items || [])
    } catch (e) {
      console.error(e)
    }
  }

  React.useEffect(() => {
    if (open) loadWorkspaces()
  }, [open])

  const renderContent = () => {
    if (selectedWorkspaceId) {
      const ws = workspaces.find((w) => w.id === selectedWorkspaceId)
      if (ws) {
        return <ProjectSettingsTab workspace={ws} onUpdated={loadWorkspaces} />
      }
    }

    switch (activeTab) {
      case "general":
        return <GeneralTab />
      case "appearance":
        return <AppearanceTab />
      case "models":
        return (
          <ModelsTab
            initialEditModel={initialModelEdit}
            onClearInitialEditModel={onClearInitialModelEdit}
          />
        )
      case "toolset":
        return <ToolsetTab />
      case "memory":
        return <MemoryTab />
      case "skills":
        return <SkillsTab />
      case "mcp":
        return <McpTab />
      case "plugins":
        return <PluginsTab />
      case "archive":
        return (
          <ArchiveTab
            onSelectSession={onSelectSession}
            onCloseSettings={() => onOpenChange(false)}
          />
        )
      case "usage":
        return <UsageTab />
      case "rules":
        return <RulesTab />
      case "uploads":
        return <UploadsTab />
      default:
        return <GeneralTab />
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[96vw] w-[95vw] h-[92vh] max-h-[92vh] p-0 gap-0 overflow-hidden flex bg-white dark:bg-neutral-950 border border-neutral-200/80 dark:border-neutral-800 shadow-2xl rounded-2xl">
        <DialogTitle className="sr-only">控制台与设置</DialogTitle>

        {/* 左侧语义导航栏 (1:1 对齐小浣熊 Box-Agent 布局) */}
        <div className="w-56 shrink-0 border-r border-neutral-200/70 dark:border-neutral-800 bg-[#fbfbfd] dark:bg-neutral-900/40 p-3.5 flex flex-col justify-between select-none overflow-y-auto">
          <div className="space-y-4">
            {/* 顶部返回应用按钮 (对齐截图左上角) */}
            <button
              onClick={() => onOpenChange(false)}
              className="flex items-center space-x-1 px-1 py-1 text-xs font-medium text-neutral-600 dark:text-neutral-400 hover:text-neutral-900 dark:hover:text-neutral-100 transition-colors"
              title="返回工作台视口"
            >
              <ChevronLeft className="h-4 w-4" />
              <span>返回应用</span>
            </button>

            {/* 导航分组列表 */}
            <div className="space-y-4">
              {navGroups.map((grp, gIdx) => (
                <div key={gIdx} className="space-y-1">
                  {grp.group && (
                    <div className="px-2.5 py-1 text-[11px] text-neutral-400 dark:text-neutral-500 font-normal">
                      {grp.group}
                    </div>
                  )}
                  <div className="space-y-0.5">
                    {grp.items.map((item) => {
                      const Icon = item.icon
                      const isActive = !selectedWorkspaceId && activeTab === item.id
                      return (
                        <button
                          key={item.id}
                          onClick={() => {
                            setSelectedWorkspaceId(null)
                            setActiveTab(item.id)
                          }}
                          className={`w-full flex items-center space-x-2.5 px-3 py-2 rounded-xl text-xs transition-all text-left ${
                            isActive
                              ? "border border-violet-300 dark:border-violet-700 bg-violet-50/70 dark:bg-violet-950/40 text-violet-700 dark:text-violet-300 font-medium shadow-2xs"
                              : "border border-transparent text-neutral-600 dark:text-neutral-400 hover:text-neutral-900 dark:hover:text-neutral-100 hover:bg-neutral-100/60 dark:hover:bg-neutral-800/40"
                          }`}
                        >
                          <Icon
                            className={`h-4 w-4 shrink-0 ${
                              isActive
                                ? "text-violet-600 dark:text-violet-400"
                                : "text-neutral-500 dark:text-neutral-400"
                            }`}
                          />
                          <span className="truncate">{item.label}</span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              ))}

              {/* 工程工作区 */}
              {workspaces.length > 0 && (
                <div className="space-y-1 pt-2 border-t border-neutral-200/50 dark:border-neutral-800/50">
                  <div className="px-2.5 py-1 text-[11px] text-neutral-400 dark:text-neutral-500 font-normal">
                    工程工作区
                  </div>
                  <div className="space-y-0.5">
                    {workspaces.map((ws) => {
                      const isWsActive = selectedWorkspaceId === ws.id
                      return (
                        <button
                          key={ws.id}
                          onClick={() => setSelectedWorkspaceId(ws.id)}
                          className={`w-full flex items-center space-x-2.5 px-3 py-2 rounded-xl text-xs transition-all ${
                            isWsActive
                              ? "border border-violet-300 dark:border-violet-700 bg-violet-50/70 dark:bg-violet-950/40 text-violet-700 dark:text-violet-300 font-medium shadow-2xs"
                              : "border border-transparent text-neutral-600 dark:text-neutral-400 hover:text-neutral-900 dark:hover:text-neutral-100 hover:bg-neutral-100/60 dark:hover:bg-neutral-800/40"
                          }`}
                        >
                          <Folder className="h-4 w-4 shrink-0 text-neutral-500" />
                          <span className="truncate">{ws.name}</span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="px-2 pt-3 border-t border-neutral-200/50 dark:border-neutral-800/50 text-[10px] text-neutral-400 flex items-center justify-between">
            <span>openBIMAgent v1.0</span>
            <kbd className="px-1.5 py-0.5 rounded bg-neutral-100 dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 text-[9px] font-mono">
              ESC
            </kbd>
          </div>
        </div>

        {/* 右侧主内容区 */}
        <div className="flex-1 flex flex-col min-w-0 bg-white dark:bg-neutral-950 overflow-hidden relative">
          <div className="flex-1 overflow-y-auto px-8 py-7 min-h-0">
            {renderContent()}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
