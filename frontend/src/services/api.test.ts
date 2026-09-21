import { describe, it, expect, vi, beforeEach } from "vitest"
import { api, buildSessionEventsStreamUrl, eventSuggestsIrUpdate, deriveRunActivity, deriveThreadView, deriveRunWrapUp, extractSubagentRequestId, isApprovalSignal, SessionEvent } from "./api"

// 构造一个 SSE ReadableStream 响应（模拟后端 /chat/stream 的 event:/data: 帧）
function sseResponse(frames: string[]): Response {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const f of frames) controller.enqueue(encoder.encode(f))
      controller.close()
    },
  })
  return { ok: true, status: 200, statusText: "OK", body, text: async () => "" } as unknown as Response
}

describe("api.streamChat — SSE 逐帧解析（B3）", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("按帧解析 meta/reasoning/delta/done 并回调", async () => {
    const frames = [
      'event: meta\ndata: {"model":"sensenova-6.8-flash-lite"}\n\n',
      'event: reasoning\ndata: {"text":"思考中"}\n\n',
      'event: delta\ndata: {"text":"你"}\n\n',
      'event: delta\ndata: {"text":"好"}\n\n',
      'event: done\ndata: {"persisted":true}\n\n',
    ]
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(frames)))
    const deltas: string[] = []
    const reasons: string[] = []
    let meta: { model?: string } | null = null
    let done = false
    await api.streamChat(
      "hi",
      "sess-1",
      {
        onMeta: (m) => {
          meta = m
        },
        onDelta: (t) => deltas.push(t),
        onReasoning: (t) => reasons.push(t),
        onDone: () => {
          done = true
        },
      }
    )
    expect((meta as { model?: string } | null)?.model).toBe("sensenova-6.8-flash-lite")
    expect(reasons).toEqual(["思考中"])
    expect(deltas).toEqual(["你", "好"])
    expect(done).toBe(true)
  })

  it("跨 chunk 分帧：不完整帧留待下次拼接", async () => {
    const encoder = new TextEncoder()
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(encoder.encode('event: delta\ndata: {"te'))
        c.enqueue(encoder.encode('xt":"分片"}\n\n'))
        c.close()
      },
    })
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 200, body, text: async () => "" } as unknown as Response)
    )
    const deltas: string[] = []
    await api.streamChat("x", "s", { onDelta: (t) => deltas.push(t) })
    expect(deltas).toEqual(["分片"])
  })

  it("非 2xx（如 422 未配置 LLM）抛出后端 error 文案", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        statusText: "Unprocessable",
        body: null,
        text: async () => JSON.stringify({ error: "未配置基线 LLM" }),
      } as unknown as Response)
    )
    await expect(api.streamChat("x", "s", {})).rejects.toThrow("未配置基线 LLM")
  })
})

describe("buildSessionEventsStreamUrl — EventSource 鉴权（?token=）", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    delete window.__WB_TOKEN
    localStorage.clear()
  })

  it("无令牌（vite dev）时返回裸地址", () => {
    expect(buildSessionEventsStreamUrl("sess-1")).toBe("/api/v1/sessions/sess-1/events/stream")
  })

  it("window.__WB_TOKEN 存在时追加 ?token=", () => {
    window.__WB_TOKEN = "tok-abc"
    expect(buildSessionEventsStreamUrl("sess-1")).toBe(
      "/api/v1/sessions/sess-1/events/stream?token=tok-abc"
    )
  })

  it("window.__WB_TOKEN 缺失时回退 localStorage 缓存令牌", () => {
    localStorage.setItem("openbimagent_token", "tok-cached")
    expect(buildSessionEventsStreamUrl("sess-1")).toBe(
      "/api/v1/sessions/sess-1/events/stream?token=tok-cached"
    )
  })

  it("令牌含特殊字符时正确编码", () => {
    window.__WB_TOKEN = "a+b/c="
    expect(buildSessionEventsStreamUrl("sess-1")).toBe(
      "/api/v1/sessions/sess-1/events/stream?token=a%2Bb%2Fc%3D"
    )
  })
})

