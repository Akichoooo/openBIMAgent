import React, { useState } from "react"
import { api } from "@/services/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Boxes,
  Layers,
  Download,
  Settings,
  Moon,
  Sun,
  ShieldAlert,
  FileText,
  PanelLeft,
  PanelBottom,
  PanelRight,
  Compass,
  Hammer,
  MessageSquare,
  Cpu,
  ShieldCheck,
  Shield,
  ChevronDown,
} from "lucide-react"
import { toast } from "sonner"

interface HeaderProps {
  activeSessionTitle?: string
  currentPlaybook?: string | null
  onDisciplineChange?: (playbook: string) => void
  viewMode: "3d" | "plan" | "prof" | "trace"
  onViewModeChange: (mode: "3d" | "plan" | "prof" | "trace") => void
  onOpenSettings: (tab?: string) => void
  onOpenAudit?: () => void
  isDark: boolean
  onToggleTheme: () => void
  converged?: boolean
  iterations?: number
  usedTokens?: number
  contextWindow?: number
  showLeftSidebar?: boolean
  onToggleLeftSidebar?: () => void
  showBottomTerminal?: boolean
  onToggleBottomTerminal?: () => void
  showRightChat?: boolean
  onToggleRightChat?: () => void
  chatFocusMode?: boolean
  onToggleChatFocus?: () => void
}

const DISCIPLINE_MAP: Record<string, { label: string; code: string }> = {
  municipal_utility: { label: "市政给排水", code: "GB 50289" },
  residential_building: { label: "住宅洋房", code: "GB 50016" },
  industrial_steel: { label: "工业钢结构", code: "GB 50017" },
  district_planning: { label: "园区规划", code: "GB 50180" },
}

