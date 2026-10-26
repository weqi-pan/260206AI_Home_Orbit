import { onMounted, ref, shallowRef, type Ref, type ShallowRef } from 'vue'
import { extractErrorMessage } from '../api/http'

export interface UseRequestOptions<T> {
  /** data 的初始值：用于模板里安全地读取 .length 等字段 */
  initial?: T
  /** 传入 true 时在 setup 阶段自动执行一次（等价于 onMounted(run)） */
  immediate?: boolean
}

export interface UseRequestResult<T> {
  data: ShallowRef<T | undefined>
  loading: Ref<boolean>
  /** 失败文案；空串表示没有错误，页面据此决定是否显示错误块 */
  error: Ref<string>
  run: () => Promise<void>
}

function resolveErrorMessage(error: unknown, fallback: string): string {
  const response = (error as { response?: { data?: unknown } } | null)?.response
  if (response) return extractErrorMessage(response.data, fallback)
  if (error instanceof Error && error.message) return error.message
  return fallback
}

/**
 * 页面级数据请求：统一 loading / error / 重试。
 *
 * 只用于「挂载即拉取、失败就该让用户看到错误块」的场景；按钮触发的写操作
 * （提交、安装、审核决定等）仍走各自 try/catch + ElMessage，因为它们需要
 * 保持弹窗打开、恢复表单状态等局部行为。
 */
export function useRequest<T>(
  request: () => Promise<T>,
  options: UseRequestOptions<T> & { initial: T },
): UseRequestResult<T> & { data: ShallowRef<T> }
export function useRequest<T>(request: () => Promise<T>, options?: UseRequestOptions<T>): UseRequestResult<T>
export function useRequest<T>(request: () => Promise<T>, options: UseRequestOptions<T> = {}): UseRequestResult<T> {
  const data = shallowRef<T | undefined>(options.initial)
  const loading = ref(false)
  const error = ref('')
  // 快速连点「重试」时只认最后一次请求的结果，避免先发的旧响应覆盖新数据
  let latest = 0

  async function run(): Promise<void> {
    const ticket = ++latest
    loading.value = true
    error.value = ''
    try {
      const result = await request()
      if (ticket === latest) data.value = result
    } catch (cause) {
      if (ticket === latest) error.value = resolveErrorMessage(cause, '加载失败，请重试')
    } finally {
      if (ticket === latest) loading.value = false
    }
  }

  if (options.immediate) onMounted(run)

  return { data, loading, error, run }
}
