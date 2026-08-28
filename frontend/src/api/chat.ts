import { useAuthStore } from '../stores/auth'
import http, { forceLogout, refreshAccessToken } from './http'

export interface ChatSessionInfo {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export interface ChatMessageInfo {
  id: number
  role: 'user' | 'assistant'
  content: string
  sentiment: string | null
  sentiment_score: number | null
  feedback: 'like' | 'dislike' | null
  created_at: string
}

export interface ToolEventData {
  name: string
  status: string
  message?: string
}

export interface SentimentEventData {
  label: string
  score: number
}

export interface ChatStreamCallbacks {
  onToken?: (content: string) => void
  onTool?: (data: ToolEventData) => void
  onSentiment?: (data: SentimentEventData) => void
  onFollowup?: (questions: string[]) => void
  onDone?: (data: { message_id: number }) => void
  onError?: (message: string) => void
}

export function listSessions() {
  return http.get<ChatSessionInfo[]>('/chat/sessions').then((r) => r.data)
}

export function createSession(title?: string, deploymentId?: string) {
  const payload: { title?: string; deployment_id?: string } = {}
  if (title) payload.title = title
  if (deploymentId) payload.deployment_id = deploymentId
  return http.post<ChatSessionInfo>('/chat/sessions', payload).then((r) => r.data)
}

export function deleteSession(sessionId: string) {
  return http.delete<void>(`/chat/sessions/${sessionId}`)
}

export function listMessages(sessionId: string) {
  return http.get<ChatMessageInfo[]>(`/chat/sessions/${sessionId}/messages`).then((r) => r.data)
}

export function sendFeedback(messageId: number, feedback: 'like' | 'dislike') {
  return http
    .post<{ id: number; feedback: 'like' | 'dislike' }>(`/chat/messages/${messageId}/feedback`, { feedback })
    .then((r) => r.data)
}

function dispatchFrame(event: string, payload: unknown, callbacks: ChatStreamCallbacks) {
  const data = (payload ?? {}) as Record<string, unknown>
  switch (event) {
    case 'token': {
      const content = data.content
      if (typeof content === 'string' && content) callbacks.onToken?.(content)
      break
    }
    case 'tool':
      callbacks.onTool?.({
        name: String(data.name ?? ''),
        status: String(data.status ?? ''),
        message: typeof data.message === 'string' ? data.message : undefined,
      })
      break
    case 'sentiment':
      callbacks.onSentiment?.({ label: String(data.label ?? 'neutral'), score: Number(data.score ?? 0) })
      break
    case 'followup':
      callbacks.onFollowup?.(Array.isArray(data.questions) ? data.questions.map(String).filter(Boolean) : [])
      break
    case 'done':
      callbacks.onDone?.({ message_id: Number(data.message_id ?? 0) })
      break
    case 'error':
      callbacks.onError?.(typeof data.message === 'string' && data.message ? data.message : '聊天服务异常')
      break
    default:
      break
  }
}

function parseFrame(frame: string, callbacks: ChatStreamCallbacks) {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(':')) continue
    if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).replace(/^ /, ''))
    }
  }
  if (!dataLines.length) return
  let payload: unknown = {}
  try {
    payload = JSON.parse(dataLines.join('\n'))
  } catch {
    payload = {}
  }
  dispatchFrame(event, payload, callbacks)
}

async function readStream(response: Response, callbacks: ChatStreamCallbacks) {
  const reader = response.body?.getReader()
  if (!reader) throw new Error('当前浏览器不支持流式响应')
  const decoder = new TextDecoder('utf-8')
  const separator = /\r?\n\r?\n/
  let buffer = ''
  const drain = () => {
    for (;;) {
      const match = buffer.match(separator)
      if (!match || match.index === undefined) break
      const frame = buffer.slice(0, match.index)
      buffer = buffer.slice(match.index + match[0].length)
      parseFrame(frame, callbacks)
    }
  }
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    drain()
  }
  buffer += decoder.decode()
  if (buffer.trim()) parseFrame(buffer, callbacks)
}

export async function streamChat(
  sessionId: string,
  message: string,
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const url = `/api/chat/sessions/${sessionId}/chat`
  const doFetch = () => {
    const auth = useAuthStore()
    const headers: Record<string, string> = { 'Content-Type': 'application/json', Accept: 'text/event-stream' }
    if (auth.accessToken) headers.Authorization = `Bearer ${auth.accessToken}`
    return fetch(url, { method: 'POST', headers, body: JSON.stringify({ message }), signal })
  }
  let response = await doFetch()
  if (response.status === 401) {
    try {
      await refreshAccessToken()
      response = await doFetch()
    } catch {
      await forceLogout()
      throw new Error('登录已过期，请重新登录')
    }
  }
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    let detail = ''
    try {
      detail = String((JSON.parse(text) as { detail?: unknown }).detail ?? '')
    } catch {
      detail = text
    }
    throw new Error(detail || `请求失败（${response.status}）`)
  }
  await readStream(response, callbacks)
}
