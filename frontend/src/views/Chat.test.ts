import { mount } from '@vue/test-utils'
import { defineComponent, h, watch, type Component, type PropType } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Chat from './Chat.vue'
import type { ChatUiMessage } from '../types'

// 全量跑测时 25 个 jsdom 环境并行启动，负载高峰下 waitFor 默认的 1s 超时会偶发超时
const waitFor = (fn: () => void | Promise<void>) => vi.waitFor(fn, { timeout: 5000 })

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))

let streamCallbacks:
  | {
      onToken: (token: string) => void
      onDone: (data: { message_id: number }) => void
      onError: (message: string) => void
      onApprovalRequired: (data: { approval_id: string; code?: string }) => void
      onRun: (data: { status?: string }) => void
    }
  | undefined
let streamSignal: AbortSignal | undefined
// 由用例决定流何时结束，便于在“流式中”这一状态上做断言
let finishStream: (() => void) | undefined
let finishResume: (() => void) | undefined
let resumeCallbacks: typeof streamCallbacks
let resumeCalls: string[] = []
let approveCalls: string[] = []
let rejectCalls: string[] = []
let pendingApprovals: unknown[] = []

vi.mock('../api/chat', () => ({
  listSessions: () => Promise.resolve([{ id: 's1', title: '第一段会话' }]),
  listMessages: () => Promise.resolve([]),
  createSession: vi.fn(),
  deleteSession: vi.fn(),
  sendFeedback: vi.fn(),
  streamChat: (_sessionId: string, _content: string, callbacks: typeof streamCallbacks, signal?: AbortSignal) => {
    streamCallbacks = callbacks
    streamSignal = signal
    return new Promise<void>((resolve) => {
      finishStream = resolve
    })
  },
  resumeApproval: (approvalId: string, callbacks: typeof streamCallbacks) => {
    resumeCalls.push(approvalId)
    resumeCallbacks = callbacks
    // 和 streamChat 一样挂住：审批后的这一轮也是流式的，
    // 立刻 resolve 会让 finally 先把 streamingMessage 清掉，测不到后续 token 落在哪
    return new Promise<void>((resolve) => {
      finishResume = resolve
    })
  },
  listPendingApprovals: () => Promise.resolve(pendingApprovals),
  approveApproval: (approvalId: string) => {
    approveCalls.push(approvalId)
    return Promise.resolve({
      id: approvalId,
      status: 'approved',
      expires_at: new Date(Date.now() + 600000).toISOString(),
    })
  },
  rejectApproval: (approvalId: string) => {
    rejectCalls.push(approvalId)
    return Promise.resolve({ id: approvalId, status: 'rejected' })
  },
}))

function pendingApproval(id = 'ap-1') {
  return {
    id,
    run_id: 'run-1',
    tool_id: 'tool-1',
    status: 'pending',
    expires_at: null,
    created_at: null,
    tool_key: 'platform.test_high_risk',
    tool_display_name: '高风险测试工具',
    risk_level: 'high',
    arguments: { value: '清洗油烟机' },
    session_id: 's1',
    deployment_id: 'dep-1',
  }
}

beforeEach(() => {
  streamCallbacks = undefined
  resumeCallbacks = undefined
  finishStream = undefined
  finishResume = undefined
  resumeCalls = []
  approveCalls = []
  rejectCalls = []
  pendingApprovals = []
})

// 记录 MessageItem 收到的每一次 content，用来确认 token 是按帧合并的
function mountChat(messageItem: Component | boolean = true) {
  return mount(Chat, {
    attachTo: document.body,
    global: {
      stubs: {
        ElButton: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
        ElInput: {
          props: ['modelValue'],
          template:
            '<textarea v-bind="$attrs" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
        },
        ElIcon: true,
        ElEmpty: true,
        ElTag: true,
        MessageItem: messageItem,
      },
      directives: { loading: () => undefined },
    },
  })
}

