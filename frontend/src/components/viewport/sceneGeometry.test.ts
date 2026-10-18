import { describe, expect, it } from "vitest"
import { parseSceneGeometry } from "./sceneGeometry"

const point = (x: number, y: number, z: number) => ({ x_m: x, y_m: y, z_m: z })
const pipe = (extra = {}) => ({ segment_id: "s1", diameter_mm: 300, centerline_reference: "invert", inner_diameter_mm: 300, outer_diameter_mm: 360,
  centerline: [point(0, 0, 100), point(10, 2, 99.97)], ...extra })
const scene = (segment = pipe()) => parseSceneGeometry({ protocol_version: "1.1", segments: [segment] })

describe("project geometry shared by all viewport modes", () => {
  it("raises inner invert by the inner radius and uses the explicit outer diameter", () => {
    const s = scene().pipes[0]
    expect(s.axis).toEqual([[0, 0, 100.15], [10, 2, 100.12]])
    expect(s.outer).toBe(360)
    expect(s.solid).toBe(true)
    expect(s.invert).toEqual([[0, 0, 100], [10, 2, 99.97]])
  })
  it("keeps an explicit axis unchanged and derives the inside bottom", () => {
    const s = scene(pipe({ centerline_reference: "axis" })).pipes[0]
    expect(s.axis).toEqual([[0, 0, 100], [10, 2, 99.97]])
    expect(s.invert?.[0][2]).toBeCloseTo(99.85)
  })
  it("derives physical diameters from wall thickness only", () => {
    expect(scene(pipe({ outer_diameter_mm: undefined, wall_thickness_mm: 30 })).pipes[0].outer).toBe(360)
    expect(scene(pipe({ inner_diameter_mm: undefined, wall_thickness_mm: 30 })).pipes[0].axis?.[0][2]).toBe(100.15)
  })
  it("never turns nominal DN into a radius or treats unknown invert as an axis", () => {
    const s = scene(pipe({ inner_diameter_mm: undefined, outer_diameter_mm: undefined })).pipes[0]
    expect(s.solid).toBe(false)
    expect(s.axis).toBeNull()
    expect(s.outer).toBeNull()
  })
  it("renders an explicit axis with known OD even when inside diameter is unknown", () => {
    const s = scene(pipe({ centerline_reference: "axis", inner_diameter_mm: undefined })).pipes[0]
    expect(s.solid).toBe(true)
    expect(s.invert).toBeNull()
  })
  it("leaves legacy semantics schematic even when dimensions happen to be present", () => {
    const s = parseSceneGeometry({ protocol_version: "1.0", segments: [pipe({ centerline_reference: undefined })] }).pipes[0]
    expect(s.axis).toBeNull()
    expect(s.solid).toBe(false)
  })
  it("does not create missing segments, coordinates, or healed paths", () => {
    const result = parseSceneGeometry({ nodes: [{ node_id: "a", position: point(1, 2, 3) }, { node_id: "b", position: point(4, 5, 6) }], healed_bend: { pts: [[0, 0], [9, 9]] } })
    expect(result.pipes).toEqual([])
    expect(result.nodes[0].ground).toBeNull()
    expect(parseSceneGeometry({ nodes: [{ node_id: "missing" }] }).nodes).toEqual([])
    expect(scene(pipe({ centerline: [point(0, 0, 100), { x_m: 2 }, point(10, 2, 99.97)] })).pipes).toEqual([])
  })
  it("retains every bend without smoothing and fits real high elevations", () => {
    const s = scene(pipe({ centerline: [point(0, 0, 100), point(3, 8, 99.99), point(10, 2, 99.97)] }))
    expect(s.pipes[0].axis).toHaveLength(3)
    expect(s.pipes[0].axis?.[1]).toEqual([3, 8, 100.14])
    expect(s.bounds.center[2]).toBeGreaterThan(99)
    expect(s.bounds.min[2]).toBeCloseTo(99.94)
  })
  it("does not draw inconsistent dimensions as a solid", () => {
    const result = scene(pipe({ wall_thickness_mm: 10 }))
    expect(result.pipes[0].solid).toBe(false)
    expect(result.notices).toHaveLength(1)
  })
  it("places saved obstacle AABBs and pipe capsules at true XYZ", () => {
    const result = parseSceneGeometry({ preview_collision_context: { obstacles: [
      { obstacle_id: "box", kind: "aabb", min_corner: point(50, -3, 200), max_corner: point(52, 7, 204) },
      { obstacle_id: "gas", kind: "existing_pipe", start_center: point(1, 2, 101), end_center: point(3, 4, 102), outer_diameter_mm: 400 },
    ] } })
    expect(result.obstacles).toEqual([
      { id: "box", kind: "aabb", min: [50, -3, 200], max: [52, 7, 204] },
      { id: "gas", kind: "existing_pipe", start: [1, 2, 101], end: [3, 4, 102], outer: 400 },
    ])
    expect(result.bounds.max).toEqual([52, 7, 204])
  })
  it("reports an obstacle with missing elevation instead of inventing one", () => {
    const result = parseSceneGeometry({ obstacles: [{ id: "unknown", x: 30, y: 1, w: 4, h: 2 }] })
    expect(result.obstacles).toEqual([])
    expect(result.notices[0]).toContain("未绘制")
  })
})
