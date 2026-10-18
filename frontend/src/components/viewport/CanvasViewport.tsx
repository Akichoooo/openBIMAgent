import React, { useRef, useEffect, useState, useMemo, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Scene3D } from "./Scene3D"
import { parseSceneGeometry, pipeLabel, pipeReferenceLabel, type Point3 } from "./sceneGeometry"
import { Grid, ArrowUpDown, RotateCw, Maximize2, Minimize2, Target } from "lucide-react"
import * as THREE from "three"

export interface CanvasViewportProps {
  viewMode: "3d" | "plan" | "prof"
  onViewModeChange?: (mode: "3d" | "plan" | "prof") => void
  irData?: any
  isDark?: boolean
}

type Point2 = [number, number]
function convexHull(points: Point2[]): Point2[] {
  const sorted = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1])
  const cross = (a: Point2, b: Point2, c: Point2) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
  const half = (ps: Point2[]) => {
    const out: Point2[] = []
    for (const p of ps) { while (out.length > 1 && cross(out[out.length - 2], out[out.length - 1], p) <= 0) out.pop(); out.push(p) }
    return out.slice(0, -1)
  }
  return [...half(sorted), ...half([...sorted].reverse())]
}

/** Orthographic footprint of a straight circular cylinder, with no interpolated bends. */
function cylinderVertices(a: Point3, b: Point3, radius: number): Point3[] {
  const direction = new THREE.Vector3(...b).sub(new THREE.Vector3(...a)).normalize()
  const u = new THREE.Vector3().crossVectors(direction, Math.abs(direction.z) > 0.9 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 0, 1)).normalize()
  const v = new THREE.Vector3().crossVectors(direction, u).normalize()
  return [a, b].flatMap(p => Array.from({ length: 32 }, (_, i) => {
    const angle = i * Math.PI / 16
    return new THREE.Vector3(...p).addScaledVector(u, Math.cos(angle) * radius).addScaledVector(v, Math.sin(angle) * radius).toArray() as Point3
  }))
}

