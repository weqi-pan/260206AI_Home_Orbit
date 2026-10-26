import type { ApprovalInfo } from './api/chat'

export type ApprovalCardState = 'pending' | 'busy' | 'approved' | 'rejected' | 'failed'

/** 挂在助手气泡上的审批卡：info 来自 /api/approvals，expiresAt 只在批准后才有。 */
export interface ChatApproval {
  info: ApprovalInfo
  state: ApprovalCardState
  expiresAt: number | null
}

export interface ChatUiMessage {
  /** 前端稳定标识：列表 key 用它，SSE 结束回填真实 id 时不改变，避免整列重挂载 */
  uid: number
  id: number | null
  role: 'user' | 'assistant'
  content: string
  sentiment: string | null
  sentimentScore: number | null
  feedback: 'like' | 'dislike' | null
  streaming?: boolean
  failed?: boolean
  /** 一次运行里可能连着弹多个审批（工作流有多个 confirm 节点），所以是列表 */
  approvals?: ChatApproval[]
}
