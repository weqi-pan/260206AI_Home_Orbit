import http from './http'

export interface TicketInfo {
  id: number
  session_id: string | null
  title: string
  summary: string | null
  emotion: string | null
  priority: 'low' | 'normal' | 'high'
  status: 'open' | 'processing' | 'resolved' | 'closed'
  admin_reply: string | null
  created_at: string
  resolved_at: string | null
}

export interface TicketCreatePayload {
  session_id?: string | null
  title: string
  content: string
  emotion?: string | null
  priority?: string
}

export function myTickets() {
  return http.get<TicketInfo[]>('/tickets/my').then((r) => r.data)
}

export function ticketDetail(ticketId: number) {
  return http.get<TicketInfo>(`/tickets/my/${ticketId}`).then((r) => r.data)
}

export function createTicket(payload: TicketCreatePayload) {
  return http.post<TicketInfo>('/tickets', payload).then((r) => r.data)
}
