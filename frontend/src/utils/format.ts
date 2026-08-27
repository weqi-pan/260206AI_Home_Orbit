export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
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
