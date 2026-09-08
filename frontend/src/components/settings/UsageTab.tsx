import React, { useEffect, useState, useMemo } from "react"
import { api, UsageSummary } from "@/services/api"
import { Button } from "@/components/ui/button"
import { RotateCcw, BarChart3, Clock, Zap, Calendar, TrendingUp } from "lucide-react"
import { toast } from "sonner"

// 现代色彩调色板 (供动态模型分配)
const MODEL_PALETTE = [
  { color: "#10b981", fillColor: "rgba(16, 185, 129, 0.08)" }, // 绿 (商汤 / SenseNova)
  { color: "#3b82f6", fillColor: "rgba(59, 130, 246, 0.08)" }, // 蓝 (GLM)
  { color: "#a855f7", fillColor: "rgba(168, 85, 247, 0.08)" }, // 紫 (DeepSeek)
  { color: "#f59e0b", fillColor: "rgba(245, 158, 11, 0.08)" }, // 橙 (Kimi)
  { color: "#06b6d4", fillColor: "rgba(6, 182, 212, 0.08)" }, // 青
  { color: "#ec4899", fillColor: "rgba(236, 72, 153, 0.08)" }, // 粉
  { color: "#6366f1", fillColor: "rgba(99, 102, 241, 0.08)" }, // 靛蓝
]

export interface ModelItemView {
  name: string
  label: string
  color: string
  fillColor: string
  tokensStr: string
  tokensNum: number
  calls: number
  percentage: number
}

function formatTokens(tokens: number): string {
  if (tokens >= 100_000_000) {
    return `${(tokens / 100_000_000).toFixed(1)} 亿`
  }
  if (tokens >= 10_000) {
    return `${(tokens / 10_000).toFixed(1)} 万`
  }
  return `${tokens.toLocaleString()} tokens`
}

function formatDuration(ms: number): string {
  if (!ms || ms <= 0) return "0 秒"
  const totalSec = Math.floor(ms / 1000)
  if (totalSec < 60) return `${totalSec} 秒`
  const min = Math.floor(totalSec / 60)
  const sec = totalSec % 60
  if (min < 60) return `${min} 分钟 ${sec} 秒`
  const hr = Math.floor(min / 60)
  const remMin = min % 60
  return `${hr} 小时 ${remMin} 分钟`
}

