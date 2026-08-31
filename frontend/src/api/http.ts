import axios, { AxiosError, type AxiosInstance, type InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
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
    if (detail && typeof detail === 'object' && !Array.isArray(detail) && 'code' in detail) {
      const code = (detail as { code?: unknown }).code
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
        auth.setAccessToken(token)
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
