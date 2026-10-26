import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { streamChat } from './chat'

function sseResponse(frames: string[]) {
  return new Response(frames.join('\n\n'), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

describe('approval frames', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('surfaces approval_required instead of dropping it on the default branch', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      sseResponse([
        'event: approval_required\ndata: {"approval_id":"ap-1","code":"APPROVAL_REQUIRED"}',
        'event: run\ndata: {"run_id":"run-1","status":"waiting_approval"}',
      ]),
    )
    const required = vi.fn()
    const runs = vi.fn()

    await streamChat('session-1', 'hi', { onApprovalRequired: required, onRun: runs })

    expect(required).toHaveBeenCalledWith({
      approval_id: 'ap-1',
      code: 'APPROVAL_REQUIRED',
      kind: undefined,
      message: undefined,
    })
    expect(runs).toHaveBeenCalledWith({ run_id: 'run-1', status: 'waiting_approval', error_code: undefined })
  })

  it('ignores approval frames without an id', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      sseResponse(['event: approval_required\ndata: {"code":"APPROVAL_REQUIRED"}']),
    )
    const required = vi.fn()

    await streamChat('session-1', 'hi', { onApprovalRequired: required })

    // 没有 id 就点不了批准，渲染一张死卡只会误导用户
    expect(required).not.toHaveBeenCalled()
  })

  it('reports approval_resumed so the UI can announce it', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      sseResponse(['event: approval_resumed\ndata: {"approval_id":"ap-1","run_id":"run-1"}']),
    )
    const resumed = vi.fn()

    await streamChat('session-1', 'hi', { onApprovalResumed: resumed })

    expect(resumed).toHaveBeenCalledWith({ approval_id: 'ap-1' })
  })
})

describe('streamChat error responses', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('uses detail.code when the API returns an object detail', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: { code: 'HOUSEHOLD_QUOTA_NOT_CONFIGURED' } }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(streamChat('session-1', 'hello', {})).rejects.toThrow('HOUSEHOLD_QUOTA_NOT_CONFIGURED')
  })

  it('preserves a string detail', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: '消息内容不能为空' }), {
        status: 422,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(streamChat('session-1', 'hello', {})).rejects.toThrow('消息内容不能为空')
  })

  it('keeps FastAPI validation errors readable', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: [{ loc: ['body', 'message'], msg: 'Field required', type: 'missing' }] }), {
        status: 422,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(streamChat('session-1', 'hello', {})).rejects.toThrow('Field required')
  })

  it('falls back to the response status for an unknown body', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error: { reason: 'unknown' } }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(streamChat('session-1', 'hello', {})).rejects.toThrow('请求失败（503）')
  })
})
