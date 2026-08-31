import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { streamChat } from './chat'

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