// 构建平滑三次贝塞尔曲线 SVG 路径
function generateSplinePath(points: Array<{ x: number; y: number }>): string {
  if (points.length === 0) return ""
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`

  let path = `M ${points[0].x} ${points[0].y}`
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i === 0 ? 0 : i - 1]
    const p1 = points[i]
    const p2 = points[i + 1]
    const p3 = points[i + 2] || p2

    const cp1x = p1.x + (p2.x - p0.x) / 6
    const cp1y = p1.y + (p2.y - p0.y) / 6
    const cp2x = p2.x - (p3.x - p1.x) / 6
    const cp2y = p2.y - (p3.y - p1.y) / 6

    path += ` C ${cp1x.toFixed(1)} ${cp1y.toFixed(1)}, ${cp2x.toFixed(1)} ${cp2y.toFixed(1)}, ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`
  }
  return path
}

export const UsageTab: React.FC = () => {
  const [usage, setUsage] = useState<UsageSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [activityMode, setActivityMode] = useState<"daily" | "weekly" | "total">("daily")
  const [timeRange, setTimeRange] = useState<"7d" | "30d">("7d")
  const [hoveredModel, setHoveredModel] = useState<string | null>(null)
  const [hoveredCell, setHoveredCell] = useState<{ date: string; count: number } | null>(null)

  const loadUsage = async () => {
    setLoading(true)
    try {
      const res = await api.getUsage()
      setUsage(res.usage || null)
    } catch (e: any) {
      console.error("加载用量失败:", e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadUsage()
  }, [])

  // 1. 真实指标动态聚合
  const totalTokens = usage?.total?.total_tokens ?? 0
  const totalCalls = usage?.total?.calls ?? 0

  // 动态计算峰值 Token 数 (按日单日最大消耗，若仅有调用则取最近调用最大)
  const peakTokens = useMemo(() => {
    if (!usage) return 0
    const dailyMax = (usage.daily || []).reduce((m, d) => Math.max(m, d.total_tokens), 0)
    const recentMax = (usage.recent || []).reduce((m, r) => Math.max(m, r.total_tokens), 0)
    return Math.max(dailyMax, recentMax, totalTokens)
  }, [usage, totalTokens])

  // 最长调用/对话时长
  const maxDurationStr = useMemo(() => {
    if (!usage?.recent || usage.recent.length === 0) return "0 秒"
    const maxMs = usage.recent.reduce((m, r) => Math.max(m, r.latency_ms || 0), 0)
    return formatDuration(maxMs)
  }, [usage])

  // 当前连续天数与最长连续天数计算
  const { currentStreak, maxStreak } = useMemo(() => {
    if (!usage?.daily || usage.daily.length === 0) return { currentStreak: 0, maxStreak: 0 }
    let cur = 0
    let maxS = 0
    let temp = 0

    // 按日期正序遍历
    for (const d of usage.daily) {
      if (d.calls > 0 || d.total_tokens > 0) {
        temp++
        if (temp > maxS) maxS = temp
      } else {
        temp = 0
      }
    }

    // 从倒数第一天反推当前连续
    const reversed = [...usage.daily].reverse()
    for (const d of reversed) {
      if (d.calls > 0 || d.total_tokens > 0) {
        cur++
      } else {
        break
      }
    }

    return { currentStreak: cur, maxStreak: Math.max(cur, maxS) }
  }, [usage])

  // 2. 动态模型数据拆解 (根据 by_model 真实数据渲染环形图与列表)
  const modelItems: ModelItemView[] = useMemo(() => {
    if (!usage?.by_model || Object.keys(usage.by_model).length === 0) {
      return []
    }
    const entries = Object.entries(usage.by_model)
    return entries.map(([mName, info], idx) => {
      const palette = MODEL_PALETTE[idx % MODEL_PALETTE.length]
      const percentage = totalTokens > 0 ? (info.total_tokens / totalTokens) * 100 : 100 / entries.length
      return {
        name: mName,
        label: mName,
        color: palette.color,
        fillColor: palette.fillColor,
        tokensStr: formatTokens(info.total_tokens),
        tokensNum: info.total_tokens,
        calls: info.calls,
        percentage: Number(percentage.toFixed(1)),
      }
    })
  }, [usage, totalTokens])

  // 3. 趋势图动态数据 (近 7 日或近 14 日真实点位)
  const trendDates = useMemo(() => {
    if (!usage?.daily || usage.daily.length === 0) {
      return ["近7日无数据"]
    }
    const sliceDays = timeRange === "7d" ? 7 : 14
    return usage.daily.slice(-sliceDays).map((d) => {
      const parts = d.date.split("-")
      return parts.length >= 3 ? `${parseInt(parts[1])}月${parseInt(parts[2])}日` : d.date
    })
  }, [usage, timeRange])

  // 各模型趋势曲线点位归一化 (0 ~ 100 标尺)
  const modelTrendLines = useMemo(() => {
    if (!usage?.daily || usage.daily.length === 0 || modelItems.length === 0) {
      return []
    }
    const sliceDays = timeRange === "7d" ? 7 : 14
    const dailySlice = usage.daily.slice(-sliceDays)

    // 计算这几天的最大 token 作为纵坐标缩放基准
    const maxVal = Math.max(...dailySlice.map((d) => d.total_tokens), 1)

    return modelItems.map((m) => {
      // 若有多模型，按各模型在 daily 中的比重分布
      const points = dailySlice.map((d, i) => {
        const x = (i / (dailySlice.length - 1 || 1)) * 620 + 30
        const ratio = d.total_tokens / maxVal
        const yVal = m.percentage > 0 ? ratio * (m.percentage / 100) * 120 : 0
        const y = 140 - Math.min(120, Math.max(0, yVal))
        return { x, y }
      })
      return {
        ...m,
        points,
        path: generateSplinePath(points),
      }
    })
  }, [usage, timeRange, modelItems])

  // 4. 热力图矩阵生成 (动态感知真实的 daily 调用记录)
  const heatmapGrid = useMemo(() => {
    const cols = 42
    const rows = 7
    const grid: Array<Array<{ level: number; date: string; tokens: number }>> = []

    // 建立日常 token 快速索引
    const dailyMap = new Map<string, number>()
    if (usage?.daily) {
      for (const d of usage.daily) {
        dailyMap.set(d.date, d.total_tokens)
      }
    }

    const today = new Date()
    for (let c = 0; c < cols; c++) {
      const col: Array<{ level: number; date: string; tokens: number }> = []
      for (let r = 0; r < rows; r++) {
        // 计算距今天数：(41 - c) * 7 + (6 - r)
        const daysAgo = (cols - 1 - c) * 7 + (rows - 1 - r)
        const cellDate = new Date(today.getTime() - daysAgo * 86400000)
        const dateKey = cellDate.toISOString().slice(0, 10)
        const tokens = dailyMap.get(dateKey) || 0

        let level = 0
        if (tokens > 50_000) level = 4
        else if (tokens > 10_000) level = 3
        else if (tokens > 2_000) level = 2
        else if (tokens > 0) level = 1

        col.push({ level, date: dateKey, tokens })
      }
      grid.push(col)
    }
    return grid
  }, [usage])

  // 5. 环形图计算 (SVG 路径角度)
  const donutArcs = useMemo(() => {
    if (modelItems.length === 0) return []
    let accumulatedAngle = -90
    const r = 62
    const cx = 90
    const cy = 90

    return modelItems.map((item) => {
      const angle = (item.percentage / 100) * 360
      const startAngle = accumulatedAngle
      const endAngle = accumulatedAngle + angle
      accumulatedAngle = endAngle

      const startRad = (startAngle * Math.PI) / 180
      const endRad = (endAngle * Math.PI) / 180

      const x1 = cx + r * Math.cos(startRad)
      const y1 = cy + r * Math.sin(startRad)
      const x2 = cx + r * Math.cos(endRad)
      const y2 = cy + r * Math.sin(endRad)

      const largeArcFlag = angle > 180 ? 1 : 0
      const d = `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${largeArcFlag} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`

      return {
        ...item,
        d,
      }
    })
  }, [modelItems])

  return (
    <div className="space-y-6 max-w-5xl pb-10 select-none font-sans text-neutral-900 dark:text-neutral-100">
      {/* 1. 顶部标题区 */}
      <div className="flex items-center justify-between pt-1">
        <div className="flex items-center space-x-2.5">
          <h2 className="text-xl font-bold tracking-tight text-foreground">使用统计</h2>
          <span className="text-[11px] px-2.5 py-0.5 rounded-full bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-300 font-medium">
            应用真实流水用量
          </span>
        </div>
      </div>

      {/* 2. 统计卡片条 (真实数据驱动: 累计 Token / 峰值 / 最长聊天 / 当前连续 / 最长连续) */}
      <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 shadow-2xs overflow-hidden">
        <div className="grid grid-cols-2 sm:grid-cols-5 divide-y sm:divide-y-0 sm:divide-x divide-neutral-100 dark:divide-neutral-800 py-3.5 px-2">
          {/* 累计 Token 数 */}
          <div className="text-center px-3 py-2 sm:py-0">
            <div className="text-xl font-extrabold tracking-tight text-foreground font-sans">
              {formatTokens(totalTokens)}
            </div>
            <div className="text-[11px] text-neutral-400 mt-1 font-normal">
              累计 Token 数
            </div>
          </div>

          {/* 峰值 Token 数 */}
          <div className="text-center px-3 py-2 sm:py-0">
            <div className="text-xl font-extrabold tracking-tight text-foreground font-sans">
              {formatTokens(peakTokens)}
            </div>
            <div className="text-[11px] text-neutral-400 mt-1 font-normal">
              峰值 Token 数
            </div>
          </div>

          {/* 最长调用/聊天时长 */}
          <div className="text-center px-3 py-2 sm:py-0">
            <div className="text-xl font-extrabold tracking-tight text-foreground font-sans">
              {maxDurationStr}
            </div>
            <div className="text-[11px] text-neutral-400 mt-1 font-normal">
              单次最长耗时
            </div>
          </div>

          {/* 当前连续天数 */}
          <div className="text-center px-3 py-2 sm:py-0">
            <div className="text-xl font-extrabold tracking-tight text-foreground font-sans">
              {currentStreak} 天
            </div>
            <div className="text-[11px] text-neutral-400 mt-1 font-normal">
              当前连续活跃
            </div>
          </div>

          {/* 最长连续天数 */}
          <div className="text-center px-3 py-2 sm:py-0 col-span-2 sm:col-span-1">
            <div className="text-xl font-extrabold tracking-tight text-foreground font-sans">
              {maxStreak} 天
            </div>
            <div className="text-[11px] text-neutral-400 mt-1 font-normal">
              最长连续天数
            </div>
          </div>
        </div>
      </div>

      {/* 3. Token 活动模块 (真实热力图 + 每日/每周/累计切换) */}
      <div className="rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 p-5 shadow-2xs space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground">Token 活动</h3>
          <div className="inline-flex items-center p-0.5 rounded-lg bg-neutral-100 dark:bg-neutral-800 text-xs font-medium">
            <button
              onClick={() => setActivityMode("daily")}
              className={`px-3 py-1 rounded-md transition-all ${
                activityMode === "daily"
                  ? "bg-white dark:bg-neutral-900 text-foreground shadow-2xs font-semibold"
                  : "text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200"
              }`}
            >
              每日
            </button>
            <button
              onClick={() => setActivityMode("weekly")}
              className={`px-3 py-1 rounded-md transition-all ${
                activityMode === "weekly"
                  ? "bg-white dark:bg-neutral-900 text-foreground shadow-2xs font-semibold"
                  : "text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200"
              }`}
            >
              每周
            </button>
            <button
              onClick={() => setActivityMode("total")}
              className={`px-3 py-1 rounded-md transition-all ${
                activityMode === "total"
                  ? "bg-white dark:bg-neutral-900 text-foreground shadow-2xs font-semibold"
                  : "text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200"
              }`}
            >
              累计
            </button>
          </div>
        </div>

        {/* 7 x 42 热力矩阵 (根据真实调用记录着色) */}
        <div className="relative overflow-x-auto pb-1">
          <div className="min-w-[680px]">
            {/* 月份刻度 */}
            <div className="flex justify-between text-[10px] text-neutral-400 mb-1.5 px-4">
              <span>近 10 月</span>
              <span>12 月</span>
              <span>2 月</span>
              <span>4 月</span>
              <span>6 月</span>
              <span>8 月</span>
              <span>今日</span>
            </div>

            {/* 矩阵网格 */}
            <div className="flex gap-[3.5px] items-center justify-between">
              {heatmapGrid.map((col, colIdx) => (
                <div key={colIdx} className="flex flex-col gap-[3.5px]">
                  {col.map((cell, rowIdx) => {
                    let bg = "bg-neutral-100 dark:bg-neutral-800/60"
                    if (cell.level === 1) bg = "bg-emerald-300 dark:bg-emerald-800/70"
                    if (cell.level === 2) bg = "bg-emerald-400 dark:bg-emerald-700"
                    if (cell.level === 3) bg = "bg-emerald-500 dark:bg-emerald-600"
                    if (cell.level === 4) bg = "bg-emerald-600 dark:bg-emerald-500"

                    return (
                      <div
                        key={rowIdx}
                        onMouseEnter={() => setHoveredCell({ date: cell.date, count: cell.tokens })}
                        onMouseLeave={() => setHoveredCell(null)}
                        className={`w-[11px] h-[11px] rounded-[2.5px] transition-all cursor-pointer ${bg} hover:ring-1 hover:ring-foreground/50`}
                      />
                    )
                  })}
                </div>
              ))}
            </div>

            {/* 图例 */}
            <div className="flex items-center justify-between mt-3 text-[10px] text-neutral-400">
              <div className="h-4">
                {hoveredCell && (
                  <span className="text-foreground font-mono">
                    {hoveredCell.date} · {formatTokens(hoveredCell.count)}
                  </span>
                )}
              </div>
              <div className="flex items-center space-x-1.5">
                <span>少</span>
                <div className="w-2.5 h-2.5 rounded-[2px] bg-neutral-100 dark:bg-neutral-800/60" />
                <div className="w-2.5 h-2.5 rounded-[2px] bg-emerald-300 dark:bg-emerald-800/70" />
                <div className="w-2.5 h-2.5 rounded-[2px] bg-emerald-400 dark:bg-emerald-700" />
                <div className="w-2.5 h-2.5 rounded-[2px] bg-emerald-500 dark:bg-emerald-600" />
                <div className="w-2.5 h-2.5 rounded-[2px] bg-emerald-600 dark:bg-emerald-500" />
                <span>多</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 4. 下半部两列：时间范围曲线 + 模型用量环形图 */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
        {/* 左侧 7 列：时间范围平滑贝塞尔曲线图 */}
        <div className="lg:col-span-7 rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 p-5 shadow-2xs space-y-4 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold text-foreground">时间范围</h3>
              <div className="inline-flex items-center p-0.5 rounded-lg bg-neutral-100 dark:bg-neutral-800 text-xs font-medium">
                <button
                  onClick={() => setTimeRange("7d")}
                  className={`px-3 py-1 rounded-md transition-all ${
                    timeRange === "7d"
                      ? "bg-white dark:bg-neutral-900 text-foreground shadow-2xs font-semibold"
                      : "text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200"
                  }`}
                >
                  近 7 日
                </button>
                <button
                  onClick={() => setTimeRange("30d")}
                  className={`px-3 py-1 rounded-md transition-all ${
                    timeRange === "30d"
                      ? "bg-white dark:bg-neutral-900 text-foreground shadow-2xs font-semibold"
                      : "text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200"
                  }`}
                >
                  近 30 日
                </button>
              </div>
            </div>

            {/* 模型图例横排 */}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 mb-4 text-[11px]">
              {modelItems.map((m) => (
                <div
                  key={m.name}
                  onMouseEnter={() => setHoveredModel(m.name)}
                  onMouseLeave={() => setHoveredModel(null)}
                  className={`flex items-center space-x-1.5 cursor-pointer transition-opacity ${
                    hoveredModel && hoveredModel !== m.name ? "opacity-30" : "opacity-100"
                  }`}
                >
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: m.color }} />
                  <span className="text-neutral-600 dark:text-neutral-300 font-mono text-[10px]">
                    {m.label}
                  </span>
                </div>
              ))}
              {modelItems.length === 0 && (
                <span className="text-xs text-muted-foreground">暂无活跃模型数据</span>
              )}
            </div>
          </div>

          {/* SVG 平滑曲线图区 */}
          <div className="relative w-full h-[180px]">
            <svg className="w-full h-full overflow-visible" viewBox="0 0 680 180" preserveAspectRatio="none">
              {/* 背景标线 */}
              <line x1="30" y1="20" x2="650" y2="20" stroke="currentColor" className="text-neutral-100 dark:text-neutral-800/80" strokeDasharray="3 3" />
              <line x1="30" y1="80" x2="650" y2="80" stroke="currentColor" className="text-neutral-100 dark:text-neutral-800/80" strokeDasharray="3 3" />
              <line x1="30" y1="140" x2="650" y2="140" stroke="currentColor" className="text-neutral-200 dark:text-neutral-800" />

              {/* 动态绘制真实贝塞尔曲线 */}
              {modelTrendLines.map((line) => {
                const isHovered = hoveredModel === line.name
                const isDimmed = hoveredModel && hoveredModel !== line.name
                return (
                  <g key={line.name} className="transition-opacity duration-200" style={{ opacity: isDimmed ? 0.15 : 1 }}>
                    <path
                      d={line.path}
                      fill="none"
                      stroke={line.color}
                      strokeWidth={isHovered ? 3 : 2}
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                    {/* 数据节点 */}
                    {line.points.map((pt, idx) => (
                      <circle
                        key={idx}
                        cx={pt.x}
                        cy={pt.y}
                        r={isHovered ? 4 : 2.5}
                        fill={line.color}
                        className="transition-all"
                      />
                    ))}
                  </g>
                )
              })}
            </svg>

            {/* X 轴日期刻度 */}
            <div className="flex justify-between text-[10px] text-neutral-400 font-mono mt-1 px-4">
              {trendDates.map((date, idx) => (
                <span key={idx}>{date}</span>
              ))}
            </div>
          </div>
        </div>

        {/* 右侧 5 列：模型用量环形图 + 列表 */}
        <div className="lg:col-span-5 rounded-xl border border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 p-5 shadow-2xs space-y-4">
          <h3 className="text-sm font-semibold text-foreground">模型用量</h3>

          <div className="flex flex-col sm:flex-row items-center gap-5">
            {/* 环形图 */}
            <div className="relative w-[180px] h-[180px] shrink-0 flex items-center justify-center">
              <svg className="w-full h-full -rotate-90 transform" viewBox="0 0 180 180">
                {donutArcs.length === 0 ? (
                  <circle cx="90" cy="90" r="62" fill="none" stroke="currentColor" strokeWidth="16" className="text-neutral-200 dark:text-neutral-800" />
                ) : (
                  donutArcs.map((arc) => (
                    <path
                      key={arc.name}
                      d={arc.d}
                      fill="none"
                      stroke={arc.color}
                      strokeWidth={hoveredModel === arc.name ? 19 : 15}
                      strokeLinecap="round"
                      className="transition-all duration-200 cursor-pointer"
                      onMouseEnter={() => setHoveredModel(arc.name)}
                      onMouseLeave={() => setHoveredModel(null)}
                    />
                  ))
                )}
              </svg>

              {/* 环心数字 */}
              <div className="absolute inset-0 flex flex-col items-center justify-center text-center pointer-events-none">
                <span className="text-xs text-neutral-400 font-normal">总用量</span>
                <span className="text-sm font-extrabold tracking-tight text-foreground font-mono mt-0.5">
                  {formatTokens(totalTokens)}
                </span>
                <span className="text-[10px] text-neutral-400 mt-0.5 font-mono">
                  {totalCalls} 次调用
                </span>
              </div>
            </div>

            {/* 模型列表明细 */}
            <div className="flex-1 min-w-0 space-y-2.5 w-full">
              {modelItems.map((item) => {
                const isHovered = hoveredModel === item.name
                return (
                  <div
                    key={item.name}
                    onMouseEnter={() => setHoveredModel(item.name)}
                    onMouseLeave={() => setHoveredModel(null)}
                    className={`flex items-center justify-between text-xs py-1 px-1.5 rounded-lg transition-all cursor-pointer ${
                      isHovered ? "bg-neutral-100/70 dark:bg-neutral-800/70" : ""
                    }`}
                  >
                    <div className="flex items-center space-x-2 min-w-0">
                      <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: item.color }} />
                      <span className="font-medium text-foreground truncate max-w-[130px] font-mono text-[11px]" title={item.name}>
                        {item.label}
                      </span>
                    </div>
                    <div className="flex items-center space-x-2 shrink-0">
                      <span className="text-neutral-400 font-mono text-[10px]">{item.tokensStr}</span>
                      <span className="text-foreground font-mono font-bold text-[11px] w-10 text-right">
                        {item.percentage}%
                      </span>
                    </div>
                  </div>
                )
              })}

              {modelItems.length === 0 && (
                <div className="py-6 text-center text-xs text-muted-foreground">
                  暂无模型调用流水
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* 5. 底部右下角刷新按钮 */}
      <div className="flex justify-end pt-2">
        <Button
          variant="outline"
          size="sm"
          onClick={loadUsage}
          disabled={loading}
          className="h-8 px-3.5 text-xs text-neutral-600 dark:text-neutral-300 gap-1.5 border-neutral-200 dark:border-neutral-800 hover:bg-neutral-100 dark:hover:bg-neutral-800"
        >
          <RotateCcw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} />
          <span>刷新真实用量</span>
        </Button>
      </div>
    </div>
  )
}
