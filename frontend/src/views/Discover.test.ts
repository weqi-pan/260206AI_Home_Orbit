import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import Discover from './Discover.vue'

const installMock = vi.fn().mockResolvedValue({
  installation_id: 'i-1', deployment_id: 'd-1', agent_version_id: 'v-2', pinned_version: 2,
  granted_tool_keys: ['household.recipe.scale'],
})
const favoriteMock = vi.fn().mockResolvedValue({ entry_id: 'entry-1', favorited: true })
const reportMock = vi.fn().mockResolvedValue({ id: 'r-1', status: 'open' })

vi.mock('../market/api', () => ({
  MARKET_CATEGORIES: ['生活', '烹饪', '家务', '设备', '知识', '效率', '其他'],
  fetchListings: vi.fn().mockResolvedValue({
    official: [{ deployment_id: 'od-1', name: '家庭厨师', description: '官方厨艺助手', official: true, installed: true }],
    entries: [
      {
        id: 'entry-1', name: '市场采购助手', description: '把采购项写入家庭购物清单', category: '生活',
        official: false, install_count: 12, current_version: 2, my_installation: null,
      },
      {
        id: 'entry-2', name: '粤菜大厨', description: '提供几道广东菜做法', category: '烹饪',
        official: false, install_count: 3, current_version: 1, my_installation: null,
      },
    ],
  }),
  fetchEntry: vi.fn().mockResolvedValue({
    id: 'entry-1', name: '市场采购助手', description: '把采购项写入家庭购物清单', category: '生活',
    official: false, status: 'published', install_count: 12,
    current_version: { number: 2, agent_version_id: 'v-2' },
    versions: [{ number: 2, submission_id: 's-2', published_at: '2026-08-28T00:00:00' }],
    permissions: [
      { tool_id: 't-1', key: 'household.recipe.scale', display_name: '菜谱用量换算', description: '按人数换算', risk_level: 'low', version: '1' },
      { tool_id: 't-2', key: 'household.shopping.write', display_name: '购物清单写入', description: '新增清单条目', risk_level: 'reversible', version: '1' },
    ],
    favorited: false,
    my_installation: null,
  }),
  installEntry: (...args: unknown[]) => installMock(...(args as [string, string[]])),
  favoriteEntry: () => favoriteMock(),
  reportEntry: (...args: unknown[]) => reportMock(...(args as [string, string])),
}))

describe('Discover 市场发现页', () => {
  it('renders dense official and market lists wired to /market/listings', async () => {
    const wrapper = mount(Discover)
    await flushPromises()
    expect(wrapper.text()).toContain('家庭厨师')
    expect(wrapper.findAll('[data-testid="market-entry"]').length).toBe(2)
    expect(wrapper.text()).toContain('市场采购助手')
    expect(wrapper.text()).toContain('12') // install count
    expect(wrapper.find('.orbit-visual').exists()).toBe(false) // 密集列表，无大轨道
  })

  it('filters by search query through the API contract', async () => {
    const { fetchListings } = await import('../market/api')
    const wrapper = mount(Discover)
    await flushPromises()
    await wrapper.find('[data-testid="market-search"]').setValue('粤菜')
    await wrapper.find('form').trigger('submit')
    expect(fetchListings).toHaveBeenLastCalledWith('粤菜', '')
  })

  it('opens detail with risk-labeled permissions and installs with default-unchecked authorization', async () => {
    const wrapper = mount(Discover)
    await flushPromises()
    await wrapper.find('[data-testid="market-entry"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('购物清单写入')
    expect(wrapper.find('[data-testid="permission-risk-reversible"]').text()).toContain('可撤销写入')

    await wrapper.find('[data-testid="open-install"]').trigger('click')
    await flushPromises()
    const boxes = wrapper.findAll('[data-testid="grant-tool"]')
    expect(boxes.length).toBeGreaterThan(0)
    expect(boxes.every((box) => (box.element as HTMLInputElement).checked === false)).toBe(true) // 默认不勾选
    await wrapper.find('[data-testid="confirm-install"]').trigger('click')
    expect(installMock).toHaveBeenCalledWith('entry-1', [])
  })

  it('supports favorite and report actions on the entry detail', async () => {
    const wrapper = mount(Discover)
    await flushPromises()
    await wrapper.find('[data-testid="market-entry"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="toggle-favorite"]').trigger('click')
    expect(favoriteMock).toHaveBeenCalled()
    await wrapper.find('[data-testid="report-reason"]').setValue('描述与行为不符')
    await wrapper.find('[data-testid="submit-report"]').trigger('click')
    expect(reportMock).toHaveBeenCalledWith('entry-1', '描述与行为不符')
  })
})
