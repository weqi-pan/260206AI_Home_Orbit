import http from './http'
import type { CeilingTool, CreatorDraftDetail, CreatorDraftSummary, IntentCard, PublishedAgent, PublishResult, TrialSummary, ValidationReport } from '../creator/types'

export const fetchCeilingTools = () =>
  http.get<CeilingTool[]>('/creator/tools').then((response) => response.data)

export const fetchDrafts = () =>
  http.get<CreatorDraftSummary[]>('/creator/drafts').then((response) => response.data)

export const createDraft = (title: string, description: string) =>
  http.post<CreatorDraftDetail>('/creator/drafts', { title, description }).then((response) => response.data)

export const fetchDraft = (draftId: string) =>
  http.get<CreatorDraftDetail>(`/creator/drafts/${draftId}`).then((response) => response.data)

export const updateDraft = (draftId: string, payload: { title?: string; intent_card?: IntentCard }) =>
  http.patch<CreatorDraftDetail>(`/creator/drafts/${draftId}`, payload).then((response) => response.data)

export const generateCard = (draftId: string, description: string) =>
  http.post<{ intent_card: IntentCard; validation: ValidationReport; status: string }>(
    `/creator/drafts/${draftId}/generate`, { description }).then((response) => response.data)

export const validateDraft = (draftId: string) =>
  http.post<{ status: string; validation: ValidationReport }>(`/creator/drafts/${draftId}/validate`)
    .then((response) => response.data)

export const uploadKnowledge = (draftId: string, file: File, skillKey: string) => {
  const form = new FormData()
  form.append('file', file)
  form.append('skill_key', skillKey)
  return http.post(`/creator/drafts/${draftId}/knowledge`, form, { params: { skill_key: skillKey } })
    .then((response) => response.data)
}

export const deleteKnowledge = (draftId: string, assetId: string) =>
  http.delete(`/creator/drafts/${draftId}/knowledge/${assetId}`)

export const runTrial = (draftId: string, grantedToolKeys: string[]) =>
  http.post<{ report_id: string; status: string; cases: TrialSummary['cases'] }>(
    `/creator/drafts/${draftId}/trial`, { granted_tool_keys: grantedToolKeys })
    .then((response) => response.data)

export const fetchTrial = (draftId: string, reportId: string) =>
  http.get<TrialSummary>(`/creator/drafts/${draftId}/trials/${reportId}`).then((response) => response.data)

export const publishDraft = (draftId: string, grantedToolKeys: string[]) =>
  http.post<PublishResult>(`/creator/drafts/${draftId}/publish`, { granted_tool_keys: grantedToolKeys })
    .then((response) => response.data)

export const fetchMyAgents = () =>
  http.get<PublishedAgent[]>('/creator/agents').then((response) => response.data)
