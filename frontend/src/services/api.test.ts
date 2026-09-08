import { describe, it, expect, vi, beforeEach } from "vitest"
import { api } from "./api"

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
