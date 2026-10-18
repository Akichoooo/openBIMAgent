import { beforeEach, afterEach, describe, expect, it, vi } from "vitest"
import "@testing-library/jest-dom/vitest"
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react"
import { MunicipalWorkbench } from "./MunicipalWorkbench"
import { api, type MunicipalState } from "@/services/api"

vi.mock("@/services/api", () => ({ api: { getMunicipal: vi.fn(), saveMunicipal: vi.fn(), municipalAction: vi.fn(), getMunicipalVisionConfig: vi.fn(), retryIfc: vi.fn(), municipalIfcDownloadUrl: vi.fn(() => "/api/v1/municipal/s/delivery/ifc") } }))

function saved(): MunicipalState {
  return { session_id: "s", revision: 2, requirement_revision: 1, candidate_sha256: "digest", review_token: "review",
    candidate: { system_name: "任务", coordinate_reference: {}, nodes: [{ node_id: "MH-1", node_type: "manhole", x_m: 0, y_m: 0, ground_elevation_m: 11, invert_anchor_m: 9 }],
      segments: [{ segment_id: "P-1", start_node_id: "MH-1", end_node_id: "MH-1", diameter_mm: 300, design_slope: 0.003, inner_diameter_mm: 300, outer_diameter_mm: 360, surface_context: "driveway" }], collision_context: null },
    validation: { executable: true, compiled_ir: { nodes: [], segments: [] }, issues: [] },
    locks: [{ path: "/segments/0/design_slope", value: 0.003 }], proposal: null, confirmation: { revision: 2 }, visual: null, delivery: {}, history: [] }
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getMunicipalVisionConfig).mockResolvedValue({
    status: "ready", profile: "test", configured_model: "vision-model", effective_model: "vision-model",
    provider: "test-provider", endpoint: "https://example.invalid/v1/chat/completions",
    credential_env: "TEST_VISION_KEY", credential_present: true, configured_vision: true,
    retry_max: 1, timeout_s: 60, openscad: { path: "openscad", available: true },
  })
})
afterEach(cleanup)

