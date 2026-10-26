import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import AppLayout from '../components/AppLayout.vue'
import Home from '../views/Home.vue'

// 全量跑测时 25 个 jsdom 环境并行启动，负载高峰下 waitFor 默认的 1s 超时会偶发超时
const waitFor = (fn: () => void | Promise<void>) => vi.waitFor(fn, { timeout: 5000 })

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))
vi.mock('../stores/auth', () => ({
  useAuthStore: () => ({ account: { username: '验收用户', role: 'user' }, logout: vi.fn() }),
}))
vi.mock('../api/platform', () => ({
  fetchOrbit: () =>
    Promise.resolve({
      nodes: [
        {
          id: 'household',
          label: '家庭空间',
          kind: 'household',
          status: 'ready',
          href: '/household',
          detail: '1 位家庭成员',
        },
        { id: 'tasks', label: '今日任务', kind: 'tasks', status: 'attention', href: '/household', detail: '浇花' },
      ],
    }),
  fetchSetupStatus: () => Promise.resolve({ ready: false, can_configure: false }),
  fetchHomeToday: () =>
    Promise.resolve({
      date: '2026-08-28',
      tasks: [],
      reminders: [],
      shopping: { total: 0, unchecked: 0, checked: 0 },
      notifications: { unread: 0 },
      anomalies: [],
    }),
}))

const globalCss = readFileSync(resolve(__dirname, '../styles/global.css'), 'utf-8')
const srcRoot = resolve(__dirname, '..')

function viewSources(subdir: string): string {
  const dir = resolve(srcRoot, subdir)
  return readdirSync(dir)
    .filter((name) => name.endsWith('.vue'))
    .map((name) => readFileSync(resolve(dir, name), 'utf-8'))
    .join('\n')
}

const routerStubs = { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } }

describe('Phase 9 acceptance: accessibility foundation', () => {
  it('defines the exact Home Orbit design tokens without dilution', () => {
    // 大小写不敏感：Prettier 会把十六进制统一成小写，色值本身不变
    const css = globalCss.toLowerCase()
    for (const token of ['#E8EAF5', '#211D4F', '#4B4297', '#FF735F', '#FBFAFF']) {
      expect(css).toContain(token.toLowerCase())
    }
  })

  it('guarantees 44px touch targets and visible focus-visible globally', () => {
    expect(globalCss).toMatch(/button,\s*a,\s*input,\s*select\s*\{\s*min-height:\s*44px/)
    expect(globalCss).toContain(':focus-visible')
    expect(globalCss).toMatch(/outline:\s*3px solid var\(--charge-coral\)/)
  })

  it('stops non-essential animation under prefers-reduced-motion', () => {
    expect(globalCss).toContain('@media (prefers-reduced-motion: reduce)')
    // Prettier 会把 `.01ms` 补成 `0.01ms`，两种写法等价，都接受
    expect(globalCss).toMatch(/animation-duration:\s*0?\.01ms\s*!important/)
    expect(globalCss).toMatch(/transition-duration:\s*0?\.01ms\s*!important/)
    // 轨道装饰不得自带无限动画（全局规则之外的运动都是违规）
    const home = readFileSync(resolve(srcRoot, 'views/Home.vue'), 'utf-8')
    expect(home).not.toMatch(/animation:\s*[^;]*infinite/)
  })

  it('provides a functional skip link to the main landmark', async () => {
    const wrapper = mount(AppLayout, {
      attachTo: document.body,
      global: { stubs: { ...routerStubs, RouterView: { template: '<div />' } } },
    })
    const skip = wrapper.find('a.skip-link')
    expect(skip.exists()).toBe(true)
    expect(skip.attributes('href')).toBe('#main-content')
    const main = wrapper.find('#main-content')
    expect(main.exists()).toBe(true)
    expect(main.attributes('tabindex')).toBe('-1')
    wrapper.unmount()
  })

  it('keeps the mobile bottom navigation in the shell', async () => {
    const wrapper = mount(AppLayout, {
      attachTo: document.body,
      global: { stubs: { ...routerStubs, RouterView: { template: '<div />' } } },
    })
    const bottom = wrapper.find('nav.bottom-nav[aria-label="移动端主要导航"]')
    expect(bottom.exists()).toBe(true)
    expect(bottom.findAll('a').length).toBeGreaterThanOrEqual(5)
    wrapper.unmount()
  })

  it('associates auth form inputs with their labels', () => {
    for (const name of ['Login.vue', 'Register.vue']) {
      const source = readFileSync(resolve(srcRoot, 'views', name), 'utf-8')
      const items = source.match(/<el-form-item[^>]*>/g) ?? []
      expect(items.length).toBeGreaterThan(0)
      for (const item of items) {
        expect(item).toMatch(/label="[^"]+"/) // 每个表单项都有可见标签
      }
    }
  })
})

describe('Phase 9 acceptance: Home Orbit visual constraints', () => {
  it('renders the large orbit with real-state nodes and a linear equivalent on Home', async () => {
    const wrapper = mount(Home, { global: { stubs: routerStubs } })
    await waitFor(() => expect(wrapper.findAll('.visual-node').length).toBe(2))
    expect(wrapper.find('.orbit-stage').exists()).toBe(true)
    expect(wrapper.find('.orbit-outer').exists()).toBe(true) // 大轨道仅首页/Agent运行页
    const linear = wrapper.find('section.state-list')
    expect(linear.exists()).toBe(true) // 必须有等价线性列表
    expect(linear.findAll('a').length).toBe(2)
    wrapper.unmount()
  })

  it('keeps admin and secondary views free of the large orbit', () => {
    const adminSources = viewSources('views/admin')
    expect(adminSources).not.toContain('orbit-outer')
    expect(adminSources).not.toContain('orbit-stage')
    const secondary = ['Discover.vue', 'MyAgents.vue', 'Creator.vue', 'Household.vue']
      .map((name) => readFileSync(resolve(srcRoot, 'views', name), 'utf-8'))
      .join('\n')
    expect(secondary).not.toContain('orbit-outer')
    expect(secondary).not.toContain('orbit-stage')
  })
})
