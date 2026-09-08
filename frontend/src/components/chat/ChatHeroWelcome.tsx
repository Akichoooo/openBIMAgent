import React, { useState, useRef } from "react"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Plus,
  ArrowUp,
  Square,
  Mic,
  MicOff,
  Compass,
  Hammer,
  MessageSquare,
  Bot,
  Brain,
  Folder,
  Code2,
  Settings,
  Building2,
  Workflow,
  FileCheck2,
  ShieldCheck,
  PackageCheck,
  BookOpen,
  ChevronDown,
  Layers,
  Laptop,
  Cloud,
} from "lucide-react"
import { toast } from "sonner"
import type { ReasoningLevel } from "./ModelPicker"

interface ChatHeroWelcomeProps {
  inputText: string
  setInputText: (text: string | ((prev: string) => string)) => void
  onSend: (overridePrompt?: string) => void
  sending: boolean
  runActive: boolean
  onStopRun: () => void
  currentModel: string
  onSelectModel?: (modelId: string) => void
  availableModels?: string[]
  effort: ReasoningLevel
  onEffortChange: (level: ReasoningLevel) => void
  playbook: string
  onPlaybookChange?: (playbook: string) => void
  onOpenSettings?: (tab?: string) => void
  onAttachFile?: (file: File) => void
  voiceOn: boolean
  onToggleVoice: () => void
  toolsetPreset: "minimal" | "modeling" | "full"
  onToolsetPresetChange: (preset: "minimal" | "modeling" | "full") => void
  workspaceName?: string
}

const EFFORT_LABELS: Record<ReasoningLevel, string> = {
  off: "无思考",
  low: "轻度",
  medium: "深度思考",
  high: "强化",
  max: "极限",
}

const PLAYBOOK_OPTIONS = [
  { id: "municipal_utility", label: "市政给排水管网", desc: "重力流管网放样、标高碰撞自愈与管件布尔求交" },
  { id: "single_asset_hero", label: "单体资产建模", desc: "高精度单体建筑、桥梁、塔架参数化几何生成" },
  { id: "edo_cyberpunk_district", label: "Edo 街区规划", desc: "多地块建筑群拓扑生成与空间路网协同" },
  { id: "general", label: "常规工程任务", desc: "通用参数化 CAD 脚本编写、图纸校验与审图答疑" },
]

const SCENARIOS = [
  {
    icon: Building2,
    label: "建筑空间生成",
    prompt: "生成三层办公空间参数化建筑模型，包含楼梯间、核心筒与外幕墙网格，符合建筑模数规范。",
  },
  {
    icon: Workflow,
    label: "市政管网避障",
    prompt: "执行市政雨污水重力管网三维放样，自动按标高避障地下既有管廊并输出 CompiledUtilityIR。",
  },
  {
    icon: FileCheck2,
    label: "CAD/IFC 审图",
    prompt: "读取当前工程 IFC 结构模型，核查构件几何流形完整性与国标消防疏散净空合规性。",
  },
  {
    icon: Hammer,
    label: "几何布尔自愈",
    prompt: "检查并修复当前 OpenSCAD 几何的自相交网格与非流形棱边缝隙，保障三维布尔切削稳态。",
  },
  {
    icon: PackageCheck,
    label: "构件出图交付",
    prompt: "将已收敛的三维管网与结构资产一键导出为 Blender-BIM 与 Vectorworks 真机工程图纸交付件。",
  },
  {
    icon: BookOpen,
    label: "规范库检索",
    prompt: "检索 GB 50014-2021 给水排水工程规范，查询雨污水管线最小覆土深度与管道交叉间距限值。",
  },
]