describe("municipal editing workflow", () => {
  it("starts with missing physical dimensions, keeps unknown obstacle survey, and cannot export", async () => {
    vi.mocked(api.getMunicipal).mockResolvedValue({ state: null })
    render(<MunicipalWorkbench sessionId="s" onModelChange={vi.fn()} />)
    const outer = await screen.findByLabelText("P-1 实际外径 (mm)")
    expect(outer).toHaveValue(null)
    expect(screen.getByText(/我已核对当前检查范围/).closest("label")?.querySelector("input")).not.toBeChecked()
    expect(screen.queryByRole("button", { name: "导出至 Vectorworks" })).not.toBeInTheDocument()
  })

  it("locks numeric inputs, disables stale delivery, and preserves failed save edits", async () => {
    vi.mocked(api.getMunicipal).mockResolvedValue({ state: saved() })
    vi.mocked(api.saveMunicipal).mockRejectedValue(new Error("服务器拒绝了旧版本"))
    render(<MunicipalWorkbench sessionId="s" onModelChange={vi.fn()} />)
    expect(await screen.findByLabelText("P-1 坡度 (m/m)")).toBeDisabled()
    fireEvent.change(screen.getByLabelText("MH-1 X 坐标 (m)"), { target: { value: "23" } })
    expect(screen.getByRole("button", { name: "导出至 Vectorworks" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "保存并检查" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("服务器拒绝了旧版本")
    expect(screen.getByLabelText("MH-1 X 坐标 (m)")).toHaveValue(23)
    expect(api.saveMunicipal).toHaveBeenCalledWith("s", expect.objectContaining({ base_revision: 2, candidate_sha256: "digest", source: "correction" }))
  })

  it("requires an explicit host confirmation and sends the bound revision", async () => {
    vi.mocked(api.getMunicipal).mockResolvedValue({ state: saved() })
    vi.mocked(api.municipalAction).mockResolvedValue({ state: { ...saved(), delivery: { status: "failed", error: "runner unavailable" } } })
    render(<MunicipalWorkbench sessionId="s" onModelChange={vi.fn()} />)
    fireEvent.click(await screen.findByRole("button", { name: "导出至 Vectorworks" }))
    expect(api.municipalAction).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: "确认执行" }))
    await waitFor(() => expect(api.municipalAction).toHaveBeenCalledWith("s", "export", { base_revision: 2, candidate_sha256: "digest", host: "vectorworks", confirm: true }))
    expect(await screen.findByText(/runner unavailable/)).toBeInTheDocument()
  })

  it("requires explicit retry of the latest failed host attempt even after another host ran", async () => {
    const state = saved()
    state.history = [{ action: "export", host: "vectorworks", revision: 2, status: "failed", execution_id: "failed-attempt" }]
    state.delivery = { host: "blender", revision: 2, status: "partial" }
    vi.mocked(api.getMunicipal).mockResolvedValue({ state })
    vi.mocked(api.municipalAction).mockResolvedValue({ state })
    render(<MunicipalWorkbench sessionId="s" onModelChange={vi.fn()} />)
    fireEvent.click(await screen.findByRole("button", { name: "导出至 Vectorworks" }))
    expect(api.municipalAction).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: "已核对宿主，重新执行" }))
    await waitFor(() => expect(api.municipalAction).toHaveBeenCalledWith("s", "export", expect.objectContaining({ host: "vectorworks", retry_of: "failed-attempt" })))
  })

  it("shows entity, semantic and geometry readback separately for a partial Vectorworks delivery", async () => {
    const state = saved()
    state.delivery = {
      status: "partial", host_status: "completed",
      receipt: {
        output_path: "D:/authorized/case.vwx", confirmed_objects: 6,
        geometry_status: "failed", semantic_comparison_report: { status: "PASS" },
        geometry_snapshot: { objects: [{
          stable_id: "P-1", status: "failed",
          measured: { inner_diameter_mm: 300, outer_diameter_mm: 370, length_m: 10, vertex_count: 96 },
          failures: ["outer diameter differs from IR"],
        }] },
        ifc: {
          status: "failed",
          generation: { status: "generated", ifc_path: "D:/authorized/ifc/case.ifc", ifc_sha256: "a".repeat(64), ifc_bytes: 15254 },
          checks: {
            ifc_schema: { status: "pass", schema: "IFC4X3" },
            ids_semantic: { status: "pass", findings: 80 },
            geometry: { status: "fail", checked_segments: 2, segments: [], failures: ["P-1 hollow_profile: missing inner surface"] },
            domain_rules: { status: "review_required", production_verification: "review_required" },
            status: "fail",
          },
          affected_ids: ["P-1"],
          error: "P-1：缺少空腔",
        },
      },
    }
    vi.mocked(api.getMunicipal).mockResolvedValue({ state })
    render(<MunicipalWorkbench sessionId="s" onModelChange={vi.fn()} />)
    expect(await screen.findByText(/宿主结果：部分完成/)).toBeInTheDocument()
    expect(screen.getByText(/语义读回：通过/)).toBeInTheDocument()
    expect(screen.getAllByText(/几何读回：未通过/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/P-1/).length).toBeGreaterThan(0)
    expect(screen.getByText(/outer diameter differs from IR/)).toBeInTheDocument()
    expect(screen.getByText(/D:\/authorized\/case\.vwx/)).toBeInTheDocument()
    expect(screen.getByText(/IFC 交付（IR 派生，非 Vectorworks 原生导出）/)).toBeInTheDocument()
    expect(screen.getByText(/生成：已生成/)).toBeInTheDocument()
    expect(screen.getByText(/模式检查：通过（IFC4X3）/)).toBeInTheDocument()
    expect(screen.getByText(/IDS\/语义：通过（80 项）/)).toBeInTheDocument()
    expect(screen.getByText(/下载 IFC/)).toBeInTheDocument()
    expect(screen.getByText(/涉及构件：P-1/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "重试 IFC" })).toBeInTheDocument()
  })

  it("shows dimension readback separately from topology limitations for the VW mesh runner", async () => {
    const state = saved()
    state.delivery = {
      status: "partial", host_status: "completed", geometry_status: "dimension_verified_topology_unverified",
      receipt: {
        output_path: "D:/authorized/case.vwx", confirmed_objects: 6,
        geometry_status: "dimension_verified_topology_unverified", semantic_comparison_report: { status: "PASS" },
        geometry_snapshot: { topology_status: "unverified", objects: [{
          stable_id: "P-1", status: "dimension_verified_topology_unverified",
          dimension_status: "verified", topology_status: "unverified",
          measured: { inner_diameter_mm: 300, outer_diameter_mm: 360, length_m: 9.9, vertex_count: 96 },
          failures: [],
        }] },
        geometry_caveats: ["闭合性（面数/重复面/朝向/流形边）无法在 Vectorworks 上读回验证。"],
        ifc: { scope: "IFC 实体几何未完成" },
      },
    }
    vi.mocked(api.getMunicipal).mockResolvedValue({ state })
    render(<MunicipalWorkbench sessionId="s" onModelChange={vi.fn()} />)
    expect(await screen.findByText(/几何读回：尺寸通过 · 闭合性未验证/)).toBeInTheDocument()
    expect(screen.getByText(/尺寸 已通过/)).toBeInTheDocument()
    expect(screen.getByText(/闭合性 未验证（宿主未返回面\/边数据）/)).toBeInTheDocument()
    expect(screen.getByText(/无法在 Vectorworks 上读回验证/)).toBeInTheDocument()
  })

  it("keeps the three views and points to the existing credential entry when the VLM is not called", async () => {
    const state = saved()
    state.visual = {
      status: "unavailable", failure_code: "credentials_missing", revision: 2, vlm_called: false,
      summary: "三视图已生成，但视觉模型未调用：凭据缺失。",
      preview: { revision: 2, images: { iso: "/iso.png", front: "/front.png", top: "/top.png" } },
      issues: [], patches: [],
      vision_config: { credential_env: "SENSENOVA_API_KEY", credential_present: false },
    }
    vi.mocked(api.getMunicipal).mockResolvedValue({ state })
    render(<MunicipalWorkbench sessionId="s" onModelChange={vi.fn()} />)
    expect(await screen.findByText(/凭据缺失/)).toBeInTheDocument()
    expect(screen.getAllByText(/SENSENOVA_API_KEY/).length).toBeGreaterThan(0)
    expect(screen.getByText(/设置 → 模型与供应商/)).toBeInTheDocument()
    expect(screen.getByAltText("轴测图，版本 2")).toBeInTheDocument()
  })

  it.each([
    ["timeout", /超过有界时限/],
    ["rate_limited", /429 限流/],
    ["response_invalid", /不符合结构化契约/],
    ["model_not_vision_capable", /critic_scad 或其降级链/],
    ["render_failed", /OpenSCAD 路径/],
  ])("gives a distinct next step for %s", async (failureCode, message) => {
    const state = saved()
    state.visual = {
      status: "unavailable", failure_code: failureCode, revision: 2, vlm_called: true,
      summary: "视觉检查未完成。", issues: [], patches: [],
      preview: failureCode === "render_failed" ? null : { revision: 2, images: { iso: "/iso.png", front: "/front.png", top: "/top.png" } },
    }
    vi.mocked(api.getMunicipal).mockResolvedValue({ state })
    render(<MunicipalWorkbench sessionId="s" onModelChange={vi.fn()} />)
    expect(await screen.findByText(message)).toBeInTheDocument()
  })

  it("shows duration and transport diagnostics even when usage is missing", async () => {
    const state = saved()
    state.visual = {
      status: "unavailable", failure_code: "timeout", revision: 2, vlm_called: true,
      duration_ms: 1234, vlm_duration_ms: 1100, render_duration_ms: 100, usage: null,
      summary: "视觉模型调用超过有界时限。", issues: [], patches: [],
      provider: "fallback-provider", endpoint: "https://fallback.invalid/v1/chat/completions",
      model_attempted: "fallback-model", model: "service-model-id",
      diagnostics: {
        deadline_s: 60, business_outcome: "aborted_partial",
        request: { image_count: 3, view_order: ["front", "iso", "top"], text_chars: 100, context_chars: 300, response_schema_chars: 50, reasoning_level: "low",
          images: [{ view_id: "iso", width_px: 1024, height_px: 768, encoded_bytes: 1234 }] },
        attempts: [{ model: "fallback-model", provider: "fallback-provider", endpoint: "https://fallback.invalid/v1/chat/completions",
          retry_index: 0, request_sent: true, http_status: 200, headers_at_ms: 100, first_event_at_ms: 200,
          first_reasoning_at_ms: 300, ended_at_ms: 1200, content_chars: 0, reasoning_chars: 42,
          finish_reason: null, usage_present: false, aborted: true, cancel_reason: "deadline",
          close_requested: true, worker_exited: true }],
      },
    }
    vi.mocked(api.getMunicipal).mockResolvedValue({ state })
    render(<MunicipalWorkbench sessionId="s" onModelChange={vi.fn()} />)
    expect(await screen.findByText(/总 1234 ms/)).toBeInTheDocument()
    expect(screen.getByText(/视觉调用 1100 ms/)).toBeInTheDocument()
    expect(screen.getAllByText(/超过有界时限/).length).toBeGreaterThan(0)
    expect(screen.getByText(/HTTP 200/)).toBeInTheDocument()
    expect(screen.getByText(/reasoning 42 字符/)).toBeInTheDocument()
    expect(screen.getByText(/后台线程退出：是/)).toBeInTheDocument()
    expect(screen.getAllByText(/fallback-provider/).length).toBeGreaterThan(0)
  })

  it("shows locatable issues and limited patches, then sends the visual proposal for review", async () => {
    const state = saved()
    state.locks = []
    state.validation.issues = [{ rule_id: "MU-DRAIN-004", status: "fail", severity: "error", object_ids: ["P-1"], detail: "slope fail" }]
    state.visual = {
      status: "review_required", revision: 2, vlm_called: true, model: "actual-vision", model_effective: "configured-vision",
      provider: "test-provider", usage: { total_tokens: 123, prompt_tokens: 100, completion_tokens: 23 },
      duration_ms: 1200, vlm_duration_ms: 900,
      preview: { revision: 2, images: { iso: "/iso.png", front: "/front.png", top: "/top.png" } },
      summary: "Two observations need review.",
      issues: [{
        observation_kind: "locatable_suspicion", evidence_source: "visual_observation",
        object_ids: ["P-1"], constraint_ids: ["MU-DRAIN-004"],
        parameter_paths: ["/segments/0/design_slope"], view_id: "top", severity: "warning",
        evidence: "Branch label overlaps the junction.", description: "Junction geometry needs review.",
        suggestion: "Compare with the source drawing.", uncertainty: "Labels can hide the junction.",
      }],
      patches: [{ path: "/segments/0/design_slope", old_value: 0.003, value: 0.004, reason: "Review proposal only." }],
    }
    vi.mocked(api.getMunicipal).mockResolvedValue({ state })
    vi.mocked(api.municipalAction).mockResolvedValue({ state })
    render(<MunicipalWorkbench sessionId="s" onModelChange={vi.fn()} />)
    expect(await screen.findByText("可定位视觉疑点")).toBeInTheDocument()
    expect(screen.getByText(/图像观察/)).toBeInTheDocument()
    expect(screen.getAllByText(/MU-DRAIN-004/).length).toBeGreaterThan(0)
    expect(screen.getByText(/不能认定独立视觉发现/)).toBeInTheDocument()
    expect(screen.getByText(/0\.003 → 0\.004/)).toBeInTheDocument()
    expect(screen.getByText(/必须再次视觉检查/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "审阅视觉修改建议" }))
    await waitFor(() => expect(api.municipalAction).toHaveBeenCalledWith("s", "propose", expect.objectContaining({ source: "visual", base_revision: 2, candidate_sha256: "digest" })))
  })
})
