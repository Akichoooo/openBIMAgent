import React, { useState, useEffect } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  ShieldCheck,
  ShieldAlert,
  Terminal,
  Cpu,
  Search,
  Wrench,
  CheckCircle2,
  Folder,
  Layers,
  Sparkles,
  ExternalLink,
  RotateCw,
  Sliders,
  Check,
} from "lucide-react"
import { toast } from "sonner"
import { api } from "@/services/api"

export function GeneralTab() {
  // 沙箱设置与权限 (对标图三)
  const [sandboxScope, setSandboxScope] = useState<string>(() => {
    return localStorage.getItem("wb_sandbox_scope") || "restricted"
  })
  const [defaultPerm, setDefaultPerm] = useState<boolean>(() => {
    return localStorage.getItem("wb_perm_default") !== "false"
  })
  const [fullAccess, setFullAccess] = useState<boolean>(() => {
    return localStorage.getItem("wb_perm_full") === "true"
  })

  // 常规运行环境 (对标图二)
  const [defaultEditor, setDefaultEditor] = useState<string>(() => {
    return localStorage.getItem("wb_default_editor") || "vscode"
  })
  const [agentEnv, setAgentEnv] = useState<string>(() => {
    return localStorage.getItem("wb_agent_env") || "native"
  })
  const [shell, setShell] = useState<string>(() => {
    return localStorage.getItem("wb_shell") || "powershell"
  })
  const [terminalPosition, setTerminalPosition] = useState<"bottom" | "right">(() => {
    return (localStorage.getItem("wb_terminal_pos") as any) || "bottom"
  })

  // 依赖项与诊断 (对标图三)
  const [kernelDepsEnabled, setKernelDepsEnabled] = useState<boolean>(true)
  const [diagnosing, setDiagnosing] = useState(false)
  const [reloading, setReloading] = useState(false)

  // 网页搜索与交互 (对标图三)
  const [webSearchMode, setWebSearchMode] = useState<string>(() => {
    return localStorage.getItem("wb_web_search") || "cached"
  })
  const [verbosity, setVerbosity] = useState<string>(() => {
    return localStorage.getItem("wb_verbosity") || "default"
  })
  const [reasoningSummary, setReasoningSummary] = useState<string>(() => {
    return localStorage.getItem("wb_reasoning_summary") || "auto"
  })
  const [sendShortcut, setSendShortcut] = useState<string>(() => {
    return localStorage.getItem("wb_send_shortcut") || "enter"
  })
  const [followupMode, setFollowupMode] = useState<string>(() => {
    return localStorage.getItem("wb_followup_mode") || "queue"
  })
  const [showCtxUsage, setShowCtxUsage] = useState<boolean>(() => {
    return localStorage.getItem("wb_show_ctx_usage") !== "false"
  })

  const saveSetting = (key: string, val: string) => {
    localStorage.setItem(key, val)
  }

  const runDiagnostics = async () => {
    setDiagnosing(true)
    try {
      const [hosts, toolset] = await Promise.all([
        api.listHosts().catch(() => []),
        api.getToolset().catch(() => ({ preset: "modeling" })),
      ])
      const blenderStatus = hosts.find((h) => h.id.includes("blender"))?.status || "未连接"
      const vwStatus = hosts.find((h) => h.id.includes("vectorworks"))?.status || "未连接"

      toast.success("工作空间与微内核诊断完成", {
        description: `几何微内核: 就绪 · 模式: ${toolset.preset} · Blender: ${blenderStatus} · Vectorworks: ${vwStatus}`,
      })
    } catch (e: any) {
      toast.error("诊断失败: " + e.message)
    } finally {
      setDiagnosing(false)
    }
  }

  const handleResetWorkspace = async () => {
    setReloading(true)
    try {
      await api.listHosts()
      toast.success("已重置并重新加载工程微内核与工具链")
    } catch (e: any) {
      toast.error("重新加载失败: " + e.message)
    } finally {
      setReloading(false)
    }
  }

  return (
    <div className="space-y-6 pb-8 text-neutral-800 dark:text-neutral-200">
      {/* 1. 权限与沙盒设置 (对齐图二、图三首区) */}
      <section className="space-y-3">
        <h3 className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 uppercase tracking-wider px-1">
          权限与沙盒安全
        </h3>
        <Card className="border-neutral-200/80 dark:border-neutral-800 bg-white/60 dark:bg-neutral-900/50 shadow-xs">
          <CardContent className="divide-y divide-neutral-200/60 dark:divide-neutral-800/60 p-0">
            {/* 沙盒设置 */}
            <div className="flex items-center justify-between p-4 hover:bg-neutral-50/50 dark:hover:bg-neutral-800/20 transition-colors">
              <div className="space-y-0.5 max-w-[65%]">
                <div className="text-sm font-medium">沙盒设置</div>
                <div className="text-xs text-neutral-500 dark:text-neutral-400">
                  选择 openBIMAgent 运行命令与执行工具时的权限范围
                </div>
              </div>
              <Select
                value={sandboxScope}
                onValueChange={(v) => {
                  setSandboxScope(v)
                  saveSetting("wb_sandbox_scope", v)
                  toast.success(`沙盒模式已更新为: ${v === "readonly" ? "只读" : v === "restricted" ? "工作区受限" : "完全访问"}`)
                }}
              >
                <SelectTrigger className="w-[140px] h-8 text-xs bg-neutral-100/70 dark:bg-neutral-800/70">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="readonly" className="text-xs">只读</SelectItem>
                  <SelectItem value="restricted" className="text-xs">工作区受限</SelectItem>
                  <SelectItem value="full" className="text-xs">完全访问</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* 默认权限 */}
            <div className="flex items-center justify-between p-4 hover:bg-neutral-50/50 dark:hover:bg-neutral-800/20 transition-colors">
              <div className="space-y-0.5 max-w-[70%]">
                <div className="text-sm font-medium">默认权限</div>
                <div className="text-xs text-neutral-500 dark:text-neutral-400 leading-relaxed">
                  默认情况下，openBIMAgent 可以读取和编辑其工作空间中的文件。需要时，它可以请求额外访问权限。
                </div>
              </div>
              <Switch
                checked={defaultPerm}
                onCheckedChange={(c) => {
                  setDefaultPerm(c)
                  saveSetting("wb_perm_default", String(c))
                }}
              />
            </div>

            {/* 完整访问权限 */}
            <div className="flex items-center justify-between p-4 hover:bg-neutral-50/50 dark:hover:bg-neutral-800/20 transition-colors">
              <div className="space-y-0.5 max-w-[70%]">
                <div className="text-sm font-medium">完整访问权限</div>
                <div className="text-xs text-neutral-500 dark:text-neutral-400 leading-relaxed">
                  当 openBIMAgent 以完整访问权限运行时，它无需你的批准即可编写工程文件，并运行可访问网络的求解命令。
                </div>
              </div>
              <Switch
                checked={fullAccess}
                onCheckedChange={(c) => {
                  setFullAccess(c)
                  saveSetting("wb_perm_full", String(c))
                  if (c) {
                    toast.warning("已启用完整访问权限：Agent 可自主写入工程目录")
                  }
                }}
              />
            </div>
          </CardContent>
        </Card>
      </section>

      {/* 2. 常规运行环境 (对齐图二) */}
      <section className="space-y-3">
        <h3 className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 uppercase tracking-wider px-1">
          常规
        </h3>
        <Card className="border-neutral-200/80 dark:border-neutral-800 bg-white/60 dark:bg-neutral-900/50 shadow-xs">
          <CardContent className="divide-y divide-neutral-200/60 dark:divide-neutral-800/60 p-0">
            {/* 默认文件与 CAD 打开位置 */}
            <div className="flex items-center justify-between p-4 hover:bg-neutral-50/50 dark:hover:bg-neutral-800/20 transition-colors">
              <div className="space-y-0.5 max-w-[65%]">
                <div className="text-sm font-medium">默认 CAD 与模型打开位置</div>
                <div className="text-xs text-neutral-500 dark:text-neutral-400">
                  默认打开 CAD 脚本与装配工件的宿主工具
                </div>
              </div>
              <Select
                value={defaultEditor}
                onValueChange={(v) => {
                  setDefaultEditor(v)
                  saveSetting("wb_default_editor", v)
                  toast.success(`默认 CAD 打开位置已设为: ${v}`)
                }}
              >
                <SelectTrigger className="w-[170px] h-8 text-xs bg-neutral-100/70 dark:bg-neutral-800/70">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="vscode" className="text-xs">VS Code (OpenSCAD)</SelectItem>
                  <SelectItem value="blender" className="text-xs">Blender (Bpy)</SelectItem>
                  <SelectItem value="vectorworks" className="text-xs">Vectorworks</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* 智能体环境 */}
            <div className="flex items-center justify-between p-4 hover:bg-neutral-50/50 dark:hover:bg-neutral-800/20 transition-colors">
              <div className="space-y-0.5 max-w-[65%]">
                <div className="text-sm font-medium">智能体环境</div>
                <div className="text-xs text-neutral-500 dark:text-neutral-400">
                  选择智能体在宿主操作系统上的执行环境
                </div>
              </div>
              <Select
                value={agentEnv}
                onValueChange={(v) => {
                  setAgentEnv(v)
                  saveSetting("wb_agent_env", v)
                }}
              >
                <SelectTrigger className="w-[140px] h-8 text-xs bg-neutral-100/70 dark:bg-neutral-800/70">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="native" className="text-xs">Windows 原生</SelectItem>
                  <SelectItem value="wsl" className="text-xs">WSL</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* 集成终端 Shell */}
            <div className="flex items-center justify-between p-4 hover:bg-neutral-50/50 dark:hover:bg-neutral-800/20 transition-colors">
              <div className="space-y-0.5 max-w-[65%]">
                <div className="text-sm font-medium">集成终端 Shell</div>
                <div className="text-xs text-neutral-500 dark:text-neutral-400">
                  选择在底部终端面板中打开的 Shell
                </div>
              </div>
              <Select
                value={shell}
                onValueChange={(v) => {
                  setShell(v)
                  saveSetting("wb_shell", v)
                }}
              >
                <SelectTrigger className="w-[140px] h-8 text-xs bg-neutral-100/70 dark:bg-neutral-800/70">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="powershell" className="text-xs">PowerShell</SelectItem>
                  <SelectItem value="cmd" className="text-xs">Command Prompt</SelectItem>
                  <SelectItem value="bash" className="text-xs">Git Bash</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* 默认终端位置 */}
            <div className="flex items-center justify-between p-4 hover:bg-neutral-50/50 dark:hover:bg-neutral-800/20 transition-colors">
              <div className="space-y-0.5 max-w-[65%]">
                <div className="text-sm font-medium">默认终端位置</div>
                <div className="text-xs text-neutral-500 dark:text-neutral-400">
                  选择终端快捷键和环境操作在何处打开终端标签页
                </div>
              </div>
              <div className="inline-flex rounded-lg border border-neutral-200 dark:border-neutral-800 p-0.5 bg-neutral-100/80 dark:bg-neutral-800/80 text-xs">
                <button
                  type="button"
                  onClick={() => {
                    setTerminalPosition("bottom")
                    saveSetting("wb_terminal_pos", "bottom")
                  }}
                  className={`px-3 py-1 rounded-md transition-all font-medium ${
                    terminalPosition === "bottom"
                      ? "bg-white dark:bg-neutral-900 text-foreground shadow-xs"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  底部
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setTerminalPosition("right")
                    saveSetting("wb_terminal_pos", "right")
                  }}
                  className={`px-3 py-1 rounded-md transition-all font-medium ${
                    terminalPosition === "right"
                      ? "bg-white dark:bg-neutral-900 text-foreground shadow-xs"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  右侧
                </button>
              </div>
            </div>
          </CardContent>
        </Card>
      </section>

      {/* 3. 工作空间与几何依赖项 (对齐图三) */}
      <section className="space-y-3">
        <h3 className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 uppercase tracking-wider px-1">
          工作空间依赖项
        </h3>
        <Card className="border-neutral-200/80 dark:border-neutral-800 bg-white/60 dark:bg-neutral-900/50 shadow-xs">
          <CardContent className="divide-y divide-neutral-200/60 dark:divide-neutral-800/60 p-0">
            {/* 几何求解内核依赖 */}
            <div className="flex items-center justify-between p-4 hover:bg-neutral-50/50 dark:hover:bg-neutral-800/20 transition-colors">
              <div className="space-y-0.5 max-w-[70%]">
                <div className="text-sm font-medium">openBIMAgent 几何求解内核依赖</div>
                <div className="text-xs text-neutral-500 dark:text-neutral-400">
                  允许 Agent 提供随附的 Python 3.11、NetworkX 水力图求解及 OpenSCAD 依赖
                </div>
              </div>
              <Switch
                checked={kernelDepsEnabled}
                onCheckedChange={setKernelDepsEnabled}
              />
            </div>

            {/* 诊断工作空间 */}
            <div className="flex items-center justify-between p-4 hover:bg-neutral-50/50 dark:hover:bg-neutral-800/20 transition-colors">
              <div className="space-y-0.5 max-w-[70%]">
                <div className="text-sm font-medium">诊断 工作空间中的问题</div>
                <div className="text-xs text-neutral-500 dark:text-neutral-400">
                  检查当前捆绑包、CAD 外部驱动并记录诊断日志
                </div>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={runDiagnostics}
                disabled={diagnosing}
                className="h-8 text-xs gap-1.5"
              >
                <Search className={`h-3.5 w-3.5 ${diagnosing ? "animate-spin" : ""}`} />
                <span>{diagnosing ? "诊断中..." : "诊断"}</span>
              </Button>
            </div>

            {/* 重置并重新加载 */}
            <div className="flex items-center justify-between p-4 hover:bg-neutral-50/50 dark:hover:bg-neutral-800/20 transition-colors">
              <div className="space-y-0.5 max-w-[70%]">
                <div className="text-sm font-medium">重置并重新加载工具链</div>
                <div className="text-xs text-neutral-500 dark:text-neutral-400">
                  重新加载工程规则库、微内核几何管网求解器及外部 MCP 接口
                </div>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={handleResetWorkspace}
                disabled={reloading}
                className="h-8 text-xs gap-1.5"
              >
                <RotateCw className={`h-3.5 w-3.5 ${reloading ? "animate-spin" : ""}`} />
                <span>重新加载</span>
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>

      {/* 4. 智能体交互与搜索 (对齐图二、图三) */}
      <section className="space-y-3">
        <h3 className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 uppercase tracking-wider px-1">
          交互与搜索
        </h3>
        <Card className="border-neutral-200/80 dark:border-neutral-800 bg-white/60 dark:bg-neutral-900/50 shadow-xs">
          <CardContent className="divide-y divide-neutral-200/60 dark:divide-neutral-800/60 p-0">
            {/* 网页搜索 */}
            <div className="flex items-center justify-between p-4 hover:bg-neutral-50/50 dark:hover:bg-neutral-800/20 transition-colors">
              <div className="space-y-0.5 max-w-[65%]">
                <div className="text-sm font-medium">网页搜索</div>
                <div className="text-xs text-neutral-500 dark:text-neutral-400">
                  选择 openBIMAgent 访问网络国标及在线工程素材的方式
                </div>
              </div>
              <Select
                value={webSearchMode}
                onValueChange={(v) => {
                  setWebSearchMode(v)
                  saveSetting("wb_web_search", v)
                  toast.success(`网页搜索方式已更新: ${v === "cached" ? "已缓存" : v === "live" ? "实时联网" : "关闭"}`)
                }}
              >
                <SelectTrigger className="w-[140px] h-8 text-xs bg-neutral-100/70 dark:bg-neutral-800/70">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="cached" className="text-xs">已缓存</SelectItem>
                  <SelectItem value="live" className="text-xs">实时联网</SelectItem>
                  <SelectItem value="off" className="text-xs">关闭</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* 输出详细程度 */}
            <div className="flex items-center justify-between p-4 hover:bg-neutral-50/50 dark:hover:bg-neutral-800/20 transition-colors">
              <div className="space-y-0.5 max-w-[65%]">
                <div className="text-sm font-medium">输出详细程度</div>
                <div className="text-xs text-neutral-500 dark:text-neutral-400">
                  选择 openBIMAgent 回复包含工程细节的详细程度
                </div>
              </div>
              <Select
                value={verbosity}
                onValueChange={(v) => {
                  setVerbosity(v)
                  saveSetting("wb_verbosity", v)
                }}
              >
                <SelectTrigger className="w-[140px] h-8 text-xs bg-neutral-100/70 dark:bg-neutral-800/70">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="default" className="text-xs">模型默认</SelectItem>
                  <SelectItem value="verbose" className="text-xs">工程详尽</SelectItem>
                  <SelectItem value="concise" className="text-xs">简洁精炼</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* 发送快捷键 */}
            <div className="flex items-center justify-between p-4 hover:bg-neutral-50/50 dark:hover:bg-neutral-800/20 transition-colors">
              <div className="space-y-0.5 max-w-[65%]">
                <div className="text-sm font-medium">发送快捷键</div>
                <div className="text-xs text-neutral-500 dark:text-neutral-400">
                  选择按 Enter 时是发送提示还是插入换行
                </div>
              </div>
              <Select
                value={sendShortcut}
                onValueChange={(v) => {
                  setSendShortcut(v)
                  saveSetting("wb_send_shortcut", v)
                }}
              >
                <SelectTrigger className="w-[140px] h-8 text-xs bg-neutral-100/70 dark:bg-neutral-800/70">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="enter" className="text-xs">按 Enter 键</SelectItem>
                  <SelectItem value="cmd_enter" className="text-xs">按 ⌘+Enter 键</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* 跟进处理方式 (对标图二加入队列 / 调整方向) */}
            <div className="flex items-center justify-between p-4 hover:bg-neutral-50/50 dark:hover:bg-neutral-800/20 transition-colors">
              <div className="space-y-0.5 max-w-[65%]">
                <div className="text-sm font-medium">跟进处理方式</div>
                <div className="text-xs text-neutral-500 dark:text-neutral-400">
                  在 Agent 运行时后续发送消息的处理方式（按 Ctrl+↵ 触发）
                </div>
              </div>
              <div className="inline-flex rounded-lg border border-neutral-200 dark:border-neutral-800 p-0.5 bg-neutral-100/80 dark:bg-neutral-800/80 text-xs">
                <button
                  type="button"
                  onClick={() => {
                    setFollowupMode("queue")
                    saveSetting("wb_followup_mode", "queue")
                  }}
                  className={`px-3 py-1 rounded-md transition-all font-medium ${
                    followupMode === "queue"
                      ? "bg-white dark:bg-neutral-900 text-foreground shadow-xs"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  加入队列
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setFollowupMode("steer")
                    saveSetting("wb_followup_mode", "steer")
                  }}
                  className={`px-3 py-1 rounded-md transition-all font-medium ${
                    followupMode === "steer"
                      ? "bg-white dark:bg-neutral-900 text-foreground shadow-xs"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  调整方向
                </button>
              </div>
            </div>

            {/* 显示上下文窗口使用情况 */}
            <div className="flex items-center justify-between p-4 hover:bg-neutral-50/50 dark:hover:bg-neutral-800/20 transition-colors">
              <div className="space-y-0.5 max-w-[70%]">
                <div className="text-sm font-medium">显示上下文窗口使用情况</div>
                <div className="text-xs text-neutral-500 dark:text-neutral-400">
                  在输入框与顶栏实时呈现当前模型的上下文 Token 占用比例
                </div>
              </div>
              <Switch
                checked={showCtxUsage}
                onCheckedChange={(c) => {
                  setShowCtxUsage(c)
                  saveSetting("wb_show_ctx_usage", String(c))
                }}
              />
            </div>
          </CardContent>
        </Card>
      </section>
    </div>
  )
}
