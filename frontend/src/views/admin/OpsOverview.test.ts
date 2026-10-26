import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import OpsOverview from './OpsOverview.vue'

vi.mock('../../api/ops', () => ({
  fetchOpsOverview: vi.fn().mockResolvedValue({
    window_days: 7,
    households: { total: 12, new_in_window: 3, growth_by_day: [{ day: '2026-08-28', count: 3 }] },
    installations: { active: 9, households_with_active: 5 },
    agent_runs: {
      total_in_window: 21,
      by_status: { completed: 18, failed: 3 },
      by_day: [{ day: '2026-08-28', count: 21 }],
    },
    tool_executions: {
      total_in_window: 7,
      by_status: { success: 5, permission_denied: 2 },
      permission_denials: 2,
      failed: 0,
    },
    celery_tasks: {
      total_in_window: 10,
      by_status: { succeeded: 8, dead: 2 },
      failure_rate: 0.2,
      throughput_by_day: [{ day: '2026-08-28', count: 8 }],
    },
    review: { queue_depth: 4, decided_in_window: 2, avg_turnaround_seconds: 7200, decided_by_action: { approved: 2 } },
    model_usage: {
      requests: 30,
      tokens: 2400,
      by_model: [{ model_id: 'm-1', model_name: 'chat', requests: 30, tokens: 2400 }],
      by_purpose: [{ purpose: 'primary_chat', requests: 30, tokens: 2400 }],
      by_day: [{ day: '2026-08-28', requests: 30, tokens: 2400 }],
    },
    quota_incidents: { count: 2, by_code: { HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED: 1, MODEL_RATE_LIMIT_EXCEEDED: 1 } },
    risk: { high_risk_approvals: 1, permission_denials: 2 },
  }),
}))

describe('Admin OpsOverview 运营概览', () => {
  it('renders metric cards and trends from the real API contract', async () => {
    const wrapper = mount(OpsOverview)
    await flushPromises()
    expect(wrapper.find('[data-testid="ops-cards"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="metric-households"]').text()).toBe('12')
    expect(wrapper.find('[data-testid="metric-tasks"]').text()).toBe('20%')
    expect(wrapper.text()).toContain('2026-08-28') // 按日趋势来自真实分组数据
    expect(wrapper.text()).toContain('HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED') // 稳定码可见
    expect(wrapper.find('.orbit-visual').exists()).toBe(false) // 密集证据风格
  })
})
