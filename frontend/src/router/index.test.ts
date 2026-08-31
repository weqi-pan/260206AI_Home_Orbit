import { beforeEach, describe, expect, it, vi } from 'vitest'

const fetchSetupStatus = vi.fn()

vi.mock('../stores/auth', () => ({
  useAuthStore: () => ({
    isLoggedIn: true,
    mustChangePassword: false,
    account: { role: 'user' },
  }),
}))
vi.mock('../api/setup', () => ({ fetchSetupStatus: () => fetchSetupStatus() }))

describe('AI route gate', () => {
  beforeEach(() => {
    fetchSetupStatus.mockReset()
  })

  it('allows /chat when primary chat is ready even if embedding is unavailable', async () => {
    fetchSetupStatus.mockResolvedValue({
      ready: true,
      ai_ready: true,
      embedding_ready: false,
      error_code: null,
      required_purposes: ['primary_chat'],
      optional_purposes: ['embedding'],
      configured_purposes: ['primary_chat'],
      missing_purposes: [],
      missing_optional_purposes: ['embedding'],
      degraded_features: ['knowledge_retrieval', 'vectorization'],
      can_configure: false,
      master_key_valid: true,
    })
    const { navigationGuard } = await import('./index')

    const redirect = await navigationGuard({ path: '/chat', meta: { ai: true } } as never)

    expect(redirect).toBeUndefined()
  })
})
