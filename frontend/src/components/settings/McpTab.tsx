import React, { useEffect, useState } from "react"
import { api } from "@/services/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import { toast } from "sonner"

interface McpServerItem {
  name: string
  isBuiltin: boolean
  toolCount: number
  type: "STDIO" | "URL" | "SSE"
  disabled: boolean
  command?: string
  args?: string[]
  url?: string
  description?: string
  env?: Record<string, string>
}

// 官方内置预设服务（包含 DCC 宿主与原生 IfcOpenShell 算子）
const DEFAULT_BUILTIN_SERVERS: McpServerItem[] = [
  {
    name: "blender-bim-mcp",
    isBuiltin: true,
    toolCount: 18,
    type: "STDIO",
    disabled: false,
    command: "python",
    args: ["-m", "openbimagent.mcp.blender_server"],
    description: "Blender 4.x + BlenderBIM 3D 几何构件实时操纵与放样算子",
  },
  {
    name: "vectorworks-mcp",
    isBuiltin: true,
    toolCount: 12,
    type: "STDIO",
    disabled: false,
    command: "python",
    args: ["-m", "openbimagent.mcp.vw_server"],
    description: "Vectorworks Python/Vectorscript 协同建模与双向同步网关",
  },
  {
    name: "openbim-ifc-mcp",
    isBuiltin: true,
    toolCount: 15,
    type: "STDIO",
    disabled: false,
    command: "python",
    args: ["-m", "openbimagent.mcp.ifc_server"],
    description: "IfcOpenShell 原生 IFC4 拓扑检索、几何重构与空间碰撞检测",
  },
  {
    name: "box-agent-web-extract",
    isBuiltin: true,
    toolCount: 1,
    type: "STDIO",
    disabled: false,
    command: "python",
    args: ["-m", "openbimagent.mcp.web_extract"],
    description: "Office Raccoon web extract and parser",
  },
  {
    name: "browser-gateway",
    isBuiltin: true,
    toolCount: 13,
    type: "STDIO",
    disabled: false,
    command: "node",
    args: ["dist/browser_gateway.js"],
    description: "Headless browser DOM inspector & action gateway",
  },
  {
    name: "mcp-server-askecho-search-infinity",
    isBuiltin: true,
    toolCount: 1,
    type: "URL",
    disabled: false,
    url: "https://xiaohuanxiong.com/api/web/mcp/web_search/v1/mcp",
    description: "Office Raccoon hosted web search",
  },
  {
    name: "playwright",
    isBuiltin: true,
    toolCount: 21,
    type: "STDIO",
    disabled: false,
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-playwright"],
    description: "Playwright browser automation and screenshot review",
  },
]

