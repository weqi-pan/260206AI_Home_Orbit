import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import Reviews from './Reviews.vue'

const decideMock = vi.fn().mockResolvedValue({ submission_id: 'sub-1', status: 'approved' })
const takedownMock = vi.fn().mockResolvedValue({ entry_id: 'entry-1', status: 'deprecated' })
const handleReportMock = vi.fn().mockResolvedValue({ id: 'rep-1', status: 'dismissed' })

vi.mock('../../review/api', () => ({
  fetchReviewQueue: vi.fn().mockResolvedValue({
    items: [
      {
        submission_id: 'sub-1',
        entry_id: null,
        name: '市场采购助手',
        household_id: 'h-1',
        status: 'review_pending',
        category: '生活',
        version: 2,
        prior_public_version: 1,
        precheck_status: 'passed',
        risk_summary_status: 'done',
        submitted_at: '2026-08-28T08:00:00',
        decided_at: null,
        decision_reason: null,
      },
    ],
  }),
  fetchReviewSubmission: vi.fn().mockResolvedValue({
    submission: {
      id: 'sub-1',
      status: 'review_pending',
      household_id: 'h-1',
      draft_id: 'd-1',
      category: '生活',
      precheck_status: 'passed',
      risk_summary_status: 'done',
      submitted_at: '2026-08-28T08:00:00',
      decided_at: null,
      decision_reason: null,
    },
    listing_preview: { name: '市场采购助手', description: '把采购项写入家庭购物清单', category: '生活' },
    agent_version: { id: 'v-2', version: 2, definition: { prompt: '你是采购助手' } },
    prior_public_version: { version: 1, definition: { prompt: '你是采购助手 v1' } },
    diff: {
      prompt_changed: true,
      tools_added: ['household.memo.write'],
      tools_removed: [],
      budgets_changed: [],
      workflow_changed: false,
      skills_changed: false,
    },
    permissions: [
      {
        tool_id: 't-1',
        key: 'household.shopping.write',
        display_name: '购物清单写入',
        description: '写入清单',
        risk_level: 'reversible',
        version: '1',
      },
    ],
    knowledge: [
      {
        asset_id: 'ka-1',
        filename: '采购知识.txt',
        file_type: '.txt',
        scan_status: 'passed',
        scan_reason: null,
        content_hash: 'a'.repeat(64),
      },
    ],
    sandbox: {
      status: 'completed',
      error_code: null,
      cases: [{ name: '加购', input: '加鸡蛋', status: 'passed', error_code: null, output: '鸡蛋已加入清单' }],
    },
    precheck: { passed: true, codes: [], checks: [{ code: 'SANDBOX_TRIAL', passed: true, detail: '' }] },
    risk_summary: { status: 'done', summary: '整体风险较低，注意写入权限。', risk_level: 'elevated' },
    risk_payload: { name: '市场采购助手', tools: [] },
  }),
  decideReviewSubmission: (...args: unknown[]) => decideMock(...(args as [string, string, string])),
  takedownEntry: (...args: unknown[]) => takedownMock(...(args as [string, string])),
  fetchReviewReports: vi.fn().mockResolvedValue({
    items: [
      {
        id: 'rep-1',
        entry_id: 'entry-1',
        entry_name: '市场采购助手',
        entry_status: 'published',
        household_id: 'h-2',
        reporter_account_id: 'acc-2',
        reason: '描述与行为不符',
        status: 'open',
        resolution_note: null,
        created_at: '2026-08-28T09:00:00',
      },
    ],
  }),
  handleReviewReport: (...args: unknown[]) => handleReportMock(...(args as [string, string, string])),
}))

describe('Admin Reviews 审核工作台', () => {
  it('renders the evidence-dense queue with status filters', async () => {
    const wrapper = mount(Reviews)
    await flushPromises()
    expect(wrapper.text()).toContain('市场采购助手')
    expect(wrapper.text()).toContain('review_pending')
    expect(wrapper.find('table').exists()).toBe(true) // 表格化证据，不铺大轨道
    expect(wrapper.find('.orbit-visual').exists()).toBe(false)
  })

  it('shows the full evidence bundle including advisory risk summary', async () => {
    const wrapper = mount(Reviews)
    await flushPromises()
    await wrapper.find('[data-testid="open-submission"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('把采购项写入家庭购物清单')
    expect(wrapper.text()).toContain('household.memo.write') // 版本差异
    expect(wrapper.text()).toContain('采购知识.txt') // 知识来源与扫描
    expect(wrapper.text()).toContain('鸡蛋已加入清单') // 沙盒轨迹
    expect(wrapper.text()).toContain('整体风险较低') // LLM 摘要
    expect(wrapper.find('[data-testid="risk-advisory"]').text()).toContain('仅参考') // 永不自动通过
  })

  it('submits audited decisions with reason', async () => {
    const wrapper = mount(Reviews)
    await flushPromises()
    await wrapper.find('[data-testid="open-submission"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="decision-reason"]').setValue('预检与摘要无阻断风险')
    await wrapper.find('[data-testid="decision-action"]').setValue('approve')
    await wrapper.find('[data-testid="submit-decision"]').trigger('click')
    expect(decideMock).toHaveBeenCalledWith('sub-1', 'approve', '预检与摘要无阻断风险')
  })

  it('handles reports and takedown with audit', async () => {
    const wrapper = mount(Reviews)
    await flushPromises()
    await wrapper.find('[data-testid="report-note"]').setValue('查证不成立')
    await wrapper.find('[data-testid="dismiss-report"]').trigger('click')
    expect(handleReportMock).toHaveBeenCalledWith('rep-1', 'dismiss', '查证不成立')
  })
})
