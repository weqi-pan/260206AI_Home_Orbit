import http from './http'
import type { TicketInfo } from './tickets'

export interface AdminAccount {
  id: string
  username: string
  role: 'admin' | 'user'
  status: 'active' | 'disabled'
  user_id: string | null
  must_change_password: boolean
  created_at: string
}

export interface DashboardData {
  accounts: number
  users: number
  devices: number
  open_tickets: number
  today_sessions: number
  messages_last_7_days: number
  satisfaction_rate: number
  ticket_status_counts: Record<string, number>
  low_stock_devices: { model_id: string; model_name: string; total_stock: number; stock_threshold: number }[]
  knowledge_files: number
  approved_faq: number
}

export interface AdminDevice {
  model_id: string
  model_name: string
  category: string
  specs: string
  description: string
  price: number
  features: string
}

export interface DevicePayload {
  model_id: string
  model_name: string
  category: string
  specs: string
  description: string
  price: number
  features: string
}

export interface InventoryPayload {
  model_id: string
  warehouse: string
  stock: number
  min_stock: number
}

export interface AdminTicket extends TicketInfo {
  account_id: string
}

export interface TicketUpdatePayload {
  status?: 'open' | 'processing' | 'resolved' | 'closed'
  priority?: 'low' | 'normal' | 'high'
  admin_reply?: string
}

export function fetchDashboard() {
  return http.get<DashboardData>('/admin/dashboard').then((r) => r.data)
}

export function fetchAccounts() {
  return http.get<AdminAccount[]>('/admin/accounts').then((r) => r.data)
}

export function updateAccount(accountId: string, payload: { status?: 'active' | 'disabled'; role?: 'admin' | 'user' }) {
  return http.put<AdminAccount>(`/admin/accounts/${encodeURIComponent(accountId)}`, payload).then((r) => r.data)
}

export function resetAccountPassword(accountId: string, password: string) {
  return http.post<void>(`/admin/accounts/${encodeURIComponent(accountId)}/reset-password`, { password })
}

export function fetchAdminDevices() {
  return http.get<AdminDevice[]>('/admin/devices').then((r) => r.data)
}

export function createDevice(payload: DevicePayload) {
  return http.post<AdminDevice>('/admin/devices', payload).then((r) => r.data)
}

export function updateDevice(modelId: string, payload: DevicePayload) {
  return http.put<AdminDevice>(`/admin/devices/${encodeURIComponent(modelId)}`, payload).then((r) => r.data)
}

export function deleteDevice(modelId: string) {
  return http.delete<void>(`/admin/devices/${encodeURIComponent(modelId)}`)
}

export function upsertInventory(payload: InventoryPayload) {
  return http
    .put<{ id: number; model_id: string; warehouse: string; stock: number; min_stock: number }>('/admin/inventory', payload)
    .then((r) => r.data)
}

export function fetchAdminTickets() {
  return http.get<AdminTicket[]>('/admin/tickets').then((r) => r.data)
}

export function updateTicket(ticketId: number, payload: TicketUpdatePayload) {
  return http.put<AdminTicket>(`/admin/tickets/${ticketId}`, payload).then((r) => r.data)
}
