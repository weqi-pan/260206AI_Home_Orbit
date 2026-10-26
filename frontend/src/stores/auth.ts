import { defineStore } from 'pinia'

export interface AccountInfo {
  id: string
  username: string
  role: string
  status: string
  must_change_password: boolean
  user_id: string | null
}

export interface AuthState {
  accessToken: string
  refreshToken: string
  account: AccountInfo | null
}

const STORAGE_KEY = 'cs.auth'

function readStorage(): AuthState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<AuthState>
      if (parsed.accessToken && parsed.refreshToken) {
        return {
          accessToken: parsed.accessToken,
          refreshToken: parsed.refreshToken,
          account: parsed.account ?? null,
        }
      }
    }
  } catch {}
  return { accessToken: '', refreshToken: '', account: null }
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => readStorage(),
  getters: {
    isLoggedIn: (state) => state.accessToken.length > 0,
    mustChangePassword: (state) => state.account?.must_change_password ?? false,
  },
  actions: {
    persist() {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ accessToken: this.accessToken, refreshToken: this.refreshToken, account: this.account }),
      )
    },
    setSession(accessToken: string, refreshToken: string, account: AccountInfo) {
      this.accessToken = accessToken
      this.refreshToken = refreshToken
      this.account = account
      this.persist()
    },
    setAccessToken(token: string) {
      this.accessToken = token
      this.persist()
    },
    // 刷新令牌是轮换的：只写回 access 会让下一次静默刷新重放已用令牌，
    // 后端会按「重放」整族吊销并要求重新登录。必须成对更新。
    setTokens(accessToken: string, refreshToken: string) {
      this.accessToken = accessToken
      this.refreshToken = refreshToken
      this.persist()
    },
    logout() {
      this.accessToken = ''
      this.refreshToken = ''
      this.account = null
      localStorage.removeItem(STORAGE_KEY)
    },
  },
})
