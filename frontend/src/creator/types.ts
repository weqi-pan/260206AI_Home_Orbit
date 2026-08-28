export type ToolRiskLevel = 'low' | 'reversible' | 'high'

export interface CeilingTool {
  tool_id: string
  key: string
  version: string
  risk_level: ToolRiskLevel
  display_name: string
  description: string
}

export interface CardExample {
  user: string
  assistant: string
}

export interface CardSkill {
  name: string
  instructions: string
  knowledge_asset_ids: string[]
}

export interface CardTestCase {
  name: string
  input: string
  expected_contains: string
}

export interface CardBudgets {
  tool_calls: number
  tokens: number
  wall_time_seconds: number
  delegation_depth: number
}

export interface IntentCard {
  name: string
  description: string
  prompt: string
  examples: CardExample[]
  model_requirements: { capabilities: string[] }
  skills: CardSkill[]
  tools: string[]
  budgets: CardBudgets
  tests: CardTestCase[]
  workflow: Record<string, unknown> | null
}

export interface ValidationError {
  code: string
  path: string
  detail?: string
}

export interface ValidationReport {
  valid: boolean
  errors: ValidationError[]
}

export interface KnowledgeAsset {
  id: string
  filename: string
  file_type: string
  size_bytes: number
  skill_key: string
  scan_status: 'pending' | 'passed' | 'rejected'
  scan_reason: string | null
  content_hash: string
}

export interface TrialCase {
  name: string
  input: string
  status: 'passed' | 'failed'
  error_code: string | null
  output: string
}

export interface TrialSummary {
  id: string
  draft_id: string
  status: string
  cases: TrialCase[]
  error_code: string | null
  granted_tool_keys: string[]
}

export interface CreatorDraftSummary {
  id: string
  title: string
  status: 'draft' | 'validated' | 'private_published'
  updated_at: string
}

export interface CreatorDraftDetail extends CreatorDraftSummary {
  description: string
  intent_card: IntentCard | null
  validation: ValidationReport | null
  knowledge_assets: KnowledgeAsset[]
  agent_versions: { id: string; version: number; published_at: string | null }[]
  deployment_id: string | null
  latest_trial: { id: string; status: string; error_code: string | null } | null
  created_at: string
}

export interface PublishedAgent {
  draft_id: string
  agent_id: string
  name: string
  description: string
  deployment_id: string | null
  published_at: string | null
  versions: { id: string; version: number; published_at: string | null }[]
}

export interface PublishResult {
  agent_id: string
  agent_version_id: string
  agent_version: number
  deployment: { id: string; revision: number; visibility: string }
  installation_id: string
  skills: { name: string; skill_id: string; skill_version_id: string }[]
  granted_tool_keys: string[]
  vectorized_assets: { asset_id: string; skill_version_id: string; status: string; error_code: string | null }[]
}
