import http from './http'

export interface SetupStatus {
  ready: boolean
  ai_ready: boolean
  embedding_ready: boolean
  error_code: 'MODEL_NOT_CONFIGURED' | null
  required_purposes: string[]
  optional_purposes: string[]
  configured_purposes: string[]
  missing_purposes: string[]
  missing_optional_purposes: string[]
  degraded_features: Array<'knowledge_retrieval' | 'vectorization'>
  can_configure: boolean
  master_key_valid: boolean
}

export const fetchSetupStatus = () => http.get<SetupStatus>('/setup/status').then((response) => response.data)
