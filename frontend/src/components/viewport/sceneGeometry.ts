/** Coordinates stay in project metres. DN never supplies a physical diameter. */
export type Point3 = [number, number, number]
export interface SceneNode {
  id: string
  point: Point3
  ground: number | null
  invert: number | null
}
export interface ScenePipe {
  id: string
  points: Point3[]
  axis: Point3[] | null
  invert: Point3[] | null
  reference: "axis" | "invert" | "legacy_invert" | "unknown"
  inner: number | null
  outer: number | null
  dn: number | null
  solid: boolean
}
export type SceneObstacle =
  | { id: string; kind: "aabb"; min: Point3; max: Point3 }
  | { id: string; kind: "existing_pipe"; start: Point3; end: Point3; outer: number }
export interface SceneGeometry {
  nodes: SceneNode[]
  pipes: ScenePipe[]
  obstacles: SceneObstacle[]
  notices: string[]
  bounds: { min: Point3; max: Point3; center: Point3; span: number }
}

const finite = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null
const positive = (value: unknown) => {
  const n = finite(value)
  return n !== null && n > 0 ? n : null
}
export function readPoint(value: any): Point3 | null {
  if (!value) return null
  const p = Array.isArray(value) ? value.map(finite) : [finite(value.x_m ?? value.x), finite(value.y_m ?? value.y), finite(value.z_m ?? value.z)]
  return p.length === 3 && p.every((n: number | null) => n !== null) ? p as Point3 : null
}

export function parseSceneGeometry(ir: any): SceneGeometry {
  const notices: string[] = []
  const nodes: SceneNode[] = []
  for (const n of Array.isArray(ir?.nodes) ? ir.nodes : []) {
    const point = readPoint(n.position) ?? readPoint(n)
    if (!point) { notices.push(`节点 ${n.node_id ?? n.id ?? "?"} 缺少完整坐标，未绘制`); continue }
    nodes.push({ id: String(n.node_id ?? n.id ?? "未命名节点"), point,
      ground: finite(n.ground_elevation_m ?? n.ground),
      invert: finite(n.ports?.[0]?.position?.z_m ?? n.invert_elevation_m ?? n.invert) })
  }
  const pipes: ScenePipe[] = []
  for (const s of Array.isArray(ir?.segments) ? ir.segments : []) {
    const id = String(s.segment_id ?? s.id ?? "未命名管段")
    const points = Array.isArray(s.centerline) ? s.centerline.map(readPoint) : []
    // Never bridge missing vertices or invent a segment between adjacent nodes.
    if (points.length < 2 || points.some((p: Point3 | null) => !p)) {
      notices.push(`管段 ${id} 缺少完整路径，未绘制`); continue
    }
    const reference: ScenePipe["reference"] = ["axis", "invert", "legacy_invert"].includes(s.centerline_reference)
      ? s.centerline_reference : ir?.protocol_version === "1.0" ? "legacy_invert" : "unknown"
    let inner = positive(s.inner_diameter_mm), outer = positive(s.outer_diameter_mm)
    const wall = positive(s.wall_thickness_mm)
    let badDimensions = [s.inner_diameter_mm, s.outer_diameter_mm, s.wall_thickness_mm].some(v => v != null && positive(v) === null)
    if (inner !== null && wall !== null) {
      const expected = inner + 2 * wall
      if (outer !== null && Math.abs(outer - expected) > 1e-6) badDimensions = true
      outer = expected
    } else if (outer !== null && wall !== null && inner === null) inner = outer - 2 * wall
    if (inner !== null && (inner <= 0 || (outer !== null && outer < inner))) badDimensions = true
    if (badDimensions) { inner = null; outer = null; notices.push(`管段 ${id} 物理尺寸无效，按参考线显示`) }
    const path = points as Point3[]
    const axis = reference === "axis" ? path : reference === "invert" && inner !== null
      ? path.map(([x, y, z]): Point3 => [x, y, z + inner! / 2000]) : null
    const invert = reference === "invert" || reference === "legacy_invert" ? path
      : reference === "axis" && inner !== null ? path.map(([x, y, z]): Point3 => [x, y, z - inner! / 2000]) : null
    const solid = axis !== null && outer !== null && !badDimensions
    pipes.push({ id, points: path, axis, invert, reference, inner, outer, dn: positive(s.diameter_mm ?? s.dn), solid })
  }
  const obstacles: SceneObstacle[] = []
  const rawObstacles = ir?.preview_collision_context?.obstacles ?? ir?.obstacles ?? []
  for (const o of Array.isArray(rawObstacles) ? rawObstacles : []) {
    const id = String(o.obstacle_id ?? o.id ?? "未命名障碍物")
    if (o.kind === "aabb") {
      const min = readPoint(o.min_corner), max = readPoint(o.max_corner)
      if (min && max && min.every((v, i) => v < max[i])) { obstacles.push({ id, kind: "aabb", min, max }); continue }
    } else if (o.kind === "existing_pipe") {
      const start = readPoint(o.start_center), end = readPoint(o.end_center), outer = positive(o.outer_diameter_mm)
      if (start && end && outer && Math.hypot(...start.map((v, i) => v - end[i])) > 1e-9) {
        obstacles.push({ id, kind: "existing_pipe", start, end, outer }); continue
      }
    }
    notices.push(`障碍物 ${id} 缺少可确认的三维几何，未绘制`)
  }
  const all: Point3[] = []
  nodes.forEach(n => { all.push(n.point); if (n.ground !== null) all.push([n.point[0], n.point[1], n.ground]); if (n.invert !== null) all.push([n.point[0], n.point[1], n.invert]) })
  pipes.forEach(s => (s.axis ?? s.points).forEach(p => {
    const r = s.solid ? s.outer! / 2000 : 0
    all.push([p[0] - r, p[1] - r, p[2] - r], [p[0] + r, p[1] + r, p[2] + r])
  }))
  obstacles.forEach(o => {
    if (o.kind === "aabb") all.push(o.min, o.max)
    else [o.start, o.end].forEach(p => { const r = o.outer / 2000; all.push([p[0] - r, p[1] - r, p[2] - r], [p[0] + r, p[1] + r, p[2] + r]) })
  })
  const min: Point3 = all.length ? [0, 1, 2].map(i => Math.min(...all.map(p => p[i]))) as Point3 : [0, 0, 0]
  const max: Point3 = all.length ? [0, 1, 2].map(i => Math.max(...all.map(p => p[i]))) as Point3 : [0, 0, 0]
  const center = min.map((v, i) => (v + max[i]) / 2) as Point3
  return { nodes, pipes, obstacles, notices, bounds: { min, max, center, span: Math.max(...max.map((v, i) => v - min[i]), 1) } }
}

export function pipeReferenceLabel(pipe: ScenePipe): string {
  return pipe.axis ? "轴线" : pipe.reference === "invert" ? "内底参考线" : pipe.reference === "legacy_invert" ? "旧版内底参考线" : "参考线（语义未确认）"
}
export function pipeLabel(pipe: ScenePipe): string {
  return `${pipe.id}${pipe.dn !== null ? ` · DN${pipe.dn}（公称）` : ""}${pipe.solid ? ` · OD ${pipe.outer}mm` : ` · ${pipeReferenceLabel(pipe)}示意，非实体`}`
}
