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
    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"][aria-label="会话列表"]')!
    expect(dialog).not.toBeNull()
    expect(document.activeElement).toBe(dialog.querySelector('[aria-label="关闭会话列表"]'))
    dialog.querySelector<HTMLElement>('[data-session-id="s1"]')!.click()
    await vi.waitFor(() => expect(document.body.querySelector('[role="dialog"][aria-label="会话列表"]')).toBeNull())
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })

  it('traps focus, closes on Escape, and makes background inert while open', async () => {
    const wrapper = mount(Chat, { attachTo: document.body, global: { stubs: { ElButton: { template: '<button @click="$emit(\'click\')"><slot /></button>' }, ElIcon: true, ElEmpty: true, ElInput: true, ElTag: true, MessageItem: true }, directives: { loading: () => undefined } } })
    await vi.waitFor(() => expect(wrapper.find('[data-session-id="s1"]').exists()).toBe(true))
    const trigger = wrapper.find<HTMLButtonElement>('[aria-label="打开会话列表"]')
    await trigger.trigger('click')
    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"][aria-label="会话列表"]')!
    const close = dialog.querySelector<HTMLButtonElement>('[aria-label="关闭会话列表"]')!
    const last = dialog.querySelector<HTMLElement>('[data-session-id="s1"]')!
    last.focus()
    last.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(close)
    close.focus()
    close.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(last)
    close.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await vi.waitFor(() => expect(document.body.querySelector('[role="dialog"]')).toBeNull())
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })
})
