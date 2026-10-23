import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'
import Users from './Users.vue'
import { useAuthStore } from '../../stores/auth'
import type { AdminAccount, AdminHousehold } from '../../api/admin'

const fetchAccounts = vi.fn()
const fetchAdminHouseholds = vi.fn()
const updateAccount = vi.fn()

vi.mock('../../api/admin', () => ({
  fetchAccounts: () => fetchAccounts(),
  fetchAdminHouseholds: () => fetchAdminHouseholds(),
  updateAccount: (...args: unknown[]) => updateAccount(...args),
  resetAccountPassword: vi.fn(),
}))
vi.mock('../../api/ops', () => ({
  fetchHouseholdQuota: vi.fn(),
  adjustHouseholdQuota: vi.fn(),
}))

// 账号数据刻意覆盖两种角色与「需改密」两态：表里这两列都由三元 / v-if 决定，容易回归
const ACCOUNTS: AdminAccount[] = [
  {
    id: 'a-1',
    username: 'root',
    role: 'admin',
    status: 'active',
    user_id: null,
    must_change_password: false,
    created_at: '2026-10-01T08:00:00',
  },
  {
    id: 'a-2',
    username: 'kid',
    role: 'user',
    status: 'active',
    user_id: null,
    must_change_password: true,
    created_at: '2026-10-02T08:00:00',
  },
]
const HOUSEHOLDS: AdminHousehold[] = [
  {
    id: 'h-1',
    name: '三个人的家',
    created_at: '2026-10-01T08:00:00',
    members: [{ account_id: 'a-1', username: 'root', role: 'owner' }],
  },
]

let pinia: ReturnType<typeof createPinia>

function mountPage() {
  return mount(Users, { global: { plugins: [ElementPlus, pinia] } })
}

describe('admin/Users 账号管理', () => {
  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    fetchAccounts.mockResolvedValue(ACCOUNTS)
    fetchAdminHouseholds.mockResolvedValue(HOUSEHOLDS)
  })

  it('加载后同时渲染账号表与家庭表', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(fetchAccounts).toHaveBeenCalled()
    expect(fetchAdminHouseholds).toHaveBeenCalled()
    // 账号 2 行 + 家庭 1 行
    expect(wrapper.findAll('.el-table__row')).toHaveLength(3)
    expect(wrapper.text()).toContain('管理员')
    expect(wrapper.text()).toContain('普通用户')
    expect(wrapper.text()).toContain('三个人的家')
    expect(wrapper.text()).toContain('root（owner）')
  })

  it('按用户名过滤账号表，家庭表不受影响', async () => {
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.findAll('.el-table__row')).toHaveLength(3)

    await wrapper.find('input').setValue('kid')
    await flushPromises()

    // 账号表只剩 kid 一行，家庭表那行还在
    expect(wrapper.findAll('.el-table__row')).toHaveLength(2)
    expect(wrapper.text()).toContain('三个人的家')
  })

  it('加载失败时不渲染半张表，且 loading 复位', async () => {
    // 改造前这里是空 catch：接口挂了页面就是一片空白，也没有任何提示
    fetchAccounts.mockRejectedValueOnce({ response: { data: { detail: 'FORBIDDEN' } } })
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.findAll('.el-table__row')).toHaveLength(0)
    // 遮罩节点常驻（el-loading 用 v-show 控制），断言的是它已经隐藏
    expect(wrapper.find('.el-loading-mask').isVisible()).toBe(false)
  })

  it('当前登录账号不能禁用自己的账号', async () => {
    useAuthStore().account = {
      id: 'a-1',
      username: 'root',
      role: 'admin',
      status: 'active',
      must_change_password: false,
      user_id: null,
    }
    const wrapper = mountPage()
    await flushPromises()

    const rows = wrapper.findAll('.el-table__row')
    // 自己那一行的操作按钮全部禁用；别人那一行至少有一个可用
    expect(rows[0].findAll('button').every((btn) => btn.attributes('disabled') !== undefined)).toBe(true)
    expect(rows[1].findAll('button').some((btn) => btn.attributes('disabled') === undefined)).toBe(true)
  })
})