export const ChatHeroWelcome: React.FC<ChatHeroWelcomeProps> = ({
  inputText,
  setInputText,
  onSend,
  sending,
  runActive,
  onStopRun,
  currentModel,
  onSelectModel,
  availableModels = ["DeepSeek-V3", "Qwen-2.5-Coder", "Claude-3.5-Sonnet", "GPT-4o"],
  effort,
  onEffortChange,
  playbook,
  onPlaybookChange,
  onOpenSettings,
  onAttachFile,
  voiceOn,
  onToggleVoice,
  toolsetPreset,
  onToolsetPresetChange,
  workspaceName = "openBIMAgent",
}) => {
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      if (runActive) {
        onStopRun()
      } else {
        onSend()
      }
    }
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      if (onAttachFile) {
        onAttachFile(file)
      } else {
        toast.success(`已挂载图纸工程附件: ${file.name}`)
      }
    }
    e.target.value = ""
  }

  const currentPlaybookObj = PLAYBOOK_OPTIONS.find((p) => p.id === playbook) || PLAYBOOK_OPTIONS[0]

  return (
    <div className="w-full max-w-3xl mx-auto px-4 py-8 flex flex-col items-center justify-center space-y-7 select-none">
      {/* 顶部 Hero 区域 */}
      <div className="w-full flex flex-col md:flex-row items-center justify-between gap-6">
        {/* 左侧：标语与工程定位 */}
        <div className="space-y-2 text-center md:text-left flex-1 min-w-0">
          <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-primary/10 text-primary text-[11px] font-medium tracking-wide">
            <Building2 className="h-3 w-3" />
            <span>openBIMAgent 生成式三维设计平台</span>
          </div>
          <h1 className="text-2xl md:text-3xl font-extrabold tracking-tight text-neutral-900 dark:text-neutral-50 font-sans leading-tight">
            建筑与市政空间，一语建模
          </h1>
          <p className="text-xs text-neutral-500 dark:text-neutral-400 max-w-lg leading-relaxed">
            基于国标规范刚性约束与几何求解微内核，通过自然语言驱动参数化 CAD、雨污水管网放样自愈与工程资产交付。
          </p>
        </div>

        {/* 右侧：Generative BIM 3D 等轴测空间轴网微内核视效 (替换卡通吉祥物，契合工程美学) */}
        <div className="shrink-0 relative w-36 h-32 flex items-center justify-center">
          <svg viewBox="0 0 200 180" className="w-full h-full drop-shadow-sm">
            <defs>
              <linearGradient id="cubeGlow" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stopColor="#8b5cf6" stopOpacity="0.8" />
                <stop offset="100%" stopColor="#3b82f6" stopOpacity="0.8" />
              </linearGradient>
              <linearGradient id="gridGrad" x1="0%" y1="0%" x2="0%" y2="100%">
                <stop offset="0%" stopColor="#a78bfa" stopOpacity="0.3" />
                <stop offset="100%" stopColor="#8b5cf6" stopOpacity="0.05" />
              </linearGradient>
            </defs>

            {/* 空间地面等轴测网格 */}
            <path d="M 100 90 L 170 125 L 100 160 L 30 125 Z" fill="url(#gridGrad)" stroke="#8b5cf6" strokeWidth="1" strokeDasharray="3 3" opacity="0.6" />
            <path d="M 100 110 L 140 130 L 100 150 L 60 130 Z" fill="none" stroke="#8b5cf6" strokeWidth="0.8" strokeDasharray="2 2" opacity="0.4" />

            {/* 三维参数化建筑与管网等轴测线框 */}
            <polygon points="100,45 150,70 100,95 50,70" fill="#ede9fe" fillOpacity="0.4" stroke="#7c3aed" strokeWidth="1.5" />
            <polygon points="50,70 100,95 100,140 50,115" fill="#ddd6fe" fillOpacity="0.3" stroke="#7c3aed" strokeWidth="1.5" />
            <polygon points="100,95 150,70 150,115 100,140" fill="#c4b5fd" fillOpacity="0.4" stroke="#7c3aed" strokeWidth="1.5" />

            {/* 空间发光管网管道 */}
            <path d="M 40 100 L 80 120 L 100 110 L 140 130 L 170 115" fill="none" stroke="#06b6d4" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M 100 65 L 100 110 L 120 120" fill="none" stroke="#10b981" strokeWidth="2.5" strokeLinecap="round" />

            {/* 拓扑关键节点 (发光圆点) */}
            <circle cx="100" cy="45" r="3.5" fill="#8b5cf6" />
            <circle cx="150" cy="70" r="3" fill="#8b5cf6" />
            <circle cx="50" cy="70" r="3" fill="#8b5cf6" />
            <circle cx="100" cy="95" r="4" fill="#a855f7" />
            <circle cx="80" cy="120" r="3" fill="#06b6d4" />
            <circle cx="140" cy="130" r="3" fill="#06b6d4" />
            <circle cx="100" cy="110" r="3.5" fill="#10b981" />

            {/* 空间坐标轴指示器 */}
            <path d="M 100 95 L 100 20" stroke="#ef4444" strokeWidth="1.5" strokeDasharray="2 2" />
            <path d="M 100 95 L 180 55" stroke="#10b981" strokeWidth="1.5" strokeDasharray="2 2" />
            <path d="M 100 95 L 20 55" stroke="#3b82f6" strokeWidth="1.5" strokeDasharray="2 2" />
            <text x="102" y="25" fill="#ef4444" fontSize="9" fontWeight="bold" fontFamily="monospace">Z</text>
            <text x="175" y="52" fill="#10b981" fontSize="9" fontWeight="bold" fontFamily="monospace">X</text>
            <text x="22" y="52" fill="#3b82f6" fontSize="9" fontWeight="bold" fontFamily="monospace">Y</text>
          </svg>
        </div>
      </div>

      {/* 中央核心交互提问卡片 (1:1 吸收图二交互与控件布局) */}
      <div className="w-full bg-white dark:bg-neutral-900 border border-neutral-200/85 dark:border-neutral-800 rounded-2xl p-4 shadow-sm hover:border-neutral-300 dark:hover:border-neutral-700 transition-colors space-y-3">
        {/* 多行输入区域 */}
        <textarea
          ref={textareaRef}
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="有什么好想法？输入建筑/市政工程需求，或粘贴 CAD/OpenSCAD 规范..."
          rows={3}
          className="w-full bg-transparent border-0 resize-none p-0 text-sm text-neutral-900 dark:text-neutral-100 placeholder:text-neutral-400 focus:outline-none focus:ring-0 leading-relaxed font-sans"
        />

        {/* 内部操作栏: 附件上传 | 模型选择 + 语音 + 发送 */}
        <div className="flex items-center justify-between gap-2 pt-2 border-t border-neutral-100 dark:border-neutral-800/80">
          {/* 左侧：上传附件 */}
          <div className="flex items-center space-x-2">
            <input
              type="file"
              ref={fileInputRef}
              onChange={handleFileChange}
              className="hidden"
              accept=".ifc,.scad,.dxf,.dwg,.json,.png,.jpg,.jpeg"
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              className="h-8 w-8 rounded-full border border-neutral-200 dark:border-neutral-700/80 flex items-center justify-center text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors"
              title="上传工程底图 CAD/IFC/OpenSCAD 文件"
            >
              <Plus className="h-4 w-4" />
            </button>
          </div>

          {/* 右侧：模型选择 + 语音 + 发送 */}
          <div className="flex items-center space-x-2">
            {/* 模型胶囊 */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button className="h-8 px-3 rounded-full border border-neutral-200 dark:border-neutral-700/80 text-xs font-medium text-neutral-600 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors flex items-center space-x-1.5 cursor-pointer">
                  <Bot className="h-3.5 w-3.5 text-primary shrink-0" />
                  <span className={!currentModel ? "text-neutral-400 italic font-sans" : "font-mono font-medium"}>
                    {currentModel || "未配置模型"}
                  </span>
                  <ChevronDown className="h-3 w-3 text-neutral-400 ml-0.5" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56 text-xs">
                <div className="px-2 py-1 text-[10px] font-semibold text-neutral-400">
                  选择执行模型
                </div>
                {availableModels.length === 0 ? (
                  <div className="px-2 py-3 text-center text-xs text-neutral-400">
                    暂无可用的模型
                  </div>
                ) : (
                  availableModels.map((m) => (
                    <DropdownMenuItem
                      key={m}
                      onClick={() => onSelectModel?.(m)}
                      className="cursor-pointer flex items-center justify-between"
                    >
                      <span>{m}</span>
                      {currentModel === m && (
                        <span className="text-[10px] text-primary font-mono font-semibold">当前</span>
                      )}
                    </DropdownMenuItem>
                  ))
                )}
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onClick={() => onOpenSettings?.("models")}
                  className="cursor-pointer text-primary font-medium"
                >
                  <Settings className="h-3 w-3 mr-2" />
                  配置模型服务...
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>

            {/* 语音按钮 */}
            <button
              onClick={onToggleVoice}
              className={`h-8 w-8 rounded-full border border-neutral-200 dark:border-neutral-700/80 flex items-center justify-center transition-colors ${
                voiceOn
                  ? "bg-rose-500 text-white animate-pulse"
                  : "text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800"
              }`}
              title={voiceOn ? "停止语音输入" : "开启语音识别"}
            >
              {voiceOn ? <MicOff className="h-3.5 w-3.5" /> : <Mic className="h-3.5 w-3.5" />}
            </button>

            {/* 发送 / 停止按钮 */}
            {runActive ? (
              <button
                onClick={onStopRun}
                className="h-8 w-8 rounded-full bg-rose-500 hover:bg-rose-600 text-white flex items-center justify-center transition-all shadow-xs"
                title="中止当前生成"
              >
                <Square className="h-3.5 w-3.5 fill-white" />
              </button>
            ) : (
              <button
                onClick={() => onSend()}
                disabled={sending || !inputText.trim()}
                className="h-8 w-8 rounded-full bg-primary hover:bg-primary/90 disabled:opacity-40 disabled:hover:bg-primary text-primary-foreground flex items-center justify-center transition-all shadow-xs cursor-pointer"
                title="发送工程指令 (Enter)"
              >
                <ArrowUp className="h-4 w-4 stroke-[2.5]" />
              </button>
            )}
          </div>
        </div>

        {/* 内部操作栏第二行 (对标图二底边: 📁 工作区 ∨   </> 代码任务 ∨) */}
        <div className="flex items-center space-x-3 text-xs text-neutral-400 pt-1">
          <div className="flex items-center space-x-1 cursor-pointer hover:text-neutral-600 dark:hover:text-neutral-200 transition-colors">
            <Folder className="h-3 w-3 text-violet-500" />
            <span>{workspaceName}</span>
          </div>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button className="flex items-center space-x-1 cursor-pointer hover:text-neutral-600 dark:hover:text-neutral-200 transition-colors">
                <Code2 className="h-3 w-3 text-violet-500" />
                <span>{currentPlaybookObj.label}</span>
                <ChevronDown className="h-2.5 w-2.5" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-56 text-xs">
              <div className="px-2 py-1 text-[10px] font-semibold text-neutral-400">
                选择工程领域 Playbook
              </div>
              {PLAYBOOK_OPTIONS.map((p) => (
                <DropdownMenuItem
                  key={p.id}
                  onClick={() => onPlaybookChange?.(p.id)}
                  className="cursor-pointer"
                >
                  <div>
                    <div className="font-medium">{p.label}</div>
                    <div className="text-[10px] text-neutral-400">{p.desc}</div>
                  </div>
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {/* 卡片下方辅助设置指引 (对齐图二: ⚙ 完成设置，让小浣熊更懂你 | 不再提示) */}
      <div className="flex items-center space-x-2 text-xs text-neutral-400">
        <span>⚙ 完成设置，让 openBIMAgent 更懂你的工程规范</span>
        <span>|</span>
        <button
          onClick={() => onOpenSettings?.("rules")}
          className="text-violet-600 dark:text-violet-400 hover:underline font-medium"
        >
          快捷偏好设置
        </button>
      </div>

      {/* 场景示例模块 (对标图二下方「场景示例」药丸条) */}
      <div className="w-full space-y-3">
        <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400">
          场景示例
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 gap-2.5">
          {SCENARIOS.map((sc, i) => {
            const Icon = sc.icon
            return (
              <button
                key={i}
                onClick={() => {
                  setInputText(sc.prompt)
                  textareaRef.current?.focus()
                }}
                className="flex items-center space-x-2 px-3.5 py-2.5 rounded-xl border border-neutral-200/70 dark:border-neutral-800 bg-white/70 dark:bg-neutral-900/60 hover:border-violet-300 dark:hover:border-violet-700 hover:bg-violet-50/40 dark:hover:bg-violet-950/20 text-neutral-700 dark:text-neutral-300 transition-all text-left group shadow-2xs"
              >
                <div className="p-1 rounded-md bg-neutral-100 dark:bg-neutral-800 text-neutral-500 group-hover:text-violet-600 dark:group-hover:text-violet-400 transition-colors shrink-0">
                  <Icon className="h-3.5 w-3.5" />
                </div>
                <span className="text-xs font-medium truncate">{sc.label}</span>
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}
