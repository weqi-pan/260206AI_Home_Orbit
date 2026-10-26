import http from './http'

export interface DayCount {
  day: string
  count: number
}
export interface DayUsage {
  day: string
  requests: number
  tokens: number
}

export interface OpsHouseholdsMetrics {
  total: number
  new_in_window: number
  growth_by_day: DayCount[]
}

export interface OpsInstallationsMetrics {
  active: number
  households_with_active: number
}

export interface OpsAgentRunsMetrics {
  total_in_window: number
  by_status: Record<string, number>
  by_day: DayCount[]
}

export interface OpsToolExecutionsMetrics {
  total_in_window: number
  by_status: Record<string, number>
  permission_denials: number
  failed: number
}

export interface OpsCeleryMetrics {
  total_in_window: number
  by_status: Record<string, number>
  failure_rate: number
  throughput_by_day: DayCount[]
}

export interface OpsReviewMetrics {
  queue_depth: number
  decided_in_window: number
  avg_turnaround_seconds: number | null
  decided_by_action: Record<string, number>
}

export interface UsageByModel {
  model_id: string
  model_name: string
  requests: number
  tokens: number
}
export interface UsageByPurpose {
  purpose: string
  requests: number
  tokens: number
}

export interface OpsModelUsageMetrics {
  requests: number
  tokens: number
  by_model: UsageByModel[]
  by_purpose: UsageByPurpose[]
  by_day: DayUsage[]
}

export interface OpsQuotaIncidentMetrics {
  count: number
  by_code: Record<string, number>
}

export interface OpsRiskMetrics {
  high_risk_approvals: number
  permission_denials: number
}

export interface OpsOverview {
  window_days: number
  households: OpsHouseholdsMetrics
  installations: OpsInstallationsMetrics
  agent_runs: OpsAgentRunsMetrics
  tool_executions: OpsToolExecutionsMetrics
  celery_tasks: OpsCeleryMetrics
  review: OpsReviewMetrics
  model_usage: OpsModelUsageMetrics
  quota_incidents: OpsQuotaIncidentMetrics
  risk: OpsRiskMetrics
}

export interface QuotaIncidentItem {
  id: string
  household_id: string
  model_id: string
  code: string
  created_at: string
}

export interface HouseholdQuotaIncident {
  id: string
  model_id: string
  model_name: string
  code: string
  created_at: string
}

export interface HouseholdQuotaView {
  household_id: string
  quota: { monthly_limit: number; requests_per_minute: number; agent_token_budget: number | null } | null
  usage: { month: string; requests: number; tokens: number }
  trend: DayUsage[]
  incidents: HouseholdQuotaIncident[]
}

export interface TaskTypeSpec {
  task_type: string
  max_attempts: number
  description: string
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface QuotaAdjustPayload {
  monthly_limit: number
  requests_per_minute: number
  agent_token_budget: number | null
}

export function fetchOpsOverview(windowDays = 7) {
  return http.get<OpsOverview>('/ops/overview', { params: { window_days: windowDays } }).then((r) => r.data)
}

export function fetchHouseholdQuota(householdId: string) {
  return http.get<HouseholdQuotaView>(`/ops/households/${encodeURIComponent(householdId)}/quota`).then((r) => r.data)
}

export function adjustHouseholdQuota(householdId: string, payload: QuotaAdjustPayload) {
  return http
    .put<{
      household_id: string
      monthly_limit: number
      requests_per_minute: number
      agent_token_budget: number | null
    }>(`/models/households/${encodeURIComponent(householdId)}/quota`, payload)
    .then((r) => r.data)
}

export function fetchQuotaIncidents(params: {
  household_id?: string
  code?: string
  page?: number
  page_size?: number
}) {
  return http.get<Page<QuotaIncidentItem>>('/ops/quota/incidents', { params }).then((r) => r.data)
}

export function releaseExpiredReservations() {
  return http.post<{ released: number }>('/ops/model-usage/release-expired').then((r) => r.data)
}

export function fetchTaskTypes() {
  return http.get<{ items: TaskTypeSpec[] }>('/ops/tasks/types').then((r) => r.data.items)
}

export function runAdminTask(taskType: string, payload: Record<string, unknown>, householdId?: string | null) {
  return http
    .post<{ idempotency_key: string; status: string; attempt: number; error_code: string | null }>(
      `/admin/tasks/${encodeURIComponent(taskType)}/run`,
      { payload, household_id: householdId ?? null },
    )
    .then((r) => r.data)
}
