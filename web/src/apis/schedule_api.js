import { apiGet, apiPost, apiPut } from './base'

const queryString = (values) => {
  const query = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  })
  return query.toString()
}

export const scheduleApi = {
  submitImport: (payload) => apiPost('/api/schedule/imports', payload),
  submitSnapshot: (payload) => apiPost('/api/schedule/snapshots', payload),
  listSnapshots: ({ limit = 50, offset = 0 } = {}) =>
    apiGet(`/api/schedule/snapshots?${queryString({ limit, offset })}`),
  getSnapshot: (snapshotId) => apiGet(`/api/schedule/snapshots/${encodeURIComponent(snapshotId)}`),
  getAudit: (snapshotId) =>
    apiGet(`/api/schedule/snapshots/${encodeURIComponent(snapshotId)}/audit`),
  listIssues: (
    snapshotId,
    { category = '', severity = '', limit = 100, offset = 0 } = {}
  ) =>
    apiGet(
      `/api/schedule/snapshots/${encodeURIComponent(snapshotId)}/issues?${queryString({
        category,
        severity,
        limit,
        offset
      })}`
    ),
  getIssue: (issueId) => apiGet(`/api/schedule/issues/${encodeURIComponent(issueId)}`),
  getDependencyWorkbench: (issueId) =>
    apiGet(`/api/schedule/issues/${encodeURIComponent(issueId)}/dependency-workbench`),
  saveDependencyDecision: (issueId, payload) =>
    apiPut(`/api/schedule/issues/${encodeURIComponent(issueId)}/dependency-decision`, payload),
  confirmDependencyDecision: (issueId) =>
    apiPost(`/api/schedule/issues/${encodeURIComponent(issueId)}/dependency-decision/confirm`, {}),
  createDependencyCandidate: (snapshotId, payload) =>
    apiPost(`/api/schedule/snapshots/${encodeURIComponent(snapshotId)}/optimizations`, payload),
  createForwardRecalculation: (snapshotId, payload) =>
    apiPost(
      `/api/schedule/snapshots/${encodeURIComponent(snapshotId)}/recalculate-automatic-downstream`,
      payload
    ),
  createGoalOptimization: (snapshotId, payload) =>
    apiPost(
      `/api/schedule/snapshots/${encodeURIComponent(snapshotId)}/goal-optimizations`,
      payload
    ),
  getCandidate: (candidateId) =>
    apiGet(`/api/schedule/candidates/${encodeURIComponent(candidateId)}`),
  recordCandidateDecision: (candidateId, payload) =>
    apiPost(`/api/schedule/candidates/${encodeURIComponent(candidateId)}/decisions`, payload),
  getCandidateDelivery: (candidateId) =>
    apiGet(`/api/schedule/candidates/${encodeURIComponent(candidateId)}/delivery`),
  getCandidateAcceptanceEvidence: (candidateId) =>
    apiGet(`/api/schedule/candidates/${encodeURIComponent(candidateId)}/acceptance-evidence`),
  listCapableAgents: ({ scope = 'issue' } = {}) =>
    apiGet(`/api/schedule/agents?${queryString({ scope })}`)
}
