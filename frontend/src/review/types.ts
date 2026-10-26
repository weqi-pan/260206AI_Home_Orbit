import type { MarketPermission } from '../market/types'

export interface ReviewQueueItem {
  submission_id: string
  entry_id: string | null
  name: string
  household_id: string
  status: 'precheck_pending' | 'precheck_failed' | 'review_pending' | 'returned' | 'rejected' | 'approved'
  category: string
  version: number
  prior_public_version: number | null
  precheck_status: 'pending' | 'passed' | 'failed'
  risk_summary_status: 'pending' | 'done' | 'unavailable'
  submitted_at: string
  decided_at: string | null
  decision_reason: string | null
}

export interface PrecheckCheck {
  code: string
  passed: boolean
  detail: string
}

export interface ReviewSubmissionDetail {
  submission: {
    id: string
    status: ReviewQueueItem['status']
    household_id: string
    draft_id: string
    category: string
    precheck_status: ReviewQueueItem['precheck_status']
    risk_summary_status: ReviewQueueItem['risk_summary_status']
    submitted_at: string
    decided_at: string | null
    decision_reason: string | null
  }
  listing_preview: { name: string; description: string; category: string }
  agent_version: { id: string; version: number; definition: Record<string, unknown> }
  prior_public_version: { version: number; definition: Record<string, unknown> } | null
  diff: {
    prompt_changed: boolean
    tools_added: string[]
    tools_removed: string[]
    budgets_changed: string[]
    workflow_changed: boolean
    skills_changed: boolean
  } | null
  permissions: MarketPermission[]
  knowledge: {
    asset_id: string
    filename: string
    file_type: string
    scan_status: string
    scan_reason: string | null
    content_hash: string
  }[]
  sandbox: {
    status: string
    error_code: string | null
    cases: { name: string; input: string; status: string; error_code: string | null; output: string }[]
  } | null
  precheck: { passed: boolean; codes: string[]; checks: PrecheckCheck[] } | null
  risk_summary: { status: string; summary?: string; risk_level?: string; code?: string }
  risk_payload: Record<string, unknown>
}

export interface ReviewReportItem {
  id: string
  entry_id: string
  entry_name: string | null
  entry_status: string | null
  household_id: string
  reporter_account_id: string | null
  reason: string
  status: 'open' | 'resolved' | 'dismissed'
  resolution_note: string | null
  created_at: string
}

export type ReviewAction = 'approve' | 'reject' | 'return' | 're-review'

export const REVIEW_STATUS_LABELS: Record<ReviewQueueItem['status'], string> = {
  precheck_pending: '预检中',
  precheck_failed: '预检未过',
  review_pending: '待人工审核',
  returned: '已退回',
  rejected: '已拒绝',
  approved: '已通过',
}
