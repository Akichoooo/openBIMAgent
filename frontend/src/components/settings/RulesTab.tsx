import React, { useEffect, useState } from "react"
import { api, RuleItem } from "@/services/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import {
  Search,
  RefreshCw,
  Plus,
  ShieldCheck,
  Droplets,
  Snowflake,
  Ruler,
  ChevronsDown,
  Box,
  FileCheck,
  Save,
  RotateCcw,
  Sun,
  Building2,
  Flame,
  LayoutGrid,
  Maximize2,
  Hammer,
} from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "sonner"

// 6 大刚性工程约束参数配置模型
export interface EngineeringConstraintsConfig {
  // 1. 水力坡度与流速保证
  minVelocityDrainage: number // 默认 0.60 m/s (防泥沙沉降淤积)
  maxVelocityDrainage: number // 默认 5.00 m/s (防高速冲刷破坏管衬)
  forbidNegativeSlope: boolean // 默认 true (严禁重力流逆向倒坡)

  // 2. 覆土深度与季节性防冻线
  localFrostDepthM: number // 默认 0.80 m (哈尔滨可调至 1.6m, 三亚可设 0m)
  frostSafetyMarginM: number // 默认 0.15 m (管顶低于冻土层)
  trafficRoadCoverMinM: number // 默认 0.70 m (车行道最小覆土厚度)

  // 3. 垂直交叉与避让层级
  clearanceWaterSewerM: number // 默认 0.40 m (给水与污水垂直安全间距)
  clearancePowerHeatM: number // 默认 0.50 m (强电电缆与热力管净距)
  pressureYieldsGravity: boolean // 默认 true (有压管倒虹吸避让重力管)

  // 4. 跌水井消能设施
  dropManholeThresholdSewerM: number // 默认 1.00 m (污水高差超标加跌水管)
  dropManholeThresholdRainM: number // 默认 1.50 m (雨水高差超标加跌水)

  // 5. 施工检修与软空间包络
  pipeInsulationToleranceMm: number // 默认 50.0 mm (保温/防腐层外包空间)
  manholeWorkingSpaceRadiusM: number // 默认 0.70 m (人工作业回转操作面)

  // 6. IFC 语义与清单挂接
  enforcePsetCommon: boolean // 默认 true (强制输出 Pset_PipeSegmentTypeCommon)
  enforceInvertElevation: boolean // 默认 true (强制挂接管底标高与材质清单)
}

const DEFAULT_CONSTRAINTS: EngineeringConstraintsConfig = {
  minVelocityDrainage: 0.6,
  maxVelocityDrainage: 5.0,
  forbidNegativeSlope: true,
  localFrostDepthM: 0.8,
  frostSafetyMarginM: 0.15,
  trafficRoadCoverMinM: 0.7,
  clearanceWaterSewerM: 0.4,
  clearancePowerHeatM: 0.5,
  pressureYieldsGravity: true,
  dropManholeThresholdSewerM: 1.0,
  dropManholeThresholdRainM: 1.5,
  pipeInsulationToleranceMm: 50.0,
  manholeWorkingSpaceRadiusM: 0.7,
  enforcePsetCommon: true,
  enforceInvertElevation: true,
}

export interface ResidentialConstraintsConfig {
  minSunlightHours: number
  redlineSetbackM: number
  enforceSunlightEnvelope: boolean
  floorHeightM: number
  livingRoomClearHeightM: number
  kitchenClearHeightM: number
  maxEgressDistanceM: number
  stairClearWidthM: number
  enforceDualEgress: boolean
  windowToFloorRatio: number
  insulationThicknessMm: number
  thermalBreakWindows: boolean
  minShearWallMm: number
  structuralGridModuleMm: number
  forbidProtrudingBeams: boolean
  enforcePsetWallCommon: boolean
  enforceDoorWindowSchedule: boolean
}

const DEFAULT_RESIDENTIAL_CONFIG: ResidentialConstraintsConfig = {
  minSunlightHours: 2.0,
  redlineSetbackM: 5.0,
  enforceSunlightEnvelope: true,
  floorHeightM: 3.1,
  livingRoomClearHeightM: 2.7,
  kitchenClearHeightM: 2.3,
  maxEgressDistanceM: 25.0,
  stairClearWidthM: 1.2,
  enforceDualEgress: true,
  windowToFloorRatio: 0.25,
  insulationThicknessMm: 80,
  thermalBreakWindows: true,
  minShearWallMm: 200,
  structuralGridModuleMm: 300,
  forbidProtrudingBeams: true,
  enforcePsetWallCommon: true,
  enforceDoorWindowSchedule: true,
}

export interface SteelConstraintsConfig {
  maxDeflectionRatio: number
  purlinDeflectionRatio: number
  considerCamber: boolean
  maxCompressionSlenderness: number
  maxTensionSlenderness: number
  craneClearanceM: number
  craneVerticalClearanceM: number
  columnFireRatingHours: number
  beamFireRatingHours: number
  enforceIntumescentCoating: boolean
  minBoltEdgeDistanceRatio: number
  weldEnvelopeMm: number
  enforceSteelPset: boolean
}

const DEFAULT_STEEL_CONFIG: SteelConstraintsConfig = {
  maxDeflectionRatio: 400,
  purlinDeflectionRatio: 250,
  considerCamber: true,
  maxCompressionSlenderness: 150,
  maxTensionSlenderness: 300,
  craneClearanceM: 0.4,
  craneVerticalClearanceM: 2.2,
  columnFireRatingHours: 3.0,
  beamFireRatingHours: 2.0,
  enforceIntumescentCoating: true,
  minBoltEdgeDistanceRatio: 1.5,
  weldEnvelopeMm: 12,
  enforceSteelPset: true,
}