async function sendFirstMessage(wrapper: ReturnType<typeof mountChat>) {
  const send = wrapper.find<HTMLTextAreaElement>('textarea')
  await send.setValue('讲个故事')
  await send.trigger('keydown', { key: 'Enter' })
  await waitFor(() => expect(streamCallbacks).toBeDefined())
}

// 让滚动容器在 jsdom 里报告真实尺寸：默认 scrollHeight / clientHeight 都是 0
function sizeScrollContainer(el: HTMLElement, scrollHeight: number, clientHeight: number) {
  Object.defineProperty(el, 'scrollHeight', { value: scrollHeight, configurable: true })
  Object.defineProperty(el, 'clientHeight', { value: clientHeight, configurable: true })
}

function nextFrame() {
  return new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
}

describe('SSE streaming aria-live announcements', () => {
  it('announces only completed paragraphs and the final completion, never per-token', async () => {
    const wrapper = mountChat()
    await sendFirstMessage(wrapper)

    const live = wrapper.find('[role="status"][aria-live="polite"][data-testid="stream-live"]')
    expect(live.exists()).toBe(true)

    streamCallbacks!.onToken('第一段开始，')
    streamCallbacks!.onToken('句子还在继续')
    await Promise.resolve()
    expect(live.text()).toBe('') // 段落未完成：绝不逐 token 播报

    streamCallbacks!.onToken('\n\n')
    await Promise.resolve()
    expect(live.text()).toContain('第一段开始，句子还在继续') // 段落完成：整段播报
    expect(live.text()).not.toContain('句子还在继续，')

    streamCallbacks!.onToken('第二段半途')
    await Promise.resolve()
    expect(live.text()).not.toContain('第二段半途') // 未完成段落不播报

    streamCallbacks!.onDone({ message_id: 42 })
    await Promise.resolve()
    expect(live.text()).toContain('第二段半途') // 完成时播报剩余段落
    expect(live.text()).toContain('回复完成')
    wrapper.unmount()
  })
})

describe('streaming render batching', () => {
  it('merges tokens that arrive in the same frame into a single content update', async () => {
    const updates: string[] = []
    const RecordingMessageItem = defineComponent({
      props: { message: { type: Object as PropType<ChatUiMessage>, required: true } },
      setup(props) {
        watch(
          () => props.message.content,
          (value) => {
            if (props.message.role === 'assistant') updates.push(value)
          },
          { immediate: true },
        )
        return () => h('div', props.message.content)
      },
    })
    const wrapper = mountChat(RecordingMessageItem)
    await sendFirstMessage(wrapper)
    const state = wrapper.vm as unknown as { messages: ChatUiMessage[] }
    expect(updates).toEqual(['']) // 助手气泡挂载，内容为空

    streamCallbacks!.onToken('你')
    streamCallbacks!.onToken('好')
    streamCallbacks!.onToken('呀')
    // 同一帧内：token 只在缓冲里，响应式状态还没被写过
    expect(state.messages[1].content).toBe('')
    expect(updates).toEqual([''])

    await nextFrame()
    expect(state.messages[1].content).toBe('你好呀')
    expect(updates).toEqual(['', '你好呀']) // 三个 token 合并成一次写入

    streamCallbacks!.onToken('！')
    await nextFrame()
    expect(updates).toEqual(['', '你好呀', '你好呀！'])
    wrapper.unmount()
  })

  it('flushes buffered tokens before marking the message complete', async () => {
    const wrapper = mountChat()
    await sendFirstMessage(wrapper)
    streamCallbacks!.onToken('还没到帧')
    streamCallbacks!.onDone({ message_id: 7 })
    // onDone 必须先把缓冲写回，否则最后一批 token 会永久丢失
    const state = wrapper.vm as unknown as { messages: ChatUiMessage[] }
    expect(state.messages[1].content).toBe('还没到帧')
    expect(state.messages[1].streaming).toBe(true) // 流本身尚未结束
    finishStream!()
    await nextFrame()
    expect(state.messages[1].streaming).toBe(false)
    wrapper.unmount()
  })
})

