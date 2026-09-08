import React, { useRef, useEffect, useState, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Scene3D } from "@/components/viewport/Scene3D"
import {
  Grid,
  ArrowUpDown,
  RotateCw,
  Maximize2,
  Minimize2,
  Target,
  Play,
  Pause,
  Ghost,
} from "lucide-react"

type SegLite = { a: string; b: string; dn: number; slope: number; len: number }
type DiffItem = { kind: "added" | "removed" | "changed"; key: string; detail: string }

/** 几何差分(Cursor 范式):按段 key 对照改动前后 IR,产出新增/删除/参数变化清单 */
function diffSegments(prev: SegLite[], cur: SegLite[]): DiffItem[] {
  const pk = new Map(prev.map((s) => [`${s.a}→${s.b}`, s]))
  const ck = new Map(cur.map((s) => [`${s.a}→${s.b}`, s]))
  const out: DiffItem[] = []
  for (const [k, s] of ck) {
    const p = pk.get(k)
    if (!p) {
      out.push({ kind: "added", key: k, detail: `新增段 DN${s.dn} · 坡度 ${s.slope}` })
      continue
    }
    const d: string[] = []
    if (p.dn !== s.dn) d.push(`DN ${p.dn}→${s.dn}`)
    if (Math.abs(p.slope - s.slope) > 1e-6) d.push(`坡度 ${p.slope}→${s.slope}`)
    if (Math.abs((p.len || 0) - (s.len || 0)) > 0.05) d.push(`长 ${p.len}→${s.len}m`)
    if (d.length) out.push({ kind: "changed", key: k, detail: d.join(" · ") })
  }
  for (const [k] of pk) {
    if (!ck.has(k)) out.push({ kind: "removed", key: k, detail: "删除段（改道绕行）" })
  }
  return out
}

export interface CanvasViewportProps {
  viewMode: "3d" | "plan" | "prof"
  onViewModeChange?: (mode: "3d" | "plan" | "prof") => void
  irData?: any
  isDark?: boolean
}