const RULE_I18N: Record<string, { title: string; category: string; spec: string; desc: string; val: string }> = {
  // 水力坡度类
  "MU-HYDR-001": {
    title: "污水管网最小自净流速保证",
    category: "水力与坡度",
    spec: "《室外排水设计标准 GB 50014-2020》§4.2.1",
    desc: "污水重力流管道在设计充满度下最小流速不低于 0.60 m/s，防止悬浮物及泥沙沉降淤积导致管网堵塞。",
    val: "v ≥ 0.60 m/s",
  },
  "MU-HYDR-002": {
    title: "排水管道最大极限冲刷流速限制",
    category: "水力与坡度",
    spec: "《室外排水设计标准 GB 50014-2020》§4.2.5",
    desc: "金属管最大允许流速 7.0m/s，混凝土及塑料管不得超过 5.0m/s，防止水力剪切磨蚀管道内衬造成管体穿孔。",
    val: "v ≤ 5.00 m/s",
  },
  "MU-SLOPE-003": {
    title: "重力流管段单向连续水力坡度 (严禁倒坡)",
    category: "水力与坡度",
    spec: "《室外排水设计标准 GB 50014-2020》§4.2.8",
    desc: "下游检查井进水管底标高严禁高于上游出水管底标高，倒坡直接判定为致命设计缺陷。",
    val: "Slope > 0 (No Invert)",
  },

  // 覆土与防冻线类
  "MU-FROST-001": {
    title: "季节性最大冻土线以下安全埋深",
    category: "覆土防冻",
    spec: "《城市工程管线综合规划规范 GB 50289-2016》§4.1.6",
    desc: "严寒和寒冷地区给水及无保温重力管道管顶，必须埋设在当地最大冰冻线以下至少 0.15m，防止管体胀裂。",
    val: "Depth ≥ 冻土线+0.15m",
  },
  "MU-COVER-002": {
    title: "城市机动车道下最小覆土荷载防护",
    category: "覆土防冻",
    spec: "《城市工程管线综合规划规范 GB 50289-2016》§4.1.7",
    desc: "车行道下管顶最小覆土厚度不宜小于 0.70m，低于此值需触发混凝土包封或加套管抗动荷载碾压。",
    val: "Cover ≥ 0.70 m",
  },

  // 空间垂直交叉类
  "MU-CROSS-001": {
    title: "给水管与排水分流管垂直交叉净距",
    category: "空间净距",
    spec: "《城市工程管线综合规划规范 GB 50289-2016》§4.1.11 表 4.1.11",
    desc: "给水管道与排水管道交叉时，垂直净距不应小于 0.40m，给水管应敷设在污水管上方。",
    val: "≥ 0.40 m",
  },
  "MU-CROSS-002": {
    title: "电力电缆与热力管沟空间垂直净距",
    category: "空间净距",
    spec: "《城市工程管线综合规划规范 GB 50289-2016》§4.1.11",
    desc: "强电电缆与热力管道交叉时防热老化绝缘击穿，最小垂直隔离净距为 0.50m。",
    val: "≥ 0.50 m",
  },
  "MU-CLEAR-001:building": {
    title: "建（构）筑物外墙基础安全净距",
    category: "建筑基础",
    spec: "《城市工程管线综合规划规范 GB 50289-2016》§4.1.9 表 4.1.9",
    desc: "地下排水/给水管道与建筑物外墙基础的最小水平净距，防止管道沉降开裂或建筑物基础淘空。",
    val: "≥ 2.50 m",
  },
  "MU-CLEAR-005:water:d_gt_200": {
    title: "给水干管水平避让净距 (DN>200mm)",
    category: "空间净距",
    spec: "《城市工程管线综合规划规范 GB 50289-2016》§4.1.9 表 4.1.9",
    desc: "市政给水主管径大于200mm时与排水分流管线的净距约束，保障供水水质与维修工作面。",
    val: "≥ 1.50 m",
  },

  // 跌水消能类
  "MU-DROP-001": {
    title: "落差大于 1.0m 检查井跌水消能管设置",
    category: "跌水消能",
    spec: "《室外排水设计标准 GB 50014-2020》§4.4.12",
    desc: "支管接入主干管落差大于 1.0m 时应设跌水井，超标落差必须加设消能竖向管，防止剧烈淘刷井底与恶臭逸散。",
    val: "ΔH > 1.00 m (加跌水管)",
  },

  // 软空间包络类
  "MU-SOFT-001": {
    title: "热力/给水管道保温层与检修工作包络",
    category: "施工软空间",
    spec: "《城市工程管线综合规划规范 GB 50289-2016》§4.1.3",
    desc: "空间碰撞检测必须计入保温层、防腐层外皮厚度（50mm）及人工扳手旋拧作业操作包络面。",
    val: "Tolerance ≥ 50 mm",
  },

  // IFC 属性类
  "MU-IFC-001": {
    title: "IFC4 构件通用属性集完备性与清单工程量挂接",
    category: "IFC属性",
    spec: "buildingSMART IFC4 Spec / 《建筑信息模型应用统一标准》",
    desc: "导出的管道实体必须携带 Pset_PipeSegmentTypeCommon、材质规格及管底内壁标高，满足下游算量与水力复核。",
    val: "Pset Completeness 100%",
  },
}

function normalizeRule(r: any): RuleItem {
  const key = r.rule_key || r.id || ""
  const info = RULE_I18N[key] || {}
  const title = r.title || info.title || r.source_rule_id || key || "规范规则"
  const category = r.category || info.category || r.obstacle_category || "空间净距"
  const description =
    r.description ||
    info.desc ||
    r.source_clause ||
    (r.standard_id ? `${r.standard_id} ${r.clause || ""}` : "")
  const condition =
    r.condition ||
    info.val ||
    (r.required_clearance_m !== undefined ? `≥ ${r.required_clearance_m} m` : undefined)
  const severity = (r.severity || r.enforcement || "BLOCKER") as "BLOCKER" | "WARN" | "INFO"

  return {
    id: key || r.id || String(Math.random()),
    title,
    category,
    description,
    severity,
    condition,
  }
}

