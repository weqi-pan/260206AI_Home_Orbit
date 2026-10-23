import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import Household from './Household.vue'

const fetchHousehold = vi.fn()
const inviteMember = vi.fn().mockResolvedValue({ id: 'inv-1', username: 'newbie', role: 'member' })

vi.mock('../api/platform', () => ({
  fetchHousehold: () => fetchHousehold(),
  inviteMember: (...args: unknown[]) => inviteMember(...(args as [string, string, string])),
}))

const HOUSEHOLD = {
  id: 'h-1',
  name: '三个人的家',
  role: 'owner' as const,
  members: [
    { account_id: 'acc-1', username: 'owner', role: 'owner' as const },
    { account_id: 'acc-2', username: 'kid', role: 'member' as const },
  ],
}

describe('Household 家庭页', () => {
  it('渲染成员数量与角色，并给 owner 显示邀请表单', async () => {
    fetchHousehold.mockResolvedValueOnce(HOUSEHOLD)
    const wrapper = mount(Household)
    await flushPromises()
    expect(wrapper.find('h1').text()).toBe('三个人的家')
    expect(wrapper.findAll('.household-grid li')).toHaveLength(2)
    expect(wrapper.find('#invite-username').exists()).toBe(true)
  })

  it('非 owner/admin 不显示邀请表单', async () => {
    fetchHousehold.mockResolvedValueOnce({ ...HOUSEHOLD, role: 'viewer', members: HOUSEHOLD.members.slice(1) })
    const wrapper = mount(Household)
    await flushPromises()
    expect(wrapper.find('#invite-username').exists()).toBe(false)
    expect(wrapper.text()).toContain('只有家庭 owner 或 admin 可以创建邀请')
  })

  it('加载失败时显示可重试的错误块，而不是空白页', async () => {
    // 之前这里是 onMounted 直接 await，失败后整页停在「家庭空间」占位标题上
    fetchHousehold.mockRejectedValueOnce({ response: { data: { detail: 'HOUSEHOLD_NOT_FOUND' } } })
    const wrapper = mount(Household)
    await flushPromises()
    const banner = wrapper.find('[data-testid="page-error"]')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('HOUSEHOLD_NOT_FOUND')
    // 失败时不再渲染半张成员表
    expect(wrapper.find('.household-grid').exists()).toBe(false)

    fetchHousehold.mockResolvedValueOnce(HOUSEHOLD)
    await banner.find('button').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="page-error"]').exists()).toBe(false)
    expect(wrapper.find('h1').text()).toBe('三个人的家')
  })

  it('邀请成功后清空用户名并保留 household id 语义', async () => {
    fetchHousehold.mockResolvedValueOnce(HOUSEHOLD)
    const wrapper = mount(Household)
    await flushPromises()
    await wrapper.find('#invite-username').setValue('newbie')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(inviteMember).toHaveBeenCalledWith('h-1', 'newbie', 'member')
    expect((wrapper.find('#invite-username').element as HTMLInputElement).value).toBe('')
  })
})
