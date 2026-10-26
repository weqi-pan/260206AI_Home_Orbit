import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const postMock = vi.fn()
vi.mock('axios', () => {
  const instance = {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  }
  const axios = {
    create: () => instance,
    post: (...args: unknown[]) => postMock(...args),
  }
  return { default: axios }
})

const { extractErrorMessage, refreshAccessToken } = await import('./http')
const { useAuthStore } = await import('../stores/auth')

describe('extractErrorMessage', () => {
  it('优先展示后端给用户看的中文 message', () => {
    const body = { detail: { code: 'HOUSEHOLD_NOT_FOUND', message: '家庭不存在' } }

    expect(extractErrorMessage(body, '兜底')).toBe('家庭不存在')
  })

  it('没有 message 时退回稳定错误码', () => {
    expect(extractErrorMessage({ detail: { code: 'HOUSEHOLD_QUOTA_NOT_CONFIGURED' } }, '兜底')).toBe(
      'HOUSEHOLD_QUOTA_NOT_CONFIGURED',
    )
  })

  it('校验失败的 fields 比通用文案更能说明哪个字段错了', () => {
    const body = {
      detail: {
        code: 'VALIDATION_FAILED',
        message: '请求参数校验失败',
        fields: [
          { path: 'body.username', message: '字段必填' },
          { path: 'body.password', message: '长度不足' },
        ],
      },
    }

    expect(extractErrorMessage(body, '兜底')).toBe('字段必填; 长度不足')
  })

  it('保留旧的字符串 detail 与 FastAPI 数组形态', () => {
    expect(extractErrorMessage({ detail: '用户名或密码错误' }, '兜底')).toBe('用户名或密码错误')
    expect(extractErrorMessage({ detail: [{ loc: ['body', 'message'], msg: 'Field required' }] }, '兜底')).toBe(
      'Field required',
    )
  })

  it('无法识别的响应体回退到默认文案', () => {
    expect(extractErrorMessage({ error: { reason: 'unknown' } }, '请求失败')).toBe('请求失败')
    expect(extractErrorMessage(undefined, '请求失败')).toBe('请求失败')
  })
})

describe('refreshAccessToken 的令牌轮换', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    postMock.mockReset()
  })

  it('把轮换后的 refresh 一并写回，而不是只更新 access', async () => {
    const auth = useAuthStore()
    auth.setTokens('access-old', 'refresh-old')
    postMock.mockResolvedValue({ data: { access_token: 'access-new', refresh_token: 'refresh-new' } })

    await expect(refreshAccessToken()).resolves.toBe('access-new')

    // 不写回 refresh 的话，下一次静默刷新会重放已用令牌 → 后端断族 → 强制登出
    expect(auth.accessToken).toBe('access-new')
    expect(auth.refreshToken).toBe('refresh-new')
    expect(JSON.parse(localStorage.getItem('cs.auth')!).refreshToken).toBe('refresh-new')
  })

  it('后端没有返回新 refresh 时退回只更新 access', async () => {
    const auth = useAuthStore()
    auth.setTokens('access-old', 'refresh-old')
    postMock.mockResolvedValue({ data: { access_token: 'access-new' } })

    await refreshAccessToken()

    expect(auth.accessToken).toBe('access-new')
    expect(auth.refreshToken).toBe('refresh-old')
  })
})
