import type { IntentCard, ToolRiskLevel, ValidationError } from './types'

export function emptyCard(name = '', description = ''): IntentCard {
  return {
    name,
    description,
    prompt: '',
    examples: [],
    model_requirements: { capabilities: ['chat'] },
    skills: [],
    tools: [],
    budgets: { tool_calls: 16, tokens: 32000, wall_time_seconds: 120, delegation_depth: 2 },
    tests: [],
    workflow: null,
  }
}

export const RISK_LABELS: Record<ToolRiskLevel, string> = {
  low: '低风险·只读',
  reversible: '可撤销写入',
  high: '高风险·需逐次确认',
}

export const ERROR_MESSAGES: Record<string, string> = {
  INTENT_CARD_INVALID: '意图卡结构不合法',
  INTENT_CARD_UNSAFE: '意图卡包含禁止内容（代码/URL/导入等）',
  AGENT_DEFINITION_UNSAFE: '系统提示词包含禁止内容',
  SKILL_DEFINITION_UNSAFE: 'Skill 指令包含禁止内容',
  AGENT_DEFINITION_INVALID: 'Agent 声明不合法',
  SKILL_DEFINITION_INVALID: 'Skill 声明不合法',
  BUDGET_INVALID: '预算超出允许范围',
  TOOL_NOT_IN_CATALOG: '工具不在平台内置目录中',
  TOOL_NOT_ENABLED: '工具未启用',
  TOOL_CATALOG_MISMATCH: '工具版本/风险与目录不一致',
  TOOL_NOT_GRANTED: '家庭未获得该工具授权',
  PROMPT_INJECTION_DETECTED: '检测到提示注入内容',
  WORKFLOW_INVALID: '受限工作流不合法',
  KNOWLEDGE_ASSET_INVALID: '知识资产不可用或未通过扫描',
}

export function errorText(error: ValidationError): string {
  const base = ERROR_MESSAGES[error.code] ?? error.code
  const where = error.path ? `（${error.path}）` : ''
  const detail = error.detail ? `：${error.detail}` : ''
  return `${base}${where}${detail}`
}

export function isSelectableRisk(risk: ToolRiskLevel): boolean {
  return risk === 'low' || risk === 'reversible'
}

export function advancedJson(card: IntentCard | null): string {
  return card ? JSON.stringify(card, null, 2) : ''
}
