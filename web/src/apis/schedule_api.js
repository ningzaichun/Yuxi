import { apiGet, apiPost } from './base'

const queryString = (values) => {
  const query = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  })
  return query.toString()
}

export const scheduleApi = {
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
  listCapableAgents: () => apiGet('/api/schedule/agents')
}
