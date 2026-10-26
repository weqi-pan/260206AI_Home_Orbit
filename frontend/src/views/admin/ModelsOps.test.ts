import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ElementPlus from 'element-plus'
import ModelsOps from './ModelsOps.vue'
import Users from './Users.vue'

const releaseMock = vi.fn().mockResolvedValue({ released: 2 })
const adjustMock = vi
  .fn()
  .mockResolvedValue({ household_id: 'h-1', monthly_limit: 60, requests_per_minute: 6, agent_token_budget: 8 })

vi.mock('../../api/audit', () => ({
  fetchUsageAccounting: vi.fn().mockResolvedValue({
    window_days: 7,
    totals: { requests: 12, tokens: 96 },
    by_status: { completed: 10, reserved: 1, released: 1 },
    by_model: [{ model_id: 'm-1', model_name: 'chat', requests: 12, tokens: 96 }],
    by_purpose: [{ purpose: 'primary_chat', requests: 12, tokens: 96 }],
    by_day: [{ day: '2026-08-28', requests: 12, tokens: 96 }],
    unreconciled: {
      count: 1,
      window_minutes: 15,
      items: [
        { id: 'u-1', household_id: 'h-1', model_id: 'm-1', reserved_tokens: 8, created_at: '2026-08-28T06:00:00' },
      ],
    },
  }),
}))

vi.mock('../../api/ops', () => ({
  releaseExpiredReservations: (...args: unknown[]) => releaseMock(...(args as [])),
  fetchHouseholdQuota: vi.fn().mockResolvedValue({
    household_id: 'h-1',
    quota: { monthly_limit: 100, requests_per_minute: 10, agent_token_budget: 8 },
    usage: { month: '2026-08', requests: 5, tokens: 40 },
    trend: [{ day: '2026-08-28', requests: 5, tokens: 40 }],
    incidents: [
      {
        id: 'i-1',
        model_id: 'm-1',
        model_name: 'chat',
        code: 'HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED',
        created_at: '2026-08-28T05:00:00',
      },
    ],
  }),
  adjustHouseholdQuota: (...args: unknown[]) => adjustMock(...(args as [string, Record<string, unknown>])),
}))

vi.mock('../../api/admin', () => ({
  fetchAccounts: vi.fn().mockResolvedValue([]),
  fetchAdminHouseholds: vi.fn().mockResolvedValue([
    {
      id: 'h-1',
      name: '演示家庭',
      created_at: '2026-08-01T00:00:00',
      members: [{ account_id: 'acc-1', username: 'demo', role: 'owner' }],
    },
  ]),
}))

vi.mock('../../stores/auth', () => ({
  useAuthStore: () => ({ account: { id: 'acc-admin', username: 'admin', role: 'admin' } }),
}))

describe('Admin ModelsOps 模型用量与对账', () => {
  it('renders reservation reconciliation and surfaces unreconciled reservations', async () => {
    const wrapper = mount(ModelsOps)
    await flushPromises()
    expect(wrapper.text()).toContain('未对账预留')
    expect(wrapper.text()).toContain('released')
    expect(wrapper.text()).toContain('primary_chat')
  })

  it('releases expired reservations through the audited endpoint', async () => {
    const wrapper = mount(ModelsOps)
    await flushPromises()
    await wrapper.find('[data-testid="release-expired"]').trigger('click')
    await flushPromises()
    expect(releaseMock).toHaveBeenCalled()
  })
})

describe('Admin Users 家庭配额面板', () => {
  it('opens household quota view with usage, trend and incident history', async () => {
    const wrapper = mount(Users, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(wrapper.text()).toContain('家庭与配额')
    await wrapper.find('[data-testid="open-quota"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('40 令牌 / 5 次请求')
    expect(wrapper.text()).toContain('HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED')
  })

  it('saves audited quota adjustments', async () => {
    const wrapper = mount(Users, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    await wrapper.find('[data-testid="open-quota"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="save-quota"]').trigger('click')
    await flushPromises()
    expect(adjustMock).toHaveBeenCalled()
  })
})