describe("eventSuggestsIrUpdate — IR 产物更新信号", () => {
  const ev = (type: string, payload: any): SessionEvent => ({
    id: "e1",
    type,
    created_at: "2026-09-21T00:00:00Z",
    payload,
  })

  it("payload 直接携带 ir / result_ir", () => {
    expect(eventSuggestsIrUpdate(ev("message", { role: "assistant", ir: { nodes: [] } }))).toBe(true)
    expect(eventSuggestsIrUpdate(ev("message", { role: "assistant", result_ir: {} }))).toBe(true)
  })

  it("artifact_committed 路径指向 compiled_utility_ir.json", () => {
    expect(
      eventSuggestsIrUpdate(
        ev("custom", {
          customType: "artifact_committed",
          artifact: { kind: "ir", path: "/out/runs/r1/compiled_utility_ir.json", sha256: "x" },
        })
      )
    ).toBe(true)
  })

  it("tool_call 结果视图提及 compiled_utility_ir", () => {
    expect(
      eventSuggestsIrUpdate(
        ev("tool_call", { toolName: "geometry_solver", result_llm_view: "wrote compiled_utility_ir.json ok" })
      )
    ).toBe(true)
    expect(
      eventSuggestsIrUpdate(
        ev("tool_call", { toolName: "geometry_solver", result_ui_view: { file: "compiled_utility_ir.json" } })
      )
    ).toBe(true)
  })

  it("无关事件不误报", () => {
    expect(eventSuggestsIrUpdate(ev("message", { role: "user", content: "放样雨水管" }))).toBe(false)
    expect(eventSuggestsIrUpdate(ev("tool_call", { toolName: "bash", args_summary: "ls -la" }))).toBe(false)
    expect(
      eventSuggestsIrUpdate(
        ev("custom", { customType: "artifact_committed", artifact: { kind: "mesh", path: "/out/scene.glb" } })
      )
    ).toBe(false)
    expect(eventSuggestsIrUpdate(ev("custom", { customType: "score", rubric_scores: {} }))).toBe(false)
  })
})

describe("deriveRunActivity — 状态行活动推导", () => {
  const ev = (id: string, type: string, payload: any): SessionEvent => ({
    id,
    type,
    created_at: "2026-09-21T00:00:00Z",
    payload,
  })

  it("取尾部最近 tool_call 的角色与工具名", () => {
    const events = [
      ev("1", "message", { role: "user", content: "hi" }),
      ev("2", "tool_call", { toolName: "bash", agent_role: "modeler" }),
      ev("3", "tool_call", { toolName: "geometry_solver", agent_role: "planner" }),
    ]
    expect(deriveRunActivity(events)).toEqual({
      role: "planner",
      tool: "geometry_solver",
      at: "2026-09-21T00:00:00Z",
    })
  })

  it("无 tool_call 时回退到最近 assistant 消息", () => {
    const events = [ev("1", "message", { role: "assistant", content: "ok" })]
    expect(deriveRunActivity(events).role).toBe("assistant")
    expect(deriveRunActivity(events).tool).toBeUndefined()
  })

  it("agent/batch 字段亦可作为角色来源，空事件流返回空对象", () => {
    expect(deriveRunActivity([ev("1", "tool_call", { toolName: "t", agent: "deliver" })]).role).toBe("deliver")
    expect(deriveRunActivity([ev("1", "tool_call", { toolName: "t", batch: "critic_scad" })]).role).toBe("critic_scad")
    expect(deriveRunActivity([])).toEqual({})
  })
})

