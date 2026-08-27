import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import AdminLayout from './AdminLayout.vue'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('../../stores/auth', () => ({ useAuthStore: () => ({ account: { username: 'admin' }, logout: vi.fn() }) }))

describe('mobile admin drawer', () => {
  it('contains navigation, return and logout actions with focus restoration', async () => {
    const wrapper = mount(AdminLayout, { attachTo: document.body, global: { stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' }, RouterView: true } } })
    const trigger = wrapper.find<HTMLButtonElement>('[aria-label="打开管理员导航"]')
    expect(trigger.exists()).toBe(true)
    await trigger.trigger('click')
    const drawer = wrapper.find('[role="dialog"][aria-label="管理员导航"]')
    expect(drawer.exists()).toBe(true)
    expect(drawer.text()).toContain('返回用户端')
    expect(drawer.text()).toContain('退出 admin')
    expect(document.activeElement).toBe(wrapper.find('[aria-label="关闭管理员导航"]').element)
    await wrapper.find('[aria-label="关闭管理员导航"]').trigger('click')
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })
})
