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
