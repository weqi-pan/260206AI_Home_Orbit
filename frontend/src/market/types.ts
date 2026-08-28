export type ToolRiskLevel = 'low' | 'reversible' | 'high'

export interface MarketPermission {
  tool_id: string
  key: string
  display_name: string
  description: string
  risk_level: ToolRiskLevel
  version: string
}

export interface MyInstallationState {
  installation_id: string
  deployment_id: string
  pinned_version: number
}

export interface MarketEntrySummary {
  id: string
  name: string
  description: string
  category: string
  official: boolean
  install_count: number
  current_version: number
  my_installation: MyInstallationState | null
}

export interface OfficialListing {
  deployment_id: string
  name: string
  description: string
  official: true
  installed: boolean
}

export interface MarketListings {
  official: OfficialListing[]
  entries: MarketEntrySummary[]
}

export interface MarketEntryVersion {
  number: number
  submission_id: string
  published_at: string | null
}

export interface MarketEntryDetail {
  id: string
  name: string
  description: string
  category: string
  official: boolean
  status: string
  install_count: number
  current_version: { number: number; agent_version_id: string }
  versions: MarketEntryVersion[]
  permissions: MarketPermission[]
  favorited: boolean
  my_installation: MyInstallationState | null
  deprecated?: boolean
  takedown_reason?: string
}

export interface MarketInstallResult {
  installation_id: string
  deployment_id: string
  agent_version_id: string
  pinned_version: number
  granted_tool_keys: string[]
  required_tools: MarketPermission[]
}

export interface MarketUpdatePlan {
  from_version: number
  to_version: number
  new_permissions: MarketPermission[]
}

export interface MyMarketInstall {
  installation_id: string
  entry: { id: string; name: string; description: string; category: string }
  deployment_id: string
  agent_version_id: string
  pinned_version: number
  latest_version: number
  update_available: boolean
  deprecated: boolean
  granted_tool_keys: string[]
  update?: MarketUpdatePlan
}

export interface MarketFavoriteSummary {
  id: string
  name: string
  description: string
  category: string
  official: boolean
  install_count: number
  current_version: number
}

export const RISK_LABELS: Record<ToolRiskLevel, string> = {
  low: '低风险只读',
  reversible: '可撤销写入',
  high: '高风险副作用',
}
