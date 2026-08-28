import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ElementPlus from 'element-plus'
import TaskOps from './TaskOps.vue'

const runMock = vi.fn().mockResolvedValue({ idempotency_key: 'admin:demo:x', status: 'succeeded', attempt: 1, error_code: null })

vi.mock('../../api/audit', () => ({
  fetchCeleryTasks: vi.fn().mockResolvedValue({
    total: 2, page: 1, page_size: 20,
    items: [
      { id: 'task-1', idempotency_key: 'beat:reminders:h1', task_type: 'household.reminders.dispatch', household_id: 'h-1', status: 'succeeded', attempt: 1, max_attempts: 3, error_code: null, next_retry_at: null, created_at: '2026-08-28T08:00:00', updated_at: '2026-08-28T08:00:05' },
      { id: 'task-2', idempotency_key: 'demo.broken:1', task_type: 'demo.broken', household_id: 'h-1', status: 'dead', attempt: 3, max_attempts: 3, error_code: 'UPSTREAM_UNAVAILABLE', next_retry_at: null, created_at: '2026-08-27T08:00:00', updated_at: '2026-08-27T08:02:00' },
    ],
  }),
  fetchCeleryTaskDetail: vi.fn().mockResolvedValue({
    id: 'task-1', idempotency_key: 'beat:reminders:h1', task_type: 'household.reminders.dispatch', household_id: 'h-1',
    status: 'succeeded', attempt: 1, max_attempts: 3, error_code: null, next_retry_at: null,
    created_at: '2026-08-28T08:00:00', updated_at: '2026-08-28T08:00:05',
    error_detail: null, payload: { household_id: 'h-1' }, result: { occurrences: 2 },
    audit: { submitted_at: '2026-08-28T08:00:00', finished_at: '2026-08-28T08:00:05' },
    notifications: [{ id: 'n-1', title: '后台任务失败', kind: 'task_failed', created_at: '2026-08-27T08:02:00' }],
  }),
}))

vi.mock('../../api/ops', () => ({
  fetchTaskTypes: vi.fn().mockResolvedValue([
    { task_type: 'household.reminders.dispatch', max_attempts: 3, description: '到期提醒派发' },
    { task_type: 'creator.sandbox_trial', max_attempts: 1, description: '沙盒试运行' },
  ]),
  runAdminTask: (...args: unknown[]) => runMock(...(args as [string, Record<string, unknown>, string | null])),
}))

describe('Admin TaskOps 任务运营', () => {
  it('lists tasks with retries, dead letters and stable error codes', async () => {
    const wrapper = mount(TaskOps, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(wrapper.text()).toContain('household.reminders.dispatch')
    expect(wrapper.text()).toContain('UPSTREAM_UNAVAILABLE')
    expect(wrapper.text()).toContain('3/3') // 尝试/上限
  })

  it('opens detail with idempotency key, audit trail and failure notifications', async () => {
    const wrapper = mount(TaskOps, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    await wrapper.find('[data-testid="open-task"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('beat:reminders:h1')
    expect(wrapper.text()).toContain('finished_at')
    expect(wrapper.text()).toContain('后台任务失败') // 任务失败产生的通知记录
  })

  it('submits manual re-trigger through the audited admin boundary', async () => {
    const wrapper = mount(TaskOps, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    await wrapper.find('[data-testid="trigger-type"]').setValue('creator.sandbox_trial')
    await wrapper.find('[data-testid="trigger-household"]').setValue('h-1')
    await wrapper.find('[data-testid="trigger-submit"]').trigger('submit')
    await flushPromises()
    expect(runMock).toHaveBeenCalledWith('creator.sandbox_trial', {}, 'h-1')
  })
})
