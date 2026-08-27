import http from './http'

export interface KnowledgeFileInfo {
  id: string
  filename: string
  file_md5: string
  file_type: string
  status: string
  created_at: string
}

export interface FaqPairInfo {
  id: number
  question: string
  answer: string
  source_file_id: string
  status: 'pending' | 'approved' | 'rejected'
  created_at: string
  reviewed_at: string | null
}

export const MAX_UPLOAD_SIZE = 20 * 1024 * 1024

export function fetchKnowledgeFiles() {
  return http.get<KnowledgeFileInfo[]>('/knowledge/files').then((r) => r.data)
}

export function uploadKnowledgeFile(file: File) {
  const form = new FormData()
  form.append('file', file)
  return http
    .post<KnowledgeFileInfo>('/knowledge/files', form, { headers: { 'Content-Type': 'multipart/form-data' } })
    .then((r) => r.data)
}

export function deleteKnowledgeFile(fileId: string) {
  return http.delete<void>(`/knowledge/files/${encodeURIComponent(fileId)}`)
}

export function rebuildVectorStore() {
  return http.post<{ status: string }>('/knowledge/rebuild').then((r) => r.data)
}

export const FAQ_GENERATION_TIMEOUT_MS = 120000

export function generateFaq(fileId: string) {
  return http
    .post<FaqPairInfo[]>(`/knowledge/files/${encodeURIComponent(fileId)}/generate-faq`, undefined, {
      timeout: FAQ_GENERATION_TIMEOUT_MS,
    })
    .then((r) => r.data)
}

export function fetchFaqs(status?: 'pending' | 'approved' | 'rejected') {
  return http
    .get<FaqPairInfo[]>('/knowledge/faq', { params: status ? { status } : {} })
    .then((r) => r.data)
}

export function reviewFaq(faqId: number, status: 'approved' | 'rejected') {
  return http.put<FaqPairInfo>(`/knowledge/faq/${faqId}/review`, { status }).then((r) => r.data)
}