export const CanvasViewport: React.FC<CanvasViewportProps> = ({ viewMode, irData, isDark = true }) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const scene = useMemo(() => parseSceneGeometry(irData), [irData])
  const [gridEnabled, setGridEnabled] = useState(true)
  const [exagEnabled, setExagEnabled] = useState(false)
  const [spinEnabled, setSpinEnabled] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [cameraResetKey, setCameraResetKey] = useState(0)
  const hasData = scene.nodes.length + scene.pipes.length + scene.obstacles.length > 0
  const solids = scene.pipes.filter(p => p.solid).length
  const horizontalAxis = scene.bounds.max[0] - scene.bounds.min[0] >= scene.bounds.max[1] - scene.bounds.min[1] ? 0 : 1
  const axisName = horizontalAxis === 0 ? "X" : "Y"
  const exaggeration = exagEnabled && viewMode !== "plan" ? 3 : 1

  const render = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas || viewMode === "3d") return
    const ctx = canvas.getContext("2d")
    if (!ctx) return
    const W = canvas.clientWidth, H = canvas.clientHeight
    if (!W || !H) return
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr)
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, W, H)
    if (!hasData) return
    const isPlan = viewMode === "plan"
    const hx = isPlan ? 0 : horizontalAxis, vy = isPlan ? 1 : 2
    const spanX = Math.max(scene.bounds.max[hx] - scene.bounds.min[hx], 1)
    const spanY = Math.max(scene.bounds.max[vy] - scene.bounds.min[vy], 1)
    const scale = Math.max(0.01, Math.min(Math.max(1, W - 160) / spanX, Math.max(1, H - 200) / (spanY * exaggeration)))
    const sx = scale, sy = scale * exaggeration
    const X = (x: number) => W / 2 + (x - scene.bounds.center[hx]) * sx
    const Y = (y: number) => H / 2 - (y - scene.bounds.center[vy]) * sy
    const project = (p: Point3): Point2 => [X(p[hx]), Y(p[vy])]
    const textColor = isDark ? "#e2e8f0" : "#1e293b"
    const label = (text: string, p: Point2, color = textColor) => {
      ctx.font = "12px ui-monospace, Consolas, monospace"
      const maxWidth = Math.max(80, Math.min(W - 28, 420))
      let shown = text
      while (shown.length > 1 && ctx.measureText(shown).width > maxWidth) shown = shown.slice(0, -2) + "…"
      const width = ctx.measureText(shown).width
      const x = Math.max(10, Math.min(W - width - 10, p[0]))
      const y = Math.max(60, Math.min(H - 65, p[1]))
      ctx.fillStyle = isDark ? "rgba(11,18,32,.92)" : "rgba(248,250,252,.94)"
      ctx.fillRect(x - 3, y - 12, width + 6, 17)
      ctx.fillStyle = color; ctx.fillText(shown, x, y)
    }
    const path = (points: Point3[], color: string, dashed = false) => {
      if (!points.length) return
      ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.setLineDash(dashed ? [5, 4] : [])
      points.forEach((p, i) => { const q = project(p); if (i) ctx.lineTo(...q); else ctx.moveTo(...q) })
      ctx.stroke(); ctx.setLineDash([])
    }
    const polygon = (points: Point3[], color: string) => {
      const hull = convexHull(points.map(project))
      if (!hull.length) return
      ctx.beginPath(); hull.forEach((p, i) => i ? ctx.lineTo(...p) : ctx.moveTo(...p)); ctx.closePath()
      ctx.fillStyle = `${color}30`; ctx.strokeStyle = color; ctx.lineWidth = 1
      ctx.fill(); ctx.stroke()
    }
    const pipeFootprint = (points: Point3[], radius: number, color: string, capsule = false) => {
      points.slice(1).forEach((p, i) => polygon(cylinderVertices(points[i], p, radius), color))
      if (capsule) points.forEach(p => {
        const q = project(p); ctx.beginPath(); ctx.ellipse(q[0], q[1], radius * sx, radius * sy, 0, 0, Math.PI * 2)
        ctx.fillStyle = `${color}30`; ctx.strokeStyle = color; ctx.fill(); ctx.stroke()
      })
    }
    if (gridEnabled) {
      const step = 10 ** Math.floor(Math.log10(Math.max(spanX, spanY) / 5))
      ctx.lineWidth = 1; ctx.strokeStyle = isDark ? "#1e293b" : "#e2e8f0"
      ctx.fillStyle = isDark ? "#cbd5e1" : "#475569"; ctx.font = "11px ui-monospace, Consolas, monospace"
      for (let i = 0; i <= 10; i++) {
        const x = Math.ceil(scene.bounds.min[hx] / step) * step + i * step
        if (x > scene.bounds.max[hx]) break
        ctx.beginPath(); ctx.moveTo(X(x), 76); ctx.lineTo(X(x), H - 72); ctx.stroke()
        ctx.fillText(`${x.toFixed(1)}`, X(x) + 4, H - 58)
      }
      for (let i = 0; i <= 10; i++) {
        const y = Math.ceil(scene.bounds.min[vy] / step) * step + i * step
        if (y > scene.bounds.max[vy]) break
        ctx.beginPath(); ctx.moveTo(60, Y(y)); ctx.lineTo(W - 60, Y(y)); ctx.stroke()
        ctx.fillText(`${y.toFixed(2)}m`, 12, Y(y) - 4)
      }
    }
    scene.obstacles.forEach(o => {
      if (o.kind === "aabb") {
        const corners: Point3[] = [0, 1].flatMap(x => [0, 1].flatMap(y => [0, 1].map(z => [x ? o.max[0] : o.min[0], y ? o.max[1] : o.min[1], z ? o.max[2] : o.min[2]] as Point3)))
        polygon(corners, "#d97706")
        const p = project(o.max); label(`${o.id} · 包围盒 Z ${o.min[2]}–${o.max[2]}m`, [p[0] + 8, p[1] - 12], isDark ? "#fcd34d" : "#92400e")
      } else {
        pipeFootprint([o.start, o.end], o.outer / 2000, "#d97706", true)
        const p = project(o.start); label(`${o.id} · 既有管 OD ${o.outer}mm · 胶囊体`, [p[0] + 8, p[1] - 12], isDark ? "#fcd34d" : "#92400e")
      }
    })
    scene.pipes.forEach((pipe, index) => {
      const points = pipe.axis ?? pipe.points
      if (pipe.solid) pipeFootprint(points, pipe.outer! / 2000, "#3b82f6")
      path(points, isDark ? "#93c5fd" : "#1d4ed8", !pipe.solid)
      if (!isPlan && pipe.axis && pipe.invert) path(pipe.invert, isDark ? "#6ee7b7" : "#047857", true)
      const a = points[Math.floor((points.length - 1) / 2)], b = points[Math.ceil((points.length - 1) / 2)]
      const mid = project(a.map((v, i) => (v + b[i]) / 2) as Point3)
      label(pipeLabel(pipe), [mid[0] - 30, mid[1] + 22 + index % 3 * 18], isDark ? "#93c5fd" : "#1d4ed8")
      if (!isPlan) label(`${pipeReferenceLabel(pipe)} Z ${points[0][2].toFixed(3)} → ${points[points.length - 1][2].toFixed(3)}m${pipe.invert ? ` · 内底 ${pipe.invert[0][2].toFixed(3)} → ${pipe.invert[pipe.invert.length - 1][2].toFixed(3)}m` : " · 内底未知"}`, [mid[0] - 30, mid[1] + 40 + index % 3 * 18])
    })
    scene.nodes.forEach((n, i) => {
      const p = project(n.point)
      if (!isPlan && n.ground !== null && n.invert !== null) path([[n.point[0], n.point[1], n.ground], [n.point[0], n.point[1], n.invert]], isDark ? "#cbd5e1" : "#475569", true)
      ctx.beginPath(); ctx.arc(p[0], p[1], 4, 0, Math.PI * 2); ctx.fillStyle = textColor; ctx.fill()
      const top = !isPlan && n.ground !== null ? project([n.point[0], n.point[1], n.ground]) : p
      label(`${n.id} · 井位示意`, [top[0] + 8, top[1] - 16 - i % 2 * 18])
      if (!isPlan && n.ground !== null) label(`地面 ${n.ground.toFixed(2)}m`, [top[0] + 8, top[1] - 1 - i % 2 * 18])
    })
  }, [viewMode, scene, isDark, gridEnabled, exaggeration, hasData, horizontalAxis])

  useEffect(() => { render(); const observer = new ResizeObserver(render); if (containerRef.current) observer.observe(containerRef.current); return () => observer.disconnect() }, [render])
  useEffect(() => {
    const changed = () => setIsFullscreen(document.fullscreenElement === containerRef.current)
    document.addEventListener("fullscreenchange", changed)
    return () => document.removeEventListener("fullscreenchange", changed)
  }, [])
  const toggleFullscreen = async () => {
    if (document.fullscreenElement) await document.exitFullscreen?.()
    else await containerRef.current?.requestFullscreen?.()
  }
  const toolClass = "h-8 w-8 p-0 rounded-lg border-border bg-background/95 text-muted-foreground hover:text-foreground shadow-sm"
  const schematicCount = scene.pipes.length - solids

  return <div ref={containerRef} className="relative w-full h-full min-h-0 overflow-hidden bg-background flex flex-col">
    {viewMode === "3d" && hasData ? <Scene3D irData={irData} isDark={isDark} showGrid={gridEnabled} verticalExag={exagEnabled} autoRotate={spinEnabled} cameraResetKey={cameraResetKey} />
      : <canvas ref={canvasRef} className="w-full h-full block" aria-label={viewMode === "plan" ? "管网平面投影" : "管网高程正投影"} />}
    {hasData && <>
      <div className="absolute top-3 left-3 right-14 pointer-events-none z-10 space-y-1 text-xs text-foreground">
        <div className="inline-block rounded border border-border bg-background/95 px-2 py-1 shadow-sm">{scene.nodes.length} 个井位 · {solids} 段实体 · {schematicCount} 段参考线 · {scene.obstacles.length} 个障碍物</div>
        <div className="w-fit max-w-full rounded border border-border bg-background/95 px-2 py-1 text-muted-foreground">
          {viewMode === "3d" ? "拖动旋转 · 滚轮缩放 · 井位为示意标记" : viewMode === "plan" ? "平面 X–Y 正投影 · 坐标单位 m" : `${axisName}–高程正投影 · 非路线里程，前后几何可能重叠`}
          {viewMode !== "plan" && ` · 垂直比例 ×${exaggeration}`}
        </div>
      </div>
      <div className="absolute top-3 right-3 flex flex-col gap-1.5 z-10">
        <Button variant="outline" size="sm" className={toolClass} onClick={() => setGridEnabled(v => !v)} title="切换参考网格" aria-label="切换参考网格" aria-pressed={gridEnabled}><Grid className="h-4 w-4" /></Button>
        {viewMode !== "plan" && <Button variant="outline" size="sm" className={toolClass} onClick={() => setExagEnabled(v => !v)} title="切换垂直比例：×1 / ×3" aria-label="切换垂直比例：×1 / ×3" aria-pressed={exagEnabled}><ArrowUpDown className="h-4 w-4" /></Button>}
        {viewMode === "3d" && <Button variant="outline" size="sm" className={toolClass} onClick={() => setSpinEnabled(v => !v)} title="自动环绕旋转" aria-label="自动环绕旋转" aria-pressed={spinEnabled}><RotateCw className="h-4 w-4" /></Button>}
        <Button variant="outline" size="sm" className={toolClass} onClick={() => { setCameraResetKey(v => v + 1); render() }} title="适配全部几何" aria-label="适配全部几何"><Target className="h-4 w-4" /></Button>
        <Button variant="outline" size="sm" className={toolClass} onClick={toggleFullscreen} title={isFullscreen ? "退出全屏" : "全屏视口"} aria-label={isFullscreen ? "退出全屏" : "全屏视口"}>{isFullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}</Button>
      </div>
      <div className="absolute bottom-3 left-3 right-3 z-10 pointer-events-none rounded border border-border bg-background/95 px-2 py-1 text-xs leading-relaxed text-muted-foreground">
        蓝色实体按轴线与 OD 绘制；参考线宽度及井位标记大小仅作示意。{viewMode === "prof" ? "绿色虚线为内底线。" : ""}{viewMode === "3d" ? `网格参考高程 ${scene.bounds.min[2].toFixed(2)}m。` : ""}
        {schematicCount > 0 && " 参考线缺少实体所需的物理尺寸或高程语义；DN 为公称值。"}
      </div>
    </>}
    {scene.notices.length > 0 && <div role="status" className="absolute top-24 left-3 right-14 z-10 max-h-28 overflow-auto rounded border border-amber-600/50 bg-background/95 px-2 py-1 text-xs text-amber-700 dark:text-amber-300">{scene.notices.map((n, i) => <p key={i}>{n}</p>)}</div>}
    {!hasData && <div className="absolute inset-0 flex items-center justify-center pointer-events-none"><div className="max-w-sm px-8 text-center space-y-2"><p className="text-sm font-medium text-foreground">暂无可显示的几何</p><p className="text-xs leading-relaxed text-muted-foreground">选择设计候选或已有会话后显示真实路径与高程。</p></div></div>}
  </div>
}
