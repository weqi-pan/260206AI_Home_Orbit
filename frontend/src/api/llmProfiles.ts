import http from './http'

export type ModelProtocol = 'openai_compatible' | 'anthropic'
export type ModelType = 'chat' | 'embedding'
export type ModelPurpose = 'household_orchestrator' | 'primary_chat' | 'auxiliary_generation' | 'review_model' | 'embedding'

export interface EndpointPayload {
  protocol: ModelProtocol
  display_name: string
  base_url: string
  api_key?: string
  timeout_seconds: number
  max_retries: number
  enabled: boolean
}

export interface ModelEndpoint extends Omit<EndpointPayload, 'api_key'> {
  id: string
  revision: number
  has_api_key: boolean
  masked_api_key: string | null
  disabled_reason: string | null
}

export interface ModelProfile {
  id: string
  endpoint_id: string
  model_name: string
  model_type: ModelType
  temperature: number
  max_tokens: number | null
  embedding_dimensions: number | null
  use_responses_api: boolean
  capability_flags: Record<string, string>
  requests_per_minute: number
  agent_token_budget: number | null
  enabled: boolean
}

export interface ModelPayload extends Omit<ModelProfile, 'id' | 'capability_flags'> {}
export interface CapabilityResult { passed: boolean; capabilities: Record<string, string>; error_code: string | null; dimensions: number | null }

export const fetchEndpoints = () => http.get<ModelEndpoint[]>('/models/endpoints').then((response) => response.data)
export const createEndpoint = (payload: EndpointPayload) => http.post<ModelEndpoint>('/models/endpoints', payload).then((response) => response.data)
export const fetchModels = () => http.get<ModelProfile[]>('/models/profiles').then((response) => response.data)
export const createModel = (payload: ModelPayload) => http.post<ModelProfile>('/models/profiles', payload).then((response) => response.data)
export const runCapabilityTest = (modelId: string) => http.post<CapabilityResult>(`/models/profiles/${modelId}/capability-tests`).then((response) => response.data)
export const bindPurpose = (purpose: ModelPurpose, modelId: string) => http.put(`/models/bindings/${purpose}`, { purpose, model_id: modelId }).then((response) => response.data)