export const CanvasViewport: React.FC<CanvasViewportProps> = ({
  viewMode,
  irData,
  isDark = true,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)

  // Camera & Tools State
  const [gridEnabled, setGridEnabled] = useState(true)
  const [exagEnabled, setExagEnabled] = useState(true)
  const [spinEnabled, setSpinEnabled] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [hudCamText, setHudCamText] = useState("")
  const [hudStatText, setHudStatText] = useState("—")
  const [hasData, setHasData] = useState(false)

  // Timeline State
  const [tlStep, setTlStep] = useState<number>(2) // default to converged state (iter 2)
  const [isPlaying, setIsPlaying] = useState(false)

  // Ghosting 几何差分(Cursor 范式):改动前 IR 红色幽灵层叠合 + 差分对照面板
  const [ghostEnabled, setGhostEnabled] = useState(false)
  const [diffItems, setDiffItems] = useState<DiffItem[]>([])
  const prevSceneRef = useRef<{ nodes: any[]; segments: any[] } | null>(null)

  // Internal 3D Camera & Scene State
  const camRef = useRef({
    yaw: -0.88,
    pitch: 0.58,
    dist: 120,
    tx: 66,
    ty: 8,
    tz: 9.6,
  })

  // Scene Data（默认空场景：无真实 IR 时不渲染任何伪造几何）
  const sceneRef = useRef<{
    nodes: any[]
    segments: any[]
    obst: any[]
    healedBend: { from: string; to: string; pts: [number, number][] } | null
    timeline: Array<{ st: string; lb: string; sub: string; note: string }>
  }>({
    nodes: [],
    segments: [],
    obst: [],
    healedBend: null,
    timeline: [],
  })

  // Parse irData if present
  useEffect(() => {
    // Ghosting:新 IR 到达前,把当前场景快照为"改动前"幽灵层
    if (irData && sceneRef.current.nodes.length > 0) {
      prevSceneRef.current = {
        nodes: [...sceneRef.current.nodes],
        segments: [...sceneRef.current.segments],
      }
    }
    if (!irData || !irData.nodes || irData.nodes.length < 2) {
      sceneRef.current.nodes = []
      sceneRef.current.segments = []
      sceneRef.current.obst = []
      sceneRef.current.healedBend = null
      sceneRef.current.timeline = []
      setHasData(false)
      setHudStatText("—")
      render()
      return
    }
    const nodes = irData.nodes.map((n: any) => ({
      id: n.node_id || n.id,
      x: n.x,
      y: n.y,
      ground: n.ground || n.ground_elevation_m || 10.9,
      invert: n.invert || n.invert_z || 8.7,
    }))

    const segments =
      irData.segments && irData.segments.length
        ? irData.segments.map((s: any, i: number) => ({
            a: s.a || s.from_node || nodes[i]?.id,
            b: s.b || s.to_node || nodes[i + 1]?.id || "",
            dn: s.diameter_mm || s.dn || 400,
            slope: s.slope || 0.003,
            len: s.length_m || s.len || 25,
          }))
        : nodes.slice(0, -1).map((n: any, i: number) => {
            const next = nodes[i + 1]
            return {
              a: n.id,
              b: next.id,
              dn: 400,
              slope: 0.003,
              len: +Math.hypot(next.x - n.x, next.y - n.y).toFixed(1),
            }
          })

    sceneRef.current.nodes = nodes
    sceneRef.current.segments = segments
    sceneRef.current.obst = Array.isArray(irData.obstacles)
      ? irData.obstacles.map((o: any, i: number) => ({
          id: o.obstacle_id || o.id || `OB-${i + 1}`,
          kind: o.kind || o.type || "障碍物",
          x: o.x ?? 0,
          y: o.y ?? 0,
          w: o.w ?? o.width ?? 4,
          d: o.d ?? o.depth ?? 4,
          h: o.h ?? o.height ?? 1.2,
          clear: o.clear ?? o.clearance_m ?? 2.5,
        }))
      : []
    sceneRef.current.healedBend = irData.healed_bend || null
    sceneRef.current.timeline = Array.isArray(irData.timeline) ? irData.timeline : []
    if (prevSceneRef.current) {
      setDiffItems(diffSegments(prevSceneRef.current.segments, segments))
    }
    setHasData(true)
    setHudStatText(`${nodes.length} 井 · ${segments.length} 段 · DN${segments[0]?.dn || 400}`)
    resetCamera()
  }, [irData])

  const VSCALE = () => (exagEnabled ? 3 : 1)
  const z0 = 10.3
  const nz = (z: number) => (z - z0) * VSCALE()

  // Project point in 3D
  const project = (p: [number, number, number], W: number, H: number): [number, number, number] | null => {
    const cam = camRef.current
    const cp = Math.cos(cam.pitch),
      sp = Math.sin(cam.pitch),
      cy = Math.cos(cam.yaw),
      sy = Math.sin(cam.yaw)
    const px = cam.tx + cam.dist * cp * sy,
      py = cam.ty + cam.dist * cp * cy,
      pz = cam.tz + cam.dist * sp
    const dx = p[0] - px,
      dy = p[1] - py,
      dz = p[2] - pz
    const fx = -cp * sy,
      fy = -cp * cy,
      fz = -sp
    const rx = cy,
      ry = -sy,
      rz = 0
    const ux = -sp * sy,
      uy = -sp * cy,
      uz = cp
    const vx = dx * rx + dy * ry + dz * rz,
      vy = dx * ux + dy * uy + dz * uz,
      vz = dx * fx + dy * fy + dz * fz
    if (vz < 1) return null
    const f = H * 0.9
    return [W / 2 + (vx * f) / vz, H / 2 - (vy * f) / vz, vz]
  }

  const avg = (a: any[]) => a.reduce((s, q) => s + q[2], 0) / (a.length || 1)

  // Main Render Function
  const render = useCallback(() => {
    const cv = canvasRef.current
    if (!cv) return
    const cx = cv.getContext("2d")
    if (!cx) return

    const W = cv.clientWidth
    const H = cv.clientHeight
    if (!W || !H) return

    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
      cv.width = Math.round(W * dpr)
      cv.height = Math.round(H * dpr)
    }
    cx.setTransform(dpr, 0, 0, dpr, 0, 0)
    cx.clearRect(0, 0, W, H)

    const fontMono = "ui-monospace, Consolas, monospace"
    const { nodes, segments, obst, healedBend } = sceneRef.current
    const bendT = [0, 0.5, 1][tlStep] ?? 1

    // 空场景：只画空画布，几何渲染全部跳过（防除零/越界）
    if (nodes.length === 0) {
      setHudCamText("")
      return
    }

    if (viewMode === "plan") {
      // 2D Plan View
      const pad = 56
      const xs: number[] = []
      const ys: number[] = []
      nodes.forEach((n) => {
        xs.push(n.x)
        ys.push(n.y)
      })
      if (healedBend?.pts) {
        healedBend.pts.forEach((q) => {
          xs.push(q[0])
          ys.push(q[1])
        })
      }
      obst.forEach((o) => {
        const R = Math.max(o.w, o.d) / 2 + (o.clear || 2.5)
        xs.push(o.x - R, o.x + R)
        ys.push(o.y - R, o.y + R)
      })
      const x0 = Math.min(...xs, 0) - 6,
        x1 = Math.max(...xs, 140) + 6,
        y0 = Math.min(...ys, -15) - 6,
        y1 = Math.max(...ys, 30) + 6
      const s = Math.min((W - 2 * pad) / (x1 - x0), (H - 2 * pad) / (y1 - y0))
      const cxMid = (x0 + x1) / 2,
        cyMid = (y0 + y1) / 2
      const X = (x: number) => W / 2 + (x - cxMid) * s,
        Y = (y: number) => H / 2 - (y - cyMid) * s

      if (gridEnabled) {
        cx.strokeStyle = isDark ? "rgba(255,255,255,.05)" : "rgba(0,0,0,.06)"
        cx.lineWidth = 1
        const step = 20
        for (let x = Math.ceil(x0 / step) * step; x <= x1; x += step) {
          cx.beginPath()
          cx.moveTo(X(x), Y(y0))
          cx.lineTo(X(x), Y(y1))
          cx.stroke()
        }
        for (let y = Math.ceil(y0 / step) * step; y <= y1; y += step) {
          cx.beginPath()
          cx.moveTo(X(x0), Y(y))
          cx.lineTo(X(x1), Y(y))
          cx.stroke()
        }
      }

      // Obstacles
      obst.forEach((o) => {
        cx.fillStyle = isDark ? "rgba(120,140,165,.18)" : "rgba(120,140,165,.12)"
        cx.strokeStyle = "rgba(160,180,205,.5)"
        cx.fillRect(X(o.x - o.w / 2), Y(o.y + o.d / 2), o.w * s, o.d * s)
        cx.strokeRect(X(o.x - o.w / 2), Y(o.y + o.d / 2), o.w * s, o.d * s)

        cx.strokeStyle = o.id === "BLDG-A" && tlStep < 2 ? "rgba(239,68,68,.7)" : "rgba(217,119,6,.6)"
        cx.setLineDash([4, 4])
        const R = (Math.max(o.w, o.d) / 2 + o.clear) * s
        cx.beginPath()
        cx.arc(X(o.x), Y(o.y), R, 0, 7)
        cx.stroke()
        cx.setLineDash([])

        cx.fillStyle = isDark ? "rgba(160,180,205,.8)" : "#475569"
        cx.font = "10px " + fontMono
        cx.fillText(o.id + " · " + o.kind, X(o.x - o.w / 2), Y(o.y + o.d / 2) - 6)
      })

      // Ghosting:改动前路径红色半透明虚线(几何差分幽灵层)
      if (ghostEnabled && prevSceneRef.current?.segments?.length) {
        const prevById: Record<string, any> = {}
        prevSceneRef.current.nodes.forEach((n: any) => (prevById[n.id] = n))
        cx.save()
        cx.strokeStyle = "rgba(239,68,68,.5)"
        cx.lineWidth = 2
        cx.setLineDash([6, 4])
        prevSceneRef.current.segments.forEach((seg: any) => {
          const A = prevById[seg.a],
            B = prevById[seg.b]
          if (!A || !B) return
          cx.beginPath()
          cx.moveTo(X(A.x), Y(A.y))
          cx.lineTo(X(B.x), Y(B.y))
          cx.stroke()
        })
        cx.restore()
      }

      // Segments
      const byId: Record<string, any> = {}
      nodes.forEach((n) => (byId[n.id] = n))
      segments.forEach((seg) => {
        const A = byId[seg.a],
          B = byId[seg.b]
        if (!A || !B) return
        const isH = healedBend && seg.a === healedBend.from && seg.b === healedBend.to
        cx.lineWidth = isH ? 3.5 : 2.2
        cx.strokeStyle = isH ? (tlStep === 0 ? "#ef4444" : tlStep === 1 ? "#f59e0b" : "#10b981") : "#3b82f6"
        cx.beginPath()
        if (isH && healedBend.pts) {
          healedBend.pts.forEach((q, i) => {
            const t = i / (healedBend.pts.length - 1)
            const lx = A.x + (B.x - A.x) * t,
              ly = A.y + (B.y - A.y) * t
            const px = lx + (q[0] - lx) * bendT,
              py = ly + (q[1] - ly) * bendT
            i ? cx.lineTo(X(px), Y(py)) : cx.moveTo(X(px), Y(py))
          })
        } else {
          cx.moveTo(X(A.x), Y(A.y))
          cx.lineTo(X(B.x), Y(B.y))
        }
        cx.stroke()
      })

      // Nodes
      nodes.forEach((n) => {
        cx.fillStyle = isDark ? "#f8fafc" : "#0f172a"
        cx.beginPath()
        cx.arc(X(n.x), Y(n.y), 4.5, 0, 7)
        cx.fill()
        cx.strokeStyle = isDark ? "rgba(255,255,255,.3)" : "rgba(0,0,0,.3)"
        cx.beginPath()
        cx.arc(X(n.x), Y(n.y), 7.5, 0, 7)
        cx.stroke()
        cx.fillStyle = isDark ? "#e2e8f0" : "#1e293b"
        cx.font = "10.5px " + fontMono
        cx.fillText(n.id, X(n.x) + 9, Y(n.y) - 7)
      })

      setHudCamText("2D 正交平面图 · 俯视投影")
    } else if (viewMode === "prof") {
      // Profile View
      const padL = 64,
        padR = 40,
        padT = 44,
        padB = 52
      const chain = [0]
      segments.forEach((s) => chain.push(chain[chain.length - 1] + (s.len || 25)))
      const x0 = 0,
        x1 = chain[chain.length - 1] || 100
      const zs = nodes.map((n) => n.ground).concat(nodes.map((n) => n.invert))
      const zMin = Math.min(...zs) - 0.6,
        zMax = Math.max(...zs) + 0.6
      const boxW = W - padL - padR,
        boxH = H - padT - padB
      const exagF = exagEnabled ? 20 : 1
      const sxm = boxW / Math.max(x1 - x0, 1)
      const sym = Math.min(sxm * exagF, boxH / Math.max(zMax - zMin, 0.1))
      const zMid = (zMin + zMax) / 2
      const X = (x: number) => padL + ((x - x0) / Math.max(x1 - x0, 1)) * boxW
      const Y = (z: number) => H / 2 - (z - zMid) * sym

      // Grid
      cx.strokeStyle = isDark ? "rgba(255,255,255,.06)" : "rgba(0,0,0,.08)"
      cx.fillStyle = isDark ? "rgba(154,164,178,.7)" : "#475569"
      cx.font = "10px " + fontMono
      cx.lineWidth = 1
      for (let z = Math.ceil(zMin * 2) / 2; z <= zMax; z += 0.5) {
        cx.beginPath()
        cx.moveTo(padL, Y(z))
        cx.lineTo(W - padR, Y(z))
        cx.stroke()
        cx.fillText(z.toFixed(1) + "m", 18, Y(z) + 3)
      }

      // Ground Fill
      cx.beginPath()
      cx.moveTo(X(chain[0]), Y(nodes[0].ground))
      nodes.forEach((n, i) => cx.lineTo(X(chain[i]), Y(n.ground)))
      for (let i = nodes.length - 1; i >= 0; i--) cx.lineTo(X(chain[i]), Y(nodes[i].invert))
      cx.closePath()
      cx.fillStyle = isDark ? "rgba(59,130,246,.08)" : "rgba(59,130,246,.1)"
      cx.fill()

      // Ground Line
      cx.strokeStyle = isDark ? "#94a3b8" : "#475569"
      cx.lineWidth = 1.6
      cx.beginPath()
      nodes.forEach((n, i) => (i ? cx.lineTo(X(chain[i]), Y(n.ground)) : cx.moveTo(X(chain[i]), Y(n.ground))))
      cx.stroke()

      // Invert Line
      cx.strokeStyle = "#3b82f6"
      cx.lineWidth = 2.6
      cx.beginPath()
      nodes.forEach((n, i) => (i ? cx.lineTo(X(chain[i]), Y(n.invert)) : cx.moveTo(X(chain[i]), Y(n.invert))))
      cx.stroke()

      // Wells & Labels
      nodes.forEach((n, i) => {
        cx.strokeStyle = isDark ? "rgba(255,255,255,.4)" : "rgba(0,0,0,.4)"
        cx.lineWidth = 1.4
        cx.beginPath()
        cx.moveTo(X(chain[i]), Y(n.ground))
        cx.lineTo(X(chain[i]), Y(n.invert))
        cx.stroke()

        cx.fillStyle = isDark ? "#f8fafc" : "#0f172a"
        cx.beginPath()
        cx.arc(X(chain[i]), Y(n.invert), 3.5, 0, 7)
        cx.fill()

        cx.fillText(n.id, X(chain[i]) - 14, Y(n.ground) - 8)
        cx.fillText("inv " + (n.invert || 0).toFixed(2), X(chain[i]) + 8, Y(n.invert) + 4)
      })

      setHudCamText(`纵断面 · 垂直夸大 ${exagEnabled ? "×20" : "1:1"} · 链长 ${x1.toFixed(0)}m`)
    } else {
      // 3D Orbital Projection
      const prims: any[] = []
      const P = (p: [number, number, number]) => project(p, W, H)

      // Grid
      if (gridEnabled) {
        const gz = 0
        const gc = isDark ? "rgba(255,255,255,.05)" : "rgba(0,0,0,.08)"
        for (let x = -20; x <= 160; x += 10) {
          const a = P([x, -24, gz]),
            b = P([x, 44, gz])
          if (a && b) prims.push({ d: (a[2] + b[2]) / 2, k: "l", p: [a, b], c: gc, w: 1 })
        }
        for (let y = -20; y <= 40; y += 10) {
          const a = P([-20, y, gz]),
            b = P([160, y, gz])
          if (a && b) prims.push({ d: (a[2] + b[2]) / 2, k: "l", p: [a, b], c: gc, w: 1 })
        }
      }

      // Obstacles
      obst.forEach((o) => {
        const bx = o.x - o.w / 2,
          by = o.y - o.d / 2
        const g0 = nz(10.9)
        const c = [
          [bx, by],
          [bx + o.w, by],
          [bx + o.w, by + o.d],
          [bx, by + o.d],
        ]
        const bot = c.map((q) => P([q[0], q[1], g0]))
        const top = c.map((q) => P([q[0], q[1], g0 + (o.h || 1.2)]))
        if (bot.every(Boolean) && top.every(Boolean)) {
          prims.push({
            d: avg([...bot, ...top]),
            k: "poly",
            p: top,
            c: isDark ? "rgba(120,140,165,.24)" : "rgba(120,140,165,.15)",
            stroke: "rgba(160,180,205,.5)",
          })
          for (let i = 0; i < 4; i++) {
            prims.push({ d: (bot[i]![2] + top[i]![2]) / 2, k: "l", p: [bot[i], top[i]], c: "rgba(160,180,205,.4)", w: 1 })
            const a = bot[i]!,
              b = bot[(i + 1) % 4]!
            prims.push({ d: (a[2] + b[2]) / 2, k: "l", p: [a, b], c: "rgba(160,180,205,.35)", w: 1 })
          }
        }
      })

      // Segments
      const byId: Record<string, any> = {}
      nodes.forEach((n) => (byId[n.id] = n))
      segments.forEach((seg) => {
        const A = byId[seg.a],
          B = byId[seg.b]
        if (!A || !B) return
        const isH = healedBend && seg.a === healedBend.from && seg.b === healedBend.to
        let pts: number[][]
        if (isH && healedBend.pts) {
          const bp = healedBend.pts
          const lerp = (a: number, b: number, t: number) => a + (b - a) * t
          pts = bp.map((q, i) => {
            const t = i / (bp.length - 1)
            const lx = lerp(A.x, B.x, t),
              ly = lerp(A.y, B.y, t)
            return [lerp(lx, q[0], bendT), lerp(ly, q[1], bendT)]
          })
        } else {
          pts = [
            [A.x, A.y],
            [B.x, B.y],
          ]
        }

        const path = pts
          .map((q, i) => {
            const t = pts.length === 1 ? 0 : i / (pts.length - 1)
            const z = A.invert + (B.invert - A.invert) * t
            return P([q[0], q[1], nz(z)])
          })
          .filter(Boolean)

        if (path.length >= 2) {
          const col = isH ? (tlStep === 0 ? "#ef4444" : tlStep === 1 ? "#f59e0b" : "#10b981") : "#3b82f6"
          prims.push({ d: avg(path), k: "path", p: path, c: col, w: isH ? 3.5 : 2.4, glow: isH })
        }
      })

      // Wells
      nodes.forEach((n) => {
        const g = P([n.x, n.y, nz(n.ground)]),
          iv = P([n.x, n.y, nz(n.invert)])
        if (!g || !iv) return
        prims.push({ d: (g[2] + iv[2]) / 2, k: "l", p: [g, iv], c: isDark ? "rgba(255,255,255,.55)" : "rgba(0,0,0,.5)", w: 2 })
        prims.push({ d: g[2], k: "node", p: g, r: 4.5, c: isDark ? "#f8fafc" : "#0f172a", ring: true })
        prims.push({ d: iv[2], k: "node", p: iv, r: 3, c: "#3b82f6" })
        prims.push({ d: g[2] - 0.01, k: "label", p: [g[0] + 8, g[1] - 8], t: n.id, c: isDark ? "#e2e8f0" : "#0f172a" })
      })

      // Sort depth & draw
      prims.sort((a, b) => b.d - a.d)
      prims.forEach((r) => {
        cx.save()
        if (r.k === "l") {
          cx.strokeStyle = r.c
          cx.lineWidth = r.w
          cx.beginPath()
          cx.moveTo(r.p[0][0], r.p[0][1])
          cx.lineTo(r.p[1][0], r.p[1][1])
          cx.stroke()
        } else if (r.k === "path") {
          cx.strokeStyle = r.c
          cx.lineWidth = r.w
          if (r.glow) {
            cx.shadowColor = r.c
            cx.shadowBlur = 10
          }
          cx.beginPath()
          cx.moveTo(r.p[0][0], r.p[0][1])
          for (let i = 1; i < r.p.length; i++) cx.lineTo(r.p[i][0], r.p[i][1])
          cx.stroke()
        } else if (r.k === "poly") {
          cx.fillStyle = r.c
          cx.strokeStyle = r.stroke
          cx.beginPath()
          cx.moveTo(r.p[0][0], r.p[0][1])
          for (let i = 1; i < r.p.length; i++) cx.lineTo(r.p[i][0], r.p[i][1])
          cx.closePath()
          cx.fill()
          cx.stroke()
        } else if (r.k === "node") {
          cx.fillStyle = r.c
          cx.beginPath()
          cx.arc(r.p[0], r.p[1], r.r, 0, 7)
          cx.fill()
          if (r.ring) {
            cx.strokeStyle = isDark ? "rgba(255,255,255,.4)" : "rgba(0,0,0,.35)"
            cx.lineWidth = 1
            cx.beginPath()
            cx.arc(r.p[0], r.p[1], r.r + 3, 0, 7)
            cx.stroke()
          }
        } else if (r.k === "label") {
          cx.fillStyle = r.c
          cx.font = "11px " + fontMono
          cx.fillText(r.t, r.p[0], r.p[1])
        }
        cx.restore()
      })

      const cam = camRef.current
      setHudCamText(`yaw ${(cam.yaw * 57.3).toFixed(0)}° · pitch ${(cam.pitch * 57.3).toFixed(0)}° · d ${cam.dist.toFixed(0)}m`)
    }
  }, [viewMode, gridEnabled, exagEnabled, tlStep, isDark, ghostEnabled])

  // Spin animation
  useEffect(() => {
    if (!spinEnabled || viewMode !== "3d") return
    let req: number
    const loop = () => {
      camRef.current.yaw += 0.005
      render()
      req = requestAnimationFrame(loop)
    }
    req = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(req)
  }, [spinEnabled, viewMode, render])

  // Trigger render on props/state change
  useEffect(() => {
    render()
  }, [render])

  // Window resize
  useEffect(() => {
    const handleResize = () => render()
    window.addEventListener("resize", handleResize)
    return () => window.removeEventListener("resize", handleResize)
  }, [render])

  // Mouse interaction
  const dragRef = useRef({ dragging: false, lx: 0, ly: 0, panMode: false })

  const handlePointerDown = (e: React.PointerEvent) => {
    dragRef.current.dragging = true
    dragRef.current.lx = e.clientX
    dragRef.current.ly = e.clientY
    dragRef.current.panMode = e.shiftKey || e.button === 2
    canvasRef.current?.setPointerCapture(e.pointerId)
  }

  const handlePointerMove = (e: React.PointerEvent) => {
    if (!dragRef.current.dragging) return
    const dx = e.clientX - dragRef.current.lx
    const dy = e.clientY - dragRef.current.ly
    dragRef.current.lx = e.clientX
    dragRef.current.ly = e.clientY

    if (viewMode === "3d") {
      const cam = camRef.current
      if (dragRef.current.panMode) {
        const k = cam.dist / 700
        cam.tx -= dx * k * Math.cos(cam.yaw)
        cam.ty += dx * k * Math.sin(cam.yaw)
      } else {
        cam.yaw += dx * 0.005
        cam.pitch = Math.max(0.08, Math.min(1.5, cam.pitch + dy * 0.004))
      }
      render()
    }
  }

  const handlePointerUp = () => {
    dragRef.current.dragging = false
  }

  const handleWheel = (e: React.WheelEvent) => {
    if (viewMode === "3d") {
      e.preventDefault()
      const cam = camRef.current
      cam.dist = Math.max(30, Math.min(320, cam.dist * (1 + Math.sign(e.deltaY) * 0.09)))
      render()
    }
  }

  const resetCamera = () => {
    camRef.current = {
      yaw: -0.88,
      pitch: 0.58,
      dist: 120,
      tx: 66,
      ty: 8,
      tz: 9.6,
    }
    render()
  }

  const toggleFullscreen = () => {
    if (!containerRef.current) return
    if (!document.fullscreenElement) {
      containerRef.current.requestFullscreen?.()
      setIsFullscreen(true)
    } else {
      document.exitFullscreen?.()
      setIsFullscreen(false)
    }
  }

  // Self-healing timeline playback
  useEffect(() => {
    if (!isPlaying) return
    const timer = setInterval(() => {
      setTlStep((prev) => (prev >= 2 ? 0 : prev + 1))
    }, 1800)
    return () => clearInterval(timer)
  }, [isPlaying])

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full min-h-0 select-none overflow-hidden bg-background flex flex-col"
    >
      {/* 渲染画布：3d + 有数据走 R3F 真 3D 引擎；plan/prof 与空态保留 2D canvas */}
      {viewMode === "3d" && hasData ? (
        <Scene3D
          irData={irData}
          isDark={isDark}
          showGrid={gridEnabled}
          verticalExag={exagEnabled}
          autoRotate={spinEnabled}
        />
      ) : (
        <canvas
          ref={canvasRef}
          className="w-full h-full block cursor-grab active:cursor-grabbing"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onWheel={handleWheel}
          onContextMenu={(e) => e.preventDefault()}
        />
      )}

      {/* 悬浮 HUD 状态 (左上: 仅在加载了真实模型几何数据时展示) */}
      {hasData && hudStatText !== "—" && (
        <div className="absolute top-3 left-3.5 flex items-center gap-2 pointer-events-none z-10">
          <div className="flex items-center gap-2 px-2.5 py-1 rounded-lg bg-background/80 backdrop-blur-md border border-border/80 text-[11px] font-mono text-muted-foreground shadow-sm">
            <span className="w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)]" />
            <span>{hudStatText}</span>
          </div>
          {hudCamText && (
            <div className="px-2.5 py-1 rounded-lg bg-background/80 backdrop-blur-md border border-border/80 text-[11px] font-mono text-muted-foreground shadow-sm">
              {hudCamText}
            </div>
          )}
        </div>
      )}

      {/* Ghosting 几何差分对照面板(改动前红幽灵 vs 当前;含合规得失 note) */}
      {ghostEnabled && diffItems.length > 0 && (
        <div className="absolute top-14 left-3.5 w-72 max-h-56 overflow-y-auto rounded-lg bg-background/85 backdrop-blur-md border border-border/80 shadow-lg z-10 p-2 space-y-1">
          <div className="text-[10px] font-mono text-muted-foreground px-1">
            几何差分 · <span className="text-rose-500">红虚线=改动前</span> · 实线=当前
          </div>
          {diffItems.map((d) => (
            <div key={d.kind + d.key} className="flex items-start gap-1.5 px-1 py-0.5 text-[11px] font-mono">
              <span
                className={`mt-1 w-1.5 h-1.5 rounded-full shrink-0 ${
                  d.kind === "added" ? "bg-emerald-500" : d.kind === "removed" ? "bg-rose-500" : "bg-amber-500"
                }`}
              />
              <span className="text-foreground/80 shrink-0">{d.key}</span>
              <span className="text-muted-foreground">{d.detail}</span>
            </div>
          ))}
          {sceneRef.current.timeline[tlStep]?.note && (
            <div className="text-[10px] text-muted-foreground px-1 pt-1 border-t border-border/60">
              合规得失：{sceneRef.current.timeline[tlStep].note}
            </div>
          )}
        </div>
      )}

      {/* 悬浮相机控制工具条 (右上) */}
      <div className="absolute top-3 right-3.5 flex flex-col gap-1.5 z-10">
        <Button
          variant="outline"
          size="sm"
          className={`w-8 h-8 p-0 rounded-lg shadow-sm border transition-all cursor-pointer ${
            gridEnabled
              ? "bg-muted text-foreground border-border/90 shadow-inner font-semibold"
              : "bg-background/80 backdrop-blur-md text-muted-foreground border-border/70 hover:text-foreground hover:bg-muted/50"
          }`}
          onClick={() => setGridEnabled(!gridEnabled)}
          title="切换参考网格"
        >
          <Grid className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          size="sm"
          className={`w-8 h-8 p-0 rounded-lg shadow-sm border transition-all cursor-pointer ${
            exagEnabled
              ? "bg-muted text-foreground border-border/90 shadow-inner font-semibold"
              : "bg-background/80 backdrop-blur-md text-muted-foreground border-border/70 hover:text-foreground hover:bg-muted/50"
          }`}
          onClick={() => setExagEnabled(!exagEnabled)}
          title="垂直高程夸大"
        >
          <ArrowUpDown className="h-4 w-4" />
        </Button>
        {viewMode === "3d" && (
          <Button
            variant="outline"
            size="sm"
            className={`w-8 h-8 p-0 rounded-lg shadow-sm border transition-all cursor-pointer ${
              spinEnabled
                ? "bg-muted text-foreground border-border/90 shadow-inner font-semibold"
                : "bg-background/80 backdrop-blur-md text-muted-foreground border-border/70 hover:text-foreground hover:bg-muted/50"
            }`}
            onClick={() => setSpinEnabled(!spinEnabled)}
            title="自动环绕旋转"
          >
            <RotateCw className="h-4 w-4" />
          </Button>
        )}
        {diffItems.length > 0 && (
          <Button
            variant="outline"
            size="sm"
            className={`w-8 h-8 p-0 rounded-lg shadow-sm border transition-all cursor-pointer ${
              ghostEnabled
                ? "bg-muted text-foreground border-border/90 shadow-inner font-semibold"
                : "bg-background/80 backdrop-blur-md text-muted-foreground border-border/70 hover:text-foreground hover:bg-muted/50"
            }`}
            onClick={() => setGhostEnabled(!ghostEnabled)}
            title="几何差分:改动前路径红色幽灵层叠合对照"
          >
            <Ghost className="h-4 w-4" />
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          className="w-8 h-8 p-0 rounded-lg shadow-sm border border-border/70 bg-background/80 backdrop-blur-md text-muted-foreground hover:text-foreground hover:bg-muted/50 cursor-pointer"
          onClick={resetCamera}
          title="重置相机视角"
        >
          <Target className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="w-8 h-8 p-0 rounded-lg shadow-sm border border-border/70 bg-background/80 backdrop-blur-md text-muted-foreground hover:text-foreground hover:bg-muted/50 cursor-pointer"
          onClick={toggleFullscreen}
          title="全屏视口"
        >
          {isFullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
        </Button>
      </div>

      {/* 空态：无真实几何数据时不渲染任何伪造场景 */}
      {!hasData && (
        <div className="absolute inset-0 flex items-center justify-center z-10 pointer-events-none">
          <div className="text-center px-8 py-6 rounded-2xl bg-background/70 backdrop-blur-md border border-border/60 space-y-1.5">
            <p className="text-sm font-medium text-foreground">暂无几何数据</p>
            <p className="text-xs text-muted-foreground">
              发起新任务或选择已有会话后，真实 IR 几何将投影到此处
            </p>
          </div>
        </div>
      )}

      {/* 底部自愈回放时间线（仅当真实数据带 timeline 时显示） */}
      {sceneRef.current.timeline.length > 0 && (
      <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-3 px-3.5 py-2 rounded-xl bg-background/85 backdrop-blur-md border border-border/80 shadow-lg z-10 max-w-[92vw]">
        <Button
          variant="ghost"
          size="sm"
          className="h-7 w-7 p-0 rounded-full hover:bg-muted"
          onClick={() => setIsPlaying(!isPlaying)}
          title={isPlaying ? "暂停自愈回放" : "自动播放自愈迭代"}
        >
          {isPlaying ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5 fill-current" />}
        </Button>

        <div className="flex items-center gap-2">
          {sceneRef.current.timeline.map((step, idx) => {
            const isSelected = tlStep === idx
            return (
              <React.Fragment key={idx}>
                <button
                  onClick={() => {
                    setTlStep(idx)
                    setIsPlaying(false)
                  }}
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-mono transition-all ${
                    isSelected
                      ? idx === 0
                        ? "bg-rose-500/15 text-rose-500 border border-rose-500/30 font-semibold"
                        : idx === 1
                        ? "bg-amber-500/15 text-amber-500 border border-amber-500/30 font-semibold"
                        : "bg-emerald-500/15 text-emerald-500 border border-emerald-500/30 font-semibold"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted/50 border border-transparent"
                  }`}
                >
                  <span
                    className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] border ${
                      isSelected
                        ? idx === 0
                          ? "border-rose-500 bg-rose-500 text-white"
                          : idx === 1
                          ? "border-amber-500 bg-amber-500 text-white"
                          : "border-emerald-500 bg-emerald-500 text-white"
                        : "border-muted-foreground/40"
                    }`}
                  >
                    {idx}
                  </span>
                  <span>{step.lb}</span>
                </button>
                {idx < sceneRef.current.timeline.length - 1 && (
                  <div className="w-4 h-[1px] bg-border/80" />
                )}
              </React.Fragment>
            )
          })}
        </div>

        <div className="text-[11px] text-muted-foreground font-mono pl-2 border-l border-border/60 max-w-xs truncate hidden sm:block">
          {sceneRef.current.timeline[tlStep]?.note}
        </div>
      </div>
      )}
    </div>
  )
}
