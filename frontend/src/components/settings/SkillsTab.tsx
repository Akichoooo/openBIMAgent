import React, { useEffect, useState } from "react"
import { api } from "@/services/api"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  BookOpen,
  Sparkles,
  Check,
  RefreshCw,
  Layers,
  Code2,
  Trash2,
  Plus,
  ExternalLink,
  Upload,
} from "lucide-react"
import { toast } from "sonner"

interface SkillItem {
  id: string
  name: string
  version?: string
  description?: string
  isBuiltin?: boolean
  enabled: boolean
  standard?: string
  tools?: string[]
  content?: string
}

const DEFAULT_BUILTIN_SKILLS: SkillItem[] = [
  {
    id: "municipal-drainage-solver",
    name: "市政排水分流与跌水放样算子",
    version: "1.2",
    description: "遵循 GB 50014，计算室外排水管线水力坡度、跌水井标高及流速检算。",
    isBuiltin: true,
    enabled: true,
    standard: "SKILL.MD",
    tools: ["drainage_calc", "invert_level_eval", "hydraulic_slope"],
    content: `---
name: municipal-drainage-solver
version: 1.2
standard: GB 50014-2021
description: 市政排水分流跌水与放样计算算子
triggers:
  - "/solve drainage"
  - "计算管网放样标高"
  - "跌水井水力复核"
allowed-tools:
  - drainage_calc
  - invert_level_eval
  - hydraulic_slope
---
# Municipal Drainage Solver Spec
计算重力流管道充满度、流速校核及跌水衔接。`,
  },
  {
    id: "ifc-collision-detector",
    name: "IFC 空间三维碰撞与净距分析",
    version: "2.0",
    description: "基于 ifcopenshell 与 RTree 索引，秒级扫描给排水与强弱电/燃气管线空间包络间距。",
    isBuiltin: true,
    enabled: true,
    standard: "BUILTIN",
    tools: ["ifc_collision_check", "envelope_buffer"],
    content: `---
name: ifc-collision-detector
version: 2.0
standard: ISO 16739-1:2018 (IFC4)
description: IFC 空间三维碰撞与净距分析内核
triggers:
  - "/evidence"
  - "空间净距冲突扫描"
allowed-tools:
  - ifc_collision_check
  - envelope_buffer
---
# IFC Collision Detector Spec
基于 IFC4 拓扑实体，执行实体间最小水平/垂直净距裁决。`,
  },
  {
    id: "scad-boolean-evaluator",
    name: "OpenSCAD CSG 构造实体几何算子",
    version: "1.0",
    description: "无头 CLI 编译 OpenSCAD 脚本，生成弯头、三通、检查井实体及多视口投影快照。",
    isBuiltin: true,
    enabled: true,
    standard: "BUILTIN",
    tools: ["openscad_render", "csg_boolean_eval"],
    content: `---
name: scad-boolean-evaluator
version: 1.0
standard: OpenSCAD CLI
description: OpenSCAD CSG 构造实体几何算子
triggers:
  - "/scad"
  - "/render"
allowed-tools:
  - openscad_render
  - csg_boolean_eval
---
# OpenSCAD Boolean Evaluator Spec
驱动无头 OpenSCAD 编译器执行三维几何放样与布尔剪切。`,
  },
  {
    id: "national-code-auditor",
    name: "工程管线综合规程合规审计",
    version: "1.5",
    description: "自动加载《城市工程管线综合规划规范 GB 50289-2016》，对所有放样构件执行规则门审计。",
    isBuiltin: true,
    enabled: true,
    standard: "SKILL.MD",
    tools: ["rule_audit_gate", "clearance_verifier"],
    content: `---
name: national-code-auditor
version: 1.5
standard: GB 50289-2016
description: 城市工程管线综合规划规范合规审计
triggers:
  - "/reflect"
  - "规范合规复核"
allowed-tools:
  - rule_audit_gate
  - clearance_verifier
---
# National Code Auditor Spec
对放样工件进行 GB 50289-2016 强阻断条款核验。`,
  },
]