describe("子代理生命周期：关联、终态推进、审批信号、收尾统计", () => {
  const ev = (id: string, type: string, payload: any, created_at = "2026-09-21T10:00:00Z"): SessionEvent => ({
    id,
    type,
    created_at,
    payload,
  })
  const tc = (id: string, toolCallId: string, payload: any) =>
    ev(id, "tool_call", { toolCallId, ...payload })

  it("extractSubagentRequestId：ui_view 结构化优先，文本兜底", () => {
    expect(extractSubagentRequestId({ result_ui_view: { request_id: "r-1" } })).toBe("r-1")
    expect(extractSubagentRequestId({ result_llm_view: "queued: request_id=r-2; agent_id=a" })).toBe("r-2")
    expect(extractSubagentRequestId({ args_summary: "role=modeler" })).toBeUndefined()
    expect(extractSubagentRequestId(null)).toBeUndefined()
  })

  it("isApprovalSignal：approval_requested/decided 命中，其余不命中", () => {
    expect(isApprovalSignal(ev("1", "custom", { customType: "approval_requested" }))).toBe(true)
    expect(isApprovalSignal(ev("2", "custom", { customType: "approval_decided" }))).toBe(true)
    expect(isApprovalSignal(ev("3", "custom", { customType: "subagent_completed" }))).toBe(false)
    expect(isApprovalSignal(ev("4", "tool_call", { toolName: "bash" }))).toBe(false)
  })

  it("deriveThreadView：call/result 按 toolCallId 配对；未配对为 pending", () => {
    const items = deriveThreadView([
      tc("1", "tc1", { toolName: "bash", phase: "call", args_summary: "ls" }),
      tc("2", "tc1", { toolName: "bash", phase: "result", status: "ok", result_llm_view: "done" }),
      tc("3", "tc2", { toolName: "write", phase: "call", args_summary: "w" }),
    ])
    const tools = items.filter((i) => i.kind === "tool")
    expect(tools).toHaveLength(2) // 两条 tc1 事件合成一张卡
    expect((tools[0] as any).card.pending).toBe(false)
    expect((tools[1] as any).card.pending).toBe(true)
  })

  it("deriveThreadView：request_id 对上生命周期 → 卡翻终态徽章 + 交付标记，不再渲染内联标记", () => {
    const items = deriveThreadView([
      tc("1", "tc1", { toolName: "subagent", phase: "call", args_summary: "role=modeler task=放样" }),
      ev("2", "custom", { customType: "subagent_created", request_id: "r1", role: "modeler" }),
      tc("3", "tc1", {
        toolName: "subagent",
        phase: "result",
        status: "ok",
        result_llm_view: "queued: request_id=r1",
        result_ui_view: { request_id: "r1" },
      }),
      ev("4", "custom", { customType: "subagent_completed", request_id: "r1", status: "completed", receipt_id: "rc1" }),
      ev("5", "custom", { customType: "delivery_receipt", request_id: "r1", status: "completed", manifest_path: "/m.json" }),
    ])
    const tools = items.filter((i) => i.kind === "tool")
    const markers = items.filter((i) => i.kind === "marker")
    expect(tools).toHaveLength(1)
    const card = (tools[0] as any).card
    expect(card.requestId).toBe("r1")
    expect(card.terminalStatus).toBe("completed")
    expect(card.receipt).toBe(true)
    expect(card.role).toBe("modeler")
    expect(markers).toHaveLength(0)
  })

  it("deriveThreadView：对不上工具卡的终态渲染内联标记（角色来自 created），receipt 折叠进终态标记", () => {
    const items = deriveThreadView([
      ev("1", "custom", { customType: "subagent_created", request_id: "r9", role: "researcher" }),
      ev("2", "custom", { customType: "subagent_failed", request_id: "r9", error: { message: "boom" } }),
      ev("3", "custom", { customType: "delivery_receipt", request_id: "r9" }),
    ])
    const markers = items.filter((i) => i.kind === "marker")
    expect(markers).toHaveLength(1) // delivery 折叠进 failed 标记
    const mk = (markers[0] as any).marker
    expect(mk.status).toBe("failed")
    expect(mk.role).toBe("researcher")
    expect(mk.error).toBe("boom")
  })

  it("deriveRunActivity：子代理终态后活动行不再停在它身上", () => {
    const events = [
      tc("1", "tc1", { toolName: "subagent", phase: "result", status: "ok", result_ui_view: { request_id: "r1" }, agent_role: "modeler" }),
      ev("2", "custom", { customType: "subagent_completed", request_id: "r1" }),
    ]
    // 全部工具活动均已终态 → 无当前活动
    expect(deriveRunActivity(events)).toEqual({})
    // 终态后又有新的工具调用 → 推进到新的活动
    const events2 = [
      ...events,
      tc("3", "tc2", { toolName: "bash", phase: "call", args_summary: "ls" }),
    ]
    expect(deriveRunActivity(events2).tool).toBe("bash")
  })

  it("deriveRunWrapUp：统计窗口内已配对 result 的工具调用次数与用时", () => {
    const since = Date.parse("2026-09-21T10:00:00Z")
    const until = since + 65000
    const stats = deriveRunWrapUp(
      [
        ev("0", "tool_call", { toolCallId: "tc0", toolName: "bash", phase: "result", status: "ok" }, "2026-09-21T09:59:30Z"), // 早于窗口
        ev("1", "tool_call", { toolCallId: "tc1", toolName: "bash", phase: "result", status: "ok" }, "2026-09-21T10:00:30Z"),
        ev("2", "tool_call", { toolCallId: "tc2", toolName: "read", phase: "call" }, "2026-09-21T10:00:40Z"), // 未配对不计
        ev("3", "tool_call", { toolCallId: "tc3", toolName: "write", phase: "result", status: "ok" }, "2026-09-21T10:00:50Z"),
      ],
      since,
      until
    )
    expect(stats.toolCalls).toBe(2)
    expect(stats.seconds).toBe(65)
  })
})
