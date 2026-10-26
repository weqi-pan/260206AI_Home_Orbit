import axios, { AxiosError, type AxiosInstance, type InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { useAuthStore } from '../stores/auth'

interface ApiErrorBody {
  detail?: unknown
}

type RetriableConfig = InternalAxiosRequestConfig & { _retried?: boolean }

let refreshPromise: Promise<string> | null = null

export function extractErrorMessage(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as ApiErrorBody).detail
    if (typeof detail === 'string' && detail) return detail
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const { message, code, fields } = detail as { message?: unknown; code?: unknown; fields?: unknown }
      // 校验失败的通用文案（"请求参数校验失败"）没告诉用户哪个字段错了，
      // 所以 fields 优先：它能落到具体字段的原始提示。
      if (Array.isArray(fields)) {
        const reasons = fields
          .map((field) => (field && typeof field === 'object' ? (field as { message?: unknown }).message : field))
          .filter((item): item is string => typeof item === 'string' && Boolean(item))
        if (reasons.length) return reasons.join('; ')
      }
      // 后端错误契约：detail 是 { code, message? }。优先给用户看中文 message，
      // code 只是给程序的分支依据 —— 直接显示 HOUSEHOLD_NOT_FOUND 对用户没有意义。
      if (typeof message === 'string' && message) return message
      if (typeof code === 'string' && code) return code
    }
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => (item && typeof item === 'object' && 'msg' in item ? (item as { msg: unknown }).msg : item))
        .filter((item): item is string => typeof item === 'string' && Boolean(item))
      if (messages.length) return messages.join('; ')
    }
  }
  return fallback
}

export async function refreshAccessToken(): Promise<string> {
  const auth = useAuthStore()
  if (!auth.refreshToken) throw new Error('缺少刷新令牌')
  if (!refreshPromise) {
    refreshPromise = axios
      .post('/api/auth/refresh', { refresh_token: auth.refreshToken })
      .then((response) => {
        const token = response.data.access_token as string
        // 后端每次刷新都会轮换 refresh 令牌；只写回 access 的话，下一次刷新
        // 就会拿已用过的 refresh 去换，被判为重放并整族吊销 → 强制登出。
        const rotated = response.data.refresh_token as string | undefined
        if (rotated) auth.setTokens(token, rotated)
        else auth.setAccessToken(token)
        return token
      })
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

export async function forceLogout(): Promise<void> {
  useAuthStore().logout()
  const { default: router } = await import('../router')
  if (router.currentRoute.value.path !== '/login') await router.push('/login')
}

const http: AxiosInstance = axios.create({ baseURL: '/api', timeout: 30000 })

http.interceptors.request.use((config) => {
  const auth = useAuthStore()
  if (auth.accessToken) config.headers.set('Authorization', `Bearer ${auth.accessToken}`)
  return config
})

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const config = error.config as RetriableConfig | undefined
    const status = error.response?.status
    const isAuthEndpoint = typeof config?.url === 'string' && config.url.includes('/auth/')
    if (status === 401 && config && !config._retried && !isAuthEndpoint) {
      config._retried = true
      try {
        const token = await refreshAccessToken()
        config.headers.set('Authorization', `Bearer ${token}`)
        return http(config)
      } catch {
        await forceLogout()
        ElMessage.error('登录已过期，请重新登录')
        return Promise.reject(error)
      }
    }
    const message = extractErrorMessage(error.response?.data, error.message || '请求失败')
    ElMessage.error(message)
    return Promise.reject(error)
  },
)

export default http
