import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { defineComponent, nextTick } from 'vue'
import { useRequest } from './useRequest'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

const apiError = (message: string) => ({ response: { data: { detail: message } } })

describe('useRequest', () => {
  it('成功时写入 data 并清空 loading', async () => {
    const { data, loading, error, run } = useRequest(() => Promise.resolve(['a']), { initial: [] })
    const pending = run()
    expect(loading.value).toBe(true)
    await pending
    expect(data.value).toEqual(['a'])
    expect(loading.value).toBe(false)
    expect(error.value).toBe('')
  })

  it('失败时把稳定错误码提出来，且不再抛给调用方', async () => {
    const { error, loading, run } = useRequest<void>(() => Promise.reject(apiError('HOUSEHOLD_NOT_FOUND')))
    await expect(run()).resolves.toBeUndefined()
    expect(error.value).toBe('HOUSEHOLD_NOT_FOUND')
    expect(loading.value).toBe(false)
  })

  it('无响应体的网络错误回退到 Error.message，再回退到默认文案', async () => {
    const network = useRequest<void>(() => Promise.reject(new Error('Network Error')))
    await network.run()
    expect(network.error.value).toBe('Network Error')

    const unknown = useRequest<void>(() => Promise.reject('boom'))
    await unknown.run()
    expect(unknown.error.value).toBe('加载失败，请重试')
  })

  it('重试成功后清掉上一次的错误', async () => {
    let attempt = 0
    const { data, error, run } = useRequest(
      () => (++attempt === 1 ? Promise.reject(apiError('UPSTREAM_UNAVAILABLE')) : Promise.resolve('ok')),
    )
    await run()
    expect(error.value).toBe('UPSTREAM_UNAVAILABLE')
    await run()
    expect(error.value).toBe('')
    expect(data.value).toBe('ok')
  })

  it('并发重试时只采用最后一次请求的结果', async () => {
    const first = deferred<string>()
    const second = deferred<string>()
    const responses = [first.promise, second.promise]
    const { data, loading, run } = useRequest(() => responses.shift()!)

    const firstRun = run()
    const secondRun = run()
    second.resolve('第二次')
    await secondRun
    first.resolve('第一次')
    await firstRun

    // 先发的旧响应晚到，不能覆盖新数据
    expect(data.value).toBe('第二次')
    expect(loading.value).toBe(false)
  })

  it('immediate 在挂载时自动请求一次', async () => {
    const fetcher = vi.fn().mockResolvedValue('mounted')
    const wrapper = mount(defineComponent({
      setup() {
        const state = useRequest(fetcher, { immediate: true })
        return () => state.data.value ?? ''
      },
    }))
    // mount 会同步跑完 mounted 钩子，所以此刻请求已经发出
    expect(fetcher).toHaveBeenCalledTimes(1)
    await vi.waitFor(() => expect(wrapper.text()).toBe('mounted'))
    await nextTick()
    expect(fetcher).toHaveBeenCalledTimes(1)  // 只发一次，没有重复请求
    wrapper.unmount()
  })
})