export const Header: React.FC<HeaderProps> = ({
  activeSessionTitle = "未选择会话",
  currentPlaybook,
  onDisciplineChange,
  viewMode,
  onViewModeChange,
  onOpenSettings,
  onOpenAudit,
  isDark,
  onToggleTheme,
  converged,
  iterations,
  usedTokens = 0,
  contextWindow = 0,
  showLeftSidebar = true,
  onToggleLeftSidebar,
  showBottomTerminal = false,
  onToggleBottomTerminal,
  showRightChat = true,
  onToggleRightChat,
  chatFocusMode = false,
  onToggleChatFocus,
}) => {
  const [exporting, setExporting] = useState<string | null>(null)
  const [confirmHost, setConfirmHost] = useState<"blender" | "vectorworks" | null>(null)
  const [toolsetPreset, setToolsetPreset] = useState<string>("modeling")
  const [currentModel, setCurrentModel] = useState<string>("")

  React.useEffect(() => {
    const refreshToolset = () => {
      api.getToolset().then((res) => {
        if (res && res.preset) setToolsetPreset(res.preset)
      }).catch(() => {})
    }
    refreshToolset()
    window.addEventListener("wb-toolset-change", refreshToolset)

    api.getModelsSettings().then((res) => {
      const hasModels = (res?.providers || []).some((p) => (p.models || []).length > 0 && p.enabled !== false)
      if (hasModels && res?.current) {
        setCurrentModel(res.current)
      } else {
        setCurrentModel("")
      }
    }).catch(() => {
      setCurrentModel("")
    })

    return () => {
      window.removeEventListener("wb-toolset-change", refreshToolset)
    }
  }, [])

  const currentDiscipline = currentPlaybook && DISCIPLINE_MAP[currentPlaybook]
    ? DISCIPLINE_MAP[currentPlaybook]
    : { label: "市政给排水", code: "GB 50289" }

  // 真机导出：POST + confirm:true 过 prompt 策略门（HITL 人确认语义 = 本确认弹窗）
  const handleExport = async (host: "blender" | "vectorworks") => {
    setExporting(host)
    setConfirmHost(null)
    try {
      const res = await api.exportCad(host)
      if (res?.status === "success") {
        const receipt = res.receipt
        const detail =
          typeof receipt === "object" && receipt !== null
            ? receipt.path || receipt.file || receipt.summary || "已交付 CAD 宿主执行"
            : String(receipt || "已交付 CAD 宿主执行")
        toast.success(`导出 ${host === "blender" ? "Blender" : "Vectorworks"} 成功`, {
          description: String(detail).slice(0, 120),
        })
      } else {
        toast.error("导出被拒绝或失败", {
          description: String(res?.error || "未知错误").slice(0, 160),
        })
      }
    } catch (e: any) {
      toast.error("导出请求失败: " + e.message)
    } finally {
      setExporting(null)
    }
  }

  // 训练轨迹导出(SFT/DPO jsonl 下载):仅成功交付案例,后端 fail-closed
  const handleExportTraining = async (fmt: "sft" | "dpo") => {
    try {
      const res = await api.exportTraining(fmt)
      if (!res.count) {
        toast.info(`暂无可导出的 ${fmt.toUpperCase()} 轨迹`, {
          description: "仅成功交付案例可导出(fail-closed;DPO 需自愈迭代证据)",
        })
        return
      }
      const blob = new Blob([res.items.map((it: any) => JSON.stringify(it)).join("\n")], {
        type: "application/jsonl",
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `openbimagent_${fmt}_traces_${new Date().toISOString().slice(0, 10)}.jsonl`
      a.click()
      URL.revokeObjectURL(url)
      toast.success(`已导出 ${res.count} 条 ${fmt.toUpperCase()} 轨迹`)
    } catch (e: any) {
      toast.error("导出失败: " + e.message)
    }
  }

  const ctxRatio = contextWindow > 0 ? usedTokens / contextWindow : 0

  return (
    <>
    <header className="h-12 border-b border-border/70 bg-background/80 backdrop-blur-md px-3.5 flex items-center justify-between shrink-0 select-none z-20">
      {/* 左侧：Logo 与会话面包屑 */}
      <div className="flex items-center space-x-3 min-w-0">
        <div className="flex items-center space-x-2">
          <div className="w-7 h-7 rounded-lg bg-primary text-primary-foreground flex items-center justify-center font-bold text-xs shadow-sm">
            <Boxes className="h-4 w-4" />
          </div>
          <span className="font-semibold text-sm tracking-tight text-foreground hidden sm:inline">
            openBIMAgent
          </span>
        </div>

        <div className="h-4 w-[1px] bg-border/80" />

        <div className="flex items-center space-x-2 min-w-0">
          <span className="text-xs text-muted-foreground font-mono truncate max-w-[180px] md:max-w-xs">
            {typeof activeSessionTitle === "string" && activeSessionTitle !== "[object Object]"
              ? activeSessionTitle
              : "未选择会话"}
          </span>
          <div className="hidden lg:flex items-center space-x-1.5">
            {typeof converged === "boolean" && (
              <span
                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-mono border ${
                  converged
                    ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30"
                    : "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30"
                }`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    converged ? "bg-emerald-500" : "bg-amber-500"
                  }`}
                />
                {converged ? `已收敛 · ${iterations ?? 0} 轮迭代` : "求解自愈中"}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* 关键生成配置快速核验胶囊 (工作模式 · 生效规范) */}
      <div className="hidden lg:inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-neutral-100 dark:bg-neutral-800/80 border border-neutral-200/70 dark:border-neutral-700/70 text-xs font-medium select-none transition-colors">
        {/* 模式选择器 */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              className="flex items-center space-x-1 hover:text-foreground transition-colors cursor-pointer outline-none text-[11px]"
              title="切换当前工作交互模式 (Ask / Plan / Build)"
            >
              {toolsetPreset === "minimal" ? (
                <>
                  <MessageSquare className="h-3 w-3 text-sky-500" />
                  <span className="text-sky-600 dark:text-sky-400">Ask 答疑</span>
                </>
              ) : toolsetPreset === "full" ? (
                <>
                  <Hammer className="h-3 w-3 text-amber-500" />
                  <span className="text-amber-600 dark:text-amber-400">Build 建模</span>
                </>
              ) : (
                <>
                  <Compass className="h-3 w-3 text-violet-500" />
                  <span className="text-violet-600 dark:text-violet-400">Plan 规划</span>
                </>
              )}
              <ChevronDown className="h-2.5 w-2.5 text-muted-foreground/60 ml-0.5" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-52 p-1 text-xs">
            <div className="px-2 py-1 text-[10px] font-semibold text-muted-foreground">
              工作交互与执行模式
            </div>
            <DropdownMenuItem
              onClick={() => {
                setToolsetPreset("minimal")
                api.setToolset("minimal").then(() => {
                  window.dispatchEvent(new CustomEvent("wb-toolset-change"))
                }).catch(() => {})
                toast.success("已切换为 Ask 方案答疑模式 (只读安全)")
              }}
              className="gap-2 cursor-pointer"
            >
              <MessageSquare className="h-3.5 w-3.5 text-sky-500 shrink-0" />
              <div>
                <div className="font-medium">Ask 方案答疑</div>
                <div className="text-[10px] text-muted-foreground">只读分析、查阅规范与审图</div>
              </div>
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => {
                setToolsetPreset("modeling")
                api.setToolset("modeling").then(() => {
                  window.dispatchEvent(new CustomEvent("wb-toolset-change"))
                }).catch(() => {})
                toast.success("已切换为 Plan 规划推演模式 (推荐)")
              }}
              className="gap-2 cursor-pointer"
            >
              <Compass className="h-3.5 w-3.5 text-violet-500 shrink-0" />
              <div>
                <div className="font-medium">Plan 规划与追问 (推荐)</div>
                <div className="text-[10px] text-muted-foreground">方案推演，主动追问不确定参数</div>
              </div>
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => {
                setToolsetPreset("full")
                api.setToolset("full").then(() => {
                  window.dispatchEvent(new CustomEvent("wb-toolset-change"))
                }).catch(() => {})
                toast.success("已切换为 Build 直接建模模式 (敏捷自愈)")
              }}
              className="gap-2 cursor-pointer"
            >
              <Hammer className="h-3.5 w-3.5 text-amber-500 shrink-0" />
              <div>
                <div className="font-medium">Build 直接建模</div>
                <div className="text-[10px] text-muted-foreground">放样推演、碰撞检测与文件生成</div>
              </div>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        <span className="w-px h-2.5 bg-neutral-300 dark:bg-neutral-700" />

        {/* 工程规范领域选择器 */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              className="flex items-center space-x-1 text-sky-600 dark:text-sky-400 hover:text-sky-700 dark:hover:text-sky-300 transition-colors cursor-pointer outline-none text-[11px]"
              title="切换当前会话工程专业领域与国标规范"
            >
              <ShieldCheck className="h-3 w-3 shrink-0" />
              <span>{currentDiscipline.label}</span>
              <ChevronDown className="h-2.5 w-2.5 text-muted-foreground/60 ml-0.5" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-64 p-1 text-xs">
            <div className="px-2 py-1 text-[10px] font-semibold text-muted-foreground flex items-center justify-between">
              <span>工程专业领域 (Discipline)</span>
              <button
                onClick={() => onOpenSettings?.("rules")}
                className="text-[10px] text-primary hover:underline"
              >
                规则设置
              </button>
            </div>
            {Object.entries(DISCIPLINE_MAP).map(([key, disc]) => {
              const active = (currentPlaybook || "municipal_utility") === key
              return (
                <DropdownMenuItem
                  key={key}
                  onClick={() => {
                    onDisciplineChange?.(key)
                    window.dispatchEvent(new CustomEvent("wb-discipline-change", { detail: { playbook: key } }))
                    toast.success(`已切换工程领域: ${disc.label}`)
                  }}
                  className="gap-2 cursor-pointer"
                >
                  <ShieldCheck className={`h-3.5 w-3.5 shrink-0 ${active ? "text-sky-500" : "text-muted-foreground"}`} />
                  <div className="flex-1 min-w-0">
                    <div className={`font-medium ${active ? "text-foreground font-semibold" : ""}`}>
                      {disc.label} ({disc.code})
                    </div>
                    <div className="text-[10px] text-muted-foreground truncate">
                      {key === "municipal_utility" ? "雨污水管网、给排水管廊、检查井跌水" :
                       key === "residential_building" ? "民用建筑方案、防火疏散分区、净空合规" :
                       key === "industrial_steel" ? "门式钢架、工业厂房排架、吊车荷载" :
                       "居住区规划、日照间距、绿地率指标"}
                    </div>
                  </div>
                  {active && <span className="text-[10px] text-sky-500 font-mono">当前</span>}
                </DropdownMenuItem>
              )
            })}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* 中间：视口与工作台模式切换胶囊 */}
      <div className="flex items-center bg-muted/50 p-0.5 rounded-lg border border-border/60">
        <button
          onClick={() => {
            if (chatFocusMode) onToggleChatFocus?.()
            onViewModeChange("3d")
          }}
          className={`px-3 py-1 rounded-md text-xs font-medium transition-all ${
            !chatFocusMode && viewMode === "3d"
              ? "bg-background text-foreground shadow-sm font-semibold"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          3D 视口
        </button>
        <button
          onClick={() => {
            if (chatFocusMode) onToggleChatFocus?.()
            onViewModeChange("plan")
          }}
          className={`px-3 py-1 rounded-md text-xs font-medium transition-all ${
            !chatFocusMode && viewMode === "plan"
              ? "bg-background text-foreground shadow-sm font-semibold"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          2D 平面
        </button>
        <button
          onClick={() => {
            if (chatFocusMode) onToggleChatFocus?.()
            onViewModeChange("prof")
          }}
          className={`px-3 py-1 rounded-md text-xs font-medium transition-all ${
            !chatFocusMode && viewMode === "prof"
              ? "bg-background text-foreground shadow-sm font-semibold"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          纵断面
        </button>
        <button
          onClick={() => {
            if (chatFocusMode) onToggleChatFocus?.()
            onViewModeChange("trace")
          }}
          className={`px-3 py-1 rounded-md text-xs font-medium transition-all ${
            !chatFocusMode && viewMode === "trace"
              ? "bg-background text-foreground shadow-sm font-semibold"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          证据轨迹
        </button>
      </div>

      {/* 右侧：导出与全局控制 */}
      <div className="flex items-center space-x-1.5">
        {/* Context 预算条(pi-mono/Claude Code 范式):占用百分比变色,点击触发 /compact */}
        {contextWindow > 0 && usedTokens > 0 && (
          <button
            type="button"
            onClick={() => window.dispatchEvent(new CustomEvent("wb-command", { detail: { cmd: "/compact" } }))}
            className="hidden md:flex items-center gap-1.5 h-8 px-2 rounded-lg border border-border/60 hover:bg-muted/60"
            title={`上下文占用 ${usedTokens}/${contextWindow} tokens · 点击触发 /compact 压缩`}
          >
            <div className="w-14 h-1.5 rounded-full bg-muted overflow-hidden">
              <div
                className={`h-full rounded-full ${
                  ctxRatio > 0.8 ? "bg-rose-500" : ctxRatio > 0.6 ? "bg-amber-500" : "bg-emerald-500"
                }`}
                style={{ width: `${Math.min(100, ctxRatio * 100)}%` }}
              />
            </div>
            <span className="text-[10px] font-mono text-muted-foreground">{Math.round(ctxRatio * 100)}%</span>
          </button>
        )}

        {/* 导出 CAD 菜单 */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 px-2.5 text-xs gap-1.5">
              <Download className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="hidden md:inline">导出工件</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <DropdownMenuItem
              onClick={() => setConfirmHost("blender")}
              disabled={exporting !== null}
              className="text-xs cursor-pointer"
            >
              <Layers className="h-3.5 w-3.5 mr-2 text-amber-500" />
              Blender-BIM 真机导出
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => setConfirmHost("vectorworks")}
              disabled={exporting !== null}
              className="text-xs cursor-pointer"
            >
              <Boxes className="h-3.5 w-3.5 mr-2 text-sky-500" />
              Vectorworks 真机导出
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => handleExportTraining("sft")} className="text-xs cursor-pointer">
              <FileText className="h-3.5 w-3.5 mr-2 text-emerald-500" />
              导出 SFT 微调轨迹
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => handleExportTraining("dpo")} className="text-xs cursor-pointer">
              <FileText className="h-3.5 w-3.5 mr-2 text-violet-500" />
              导出 DPO 偏好对
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        {/* 审计日志入口 */}
        {onOpenAudit && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onOpenAudit}
            className="h-8 w-8 p-0 rounded-lg text-muted-foreground hover:text-foreground"
            title="审计日志(安全操作留痕)"
          >
            <FileText className="h-4 w-4" />
          </Button>
        )}

        {/* VS Code 范式三栏布局控制器：左侧栏 / 底部控制台 / 右侧栏 (对标 VS Code 顶栏控制器) */}
        <div className="flex items-center bg-muted/60 p-0.5 rounded-lg border border-border/60">
          <Button
            variant="ghost"
            size="sm"
            onClick={onToggleLeftSidebar}
            className={`h-7 w-7 p-0 rounded-md transition-all ${
              showLeftSidebar
                ? "bg-background text-foreground shadow-xs"
                : "text-muted-foreground hover:text-foreground"
            }`}
            title={showLeftSidebar ? "收起左侧栏 (任务/会话)" : "展开左侧栏 (任务/会话)"}
          >
            <PanelLeft className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onToggleBottomTerminal}
            className={`h-7 w-7 p-0 rounded-md transition-all ${
              showBottomTerminal
                ? "bg-background text-foreground shadow-xs"
                : "text-muted-foreground hover:text-foreground"
            }`}
            title={showBottomTerminal ? "收起底部终端 (Runner Terminal)" : "展开底部终端 (Runner Terminal)"}
          >
            <PanelBottom className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onToggleRightChat}
            className={`h-7 w-7 p-0 rounded-md transition-all ${
              showRightChat
                ? "bg-background text-foreground shadow-xs"
                : "text-muted-foreground hover:text-foreground"
            }`}
            title={showRightChat ? "收起右侧智能体对话" : "展开右侧智能体对话"}
          >
            <PanelRight className="h-3.5 w-3.5" />
          </Button>
        </div>

        {/* 设置面板胶囊入口 */}
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onOpenSettings?.("appearance")}
          className="h-8 px-2.5 rounded-full text-xs text-muted-foreground hover:text-foreground hover:bg-muted/80 transition-colors gap-1.5"
          title="打开系统设置 (Cursor / OpenDesign 范式)"
        >
          <Settings className="h-4 w-4" />
          <span className="hidden sm:inline">设置</span>
        </Button>
      </div>
    </header>

    {/* 真机导出 HITL 确认门 */}
    <Dialog open={confirmHost !== null} onOpenChange={(o) => !o && setConfirmHost(null)}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="text-base flex items-center gap-2">
            <ShieldAlert className="h-4 w-4 text-amber-500" />
            确认真机导出
          </DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground leading-relaxed py-1">
          该操作将对当前演示场景执行自愈求解，并把结果真实写入{" "}
          <span className="font-medium text-foreground">
            {confirmHost === "blender" ? "Blender（headless 执行计划）" : "Vectorworks（宿主 runner）"}
          </span>
          。此为受控写盘动作，确认后立即执行。
        </p>
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => setConfirmHost(null)}>
            取消
          </Button>
          <Button
            size="sm"
            disabled={exporting !== null}
            onClick={() => confirmHost && handleExport(confirmHost)}
          >
            {exporting ? "正在导出..." : "确认导出"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    </>
  )
}
