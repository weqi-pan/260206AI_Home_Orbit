import http from './http'
import type { AccountInfo } from '../stores/auth'

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: string
  account: AccountInfo
}

export interface RegisterPayload {
  username: string
  password: string
  city?: string
  phone?: string
}

export function login(username: string, password: string) {
  return http.post<TokenPair>('/auth/login', { username, password }).then((r) => r.data)
}

export function register(payload: RegisterPayload) {
  return http.post<TokenPair>('/auth/register', payload).then((r) => r.data)
}

export function fetchMe() {
  return http.get<AccountInfo>('/auth/me').then((r) => r.data)
}

export function changePassword(currentPassword: string, newPassword: string) {
  return http.put<void>('/auth/password', { current_password: currentPassword, new_password: newPassword })
}

/** 吊销当前 refresh 令牌。失败也要继续本地登出，所以调用方用 best-effort 包一层。 */
export function logout(refreshToken: string) {
  return http.post<void>('/auth/logout', { refresh_token: refreshToken })
}

/** 吊销该账号全部会话（所有设备）。 */
export function logoutAll() {
  return http.post<void>('/auth/logout-all')
}
