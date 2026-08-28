import http from './http'

export { fetchSetupStatus } from './setup'
export type { SetupStatus } from './setup'

export interface HouseholdMember {
  account_id: string
  username: string
  role: 'owner' | 'admin' | 'member' | 'viewer'
}

export interface Household {
  id: string
  name: string
  role: HouseholdMember['role']
  members: HouseholdMember[]
}

export interface OrbitNode {
  id: string
  label: string
  kind: string
  status: 'ready' | 'attention' | 'running' | 'failed'
  href: string
  detail: string
}

export interface TodayTask {
  id: string
  title: string
  description: string
  status: string
  due_at: string | null
}

export interface TodayReminder {
  id: string
  task_id: string
  interval_minutes: number
  next_run_at: string | null
  active: boolean
}

export interface HomeToday {
  date: string
  tasks: TodayTask[]
  reminders: TodayReminder[]
  shopping: { total: number; unchecked: number; checked: number }
  notifications: { unread: number }
  anomalies: { model_id: string; stock: number }[]
}

export const fetchHousehold = () => http.get<Household>('/households/current').then((response) => response.data)
export const fetchOrbit = () => http.get<{ nodes: OrbitNode[] }>('/home/orbit').then((response) => response.data)
export const fetchHomeToday = () => http.get<HomeToday>('/home/today').then((response) => response.data)
export const inviteMember = (householdId: string, username: string, role: string) =>
  http.post(`/households/${householdId}/invitations`, { username, role }).then((response) => response.data)
