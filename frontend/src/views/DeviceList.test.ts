import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import DeviceList from './DeviceList.vue'

const searchDevices = vi.fn()
const restockNotify = vi.fn().mockResolvedValue({ id: 1 })

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('../api/devices', () => ({
  searchDevices: (...args: unknown[]) => searchDevices(...(args as [string])),
  restockNotify: (...args: unknown[]) => restockNotify(...(args as [string])),
}))

const stubs = {
  ElInput: { props: ['modelValue'], template: '<input :value="modelValue" />' },
  ElButton: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
  ElTag: { template: '<span class="tag"><slot /></span>' },
  ElTable: {
    props: ['data'],
    template:
      '<table><tbody><tr v-for="row in data" :key="row.model_id" class="row"><td>{{ row.model_id }}</td><td>{{ row.model_name }}</td><td><slot name="default" :row="row" /></td></tr></tbody></table>',
  },
  ElTableColumn: { template: '<div />' },
  ElEmpty: { props: ['description'], template: '<p class="empty">{{ description }}</p>' },
}

function mountPage() {
  return mount(DeviceList, { global: { stubs, directives: { loading: () => undefined } } })
}

describe('DeviceList 设备列表', () => {
  it('挂载时按空关键词拉取设备', async () => {
    searchDevices.mockResolvedValueOnce([
      { model_id: 'm-1', model_name: '扫地机器人', category: '清洁', total_stock: 3 },
    ])
    const wrapper = mountPage()
    await flushPromises()
    expect(searchDevices).toHaveBeenCalledWith('')
    expect(wrapper.text()).toContain('扫地机器人')
  })

  it('搜索失败时显示错误块并可重试，而不是当成「未找到匹配设备」', async () => {
    // 改造前这里是空 catch：接口报错后表格留空，只剩「未找到匹配设备」
    searchDevices.mockRejectedValueOnce({ response: { data: { detail: 'UPSTREAM_UNAVAILABLE' } } })
    const wrapper = mountPage()
    await flushPromises()

    const banner = wrapper.find('[data-testid="page-error"]')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('UPSTREAM_UNAVAILABLE')
    expect(wrapper.find('.empty').exists()).toBe(false)

    searchDevices.mockResolvedValueOnce([
      { model_id: 'm-2', model_name: '空气净化器', category: '清洁', total_stock: 0 },
    ])
    await banner.find('button').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="page-error"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('空气净化器')
  })

  it('正常返回空列表时才显示空态', async () => {
    searchDevices.mockResolvedValueOnce([])
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.find('.empty').text()).toBe('未找到匹配设备')
  })
})
