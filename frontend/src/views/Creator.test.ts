import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Creator from './Creator.vue'
import type { CreatorDraftDetail } from '../creator/types'

const api = vi.hoisted(() => ({
  fetchCeilingTools: vi.fn(),
  fetchDrafts: vi.fn(),
  fetchDraft: vi.fn(),
  createDraft: vi.fn(),
  updateDraft: vi.fn(),
  generateCard: vi.fn(),
  validateDraft: vi.fn(),
  uploadKnowledge: vi.fn(),
  deleteKnowledge: vi.fn(),
  runTrial: vi.fn(),
  fetchTrial: vi.fn(),
  publishDraft: vi.fn(),
}))

vi.mock('../api/creator', () => api)
vi.mock('element-plus/es/components/message/index.mjs', () => ({ ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }))

const TOOLS = [
  { tool_id: 't1', key: 'household.recipe.scale', version: '1', risk_level: 'low', display_name: '菜谱用量换算', description: '' },
  { tool_id: 't2', key: 'household.knowledge.search', version: '1', risk_level: 'low', display_name: '家庭知识检索', description: '' },
  { tool_id: 't3', key: 'household.shopping.write', version: '1', risk_level: 'reversible', display_name: '购物清单写入', description: '' },
  { tool_id: 't4', key: 'platform.test_high_risk', version: '1', risk_level: 'high', display_name: '高风险测试', description: '' },
]

const CARD = {
  name: '广东菜大厨',
  description: '提供几道广东菜做法',
  prompt: '你是家庭粤菜大厨。',
  examples: [{ user: '推荐一道粤菜', assistant: '白切鸡。' }],
  model_requirements: { capabilities: ['chat'] },
  skills: [{ name: '粤菜菜谱', instructions: '根据菜谱知识回答。', knowledge_asset_ids: [] }],
  tools: ['household.recipe.scale'],
  budgets: { tool_calls: 8, tokens: 8000, wall_time_seconds: 60, delegation_depth: 1 },
  tests: [{ name: '菜谱问答', input: '白切鸡怎么做', expected_contains: '白切鸡' }],
  workflow: null,
}

function detail(overrides: Partial<CreatorDraftDetail> = {}): CreatorDraftDetail {
  return {
    id: 'd1', title: '广东菜大厨', status: 'draft', updated_at: '2026-08-28T00:00:00Z',
    description: '提供几道广东菜做法', intent_card: { ...CARD }, validation: { valid: true, errors: [] },
    knowledge_assets: [], agent_versions: [], deployment_id: null, latest_trial: null,
    created_at: '2026-08-28T00:00:00Z', ...overrides,
  } as CreatorDraftDetail
}

function mountPage() {
  return mount(Creator, {
    global: { stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } } },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  api.fetchCeilingTools.mockResolvedValue(TOOLS)
  api.fetchDrafts.mockResolvedValue([{ id: 'd1', title: '广东菜大厨', status: 'draft', updated_at: '2026-08-28' }])
  api.fetchDraft.mockResolvedValue(detail())
})

describe('创建页 intent card', () => {
  it('renders the draft and tool toggles with risk labels from the household ceiling', async () => {
    const wrapper = mountPage()
    await vi.waitFor(() => expect(wrapper.text()).toContain('广东菜大厨'))
    expect(wrapper.text()).toContain('菜谱用量换算')
    expect(wrapper.text()).toContain('可撤销写入')
    expect(wrapper.text()).toContain('高风险·需逐次确认')
    const highRisk = wrapper.find('#tool-platform\\.test_high_risk')
    expect((highRisk.element as HTMLInputElement).disabled).toBe(true)
    expect((wrapper.find('#tool-household\\.recipe\\.scale').element as HTMLInputElement).checked).toBe(true)
  })

  it('generates an intent card from a one-sentence description', async () => {
    api.generateCard.mockResolvedValue({ intent_card: CARD, validation: { valid: true, errors: [] }, status: 'draft' })
    const wrapper = mountPage()
    await vi.waitFor(() => expect(wrapper.find('input[aria-label="一句话描述"]').exists()).toBe(true))
    await wrapper.find('input[aria-label="一句话描述"]').setValue('提供几道广东菜做法')
    await wrapper.find('.composer button').trigger('click')
    await vi.waitFor(() => expect(api.generateCard).toHaveBeenCalledWith('d1', '提供几道广东菜做法'))
    await vi.waitFor(() => expect(api.fetchDraft).toHaveBeenCalled())
  })

  it('surfaces MODEL_NOT_CONFIGURED gracefully while keeping the page usable', async () => {
    api.generateCard.mockRejectedValue({
      response: { data: { detail: { code: 'MODEL_NOT_CONFIGURED' } } },
    })
    const wrapper = mountPage()
    await vi.waitFor(() => expect(wrapper.find('.composer button').exists()).toBe(true))
    await wrapper.find('.composer button').trigger('click')
    await vi.waitFor(() => expect(wrapper.find('.unready').text()).toContain('MODEL_NOT_CONFIGURED'))
    expect(wrapper.find('.card-form').exists()).toBe(true) // 草稿编辑仍可用
  })

  it('shows validation errors with stable codes and Chinese explanations', async () => {
    api.fetchDraft.mockResolvedValue(detail({
      validation: { valid: false, errors: [{ code: 'TOOL_NOT_GRANTED', path: 'tools.household.shopping.write', detail: '' }] },
    }))
    const wrapper = mountPage()
    await vi.waitFor(() => expect(wrapper.find('.error-panel').exists()).toBe(true))
    expect(wrapper.find('.error-panel code').text()).toBe('TOOL_NOT_GRANTED')
    expect(wrapper.find('.error-panel').text()).toContain('家庭未获得该工具授权')
  })

  it('publishes with explicitly confirmed reversible grants and renders trial results', async () => {
    api.runTrial.mockResolvedValue({ report_id: 'r1', status: 'succeeded', cases: [] })
    api.fetchTrial.mockResolvedValue({
      id: 'r1', draft_id: 'd1', status: 'completed', error_code: null, granted_tool_keys: ['household.shopping.write'],
      cases: [{ name: '清单', input: '加菜', status: 'passed', error_code: null, output: '鸡蛋已加入清单' }],
    })
    api.publishDraft.mockResolvedValue({
      agent_id: 'a1', agent_version_id: 'v1', agent_version: 1,
      deployment: { id: 'dep1', revision: 1, visibility: 'private' }, installation_id: 'i1',
      skills: [], granted_tool_keys: ['household.shopping.write'], vectorized_assets: [],
    })
    api.fetchDraft.mockResolvedValue(detail({ status: 'validated' }))
    const wrapper = mountPage()
    await vi.waitFor(() => expect((wrapper.vm as unknown as { card: { tools: string[] } }).card).toBeTruthy())
    const card = wrapper.vm as unknown as { card: { tools: string[] } }
    card.card.tools = [...card.card.tools, 'household.shopping.write']
    await vi.waitFor(() => expect(wrapper.find('#grant-household\\.shopping\\.write').exists()).toBe(true))
    await wrapper.find('#grant-household\\.shopping\\.write').setValue(true)
    await wrapper.find('[aria-labelledby="trial-title"] button').trigger('click')
    await vi.waitFor(() => expect(api.runTrial).toHaveBeenCalledWith('d1', ['household.shopping.write']))
    await vi.waitFor(() => expect(wrapper.find('[data-testid="trial-results"]').text()).toContain('通过'))
    await wrapper.find('button.primary').trigger('click')
    await vi.waitFor(() => expect(api.publishDraft).toHaveBeenCalledWith('d1', ['household.shopping.write']))
  })
})
