import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import AuditRelease from './AuditRelease.vue'

vi.mock('../../api/audit', () => ({
  fetchAdminActions: vi.fn().mockResolvedValue({
    total: 2, page: 1, page_size: 20,
    items: [
      { id: 'a-1', principal_id: 'acc-1', actor_username: 'admin', household_id: 'h-1', action: 'review.approve', resource_type: 'review_submission', resource_id: 'sub-1', detail: { reason: '链路完整' }, created_at: '2026-08-28T09:00:00' },
      { id: 'a-2', principal_id: 'acc-1', actor_username: 'admin', household_id: null, action: 'models.endpoint.create', resource_type: 'llm_endpoint', resource_id: 'e-1', detail: { has_api_key: true, masked_api_key: '****1234' }, created_at: '2026-08-28T08:00:00' },
    ],
  }),
  fetchDeploymentPointers: vi.fn().mockResolvedValue({
    market_entries: {
      total: 7, page: 1, page_size: 20,
      items: [
        { entry_id: 'entry-1', name: '市场采购助手', source_household_id: 'h-1', official: false, status: 'published', current_version_number: 2, current_version_id: 'v-2', history: [
          { version: 2, version_id: 'v-2', changed_by_account_id: 'acc-1', changed_at: '2026-08-28T10:00:00', submission_id: 'sub-2' },
          { version: 1, version_id: 'v-1', changed_by_account_id: 'acc-1', changed_at: '2026-08-27T10:00:00', submission_id: 'sub-1' },
        ] },
      ],
    },
    official: {
      total: 1, page: 1, page_size: 20,
      items: [
        { deployment_id: 'd-1', household_id: 'h-1', revision: 1, agent_version_id: 'v-9', created_by: null, created_at: '2026-08-01T00:00:00', history: [{ version: 1, version_id: 'v-9', published_at: '2026-08-01T00:00:00', changed_by: null }] },
      ],
    },
  }),
  fetchModelConfigRevisions: vi.fn().mockResolvedValue({
    total: 1, page: 1, page_size: 20,
    items: [
      { model_id: 'm-1', model_name: 'chat', model_type: 'chat', enabled: true, revision: 3, latest_health: { status: 'passed', error_code: null, checked_at: '2026-08-28T07:00:00' }, history: [
        { status: 'passed', error_code: null, endpoint_revision: 2, model_revision: 3, checked_at: '2026-08-28T07:00:00' },
      ] },
    ],
  }),
}))

describe('Admin AuditRelease 审计与发布', () => {
  it('renders the audited admin action log with actor, tenant and sanitized detail', async () => {
    const wrapper = mount(AuditRelease)
    await flushPromises()
    expect(wrapper.text()).toContain('review.approve')
    expect(wrapper.text()).toContain('admin')
    expect(wrapper.text()).toContain('masked_api_key') // 只出现掩码，不出现明文
  })

  it('shows per-entry deployment pointer history and model revision evidence', async () => {
    const wrapper = mount(AuditRelease)
    await flushPromises()
    expect(wrapper.text()).toContain('市场采购助手')
    expect(wrapper.text()).toContain('v2 · acc-1') // 指针历史：版本 · 决定者 · 时间
    expect(wrapper.text()).toContain('e2/m3') // 模型修订证据
  })

  it('renders pagination totals for pointer history and model revisions', async () => {
    const wrapper = mount(AuditRelease)
    await flushPromises()
    expect(wrapper.find('[data-testid="pointer-total"]').text()).toContain('7 条')
    expect(wrapper.find('[data-testid="revision-total"]').text()).toContain('1 条')
  })
})
