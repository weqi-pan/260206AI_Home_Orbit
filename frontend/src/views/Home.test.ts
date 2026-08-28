import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import Home from './Home.vue'

vi.mock('../stores/auth', () => ({ useAuthStore: () => ({ account: { username: '测试用户' } }) }))
vi.mock('../api/platform', () => ({
  fetchOrbit: () => Promise.resolve({ nodes: [
    { id: 'household', label: '家庭空间', kind: 'household', status: 'ready', href: '/household', detail: '1 位家庭成员' },
    { id: 'tasks', label: '今日任务', kind: 'tasks', status: 'attention', href: '/household', detail: '浇花' },
  ] }),
  fetchSetupStatus: () => Promise.resolve({ ready: false, can_configure: false }),
  fetchHomeToday: () => Promise.resolve({
    date: '2026-08-28',
    tasks: [{ id: 't1', title: '浇花', description: '', status: 'open', due_at: null }],
    reminders: [{ id: 'r1', task_id: 't1', interval_minutes: 60, next_run_at: null, active: true }],
    shopping: { total: 2, unchecked: 1, checked: 1 },
    notifications: { unread: 3 },
    anomalies: [],
  }),
}))

describe('Home Orbit', () => {
  it('renders each visual node as a named keyboard link matching the linear destination', async () => {
    const wrapper = mount(Home, { global: { stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } } } })
    await vi.waitFor(() => expect(wrapper.findAll('a[href="/household"]').length).toBeGreaterThanOrEqual(2))
    const visual = wrapper.find('.visual-node')
    expect(visual.element.tagName).toBe('A')
    expect(visual.attributes('aria-label')).toContain('家庭空间')
  })

  it('surfaces real household daily state from /home/today', async () => {
    const wrapper = mount(Home, { global: { stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } } } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('浇花'))
    expect(wrapper.text()).toContain('1')
    expect(wrapper.find('[data-testid="today-tasks"]').text()).toContain('浇花')
    expect(wrapper.find('[data-testid="today-notifications"]').text()).toContain('3')
  })
})
