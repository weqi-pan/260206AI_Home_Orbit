import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ErrorState from './ErrorState.vue'

describe('ErrorState', () => {
  it('以 alert 角色播报错误文案', () => {
    const wrapper = mount(ErrorState, { props: { message: 'UPSTREAM_UNAVAILABLE' } })
    // role="alert" 让读屏器立即播报，而不是让用户停在空白页
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('UPSTREAM_UNAVAILABLE')
  })

  it('点击重试抛出 retry 事件', async () => {
    const wrapper = mount(ErrorState, { props: { message: '加载失败' } })
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)
  })

  it('重试中禁用按钮并改变文案', () => {
    const wrapper = mount(ErrorState, { props: { message: '加载失败', retrying: true } })
    const button = wrapper.find('button')
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.text()).toBe('重试中…')
    // 连续点击不应重复触发
    void button.trigger('click')
    expect(wrapper.emitted('retry')).toBeUndefined()
  })
})
