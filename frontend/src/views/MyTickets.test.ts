import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import MyTickets from './MyTickets.vue'

const myTickets = vi.fn()
const createTicket = vi.fn().mockResolvedValue({ id: 2 })

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn().mockResolvedValue(undefined) }),
}))
vi.mock('../api/tickets', () => ({
  myTickets: () => myTickets(),
  createTicket: (...args: unknown[]) => createTicket(...args),
}))

const TICKET = {
  id: 1, session_id: null, title: '冰箱不制冷', summary: null, emotion: null,
  priority: 'normal' as const, status: 'open' as const, admin_reply: null,
  created_at: '2026-10-20T08:00:00', resolved_at: null,
}

const stubs = {
  ElButton: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
  ElTable: { props: ['data'], template: '<table><tbody><tr v-for="row in data" :key="row.id" class="row"><td>{{ row.title }}</td><td><slot /></td></tr></tbody></table>' },
  ElTableColumn: { template: '<div />' },
  ElTag: { template: '<span class="tag"><slot /></span>' },
  ElEmpty: { props: ['description'], template: '<p class="empty">{{ description }}</p>' },
  ElDialog: { template: '<div><slot /></div>' },
  ElDescriptions: true, ElDescriptionsItem: true, ElForm: true, ElFormItem: true, ElInput: true, ElSelect: true, ElOption: true,
}

function mountPage() {
  return mount(MyTickets, { global: { stubs, directives: { loading: () => undefined } } })
}

describe('MyTickets 我的工单', () => {
  it('渲染工单列表', async () => {
    myTickets.mockResolvedValueOnce([TICKET])
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.text()).toContain('冰箱不制冷')
  })

  it('加载失败时显示错误块而不是「暂无工单」', async () => {
    // 改造前是空 catch：接口 500 会被渲染成「暂无工单」，用户误以为自己没提过单
    myTickets.mockRejectedValueOnce({ response: { data: { detail: 'INTERNAL_ERROR' } } })
    const wrapper = mountPage()
    await flushPromises()

    const banner = wrapper.find('[data-testid="page-error"]')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('INTERNAL_ERROR')
    expect(wrapper.find('.empty').exists()).toBe(false)
  })

  it('重试成功后恢复列表', async () => {
    myTickets.mockRejectedValueOnce({ response: { data: { detail: 'INTERNAL_ERROR' } } })
    const wrapper = mountPage()
    await flushPromises()
    myTickets.mockResolvedValueOnce([TICKET])
    await wrapper.find('[data-testid="page-error"] button').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="page-error"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('冰箱不制冷')
  })
})
