import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import AppLayout from './AppLayout.vue'
import Chat from '../views/Chat.vue'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))
vi.mock('../stores/auth', () => ({ useAuthStore: () => ({ account: { username: 'user', role: 'user' }, logout: vi.fn() }) }))
vi.mock('../api/chat', () => ({
  listSessions: () => Promise.resolve([{ id: 's1', title: '第一段会话' }]),
  listMessages: () => Promise.resolve([]), createSession: vi.fn(), deleteSession: vi.fn(), sendFeedback: vi.fn(), streamChat: vi.fn(),
}))

describe('user shell modal isolation', () => {
  it('isolates the full shell while the Chat drawer is open and restores it after close', async () => {
    const wrapper = mount(AppLayout, {
      attachTo: document.body,
      global: {
        stubs: {
          RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
          RouterView: Chat,
          ElButton: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
          ElIcon: true, ElEmpty: true, ElInput: true, ElTag: true, MessageItem: true,
        },
        directives: { loading: () => undefined },
      },
    })
    await vi.waitFor(() => expect(wrapper.find('[data-session-id="s1"]').exists()).toBe(true))
    const trigger = wrapper.find<HTMLButtonElement>('[aria-label="打开会话列表"]')
    await trigger.trigger('click')
    const background = wrapper.find('.app-shell-background')
    expect(background.attributes('inert')).toBeDefined()
    expect(background.attributes('aria-hidden')).toBe('true')
    expect(background.find('.app-topbar').exists()).toBe(true)
    expect(background.find('.bottom-nav').exists()).toBe(true)
    expect(document.body.querySelector('[role="dialog"][aria-label="会话列表"]')).not.toBeNull()
    const close = document.body.querySelector<HTMLButtonElement>('[aria-label="关闭会话列表"]')!
    close.click()
    await vi.waitFor(() => expect(background.attributes('inert')).toBeUndefined())
    expect(background.attributes('aria-hidden')).toBeUndefined()
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })
})
