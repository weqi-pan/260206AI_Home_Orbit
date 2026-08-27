import http from './http'

export interface SetupStatus {
  ready: boolean
  error_code: 'MODEL_NOT_CONFIGURED' | null
  required_purposes: string[]
  configured_purposes: string[]
  missing_purposes: string[]
  can_configure: boolean
}

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

export const fetchSetupStatus = () => http.get<SetupStatus>('/setup/status').then((response) => response.data)
export const fetchHousehold = () => http.get<Household>('/households/current').then((response) => response.data)
export const fetchOrbit = () => http.get<{ nodes: OrbitNode[] }>('/home/orbit').then((response) => response.data)
export const inviteMember = (householdId: string, username: string, role: string) =>
  http.post(`/households/${householdId}/invitations`, { username, role }).then((response) => response.data)
