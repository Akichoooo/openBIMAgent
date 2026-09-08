import React, { useMemo } from "react"
import { Canvas } from "@react-three/fiber"
import { OrbitControls, Grid } from "@react-three/drei"
import * as THREE from "three"

interface Node3D {
  id: string
  x: number
  y: number
  ground: number
  invert: number
}
interface Seg3D {
  a: string
  b: string
  dn: number
}

interface Scene3DProps {
  irData?: any
  isDark?: boolean
  showGrid?: boolean
  verticalExag?: boolean
  autoRotate?: boolean
}

// 与 CanvasViewport 2D 解析同口径：从 CompiledUtilityIR 派生 nodes/segments
function parseIr(irData: any): { nodes: Node3D[]; segments: Seg3D[] } {
  if (!irData || !irData.nodes || irData.nodes.length < 2) return { nodes: [], segments: [] }
  const nodes: Node3D[] = irData.nodes.map((n: any) => ({
    id: n.node_id || n.id,
    x: n.x ?? n.x_m ?? 0,
    y: n.y ?? n.y_m ?? 0,
    ground: n.ground ?? n.ground_elevation_m ?? 10.9,
    invert: n.invert ?? n.invert_z ?? 8.7,
  }))
  const rawSegs =
    irData.segments && irData.segments.length
      ? irData.segments
      : nodes.slice(0, -1).map((n: Node3D, i: number) => ({ a: n.id, b: nodes[i + 1].id }))
  const segments: Seg3D[] = rawSegs.map((s: any, i: number) => ({
    a: s.a || s.from_node || s.start_node_id || nodes[i]?.id,
    b: s.b || s.to_node || s.end_node_id || nodes[i + 1]?.id || "",
    dn: s.diameter_mm || s.dn || 400,
  }))
  return { nodes, segments }
}

const Z0 = 10 // 高程基准（场景落到 y≈0 附近）

// 管段：连接两节点 invert 高程的定向圆柱
function Pipe({ a, b, dn, vscale }: { a: Node3D; b: Node3D; dn: number; vscale: number }) {
  const { position, quaternion, length } = useMemo(() => {
    const start = new THREE.Vector3(a.x, (a.invert - Z0) * vscale, a.y)
    const end = new THREE.Vector3(b.x, (b.invert - Z0) * vscale, b.y)
    const dir = new THREE.Vector3().subVectors(end, start)
    const len = Math.max(dir.length(), 0.01)
    const mid = new THREE.Vector3().addVectors(start, end).multiplyScalar(0.5)
    const quat = new THREE.Quaternion().setFromUnitVectors(
      new THREE.Vector3(0, 1, 0),
      dir.clone().normalize()
    )
    return { position: mid, quaternion: quat, length: len }
  }, [a, b, vscale])
  const radius = Math.max(0.2, dn / 1600)
  return (
    <mesh position={position} quaternion={quaternion} castShadow>
      <cylinderGeometry args={[radius, radius, length, 20]} />
      <meshStandardMaterial color="#3b82f6" metalness={0.35} roughness={0.55} />
    </mesh>
  )
}

// 检查井：invert→ground 竖直井筒 + 井口圆环
function Manhole({ node, vscale }: { node: Node3D; vscale: number }) {
  const gY = (node.ground - Z0) * vscale
  const iY = (node.invert - Z0) * vscale
  const h = Math.max(0.1, gY - iY)
  return (
    <group position={[node.x, 0, node.y]}>
      <mesh position={[0, (gY + iY) / 2, 0]} castShadow>
        <cylinderGeometry args={[0.55, 0.55, h, 20]} />
        <meshStandardMaterial color="#94a3b8" metalness={0.2} roughness={0.7} />
      </mesh>
      <mesh position={[0, gY, 0]} rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[0.6, 0.09, 10, 28]} />
        <meshStandardMaterial color="#e2e8f0" metalness={0.4} roughness={0.4} />
      </mesh>
    </group>
  )
}

/**
 * R3F 真 3D 管网场景（方案 G）。保守增量：仅替换 CanvasViewport 的 3d 模式，
 * plan/prof 的 2D canvas 与空态逻辑完全保留。消费现有工具条 state：
 * showGrid（地面网格）/ verticalExag（垂直夸大 ×3）/ autoRotate（自旋）。
 */
export const Scene3D: React.FC<Scene3DProps> = ({
  irData,
  isDark = true,
  showGrid = true,
  verticalExag = true,
  autoRotate = false,
}) => {
  const { nodes, segments } = useMemo(() => parseIr(irData), [irData])
  const byId = useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n])), [nodes])
  const vscale = verticalExag ? 3 : 1

  const center = useMemo<[number, number, number]>(() => {
    if (!nodes.length) return [0, 0, 0]
    const cx = nodes.reduce((s, n) => s + n.x, 0) / nodes.length
    const cz = nodes.reduce((s, n) => s + n.y, 0) / nodes.length
    return [cx, 0, cz]
  }, [nodes])

  const span = useMemo(() => {
    if (!nodes.length) return 60
    const xs = nodes.map((n) => n.x)
    const ys = nodes.map((n) => n.y)
    return Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), 30)
  }, [nodes])

  return (
    <Canvas
      className="w-full h-full block"
      dpr={[1, 2]}
      camera={{ position: [center[0] + span, span * 0.8, center[2] + span], fov: 50 }}
    >
      <color attach="background" args={[isDark ? "#0b1220" : "#f1f5f9"]} />
      <ambientLight intensity={0.65} />
      <directionalLight position={[span, span * 1.6, span * 0.6]} intensity={1.1} />
      <directionalLight position={[-span, span, -span]} intensity={0.35} />
      {showGrid && (
        <Grid
          position={[center[0], -0.6, center[2]]}
          args={[span * 4, span * 4]}
          cellSize={5}
          cellThickness={0.6}
          cellColor={isDark ? "#1e293b" : "#cbd5e1"}
          sectionSize={25}
          sectionThickness={1}
          sectionColor={isDark ? "#3b82f6" : "#94a3b8"}
          fadeDistance={span * 5}
          fadeStrength={1}
          infiniteGrid
        />
      )}
      {nodes.map((n) => (
        <Manhole key={n.id} node={n} vscale={vscale} />
      ))}
      {segments.map((s, i) => {
        const a = byId[s.a]
        const b = byId[s.b]
        return a && b ? <Pipe key={i} a={a} b={b} dn={s.dn} vscale={vscale} /> : null
      })}
      <OrbitControls
        target={center}
        enableDamping
        autoRotate={autoRotate}
        autoRotateSpeed={0.8}
        maxPolarAngle={Math.PI / 2.05}
        minDistance={span * 0.2}
        maxDistance={span * 6}
      />
    </Canvas>
  )
}
