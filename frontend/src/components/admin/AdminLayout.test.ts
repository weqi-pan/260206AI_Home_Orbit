import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import AdminLayout from './AdminLayout.vue'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('../../stores/auth', () => ({ useAuthStore: () => ({ account: { username: 'admin' }, logout: vi.fn() }) }))

describe('desktop admin layout', () => {
  it('places the sidebar and main shell in the same grid row', () => {
    const wrapper = mount(AdminLayout, {
      attachTo: document.body,
      global: { stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' }, RouterView: true } },
    })
    const aside = wrapper.find('.admin-aside')
    const shell = wrapper.find('.admin-shell-background')

    expect(aside.exists()).toBe(true)
    expect(shell.find('#admin-main').exists()).toBe(true)
    expect(getComputedStyle(aside.element).gridColumn).toBe('1')
    expect(getComputedStyle(aside.element).gridRow).toBe('1')
    expect(getComputedStyle(shell.element).gridColumn).toBe('2')
    expect(getComputedStyle(shell.element).gridRow).toBe('1')
    wrapper.unmount()
  })
})

describe('mobile admin drawer', () => {
  it('contains navigation, return and logout actions with focus restoration', async () => {
    const wrapper = mount(AdminLayout, {
      attachTo: document.body,
      global: { stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' }, RouterView: true } },
    })
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
    const wrapper = mount(AdminLayout, {
      attachTo: document.body,
      global: { stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' }, RouterView: true } },
    })
    const trigger = wrapper.find<HTMLButtonElement>('[aria-label="打开管理员导航"]')
    await trigger.trigger('click')
    const background = wrapper.find('.admin-shell-background')
    expect(background.attributes('inert')).toBeDefined()
    expect(background.attributes('aria-hidden')).toBe('true')
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

  it('isolates every non-modal shell control and restores the shell after close', async () => {
    const wrapper = mount(AdminLayout, {
      attachTo: document.body,
      global: { stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' }, RouterView: true } },
    })
    const trigger = wrapper.find<HTMLButtonElement>('[aria-label="打开管理员导航"]')
    await trigger.trigger('click')
    const background = wrapper.find('.admin-shell-background')
    expect(background.attributes('inert')).toBeDefined()
    expect(background.attributes('aria-hidden')).toBe('true')
    expect(background.find('.skip-link').exists()).toBe(true)
    expect(background.find('[aria-label="打开管理员导航"]').exists()).toBe(true)
    expect(background.find('#admin-main').exists()).toBe(true)
    await wrapper.find('[aria-label="关闭管理员导航"]').trigger('click')
    expect(background.attributes('inert')).toBeUndefined()
    expect(background.attributes('aria-hidden')).toBeUndefined()
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })
})
