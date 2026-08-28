import http from './http'
import type { Page, UsageByModel, UsageByPurpose, DayUsage } from './ops'

export interface AgentRunItem {
  id: string
  household_id: string
  deployment_id: string
  deployment_revision: number
  agent_version_id: string
  model_id: string
  principal_id: string
  session_id: string
  status: string
  error_code: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export interface ToolExecutionItem {
  id: string
  household_id: string
  run_id: string
  tool_id: string
  principal_id: string
  tool_version: string
  model_id: string
  deployment_id: string
  status: string
  error_code: string | null
  duration_ms: number | null
  started_at: string
  finished_at: string | null
  redaction_schema_version: number
}

export interface ToolExecutionDetail extends ToolExecutionItem {
  arguments: Record<string, unknown>
  result: Record<string, unknown>
}

export interface ModelUsageItem {
  id: string
  household_id: string
  model_id: string
  month: string
  tokens: number
  reserved_tokens: number
  status: string
  created_at: string
}

export interface UsageAccounting {
  window_days: number
  totals: { requests: number; tokens: number }
  by_status: Record<string, number>
  by_model: UsageByModel[]
  by_purpose: UsageByPurpose[]
  by_day: DayUsage[]
  unreconciled: {
    count: number
    window_minutes: number
    items: { id: string; household_id: string; model_id: string; reserved_tokens: number; created_at: string }[]
  }
}

export interface CeleryTaskItem {
  id: string
  idempotency_key: string
  task_type: string
  household_id: string | null
  status: string
  attempt: number
  max_attempts: number
  error_code: string | null
  next_retry_at: string | null
  created_at: string
  updated_at: string
}

export interface CeleryTaskDetail extends CeleryTaskItem {
  error_detail: string | null
  payload: Record<string, unknown>
  result: Record<string, unknown> | null
  audit: Record<string, unknown>
  notifications: { id: string; title: string; kind: string; created_at: string }[]
}

export interface AdminActionItem {
  id: string
  principal_id: string
  actor_username: string | null
  household_id: string | null
  action: string
  resource_type: string
  resource_id: string
  detail: Record<string, unknown>
  created_at: string
}

export interface MarketPointerHistoryItem {
  version: number
  version_id: string
  changed_by_account_id: string | null
  changed_at: string | null
  submission_id: string
}

export interface MarketPointerEntry {
  entry_id: string
  name: string
  source_household_id: string
  official: boolean
  status: string
  current_version_number: number
  current_version_id: string | null
  history: MarketPointerHistoryItem[]
}

export interface OfficialDeploymentPointer {
  deployment_id: string
  household_id: string
  revision: number
  agent_version_id: string
  created_by: string | null
  created_at: string
  history: { version: number; version_id: string; published_at: string | null; changed_by: string | null }[]
}

export interface DeploymentPointers {
  market_entries: Page<MarketPointerEntry>
  official: Page<OfficialDeploymentPointer>
}

export interface ModelRevisionCheck {
  status: string
  error_code: string | null
  endpoint_revision: number
  model_revision: number
  checked_at: string
}

export interface ModelConfigRevision {
  model_id: string
  model_name: string
  model_type: string
  enabled: boolean
  revision: number
  latest_health: { status: string; error_code: string | null; checked_at: string } | null
  history: ModelRevisionCheck[]
}

export interface AuditListParams {
  page?: number
  page_size?: number
  from?: string
  to?: string
}

export function fetchAgentRuns(params: AuditListParams & {
  status?: string; model_id?: string; deployment_id?: string; household_id?: string
}) {
  return http.get<Page<AgentRunItem>>('/audit/agent-runs', { params }).then((r) => r.data)
}

export function fetchToolExecutions(params: AuditListParams & {
  run_id?: string; tool_id?: string; status?: string; household_id?: string
}) {
  return http.get<Page<ToolExecutionItem>>('/audit/tool-executions', { params }).then((r) => r.data)
}

export function fetchToolExecutionDetail(id: string) {
  return http.get<ToolExecutionDetail>(`/audit/tool-executions/${encodeURIComponent(id)}`).then((r) => r.data)
}

export function fetchModelUsage(params: AuditListParams & {
  household_id?: string; model_id?: string; purpose?: string; status?: string
}) {
  return http.get<Page<ModelUsageItem>>('/audit/model-usage', { params }).then((r) => r.data)
}

export function fetchUsageAccounting(days = 7) {
  return http.get<UsageAccounting>('/audit/model-usage/accounting', { params: { days } }).then((r) => r.data)
}

export function fetchCeleryTasks(params: AuditListParams & {
  task_type?: string; status?: string; household_id?: string; error_code?: string
}) {
  return http.get<Page<CeleryTaskItem>>('/audit/celery-tasks', { params }).then((r) => r.data)
}

export function fetchCeleryTaskDetail(id: string) {
  return http.get<CeleryTaskDetail>(`/audit/celery-tasks/${encodeURIComponent(id)}`).then((r) => r.data)
}

export function fetchAdminActions(params: AuditListParams & {
  actor_id?: string; action?: string; resource_type?: string; household_id?: string
}) {
  return http.get<Page<AdminActionItem>>('/audit/admin-actions', { params }).then((r) => r.data)
}

export function fetchDeploymentPointers(params: { page?: number; page_size?: number } = {}) {
  return http.get<DeploymentPointers>('/audit/deployments/pointers', { params }).then((r) => r.data)
}

export function fetchModelConfigRevisions(params: { page?: number; page_size?: number } = {}) {
  return http.get<Page<ModelConfigRevision>>('/audit/models/revisions', { params }).then((r) => r.data)
}
