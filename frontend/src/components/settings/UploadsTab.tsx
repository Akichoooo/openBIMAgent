import React, { useEffect, useRef, useState } from "react"
import { api, UploadItem } from "@/services/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Upload,
  FileText,
  Trash2,
  RefreshCw,
  Plus,
  Copy,
  Check,
  HardDrive,
  FileCode,
  Layers,
  ShieldCheck,
  Search,
} from "lucide-react"
import { toast } from "sonner"

// 安全时间格式化，兼容 Compact ISO (20260908T134402Z)、标准 ISO 及时间戳，杜绝 "Invalid Date"
function formatUploadDate(val?: string | number): string {
  if (!val) return "-"
  if (typeof val === "string") {
    const compact = val.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z?$/)
    if (compact) {
      return `${compact[1]}-${compact[2]}-${compact[3]} ${compact[4]}:${compact[5]}`
    }
  }
  const d = new Date(val)
  if (isNaN(d.getTime())) return typeof val === "string" ? val : "-"
  return d.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

// 获取文件扩展名与对应视觉样式
function getFileBadge(filename: string) {
  const ext = filename.split(".").pop()?.toLowerCase() || "file"
  switch (ext) {
    case "ifc":
      return {
        label: "IFC 4",
        className: "bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300 border-blue-200/60 dark:border-blue-900/40",
      }
    case "dxf":
    case "dwg":
      return {
        label: ext.toUpperCase(),
        className: "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300 border-amber-200/60 dark:border-amber-900/40",
      }
    case "pdf":
      return {
        label: "PDF",
        className: "bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300 border-rose-200/60 dark:border-rose-900/40",
      }
    case "csv":
    case "xlsx":
      return {
        label: ext.toUpperCase(),
        className: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300 border-emerald-200/60 dark:border-emerald-900/40",
      }
    default:
      return {
        label: ext.toUpperCase(),
        className: "bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300 border-neutral-200/70 dark:border-neutral-700/70",
      }
  }
}