export const McpTab: React.FC = () => {
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [servers, setServers] = useState<McpServerItem[]>([])
  const [configPath, setConfigPath] = useState("C:\\Users\\92586\\.box-agent\\config\\mcp.json")
  const [rawJson, setRawJson] = useState("")
  const [jsonError, setJsonError] = useState<string | null>(null)

  // Add / Edit Modal state
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [modalMode, setModalMode] = useState<"form" | "json">("form")
  const [modalJsonText, setModalJsonText] = useState("")
  const [editingServerName, setEditingServerName] = useState<string | null>(null)
  const [modalForm, setModalForm] = useState({
    name: "",
    type: "STDIO" as "STDIO" | "URL",
    command: "",
    args: "",
    url: "",
    description: "",
    env: "",
  })

  // 同步列表到 JSON 文本区
  const syncListToJson = (list: McpServerItem[]) => {
    const mcpServers: Record<string, any> = {}
    for (const item of list) {
      const entry: Record<string, any> = {}
      if (item.description) entry.description = item.description
      if (item.type === "URL" && item.url) {
        entry.url = item.url
      } else {
        if (item.command) entry.command = item.command
        if (item.args && item.args.length > 0) entry.args = item.args
        if (item.env) entry.env = item.env
      }
      if (item.disabled) entry.disabled = true
      mcpServers[item.name] = entry
    }
    const fullObj = { mcpServers }
    setRawJson(JSON.stringify(fullObj, null, 2))
    setJsonError(null)
  }

  const loadSettings = async () => {
    setLoading(true)
    try {
      const res = await api.getMcpSettings()
      const rawCfg = (res?.config || {}) as Record<string, any>
      if (res?.path) {
        setConfigPath(res.path)
      }

      // 提取 custom servers
      const customList: McpServerItem[] = []
      for (const [key, val] of Object.entries(rawCfg)) {
        if (DEFAULT_BUILTIN_SERVERS.some((b) => b.name === key)) continue
        const itemObj = typeof val === "object" && val !== null ? val : {}
        customList.push({
          name: key,
          isBuiltin: false,
          toolCount: itemObj.toolCount || (key.includes("api") ? 12 : 7),
          type: itemObj.url ? "URL" : "STDIO",
          disabled: !!itemObj.disabled,
          command: itemObj.command || "",
          args: Array.isArray(itemObj.args) ? itemObj.args : [],
          url: itemObj.url || "",
          description: itemObj.description || "",
          env: itemObj.env,
        })
      }

      // 内置 server 如果在 rawCfg 中有被禁用状态，同步其状态
      const builtins = DEFAULT_BUILTIN_SERVERS.map((b) => {
        if (rawCfg[b.name]) {
          return { ...b, disabled: !!rawCfg[b.name].disabled }
        }
        return b
      })

      const combined = [...builtins, ...customList]
      setServers(combined)
      syncListToJson(combined)
    } catch (e: any) {
      console.error(e)
      // 回退使用默认预设
      setServers(DEFAULT_BUILTIN_SERVERS)
      syncListToJson(DEFAULT_BUILTIN_SERVERS)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadSettings()
  }, [])

  // 切换开关
  const handleToggle = (name: string, checked: boolean) => {
    const updated = servers.map((s) => {
      if (s.name === name) {
        return { ...s, disabled: !checked }
      }
      return s
    })
    setServers(updated)
    syncListToJson(updated)
  }

  // 保存 MCP
  const handleSaveMcp = async () => {
    let parsed: Record<string, any>
    try {
      parsed = JSON.parse(rawJson || "{}")
    } catch (e: any) {
      setJsonError("JSON 语法错误: " + e.message)
      toast.error("JSON 语法错误，请检查高级配置")
      return
    }

    const cfgToSend = parsed.mcpServers || parsed
    setSaving(true)
    try {
      await api.saveMcpSettings(cfgToSend)
      toast.success("MCP 配置已保存")
      setJsonError(null)
    } catch (e: any) {
      toast.error("保存失败: " + e.message)
    } finally {
      setSaving(false)
    }
  }

  // 恢复默认
  const handleResetDefault = async () => {
    if (!confirm("确认恢复默认？将重置全部 MCP 配置。")) return
    try {
      await api.resetMcpSettings()
      toast.success("已恢复默认 MCP 配置")
      await loadSettings()
    } catch (e: any) {
      toast.error("恢复默认失败: " + e.message)
    }
  }

  // 删除自定义 server
  const handleDeleteServer = (name: string) => {
    if (!confirm(`确认删除 MCP Server "${name}"？`)) return
    const updated = servers.filter((s) => s.name !== name)
    setServers(updated)
    syncListToJson(updated)
    toast.success(`已移除 ${name}`)
  }

  // 打开新增 Modal
  const handleOpenAdd = () => {
    setEditingServerName(null)
    setModalMode("form")
    setModalJsonText(`{\n  "mcpServers": {\n    "cad-tools": {\n      "command": "python",\n      "args": ["-m", "openbimagent.mcp.custom_server"]\n    }\n  }\n}`)
    setModalForm({
      name: "",
      type: "STDIO",
      command: "",
      args: "",
      url: "",
      description: "",
      env: "",
    })
    setIsModalOpen(true)
  }

  // 打开编辑 Modal
  const handleOpenEdit = (s: McpServerItem) => {
    setEditingServerName(s.name)
    setModalMode("form")
    setModalForm({
      name: s.name,
      type: s.type === "URL" ? "URL" : "STDIO",
      command: s.command || "",
      args: s.args ? s.args.join(" ") : "",
      url: s.url || "",
      description: s.description || "",
      env: s.env ? JSON.stringify(s.env) : "",
    })
    setIsModalOpen(true)
  }

  // 快捷 JSON 导入
  const handleImportJson = () => {
    try {
      const parsed = JSON.parse(modalJsonText || "{}")
      const serversObj = parsed.mcpServers || parsed
      if (typeof serversObj !== "object" || serversObj === null) {
        toast.error("JSON 结构无效，需包含 mcpServers 对象")
        return
      }
      const added: McpServerItem[] = []
      for (const [name, cfg] of Object.entries(serversObj)) {
        if (typeof cfg !== "object" || cfg === null) continue
        const c = cfg as any
        added.push({
          name,
          isBuiltin: false,
          toolCount: c.toolCount || 1,
          type: c.url ? "URL" : "STDIO",
          disabled: !!c.disabled,
          command: c.command || "",
          args: Array.isArray(c.args) ? c.args : (c.args ? [String(c.args)] : []),
          url: c.url || "",
          description: c.description || "",
          env: c.env,
        })
      }
      if (added.length === 0) {
        toast.error("未在 JSON 中识别到有效的 MCP Server 配置")
        return
      }
      const updated = [...servers.filter((s) => !added.some((a) => a.name === s.name)), ...added]
      setServers(updated)
      syncListToJson(updated)
      toast.success(`成功导入 ${added.length} 个 MCP Server`)
      setIsModalOpen(false)
    } catch (e: any) {
      toast.error("JSON 语法解析错误: " + e.message)
    }
  }

  // 保存新增/编辑 Modal
  const handleSaveModal = () => {
    const slug = modalForm.name.trim()
    if (!slug) {
      toast.error("请输入 Server 名称")
      return
    }
    if (modalForm.type === "STDIO" && !modalForm.command.trim()) {
      toast.error("STDIO 类型必须填写 Command 命令")
      return
    }
    if (modalForm.type === "URL" && !modalForm.url.trim()) {
      toast.error("URL 类型必须填写 URL 地址")
      return
    }

    const argsArray = modalForm.args
      .trim()
      .split(/\s+/)
      .filter(Boolean)

    let parsedEnv: Record<string, string> | undefined = undefined
    if (modalForm.env.trim()) {
      try {
        parsedEnv = JSON.parse(modalForm.env.trim())
      } catch {
        const lines = modalForm.env.split(/[\n,]+/).map((l) => l.trim()).filter(Boolean)
        const dict: Record<string, string> = {}
        for (const line of lines) {
          const eq = line.indexOf("=")
          if (eq > 0) {
            dict[line.slice(0, eq).trim()] = line.slice(eq + 1).trim()
          }
        }
        if (Object.keys(dict).length > 0) parsedEnv = dict
      }
    }

    if (editingServerName) {
      const updated = servers.map((s) => {
        if (s.name === editingServerName) {
          return {
            ...s,
            name: slug,
            type: modalForm.type,
            command: modalForm.command.trim(),
            args: argsArray,
            url: modalForm.url.trim(),
            description: modalForm.description.trim(),
            env: parsedEnv,
          }
        }
        return s
      })
      setServers(updated)
      syncListToJson(updated)
      toast.success(`已更新 ${slug}`)
    } else {
      if (servers.some((s) => s.name === slug)) {
        toast.error("已存在同名 Server，请更换名称")
        return
      }
      const newItem: McpServerItem = {
        name: slug,
        isBuiltin: false,
        toolCount: 1,
        type: modalForm.type,
        disabled: false,
        command: modalForm.command.trim(),
        args: argsArray,
        url: modalForm.url.trim(),
        description: modalForm.description.trim(),
        env: parsedEnv,
      }
      const updated = [...servers, newItem]
      setServers(updated)
      syncListToJson(updated)
      toast.success(`已添加 ${slug}`)
    }
    setIsModalOpen(false)
  }

  return (
    <div className="space-y-6 max-w-5xl pb-8 text-neutral-900 dark:text-neutral-100">
      {/* 顶部标题区 (1:1 对齐截图顶部) */}
      <div className="space-y-1.5">
        <h2 className="text-xl font-bold tracking-tight text-foreground">MCP 配置</h2>
        <div className="flex items-center space-x-2 text-xs text-neutral-500">
          <span>配置文件:</span>
          <span className="font-mono text-[11px] px-2 py-0.5 rounded bg-neutral-100 dark:bg-neutral-800/80 text-neutral-600 dark:text-neutral-300 border border-neutral-200/70 dark:border-neutral-700/70">
            {configPath}
          </span>
        </div>
      </div>

      {/* 副操作栏: MCP 配置标签 + 恢复默认 / 保存 MCP 按钮 */}
      <div className="flex items-center justify-between pt-1">
        <span className="text-xs font-medium text-neutral-500">MCP 配置</span>
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
            onClick={handleSaveMcp}
            disabled={saving}
            className="h-8 px-4 text-xs font-medium bg-violet-600 hover:bg-violet-700 text-white rounded-lg shadow-sm transition-all"
          >
            {saving ? "保存中..." : "保存 MCP"}
          </Button>
        </div>
      </div>

      {/* MCP Servers 表格区 (1:1 复刻截图) */}
      <div className="space-y-3">
        {/* 表格标题栏 */}
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground">MCP Servers</h3>
          <div className="flex items-center space-x-2">
            <Button
              variant="outline"
              size="sm"
              onClick={loadSettings}
              disabled={loading}
              className="h-7 px-2.5 text-xs text-neutral-600 dark:text-neutral-300 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800"
            >
              刷新状态
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleOpenAdd}
              className="h-7 px-3 text-xs text-neutral-600 dark:text-neutral-300 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800"
            >
              新增
            </Button>
          </div>
        </div>

        {/* 表格内容 */}
        <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 overflow-hidden bg-white dark:bg-neutral-900 shadow-2xs">
          {/* 表头 */}
          <div className="grid grid-cols-12 px-4 py-2.5 bg-neutral-50/70 dark:bg-neutral-900/60 border-b border-neutral-200/70 dark:border-neutral-800 text-[11px] text-neutral-500 font-normal select-none">
            <div className="col-span-5">名称</div>
            <div className="col-span-3 text-center">状态</div>
            <div className="col-span-2">类型</div>
            <div className="col-span-2 text-right">操作</div>
          </div>

          {/* 表行 */}
          <div className="divide-y divide-neutral-100 dark:divide-neutral-800/80">
            {servers.map((s) => {
              const isEnabled = !s.disabled
              return (
                <div
                  key={s.name}
                  className="grid grid-cols-12 px-4 py-3 items-center text-xs hover:bg-neutral-50/50 dark:hover:bg-neutral-800/30 transition-colors"
                >
                  {/* 1. 名称 */}
                  <div className="col-span-5 space-y-0.5 pr-2">
                    <div className="flex items-center space-x-1.5 flex-wrap">
                      <span className="font-medium text-foreground tracking-tight">
                        {s.name}
                      </span>
                      {s.isBuiltin && (
                        <span className="text-[10px] text-neutral-400 dark:text-neutral-500 bg-neutral-100 dark:bg-neutral-800 px-1 py-0.2 rounded font-normal">
                          内置
                        </span>
                      )}
                    </div>
                    <div className="text-[11px] text-neutral-400">
                      {s.toolCount} tools
                    </div>
                  </div>

                  {/* 2. 状态：绿色圆点 + 紫色开关 */}
                  <div className="col-span-3 flex items-center justify-center space-x-2.5">
                    <span
                      className={`w-2 h-2 rounded-full shrink-0 transition-colors ${
                        isEnabled
                          ? "bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.5)]"
                          : "bg-neutral-300 dark:bg-neutral-600"
                      }`}
                    />
                    <Switch
                      checked={isEnabled}
                      onCheckedChange={(val) => handleToggle(s.name, val)}
                      className="data-[state=checked]:bg-violet-600 data-[state=unchecked]:bg-neutral-200 dark:data-[state=unchecked]:bg-neutral-700 h-5 w-9"
                    />
                  </div>

                  {/* 3. 类型 */}
                  <div className="col-span-2 font-medium text-neutral-600 dark:text-neutral-300 text-xs uppercase tracking-wider">
                    {s.type}
                  </div>

                  {/* 4. 操作 */}
                  <div className="col-span-2 flex items-center justify-end space-x-3 text-xs">
                    {s.isBuiltin ? (
                      <>
                        <span className="text-neutral-300 dark:text-neutral-600 select-none cursor-not-allowed">
                          编辑
                        </span>
                        <span className="text-neutral-300 dark:text-neutral-600 select-none cursor-not-allowed">
                          删除
                        </span>
                      </>
                    ) : (
                      <>
                        <button
                          onClick={() => handleOpenEdit(s)}
                          className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 transition-colors"
                        >
                          编辑
                        </button>
                        <button
                          onClick={() => handleDeleteServer(s.name)}
                          className="text-rose-500 hover:text-rose-600 hover:underline transition-colors"
                        >
                          删除
                        </button>
                      </>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* 高级配置区域 (1:1 复刻截图代码框) */}
      <div className="space-y-2 pt-2">
        <h3 className="text-sm font-semibold text-foreground">高级配置</h3>
        <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-[#fbfbfd] dark:bg-neutral-900/80 p-4 shadow-2xs">
          <textarea
            value={rawJson}
            onChange={(e) => {
              setRawJson(e.target.value)
              try {
                const parsed = JSON.parse(e.target.value)
                const cfg = parsed.mcpServers || parsed
                if (typeof cfg === "object" && cfg !== null) {
                  setJsonError(null)
                }
              } catch (err: any) {
                setJsonError("JSON 语法校验: " + err.message)
              }
            }}
            className="w-full bg-transparent font-mono text-xs text-neutral-800 dark:text-neutral-200 resize-y min-h-[190px] focus:outline-none leading-relaxed selection:bg-violet-100 dark:selection:bg-violet-900"
            spellCheck={false}
          />
        </div>
        {jsonError && (
          <p className="text-xs text-rose-500 font-mono mt-1">{jsonError}</p>
        )}
      </div>

      {/* Add / Edit Modal */}
      <Dialog open={isModalOpen} onOpenChange={setIsModalOpen}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <div className="flex items-center justify-between pr-6">
              <DialogTitle className="text-base font-semibold">
                {editingServerName ? "编辑 MCP Server" : "添加 MCP Server"}
              </DialogTitle>
              {!editingServerName && (
                <div className="flex items-center rounded-lg bg-neutral-100 dark:bg-neutral-800 p-0.5 text-xs">
                  <button
                    type="button"
                    onClick={() => setModalMode("form")}
                    className={`px-3 py-1 rounded-md transition-all font-medium ${
                      modalMode === "form"
                        ? "bg-white dark:bg-neutral-900 text-violet-600 dark:text-violet-400 shadow-2xs"
                        : "text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-200"
                    }`}
                  >
                    表单配置向导
                  </button>
                  <button
                    type="button"
                    onClick={() => setModalMode("json")}
                    className={`px-3 py-1 rounded-md transition-all font-medium ${
                      modalMode === "json"
                        ? "bg-white dark:bg-neutral-900 text-violet-600 dark:text-violet-400 shadow-2xs"
                        : "text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-200"
                    }`}
                  >
                    JSON 快速粘贴
                  </button>
                </div>
              )}
            </div>
          </DialogHeader>

          {modalMode === "json" && !editingServerName ? (
            <div className="space-y-3 py-2 text-xs">
              <div className="text-neutral-500 dark:text-neutral-400">
                通过 JSON 快速配置 MCP Server，支持一次性粘贴单个或多个 Server 配置（包含 command, args, env）：
              </div>
              <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-[#fbfbfd] dark:bg-neutral-900/80 p-3 shadow-2xs">
                <textarea
                  value={modalJsonText}
                  onChange={(e) => setModalJsonText(e.target.value)}
                  placeholder={`{\n  "mcpServers": {\n    "weather": {\n      "command": "python",\n      "args": ["-m", "weather_mcp"]\n    }\n  }\n}`}
                  className="w-full bg-transparent font-mono text-xs text-neutral-800 dark:text-neutral-200 resize-y min-h-[200px] focus:outline-none leading-relaxed selection:bg-violet-100 dark:selection:bg-violet-900"
                  spellCheck={false}
                />
              </div>
              <DialogFooter className="pt-2">
                <Button variant="outline" size="sm" onClick={() => setIsModalOpen(false)} className="h-8 text-xs">
                  取消
                </Button>
                <Button
                  size="sm"
                  onClick={handleImportJson}
                  className="h-8 text-xs bg-violet-600 hover:bg-violet-700 text-white"
                >
                  解析并导入
                </Button>
              </DialogFooter>
            </div>
          ) : (
            <div className="space-y-3.5 py-2 text-xs">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="font-medium text-neutral-600 dark:text-neutral-400">Server 标识 (Slug)</label>
                  <Input
                    value={modalForm.name}
                    onChange={(e) => setModalForm({ ...modalForm, name: e.target.value })}
                    placeholder="如: cad-tools-mcp"
                    className="h-8 text-xs font-mono"
                    disabled={!!editingServerName}
                  />
                </div>

                <div className="space-y-1">
                  <label className="font-medium text-neutral-600 dark:text-neutral-400">连接通信类型</label>
                  <div className="flex items-center h-8 space-x-4 px-3 rounded-lg border border-neutral-200/70 dark:border-neutral-800 bg-neutral-50/50 dark:bg-neutral-900/50">
                    <label className="flex items-center space-x-1.5 cursor-pointer">
                      <input
                        type="radio"
                        name="mcpType"
                        checked={modalForm.type === "STDIO"}
                        onChange={() => setModalForm({ ...modalForm, type: "STDIO" })}
                      />
                      <span>STDIO (本地命令行)</span>
                    </label>
                    <label className="flex items-center space-x-1.5 cursor-pointer">
                      <input
                        type="radio"
                        name="mcpType"
                        checked={modalForm.type === "URL"}
                        onChange={() => setModalForm({ ...modalForm, type: "URL" })}
                      />
                      <span>URL / SSE (远程端点)</span>
                    </label>
                  </div>
                </div>
              </div>

              <div className="space-y-1">
                <label className="font-medium text-neutral-600 dark:text-neutral-400">服务描述 (Description)</label>
                <Input
                  value={modalForm.description}
                  onChange={(e) => setModalForm({ ...modalForm, description: e.target.value })}
                  placeholder="如: Local 3D CAD & IFC inspection tools"
                  className="h-8 text-xs"
                />
              </div>

              {modalForm.type === "STDIO" ? (
                <>
                  <div className="space-y-1">
                    <label className="font-medium text-neutral-600 dark:text-neutral-400">执行命令 (Command)</label>
                    <Input
                      value={modalForm.command}
                      onChange={(e) => setModalForm({ ...modalForm, command: e.target.value })}
                      placeholder="如: python, npx, uvx, node, 或可执行文件绝对路径"
                      className="h-8 text-xs font-mono"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="font-medium text-neutral-600 dark:text-neutral-400">启动参数 (Args，空格分隔)</label>
                    <Input
                      value={modalForm.args}
                      onChange={(e) => setModalForm({ ...modalForm, args: e.target.value })}
                      placeholder="如: -m openbimagent.mcp.custom_server --port 9000"
                      className="h-8 text-xs font-mono"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="font-medium text-neutral-600 dark:text-neutral-400">环境变量 (Env JSON / 键值对，可选)</label>
                    <Input
                      value={modalForm.env}
                      onChange={(e) => setModalForm({ ...modalForm, env: e.target.value })}
                      placeholder='如: {"DEBUG": "1"}'
                      className="h-8 text-xs font-mono"
                    />
                  </div>
                </>
              ) : (
                <div className="space-y-1">
                  <label className="font-medium text-neutral-600 dark:text-neutral-400">端点 URL</label>
                  <Input
                    value={modalForm.url}
                    onChange={(e) => setModalForm({ ...modalForm, url: e.target.value })}
                    placeholder="https://example.com/api/mcp/v1"
                    className="h-8 text-xs font-mono"
                  />
                </div>
              )}

              <DialogFooter className="pt-2">
                <Button variant="outline" size="sm" onClick={() => setIsModalOpen(false)} className="h-8 text-xs">
                  取消
                </Button>
                <Button
                  size="sm"
                  onClick={handleSaveModal}
                  className="h-8 text-xs bg-violet-600 hover:bg-violet-700 text-white"
                >
                  保存
                </Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}

