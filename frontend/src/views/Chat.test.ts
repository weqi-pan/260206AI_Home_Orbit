import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import Chat from './Chat.vue'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))

let streamCallbacks: {
  onToken: (token: string) => void
  onDone: (data: { message_id: number }) => void
  onError: (message: string) => void
} | undefined

vi.mock('../api/chat', () => ({
  listSessions: () => Promise.resolve([{ id: 's1', title: '第一段会话' }]),
  listMessages: () => Promise.resolve([]), createSession: vi.fn(), deleteSession: vi.fn(), sendFeedback: vi.fn(),
  streamChat: (_sessionId: string, _content: string, callbacks: typeof streamCallbacks) => {
    streamCallbacks = callbacks
    return Promise.resolve()
  },
}))

function mountChat() {
  return mount(Chat, { attachTo: document.body, global: { stubs: {
    ElButton: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
    ElInput: {
      props: ['modelValue'],
      template: '<textarea v-bind="$attrs" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    },
    ElIcon: true, ElEmpty: true, ElTag: true, MessageItem: true,
  }, directives: { loading: () => undefined } } })
}

describe('SSE streaming aria-live announcements', () => {
  it('announces only completed paragraphs and the final completion, never per-token', async () => {
    const wrapper = mountChat()
    const send = wrapper.find<HTMLTextAreaElement>('textarea')
    await send.setValue('讲个故事')
    await send.trigger('keydown', { key: 'Enter' })
    await vi.waitFor(() => expect(streamCallbacks).toBeDefined())

    const live = wrapper.find('[role="status"][aria-live="polite"][data-testid="stream-live"]')
    expect(live.exists()).toBe(true)

    streamCallbacks!.onToken('第一段开始，')
    streamCallbacks!.onToken('句子还在继续')
    await Promise.resolve()
    expect(live.text()).toBe('')  // 段落未完成：绝不逐 token 播报

    streamCallbacks!.onToken('\n\n')
    await Promise.resolve()
    expect(live.text()).toContain('第一段开始，句子还在继续')  // 段落完成：整段播报
    expect(live.text()).not.toContain('句子还在继续，')

    streamCallbacks!.onToken('第二段半途')
    await Promise.resolve()
    expect(live.text()).not.toContain('第二段半途')  // 未完成段落不播报

    streamCallbacks!.onDone({ message_id: 42 })
    await Promise.resolve()
    expect(live.text()).toContain('第二段半途')  // 完成时播报剩余段落
    expect(live.text()).toContain('回复完成')
    wrapper.unmount()
  })
})

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
