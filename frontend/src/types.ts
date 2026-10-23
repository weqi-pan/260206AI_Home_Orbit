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
}
