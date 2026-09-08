import React, { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog"
import { api, ProviderItem, ProviderModel, ModelsSettingsData } from "@/services/api"
import {
  Eye,
  EyeOff,
  Plus,
  Trash2,
  Check,
  Edit2,
  Lock,
  Plug,
  Search,
  RefreshCw,
  Box,
  Info,
  Sparkles,
} from "lucide-react"
import { toast } from "sonner"

interface ModelsTabProps {
  initialEditModel?: string
  onClearInitialEditModel?: () => void
}

function formatContextBadge(tokens?: number): string {
  if (!tokens) return "128K"
  if (tokens >= 1000000) return `${Math.round(tokens / 1000000)}M`
  return `${Math.round(tokens / 1000)}K`
}

export function ModelsTab({ initialEditModel, onClearInitialEditModel }: ModelsTabProps = {}) {
  const [data, setData] = useState<ModelsSettingsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedProvId, setSelectedProvId] = useState<string>("")
  const [showKey, setShowKey] = useState(false)
  const [probeResult, setProbeResult] = useState<Record<string, { latency_ms: number; status: string }>>({})

  // 是否处于“添加供应商”状态（图二形态）
  const [isAddingProvider, setIsAddingProvider] = useState(false)
  const [newProvName, setNewProvName] = useState("")
  const [newProvBaseUrl, setNewProvBaseUrl] = useState("https://api.openai.com/v1")
  const [newProvApiKey, setNewProvApiKey] = useState("")
  const [newProvApiFormat, setNewProvApiFormat] = useState("Chat Completions (/chat/completions)")
  const [newProvModels, setNewProvModels] = useState<ProviderModel[]>([])

  // 是否处于“编辑供应商名称”状态
  const [isEditingProvName, setIsEditingProvName] = useState(false)
  const [editingProvNameVal, setEditingProvNameVal] = useState("")

  // Add / Edit Model Modal state
  const [isModelModalOpen, setIsModelModalOpen] = useState(false)
  const [modelModalTarget, setModelModalTarget] = useState<"current" | "new">("current")
  const [editingModelName, setEditingModelName] = useState<string | null>(null)
  const [modelFormId, setModelFormId] = useState("")
  const [modelFormContext, setModelFormContext] = useState("128000")
  const [modelFormMaxTokens, setModelFormMaxTokens] = useState("65536")
  const [modelFormInputTypes, setModelFormInputTypes] = useState<string[]>(["text"])
  const [modelFormOutputTypes, setModelFormOutputTypes] = useState<string[]>(["text"])
  const [modelFormSupportsFn, setModelFormSupportsFn] = useState(true)

  // 从接口拉取模型列表 (Fetch Models) 弹窗状态
  const [isFetchModalOpen, setIsFetchModalOpen] = useState(false)
  const [fetchingModels, setFetchingModels] = useState(false)
  const [fetchedModels, setFetchedModels] = useState<Array<{ id: string; name: string; input_types?: string[] }>>([])
  const [selectedFetchedModels, setSelectedFetchedModels] = useState<Record<string, boolean>>({})
  const [fetchSearch, setFetchSearch] = useState("")

  // 防止初始模型被重复消费弹出
  const handledModelRef = React.useRef<string | null>(null)

  const loadModels = async () => {
    try {
      setLoading(true)
      const res = await api.getModelsSettings()
      setData(res)
      if (res.providers && res.providers.length > 0 && !selectedProvId && !isAddingProvider) {
        setSelectedProvId(res.providers[0].id)
      }
      if (initialEditModel && res.providers && handledModelRef.current !== initialEditModel) {
        handledModelRef.current = initialEditModel
        for (const prov of res.providers) {
          const matched = (prov.models || []).find(
            (m) => m.id === initialEditModel || m.name === initialEditModel
          )
          if (matched) {
            setSelectedProvId(prov.id)
            setIsAddingProvider(false)
            handleOpenEditModel(matched, "current")
            onClearInitialEditModel?.()
            break
          }
        }
      }
    } catch (e: any) {
      toast.error("加载模型设置失败: " + e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!initialEditModel) {
      handledModelRef.current = null
    }
    loadModels()
  }, [initialEditModel])

  const curProvider = data?.providers.find((p) => p.id === selectedProvId)

  // 静默更新供应商属性（不弹 toast，防打扰）
  const handleUpdateProvider = async (fields: Partial<ProviderItem>, showToast = false) => {
    if (!data || !curProvider) return
    const updatedProviders = data.providers.map((p) => {
      if (p.id === curProvider.id) {
        return { ...p, ...fields }
      }
      return p
    })
    const newData = { ...data, providers: updatedProviders }
    setData(newData)
    try {
      await api.saveModelsSettings(newData)
      if (showToast) {
        toast.success("配置已保存")
      }
    } catch (e: any) {
      toast.error("保存失败: " + e.message)
    }
  }

  const handleSetBaseline = async (modelId: string) => {
    if (!data) return
    const newData = { ...data, current: modelId }
    setData(newData)
    try {
      await api.saveModelsSettings(newData)
      toast.success(`已将 ${modelId} 设为全局默认模型`)
    } catch (e: any) {
      toast.error("设置默认模型失败: " + e.message)
    }
  }

  // 测速探针
  const handleProbe = async (providerId: string, modelId: string) => {
    const key = `${providerId}-${modelId}`
    try {
      toast.loading(`正在测试 ${modelId} 连通性与延迟...`, { id: key })
      const prov = data?.providers.find((p) => p.id === providerId)
      const res = (await api.probeModel(providerId, modelId, {
        base_url: prov?.base_url,
        api_key: prov?.api_key,
      })) as any

      if (res.status === "error") {
        setProbeResult((prev) => {
          const next = { ...prev }
          delete next[key]
          return next
        })
        toast.error(`测速失败: ${res.error || "网络不可达"}`, { id: key })
        return
      }
      setProbeResult((prev) => ({ ...prev, [key]: res }))
      toast.success(`测速成功: ${res.latency_ms}ms`, { id: key })
    } catch (e: any) {
      toast.error("测速失败: " + e.message, { id: key })
    }
  }

  const handleDeleteProvider = async (providerId: string) => {
    if (!confirm("确认删除该供应商及其所有模型吗？")) return
    try {
      await api.deleteProvider(providerId)
      toast.success("供应商已删除")
      await loadModels()
      if (selectedProvId === providerId) {
        setSelectedProvId(data?.providers.find((p) => p.id !== providerId)?.id || "")
      }
    } catch (e: any) {
      toast.error("删除失败: " + e.message)
    }
  }

  // 进入“添加供应商”模式 (图二)
  const handleStartAddProvider = () => {
    setIsAddingProvider(true)
    setNewProvName("")
    setNewProvBaseUrl("https://api.openai.com/v1")
    setNewProvApiKey("")
    setNewProvApiFormat("Chat Completions (/chat/completions)")
    setNewProvModels([])
  }

  // 快速套用供应商预设模板
  const handleApplyPresetTemplate = (name: string, url: string, initialModel?: string) => {
    setNewProvName(name)
    setNewProvBaseUrl(url)
    if (initialModel) {
      const exists = newProvModels.some((m) => m.id === initialModel)
      if (!exists) {
        setNewProvModels([
          {
            id: initialModel,
            name: initialModel,
            context_window: initialModel.includes("6.8") || initialModel.includes("6.7") ? 256000 : 128000,
            context_length: initialModel.includes("6.8") || initialModel.includes("6.7") ? 256000 : 128000,
            max_tokens: 65536,
            max_output_tokens: 65536,
            capabilities: ["tools"],
            input_types: ["text"],
            output_types: ["text"],
            supports_function_calling: true,
          },
        ])
      }
    }
  }

  // 保存新增供应商 (图二右下角「添加供应商」按钮)
  const handleSaveNewProvider = async () => {
    if (!newProvName.trim()) {
      toast.error("请输入供应商名称")
      return
    }
    if (!newProvBaseUrl.trim()) {
      toast.error("请输入 Base URL")
      return
    }
    if (newProvModels.length === 0) {
      toast.error("添加供应商前，请至少添加一个模型")
      return
    }
    if (!data) return

    const newProv: ProviderItem = {
      id: "prov-" + Math.random().toString(36).slice(2, 8),
      name: newProvName.trim(),
      enabled: true,
      base_url: newProvBaseUrl.trim(),
      api_format: newProvApiFormat,
      api_key: newProvApiKey.trim(),
      key_set: Boolean(newProvApiKey.trim()),
      models: newProvModels,
    }

    const newData = {
      ...data,
      providers: [...data.providers, newProv],
      current: data.current || newProvModels[0]?.id || "",
    }
    setData(newData)
    setSelectedProvId(newProv.id)
    setIsAddingProvider(false)

    try {
      await api.saveModelsSettings(newData)
      toast.success(`成功添加供应商: ${newProv.name}`)
    } catch (e: any) {
      toast.error("创建失败: " + e.message)
    }
  }

  // 打开添加模型弹窗
  const handleOpenAddModel = (target: "current" | "new" = "current") => {
    setModelModalTarget(target)
    setEditingModelName(null)
    setModelFormId("")
    setModelFormContext("128000")
    setModelFormMaxTokens("65536")
    setModelFormInputTypes(["text"])
    setModelFormOutputTypes(["text"])
    setModelFormSupportsFn(true)
    setIsModelModalOpen(true)
  }

  // 打开编辑模型弹窗
  const handleOpenEditModel = (m: ProviderModel, target: "current" | "new" = "current") => {
    setModelModalTarget(target)
    setEditingModelName(m.id || m.name)
    setModelFormId(m.id || m.name)
    setModelFormContext(String(m.context_window ?? m.context_length ?? 128000))
    setModelFormMaxTokens(String(m.max_tokens ?? m.max_output_tokens ?? 65536))
    const inTypes =
      m.input_types && m.input_types.length > 0
        ? m.input_types
        : m.capabilities?.includes("vision")
        ? ["text", "image"]
        : ["text"]
    setModelFormInputTypes(inTypes.includes("text") ? inTypes : ["text", ...inTypes])
    setModelFormOutputTypes(m.output_types && m.output_types.length > 0 ? m.output_types : ["text"])
    setModelFormSupportsFn(
      m.supports_function_calling ?? (m.capabilities ? m.capabilities.includes("tools") : true)
    )
    setIsModelModalOpen(true)
  }

  // 保存模型（单条）
  const handleSaveModel = async () => {
    if (!modelFormId.trim()) {
      toast.error("模型 ID 不能为空")
      return
    }
    const ctx = parseInt(modelFormContext) || 128000
    const maxTok = parseInt(modelFormMaxTokens) || 65536
    const inTypes = Array.from(new Set(["text", ...modelFormInputTypes]))
    const outTypes = Array.from(new Set(["text", ...modelFormOutputTypes]))
    const caps = [
      ...(modelFormSupportsFn ? ["tools"] : []),
      ...(inTypes.includes("image") ? ["vision"] : []),
      ...(inTypes.includes("pdf") ? ["pdf"] : []),
      ...(inTypes.includes("video") ? ["video"] : []),
    ]

    const updatedModel: ProviderModel = {
      id: modelFormId.trim(),
      name: modelFormId.trim(),
      context_length: ctx,
      context_window: ctx,
      max_tokens: maxTok,
      max_output_tokens: maxTok,
      input_types: inTypes,
      output_types: outTypes,
      supports_function_calling: modelFormSupportsFn,
      capabilities: caps,
    }

    if (modelModalTarget === "new") {
      if (editingModelName) {
        setNewProvModels((prev) =>
          prev.map((m) => ((m.id === editingModelName || m.name === editingModelName) ? updatedModel : m))
        )
      } else {
        setNewProvModels((prev) => [...prev, updatedModel])
      }
    } else {
      if (!curProvider || !data) return
      let newModels: ProviderModel[]
      if (editingModelName) {
        newModels = (curProvider.models || []).map((m) =>
          (m.id === editingModelName || m.name === editingModelName) ? updatedModel : m
        )
      } else {
        newModels = [...(curProvider.models || []), updatedModel]
      }
      const updated = {
        ...curProvider,
        models: newModels,
      }
      await handleUpdateProvider(updated, false)
    }

    setIsModelModalOpen(false)
  }

  // 删除模型
  const handleDeleteModel = async (modelId: string, target: "current" | "new" = "current") => {
    if (target === "new") {
      setNewProvModels((prev) => prev.filter((m) => m.id !== modelId))
    } else if (curProvider) {
      const updated = {
        ...curProvider,
        models: (curProvider.models || []).filter((m) => m.id !== modelId),
      }
      await handleUpdateProvider(updated, false)
    }
  }

  // 打开“从接口拉取模型”
  const handleOpenFetchModels = async (targetBaseUrl?: string, targetApiKey?: string, targetProvId?: string) => {
    const url = (targetBaseUrl ?? curProvider?.base_url ?? "").trim()
    const key = (targetApiKey ?? curProvider?.api_key ?? "").trim()
    if (!url) {
      toast.error("请先填写 Base URL (API 接口地址)")
      return
    }
    if (!key) {
      toast.error("请先填写 API Key")
      return
    }

    setIsFetchModalOpen(true)
    setFetchingModels(true)
    setFetchedModels([])
    setSelectedFetchedModels({})
    setFetchSearch("")

    try {
      const res = await api.fetchProviderModels({
        providerId: targetProvId,
        base_url: url,
        api_key: key,
      })
      if (res.status === "success" && Array.isArray(res.models) && res.models.length > 0) {
        setFetchedModels(res.models)
        const initSelect: Record<string, boolean> = {}
        res.models.forEach((m) => {
          initSelect[m.id] = true
        })
        setSelectedFetchedModels(initSelect)
        toast.success(`成功探测到 ${res.models.length} 个可用模型`)
      } else {
        toast.error(res.error || "未在远程端点探测到模型列表")
      }
    } catch (e: any) {
      toast.error("探测模型列表失败: " + e.message)
    } finally {
      setFetchingModels(false)
    }
  }

  // 一键应用所选拉取的模型
  const handleApplyFetchedModels = async () => {
    const toAdd = fetchedModels.filter((m) => selectedFetchedModels[m.id])
    if (toAdd.length === 0) {
      toast.error("请至少勾选一个模型")
      return
    }

    const newModelItems: ProviderModel[] = toAdd.map((m) => {
      let ctx = 128000
      let maxTok = 65536
      const idLower = m.id.toLowerCase()
      if (idLower.includes("glm-5") || idLower.includes("v4-flash") || idLower.includes("1m")) {
        ctx = 1000000
        maxTok = 128000
      } else if (idLower.includes("6.8") || idLower.includes("6.7") || idLower.includes("256k")) {
        ctx = 256000
        maxTok = 65536
      } else if (idLower.includes("200k") || idLower.includes("claude-3-5")) {
        ctx = 200000
        maxTok = 8192
      }
      const inTypes = m.input_types || ["text"]
      return {
        id: m.id,
        name: m.id,
        context_window: ctx,
        context_length: ctx,
        max_tokens: maxTok,
        max_output_tokens: maxTok,
        capabilities: ["tools", ...(inTypes.includes("image") ? ["vision"] : [])],
        input_types: inTypes,
        output_types: ["text"],
        supports_function_calling: true,
      }
    })

    if (isAddingProvider) {
      const existingIds = new Set(newProvModels.map((m) => m.id))
      const merged = [...newProvModels, ...newModelItems.filter((m) => !existingIds.has(m.id))]
      setNewProvModels(merged)
      toast.success(`已添加 ${newModelItems.length} 个模型至新供应商`)
    } else if (curProvider) {
      const existingIds = new Set((curProvider.models || []).map((m) => m.id))
      const merged = [...(curProvider.models || []), ...newModelItems.filter((m) => !existingIds.has(m.id))]
      await handleUpdateProvider({ models: merged }, true)
      toast.success(`已添加 ${newModelItems.length} 个模型`)
    }
    setIsFetchModalOpen(false)
  }


  if (loading && !data) {
    return <div className="py-20 text-center text-sm text-muted-foreground">加载模型设置中...</div>
  }

  const customProviders = data?.providers || []

  return (
    <div className="flex gap-6 min-h-[560px]">
      {/* 左侧供应商导轨 (对齐图二/图三) */}
      <div className="w-56 shrink-0 flex flex-col justify-between border-r border-border/70 pr-4 space-y-3">
        <div className="space-y-3">
          <div className="px-1">
            <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider">
              自定义供应商
            </span>
          </div>

          <div className="space-y-1">
            {customProviders.length === 0 && !isAddingProvider && (
              <div className="py-6 text-center text-xs text-muted-foreground border border-dashed rounded-xl px-2">
                暂无供应商
              </div>
            )}
            {customProviders.map((p) => {
              const isSelected = !isAddingProvider && p.id === selectedProvId
              const isEnabled = p.enabled !== false
              return (
                <div
                  key={p.id}
                  onClick={() => {
                    setIsAddingProvider(false)
                    setSelectedProvId(p.id)
                  }}
                  className={`group flex items-center justify-between px-3 py-2 rounded-xl cursor-pointer text-xs font-medium transition-all ${
                    isSelected
                      ? "bg-accent text-accent-foreground border border-border font-semibold shadow-2xs"
                      : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                  }`}
                >
                  <div className="flex items-center gap-2.5 min-w-0">
                    <Box className="h-3.5 w-3.5 shrink-0 opacity-70" />
                    <span className="truncate">{p.name}</span>
                  </div>
                  {/* 状态指示灯 (绿灯启用，灰灯禁用) */}
                  <div
                    className={`h-2 w-2 rounded-full shrink-0 transition-all ${
                      isEnabled
                        ? "bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.5)]"
                        : "bg-neutral-400 opacity-50"
                    }`}
                    title={isEnabled ? "已启用" : "已禁用"}
                  />
                </div>
              )
            })}
          </div>
        </div>

        {/* 添加供应商按钮 */}
        <Button
          variant={isAddingProvider ? "secondary" : "outline"}
          size="sm"
          onClick={handleStartAddProvider}
          className="w-full rounded-xl gap-1.5 text-xs h-9 cursor-pointer"
        >
          <Plus className="h-3.5 w-3.5" />
          <span>添加供应商</span>
        </Button>
      </div>

      {/* 右侧主面板：图二（添加供应商） VS 图三（查看/管理供应商） */}
      <div className="flex-1 space-y-5 min-w-0">
        {isAddingProvider ? (
          <div className="space-y-5">
            <div>
              <h3 className="text-base font-bold text-foreground">添加模型供应商</h3>
              <p className="text-xs text-muted-foreground mt-0.5">
                配置一个完全自定义的 API 端点和初始模型。
              </p>
            </div>

            {/* 快速预设模板 */}
            <div className="p-3 rounded-xl border border-border/60 bg-muted/20 space-y-1.5">
              <span className="text-[11px] text-muted-foreground">快速套用预设模板:</span>
              <div className="flex flex-wrap gap-1.5">
                {[
                  { name: "商汤", url: "https://token.sensenova.cn/v1", model: "sensenova-6.8-flash-lite" },
                  { name: "DeepSeek", url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
                  { name: "智谱 GLM", url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-5.2" },
                  { name: "通义千问", url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
                  { name: "月之暗面", url: "https://api.moonshot.cn/v1", model: "moonshot-v1-128k" },
                  { name: "Ollama 本地", url: "http://localhost:11434/v1", model: "qwen2.5:7b" },
                ].map((tmpl) => (
                  <button
                    key={tmpl.name}
                    type="button"
                    onClick={() => handleApplyPresetTemplate(tmpl.name, tmpl.url, tmpl.model)}
                    className="px-2 py-1 rounded-lg text-[11px] border border-border bg-background hover:bg-muted font-medium text-foreground cursor-pointer transition-colors"
                  >
                    {tmpl.name}
                  </button>
                ))}
              </div>
            </div>

            {/* 表单字段 */}
            <div className="space-y-4">
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-foreground">名称</label>
                <Input
                  value={newProvName}
                  onChange={(e) => setNewProvName(e.target.value)}
                  placeholder="如: 智谱 GLM"
                  className="text-xs h-9"
                />
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-medium text-foreground">Base URL</label>
                <Input
                  value={newProvBaseUrl}
                  onChange={(e) => setNewProvBaseUrl(e.target.value)}
                  placeholder="https://api.example.com/v1"
                  className="font-mono text-xs h-9"
                />
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-medium text-foreground">API Key</label>
                <div className="relative">
                  <Input
                    type={showKey ? "text" : "password"}
                    value={newProvApiKey}
                    onChange={(e) => setNewProvApiKey(e.target.value)}
                    placeholder="输入 API Key"
                    className="font-mono text-xs h-9 pr-9"
                  />
                  <button
                    type="button"
                    onClick={() => setShowKey(!showKey)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground cursor-pointer"
                  >
                    {showKey ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                  </button>
                </div>
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-medium text-foreground">API 格式</label>
                <Select value={newProvApiFormat} onValueChange={setNewProvApiFormat}>
                  <SelectTrigger className="text-xs h-9">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="Chat Completions (/chat/completions)" className="text-xs">
                      OpenAI Chat Completions (/chat/completions)
                    </SelectItem>
                    <SelectItem value="Anthropic Messages (/v1/messages)" className="text-xs">
                      Anthropic Messages (/v1/messages)
                    </SelectItem>
                    <SelectItem value="OpenAI Responses (/v1/responses)" className="text-xs">
                      OpenAI Responses (/v1/responses)
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* 模型列表模块 */}
              <div className="space-y-2 pt-2">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-medium text-foreground">
                    模型列表 ({newProvModels.length})
                  </label>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleOpenFetchModels(newProvBaseUrl, newProvApiKey)}
                      className="h-7 px-2.5 text-xs rounded-lg gap-1 cursor-pointer"
                      title="从端点自动探测模型列表"
                    >
                      <Search className="h-3 w-3 text-sky-600" />
                      <span>从接口拉取模型</span>
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleOpenAddModel("new")}
                      className="h-7 px-2.5 text-xs rounded-lg gap-1 cursor-pointer"
                    >
                      <Plus className="h-3 w-3" />
                      <span>添加模型</span>
                    </Button>
                  </div>
                </div>

                {newProvModels.length === 0 ? (
                  <div className="p-4 rounded-xl border border-dashed border-border/80 bg-muted/10 text-center text-xs text-muted-foreground flex items-center justify-center gap-2">
                    <Info className="h-4 w-4 text-muted-foreground/70" />
                    <span>当前没有配置模型，添加模型后可在聊天中使用。</span>
                  </div>
                ) : (
                  <div className="space-y-1.5">
                    {newProvModels.map((m) => (
                      <div
                        key={m.id}
                        className="flex items-center justify-between p-2.5 rounded-xl border border-border/70 bg-card text-xs"
                      >
                        <span className="font-mono font-medium">{m.id}</span>
                        <div className="flex items-center gap-2">
                          <Badge variant="secondary" className="font-mono text-[10px] px-1.5 py-0">
                            {formatContextBadge(m.context_window || m.context_length)}
                          </Badge>
                          <Button
                            variant="ghost"
                            size="iconSm"
                            onClick={() => handleOpenEditModel(m, "new")}
                            className="h-6 w-6 text-muted-foreground hover:text-foreground cursor-pointer"
                          >
                            <Edit2 className="h-3 w-3" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="iconSm"
                            onClick={() => handleDeleteModel(m.id, "new")}
                            className="h-6 w-6 text-muted-foreground hover:text-destructive cursor-pointer"
                          >
                            <Trash2 className="h-3 w-3" />
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* 底部保存与提示 (对齐图二) */}
            <div className="flex items-center justify-between pt-4 border-t border-border/60">
              <span className="text-xs text-muted-foreground flex items-center gap-1.5">
                <Info className="h-3.5 w-3.5" />
                <span>添加供应商前，请至少添加一个模型。</span>
              </span>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setIsAddingProvider(false)}
                  className="h-8 text-xs cursor-pointer"
                >
                  取消
                </Button>
                <Button
                  size="sm"
                  onClick={handleSaveNewProvider}
                  disabled={!newProvName.trim() || !newProvBaseUrl.trim() || newProvModels.length === 0}
                  className="h-8 px-4 text-xs bg-black text-white hover:bg-neutral-800 dark:bg-white dark:text-black dark:hover:bg-neutral-200 cursor-pointer"
                >
                  添加供应商
                </Button>
              </div>
            </div>
          </div>
        ) : curProvider ? (
          <div className="space-y-4">
            {/* 头部：名称 + 启用/禁用药丸切换 + 删除垃圾桶 */}
            <div className="flex items-center justify-between pb-2 border-b border-border/60">
              <div className="flex items-center gap-3">
                {isEditingProvName ? (
                  <div className="flex items-center gap-1.5">
                    <Input
                      value={editingProvNameVal}
                      onChange={(e) => setEditingProvNameVal(e.target.value)}
                      className="h-7 text-xs w-40"
                      autoFocus
                    />
                    <Button
                      size="iconSm"
                      variant="ghost"
                      onClick={() => {
                        if (editingProvNameVal.trim()) {
                          handleUpdateProvider({ name: editingProvNameVal.trim() }, true)
                        }
                        setIsEditingProvName(false)
                      }}
                      className="h-7 w-7 text-emerald-600 cursor-pointer"
                    >
                      <Check className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <h3 className="text-base font-bold text-foreground">{curProvider.name}</h3>
                    <button
                      type="button"
                      onClick={() => {
                        setEditingProvNameVal(curProvider.name)
                        setIsEditingProvName(true)
                      }}
                      className="text-muted-foreground hover:text-foreground cursor-pointer p-0.5 rounded"
                      title="重命名供应商"
                    >
                      <Edit2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                )}

                {/* 已启用 / 禁用 (对齐图三开关样式) */}
                <div className="inline-flex rounded-full bg-muted/60 p-0.5 border border-border/60">
                  <button
                    onClick={() => handleUpdateProvider({ enabled: true })}
                    className={`px-3 py-0.5 rounded-full text-xs font-medium transition-all cursor-pointer ${
                      curProvider.enabled !== false
                        ? "bg-emerald-600 text-white font-semibold shadow-xs"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    已启用
                  </button>
                  <button
                    onClick={() => handleUpdateProvider({ enabled: false })}
                    className={`px-3 py-0.5 rounded-full text-xs font-medium transition-all cursor-pointer ${
                      curProvider.enabled === false
                        ? "bg-neutral-600 text-white font-semibold shadow-xs"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    禁用
                  </button>
                </div>
              </div>

              <Button
                variant="ghost"
                size="iconSm"
                onClick={() => handleDeleteProvider(curProvider.id)}
                className="text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded-lg cursor-pointer"
                title="删除供应商"
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>

            {/* 供应商基础配置卡片 (无打扰静默保存) */}
            <div className="space-y-3 text-xs">
              <div className="space-y-1">
                <label className="text-muted-foreground font-medium">Base URL</label>
                <Input
                  defaultValue={curProvider.base_url || ""}
                  placeholder="https://token.sensenova.cn/v1"
                  className="font-mono text-xs h-9"
                  onBlur={(e) => handleUpdateProvider({ base_url: e.target.value.trim() })}
                />
              </div>

              <div className="space-y-1">
                <label className="text-muted-foreground font-medium">API 格式</label>
                <Select
                  defaultValue={curProvider.api_format || "Chat Completions (/chat/completions)"}
                  onValueChange={(val) => handleUpdateProvider({ api_format: val })}
                >
                  <SelectTrigger className="text-xs h-9">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="Chat Completions (/chat/completions)" className="text-xs">
                      OpenAI Chat Completions (/chat/completions)
                    </SelectItem>
                    <SelectItem value="Anthropic Messages (/v1/messages)" className="text-xs">
                      Anthropic Messages (/v1/messages)
                    </SelectItem>
                    <SelectItem value="OpenAI Responses (/v1/responses)" className="text-xs">
                      OpenAI Responses (/v1/responses)
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <label className="text-muted-foreground font-medium">API Key</label>
                  {curProvider.key_set && (
                    <span className="text-emerald-500 text-[11px]">● 已保存有效密钥</span>
                  )}
                </div>
                <div className="relative">
                  <Input
                    type={showKey ? "text" : "password"}
                    defaultValue={curProvider.api_key || ""}
                    placeholder="输入或更新 API Key"
                    className="font-mono text-xs h-9 pr-9"
                    onBlur={(e) => handleUpdateProvider({ api_key: e.target.value.trim() })}
                  />
                  <button
                    type="button"
                    onClick={() => setShowKey(!showKey)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground cursor-pointer"
                  >
                    {showKey ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                  </button>
                </div>
              </div>
            </div>

            {/* 模型列表 (对齐图三卡片排布) */}
            <div className="space-y-2 pt-2 border-t border-border/60">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-foreground">
                  模型列表 ({curProvider.models?.length || 0})
                </span>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleOpenFetchModels(curProvider.base_url, curProvider.api_key, curProvider.id)}
                    className="h-7 px-2.5 text-xs rounded-lg gap-1 cursor-pointer"
                    title="从 Base URL 自动探测支持的模型列表"
                  >
                    <Search className="h-3 w-3 text-sky-600" />
                    <span>从接口拉取模型</span>
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleOpenAddModel("current")}
                    className="h-7 px-2.5 text-xs rounded-lg gap-1 cursor-pointer"
                  >
                    <Plus className="h-3 w-3" />
                    <span>添加模型</span>
                  </Button>
                </div>
              </div>

              <div className="space-y-2">
                {(curProvider.models || []).map((m) => {
                  const isBaseline = data?.current === m.id
                  const probeKey = `${curProvider.id}-${m.id}`
                  const probe = probeResult[probeKey]

                  return (
                    <div
                      key={m.id}
                      className={`flex items-center justify-between px-3.5 py-2.5 rounded-xl border transition-all ${
                        isBaseline
                          ? "bg-primary/5 border-primary/50 shadow-2xs"
                          : "bg-card border-border/80 hover:border-border hover:bg-muted/10"
                      }`}
                    >
                      <div className="flex items-center gap-2.5 min-w-0 pr-2">
                        <span className="font-mono text-xs font-medium text-foreground truncate">
                          {m.name || m.id}
                        </span>
                        {isBaseline && (
                          <Badge variant="default" className="text-[10px] h-4 px-1.5 bg-primary">
                            默认模型
                          </Badge>
                        )}
                        {probe && probe.status === "success" && (
                          <Badge
                            variant="outline"
                            className="text-[10px] h-4 px-1.5 text-emerald-600 border-emerald-500/40 font-mono"
                          >
                            {probe.latency_ms}ms
                          </Badge>
                        )}
                      </div>

                      <div className="flex items-center gap-2 shrink-0">
                        {/* 上下文窗口胶囊 (对齐图三 1M / 256K 样式) */}
                        <Badge
                          variant="secondary"
                          className="text-[11px] font-mono px-2 py-0.5 rounded-md font-medium"
                        >
                          {formatContextBadge(m.context_window || m.context_length)}
                        </Badge>

                        {/* 测速插头图标 (对齐图三插头图标) */}
                        <Button
                          variant="ghost"
                          size="iconSm"
                          onClick={() => handleProbe(curProvider.id, m.id)}
                          className="h-7 w-7 text-muted-foreground hover:text-amber-500 rounded-lg cursor-pointer"
                          title="测试连通性与测速探针"
                        >
                          <Plug className="h-3.5 w-3.5" />
                        </Button>

                        {/* 编辑铅笔图标 */}
                        <Button
                          variant="ghost"
                          size="iconSm"
                          onClick={() => handleOpenEditModel(m, "current")}
                          className="h-7 w-7 text-muted-foreground hover:text-foreground rounded-lg cursor-pointer"
                          title="配置模型参数"
                        >
                          <Edit2 className="h-3.5 w-3.5" />
                        </Button>

                        {/* 设为默认模型 */}
                        {!isBaseline && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleSetBaseline(m.id)}
                            className="h-7 px-2 text-[11px] text-muted-foreground hover:text-primary rounded-lg cursor-pointer"
                          >
                            设为默认
                          </Button>
                        )}

                        {/* 删除垃圾桶图标 */}
                        <Button
                          variant="ghost"
                          size="iconSm"
                          onClick={() => handleDeleteModel(m.id, "current")}
                          className="h-7 w-7 text-muted-foreground hover:text-destructive rounded-lg cursor-pointer"
                          title="删除模型"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </div>
                  )
                })}

                {(!curProvider.models || curProvider.models.length === 0) && (
                  <div className="py-8 text-center text-xs text-muted-foreground border border-dashed rounded-xl">
                    当前没有配置模型，点击右上角「+ 添加模型」或「从接口拉取模型」
                  </div>
                )}
              </div>
            </div>
          </div>
        ) : (
          <div className="py-20 text-center text-xs text-muted-foreground">请选择或添加供应商</div>
        )}
      </div>

      {/* 弹窗 1：添加 / 编辑模型配置弹窗 (去除 32k/64k，增补主流模型) */}
      <Dialog open={isModelModalOpen} onOpenChange={setIsModelModalOpen}>
        <DialogContent className="sm:max-w-lg p-6 bg-background rounded-2xl border border-border shadow-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader className="pb-2">
            <DialogTitle className="text-base font-normal text-foreground">
              {editingModelName ? "编辑模型" : "添加模型"}
            </DialogTitle>
            <DialogDescription className="text-xs text-muted-foreground">
              输入模型 ID 并配置其上下文窗口与参数
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-2">
            {/* 1. 模型 ID */}
            <div className="space-y-1.5">
              <label className="text-xs text-muted-foreground font-normal">模型 ID</label>
              <Input
                value={modelFormId}
                onChange={(e) => setModelFormId(e.target.value)}
                placeholder="如: sensenova-6.8-flash-lite, glm-5.2, deepseek-chat"
                className="h-9 text-xs rounded-xl font-mono bg-background border-border"
              />
            </div>

            {/* 2. 上下文窗口 (去除 32k/64k，保留 128k, 200k, 256k, 1M, 2M) */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="text-xs text-muted-foreground font-normal">
                  上下文窗口 (Context Window)
                </label>
                <div className="flex items-center gap-1 text-[10px]">
                  {[
                    { label: "128K", val: "131072" },
                    { label: "200K", val: "200000" },
                    { label: "256K", val: "256000" },
                    { label: "1M", val: "1000000" },
                    { label: "2M", val: "2000000" },
                  ].map((p) => (
                    <button
                      key={p.label}
                      type="button"
                      onClick={() => setModelFormContext(p.val)}
                      className={`px-1.5 py-0.5 rounded border transition-colors cursor-pointer ${
                        modelFormContext === p.val
                          ? "bg-primary text-primary-foreground border-primary font-medium"
                          : "bg-muted/40 text-muted-foreground border-border hover:text-foreground"
                      }`}
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>
              <Input
                value={modelFormContext}
                onChange={(e) => setModelFormContext(e.target.value)}
                placeholder="128000"
                type="number"
                className="h-9 text-xs rounded-xl font-mono bg-background border-border"
              />
            </div>

            {/* 3. 最大输出 Token */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="text-xs text-muted-foreground font-normal">
                  最大输出 Token (Max Tokens)
                </label>
                <div className="flex items-center gap-1 text-[10px]">
                  {[
                    { label: "8K", val: "8192" },
                    { label: "16K", val: "16384" },
                    { label: "32K", val: "32768" },
                    { label: "64K", val: "65536" },
                    { label: "128K", val: "128000" },
                  ].map((p) => (
                    <button
                      key={p.label}
                      type="button"
                      onClick={() => setModelFormMaxTokens(p.val)}
                      className={`px-1.5 py-0.5 rounded border transition-colors cursor-pointer ${
                        modelFormMaxTokens === p.val
                          ? "bg-primary text-primary-foreground border-primary font-medium"
                          : "bg-muted/40 text-muted-foreground border-border hover:text-foreground"
                      }`}
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>
              <Input
                value={modelFormMaxTokens}
                onChange={(e) => setModelFormMaxTokens(e.target.value)}
                placeholder="65536"
                type="number"
                className="h-9 text-xs rounded-xl font-mono bg-background border-border"
              />
            </div>

            {/* 4. 输入类型 */}
            <div className="space-y-2">
              <label className="text-xs text-muted-foreground font-normal">输入模态支持</label>
              <div className="flex items-center gap-2 flex-wrap">
                {/* 文本: 必选 */}
                <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-border bg-muted/40 text-xs font-medium select-none">
                  <Check className="h-3.5 w-3.5 text-primary" />
                  <span>文本</span>
                  <Lock className="h-3 w-3 text-muted-foreground/60" />
                </div>

                {/* 图片 (视觉) */}
                <button
                  type="button"
                  onClick={() => {
                    setModelFormInputTypes((prev) =>
                      prev.includes("image") ? prev.filter((t) => t !== "image") : [...prev, "image"]
                    )
                  }}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl border text-xs font-medium cursor-pointer transition-all ${
                    modelFormInputTypes.includes("image")
                      ? "border-primary bg-primary/10 text-foreground font-semibold"
                      : "border-border hover:bg-muted text-muted-foreground"
                  }`}
                >
                  <div
                    className={`h-3.5 w-3.5 rounded flex items-center justify-center ${
                      modelFormInputTypes.includes("image")
                        ? "bg-primary text-primary-foreground"
                        : "border border-border"
                    }`}
                  >
                    {modelFormInputTypes.includes("image") && <Check className="h-2.5 w-2.5" />}
                  </div>
                  <span>图片 (视觉)</span>
                </button>

                {/* PDF 文档 */}
                <button
                  type="button"
                  onClick={() => {
                    setModelFormInputTypes((prev) =>
                      prev.includes("pdf") ? prev.filter((t) => t !== "pdf") : [...prev, "pdf"]
                    )
                  }}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl border text-xs font-medium cursor-pointer transition-all ${
                    modelFormInputTypes.includes("pdf")
                      ? "border-primary bg-primary/10 text-foreground font-semibold"
                      : "border-border hover:bg-muted text-muted-foreground"
                  }`}
                >
                  <div
                    className={`h-3.5 w-3.5 rounded flex items-center justify-center ${
                      modelFormInputTypes.includes("pdf")
                        ? "bg-primary text-primary-foreground"
                        : "border border-border"
                    }`}
                  >
                    {modelFormInputTypes.includes("pdf") && <Check className="h-2.5 w-2.5" />}
                  </div>
                  <span>PDF 文档</span>
                </button>
              </div>
            </div>

            {/* 5. 工具与函数调用 */}
            <div className="pt-2 border-t border-border/40">
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={modelFormSupportsFn}
                  onChange={(e) => setModelFormSupportsFn(e.target.checked)}
                  className="rounded border-input text-primary h-3.5 w-3.5"
                />
                <span className="text-xs text-foreground font-medium">
                  支持函数与工具调用 (Function Calling)
                </span>
                <span className="text-[11px] text-muted-foreground">
                  (BIM 几何放样与代码自愈必需)
                </span>
              </label>
            </div>
          </div>

          <DialogFooter className="pt-3 flex justify-end gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsModelModalOpen(false)}
              className="h-8 text-xs cursor-pointer"
            >
              取消
            </Button>
            <Button
              size="sm"
              onClick={handleSaveModel}
              className="h-8 px-5 text-xs bg-black text-white hover:bg-neutral-800 dark:bg-white dark:text-black dark:hover:bg-neutral-200 cursor-pointer"
            >
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ========================================================== */}
      {/* 弹窗 2：从接口拉取可用模型列表 (Fetch Models Modal)         */}
      {/* ========================================================== */}
      <Dialog open={isFetchModalOpen} onOpenChange={setIsFetchModalOpen}>
        <DialogContent className="sm:max-w-md p-5 bg-background rounded-2xl border border-border shadow-2xl">
          <DialogHeader className="pb-1">
            <DialogTitle className="text-base font-bold flex items-center gap-2">
              <Search className="h-4 w-4 text-sky-600" />
              <span>发现可用模型列表</span>
            </DialogTitle>
            <DialogDescription className="text-xs text-muted-foreground">
              已从远程端点 <code>/models</code> 查询到支持的模型
            </DialogDescription>
          </DialogHeader>

          {fetchingModels ? (
            <div className="py-12 flex flex-col items-center justify-center gap-3">
              <RefreshCw className="h-6 w-6 animate-spin text-sky-600" />
              <span className="text-xs text-muted-foreground">正在探测远程端点模型列表...</span>
            </div>
          ) : fetchedModels.length === 0 ? (
            <div className="py-10 text-center space-y-2">
              <Info className="h-6 w-6 text-muted-foreground mx-auto" />
              <p className="text-xs text-muted-foreground">未检测到模型，请核对 Base URL 与 API Key 是否有效</p>
            </div>
          ) : (
            <div className="space-y-3 py-1">
              {/* 搜索与全选控制 */}
              <div className="flex items-center justify-between gap-2">
                <Input
                  value={fetchSearch}
                  onChange={(e) => setFetchSearch(e.target.value)}
                  placeholder="搜索模型..."
                  className="h-8 text-xs"
                />
                <div className="flex items-center gap-1 text-[11px] shrink-0">
                  <button
                    type="button"
                    onClick={() => {
                      const all: Record<string, boolean> = {}
                      fetchedModels.forEach((m) => {
                        all[m.id] = true
                      })
                      setSelectedFetchedModels(all)
                    }}
                    className="text-primary hover:underline cursor-pointer px-1"
                  >
                    全选
                  </button>
                  <span className="text-muted-foreground">/</span>
                  <button
                    type="button"
                    onClick={() => setSelectedFetchedModels({})}
                    className="text-muted-foreground hover:text-foreground cursor-pointer px-1"
                  >
                    清空
                  </button>
                </div>
              </div>

              {/* 模型列表勾选 */}
              <div className="max-h-60 overflow-y-auto space-y-1.5 pr-1">
                {fetchedModels
                  .filter((m) => !fetchSearch || m.id.toLowerCase().includes(fetchSearch.toLowerCase()))
                  .map((m) => {
                    const isChecked = Boolean(selectedFetchedModels[m.id])
                    return (
                      <div
                        key={m.id}
                        onClick={() =>
                          setSelectedFetchedModels((prev) => ({ ...prev, [m.id]: !prev[m.id] }))
                        }
                        className={`flex items-center justify-between p-2 rounded-xl border text-xs cursor-pointer transition-colors ${
                          isChecked
                            ? "border-primary/40 bg-primary/5 text-foreground"
                            : "border-border/70 hover:bg-muted/40 text-muted-foreground"
                        }`}
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <input
                            type="checkbox"
                            checked={isChecked}
                            onChange={() => {}}
                            className="rounded border-input text-primary h-3.5 w-3.5"
                          />
                          <span className="font-mono font-medium truncate">{m.id}</span>
                        </div>
                        {m.input_types?.includes("image") && (
                          <Badge variant="outline" className="text-[9px] px-1 py-0 text-sky-600 border-sky-400">
                            视觉
                          </Badge>
                        )}
                      </div>
                    )
                  })}
              </div>
            </div>
          )}

          <DialogFooter className="pt-2 flex justify-between items-center sm:justify-between">
            <span className="text-[11px] text-muted-foreground">
              已选择 {Object.values(selectedFetchedModels).filter(Boolean).length} / {fetchedModels.length} 个模型
            </span>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setIsFetchModalOpen(false)}
                className="h-8 text-xs cursor-pointer"
              >
                取消
              </Button>
              <Button
                size="sm"
                disabled={Object.values(selectedFetchedModels).filter(Boolean).length === 0}
                onClick={handleApplyFetchedModels}
                className="h-8 px-4 text-xs bg-sky-600 text-white hover:bg-sky-700 cursor-pointer"
              >
                一键添加所选模型
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
