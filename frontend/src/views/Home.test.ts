import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Home from './Home.vue'
import homeSource from './Home.vue?raw'

const fetchSetupStatus = vi.fn()

vi.mock('../stores/auth', () => ({ useAuthStore: () => ({ account: { username: '测试用户' } }) }))
vi.mock('../api/platform', () => ({
  fetchOrbit: () =>
    Promise.resolve({
      nodes: [
        {
          id: 'household',
          label: '家庭空间',
          kind: 'household',
          status: 'ready',
          href: '/household',
          detail: '1 位家庭成员',
        },
        { id: 'tasks', label: '今日任务', kind: 'tasks', status: 'attention', href: '/household', detail: '浇花' },
        {
          id: 'shopping',
          label: '购物清单',
          kind: 'shopping',
          status: 'ready',
          href: '/household',
          detail: '1 项待购',
        },
        { id: 'devices', label: '家庭设备', kind: 'devices', status: 'ready', href: '/devices', detail: '2 台在线' },
        { id: 'support', label: '支持中心', kind: 'support', status: 'ready', href: '/support', detail: '没有新工单' },
      ],
    }),
  fetchSetupStatus: () => fetchSetupStatus(),
  fetchHomeToday: () =>
    Promise.resolve({
      date: '2026-08-28',
      tasks: [{ id: 't1', title: '浇花', description: '', status: 'open', due_at: null }],
      reminders: [{ id: 'r1', task_id: 't1', interval_minutes: 60, next_run_at: null, active: true }],
      shopping: { total: 2, unchecked: 1, checked: 1 },
      notifications: { unread: 3 },
      anomalies: [],
    }),
}))

describe('Home Orbit', () => {
  beforeEach(() => {
    fetchSetupStatus.mockResolvedValue({
      ready: false,
      ai_ready: false,
      embedding_ready: false,
      error_code: 'MODEL_NOT_CONFIGURED',
      required_purposes: ['primary_chat'],
      optional_purposes: ['embedding'],
      configured_purposes: [],
      missing_purposes: ['primary_chat'],
      missing_optional_purposes: ['embedding'],
      degraded_features: ['knowledge_retrieval', 'vectorization'],
      can_configure: false,
      master_key_valid: false,
    })
  })

  it('renders each visual node as a named keyboard link matching the linear destination', async () => {
    const wrapper = mount(Home, {
      global: { stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } } },
    })
    await vi.waitFor(() => expect(wrapper.findAll('a[href="/household"]').length).toBeGreaterThanOrEqual(2))
    const visual = wrapper.find('.visual-node')
    expect(visual.element.tagName).toBe('A')
    expect(visual.attributes('aria-label')).toContain('家庭空间')
  })

  it('positions every API-provided visual node at a unique orbit angle', async () => {
    const wrapper = mount(Home, {
      global: {
        stubs: { RouterLink: { props: ['to'], template: '<a :href="to" :style="$attrs.style"><slot /></a>' } },
      },
    })
    await vi.waitFor(() => expect(wrapper.findAll('.visual-node')).toHaveLength(5))

    const positions = wrapper.findAll('.visual-node').map((node) => node.attributes('style') ?? '')
    expect(new Set(positions).size).toBe(5)
    expect(positions.every((style) => style.includes('--node-angle:'))).toBe(true)
    expect(positions.every((style) => style.includes('--node-radius:'))).toBe(true)
  })

  it('hides decorative visual nodes at mobile widths while preserving the state list', () => {
    expect(homeSource).toMatch(/@media\s*\(max-width:\s*900px\)[\s\S]*?\.visual-node\s*\{[^}]*display:\s*none/)
    expect(homeSource).not.toMatch(/@media\s*\(max-width:\s*900px\)[\s\S]*?\.state-list\s*\{[^}]*display:\s*none/)
  })

  it('clips the mobile orbit horizontally without disabling vertical page overflow', () => {
    expect(homeSource).toMatch(
      /@media\s*\(max-width:\s*900px\)[\s\S]*?\.home-page\s*\{[^}]*position:\s*relative[^}]*overflow-x:\s*clip[^}]*overflow-y:\s*visible/,
    )
  })

  it('surfaces real household daily state from /home/today', async () => {
    const wrapper = mount(Home, {
      global: { stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } } },
    })
    await vi.waitFor(() => expect(wrapper.text()).toContain('浇花'))
    expect(wrapper.text()).toContain('1')
    expect(wrapper.find('[data-testid="today-tasks"]').text()).toContain('浇花')
    expect(wrapper.find('[data-testid="today-notifications"]').text()).toContain('3')
  })

  it('keeps chat input enabled and shows knowledge degradation when only primary chat is ready', async () => {
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
    const wrapper = mount(Home, {
      global: { stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } } },
    })

    await vi.waitFor(() => expect(wrapper.text()).toContain('AI 已启用，知识增强未启用'))
    expect(wrapper.get('#home-request').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('.composer-row button').attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).not.toContain('AI 助手尚未启用')
  })
})
