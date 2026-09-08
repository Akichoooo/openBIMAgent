/**
 * ModelPicker：模型选择与运行规格概览组件
 * - 彻底清除商业代充商/中转站的伪倍率（0.5x Credit、错峰 4 折等）；
 * - 纯净呈现当前大模型在设置中配置的真实上下文窗口、最大输出 Token 与多模态能力；
 * - 提供轻量直观的思考模式（Reasoning Effort）调节；
 * - 快捷入口一键跳转至设置中心模型管理。
 */
import React, { useState, useMemo } from "react"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Badge } from "@/components/ui/badge"
import {
  Brain,
  Check,
  Search,
  ChevronDown,
  Settings,
  FileText,
  Image as ImageIcon,
  Video,
} from "lucide-react"
import type { ModelsSettingsData } from "@/services/api"

export const REASONING_LEVELS = ["off", "low", "medium", "high", "max"] as const
export type ReasoningLevel = (typeof REASONING_LEVELS)[number]

export interface EnrichedModelInfo {
  id: string
  name: string
  providerName: string
  providerId: string
  contextWindow: number
  maxTokens: number
  inputTypes: string[]
  outputTypes: string[]
  supportsTools: boolean
  isBaseline: boolean
  desc?: string
}

export const fmtTokens = (n: number) => {
  if (!n) return "128K (128,000)"
  if (n >= 1000000) {
    const m = (n / 1000000).toFixed(n % 1000000 === 0 ? 0 : 1)
    return `${m}M (${n.toLocaleString()})`
  }
  if (n >= 1000) {
    const k = Math.round(n / 1000)
    return `${k}K (${n.toLocaleString()})`
  }
  return `${n.toLocaleString()}`
}

export const fmtCtx = fmtTokens

interface Props {
  modelsData: ModelsSettingsData | null
  currentModel: string
  onSelectModel: (m: string) => void
  effort: ReasoningLevel
  onEffortChange: (e: ReasoningLevel) => void
  contextWindow: number
  onContextWindowChange: (c: number) => void
  onOpenSettings?: (tab?: string, modelId?: string) => void
}

