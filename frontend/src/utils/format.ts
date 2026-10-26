export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

/**
 * 解析后端返回的时间戳，**按 UTC 处理**没有时区后缀的写法。
 *
 * 后端统一用 `datetime.now(timezone.utc).replace(tzinfo=None)` 存库，序列化出来是
 * `2026-10-26T10:35:00` 这种不带 Z 的形式。`Date.parse` 对「date-time 无偏移」按
 * **本地时间**解释（ECMAScript 规范），在 UTC+8 的机器上会凭空多出 8 小时。
 * 做倒计时这类时间运算时必须用这个函数，否则窗口会算错。
 */
export function parseServerTime(value: string | null | undefined): number | null {
  if (!value) return null
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value)
  const parsed = Date.parse(hasZone ? value : `${value}Z`)
  return Number.isNaN(parsed) ? null : parsed
}

export function sentimentTag(label: string | null | undefined): { label: string; type: 'success' | 'danger' | 'info' } {
  if (label === 'positive') return { label: '积极', type: 'success' }
  if (label === 'negative') return { label: '消极', type: 'danger' }
  return { label: '中性', type: 'info' }
}

export function ticketStatusTag(status: string): { label: string; type: 'primary' | 'warning' | 'success' | 'info' } {
  switch (status) {
    case 'open':
      return { label: '待处理', type: 'primary' }
    case 'processing':
      return { label: '处理中', type: 'warning' }
    case 'resolved':
      return { label: '已解决', type: 'success' }
    default:
      return { label: '已关闭', type: 'info' }
  }
}

export function ticketPriorityTag(priority: string): { label: string; type: 'info' | 'primary' | 'danger' } {
  switch (priority) {
    case 'low':
      return { label: '低', type: 'info' }
    case 'high':
      return { label: '高', type: 'danger' }
    default:
      return { label: '普通', type: 'primary' }
  }
}
