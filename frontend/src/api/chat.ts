import { useAuthStore } from '../stores/auth'
import http, { extractErrorMessage, forceLogout, refreshAccessToken } from './http'

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

/** 审批事件只带 id 和 code，工具名/风险/参数要另外从 /approvals 取。 */
export interface ApprovalEventData {
  approval_id: string
  code?: string
  kind?: string
  message?: string
}

/** GET /api/approvals 的条目：足以直接渲染一张审批卡。 */
export interface ApprovalInfo {
  id: string
  run_id: string
  tool_id: string
  status: string
  expires_at: string | null
  created_at: string | null
  tool_key: string | null
  tool_display_name: string | null
  risk_level: string | null
  arguments: Record<string, unknown>
  session_id: string | null
  deployment_id: string | null
}

export interface ChatStreamCallbacks {
  onToken?: (content: string) => void
  onTool?: (data: ToolEventData) => void
  onSentiment?: (data: SentimentEventData) => void
  onFollowup?: (questions: string[]) => void
  onDone?: (data: { message_id: number }) => void
  onError?: (message: string) => void
  onApprovalRequired?: (data: ApprovalEventData) => void
  onApprovalResumed?: (data: ApprovalEventData) => void
  /** 审批后继续的输出以 run 帧收尾，不是 done —— 这一帧没有 message_id。 */
  onRun?: (data: { run_id?: string; status?: string; error_code?: string }) => void
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
    case 'approval_required':
      // 之前这里落到 default 分支被静默丢弃，高风险审批在界面上根本走不到
      if (typeof data.approval_id === 'string' && data.approval_id) {
        callbacks.onApprovalRequired?.({
          approval_id: data.approval_id,
          code: typeof data.code === 'string' ? data.code : undefined,
          kind: typeof data.kind === 'string' ? data.kind : undefined,
          message: typeof data.message === 'string' ? data.message : undefined,
        })
      }
      break
    case 'approval_resumed':
      if (typeof data.approval_id === 'string' && data.approval_id) {
        callbacks.onApprovalResumed?.({ approval_id: data.approval_id })
      }
      break
    case 'run':
      callbacks.onRun?.({
        run_id: typeof data.run_id === 'string' ? data.run_id : undefined,
        status: typeof data.status === 'string' ? data.status : undefined,
        error_code: typeof data.error_code === 'string' ? data.error_code : undefined,
      })
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
  await streamRequest(url, { message }, callbacks, signal)
}

/**
 * 恢复一个已批准的审批，后续输出继续流式回来。
 *
 * 这个端点返回的是 `EventSourceResponse`，不是 JSON —— 所以必须走 fetch，
 * 走 axios 会拿不到增量帧，只能等整段结束。
 */
export async function resumeApproval(
  approvalId: string,
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const url = `/api/approvals/${approvalId}/resume`
  await streamRequest(url, {}, callbacks, signal)
}

async function streamRequest(
  url: string,
  body: Record<string, unknown>,
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const doFetch = () => {
    const auth = useAuthStore()
    const headers: Record<string, string> = { 'Content-Type': 'application/json', Accept: 'text/event-stream' }
    if (auth.accessToken) headers.Authorization = `Bearer ${auth.accessToken}`
    return fetch(url, { method: 'POST', headers, body: JSON.stringify(body), signal })
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
    let parsed: unknown
    try {
      parsed = JSON.parse(text)
    } catch {}
    throw new Error(extractErrorMessage(parsed, `请求失败（${response.status}）`))
  }
  await readStream(response, callbacks)
}

export function listPendingApprovals() {
  return http.get<ApprovalInfo[]>('/approvals').then((r) => r.data)
}

export function approveApproval(approvalId: string, expiresAt?: string) {
  // 传不传都行：不传服务端用默认窗口，传了也会被夹到 approval_max_minutes 以内
  const payload = expiresAt ? { expires_at: expiresAt } : {}
  return http
    .post<{ id: string; status: string; expires_at: string }>(`/approvals/${approvalId}/approve`, payload)
    .then((r) => r.data)
}

export function rejectApproval(approvalId: string) {
  return http.post<{ id: string; status: string }>(`/approvals/${approvalId}/reject`).then((r) => r.data)
}