describe('message list keying', () => {
  it('keeps the frontend uid stable when the server id is filled in on done', async () => {
    const wrapper = mountChat()
    await sendFirstMessage(wrapper)
    const state = wrapper.vm as unknown as { messages: ChatUiMessage[] }
    const before = state.messages.map((message) => message.uid)
    expect(new Set(before).size).toBe(before.length) // uid 唯一
    expect(state.messages[1].uid).toBeDefined()

    streamCallbacks!.onToken('内容')
    streamCallbacks!.onDone({ message_id: 42 })
    // key 换成 server id 会让整个列表重挂载；uid 必须保持不变
    expect(state.messages.map((message) => message.uid)).toEqual(before)
    expect(state.messages[1].id).toBe(42)
    wrapper.unmount()
  })

  it('gives sequentially loaded messages distinct uids', async () => {
    const wrapper = mountChat()
    await waitFor(() => expect(wrapper.find('[data-session-id="s1"]').exists()).toBe(true))
    const state = wrapper.vm as unknown as { messages: ChatUiMessage[] }
    await sendFirstMessage(wrapper)
    await waitFor(() => expect(state.messages.length).toBe(2))
    const [user, assistant] = state.messages
    expect(user.uid).not.toBe(assistant.uid)
    wrapper.unmount()
  })
})

describe('scroll follow behaviour', () => {
  it('stops following new tokens once the user scrolls up', async () => {
    const wrapper = mountChat()
    await sendFirstMessage(wrapper)
    const list = wrapper.find<HTMLElement>('.message-list')
    sizeScrollContainer(list.element, 1000, 400)
    list.element.scrollTop = 600 // 贴底
    await list.trigger('scroll')

    streamCallbacks!.onToken('贴底时的增量')
    await nextFrame()
    expect(list.element.scrollTop).toBe(1000) // 贴底时继续跟随

    list.element.scrollTop = 100 // 用户向上翻阅历史
    await list.trigger('scroll')
    streamCallbacks!.onToken('翻历史时的增量')
    await nextFrame()
    expect(list.element.scrollTop).toBe(100) // 不被拽回底部
    wrapper.unmount()
  })
})

describe('unmount cleanup', () => {
  it('aborts the in-flight stream when the view unmounts', async () => {
    const wrapper = mountChat()
    await sendFirstMessage(wrapper)
    expect(streamSignal?.aborted).toBe(false)
    wrapper.unmount()
    expect(streamSignal?.aborted).toBe(true)
  })
})

describe('mobile chat drawer', () => {
  it('opens with focus, closes after selection, and restores trigger focus', async () => {
    const wrapper = mount(Chat, {
      attachTo: document.body,
      global: {
        stubs: {
          ElButton: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
          ElIcon: true,
          ElEmpty: true,
          ElInput: true,
          ElTag: true,
          MessageItem: true,
        },
        directives: { loading: () => undefined },
      },
    })
    await waitFor(() => expect(wrapper.find('[aria-label="打开会话列表"]').exists()).toBe(true))
    const trigger = wrapper.find<HTMLButtonElement>('[aria-label="打开会话列表"]')
    await trigger.trigger('click')
    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"][aria-label="会话列表"]')!
    expect(dialog).not.toBeNull()
    expect(document.activeElement).toBe(dialog.querySelector('[aria-label="关闭会话列表"]'))
    dialog.querySelector<HTMLElement>('[data-session-id="s1"]')!.click()
    await waitFor(() => expect(document.body.querySelector('[role="dialog"][aria-label="会话列表"]')).toBeNull())
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })

  it('traps focus, closes on Escape, and makes background inert while open', async () => {
    const wrapper = mount(Chat, {
      attachTo: document.body,
      global: {
        stubs: {
          ElButton: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
          ElIcon: true,
          ElEmpty: true,
          ElInput: true,
          ElTag: true,
          MessageItem: true,
        },
        directives: { loading: () => undefined },
      },
    })
    await waitFor(() => expect(wrapper.find('[data-session-id="s1"]').exists()).toBe(true))
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
    await waitFor(() => expect(document.body.querySelector('[role="dialog"]')).toBeNull())
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })
})

