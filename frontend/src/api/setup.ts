import http from './http'

export interface SetupStatus {
  ready: boolean
  error_code: 'MODEL_NOT_CONFIGURED' | null
  required_purposes: string[]
  configured_purposes: string[]
  missing_purposes: string[]
  can_configure: boolean
  master_key_valid: boolean
}

export const fetchSetupStatus = () => http.get<SetupStatus>('/setup/status').then((response) => response.data)
