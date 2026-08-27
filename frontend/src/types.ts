export interface ChatUiMessage {
  id: number | null
  role: 'user' | 'assistant'
  content: string
  sentiment: string | null
  sentimentScore: number | null
  feedback: 'like' | 'dislike' | null
  streaming?: boolean
  failed?: boolean
}
