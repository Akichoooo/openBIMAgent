import React from "react"
import {
  Loader2,
  AlertTriangle,
  Terminal,
  ShieldCheck,
  Cpu,
  FolderGit2,
  CheckCircle2,
} from "lucide-react"

interface StatusBarProps {
  sessionTitle?: string
  playbook?: string
  workspace?: string
  currentModel?: string
  running?: boolean
  approvalsPending?: number
  usedTokens?: number
  contextWindow?: number
  showBottomTerminal?: boolean
  onToggleBottomTerminal?: () => void
  onOpenAudit?: () => void
  onOpenSettings?: () => void
}

/**
 * VS Code 范式全局底部状态栏 (高度 24px)
 * 提供智能体状态中枢感知 (就绪/运算/审批)、当前会话、默认模型、Tokens 预算、终端快捷切换与审计留痕
 */
export const StatusBar: React.FC<StatusBarProps> = ({
  sessionTitle = "未选择会话",
  playbook,
  workspace,
  currentModel,
  running = false,
  approvalsPending = 0,
  usedTokens = 0,
  contextWindow = 0,
  showBottomTerminal = false,
  onToggleBottomTerminal,
  onOpenAudit,
  onOpenSettings,
}) => {
  return (
    <footer className="h-6 shrink-0 border-t border-border/70 bg-card/80 backdrop-blur-sm text-muted-foreground text-[11px] select-none flex items-center justify-between px-2.5 z-30 font-sans">
      {/* 左侧：智能体实时状态、会话与工作区 */}
      <div className="flex items-center space-x-2.5 min-w-0">
        {/* Agent 状态感知灯 */}
        {running ? (
          <span className="flex items-center gap-1.5 text-sky-500 dark:text-sky-400 font-medium shrink-0">
            <Loader2 className="h-3 w-3 animate-spin" />
            <span>智能体求解运算中...</span>
          </span>
        ) : approvalsPending > 0 ? (
          <span className="flex items-center gap-1.5 text-amber-500 dark:text-amber-400 font-semibold shrink-0 animate-pulse">
            <AlertTriangle className="h-3 w-3" />
            <span>待人机审批 ({approvalsPending})</span>
          </span>
        ) : (
          <span className="flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400 font-medium shrink-0">
            <span className="h-2 w-2 rounded-full bg-emerald-500 shrink-0" />
            <span>就绪 (Idle)</span>
          </span>
        )}

        <span className="text-border/80">|</span>

        {/* 当前工作区 */}
        <span className="flex items-center gap-1 text-muted-foreground/90 shrink-0 truncate max-w-[140px]">
          <FolderGit2 className="h-3 w-3 text-muted-foreground/70 shrink-0" />
          <span className="truncate">{workspace || "默认工程"}</span>
        </span>

        <span className="text-border/80 hidden sm:inline">|</span>

        {/* 当前会话标题 */}
        <span className="truncate max-w-[220px] text-foreground/80 hidden sm:inline">
          {sessionTitle}
        </span>

        {playbook && (
          <span className="text-[10px] px-1.5 py-0.2 rounded bg-muted text-muted-foreground font-mono hidden md:inline">
            {playbook}
          </span>
        )}
      </div>

      {/* 右侧：模型规格、Token 用量预算、终端切换与安全审计 */}
      <div className="flex items-center space-x-2.5 shrink-0">
        {/* 当前模型 */}
        {currentModel && (
          <button
            type="button"
            onClick={onOpenSettings}
            className="flex items-center gap-1 hover:text-foreground transition-colors font-mono text-[10.5px] px-1.5 py-0.5 rounded hover:bg-muted/70 cursor-pointer"
            title="当前默认模型（点击前往模型配置）"
          >
            <Cpu className="h-3 w-3 text-primary/80" />
            <span>{currentModel}</span>
          </button>
        )}

        {/* Token 预算百分比 */}
        {contextWindow > 0 && (
          <span
            className="text-[10.5px] font-mono text-muted-foreground/80 hidden md:inline"
            title={`上下文预算：已用 ${usedTokens.toLocaleString()} / 上限 ${contextWindow.toLocaleString()} Tokens`}
          >
            {Math.round((usedTokens / 1000) * 10) / 10}k / {Math.round(contextWindow / 1000)}k (
            {Math.round((usedTokens / Math.max(contextWindow, 1)) * 100)}%)
          </span>
        )}

        <span className="text-border/80">|</span>

        {/* 底部终端快捷开关 */}
        <button
          type="button"
          onClick={onToggleBottomTerminal}
          className={`flex items-center gap-1 px-1.5 py-0.5 rounded transition-all cursor-pointer ${
            showBottomTerminal
              ? "bg-background text-foreground shadow-xs font-semibold"
              : "hover:text-foreground hover:bg-muted/70 text-muted-foreground"
          }`}
          title="展开 / 收起中央底部终端 (VS Code 范式)"
        >
          <Terminal className="h-3 w-3 text-emerald-500" />
          <span>终端</span>
        </button>

        {/* 审计日志留痕 */}
        {onOpenAudit && (
          <button
            type="button"
            onClick={onOpenAudit}
            className="flex items-center gap-1 hover:text-foreground px-1.5 py-0.5 rounded hover:bg-muted/70 transition-colors cursor-pointer text-muted-foreground"
            title="查看安全操作与审批审计留痕"
          >
            <ShieldCheck className="h-3 w-3 text-primary/80" />
            <span className="hidden sm:inline">审计</span>
          </button>
        )}
      </div>
    </footer>
  )
}