export const RulesTab: React.FC = () => {
  const [rules, setRules] = useState<RuleItem[]>([])
  const [loadingRules, setLoadingRules] = useState(false)
  const [searchRule, setSearchRule] = useState("")
  const [selectedCategory, setSelectedCategory] = useState<string>("ALL")
  const [selectedRule, setSelectedRule] = useState<RuleItem | null>(null)

  // 6 大刚性约束参数配置 (市政管网)
  const [config, setConfig] = useState<EngineeringConstraintsConfig>(DEFAULT_CONSTRAINTS)
  // 低密洋房与多层住宅配置
  const [resConfig, setResConfig] = useState<ResidentialConstraintsConfig>(DEFAULT_RESIDENTIAL_CONFIG)
  // 工业厂房与大跨度钢结构配置
  const [steelConfig, setSteelConfig] = useState<SteelConstraintsConfig>(DEFAULT_STEEL_CONFIG)

  // 当前生效规范包切换 (市政管网 / 洋房住宅 / 钢结构厂房)
  const [specPack, setSpecPack] = useState<string>(() => {
    return localStorage.getItem("openbim_spec_pack") || "municipal_utility"
  })

  const handleSpecPackChange = (val: string) => {
    setSpecPack(val)
    localStorage.setItem("openbim_spec_pack", val)
    const labels: Record<string, string> = {
      municipal_utility: "市政给排水工程 (GB 50289 / GB 50014)",
      residential_building: "低密洋房与多层住宅 (GB 50352 / GB 50016)",
      steel_structure: "工业厂房与大跨度钢结构 (GB 50017)",
      custom_spec: "项目自定义企标规范包",
    }
    toast.success(`已切换生效规范包: ${labels[val] || val}`)
  }

  // 新增规程弹窗状态
  const [isAddModalOpen, setIsAddModalOpen] = useState(false)
  const [newRuleForm, setNewRuleForm] = useState({
    id: "",
    title: "",
    category: "空间净距",
    severity: "BLOCKER" as "BLOCKER" | "WARN" | "INFO",
    condition: "",
    description: "",
    spec: "",
  })

  // 读取已持久化的工程约束配置
  useEffect(() => {
    try {
      const saved = localStorage.getItem("openbim_engineering_constraints")
      if (saved) setConfig(JSON.parse(saved))
      const savedRes = localStorage.getItem("openbim_residential_constraints")
      if (savedRes) setResConfig(JSON.parse(savedRes))
      const savedSteel = localStorage.getItem("openbim_steel_constraints")
      if (savedSteel) setSteelConfig(JSON.parse(savedSteel))
    } catch {}
  }, [])

  const handleSaveConfig = () => {
    try {
      if (specPack === "residential_building") {
        localStorage.setItem("openbim_residential_constraints", JSON.stringify(resConfig))
        toast.success("已保存洋房住宅工程约束参数！", {
          description: "智能体在建筑形体放样、日照排布与防火疏散中将严格执行此参数集。",
        })
      } else if (specPack === "steel_structure") {
        localStorage.setItem("openbim_steel_constraints", JSON.stringify(steelConfig))
        toast.success("已保存钢结构工程约束参数！", {
          description: "智能体在主梁挠度、构件长细比与吊车走道净空中将严格执行此参数集。",
        })
      } else {
        localStorage.setItem("openbim_engineering_constraints", JSON.stringify(config))
        toast.success("已保存市政管网工程约束参数！", {
          description: "智能体在后续管网放样、碰撞复核与 IFC 导出中将严格执行此参数集。",
        })
      }
    } catch (e: any) {
      toast.error("保存失败: " + e.message)
    }
  }

  const handleResetConfig = () => {
    if (specPack === "residential_building") {
      setResConfig(DEFAULT_RESIDENTIAL_CONFIG)
      try {
        localStorage.setItem("openbim_residential_constraints", JSON.stringify(DEFAULT_RESIDENTIAL_CONFIG))
        toast.info("已恢复《GB 50352 / GB 50016》洋房住宅标准默认参数")
      } catch {}
    } else if (specPack === "steel_structure") {
      setSteelConfig(DEFAULT_STEEL_CONFIG)
      try {
        localStorage.setItem("openbim_steel_constraints", JSON.stringify(DEFAULT_STEEL_CONFIG))
        toast.info("已恢复《GB 50017》钢结构工程标准默认参数")
      } catch {}
    } else {
      setConfig(DEFAULT_CONSTRAINTS)
      try {
        localStorage.setItem("openbim_engineering_constraints", JSON.stringify(DEFAULT_CONSTRAINTS))
        toast.info("已恢复《GB 50289 / GB 50014》市政给排水标准默认参数")
      } catch {}
    }
  }

  const handleOpenAddRule = () => {
    const nextIdx = rules.length + 1
    setNewRuleForm({
      id: `PRJ-CLEAR-00${nextIdx}`,
      title: "",
      category: "项目特规",
      severity: "BLOCKER",
      condition: "≥ 1.50 m",
      description: "",
      spec: "《工程综合设计技术规划细则与安全隔离办法》",
    })
    setIsAddModalOpen(true)
  }

  const handleSaveNewRule = () => {
    const id = newRuleForm.id.trim()
    const title = newRuleForm.title.trim()
    if (!id) {
      toast.error("请输入规程编号 (Rule ID)")
      return
    }
    if (!title) {
      toast.error("请输入规程名称 (Rule Title)")
      return
    }
    if (rules.some((r) => r.id === id)) {
      toast.error("已存在相同编号的规程，请更换编号")
      return
    }
    const customRule: RuleItem = {
      id,
      title,
      category: newRuleForm.category.trim() || "项目特规",
      severity: newRuleForm.severity,
      condition: newRuleForm.condition.trim() || undefined,
      description: newRuleForm.description.trim() || newRuleForm.spec.trim(),
    }
    const updated = [customRule, ...rules]
    setRules(updated)
    setSelectedRule(customRule)
    toast.success(`成功添加约束规程: ${title}`)
    setIsAddModalOpen(false)
  }

  const loadRules = async () => {
    setLoadingRules(true)
    try {
      const res = await api.getRuleTree().catch(() => ({ rules: [] }))
      const rawRules = res && Array.isArray(res.rules) ? res.rules : []
      const norm = rawRules.map(normalizeRule)

      // 融合预置的 6 大刚性约束标准条文
      const existingKeys = new Set(norm.map((r) => r.id))
      const extraDefaults: RuleItem[] = Object.entries(RULE_I18N)
        .filter(([key]) => !existingKeys.has(key))
        .map(([key, info]) => ({
          id: key,
          title: info.title,
          category: info.category,
          severity: key.includes("WARN") ? "WARN" : "BLOCKER",
          condition: info.val,
          description: `${info.spec} · ${info.desc}`,
        }))

      const combined = [...extraDefaults, ...norm]
      setRules(combined)
      if (combined.length > 0 && !selectedRule) {
        setSelectedRule(combined[0])
      }
    } catch (e) {
      console.error(e)
    } finally {
      setLoadingRules(false)
    }
  }

  useEffect(() => {
    loadRules()
  }, [])

  const categories = ["ALL", "水力与坡度", "覆土防冻", "空间净距", "跌水消能", "施工软空间", "IFC属性", "项目特规"]

  const filteredRules = rules.filter((r) => {
    const matchCat = selectedCategory === "ALL" || r.category === selectedCategory
    const kw = (searchRule || "").trim().toLowerCase()
    if (!kw) return matchCat

    const matchTitle = (r.title || "").toLowerCase().includes(kw)
    const matchId = (r.id || "").toLowerCase().includes(kw)
    const matchDesc = (r.description || "").toLowerCase().includes(kw)
    const matchCond = (r.condition || "").toLowerCase().includes(kw)
    return matchCat && (matchTitle || matchId || matchDesc || matchCond)
  })

  return (
    <div className="space-y-6 max-w-5xl pb-10 text-neutral-900 dark:text-neutral-100">
      {/* 顶部标题区 */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-2 border-b border-border/60">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-foreground flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-sky-600 dark:text-sky-400" />
            <span>工程规范与刚性约束 (Engineering Rules)</span>
          </h2>
          <div className="flex items-center gap-2 mt-1.5 text-xs text-muted-foreground">
            <span>当前生效规范包:</span>
            <Select value={specPack} onValueChange={handleSpecPackChange}>
              <SelectTrigger className="h-7 text-xs font-medium w-auto min-w-[320px] bg-background">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="municipal_utility" className="text-xs">
                  市政给排水工程 (GB 50289 / GB 50014)
                </SelectItem>
                <SelectItem value="residential_building" className="text-xs">
                  低密洋房与多层住宅 (GB 50352 / GB 50016)
                </SelectItem>
                <SelectItem value="steel_structure" className="text-xs">
                  工业厂房与大跨度钢结构 (GB 50017)
                </SelectItem>
                <SelectItem value="custom_spec" className="text-xs">
                  项目自定义企标规范包
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleResetConfig}
            className="h-8 text-xs gap-1.5 cursor-pointer"
            title="恢复国家规范初始默认值"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            <span>恢复默认</span>
          </Button>
          <Button
            size="sm"
            onClick={handleSaveConfig}
            className="h-8 text-xs gap-1.5 bg-sky-600 hover:bg-sky-700 text-white shadow-xs cursor-pointer"
          >
            <Save className="h-3.5 w-3.5" />
            <span>保存工程参数</span>
          </Button>
        </div>
      </div>

      {/* 核心第一部分：6 大刚性工程约束可视化控制台 */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <span className="text-xs font-semibold text-foreground uppercase tracking-wider">
              6 大刚性工程算子参数 (Parametric Constraints)
            </span>
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 font-mono">
              确定性代码算子已挂载
            </span>
          </div>
          <span className="text-[11px] text-muted-foreground">
            修改后模型将在生成与自愈循环中即时执行数学门禁
          </span>
        </div>

        {/* 条件渲染 6 大刚性约束卡片：市政给排水 / 洋房住宅 / 工业钢结构 */}
        {specPack === "residential_building" ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5">
            {/* 洋房卡片 1: 日照间距与采光系数 */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-amber-500/10 text-amber-600 dark:text-amber-400">
                      <Sun className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">1. 日照间距与采光系数</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-amber-500/30 text-amber-600 font-mono">
                    GB 50352
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  低密洋房大寒日日照不得少于2小时，严禁阴影包络遮挡邻界地块。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">大寒日有效日照时数</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.5"
                      min="1.0"
                      max="6.0"
                      value={resConfig.minSunlightHours}
                      onChange={(e) =>
                        setResConfig({ ...resConfig, minSunlightHours: parseFloat(e.target.value) || 2.0 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">h</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">用地红线规划退界</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.5"
                      min="1.0"
                      max="20.0"
                      value={resConfig.redlineSetbackM}
                      onChange={(e) =>
                        setResConfig({ ...resConfig, redlineSetbackM: parseFloat(e.target.value) || 5.0 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m</span>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-muted-foreground">强制日照包络三维截切</span>
                  <Switch
                    checked={resConfig.enforceSunlightEnvelope}
                    onCheckedChange={(c) => setResConfig({ ...resConfig, enforceSunlightEnvelope: c })}
                  />
                </div>
              </div>
            </div>

            {/* 洋房卡片 2: 室内净高与层高控制 */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-blue-500/10 text-blue-600 dark:text-blue-400">
                      <Building2 className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">2. 室内净高与层高控制</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-blue-500/30 text-blue-600 font-mono">
                    GB 50096
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  标准层层高与起居室净高红线，扣除梁高、喷淋与地暖找平层厚度。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">标准层设计层高</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.05"
                      min="2.8"
                      max="4.5"
                      value={resConfig.floorHeightM}
                      onChange={(e) =>
                        setResConfig({ ...resConfig, floorHeightM: parseFloat(e.target.value) || 3.1 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">客厅/卧室室内净高红线</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.05"
                      min="2.4"
                      max="3.5"
                      value={resConfig.livingRoomClearHeightM}
                      onChange={(e) =>
                        setResConfig({ ...resConfig, livingRoomClearHeightM: parseFloat(e.target.value) || 2.7 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">厨房/卫生间净高底线</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.05"
                      min="2.0"
                      max="3.0"
                      value={resConfig.kitchenClearHeightM}
                      onChange={(e) =>
                        setResConfig({ ...resConfig, kitchenClearHeightM: parseFloat(e.target.value) || 2.3 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m</span>
                  </div>
                </div>
              </div>
            </div>

            {/* 洋房卡片 3: 防火分区与安全疏散 */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-rose-500/10 text-rose-600 dark:text-rose-400">
                      <Flame className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">3. 防火分区与安全疏散</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-rose-500/30 text-rose-600 font-mono">
                    GB 50016
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  户门至封闭楼梯间疏散距离极限与防烟楼梯间梯段最小有效净宽。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">户门至安全出口极限距离</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="1"
                      min="10"
                      max="50"
                      value={resConfig.maxEgressDistanceM}
                      onChange={(e) =>
                        setResConfig({ ...resConfig, maxEgressDistanceM: parseFloat(e.target.value) || 25.0 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">疏散楼梯梯段净宽</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.05"
                      min="1.0"
                      max="2.5"
                      value={resConfig.stairClearWidthM}
                      onChange={(e) =>
                        setResConfig({ ...resConfig, stairClearWidthM: parseFloat(e.target.value) || 1.2 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m</span>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-muted-foreground">强制双向独立安全疏散口</span>
                  <Switch
                    checked={resConfig.enforceDualEgress}
                    onCheckedChange={(c) => setResConfig({ ...resConfig, enforceDualEgress: c })}
                  />
                </div>
              </div>
            </div>

            {/* 洋房卡片 4: 节能保温与窗墙比 */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-teal-500/10 text-teal-600 dark:text-teal-400">
                      <ShieldCheck className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">4. 节能保温与窗墙面积比</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-teal-500/30 text-teal-600 font-mono">
                    GB 50189
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  严寒/寒冷气候区最大窗墙面积比，外墙外保温岩棉/挤塑板厚度。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">外立面最大窗墙面积比</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.05"
                      min="0.1"
                      max="0.6"
                      value={resConfig.windowToFloorRatio}
                      onChange={(e) =>
                        setResConfig({ ...resConfig, windowToFloorRatio: parseFloat(e.target.value) || 0.25 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">Ratio</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">外墙保温层设计厚度</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="10"
                      min="30"
                      max="200"
                      value={resConfig.insulationThicknessMm}
                      onChange={(e) =>
                        setResConfig({ ...resConfig, insulationThicknessMm: parseFloat(e.target.value) || 80 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">mm</span>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-muted-foreground">断桥铝三玻两腔系统门窗</span>
                  <Switch
                    checked={resConfig.thermalBreakWindows}
                    onCheckedChange={(c) => setResConfig({ ...resConfig, thermalBreakWindows: c })}
                  />
                </div>
              </div>
            </div>

            {/* 洋房卡片 5: 结构开间与剪力墙布置 */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-indigo-500/10 text-indigo-600 dark:text-indigo-400">
                      <LayoutGrid className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">5. 结构开间与剪力墙布置</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-indigo-500/30 text-indigo-600 font-mono">
                    Structure
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  开间模数对齐预制叠合板，严禁在卧室或起居室内侧突兀外露结构柱。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">剪力墙最小截面厚度</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="20"
                      min="160"
                      max="400"
                      value={resConfig.minShearWallMm}
                      onChange={(e) =>
                        setResConfig({ ...resConfig, minShearWallMm: parseFloat(e.target.value) || 200 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">mm</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">建筑轴网标准模数</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="100"
                      min="100"
                      max="1200"
                      value={resConfig.structuralGridModuleMm}
                      onChange={(e) =>
                        setResConfig({ ...resConfig, structuralGridModuleMm: parseFloat(e.target.value) || 300 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">mm</span>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-muted-foreground">起居室严禁凸柱露梁</span>
                  <Switch
                    checked={resConfig.forbidProtrudingBeams}
                    onCheckedChange={(c) => setResConfig({ ...resConfig, forbidProtrudingBeams: c })}
                  />
                </div>
              </div>
            </div>

            {/* 洋房卡片 6: IFC 建筑空间与门窗表 */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                      <FileCheck className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">6. IFC 建筑空间与门窗表</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-emerald-500/30 text-emerald-600 font-mono">
                    IFC4 / Arch
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  规范导出 IfcSpace 空间边界、Pset_WallCommon 及门窗工程量统计表。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">强制输出 IfcSpace 与墙体属性</span>
                  <Switch
                    checked={resConfig.enforcePsetWallCommon}
                    onCheckedChange={(c) => setResConfig({ ...resConfig, enforcePsetWallCommon: c })}
                  />
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">自动挂接门窗编号与五金清单</span>
                  <Switch
                    checked={resConfig.enforceDoorWindowSchedule}
                    onCheckedChange={(c) => setResConfig({ ...resConfig, enforceDoorWindowSchedule: c })}
                  />
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-[11px] text-muted-foreground">空间容积与套内面积核算</span>
                  <span className="text-xs font-semibold text-emerald-600 font-mono">Ready</span>
                </div>
              </div>
            </div>
          </div>
        ) : specPack === "steel_structure" ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5">
            {/* 钢结构卡片 1: 主梁与檩条极限挠度 */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-cyan-500/10 text-cyan-600 dark:text-cyan-400">
                      <Maximize2 className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">1. 主梁与檩条极限挠度</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-cyan-500/30 text-cyan-600 font-mono">
                    GB 50017
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  防止主刚架与大跨度桁架在自重及活荷载下产生过量变形影响外观与吊顶。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">主梁最大挠度允许比 [L/N]</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="50"
                      min="200"
                      max="1000"
                      value={steelConfig.maxDeflectionRatio}
                      onChange={(e) =>
                        setSteelConfig({ ...steelConfig, maxDeflectionRatio: parseFloat(e.target.value) || 400 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">1/N</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">屋面檩条挠度允许比 [L/N]</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="25"
                      min="150"
                      max="500"
                      value={steelConfig.purlinDeflectionRatio}
                      onChange={(e) =>
                        setSteelConfig({ ...steelConfig, purlinDeflectionRatio: parseFloat(e.target.value) || 250 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">1/N</span>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-muted-foreground">大跨度构件恒载起拱设计</span>
                  <Switch
                    checked={steelConfig.considerCamber}
                    onCheckedChange={(c) => setSteelConfig({ ...steelConfig, considerCamber: c })}
                  />
                </div>
              </div>
            </div>

            {/* 钢结构卡片 2: 构件极限长细比控制 */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-orange-500/10 text-orange-600 dark:text-orange-400">
                      <Ruler className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">2. 构件极限长细比控制</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-orange-500/30 text-orange-600 font-mono">
                    Slenderness
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  控制柱截面及屋架受压受拉构件长细比，防止在地震或强风作用下发生屈曲失稳。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">轴心受压立柱长细比限值 λ</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="10"
                      min="50"
                      max="200"
                      value={steelConfig.maxCompressionSlenderness}
                      onChange={(e) =>
                        setSteelConfig({ ...steelConfig, maxCompressionSlenderness: parseFloat(e.target.value) || 150 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">λ</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">支撑与受拉斜杆长细比 λ</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="20"
                      min="150"
                      max="400"
                      value={steelConfig.maxTensionSlenderness}
                      onChange={(e) =>
                        setSteelConfig({ ...steelConfig, maxTensionSlenderness: parseFloat(e.target.value) || 300 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">λ</span>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-[11px] text-muted-foreground">长细比临界截面自动加劲</span>
                  <span className="text-xs font-semibold text-orange-600 font-mono">Auto-Stiffener</span>
                </div>
              </div>
            </div>

            {/* 钢结构卡片 3: 吊车工作与检修净空 */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-blue-500/10 text-blue-600 dark:text-blue-400">
                      <Box className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">3. 吊车工作与检修走道净空</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-blue-500/30 text-blue-600 font-mono">
                    Crane
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  桥式吊车外轮廓与厂房立柱防撞净距，吊车梁检修安全通道立体净高。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">吊车外缘与立柱安全净空</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.05"
                      min="0.2"
                      max="1.5"
                      value={steelConfig.craneClearanceM}
                      onChange={(e) =>
                        setSteelConfig({ ...steelConfig, craneClearanceM: parseFloat(e.target.value) || 0.4 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">吊车梁上检修走道垂直净高</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.1"
                      min="1.8"
                      max="3.0"
                      value={steelConfig.craneVerticalClearanceM}
                      onChange={(e) =>
                        setSteelConfig({ ...steelConfig, craneVerticalClearanceM: parseFloat(e.target.value) || 2.2 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m</span>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-[11px] text-muted-foreground">吊车荷载疲劳应力循环复核</span>
                  <span className="text-xs font-semibold text-blue-600 font-mono">Fatigue Check</span>
                </div>
              </div>
            </div>

            {/* 钢结构卡片 4: 钢构件防火涂料与耐火时限 */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-red-500/10 text-red-600 dark:text-red-400">
                      <Flame className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">4. 钢构件防火涂料耐火时限</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-red-500/30 text-red-600 font-mono">
                    GB 51249
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  钢构件在高温下屈服强度骤降，必须强制挂接膨胀型防火涂层与耐火时限。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">承重主柱耐火极限标准</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.5"
                      min="1.0"
                      max="4.0"
                      value={steelConfig.columnFireRatingHours}
                      onChange={(e) =>
                        setSteelConfig({ ...steelConfig, columnFireRatingHours: parseFloat(e.target.value) || 3.0 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">h</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">主梁与桁架耐火极限</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.5"
                      min="1.0"
                      max="3.0"
                      value={steelConfig.beamFireRatingHours}
                      onChange={(e) =>
                        setSteelConfig({ ...steelConfig, beamFireRatingHours: parseFloat(e.target.value) || 2.0 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">h</span>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-muted-foreground">强制涂料外包膨胀厚度计算</span>
                  <Switch
                    checked={steelConfig.enforceIntumescentCoating}
                    onCheckedChange={(c) => setSteelConfig({ ...steelConfig, enforceIntumescentCoating: c })}
                  />
                </div>
              </div>
            </div>

            {/* 钢结构卡片 5: 节点连接与高强螺栓端距 */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-stone-500/10 text-stone-600 dark:text-stone-400">
                      <Hammer className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">5. 节点连接与螺栓端距</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-stone-500/30 text-stone-600 font-mono">
                    Joints
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  节点板螺栓孔距构件边缘极限净距，保证坡口焊接与螺栓扳手旋拧安全包络。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">高强螺栓最小边端距比 [d0]</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.1"
                      min="1.2"
                      max="3.0"
                      value={steelConfig.minBoltEdgeDistanceRatio}
                      onChange={(e) =>
                        setSteelConfig({ ...steelConfig, minBoltEdgeDistanceRatio: parseFloat(e.target.value) || 1.5 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">d0</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">焊缝与装配对位软空间</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="2"
                      min="6"
                      max="30"
                      value={steelConfig.weldEnvelopeMm}
                      onChange={(e) =>
                        setSteelConfig({ ...steelConfig, weldEnvelopeMm: parseFloat(e.target.value) || 12 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">mm</span>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-[11px] text-muted-foreground">节点域抗剪屈服二次验算</span>
                  <span className="text-xs font-semibold text-stone-600 font-mono">Node Domain Guard</span>
                </div>
              </div>
            </div>

            {/* 钢结构卡片 6: IFC 钢构工程量与 Pset */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                      <FileCheck className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">6. IFC 钢构工程量与 Pset</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-emerald-500/30 text-emerald-600 font-mono">
                    IFC4 / Steel
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  强制挂接 Pset_BeamCommon / Column、型钢截面型号与构件净重算量清单。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">强制输出 Pset_Beam / Column</span>
                  <Switch
                    checked={steelConfig.enforceSteelPset}
                    onCheckedChange={(c) => setSteelConfig({ ...steelConfig, enforceSteelPset: c })}
                  />
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">国标热轧型钢截面规格库</span>
                  <span className="text-xs font-semibold text-sky-600 font-mono">GB/T 11263</span>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-[11px] text-muted-foreground">用钢总吨位与防腐涂装面积直通</span>
                  <span className="text-xs font-semibold text-emerald-600 font-mono">Ready (BOQ)</span>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5">
            {/* 市政卡片 1: 水力坡度与流速保证 */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-sky-500/10 text-sky-600 dark:text-sky-400">
                      <Droplets className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">1. 水力坡度与流速保证</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-sky-500/30 text-sky-600 font-mono">
                    Manning
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  重力污水/雨水流速过低导致泥沙淤积，流速过高磨损冲蚀管衬。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">最小自净流速 (v_min)</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.05"
                      min="0.1"
                      max="2.0"
                      value={config.minVelocityDrainage}
                      onChange={(e) =>
                        setConfig({ ...config, minVelocityDrainage: parseFloat(e.target.value) || 0.6 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m/s</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">最大冲刷流速 (v_max)</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.1"
                      min="1.0"
                      max="10.0"
                      value={config.maxVelocityDrainage}
                      onChange={(e) =>
                        setConfig({ ...config, maxVelocityDrainage: parseFloat(e.target.value) || 5.0 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m/s</span>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-muted-foreground">严禁重力管逆向倒坡</span>
                  <Switch
                    checked={config.forbidNegativeSlope}
                    onCheckedChange={(c) => setConfig({ ...config, forbidNegativeSlope: c })}
                  />
                </div>
              </div>
            </div>

            {/* 市政卡片 2: 覆土深度与季节性防冻线 */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-blue-500/10 text-blue-600 dark:text-blue-400">
                      <Snowflake className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">2. 覆土深度与防冻线</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-blue-500/30 text-blue-600 font-mono">
                    Frost
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  北方极寒地区管顶必须在冰冻线以下，车行道下必须抗车辆动载荷。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">当地最大冻土线深度</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.1"
                      min="0"
                      max="3.0"
                      value={config.localFrostDepthM}
                      onChange={(e) =>
                        setConfig({ ...config, localFrostDepthM: parseFloat(e.target.value) || 0 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">冻土线附加安全裕量</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.05"
                      min="0"
                      max="1.0"
                      value={config.frostSafetyMarginM}
                      onChange={(e) =>
                        setConfig({ ...config, frostSafetyMarginM: parseFloat(e.target.value) || 0.15 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">车行道最小覆土厚度</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.05"
                      min="0.3"
                      max="2.0"
                      value={config.trafficRoadCoverMinM}
                      onChange={(e) =>
                        setConfig({ ...config, trafficRoadCoverMinM: parseFloat(e.target.value) || 0.7 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m</span>
                  </div>
                </div>
              </div>
            </div>

            {/* 市政卡片 3: 垂直交叉与避让层级 */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-indigo-500/10 text-indigo-600 dark:text-indigo-400">
                      <Ruler className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">3. 垂直交叉与避让层级</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-indigo-500/30 text-indigo-600 font-mono">
                    Clearance
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  有压让无压、小管让大管，给水在污水上方，强电在给水上方。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">给水与排水垂直净距</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.05"
                      min="0.1"
                      max="2.0"
                      value={config.clearanceWaterSewerM}
                      onChange={(e) =>
                        setConfig({ ...config, clearanceWaterSewerM: parseFloat(e.target.value) || 0.4 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">强电与热力垂直隔离</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.05"
                      min="0.1"
                      max="2.0"
                      value={config.clearancePowerHeatM}
                      onChange={(e) =>
                        setConfig({ ...config, clearancePowerHeatM: parseFloat(e.target.value) || 0.5 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m</span>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-muted-foreground">有压管倒虹吸自动避让</span>
                  <Switch
                    checked={config.pressureYieldsGravity}
                    onCheckedChange={(c) => setConfig({ ...config, pressureYieldsGravity: c })}
                  />
                </div>
              </div>
            </div>

            {/* 市政卡片 4: 跌水井消能构造 */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-amber-500/10 text-amber-600 dark:text-amber-400">
                      <ChevronsDown className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">4. 跌水井消能构造</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-amber-500/30 text-amber-600 font-mono">
                    Drop
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  当支管管底与干管管底高差过大时，必须插入消能竖向管防止冲刷井底。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">污水井跌水高差阈值</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.1"
                      min="0.5"
                      max="5.0"
                      value={config.dropManholeThresholdSewerM}
                      onChange={(e) =>
                        setConfig({ ...config, dropManholeThresholdSewerM: parseFloat(e.target.value) || 1.0 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">雨水井跌水高差阈值</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.1"
                      min="0.5"
                      max="5.0"
                      value={config.dropManholeThresholdRainM}
                      onChange={(e) =>
                        setConfig({ ...config, dropManholeThresholdRainM: parseFloat(e.target.value) || 1.5 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m</span>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-[11px] text-muted-foreground">超标自动插入跌水副管</span>
                  <span className="text-xs font-semibold text-emerald-600 font-mono">Auto-Insert</span>
                </div>
              </div>
            </div>

            {/* 市政卡片 5: 施工检修与软空间包络 */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-purple-500/10 text-purple-600 dark:text-purple-400">
                      <Box className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">5. 施工检修软空间</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-purple-500/30 text-purple-600 font-mono">
                    Envelope
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  空间碰撞计算不仅算钢管本体，还需外包保温层厚度与扳手作业面。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">保温/防腐层外包空间</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="5"
                      min="0"
                      max="200"
                      value={config.pipeInsulationToleranceMm}
                      onChange={(e) =>
                        setConfig({ ...config, pipeInsulationToleranceMm: parseFloat(e.target.value) || 50 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">mm</span>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">检查井人工作业回转面</span>
                  <div className="flex items-center space-x-1">
                    <Input
                      type="number"
                      step="0.05"
                      min="0.3"
                      max="2.0"
                      value={config.manholeWorkingSpaceRadiusM}
                      onChange={(e) =>
                        setConfig({ ...config, manholeWorkingSpaceRadiusM: parseFloat(e.target.value) || 0.7 })
                      }
                      className="w-16 h-7 text-xs font-mono text-right"
                    />
                    <span className="text-[10px] text-muted-foreground">m</span>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-[11px] text-muted-foreground">软碰撞容差 (Soft Clash)</span>
                  <span className="text-xs font-semibold text-purple-600 font-mono">50mm Guard</span>
                </div>
              </div>
            </div>

            {/* 市政卡片 6: IFC 语义与清单挂接 */}
            <div className="p-3.5 rounded-xl border border-border/70 bg-card/60 shadow-2xs space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                      <FileCheck className="h-4 w-4" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">6. IFC 语义与清单挂接</span>
                  </div>
                  <Badge variant="outline" className="text-[10px] py-0 border-emerald-500/30 text-emerald-600 font-mono">
                    IFC4 / BOQ
                  </Badge>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  拒绝导出哑几何实体，强制挂接标准 Pset 属性集以供造价与施工算量。
                </p>
              </div>

              <div className="space-y-2.5 pt-2 border-t border-border/50 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">强制输出 Pset_PipeCommon</span>
                  <Switch
                    checked={config.enforcePsetCommon}
                    onCheckedChange={(c) => setConfig({ ...config, enforcePsetCommon: c })}
                  />
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">挂接管底内壁标高与材质</span>
                  <Switch
                    checked={config.enforceInvertElevation}
                    onCheckedChange={(c) => setConfig({ ...config, enforceInvertElevation: c })}
                  />
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-[11px] text-muted-foreground">清单工程量造价直通 (BOQ)</span>
                  <span className="text-xs font-semibold text-emerald-600 font-mono">Ready</span>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* 核心第二部分：分项规程与审计库 (表格索引与自定义新增) */}
      <div className="space-y-3 pt-4 border-t border-border/60">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-bold text-foreground">国家与地方强制约束条目明细</h3>
            <p className="text-xs text-muted-foreground">
              实时索引与审计底层门禁（包含 {rules.length} 条已载入的刚性判定算子）
            </p>
          </div>

          {/* 搜索与新增按钮 */}
          <div className="flex items-center space-x-2">
            <div className="relative w-48 shrink-0">
              <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                value={searchRule}
                onChange={(e) => setSearchRule(e.target.value)}
                placeholder="搜索规则名、编号或指标..."
                className="pl-8 h-8 text-xs rounded-lg"
              />
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={loadRules}
              disabled={loadingRules}
              className="h-8 px-2.5 text-xs cursor-pointer"
            >
              <RefreshCw className={`h-3 w-3 mr-1 ${loadingRules ? "animate-spin" : ""}`} />
              刷新
            </Button>
            <Button
              size="sm"
              onClick={handleOpenAddRule}
              className="h-8 px-3 text-xs bg-violet-600 hover:bg-violet-700 text-white font-medium cursor-pointer"
            >
              <Plus className="h-3 w-3 mr-1" />
              新增约束规则
            </Button>
          </div>
        </div>

        {/* 分类筛选胶囊 */}
        <div className="flex items-center space-x-1.5 overflow-x-auto pb-1">
          {categories.map((cat) => (
            <button
              key={cat}
              onClick={() => setSelectedCategory(cat)}
              className={`px-3 py-1 rounded-full text-xs transition-all cursor-pointer ${
                selectedCategory === cat
                  ? "bg-violet-600 text-white font-medium shadow-2xs"
                  : "bg-muted/70 text-muted-foreground hover:text-foreground hover:bg-muted"
              }`}
            >
              {cat === "ALL" ? "全部条款" : cat}
            </button>
          ))}
        </div>

        {/* 规则表格 */}
        <div className="rounded-xl border border-border/70 overflow-hidden bg-card shadow-2xs">
          <div className="grid grid-cols-12 px-4 py-2.5 bg-muted/40 border-b border-border/60 text-[11px] text-muted-foreground font-medium select-none">
            <div className="col-span-5">规程条款 / 编号</div>
            <div className="col-span-2">所属分类</div>
            <div className="col-span-2 text-center">违规等级</div>
            <div className="col-span-3 text-right">强约束计算指标</div>
          </div>

          <div className="divide-y divide-border/50 max-h-[340px] overflow-y-auto">
            {filteredRules.map((rule) => {
              const isSelected = selectedRule?.id === rule.id
              return (
                <div
                  key={rule.id}
                  onClick={() => setSelectedRule(rule)}
                  className={`grid grid-cols-12 px-4 py-2.5 items-center text-xs cursor-pointer transition-colors ${
                    isSelected ? "bg-violet-500/10 text-foreground" : "hover:bg-muted/40 text-muted-foreground"
                  }`}
                >
                  <div className="col-span-5 flex items-center space-x-2 min-w-0 pr-2">
                    <span className="font-mono text-[11px] text-muted-foreground shrink-0">{rule.id}</span>
                    <span className="truncate font-medium text-foreground">{rule.title}</span>
                  </div>
                  <div className="col-span-2 text-[11px] text-muted-foreground">{rule.category}</div>
                  <div className="col-span-2 text-center">
                    {rule.severity === "BLOCKER" ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20">
                        <span className="w-1.5 h-1.5 rounded-full bg-rose-500 shrink-0" />
                        致命阻断
                      </span>
                    ) : rule.severity === "WARN" ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20">
                        <span className="w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0" />
                        警告提示
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-sky-500/10 text-sky-600 dark:text-sky-400 border border-sky-500/20">
                        <span className="w-1.5 h-1.5 rounded-full bg-sky-500 shrink-0" />
                        建议参考
                      </span>
                    )}
                  </div>
                  <div className="col-span-3 text-right font-mono text-xs font-semibold text-foreground">
                    {rule.condition || "—"}
                  </div>
                </div>
              )
            })}

            {filteredRules.length === 0 && (
              <div className="py-10 text-center text-xs text-muted-foreground">
                暂未找到匹配的工程约束条款
              </div>
            )}
          </div>
        </div>

        {/* 选中规则详情抽屉 */}
        {selectedRule && (
          <div className="p-3.5 rounded-xl border border-border/70 bg-muted/20 space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <span className="font-mono text-xs font-semibold text-primary">{selectedRule.id}</span>
                <span className="text-xs font-bold text-foreground">{selectedRule.title}</span>
              </div>
              <span className="text-xs font-mono font-semibold px-2 py-0.5 rounded bg-background border border-border/70">
                {selectedRule.condition || "依具体管径动态复核"}
              </span>
            </div>
            <p className="text-xs text-muted-foreground leading-relaxed">
              {selectedRule.description || "在管网拓扑生成与高程解算阶段，自动挂载此算子进行几何欧氏距离与曼宁水力学微分校验。"}
            </p>
          </div>
        )}
      </div>

      {/* 新增约束规则弹窗 (无代码极简向导) */}
      <Dialog open={isAddModalOpen} onOpenChange={setIsAddModalOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle className="text-base flex items-center gap-2">
              <Plus className="h-4 w-4 text-violet-600" />
              新增工程约束规则 (Custom Constraint)
            </DialogTitle>
          </DialogHeader>

          <div className="space-y-3.5 py-2 text-xs">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="font-medium text-foreground">规则编号 (Rule ID)</label>
                <Input
                  value={newRuleForm.id}
                  onChange={(e) => setNewRuleForm({ ...newRuleForm, id: e.target.value })}
                  placeholder="如: PRJ-CLEAR-009"
                  className="h-8 text-xs font-mono"
                />
              </div>
              <div className="space-y-1">
                <label className="font-medium text-foreground">所属分类</label>
                <select
                  value={newRuleForm.category}
                  onChange={(e) => setNewRuleForm({ ...newRuleForm, category: e.target.value })}
                  className="w-full h-8 rounded-lg border border-input bg-background px-2.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                >
                  <option value="空间净距">空间净距 (Clearance)</option>
                  <option value="水力与坡度">水力与坡度 (Hydraulic & Slope)</option>
                  <option value="覆土防冻">覆土防冻 (Frost & Cover)</option>
                  <option value="跌水消能">跌水消能 (Drop Manhole)</option>
                  <option value="施工软空间">施工软空间 (Soft Clash)</option>
                  <option value="IFC属性">IFC 属性 (Pset)</option>
                  <option value="项目特规">项目特规 (Special Rule)</option>
                </select>
              </div>
            </div>

            <div className="space-y-1">
              <label className="font-medium text-foreground">规则名称 (Rule Title)</label>
              <Input
                value={newRuleForm.title}
                onChange={(e) => setNewRuleForm({ ...newRuleForm, title: e.target.value })}
                placeholder="如: 化工厂区高压氢气管与热力管特殊隔离净距"
                className="h-8 text-xs"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="font-medium text-foreground">违规处置级别 (Severity)</label>
                <div className="flex items-center space-x-3 h-8">
                  <label className="flex items-center space-x-1.5 cursor-pointer">
                    <input
                      type="radio"
                      name="severity"
                      checked={newRuleForm.severity === "BLOCKER"}
                      onChange={() => setNewRuleForm({ ...newRuleForm, severity: "BLOCKER" })}
                    />
                    <span className="text-rose-600 font-medium">致命阻断 (BLOCKER)</span>
                  </label>
                  <label className="flex items-center space-x-1.5 cursor-pointer">
                    <input
                      type="radio"
                      name="severity"
                      checked={newRuleForm.severity === "WARN"}
                      onChange={() => setNewRuleForm({ ...newRuleForm, severity: "WARN" })}
                    />
                    <span className="text-amber-600 font-medium">警告提示 (WARN)</span>
                  </label>
                </div>
              </div>

              <div className="space-y-1">
                <label className="font-medium text-foreground">强约束计算指标 (Condition)</label>
                <Input
                  value={newRuleForm.condition}
                  onChange={(e) => setNewRuleForm({ ...newRuleForm, condition: e.target.value })}
                  placeholder="如: ≥ 1.50 m 或 v ≥ 0.80 m/s"
                  className="h-8 text-xs font-mono"
                />
              </div>
            </div>

            <div className="space-y-1">
              <label className="font-medium text-foreground">技术规范 / 企标出处 (Citation)</label>
              <Input
                value={newRuleForm.spec}
                onChange={(e) => setNewRuleForm({ ...newRuleForm, spec: e.target.value })}
                placeholder="如: 《化工厂区管线综合设计企标 Q/SH-001》§5.2"
                className="h-8 text-xs"
              />
            </div>

            <div className="space-y-1">
              <label className="font-medium text-foreground">违规逻辑释义与处置说明 (Description)</label>
              <textarea
                value={newRuleForm.description}
                onChange={(e) => setNewRuleForm({ ...newRuleForm, description: e.target.value })}
                placeholder="说明该规程在模型计算侵限时的防爆/防渗漏/自愈重构目的..."
                className="w-full rounded-lg border border-input bg-transparent px-3 py-2 text-xs shadow-2xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring min-h-[60px]"
                rows={2}
              />
            </div>
          </div>

          <DialogFooter className="pt-2">
            <Button variant="outline" size="sm" onClick={() => setIsAddModalOpen(false)} className="h-8 text-xs">
              取消
            </Button>
            <Button
              size="sm"
              onClick={handleSaveNewRule}
              className="h-8 text-xs bg-violet-600 hover:bg-violet-700 text-white font-medium cursor-pointer"
            >
              录入并生效
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
