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

  it('traps focus, closes on Escape, and makes main content inert while open', async () => {
    const wrapper = mount(AdminLayout, { attachTo: document.body, global: { stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' }, RouterView: true } } })
    const trigger = wrapper.find<HTMLButtonElement>('[aria-label="打开管理员导航"]')
    await trigger.trigger('click')
    const main = wrapper.find('#admin-main')
    expect(main.attributes('inert')).toBeDefined()
    expect(main.attributes('aria-hidden')).toBe('true')
    const close = wrapper.find<HTMLButtonElement>('[aria-label="关闭管理员导航"]')
    const actionButtons = wrapper.findAll<HTMLButtonElement>('.admin-actions button')
    const last = actionButtons[actionButtons.length - 1]
    last.element.focus()
    await last.trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(close.element)
    close.element.focus()
    await close.trigger('keydown', { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last.element)
    await close.trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })
})