export const UploadsTab: React.FC = () => {
  const [uploads, setUploads] = useState<UploadItem[]>([])
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [isDragOver, setIsDragOver] = useState(false)
  const [searchQuery, setSearchQuery] = useState("")
  const [copiedSha, setCopiedSha] = useState<string | null>(null)
  const [selectedItem, setSelectedItem] = useState<UploadItem | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const loadUploads = async () => {
    setLoading(true)
    try {
      const res = await api.listUploads().catch(() => ({ uploads: [] }))
      const list = res.uploads || []
      setUploads(list)
      if (list.length > 0 && !selectedItem) {
        setSelectedItem(list[0])
      }
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  const uploadSingleFile = async (file: File) => {
    setUploading(true)
    try {
      toast.loading(`正在上传并计算指纹: ${file.name}...`, { id: "upload-file" })
      await api.uploadFile(file)
      toast.success(`文件 ${file.name} 已安全落盘`, { id: "upload-file" })
      await loadUploads()
    } catch (err: any) {
      toast.error("上传失败: " + err.message, { id: "upload-file" })
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }

  const handleFileInputChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    await uploadSingleFile(file)
  }

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
    const file = e.dataTransfer.files?.[0]
    if (!file) return
    await uploadSingleFile(file)
  }

  const handleDeleteUpload = async (id?: string) => {
    if (!id) return
    if (!confirm("确认删除该工程资料吗？物理文件与 SHA256 索引将一并移除。")) return
    try {
      await api.deleteUpload(id)
      toast.success("附件文件已安全移除")
      if (selectedItem?.id === id) {
        setSelectedItem(null)
      }
      await loadUploads()
    } catch (err: any) {
      toast.error("删除失败: " + err.message)
    }
  }

  const handleCopySha = (sha: string) => {
    navigator.clipboard.writeText(sha)
    setCopiedSha(sha)
    toast.success("SHA256 指纹已复制到剪贴板")
    setTimeout(() => setCopiedSha(null), 2000)
  }

  useEffect(() => {
    loadUploads()
  }, [])

  // 统计指标
  const totalSizeBytes = uploads.reduce((acc, it) => acc + (it.size || 0), 0)
  const totalSizeFormatted =
    totalSizeBytes > 1024 * 1024
      ? `${(totalSizeBytes / (1024 * 1024)).toFixed(2)} MB`
      : `${(totalSizeBytes / 1024).toFixed(1)} KB`

  const cadBimCount = uploads.filter((u) => {
    const ext = u.name.split(".").pop()?.toLowerCase()
    return ext === "ifc" || ext === "dxf" || ext === "dwg"
  }).length

  const filteredUploads = uploads.filter((u) => {
    const kw = searchQuery.trim().toLowerCase()
    if (!kw) return true
    return (
      (u.name || "").toLowerCase().includes(kw) ||
      (u.sha256 || "").toLowerCase().includes(kw)
    )
  })

  return (
    <div className="space-y-6 max-w-5xl pb-8 text-neutral-900 dark:text-neutral-100">
      {/* 顶部标题区 (1:1 对齐 MCP 风格双行结构) */}
      <div className="space-y-1.5">
        <h2 className="text-xl font-bold tracking-tight text-foreground">
          工程资料与附件 (Engineering Data Hub)
        </h2>
        <div className="flex items-center space-x-2 text-xs text-neutral-500">
          <span>存储路径:</span>
          <span className="font-mono text-[11px] px-2 py-0.5 rounded bg-neutral-100 dark:bg-neutral-800/80 text-neutral-600 dark:text-neutral-300 border border-neutral-200/70 dark:border-neutral-700/70">
            out/uploads/ · sha256 manifest 校验就绪
          </span>
        </div>
      </div>

      {/* 指标卡片行 (4 栅格卡片) */}
      <div className="grid grid-cols-4 gap-3">
        <div className="p-3 rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 shadow-2xs space-y-1">
          <div className="flex items-center justify-between text-xs text-neutral-400">
            <span>资料总数</span>
            <FileText className="h-3.5 w-3.5 text-neutral-400" />
          </div>
          <div className="text-lg font-bold text-foreground font-mono">{uploads.length} 项</div>
        </div>

        <div className="p-3 rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 shadow-2xs space-y-1">
          <div className="flex items-center justify-between text-xs text-neutral-400">
            <span>存储占用</span>
            <HardDrive className="h-3.5 w-3.5 text-neutral-400" />
          </div>
          <div className="text-lg font-bold text-foreground font-mono">{totalSizeFormatted}</div>
        </div>

        <div className="p-3 rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 shadow-2xs space-y-1">
          <div className="flex items-center justify-between text-xs text-neutral-400">
            <span>CAD / IFC 底图</span>
            <Layers className="h-3.5 w-3.5 text-violet-500" />
          </div>
          <div className="text-lg font-bold text-violet-600 dark:text-violet-400 font-mono">
            {cadBimCount} 份
          </div>
        </div>

        <div className="p-3 rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 shadow-2xs space-y-1">
          <div className="flex items-center justify-between text-xs text-neutral-400">
            <span>指纹防篡改</span>
            <ShieldCheck className="h-3.5 w-3.5 text-emerald-500" />
          </div>
          <div className="text-xs font-medium text-emerald-600 dark:text-emerald-400 flex items-center pt-1 gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block shrink-0 animate-pulse" />
            SHA256 完整
          </div>
        </div>
      </div>

      {/* 拖拽上传与操作区 */}
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setIsDragOver(true)
        }}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={handleDrop}
        className={`relative rounded-xl border border-dashed transition-all p-5 text-center flex flex-col items-center justify-center gap-2 ${
          isDragOver
            ? "border-violet-500 bg-violet-50/40 dark:bg-violet-950/20"
            : "border-neutral-200 dark:border-neutral-800 bg-neutral-50/40 dark:bg-neutral-900/40 hover:bg-neutral-50/80 dark:hover:bg-neutral-900/70"
        }`}
      >
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          onChange={handleFileInputChange}
        />
        <div className="w-10 h-10 rounded-full bg-violet-100 dark:bg-violet-950/60 flex items-center justify-center text-violet-600 dark:text-violet-400">
          <Upload className="h-5 w-5" />
        </div>
        <div className="space-y-0.5">
          <div className="text-xs font-semibold text-foreground">
            拖拽工程文件至此处，或{" "}
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="text-violet-600 dark:text-violet-400 hover:underline font-bold"
            >
              点击浏览选择
            </button>
          </div>
          <p className="text-[11px] text-neutral-400">
            支持 IFC 4、AutoCAD DXF/DWG、地质勘察 PDF、管线测量 CSV（单文件上限 64MB）
          </p>
        </div>
      </div>

      {/* 副操作栏: 搜索与刷新 */}
      <div className="flex items-center justify-between gap-2 pt-1">
        <div className="relative w-64 shrink-0">
          <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-neutral-400" />
          <Input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="搜索附件名称或 SHA256..."
            className="pl-8 h-7 text-xs rounded-lg bg-neutral-50/70 dark:bg-neutral-900"
          />
        </div>

        <div className="flex items-center space-x-2">
          <Button
            variant="outline"
            size="sm"
            onClick={loadUploads}
            disabled={loading}
            className="h-7 px-2.5 text-xs text-neutral-600 dark:text-neutral-300 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800"
          >
            <RefreshCw className={`h-3 w-3 mr-1 ${loading ? "animate-spin" : ""}`} />
            刷新
          </Button>
          <Button
            size="sm"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="h-7 px-2.5 text-xs bg-violet-600 hover:bg-violet-700 text-white shadow-2xs font-medium"
          >
            <Plus className="h-3 w-3 mr-1" />
            上传附件
          </Button>
        </div>
      </div>

      {/* 附件结构化数据表格 (1:1 对齐 MCP 规范) */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground">已落盘资料清单</h3>
          <span className="text-xs text-neutral-400">点击行可检视下方 Manifest 溯源元数据</span>
        </div>

        <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 overflow-hidden bg-white dark:bg-neutral-900 shadow-2xs">
          {/* 表头 */}
          <div className="grid grid-cols-12 px-4 py-2.5 bg-neutral-50/70 dark:bg-neutral-900/60 border-b border-neutral-200/70 dark:border-neutral-800 text-[11px] text-neutral-500 font-normal select-none">
            <div className="col-span-5">文件名称 / 格式类型</div>
            <div className="col-span-2 text-right">文件大小</div>
            <div className="col-span-3 text-center">SHA256 指纹</div>
            <div className="col-span-2 text-right">上传时间 / 操作</div>
          </div>

          {/* 表行 */}
          {loading ? (
            <div className="py-12 text-center text-xs text-neutral-400">正在检索资料清单...</div>
          ) : filteredUploads.length === 0 ? (
            <div className="py-10 text-center text-xs text-neutral-400">
              {searchQuery ? "未匹配到相关资料" : "暂无上传资料，请在上方区域拖拽上传"}
            </div>
          ) : (
            <div className="divide-y divide-neutral-100 dark:divide-neutral-800/60">
              {filteredUploads.map((it, idx) => {
                const badge = getFileBadge(it.name)
                const isSelected = selectedItem?.id === it.id
                return (
                  <div
                    key={it.id || idx}
                    onClick={() => setSelectedItem(it)}
                    className={`grid grid-cols-12 px-4 py-3 items-center text-xs transition-colors cursor-pointer ${
                      isSelected
                        ? "bg-violet-50/40 dark:bg-violet-950/20"
                        : "hover:bg-neutral-50/60 dark:hover:bg-neutral-800/40"
                    }`}
                  >
                    <div className="col-span-5 flex items-center space-x-2.5 min-w-0 pr-2">
                      <span
                        className={`text-[10px] font-mono px-2 py-0.5 rounded border font-semibold shrink-0 ${badge.className}`}
                      >
                        {badge.label}
                      </span>
                      <span className="font-medium text-foreground truncate">{it.name}</span>
                    </div>

                    <div className="col-span-2 text-right font-mono text-neutral-500 text-[11px]">
                      {it.size > 1024 * 1024
                        ? `${(it.size / (1024 * 1024)).toFixed(2)} MB`
                        : `${(it.size / 1024).toFixed(1)} KB`}
                    </div>

                    <div className="col-span-3 flex items-center justify-center space-x-1 font-mono text-[11px] text-neutral-400">
                      <span>{it.sha256 ? it.sha256.slice(0, 12) + "..." : "-"}</span>
                      {it.sha256 && (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleCopySha(it.sha256)
                          }}
                          className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 p-0.5"
                          title="复制完整 SHA256"
                        >
                          {copiedSha === it.sha256 ? (
                            <Check className="h-3 w-3 text-emerald-500" />
                          ) : (
                            <Copy className="h-3 w-3" />
                          )}
                        </button>
                      )}
                    </div>

                    <div className="col-span-2 flex items-center justify-end space-x-2">
                      <span className="text-[11px] text-neutral-400 font-mono">
                        {formatUploadDate(it.uploaded_at)}
                      </span>
                      {it.id && (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleDeleteUpload(it.id)
                          }}
                          className="p-1 text-neutral-400 hover:text-rose-600 transition-colors"
                          title="移除文件"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* Manifest 溯源与沙箱访问检视框 (等宽代码容器) */}
      <div className="space-y-2 pt-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <FileCode className="h-4 w-4 text-violet-600 dark:text-violet-400" />
            <h3 className="text-sm font-semibold text-foreground">
              资料 Manifest 元数据溯源 · {selectedItem?.name || "未选择"}
            </h3>
          </div>
          <span className="text-[11px] font-mono text-neutral-400">
            {selectedItem ? `out/uploads/${selectedItem.id}` : "out/uploads/index.json"}
          </span>
        </div>

        <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-[#fbfbfd] dark:bg-neutral-900/80 p-4 shadow-2xs">
          <textarea
            value={
              selectedItem
                ? JSON.stringify(
                    {
                      id: selectedItem.id,
                      filename: selectedItem.name,
                      bytes: selectedItem.size,
                      sha256: selectedItem.sha256,
                      stored_path: `out/uploads/${selectedItem.id}`,
                      recorded_at: formatUploadDate(selectedItem.uploaded_at),
                      sandbox_access: "Read-only isolated mounting for IFC & CAD solver pipelines",
                    },
                    null,
                    2
                  )
                : JSON.stringify(
                    {
                      manifest_status: "Indexed",
                      total_records: uploads.length,
                      storage_engine: "Local filesystem with SHA256 checksums",
                    },
                    null,
                    2
                  )
            }
            readOnly
            className="w-full bg-transparent font-mono text-xs text-neutral-800 dark:text-neutral-200 resize-y min-h-[150px] focus:outline-none leading-relaxed selection:bg-violet-100 dark:selection:bg-violet-900"
            spellCheck={false}
          />
        </div>
      </div>
    </div>
  )
}
