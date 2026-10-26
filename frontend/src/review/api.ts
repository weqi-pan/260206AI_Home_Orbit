import http from '../api/http'
import type { ReviewAction, ReviewQueueItem, ReviewReportItem, ReviewSubmissionDetail } from './types'

export const fetchReviewQueue = (status = '', entryId = '') =>
  http
    .get<{ items: ReviewQueueItem[] }>('/reviews/queue', {
      params: { status: status || undefined, entry_id: entryId || undefined },
    })
    .then((response) => response.data)

export const fetchReviewSubmission = (submissionId: string) =>
  http.get<ReviewSubmissionDetail>(`/reviews/submissions/${submissionId}`).then((response) => response.data)

export const decideReviewSubmission = (submissionId: string, action: ReviewAction, reason: string) =>
  http
    .post<{ submission_id: string; status: string }>(`/reviews/submissions/${submissionId}/decision`, {
      action,
      reason,
    })
    .then((response) => response.data)

export const takedownEntry = (entryId: string, reason: string) =>
  http
    .post<{ entry_id: string; status: string }>(`/reviews/entries/${entryId}/takedown`, { reason })
    .then((response) => response.data)

export const fetchReviewReports = (status = '') =>
  http
    .get<{ items: ReviewReportItem[] }>('/reviews/reports', { params: { status: status || undefined } })
    .then((response) => response.data)

export const handleReviewReport = (reportId: string, action: 'resolve' | 'dismiss', note: string) =>
  http
    .post<{ id: string; status: string }>(`/reviews/reports/${reportId}/handle`, { action, note })
    .then((response) => response.data)
