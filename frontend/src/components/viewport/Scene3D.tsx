import React, { useEffect, useMemo } from "react"
import { Canvas, useThree } from "@react-three/fiber"
import { OrbitControls, Grid, Html, Line } from "@react-three/drei"
import * as THREE from "three"
import { parseSceneGeometry, pipeLabel, type Point3, type ScenePipe } from "./sceneGeometry"

export interface Scene3DProps {
  irData?: any
  isDark?: boolean
  showGrid?: boolean
  verticalExag?: boolean
  autoRotate?: boolean
  cameraResetKey?: number
}

/** Three uses Y up; only the display origin changes, never the source elevations. */
const toThree = (p: Point3, origin: Point3): Point3 => [p[0] - origin[0], p[2] - origin[2], p[1] - origin[1]]

function FitCamera({ radius, resetKey }: { radius: number; resetKey: string }) {
  const { get, size } = useThree()
  useEffect(() => {
    const camera = get().camera
    if (!(camera instanceof THREE.PerspectiveCamera)) return
    const vertical = THREE.MathUtils.degToRad(camera.fov / 2)
    const horizontal = Math.atan(Math.tan(vertical) * size.width / Math.max(1, size.height))
    const distance = radius / Math.sin(Math.min(vertical, horizontal)) * 1.2
    camera.position.copy(new THREE.Vector3(0.65, 0.55, 0.75).normalize().multiplyScalar(distance))
    camera.near = Math.max(0.001, distance / 10000)
    camera.far = Math.max(1000, distance * 30)
    camera.lookAt(0, 0, 0)
    camera.updateProjectionMatrix()
  }, [get, size.width, size.height, radius, resetKey])
  return null
}

/** Preserve every straight edge of the compiled path; smoothing would change geometry. */
function CylinderEdge({ a, b, radius, color, capsule = false }: { a: Point3; b: Point3; radius: number; color: string; capsule?: boolean }) {
  const { length, midpoint, quaternion } = useMemo(() => {
    const start = new THREE.Vector3(...a), end = new THREE.Vector3(...b)
    const direction = end.clone().sub(start)
    return { length: direction.length(), midpoint: start.clone().add(end).multiplyScalar(0.5),
      quaternion: new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.normalize()) }
  }, [a, b])
  if (length < 1e-9) return null
  return <group>
    <mesh position={midpoint} quaternion={quaternion}>
      <cylinderGeometry args={[radius, radius, length, 24]} />
      <meshStandardMaterial color={color} roughness={0.65} />
    </mesh>
    {capsule && [a, b].map((p, i) => <mesh key={i} position={p}>
      <sphereGeometry args={[radius, 24, 16]} /><meshStandardMaterial color={color} roughness={0.65} />
    </mesh>)}
  </group>
}

function Pipe({ pipe, origin }: { pipe: ScenePipe; origin: Point3 }) {
  const path = (pipe.axis ?? pipe.points).map(p => toThree(p, origin))
  const mid = path[Math.floor(path.length / 2)]
  return <group>
    {pipe.solid ? path.slice(1).map((p, i) => <CylinderEdge key={i} a={path[i]} b={p} radius={pipe.outer! / 2000} color="#3b82f6" />)
      : <Line points={path} color="#60a5fa" lineWidth={1.7} dashed dashSize={0.6} gapSize={0.35} />}
    <Html position={mid} center style={{ pointerEvents: "none" }}>
      <div className="mt-10 w-max max-w-56 sm:max-w-80 rounded border border-border bg-background/95 px-2 py-1 text-xs text-foreground shadow-sm">{pipeLabel(pipe)}</div>
    </Html>
  </group>
}

