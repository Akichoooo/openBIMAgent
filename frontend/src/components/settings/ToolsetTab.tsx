import React, { useEffect, useState } from "react"
import { api, HostStatus } from "@/services/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Wrench,
  RefreshCw,
  Layers,
  ShieldCheck,
  Compass,
  Hammer,
  MessageSquare,
  Server,
  Box,
  Check,
  Terminal,
  Cpu,
} from "lucide-react"
import { toast } from "sonner"

interface FoundationalEngine {
  name: string
  spec: string
  type: string
  status: "ready" | "active"
  desc: string
}

const FOUNDATIONAL_ENGINES: FoundationalEngine[] = [
  {
    name: "OpenSCAD 无头几何编译器",
    spec: "OpenSCAD 2021.01+ CLI",
    type: "CLI",
    status: "ready",
    desc: "支持无头 CLI 编译、CSG 三维布尔实体相交计算与多模态正交视口投影快照。",
  },
  {
    name: "IFC 空间拓扑内核 (IfcOpenShell)",
    spec: "ISO 16739-1:2018 (IFC4)",
    type: "PYTHON",
    status: "ready",
    desc: "原生支持 IFC2X3 / IFC4 规范读写、构件空间包络检视与属性集 (Pset) 读写。",
  },
  {
    name: "市政管网水力与避障算子",
    spec: "GB 50014-2021 / GB 50289-2016",
    type: "NATIVE",
    status: "ready",
    desc: "内置管底高程自动放样、充满度计算、跌水井标高递推与空间管束碰撞规整算子。",
  },
]

