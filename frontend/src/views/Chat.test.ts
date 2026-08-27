import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import Chat from './Chat.vue'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('../api/chat', () => ({
  listSessions: () => Promise.resolve([{ id: 's1', title: '第一段会话' }]),
  listMessages: () => Promise.resolve([]), createSession: vi.fn(), deleteSession: vi.fn(), sendFeedback: vi.fn(), streamChat: vi.fn(),
}))

describe('mobile chat drawer', () => {
  it('opens with focus, closes after selection, and restores trigger focus', async () => {
    const wrapper = mount(Chat, { attachTo: document.body, global: { stubs: { ElButton: { template: '<button @click="$emit(\'click\')"><slot /></button>' }, ElIcon: true, ElEmpty: true, ElInput: true, ElTag: true, MessageItem: true }, directives: { loading: () => undefined } } })
    await vi.waitFor(() => expect(wrapper.find('[aria-label="打开会话列表"]').exists()).toBe(true))
    const trigger = wrapper.find<HTMLButtonElement>('[aria-label="打开会话列表"]')
    await trigger.trigger('click')
    expect(wrapper.find('[role="dialog"][aria-label="会话列表"]').exists()).toBe(true)
    expect(document.activeElement).toBe(wrapper.find('[aria-label="关闭会话列表"]').element)
    await wrapper.find('[data-session-id="s1"]').trigger('click')
    expect(wrapper.find('[role="dialog"][aria-label="会话列表"]').exists()).toBe(false)
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })
})
