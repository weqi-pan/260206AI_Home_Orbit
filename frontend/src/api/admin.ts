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

export interface AdminHousehold {
  id: string
  name: string
  created_at: string
  members: { account_id: string; username: string; role: string }[]
}

export interface AdminDevice {
  model_id: string
  model_name: string
  category: string
  specs: string
  description: string
  price: number
  features: string
  household_id: string
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

export interface InventoryRow {
  household_id: string
  model_id: string
  warehouse: string
  stock: number
  min_stock: number
  updated_at: string
}

export interface AdminTicket extends TicketInfo {
  account_id: string
  household_id: string
}

export interface TicketUpdatePayload {
  status?: 'open' | 'processing' | 'resolved' | 'closed'
  priority?: 'low' | 'normal' | 'high'
  admin_reply?: string
}

export function fetchAdminHouseholds() {
  return http.get<AdminHousehold[]>('/admin/households').then((r) => r.data)
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

export function fetchAdminDevices(householdId?: string) {
  return http
    .get<AdminDevice[]>('/admin/devices', { params: householdId ? { household_id: householdId } : {} })
    .then((r) => r.data)
}

export function createDevice(householdId: string, payload: DevicePayload) {
  return http
    .post<AdminDevice>('/admin/devices', payload, { params: { household_id: householdId } })
    .then((r) => r.data)
}

export function updateDevice(householdId: string, modelId: string, payload: DevicePayload) {
  return http
    .put<AdminDevice>(`/admin/devices/${encodeURIComponent(modelId)}`, payload, { params: { household_id: householdId } })
    .then((r) => r.data)
}

export function deleteDevice(householdId: string, modelId: string) {
  return http.delete<void>(`/admin/devices/${encodeURIComponent(modelId)}`, { params: { household_id: householdId } })
}

export function fetchInventory(householdId?: string, modelId?: string) {
  const params: Record<string, string> = {}
  if (householdId) params.household_id = householdId
  if (modelId) params.model_id = modelId
  return http.get<InventoryRow[]>('/admin/inventory', { params }).then((r) => r.data)
}

export function upsertInventory(householdId: string, payload: InventoryPayload) {
  return http
    .put<{ id: number; model_id: string; warehouse: string; stock: number; min_stock: number }>('/admin/inventory', payload, {
      params: { household_id: householdId },
    })
    .then((r) => r.data)
}

export function fetchAdminTickets(householdId?: string) {
  return http
    .get<AdminTicket[]>('/admin/tickets', { params: householdId ? { household_id: householdId } : {} })
    .then((r) => r.data)
}

export function updateTicket(householdId: string, ticketId: number, payload: TicketUpdatePayload) {
  return http
    .put<AdminTicket>(`/admin/households/${encodeURIComponent(householdId)}/tickets/${ticketId}`, payload)
    .then((r) => r.data)
}
