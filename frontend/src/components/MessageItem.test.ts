import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import MessageItem from './MessageItem.vue'
import type { ChatUiMessage } from '../types'

function message(overrides: Partial<ChatUiMessage> = {}): ChatUiMessage {
  return {
    uid: 1,
    id: null,
    role: 'assistant',
    content: '```js\nconst a = 1\n```',
    sentiment: null,
    sentimentScore: null,
    feedback: null,
    ...overrides,
  }
}

const stubs = { ElIcon: true, ElTag: true, ElButton: { template: '<button><slot /></button>' } }

function mountItem(overrides: Partial<ChatUiMessage> = {}) {
  return mount(MessageItem, { props: { message: message(overrides) }, global: { stubs } })
}

describe('MessageItem markdown rendering', () => {
  it('流式期间跳过语法高亮，结束后补上', async () => {
    const wrapper = mountItem({ streaming: true })
    expect(wrapper.find('.md-body').html()).toContain('<pre class="hljs">')
    expect(wrapper.find('.md-body').html()).not.toContain('hljs-keyword')

    await wrapper.setProps({ message: message({ streaming: false, id: 7 }) })
    // streaming 变 false 是同一个 computed 的依赖变化，必须重新渲染成高亮版本
    expect(wrapper.find('.md-body').html()).toContain('hljs-keyword')
  })

  it('有内容时不再显示「正在思考」', () => {
    expect(mountItem({ streaming: true, content: '' }).find('.thinking').exists()).toBe(true)
    expect(mountItem({ streaming: true, content: '第一句' }).find('.thinking').exists()).toBe(false)
    expect(mountItem({ streaming: true, content: '第一句' }).find('.stream-cursor').exists()).toBe(true)
  })

  it('失败的气泡带 failed 类，且助手消息完成后才出现反馈按钮', () => {
    expect(mountItem({ failed: true, content: '出错了' }).find('.bubble').classes()).toContain('failed')
    expect(mountItem({ content: '进行中', streaming: true }).findAll('button')).toHaveLength(0)
    expect(mountItem({ content: '完成了', id: 9 }).findAll('button')).toHaveLength(2)
  })
})
