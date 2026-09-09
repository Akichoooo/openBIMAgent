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
  Bot,
  Folder,
  Code2,
  Settings,
  ChevronDown,
  Laptop,
  GitBranch,
  Sparkles,
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

const PLAYBOOK_OPTIONS = [
  { id: "municipal_utility", label: "市政给排水管网", desc: "重力流管网放样、标高碰撞自愈与管件布尔求交" },
  { id: "single_asset_hero", label: "单体资产建模", desc: "高精度单体建筑、桥梁、塔架参数化几何生成" },
  { id: "edo_cyberpunk_district", label: "Edo 街区规划", desc: "多地块建筑群拓扑生成与空间路网协同" },
  { id: "general", label: "常规工程任务", desc: "通用参数化 CAD 脚本编写、图纸校验与审图答疑" },
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
  playbook,
  onPlaybookChange,
  onOpenSettings,
  onAttachFile,
  voiceOn,
  onToggleVoice,
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
        toast.success(`已挂载工程文件: ${file.name}`)
      }
    }
    e.target.value = ""
  }

  const currentPlaybookObj = PLAYBOOK_OPTIONS.find((p) => p.id === playbook) || PLAYBOOK_OPTIONS[0]

  return (
    <div className="w-full max-w-2xl mx-auto px-4 py-12 flex flex-col items-center justify-center space-y-6 select-none">
      {/* 顶部极简图标与纯净主标语（单一整句，无点击链接） */}
      <div className="flex flex-col items-center justify-center space-y-3">
        <div className="w-12 h-12 rounded-2xl bg-neutral-100 dark:bg-neutral-800/60 border border-neutral-200/80 dark:border-neutral-700/60 flex items-center justify-center text-neutral-400 dark:text-neutral-500 shadow-2xs">
          <Sparkles className="h-6 w-6 stroke-[1.4]" />
        </div>

        <h1 className="text-xl md:text-2xl font-bold tracking-tight text-neutral-900 dark:text-neutral-100 text-center font-sans">
          你想构建什么？
        </h1>
      </div>

      {/* 核心输入框容器：内置顶部环境状态栏 + 输入文本框 + 底部控制栏 */}
      <div className="w-full bg-white dark:bg-neutral-900 border border-neutral-200/90 dark:border-neutral-800 rounded-2xl shadow-sm hover:border-neutral-300 dark:hover:border-neutral-700 transition-colors overflow-hidden">
        {/* 顶部环境与状态信息条 (全矢量 SVG，零 Emoji) */}
        <div className="flex items-center space-x-2 px-3.5 py-2 bg-neutral-50/80 dark:bg-neutral-800/40 border-b border-neutral-100 dark:border-neutral-800/80 text-xs text-neutral-500 dark:text-neutral-400">
          <div className="flex items-center space-x-1.5 font-mono">
            <Folder className="h-3.5 w-3.5 text-violet-500 shrink-0" />
            <span className="font-medium text-neutral-700 dark:text-neutral-300 truncate max-w-[160px]">
              {workspaceName}
            </span>
          </div>

          <span className="text-neutral-300 dark:text-neutral-700">|</span>

          <div className="flex items-center space-x-1">
            <Laptop className="h-3.5 w-3.5 text-neutral-400 shrink-0" />
            <span>本地</span>
          </div>

          <span className="text-neutral-300 dark:text-neutral-700">|</span>

          <div className="flex items-center space-x-1 font-mono">
            <GitBranch className="h-3.5 w-3.5 text-neutral-400 shrink-0" />
            <span>main</span>
          </div>

          <span className="text-neutral-300 dark:text-neutral-700">|</span>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button className="flex items-center space-x-1 text-sky-600 dark:text-sky-400 hover:text-sky-700 dark:hover:text-sky-300 cursor-pointer outline-none">
                <Code2 className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate max-w-[130px]">{currentPlaybookObj.label}</span>
                <ChevronDown className="h-2.5 w-2.5 ml-0.5" />
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

        {/* 文本输入区 */}
        <div className="p-3.5">
          <textarea
            ref={textareaRef}
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="随心输入建筑/市政工程需求，或粘贴 CAD/OpenSCAD 规范..."
            rows={3}
            className="w-full bg-transparent border-0 resize-none p-0 text-sm text-neutral-900 dark:text-neutral-100 placeholder:text-neutral-400 focus:outline-none focus:ring-0 leading-relaxed font-sans"
          />

          {/* 底部功能栏 */}
          <div className="flex items-center justify-between gap-2 pt-2.5 mt-2 border-t border-neutral-100 dark:border-neutral-800/70">
            {/* 左侧：文件挂载（布局纯净宽松，已移除冗余人机审批标签） */}
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
                className="h-8 w-8 rounded-full border border-neutral-200 dark:border-neutral-700/80 flex items-center justify-center text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200 hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors cursor-pointer"
                title="上传工程底图 CAD/IFC/OpenSCAD 文件"
              >
                <Plus className="h-4 w-4" />
              </button>
            </div>

            {/* 右侧：模型选择 + 语音 + 发送 */}
            <div className="flex items-center space-x-2">
              {/* 模型切换 */}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button className="h-8 px-3 rounded-full border border-neutral-200 dark:border-neutral-700/80 text-xs font-medium text-neutral-600 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors flex items-center space-x-1.5 cursor-pointer max-w-[200px]">
                    <Bot className="h-3.5 w-3.5 text-primary shrink-0" />
                    <span className={!currentModel ? "text-neutral-400 italic font-sans" : "font-mono font-medium truncate"}>
                      {currentModel || "未配置模型"}
                    </span>
                    <ChevronDown className="h-3 w-3 text-neutral-400 ml-0.5 shrink-0" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-56 text-xs">
                  <div className="px-2 py-1 text-[10px] font-semibold text-neutral-400">
                    选择执行大模型
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
                        <span className="font-mono">{m}</span>
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
                  className="h-8 w-8 rounded-full bg-rose-500 hover:bg-rose-600 text-white flex items-center justify-center transition-all shadow-xs cursor-pointer"
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
        </div>
      </div>
    </div>
  )
}