export const ToolsetTab: React.FC = () => {
  const [preset, setPreset] = useState<"minimal" | "modeling" | "full">("modeling")
  const [hosts, setHosts] = useState<HostStatus[]>([])
  const [loadingHosts, setLoadingHosts] = useState(false)
  const [restartingId, setRestartingId] = useState<string | null>(null)
  const [savingPreset, setSavingPreset] = useState(false)

  const loadData = async () => {
    try {
      const ts = await api.getToolset().catch(() => ({ preset: "modeling" as const }))
      if (ts && ts.preset) setPreset(ts.preset)
    } catch (e) {
      console.error(e)
    }

    setLoadingHosts(true)
    try {
      const h = await api.listHosts().catch(() => [])
      setHosts(h)
    } catch (e) {
      console.error(e)
    } finally {
      setLoadingHosts(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  const handleSelectPreset = async (val: "minimal" | "modeling" | "full") => {
    setPreset(val)
    setSavingPreset(true)
    try {
      await api.setToolset(val)
      toast.success(`求解权限已设为: ${val.toUpperCase()}`)
    } catch (e) {
      console.error(e)
      toast.error("设置失败")
    } finally {
      setSavingPreset(false)
    }
  }

  const handleResetDefault = async () => {
    handleSelectPreset("modeling")
  }

  const handleRestartHost = async (hostId: string) => {
    setRestartingId(hostId)
    try {
      await api.restartHost(hostId)
      toast.success(`已重试连接宿主: ${hostId}`)
      await loadData()
    } catch (e) {
      console.error(e)
      toast.error("宿主重启失败")
    } finally {
      setRestartingId(null)
    }
  }

  const presetOptions = [
    {
      id: "minimal" as const,
      title: "Ask (极简方案与答疑)",
      desc: "仅挂载只读分析与规范检视算子。超低 Token 消耗，适合图纸核验、碰撞审图与答疑，严禁任何写盘或构件增删。",
      badge: "只读低耗",
      icon: MessageSquare,
    },
    {
      id: "modeling" as const,
      title: "Plan (方案规划与追问)",
      desc: "完整推演市政管线排布与规划设计，主动追问不确定参数，推演检查井跌水与标高规划。（默认生产推荐）",
      badge: "推荐生产",
      icon: Compass,
    },
    {
      id: "full" as const,
      title: "Build (直接建模与自愈)",
      desc: "包含三维几何放样、IFC 构件写盘、几何布尔自愈与外部 DCC 宿主进程 (Blender/Vectorworks) IPC 联动。",
      badge: "全能自主",
      icon: Hammer,
    },
  ]

  return (
    <div className="space-y-6 max-w-5xl pb-8 text-neutral-900 dark:text-neutral-100">
      {/* 顶部标题区 (1:1 复刻 MCP 风格) */}
      <div className="space-y-1.5">
        <h2 className="text-xl font-bold tracking-tight text-foreground">本地能力与宿主 (Toolset Profile)</h2>
        <div className="flex items-center space-x-2 text-xs text-neutral-500">
          <span>当前求解权限:</span>
          <span className="font-mono text-[11px] px-2 py-0.5 rounded bg-neutral-100 dark:bg-neutral-800/80 text-neutral-600 dark:text-neutral-300 border border-neutral-200/70 dark:border-neutral-700/70">
            openbimagent.toolset · {preset.toUpperCase()} 模式
          </span>
        </div>
      </div>

      {/* 副操作栏: 标签 + 恢复默认 / 保存状态 */}
      <div className="flex items-center justify-between pt-1">
        <span className="text-xs font-medium text-neutral-500">
          智能体算子暴露等级与外部宿主协同
        </span>
        <div className="flex items-center space-x-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleResetDefault}
            className="h-8 px-3 text-xs text-neutral-600 dark:text-neutral-300 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800"
          >
            恢复默认
          </Button>
          <Button
            size="sm"
            onClick={() => handleSelectPreset(preset)}
            disabled={savingPreset}
            className="h-8 px-4 text-xs font-medium bg-violet-600 hover:bg-violet-700 text-white rounded-lg shadow-sm transition-all"
          >
            {savingPreset ? "保存中..." : "保存生效"}
          </Button>
        </div>
      </div>

      {/* 权限预设卡片群 (精美紫罗兰选中态) */}
      <div className="space-y-2.5">
        <h3 className="text-sm font-semibold text-foreground">执行权限等级</h3>
        <div className="grid grid-cols-1 gap-2.5">
          {presetOptions.map((opt) => {
            const Icon = opt.icon
            const isSelected = preset === opt.id
            return (
              <div
                key={opt.id}
                onClick={() => handleSelectPreset(opt.id)}
                className={`flex items-start justify-between p-4 rounded-xl border cursor-pointer transition-all ${
                  isSelected
                    ? "border-violet-300 dark:border-violet-700 bg-violet-50/60 dark:bg-violet-950/25 shadow-2xs"
                    : "border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 hover:border-neutral-300 dark:hover:border-neutral-700"
                }`}
              >
                <div className="flex items-start space-x-3.5 min-w-0 pr-4">
                  <div
                    className={`mt-0.5 p-2 rounded-lg shrink-0 transition-colors ${
                      isSelected
                        ? "bg-violet-600 text-white shadow-xs"
                        : "bg-neutral-100 dark:bg-neutral-800 text-neutral-500 dark:text-neutral-400"
                    }`}
                  >
                    <Icon className="h-4 w-4" />
                  </div>
                  <div className="space-y-1 min-w-0">
                    <div className="flex items-center space-x-2">
                      <span className="text-sm font-semibold text-foreground">{opt.title}</span>
                      <span
                        className={`text-[10px] px-1.5 py-0.2 rounded font-medium ${
                          isSelected
                            ? "bg-violet-100 dark:bg-violet-900/60 text-violet-700 dark:text-violet-300"
                            : "bg-neutral-100 dark:bg-neutral-800 text-neutral-500"
                        }`}
                      >
                        {opt.badge}
                      </span>
                    </div>
                    <p className="text-xs text-neutral-500 dark:text-neutral-400 leading-relaxed max-w-2xl">
                      {opt.desc}
                    </p>
                  </div>
                </div>

                {/* 单选圆圈指示器 */}
                <div className="mt-1 shrink-0">
                  <div
                    className={`w-4 h-4 rounded-full border flex items-center justify-center transition-all ${
                      isSelected
                        ? "border-violet-600 bg-violet-600 text-white"
                        : "border-neutral-300 dark:border-neutral-600"
                    }`}
                  >
                    {isSelected && <div className="w-1.5 h-1.5 rounded-full bg-white" />}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* 本地核心基础计算引擎 (1:1 MCP 表格规范) */}
      <div className="space-y-3 pt-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Cpu className="h-4 w-4 text-violet-600 dark:text-violet-400" />
            <h3 className="text-sm font-semibold text-foreground">本地核心基础计算引擎 (Native Foundational Engines)</h3>
          </div>
          <span className="text-xs text-neutral-400">全部开箱即用 · 纯本地沙箱托管运行</span>
        </div>

        <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 overflow-hidden bg-white dark:bg-neutral-900 shadow-2xs">
          {/* 表头 */}
          <div className="grid grid-cols-12 px-4 py-2.5 bg-neutral-50/70 dark:bg-neutral-900/60 border-b border-neutral-200/70 dark:border-neutral-800 text-[11px] text-neutral-500 font-normal select-none">
            <div className="col-span-5">引擎名称 / 算子协议</div>
            <div className="col-span-3 text-center">状态</div>
            <div className="col-span-2">调用协议</div>
            <div className="col-span-2 text-right">特性</div>
          </div>

          {/* 表行 */}
          <div className="divide-y divide-neutral-100 dark:divide-neutral-800/80">
            {FOUNDATIONAL_ENGINES.map((eng) => (
              <div
                key={eng.name}
                className="grid grid-cols-12 px-4 py-3 items-center text-xs hover:bg-neutral-50/50 dark:hover:bg-neutral-800/30 transition-colors"
              >
                {/* 1. 名称与描述 */}
                <div className="col-span-5 space-y-0.5 pr-2 min-w-0">
                  <div className="font-medium text-foreground tracking-tight truncate">
                    {eng.name}
                  </div>
                  <div className="text-[11px] text-neutral-400 truncate">
                    {eng.desc}
                  </div>
                </div>

                {/* 2. 状态：发光绿色圆点 */}
                <div className="col-span-3 flex items-center justify-center space-x-1.5">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.5)] shrink-0" />
                  <span className="text-emerald-600 dark:text-emerald-400 text-xs font-medium">
                    已就绪
                  </span>
                </div>

                {/* 3. 调用类型 */}
                <div className="col-span-2 font-mono text-neutral-600 dark:text-neutral-300 text-xs uppercase tracking-wider">
                  <Badge variant="outline" className="text-[10px] px-1.5 py-0 font-mono">
                    {eng.type}
                  </Badge>
                </div>

                {/* 4. 特性说明 */}
                <div className="col-span-2 text-right text-neutral-400 text-[11px] font-mono">
                  {eng.spec.slice(0, 14)}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 外部协同宿主进程 (External Host Supervisors) */}
      <div className="space-y-3 pt-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Server className="h-4 w-4 text-violet-600 dark:text-violet-400" />
            <h3 className="text-sm font-semibold text-foreground">外部 DCC 协同宿主 (External Host Supervisors)</h3>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={loadData}
            disabled={loadingHosts}
            className="h-7 px-2.5 text-xs text-neutral-600 dark:text-neutral-300 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800"
          >
            <RefreshCw className={`h-3 w-3 mr-1 ${loadingHosts ? "animate-spin" : ""}`} />
            刷新状态
          </Button>
        </div>

        {hosts.length === 0 ? (
          <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 p-4 bg-white dark:bg-neutral-900 shadow-2xs flex items-center justify-between text-xs">
            <div className="flex items-center space-x-3">
              <div className="w-8 h-8 rounded-lg bg-neutral-100 dark:bg-neutral-800 flex items-center justify-center text-neutral-500">
                <Server className="h-4 w-4" />
              </div>
              <div>
                <span className="font-semibold text-foreground">Blender-BIM / FreeCAD / Vectorworks 协同进程</span>
                <p className="text-[11px] text-neutral-400 mt-0.5">
                  未检测到外部宿主挂载 · 系统已自动托管至内置 OpenSCAD / IfcOpenShell 无头几何管道无缝运行
                </p>
              </div>
            </div>
            <Badge variant="outline" className="text-[10px] text-neutral-500 font-mono">
              内置托管中
            </Badge>
          </div>
        ) : (
          <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 overflow-hidden bg-white dark:bg-neutral-900 shadow-2xs divide-y divide-neutral-100 dark:divide-neutral-800/80">
            {hosts.map((host) => {
              const isUp = host.status === "up"
              return (
                <div
                  key={host.id}
                  className="flex items-center justify-between p-3.5 text-xs hover:bg-neutral-50/50 dark:hover:bg-neutral-800/30 transition-colors"
                >
                  <div className="flex items-center space-x-3">
                    <div
                      className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                        isUp
                          ? "bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.5)]"
                          : "bg-neutral-300 dark:bg-neutral-600"
                      }`}
                    />
                    <div>
                      <div className="font-medium text-foreground flex items-center space-x-2">
                        <span>{host.name}</span>
                        <span className="text-[10px] font-mono text-neutral-400">({host.id})</span>
                      </div>
                      <p className="text-[11px] text-neutral-400 mt-0.5">
                        {isUp ? `已连接 · 心跳: ${host.last_seen || "正常"}` : "未连接 · 当前由内置轻量内核接管"}
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center space-x-2">
                    <span
                      className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                        isUp
                          ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
                          : "bg-neutral-100 text-neutral-500 dark:bg-neutral-800"
                      }`}
                    >
                      {isUp ? "● 已连接" : "内置托管"}
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={restartingId === host.id}
                      onClick={() => handleRestartHost(host.id)}
                      className="h-7 px-2 text-xs text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
                    >
                      <RefreshCw className={`h-3 w-3 mr-1 ${restartingId === host.id ? "animate-spin" : ""}`} />
                      重试
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

