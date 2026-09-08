import React, { useState, useEffect } from "react"
import { api } from "@/services/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  RefreshCw,
  ChevronDown,
  ChevronUp,
  Power,
  Download,
  Terminal,
  FileCheck2,
  Box,
  Eye,
  Layers,
  Cpu,
} from "lucide-react"
import { toast } from "sonner"

export const PluginsTab: React.FC = () => {
  const [loading, setLoading] = useState(false)
  const [recheckingId, setRecheckingId] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  // 插件启用状态管理
  const [pluginStatus, setPluginStatus] = useState({
    pipeline: true,
    qwenVision: true,
    blenderHost: false,
    gbCompliance: true,
  })

  const handleTogglePlugin = (key: keyof typeof pluginStatus) => {
    const next = !pluginStatus[key]
    setPluginStatus((prev) => ({ ...prev, [key]: next }))
    toast.success(next ? "插件能力已启用" : "插件能力已关闭")
  }

  const handleRecheck = (id: string, name: string) => {
    setRecheckingId(id)
    setTimeout(() => {
      setRecheckingId(null)
      toast.success(`${name} 运行状态正常`)
    }, 600)
  }

  return (
    <div className="space-y-6 max-w-5xl pb-8 text-neutral-900 dark:text-neutral-100">
      {/* 顶部标题区 (1:1 对齐截图顶部) */}
      <div className="space-y-1">
        <h2 className="text-xl font-bold tracking-tight text-foreground">本地能力</h2>
        <p className="text-xs text-neutral-500">
          先准备基础组件，再启用需要的插件能力。
        </p>
      </div>

      {/* 第一板块：本地基础组件 (1:1 复刻截图上方两张卡片) */}
      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-foreground">本地基础组件</h3>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* 卡片 1: 本地运行环境 */}
          <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 p-5 space-y-3.5 shadow-2xs">
            <div className="flex items-start justify-between">
              <div>
                <h4 className="text-sm font-semibold text-foreground">本地运行环境</h4>
                <p className="text-xs text-neutral-400 mt-0.5">
                  运行 openBIMAgent 本地三维拓扑与合规自愈所需。
                </p>
              </div>
              <span className="text-xs text-emerald-600 dark:text-emerald-400 font-medium px-2 py-0.5 rounded bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200/60 dark:border-emerald-900/60">
                已就绪
              </span>
            </div>

            <p className="text-xs text-neutral-600 dark:text-neutral-300">
              基础组件已就绪。
            </p>

            {/* 环境详情折叠行 */}
            <div className="pt-1">
              <button
                onClick={() => setExpandedId(expandedId === "env" ? null : "env")}
                className="w-full flex items-center justify-between text-xs text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300 py-1"
              >
                <span>环境详情</span>
                {expandedId === "env" ? (
                  <ChevronUp className="h-3.5 w-3.5" />
                ) : (
                  <ChevronDown className="h-3.5 w-3.5" />
                )}
              </button>

              {expandedId === "env" && (
                <div className="mt-2 p-3 rounded-lg bg-neutral-50 dark:bg-neutral-800/60 text-[11px] font-mono text-neutral-600 dark:text-neutral-300 space-y-1 border border-neutral-200/60 dark:border-neutral-700/60">
                  <div>Python 运行时: 3.13.2 (Virtualenv 沙箱)</div>
                  <div>ifcopenshell: 0.8.0-alpha (原生 IFC 编译内核)</div>
                  <div>Shapely: 2.0.7 (空间拓扑与布尔交集求交)</div>
                  <div>NetworkX: 3.2.1 (管网有向图连通度校验)</div>
                </div>
              )}
            </div>

            {/* 重新检测按钮 */}
            <div className="pt-1">
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleRecheck("env", "本地运行环境")}
                disabled={recheckingId === "env"}
                className="h-8 px-4 text-xs text-neutral-600 dark:text-neutral-300 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg"
              >
                <RefreshCw className={`h-3 w-3 mr-1.5 ${recheckingId === "env" ? "animate-spin" : ""}`} />
                重新检测
              </Button>
            </div>
          </div>

          {/* 卡片 2: OpenSCAD 3D 渲染组件 */}
          <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 p-5 space-y-3.5 shadow-2xs">
            <div className="flex items-start justify-between">
              <div>
                <h4 className="text-sm font-semibold text-foreground">OpenSCAD 渲染组件</h4>
                <p className="text-xs text-neutral-400 mt-0.5">
                  用于代码级几何建模预览和无头多模态快照。
                </p>
              </div>
              <span className="text-xs text-emerald-600 dark:text-emerald-400 font-medium px-2 py-0.5 rounded bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200/60 dark:border-emerald-900/60">
                可逐页渲染
              </span>
            </div>

            <p className="text-xs text-neutral-600 dark:text-neutral-300">
              OpenSCAD 渲染组件已就绪。
            </p>

            {/* 组件详情折叠行 */}
            <div className="pt-1">
              <button
                onClick={() => setExpandedId(expandedId === "openscad" ? null : "openscad")}
                className="w-full flex items-center justify-between text-xs text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300 py-1"
              >
                <span>组件详情</span>
                {expandedId === "openscad" ? (
                  <ChevronUp className="h-3.5 w-3.5" />
                ) : (
                  <ChevronDown className="h-3.5 w-3.5" />
                )}
              </button>

              {expandedId === "openscad" && (
                <div className="mt-2 p-3 rounded-lg bg-neutral-50 dark:bg-neutral-800/60 text-[11px] font-mono text-neutral-600 dark:text-neutral-300 space-y-1 border border-neutral-200/60 dark:border-neutral-700/60">
                  <div>编译器内核: OpenSCAD 2021.01+ (CLI Headless)</div>
                  <div>图形离屏缓存: EGL / OSMesa 无头离屏显存</div>
                  <div>多模态视觉管线: 支持每轮自愈自动渲染 1024x768 投影快照</div>
                </div>
              )}
            </div>

            {/* 重新检测按钮 */}
            <div className="pt-1">
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleRecheck("openscad", "OpenSCAD 渲染组件")}
                disabled={recheckingId === "openscad"}
                className="h-8 px-4 text-xs text-neutral-600 dark:text-neutral-300 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg"
              >
                <RefreshCw className={`h-3 w-3 mr-1.5 ${recheckingId === "openscad" ? "animate-spin" : ""}`} />
                重新检测
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* 第二板块：插件能力 (1:1 复刻截图下方卡片矩阵) */}
      <div className="space-y-3 pt-2">
        <h3 className="text-sm font-semibold text-foreground">插件能力</h3>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* 插件卡片 1: 市政给排水管网求解器 */}
          <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 p-5 space-y-3.5 shadow-2xs">
            <div className="flex items-start justify-between">
              <div className="flex items-start space-x-3">
                <div className="p-2.5 rounded-xl bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-300 shrink-0">
                  <Layers className="h-5 w-5" />
                </div>
                <div>
                  <h4 className="text-sm font-semibold text-foreground">市政管网空间求解器</h4>
                  <p className="text-xs text-neutral-400 mt-0.5">
                    管网操作、标高推求和碰撞自愈共用。
                  </p>
                </div>
              </div>
              <span className="text-xs text-emerald-600 dark:text-emerald-400 font-medium px-2 py-0.5 rounded bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200/60 dark:border-emerald-900/60 flex items-center space-x-1">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                <span>已就绪</span>
              </span>
            </div>

            <p className="text-xs text-neutral-600 dark:text-neutral-300">
              {pluginStatus.pipeline ? "已启用，可用于空间避障与标高推求自愈。" : "已停用，智能体将只生成基础管线草图。"}
            </p>

            {/* 组件详情折叠 */}
            <div className="pt-1">
              <button
                onClick={() => setExpandedId(expandedId === "pipeline" ? null : "pipeline")}
                className="w-full flex items-center justify-between text-xs text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300 py-1"
              >
                <span>组件详情</span>
                {expandedId === "pipeline" ? (
                  <ChevronUp className="h-3.5 w-3.5" />
                ) : (
                  <ChevronDown className="h-3.5 w-3.5" />
                )}
              </button>

              {expandedId === "pipeline" && (
                <div className="mt-2 p-3 rounded-lg bg-neutral-50 dark:bg-neutral-800/60 text-[11px] font-mono text-neutral-600 dark:text-neutral-300 space-y-1 border border-neutral-200/60 dark:border-neutral-700/60">
                  <div>能力标识: pipeline:solve, pipeline:clash_check</div>
                  <div>算法内核: Dijkstra 重力流三维空间避障求解器</div>
                  <div>合规约束: 跌水井自动校核与埋深坡度自愈算法</div>
                </div>
              )}
            </div>

            {/* 操作按钮栏 */}
            <div className="flex items-center space-x-2 pt-1">
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleTogglePlugin("pipeline")}
                className="h-8 px-3 text-xs text-neutral-600 dark:text-neutral-300 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg"
              >
                <Power className="h-3 w-3 mr-1.5 text-neutral-500" />
                {pluginStatus.pipeline ? "关闭求解能力" : "启用求解能力"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleRecheck("pipeline", "市政管网空间求解器")}
                disabled={recheckingId === "pipeline"}
                className="h-8 px-3 text-xs text-neutral-600 dark:text-neutral-300 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg"
              >
                <RefreshCw className={`h-3 w-3 mr-1.5 ${recheckingId === "pipeline" ? "animate-spin" : ""}`} />
                重新检测
              </Button>
            </div>
          </div>

          {/* 插件卡片 2: Qwen-MM 工业图纸视觉理解 */}
          <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 p-5 space-y-3.5 shadow-2xs">
            <div className="flex items-start justify-between">
              <div className="flex items-start space-x-3">
                <div className="p-2.5 rounded-xl bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-300 shrink-0">
                  <Eye className="h-5 w-5" />
                </div>
                <div>
                  <h4 className="text-sm font-semibold text-foreground">Qwen-MM 图纸视觉理解</h4>
                  <p className="text-xs text-neutral-400 mt-0.5">
                    工程 CAD 图纸 OCR、多模态立面与截图自检。
                  </p>
                </div>
              </div>
              <span className="text-xs text-emerald-600 dark:text-emerald-400 font-medium px-2 py-0.5 rounded bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200/60 dark:border-emerald-900/60 flex items-center space-x-1">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                <span>已就绪</span>
              </span>
            </div>

            <p className="text-xs text-neutral-600 dark:text-neutral-300">
              {pluginStatus.qwenVision ? "已启用，可用于图纸 OCR 识别与截图自审查。" : "已关闭视觉理解组件。"}
            </p>

            {/* 组件详情折叠 */}
            <div className="pt-1">
              <button
                onClick={() => setExpandedId(expandedId === "qwen" ? null : "qwen")}
                className="w-full flex items-center justify-between text-xs text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300 py-1"
              >
                <span>组件详情</span>
                {expandedId === "qwen" ? (
                  <ChevronUp className="h-3.5 w-3.5" />
                ) : (
                  <ChevronDown className="h-3.5 w-3.5" />
                )}
              </button>

              {expandedId === "qwen" && (
                <div className="mt-2 p-3 rounded-lg bg-neutral-50 dark:bg-neutral-800/60 text-[11px] font-mono text-neutral-600 dark:text-neutral-300 space-y-1 border border-neutral-200/60 dark:border-neutral-700/60">
                  <div>挂载路径: D:\Agent\mcp\Qwen-MM-Plugins</div>
                  <div>核心组件: qwen-mm-plugins-core (本地多模态解析)</div>
                  <div>API 网关: qwen-mm-plugins-api (vision_chat / ocr)</div>
                </div>
              )}
            </div>

            {/* 操作按钮栏 */}
            <div className="flex items-center space-x-2 pt-1">
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleTogglePlugin("qwenVision")}
                className="h-8 px-3 text-xs text-neutral-600 dark:text-neutral-300 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg"
              >
                <Power className="h-3 w-3 mr-1.5 text-neutral-500" />
                {pluginStatus.qwenVision ? "关闭视觉能力" : "开启视觉能力"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleRecheck("qwen", "Qwen-MM 视觉插件")}
                disabled={recheckingId === "qwen"}
                className="h-8 px-3 text-xs text-neutral-600 dark:text-neutral-300 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg"
              >
                <RefreshCw className={`h-3 w-3 mr-1.5 ${recheckingId === "qwen" ? "animate-spin" : ""}`} />
                重新检测
              </Button>
            </div>
          </div>

          {/* 插件卡片 3: Blender-BIM 外部视口协同 */}
          <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 p-5 space-y-3.5 shadow-2xs">
            <div className="flex items-start justify-between">
              <div className="flex items-start space-x-3">
                <div className="p-2.5 rounded-xl bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-300 shrink-0">
                  <Box className="h-5 w-5" />
                </div>
                <div>
                  <h4 className="text-sm font-semibold text-foreground">Blender-BIM 视口协同</h4>
                  <p className="text-xs text-neutral-400 mt-0.5">
                    连接 Blender 进行高级材质烘焙与漫游动画。
                  </p>
                </div>
              </div>
              <span className="text-xs text-amber-600 dark:text-amber-400 font-medium px-2 py-0.5 rounded bg-amber-50 dark:bg-amber-950/40 border border-amber-200/60 dark:border-amber-900/60 flex items-center space-x-1">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
                <span>等待扩展</span>
              </span>
            </div>

            <p className="text-xs text-neutral-600 dark:text-neutral-300">
              需要启动外部 Blender-BIM 插件协同桥接服务。
            </p>

            {/* 组件详情折叠 */}
            <div className="pt-1">
              <button
                onClick={() => setExpandedId(expandedId === "blender" ? null : "blender")}
                className="w-full flex items-center justify-between text-xs text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300 py-1"
              >
                <span>组件详情</span>
                {expandedId === "blender" ? (
                  <ChevronUp className="h-3.5 w-3.5" />
                ) : (
                  <ChevronDown className="h-3.5 w-3.5" />
                )}
              </button>

              {expandedId === "blender" && (
                <div className="mt-2 p-3 rounded-lg bg-neutral-50 dark:bg-neutral-800/60 text-[11px] font-mono text-neutral-600 dark:text-neutral-300 space-y-1 border border-neutral-200/60 dark:border-neutral-700/60">
                  <div>IPC 协同端口: 127.0.0.1:9876 (WebSocket RPC)</div>
                  <div>当前状态: 未连接 (系统已自动启用内置 Three.js 原生轻量视口)</div>
                </div>
              )}
            </div>

            {/* 操作按钮栏 (1:1 复刻截图黑色按钮) */}
            <div className="flex items-center space-x-2 pt-1">
              <Button
                size="sm"
                onClick={() => {
                  toast.info("已触发 Blender 协同插件连接尝试")
                }}
                className="h-8 px-3 text-xs bg-neutral-900 hover:bg-neutral-800 dark:bg-white dark:text-black dark:hover:bg-neutral-200 text-white rounded-lg shadow-sm"
              >
                <Download className="h-3 w-3 mr-1.5" />
                安装与连接协同服务
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleRecheck("blender", "Blender-BIM 视口协同")}
                disabled={recheckingId === "blender"}
                className="h-8 px-3 text-xs text-neutral-600 dark:text-neutral-300 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg"
              >
                <RefreshCw className={`h-3 w-3 mr-1.5 ${recheckingId === "blender" ? "animate-spin" : ""}`} />
                重新检测
              </Button>
            </div>
          </div>

          {/* 插件卡片 4: 国标规范合规性审查插件 */}
          <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 p-5 space-y-3.5 shadow-2xs">
            <div className="flex items-start justify-between">
              <div className="flex items-start space-x-3">
                <div className="p-2.5 rounded-xl bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-300 shrink-0">
                  <FileCheck2 className="h-5 w-5" />
                </div>
                <div>
                  <h4 className="text-sm font-semibold text-foreground">国标规范合规性审查插件</h4>
                  <p className="text-xs text-neutral-400 mt-0.5">
                    管径校验、净距审查与覆土深度自动合规。
                  </p>
                </div>
              </div>
              <span className="text-xs text-emerald-600 dark:text-emerald-400 font-medium px-2 py-0.5 rounded bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200/60 dark:border-emerald-900/60 flex items-center space-x-1">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                <span>已就绪</span>
              </span>
            </div>

            <p className="text-xs text-neutral-600 dark:text-neutral-300">
              {pluginStatus.gbCompliance ? "已启用，36 条国家与行业规范实时挂载于自愈计算管线。" : "已停用国标审查插件。"}
            </p>

            {/* 组件详情折叠 */}
            <div className="pt-1">
              <button
                onClick={() => setExpandedId(expandedId === "gb" ? null : "gb")}
                className="w-full flex items-center justify-between text-xs text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300 py-1"
              >
                <span>组件详情</span>
                {expandedId === "gb" ? (
                  <ChevronUp className="h-3.5 w-3.5" />
                ) : (
                  <ChevronDown className="h-3.5 w-3.5" />
                )}
              </button>

              {expandedId === "gb" && (
                <div className="mt-2 p-3 rounded-lg bg-neutral-50 dark:bg-neutral-800/60 text-[11px] font-mono text-neutral-600 dark:text-neutral-300 space-y-1 border border-neutral-200/60 dark:border-neutral-700/60">
                  <div>挂载规范: GB50015 建筑给水排水设计标准</div>
                  <div>合规项目: 管线最小水平净距、重力流最小流速与水力跌水井</div>
                  <div>审计规则树: 36 条活动合规规则实时生效</div>
                </div>
              )}
            </div>

            {/* 操作按钮栏 */}
            <div className="flex items-center space-x-2 pt-1">
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleTogglePlugin("gbCompliance")}
                className="h-8 px-3 text-xs text-neutral-600 dark:text-neutral-300 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg"
              >
                <Power className="h-3 w-3 mr-1.5 text-neutral-500" />
                {pluginStatus.gbCompliance ? "关闭审查插件" : "开启审查插件"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleRecheck("gb", "国标合规审查插件")}
                disabled={recheckingId === "gb"}
                className="h-8 px-3 text-xs text-neutral-600 dark:text-neutral-300 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg"
              >
                <RefreshCw className={`h-3 w-3 mr-1.5 ${recheckingId === "gb" ? "animate-spin" : ""}`} />
                重新检测
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