export const ModelPicker: React.FC<Props> = ({
  modelsData,
  currentModel,
  onSelectModel,
  effort,
  onEffortChange,
  contextWindow,
  onContextWindowChange,
  onOpenSettings,
}) => {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const [hoveredModelId, setHoveredModelId] = useState<string>(currentModel)

  // 整理所有可用模型（来自各供应商配置，按供应商归属提取真实参数）
  const allModels = useMemo<EnrichedModelInfo[]>(() => {
    const list: EnrichedModelInfo[] = []
    const seen = new Set<string>()

    const providers = modelsData?.providers || []
    for (const prov of providers) {
      if (prov.enabled === false) continue
      for (const m of prov.models || []) {
        const id = m.name || m.id
        if (!id || seen.has(id)) continue
        seen.add(id)

        const ctx = m.context_window ?? m.context_length ?? 128000
        const maxTok = m.max_tokens ?? m.max_output_tokens ?? 128000
        const inTypes = m.input_types && m.input_types.length > 0
          ? m.input_types
          : (m.capabilities?.includes("vision") ? ["text", "image"] : ["text"])
        const outTypes = m.output_types && m.output_types.length > 0 ? m.output_types : ["text"]
        const supportsTools = m.supports_function_calling ?? (m.capabilities ? m.capabilities.includes("tools") : true)

        list.push({
          id,
          name: id,
          providerName: prov.name,
          providerId: prov.id,
          contextWindow: ctx,
          maxTokens: maxTok,
          inputTypes: inTypes,
          outputTypes: outTypes,
          supportsTools,
          isBaseline: modelsData?.current === id,
          desc: `${prov.name} 托管模型，支持工程对话与智能体指令分发。`,
        })
      }
    }

    // 如果当前选中的模型不在列表中且已有模型库，提供兜底显示
    if (currentModel && currentModel !== "未配置模型" && seen.size > 0 && !seen.has(currentModel)) {
      list.unshift({
        id: currentModel,
        name: currentModel,
        providerName: "已选模型",
        providerId: "",
        contextWindow: contextWindow || 128000,
        maxTokens: 128000,
        inputTypes: ["text"],
        outputTypes: ["text"],
        supportsTools: true,
        isBaseline: modelsData?.current === currentModel,
        desc: "当前会话选定模型",
      })
    }

    return list
  }, [modelsData, currentModel, contextWindow])

  // 搜索过滤
  const filteredModels = useMemo(() => {
    if (!search.trim()) return allModels
    const q = search.trim().toLowerCase()
    return allModels.filter(
      (m) =>
        m.name.toLowerCase().includes(q) ||
        m.providerName.toLowerCase().includes(q)
    )
  }, [allModels, search])

  // 当前右侧面板聚焦的模型
  const activeDetailModelId = hoveredModelId || currentModel || (allModels[0]?.id ?? "")
  const activeModel = useMemo(() => {
    return allModels.find((m) => m.id === activeDetailModelId) || allModels[0] || {
      id: "未配置模型",
      name: "未配置模型",
      providerName: "无供应商",
      providerId: "",
      contextWindow: 128000,
      maxTokens: 128000,
      inputTypes: ["text"],
      outputTypes: ["text"],
      supportsTools: false,
      isBaseline: false,
    }
  }, [allModels, activeDetailModelId, currentModel])

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        {/* 单胶囊只显示模型名称与思考图标 */}
        <button
          type="button"
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted/70 border border-border/60 transition-all shadow-xs group cursor-pointer"
          title={allModels.length === 0 ? "未配置模型供应商 (点击接入)" : `当前模型: ${currentModel || allModels[0]?.name || "选择模型"}`}
        >
          <Brain className={`h-3.5 w-3.5 shrink-0 ${allModels.length === 0 ? "text-neutral-400" : "text-primary"}`} />
          <span className={`truncate max-w-[130px] ${allModels.length === 0 ? "text-neutral-400 italic font-normal" : "text-foreground font-medium"}`}>
            {allModels.length > 0 ? (currentModel || allModels[0]?.name || "选择模型") : "未配置模型"}
          </span>
          <ChevronDown className="h-3 w-3 opacity-60 group-hover:opacity-100 transition-opacity" />
        </button>
      </PopoverTrigger>

      <PopoverContent
        align="end"
        side="top"
        sideOffset={8}
        className="w-[580px] p-0 overflow-hidden shadow-2xl border border-border/80 rounded-2xl flex divide-x divide-border/60 bg-popover text-popover-foreground z-50"
      >
        {/* 左侧列：已配置大模型列表 */}
        <div className="w-[250px] shrink-0 flex flex-col h-[380px] bg-muted/20">
          {/* 搜索框 */}
          <div className="p-2.5 border-b border-border/50">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索已配置模型..."
                className="h-8 pl-8 text-xs bg-background rounded-lg border-border/70"
              />
            </div>
          </div>

          {/* 模型滚动条目 */}
          <ScrollArea className="flex-1 min-h-0">
            <div className="p-1.5 space-y-0.5">
              {filteredModels.map((m) => {
                const isCurrent = currentModel === m.id
                const isHovered = activeModel.id === m.id

                return (
                  <div
                    key={m.id}
                    onMouseEnter={() => setHoveredModelId(m.id)}
                    onClick={() => {
                      onSelectModel(m.id)
                      if (m.contextWindow) onContextWindowChange(m.contextWindow)
                    }}
                    className={`group flex items-center justify-between px-2.5 py-2 rounded-xl cursor-pointer transition-all text-xs ${
                      isHovered
                        ? "bg-muted text-foreground"
                        : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                    }`}
                  >
                    <div className="flex items-center space-x-1.5 min-w-0 flex-1 pr-1">
                      {isCurrent && (
                        <Check className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
                      )}
                      <span
                        className={`truncate ${
                          isCurrent ? "font-semibold text-foreground" : "font-normal"
                        }`}
                      >
                        {m.name}
                      </span>
                      {m.isBaseline && (
                        <span className="text-[9px] px-1.5 py-0.2 rounded bg-primary/10 text-primary border border-primary/20 shrink-0 font-medium scale-95">
                          默认
                        </span>
                      )}
                    </div>

                    <span className="text-[10px] text-muted-foreground/70 shrink-0 font-mono">
                      {m.providerName}
                    </span>
                  </div>
                )
              })}

              {filteredModels.length === 0 && (
                <div className="py-8 text-center text-xs text-muted-foreground">
                  未找到匹配模型
                </div>
              )}
            </div>
          </ScrollArea>

          {/* 底部设置入口 */}
          <div className="p-2 border-t border-border/50 bg-background/50">
            <button
              type="button"
              onClick={() => {
                setOpen(false)
                onOpenSettings?.("models")
              }}
              className="w-full py-1.5 text-center text-xs text-muted-foreground hover:text-foreground hover:bg-muted/70 rounded-lg transition-colors font-medium flex items-center justify-center gap-1.5 cursor-pointer"
            >
              <Settings className="h-3.5 w-3.5" />
              <span>管理供应商与模型...</span>
            </button>
          </div>
        </div>

        {/* 右侧列：模型真实规格与参数配置 */}
        <div className="flex-1 flex flex-col h-[380px] bg-background p-5 justify-between overflow-y-auto min-h-0">
          <div className="space-y-4">
            {/* 顶部标题与所属供应商 */}
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <span className="font-bold text-base text-foreground tracking-tight truncate pr-2">
                  {activeModel.name}
                </span>
                <Badge variant="outline" className="text-[10px] px-2 py-0.5 shrink-0">
                  {activeModel.providerName}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground leading-relaxed">
                {activeModel.desc}
              </p>
            </div>

            <Separator />

            {/* 模型工程规格 (真实参数，拒绝伪代币计费) */}
            <div className="space-y-2.5 text-xs">
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">上下文窗口</span>
                <span className="font-mono font-medium text-foreground">
                  {fmtTokens(activeModel.contextWindow)}
                </span>
              </div>

              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">最大输出 Token</span>
                <span className="font-mono font-medium text-foreground">
                  {fmtTokens(activeModel.maxTokens)}
                </span>
              </div>

              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">输入支持</span>
                <div className="flex items-center gap-1">
                  {activeModel.inputTypes.map((t) => (
                    <span
                      key={t}
                      className="px-1.5 py-0.5 rounded bg-muted text-[10px] font-medium text-foreground/80 flex items-center gap-1"
                    >
                      {t === "image" ? (
                        <>
                          <ImageIcon className="h-2.5 w-2.5" /> 图片
                        </>
                      ) : t === "pdf" ? (
                        <>
                          <FileText className="h-2.5 w-2.5" /> PDF
                        </>
                      ) : t === "video" ? (
                        <>
                          <Video className="h-2.5 w-2.5" /> 视频
                        </>
                      ) : (
                        "文本"
                      )}
                    </span>
                  ))}
                </div>
              </div>

              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">工具调用能力</span>
                <span className="text-xs font-medium text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
                  <Check className="h-3 w-3 stroke-[2.5]" />
                  支持 Function Calling
                </span>
              </div>
            </div>

            <Separator />

            {/* 思考模式 (Reasoning Effort) 调节 */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground font-medium">
                  深度思考与推理 (Reasoning)
                </span>
                <Switch
                  checked={effort !== "off"}
                  onCheckedChange={(checked) => {
                    onEffortChange(checked ? "medium" : "off")
                  }}
                />
              </div>

              {effort !== "off" ? (
                <div className="flex items-center gap-1 pt-1">
                  {(["low", "medium", "high", "max"] as const).map((lvl) => {
                    const isSelected = effort === lvl
                    return (
                      <button
                        key={lvl}
                        type="button"
                        onClick={() => onEffortChange(lvl)}
                        className={`flex-1 py-1 rounded-lg text-xs font-mono transition-all cursor-pointer ${
                          isSelected
                            ? "bg-amber-500/15 text-amber-600 dark:text-amber-400 font-semibold border border-amber-500/30"
                            : "text-muted-foreground hover:bg-muted/70 hover:text-foreground"
                        }`}
                      >
                        {lvl}
                      </button>
                    )
                  })}
                </div>
              ) : (
                <div className="text-[11px] text-muted-foreground/70">
                  思考已关闭：以标准直出模式响应
                </div>
              )}
            </div>
          </div>

          {/* 快捷点击前往设置修改模型 */}
          <div className="pt-3">
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setOpen(false)
                onOpenSettings?.("models", activeModel.id)
              }}
              className="w-full h-8 text-xs gap-1.5 hover:border-primary/60 hover:text-primary transition-all rounded-lg"
            >
              <Settings className="h-3.5 w-3.5" />
              <span>配置此模型规格参数</span>
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}