describe('高风险管理审批卡', () => {
  // 不 stub MessageItem：审批卡是通过它的 approvals 插槽渲染的，
  // stub 掉插槽内容就整段消失，用例会测不到任何东西
  function mountWithApprovals() {
    return mount(Chat, {
      attachTo: document.body,
      global: {
        stubs: {
          ElButton: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
          ElInput: {
            props: ['modelValue'],
            template:
              '<textarea v-bind="$attrs" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
          },
          ElIcon: true,
          ElEmpty: true,
          ElTag: true,
        },
        directives: { loading: () => undefined },
      },
    })
  }

  it('renders a card when the stream asks for approval, then resumes into the same bubble', async () => {
    pendingApprovals = [pendingApproval()]
    const wrapper = mountWithApprovals()
    await sendFirstMessage(wrapper)
    const state = wrapper.vm as unknown as { messages: ChatUiMessage[] }

    streamCallbacks!.onApprovalRequired({ approval_id: 'ap-1', code: 'APPROVAL_REQUIRED' })

    await waitFor(() => expect(wrapper.find('.approval-card').exists()).toBe(true))
    // 卡片要能让人判断：工具名、风险等级、脱敏后的参数
    expect(wrapper.text()).toContain('高风险测试工具')
    expect(wrapper.text()).toContain('高风险副作用')
    expect(wrapper.text()).toContain('清洗油烟机')

    const approveButton = wrapper.findAll('button').find((item) => item.text().includes('批准这一次'))!
    await approveButton.trigger('click')

    await waitFor(() => expect(resumeCalls).toEqual(['ap-1']))
    // 后续 token 必须落回**同一个**助手气泡，而不是新开一条
    resumeCallbacks!.onToken('执行结果')
    await waitFor(() => expect(state.messages[1].content).toContain('执行结果'))
    expect(state.messages.length).toBe(2)

    // 审批后的流以 run 帧收尾（没有 done），剩余段落也要播报出去
    resumeCallbacks!.onRun({ status: 'completed' })
    await waitFor(() => expect(wrapper.find('[data-testid="stream-live"]').text()).toContain('回复完成'))
    finishResume!()
    wrapper.unmount()
  })

  it('shows the server-decided window instead of trusting the client', async () => {
    pendingApprovals = [pendingApproval()]
    const wrapper = mountWithApprovals()
    await sendFirstMessage(wrapper)

    streamCallbacks!.onApprovalRequired({ approval_id: 'ap-1' })
    await waitFor(() => expect(wrapper.find('.approval-card').exists()).toBe(true))
    await wrapper
      .findAll('button')
      .find((item) => item.text().includes('批准这一次'))!
      .trigger('click')

    // 倒计时用的是 approve 响应里的 expires_at（已由服务端夹过），不是前端自己算的
    await waitFor(() => expect(wrapper.text()).toContain('已授权，剩余'))
    wrapper.unmount()
  })

  it('rejects without resuming the run', async () => {
    pendingApprovals = [pendingApproval()]
    const wrapper = mountWithApprovals()
    await sendFirstMessage(wrapper)

    streamCallbacks!.onApprovalRequired({ approval_id: 'ap-1' })
    await waitFor(() => expect(wrapper.find('.approval-card').exists()).toBe(true))
    await wrapper
      .findAll('button')
      .find((item) => item.text() === '拒绝')!
      .trigger('click')

    await waitFor(() => expect(rejectCalls).toEqual(['ap-1']))
    expect(resumeCalls).toEqual([])
    await waitFor(() => expect(wrapper.text()).toContain('已拒绝'))
    wrapper.unmount()
  })
})
