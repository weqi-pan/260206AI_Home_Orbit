import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import MyAgents from './MyAgents.vue'

const upgradeMock = vi.fn().mockResolvedValue({
  installation_id: 'i-2', pinned_version: 2, granted_tool_keys: ['household.shopping.write'],
  newly_granted_tool_keys: ['household.memo.write'],
})
const uninstallMock = vi.fn().mockResolvedValue(undefined)

vi.mock('../api/creator', () => ({
  fetchMyAgents: vi.fn().mockResolvedValue([
    {
      draft_id: 'd-1', agent_id: 'a-1', name: '广东菜大厨', description: '提供几道广东菜做法',
      deployment_id: 'dep-1', published_at: '2026-08-27T10:00:00',
      versions: [{ id: 'v-1', version: 1, published_at: '2026-08-27T10:00:00' }],
    },
  ]),
}))
vi.mock('../market/api', () => ({
  fetchMyInstalls: vi.fn().mockResolvedValue([
    {
      installation_id: 'i-1',
      entry: { id: 'entry-1', name: '市场采购助手', description: '采购清单写入', category: '生活' },
      deployment_id: 'dep-9', agent_version_id: 'lv-1',
      pinned_version: 1, latest_version: 2, update_available: true, deprecated: false,
      granted_tool_keys: ['household.shopping.write'],
      update: {
        from_version: 1, to_version: 2,
        new_permissions: [
          { tool_id: 't-3', key: 'household.memo.write', display_name: '家庭备忘写入', description: '写入家庭备忘', risk_level: 'reversible', version: '1' },
        ],
      },
    },
  ]),
  upgradeMarketInstall: (...args: unknown[]) => upgradeMock(...(args as [string, string[]])),
  uninstallMarketInstall: (...args: unknown[]) => uninstallMock(...(args as [string, boolean])),
}))

describe('MyAgents 我的助手页', () => {
  it('lists self-built agents and installed market agents with pinned versions', async () => {
    const wrapper = mount(MyAgents)
    await flushPromises()
    expect(wrapper.text()).toContain('广东菜大厨')
    expect(wrapper.text()).toContain('市场采购助手')
    expect(wrapper.find('[data-testid="pinned-version"]').text()).toContain('v1')
    expect(wrapper.text()).toContain('v2') // latest
    expect(wrapper.find('[data-testid="update-badge"]').exists()).toBe(true)
  })

  it('requires explicit reauthorization for new permissions before upgrade', async () => {
    const wrapper = mount(MyAgents)
    await flushPromises()
    await wrapper.find('[data-testid="start-upgrade"]').trigger('click')
    await flushPromises()
    const boxes = wrapper.findAll('[data-testid="regrant-tool"]')
    expect(boxes.length).toBe(1)
    expect((boxes[0].element as HTMLInputElement).checked).toBe(false) // 新权限默认不勾选
    await wrapper.find('[data-testid="confirm-upgrade"]').trigger('click')
    expect(upgradeMock).toHaveBeenCalledWith('i-1', []) // 未勾选则不授予
  })

  it('uninstalls with optional permission revocation', async () => {
    const wrapper = mount(MyAgents)
    await flushPromises()
    await wrapper.find('[data-testid="start-uninstall"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="revoke-permissions"]').setValue(true)
    await wrapper.find('[data-testid="confirm-uninstall"]').trigger('click')
    expect(uninstallMock).toHaveBeenCalledWith('i-1', true)
  })
})