export const Scene3D: React.FC<Scene3DProps> = ({ irData, isDark = true, showGrid = true, verticalExag = false, autoRotate = false, cameraResetKey = 0 }) => {
  const scene = useMemo(() => parseSceneGeometry(irData), [irData])
  const origin = scene.bounds.center
  const vscale = verticalExag ? 3 : 1
  const size = scene.bounds.max.map((v, i) => (v - scene.bounds.min[i]) * (i === 2 ? vscale : 1))
  const radius = Math.max(0.75, Math.hypot(...size) / 2)
  const resetKey = `${JSON.stringify(scene.bounds)}:${vscale}:${cameraResetKey}`
  const gridSize = Math.max(10, scene.bounds.span * 2)
  const gridElevation = (scene.bounds.min[2] - origin[2]) * vscale

  return <Canvas className="w-full h-full block" dpr={[1, 2]} camera={{ fov: 46 }}>
    <color attach="background" args={[isDark ? "#0b1220" : "#f8fafc"]} />
    <ambientLight intensity={0.9} />
    <directionalLight position={[radius, radius * 2, radius]} intensity={1.5} />
    <FitCamera radius={radius} resetKey={resetKey} />
    {showGrid && <Grid position={[0, gridElevation, 0]} args={[gridSize, gridSize]} cellSize={Math.max(1, 10 ** Math.floor(Math.log10(scene.bounds.span / 10)))}
      sectionSize={Math.max(5, 10 ** Math.floor(Math.log10(scene.bounds.span / 2)))}
      cellColor={isDark ? "#1e293b" : "#cbd5e1"} sectionColor={isDark ? "#334155" : "#94a3b8"} fadeDistance={gridSize * 2} />}
    <group scale={[1, vscale, 1]}>
      {scene.nodes.map((n, i) => {
        const point = toThree(n.point, origin)
        const top = toThree([n.point[0], n.point[1], n.ground ?? n.point[2]], origin)
        const bottom = toThree([n.point[0], n.point[1], n.invert ?? n.point[2]], origin)
        return <group key={`${n.id}-${i}`}>
          {n.ground !== null && n.invert !== null && <Line points={[bottom, top]} color={isDark ? "#cbd5e1" : "#475569"} lineWidth={1} dashed dashSize={0.2} gapSize={0.2} />}
          <Html position={point} center style={{ pointerEvents: "none" }}>
            <div className="h-2 w-2 rounded-full border-2 border-sky-500 bg-background" />
          </Html>
          <Html position={top} center style={{ pointerEvents: "none" }}>
            <div className="mb-12 w-max max-w-56 sm:max-w-80 rounded border border-border bg-background/95 px-2 py-1 text-xs text-foreground shadow-sm">
              {n.id} · 井位示意（尺寸未提供）
              <div className="font-mono text-muted-foreground">{n.ground !== null ? `地面 ${n.ground.toFixed(2)}m` : "地面高程未知"}{n.invert !== null ? ` · 内底 ${n.invert.toFixed(2)}m` : ""}</div>
            </div>
          </Html>
        </group>
      })}
      {scene.pipes.map((pipe, i) => <Pipe key={`${pipe.id}-${i}`} pipe={pipe} origin={origin} />)}
      {scene.obstacles.map((o, i) => {
        const position: Point3 = o.kind === "aabb" ? o.min.map((v, j) => (v + o.max[j]) / 2) as Point3 : o.start.map((v, j) => (v + o.end[j]) / 2) as Point3
        return <group key={`${o.id}-${i}`}>
          {o.kind === "aabb" ? <mesh position={toThree(position, origin)}>
            <boxGeometry args={[o.max[0] - o.min[0], o.max[2] - o.min[2], o.max[1] - o.min[1]]} />
            <meshStandardMaterial color="#d97706" transparent opacity={0.45} depthWrite={false} />
          </mesh> : <CylinderEdge a={toThree(o.start, origin)} b={toThree(o.end, origin)} radius={o.outer / 2000} color="#d97706" capsule />}
          <Html position={toThree(position, origin)} center style={{ pointerEvents: "none" }}>
            <div className="mb-16 w-max max-w-56 sm:max-w-80 rounded border border-amber-600/50 bg-background/95 px-2 py-1 text-xs text-amber-700 dark:text-amber-300">{o.id} · {o.kind === "aabb" ? "障碍物包围盒" : `既有管 OD ${o.outer}mm（胶囊体）`}</div>
          </Html>
        </group>
      })}
    </group>
    <OrbitControls key={resetKey} target={[0, 0, 0]} enableDamping dampingFactor={0.08} autoRotate={autoRotate} autoRotateSpeed={0.8} minDistance={radius * 0.1} maxDistance={radius * 30} />
  </Canvas>
}
