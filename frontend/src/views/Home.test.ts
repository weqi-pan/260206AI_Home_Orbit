import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import Home from './Home.vue'

vi.mock('../stores/auth', () => ({ useAuthStore: () => ({ account: { username: '测试用户' } }) }))
vi.mock('../api/platform', () => ({
  fetchOrbit: () => Promise.resolve({ nodes: [{ id: 'household', label: '家庭空间', kind: 'household', status: 'ready', href: '/household', detail: '1 位家庭成员' }] }),
  fetchSetupStatus: () => Promise.resolve({ ready: false, can_configure: false }),
}))

describe('Home Orbit', () => {
  it('renders each visual node as a named keyboard link matching the linear destination', async () => {
    const wrapper = mount(Home, { global: { stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } } } })
    await vi.waitFor(() => expect(wrapper.findAll('a[href="/household"]')).toHaveLength(2))
    const visual = wrapper.find('.visual-node')
    expect(visual.element.tagName).toBe('A')
    expect(visual.attributes('aria-label')).toContain('家庭空间')
  })
})