export const SkillsTab: React.FC = () => {
  const [skills, setSkills] = useState<SkillItem[]>(DEFAULT_BUILTIN_SKILLS)
  const [candidates, setCandidates] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedSkill, setSelectedSkill] = useState<SkillItem>(DEFAULT_BUILTIN_SKILLS[0])
  const [approvingId, setApprovingId] = useState<string | null>(null)
  const [discardingId, setDiscardingId] = useState<string | null>(null)

  // Add / Import Skill Modal
  const [isAddModalOpen, setIsAddModalOpen] = useState(false)
  const [skillModalMode, setSkillModalMode] = useState<"create" | "import">("create")
  const [newSkillName, setNewSkillName] = useState("")
  const [newSkillDesc, setNewSkillDesc] = useState("")
  const [newSkillContent, setNewSkillContent] = useState("")
  const fileInputRef = React.useRef<HTMLInputElement | null>(null)

  const handleOpenCreate = () => {
    setSkillModalMode("create")
    setNewSkillName("")
    setNewSkillDesc("")
    setNewSkillContent(`---
name: custom-domain-skill
version: 1.0
standard: GB 50289-2016
description: 自定义领域专业放样与管线规程技能
triggers:
  - "/solve custom"
  - "执行领域特化检算"
allowed-tools:
  - drainage_calc
  - clearance_verifier
---
# 领域执行指南与工法策略
1. 提取工程上下文与空间碰撞几何。
2. 约束门裁决并输出自愈拓扑。`)
    setIsAddModalOpen(true)
  }

  const handleOpenImport = () => {
    setSkillModalMode("import")
    setNewSkillName("")
    setNewSkillDesc("")
    setNewSkillContent("")
    setIsAddModalOpen(true)
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (evt) => {
      const text = String(evt.target?.result || "")
      setNewSkillContent(text)
      const baseName = file.name.replace(/\.md$/i, "")
      if (!newSkillName) setNewSkillName(baseName)
      toast.success(`已载入技能文件: ${file.name}`)
    }
    reader.readAsText(file)
  }

  const loadSkills = async () => {
    setLoading(true)
    try {
      const res = await api.listSkills().catch(() => ({ skills: [], candidates: [] }))
      const backendSkills: any[] = res.skills || []
      const backendCands: any[] = (res.candidates || []).map((cand: any) => {
        if (typeof cand === "string") {
          return {
            id: cand,
            file: cand,
            name: cand.replace(/\.md$/, ""),
            description: "自愈求解沉淀候选经验（待人工复核晋升）",
          }
        }
        return {
          ...cand,
          id: cand.id || cand.file || cand.name,
          file: cand.file || (cand.id?.endsWith(".md") ? cand.id : `${cand.id}.md`),
          name: cand.name || cand.file || cand.id,
          description: cand.description || cand.when_to_use || "来自近期成功规整与跌水自愈闭环交付经验",
        }
      })
      setCandidates(backendCands)

      if (backendSkills.length > 0) {
        const merged = backendSkills.map((bs) => ({
          id: bs.id || bs.name,
          name: bs.name || bs.id,
          version: bs.version || "1.0",
          description: bs.description || "市政 BIM 领域专属自愈与求解技能包。",
          isBuiltin: bs.isBuiltin ?? bs.source !== "user",
          enabled: bs.enabled !== false,
          standard: bs.standard || "SKILL.MD",
          tools: bs.tools || [],
          content: bs.content || `name: ${bs.name}\nversion: ${bs.version || "1.0"}\ndescription: ${bs.description || ""}`,
        }))
        setSkills(merged)
        if (!selectedSkill || !merged.some((s) => s.id === selectedSkill.id)) {
          setSelectedSkill(merged[0])
        }
      } else {
        setSkills(DEFAULT_BUILTIN_SKILLS)
        setSelectedSkill(DEFAULT_BUILTIN_SKILLS[0])
      }
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadSkills()
  }, [])

  const handleToggle = (skillId: string, checked: boolean) => {
    const updated = skills.map((s) => (s.id === skillId ? { ...s, enabled: checked } : s))
    setSkills(updated)
    const target = updated.find((s) => s.id === skillId)
    if (target) {
      toast.success(`技能 "${target.name}" 已${checked ? "启用" : "停用"}`)
    }
  }

  const handleApprove = async (candidateId: string) => {
    if (!candidateId) {
      toast.error("候选标识为空")
      return
    }
    setApprovingId(candidateId)
    try {
      await api.approveSkillCandidate(candidateId)
      toast.success("技能候选已批准晋升为正式技能包")
      await loadSkills()
    } catch (e: any) {
      console.error(e)
      toast.error(e?.message || "批准失败")
    } finally {
      setApprovingId(null)
    }
  }

  const handleDiscard = async (candidateId: string) => {
    if (!confirm("确认丢弃该自愈技能候选吗？")) return
    setDiscardingId(candidateId)
    try {
      await api.discardSkillCandidate(candidateId)
      toast.success("已丢弃技能候选")
      await loadSkills()
    } catch (e) {
      console.error(e)
      toast.error("丢弃失败")
    } finally {
      setDiscardingId(null)
    }
  }

  const handleSaveNewSkill = () => {
    if (!newSkillName.trim()) {
      toast.error("请输入技能名称")
      return
    }
    const slug = newSkillName.trim().toLowerCase().replace(/\s+/g, "-")
    const newSkill: SkillItem = {
      id: slug,
      name: newSkillName.trim(),
      version: "1.0",
      description: newSkillDesc.trim() || "用户自定义工程技能",
      isBuiltin: false,
      enabled: true,
      standard: "SKILL.MD",
      tools: [],
      content: newSkillContent.trim() || `# ${newSkillName}\n${newSkillDesc}`,
    }
    const updated = [...skills, newSkill]
    setSkills(updated)
    setSelectedSkill(newSkill)
    setIsAddModalOpen(false)
    setNewSkillName("")
    setNewSkillDesc("")
    setNewSkillContent("")
    toast.success("新技能已导入并就绪")
  }

  const handleDeleteSkill = (skillId: string) => {
    if (!confirm("确定移除该自定义技能吗？")) return
    const updated = skills.filter((s) => s.id !== skillId)
    setSkills(updated)
    if (selectedSkill?.id === skillId) {
      setSelectedSkill(updated[0] || null)
    }
    toast.success("已移除该技能")
  }

  return (
    <div className="space-y-6 max-w-5xl pb-8 text-neutral-900 dark:text-neutral-100">
      {/* 顶部标题区 (1:1 复刻 MCP 风格) */}
      <div className="space-y-1.5">
        <h2 className="text-xl font-bold tracking-tight text-foreground">技能目录 (Agent Skills)</h2>
        <div className="flex items-center space-x-2 text-xs text-neutral-500">
          <span>技能标准库:</span>
          <span className="font-mono text-[11px] px-2 py-0.5 rounded bg-neutral-100 dark:bg-neutral-800/80 text-neutral-600 dark:text-neutral-300 border border-neutral-200/70 dark:border-neutral-700/70">
            openbimagent.skills · {skills.filter((s) => s.enabled).length} / {skills.length} 个已激活
          </span>
        </div>
      </div>

      {/* 副操作栏: 分类标签 + 刷新 / 导入按钮 */}
      <div className="flex items-center justify-between pt-1">
        <span className="text-xs font-medium text-neutral-500">
          已加载现役技能与演化候选
        </span>
        <div className="flex items-center space-x-2">
          <Button
            variant="outline"
            size="sm"
            onClick={loadSkills}
            disabled={loading}
            className="h-8 px-3 text-xs text-neutral-600 dark:text-neutral-300 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800"
          >
            <RefreshCw className={`h-3 w-3 mr-1.5 ${loading ? "animate-spin" : ""}`} />
            刷新状态
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={handleOpenImport}
            className="h-8 px-3 text-xs text-neutral-600 dark:text-neutral-300 border-neutral-200 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800"
          >
            <Upload className="h-3.5 w-3.5 mr-1.5 text-neutral-500" />
            导入技能包
          </Button>
          <Button
            size="sm"
            onClick={handleOpenCreate}
            className="h-8 px-3.5 text-xs font-medium bg-violet-600 hover:bg-violet-700 text-white rounded-lg shadow-2xs transition-all"
          >
            <Plus className="h-3.5 w-3.5 mr-1" />
            新增技能
          </Button>
        </div>
      </div>

      {/* 待审核的自愈技能候选 (若有，以醒目卡片排布) */}
      {candidates.length > 0 && (
        <div className="space-y-3 p-4 rounded-xl border border-amber-500/40 bg-amber-500/5">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <Sparkles className="h-4 w-4 text-amber-500" />
              <span className="text-xs font-semibold text-foreground">自愈求解沉淀候选 (Skill Candidates)</span>
              <Badge className="bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30 text-[10px]">
                {candidates.length} 项待审批
              </Badge>
            </div>
            <span className="text-[11px] text-neutral-400">来自近期成功规整与跌水自愈闭环</span>
          </div>

          <div className="space-y-2 pt-1">
            {candidates.map((cand) => {
              const cid = cand.id || cand.file || cand.name || (typeof cand === "string" ? cand : "")
              const displayName = cand.name || (cand.file ? cand.file.replace(/\.md$/, "") : "自愈沉淀候选")
              const displayDesc = cand.description || cand.when_to_use || "来自近期成功规整与跌水自愈闭环交付经验"
              return (
                <div
                  key={cid}
                  className="p-3 rounded-lg border border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 flex items-center justify-between text-xs"
                >
                  <div className="space-y-0.5">
                    <div className="font-medium text-foreground">{displayName}</div>
                    <div className="text-[11px] text-neutral-400">{displayDesc}</div>
                  </div>
                  <div className="flex items-center space-x-2 shrink-0">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={discardingId === cid}
                      onClick={() => handleDiscard(cid)}
                      className="h-7 px-2 text-xs text-neutral-500 hover:text-rose-500"
                    >
                      丢弃
                    </Button>
                    <Button
                      size="sm"
                      disabled={approvingId === cid}
                      onClick={() => handleApprove(cid)}
                      className="h-7 px-3 text-xs bg-amber-500 hover:bg-amber-600 text-white"
                    >
                      <Check className="h-3 w-3 mr-1" />
                      准入晋升
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* 技能结构化表格列表 (1:1 对齐 MCP 表格规范) */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground">现役技能清单</h3>
          <span className="text-xs text-neutral-400">点击任意行可实时查阅下方规范定义</span>
        </div>

        <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 overflow-hidden bg-white dark:bg-neutral-900 shadow-2xs">
          {/* 表头 */}
          <div className="grid grid-cols-12 px-4 py-2.5 bg-neutral-50/70 dark:bg-neutral-900/60 border-b border-neutral-200/70 dark:border-neutral-800 text-[11px] text-neutral-500 font-normal select-none">
            <div className="col-span-5">技能名称 / 说明</div>
            <div className="col-span-3 text-center">状态</div>
            <div className="col-span-2">标准规范</div>
            <div className="col-span-2 text-right">操作</div>
          </div>

          {/* 表行 */}
          <div className="divide-y divide-neutral-100 dark:divide-neutral-800/80">
            {skills.map((skill) => {
              const isSelected = selectedSkill?.id === skill.id
              return (
                <div
                  key={skill.id}
                  onClick={() => setSelectedSkill(skill)}
                  className={`grid grid-cols-12 px-4 py-3 items-center text-xs cursor-pointer transition-colors ${
                    isSelected
                      ? "bg-violet-50/60 dark:bg-violet-950/20"
                      : "hover:bg-neutral-50/50 dark:hover:bg-neutral-800/30"
                  }`}
                >
                  {/* 1. 名称与描述 */}
                  <div className="col-span-5 space-y-0.5 pr-2 min-w-0">
                    <div className="flex items-center space-x-1.5 flex-wrap">
                      <span className="font-medium text-foreground tracking-tight truncate">
                        {skill.name}
                      </span>
                      {skill.isBuiltin && (
                        <span className="text-[10px] text-neutral-400 dark:text-neutral-500 bg-neutral-100 dark:bg-neutral-800 px-1 py-0.2 rounded font-normal">
                          内置
                        </span>
                      )}
                      <span className="text-[10px] font-mono text-neutral-400">
                        v{skill.version || "1.0"}
                      </span>
                    </div>
                    <div className="text-[11px] text-neutral-400 truncate">
                      {skill.description}
                    </div>
                  </div>

                  {/* 2. 状态：绿色实心发光圆点 + 紫罗兰 Switch 开关 */}
                  <div
                    className="col-span-3 flex items-center justify-center space-x-2.5"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <span
                      className={`w-2 h-2 rounded-full shrink-0 transition-colors ${
                        skill.enabled
                          ? "bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.5)]"
                          : "bg-neutral-300 dark:bg-neutral-600"
                      }`}
                    />
                    <Switch
                      checked={skill.enabled}
                      onCheckedChange={(val) => handleToggle(skill.id, val)}
                      className="data-[state=checked]:bg-violet-600 data-[state=unchecked]:bg-neutral-200 dark:data-[state=unchecked]:bg-neutral-700 h-5 w-9"
                    />
                  </div>

                  {/* 3. 标准类型 */}
                  <div className="col-span-2 font-medium text-neutral-600 dark:text-neutral-300 text-xs uppercase tracking-wider font-mono">
                    <Badge variant="outline" className="text-[10px] px-1.5 py-0 font-mono">
                      {skill.standard || "SKILL.MD"}
                    </Badge>
                  </div>

                  {/* 4. 操作 */}
                  <div className="col-span-2 flex items-center justify-end space-x-3 text-xs">
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        setSelectedSkill(skill)
                      }}
                      className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 transition-colors"
                    >
                      检视
                    </button>
                    {skill.isBuiltin ? (
                      <span className="text-neutral-300 dark:text-neutral-600 select-none cursor-not-allowed">
                        锁定
                      </span>
                    ) : (
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          handleDeleteSkill(skill.id)
                        }}
                        className="text-rose-500 hover:text-rose-600 hover:underline transition-colors"
                      >
                        删除
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* 选定技能规范定义检视框 (对齐 MCP 高级配置等宽代码容器) */}
      <div className="space-y-2 pt-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Code2 className="h-4 w-4 text-violet-600 dark:text-violet-400" />
            <h3 className="text-sm font-semibold text-foreground">
              SKILL.md 规范定义检视 · {selectedSkill?.name || "未选择"}
            </h3>
          </div>
          <span className="text-[11px] font-mono text-neutral-400">
            {selectedSkill?.id}.skill.yaml
          </span>
        </div>

        <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-[#fbfbfd] dark:bg-neutral-900/80 p-4 shadow-2xs">
          <textarea
            value={selectedSkill?.content || ""}
            readOnly
            className="w-full bg-transparent font-mono text-xs text-neutral-800 dark:text-neutral-200 resize-y min-h-[170px] focus:outline-none leading-relaxed selection:bg-violet-100 dark:selection:bg-violet-900"
            spellCheck={false}
          />
        </div>
      </div>

      {/* 新增 / 导入工程技能弹窗 */}
      <Dialog open={isAddModalOpen} onOpenChange={setIsAddModalOpen}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <div className="flex items-center justify-between pr-6">
              <DialogTitle className="text-base font-semibold">
                {skillModalMode === "create" ? "新建工程技能包 (SKILL.md)" : "导入工程技能包"}
              </DialogTitle>
              <div className="flex items-center rounded-lg bg-neutral-100 dark:bg-neutral-800 p-0.5 text-xs">
                <button
                  type="button"
                  onClick={() => setSkillModalMode("create")}
                  className={`px-3 py-1 rounded-md transition-all font-medium ${
                    skillModalMode === "create"
                      ? "bg-white dark:bg-neutral-900 text-violet-600 dark:text-violet-400 shadow-2xs"
                      : "text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-200"
                  }`}
                >
                  向导新建
                </button>
                <button
                  type="button"
                  onClick={() => setSkillModalMode("import")}
                  className={`px-3 py-1 rounded-md transition-all font-medium ${
                    skillModalMode === "import"
                      ? "bg-white dark:bg-neutral-900 text-violet-600 dark:text-violet-400 shadow-2xs"
                      : "text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-200"
                  }`}
                >
                  文件 / 代码导入
                </button>
              </div>
            </div>
          </DialogHeader>

          {skillModalMode === "import" ? (
            <div className="space-y-3 py-2 text-xs">
              <div className="flex items-center justify-between p-3 rounded-xl border border-dashed border-neutral-300 dark:border-neutral-700 bg-neutral-50/50 dark:bg-neutral-900/50">
                <div className="space-y-0.5">
                  <div className="font-medium text-foreground">从本地读取 SKILL.md</div>
                  <div className="text-[11px] text-neutral-400">支持读取规范 Markdown 与 YAML 前置头</div>
                </div>
                <div>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".md,.markdown,.yaml,.yml"
                    className="hidden"
                    onChange={handleFileChange}
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => fileInputRef.current?.click()}
                    className="h-8 text-xs gap-1.5"
                  >
                    <Upload className="h-3.5 w-3.5" />
                    选择文件
                  </Button>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="font-medium text-neutral-600 dark:text-neutral-400">技能标识 (Slug)</label>
                  <Input
                    value={newSkillName}
                    onChange={(e) => setNewSkillName(e.target.value)}
                    placeholder="如: bridge-pier-clearance"
                    className="h-8 text-xs font-mono"
                  />
                </div>
                <div className="space-y-1">
                  <label className="font-medium text-neutral-600 dark:text-neutral-400">功能简述 (Description)</label>
                  <Input
                    value={newSkillDesc}
                    onChange={(e) => setNewSkillDesc(e.target.value)}
                    placeholder="如: 桥梁桥墩承台避让与基础安全间距检算"
                    className="h-8 text-xs"
                  />
                </div>
              </div>

              <div className="space-y-1">
                <label className="font-medium text-neutral-600 dark:text-neutral-400">SKILL.md 规范文本内容</label>
                <Textarea
                  value={newSkillContent}
                  onChange={(e) => setNewSkillContent(e.target.value)}
                  placeholder={`---\nname: my-skill\nversion: 1.0\ndescription: 详细说明\ntriggers:\n  - "/solve my-task"\nallowed-tools:\n  - tool_a\n---\n# 技能说明\n详细步骤...`}
                  className="font-mono text-xs min-h-[160px]"
                />
              </div>

              <DialogFooter className="pt-2">
                <Button variant="outline" size="sm" onClick={() => setIsAddModalOpen(false)} className="h-8 text-xs">
                  取消
                </Button>
                <Button
                  size="sm"
                  onClick={handleSaveNewSkill}
                  className="h-8 text-xs bg-violet-600 hover:bg-violet-700 text-white"
                >
                  确定导入
                </Button>
              </DialogFooter>
            </div>
          ) : (
            <div className="space-y-3.5 py-2 text-xs">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="font-medium text-neutral-600 dark:text-neutral-400">技能标识 (Slug)</label>
                  <Input
                    value={newSkillName}
                    onChange={(e) => setNewSkillName(e.target.value)}
                    placeholder="如: municipal-drainage-solver"
                    className="h-8 text-xs font-mono"
                  />
                </div>
                <div className="space-y-1">
                  <label className="font-medium text-neutral-600 dark:text-neutral-400">功能描述 (Description)</label>
                  <Input
                    value={newSkillDesc}
                    onChange={(e) => setNewSkillDesc(e.target.value)}
                    placeholder="如: 室外重力流排水水力坡度与跌水高程优化"
                    className="h-8 text-xs"
                  />
                </div>
              </div>

              <div className="space-y-1">
                <label className="font-medium text-neutral-600 dark:text-neutral-400">
                  领域指南与 SKILL.md 规范模版 (YAML + Markdown)
                </label>
                <Textarea
                  value={newSkillContent}
                  onChange={(e) => setNewSkillContent(e.target.value)}
                  className="font-mono text-xs min-h-[180px]"
                  rows={8}
                />
              </div>

              <DialogFooter className="pt-2">
                <Button variant="outline" size="sm" onClick={() => setIsAddModalOpen(false)} className="h-8 text-xs">
                  取消
                </Button>
                <Button
                  size="sm"
                  onClick={handleSaveNewSkill}
                  className="h-8 text-xs bg-violet-600 hover:bg-violet-700 text-white"
                >
                  创建并启用
                </Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
