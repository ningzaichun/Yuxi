const SOURCE_SCHEMA_VERSION = 'microsoft_project_interchange_mock_v1.1'
const CANONICAL_SCHEMA_VERSION = 'canonical_schedule_v2.2'

export const MAX_SCHEDULE_JSON_BYTES = 10 * 1024 * 1024

export const parseScheduleJsonText = (text, fileName = '') =>
  parseScheduleDocument(JSON.parse(text.replace(/^\uFEFF/, '')), fileName)

export const parseScheduleDocument = (document, fileName = '') => {
  if (!document || typeof document !== 'object' || Array.isArray(document)) {
    throw new Error('排期文件根节点必须是 JSON 对象。')
  }
  if (document.schema_version === SOURCE_SCHEMA_VERSION) {
    return {
      kind: 'import',
      document,
      fileName,
      schemaVersion: document.schema_version,
      externalProjectId: document.project?.project_id || '',
      externalSnapshotId: document.source?.mpp_sha256 ? `mpp:${document.source.mpp_sha256}` : '',
      externalRevision: document.artifact_version || '',
      summary: scheduleSummary(document)
    }
  }
  if (document.schema_version === CANONICAL_SCHEMA_VERSION) {
    return {
      kind: 'snapshot',
      document,
      fileName,
      schemaVersion: document.schema_version,
      externalProjectId: document.project?.project_id || '',
      externalSnapshotId: document.snapshot_id || '',
      externalRevision: document.generated_at || '',
      summary: scheduleSummary(document)
    }
  }
  throw new Error(`不支持的排期 JSON 格式：${document.schema_version || '缺少 schema_version'}`)
}

export const buildScheduleSubmission = (
  parsed,
  { requestId, externalProjectId, externalSnapshotId, externalRevision }
) => {
  const identity = {
    request_id: requiredValue(requestId, 'request_id'),
    external_project_id: requiredValue(externalProjectId, '外部项目 ID'),
    external_snapshot_id: requiredValue(externalSnapshotId, '外部快照 ID'),
    external_revision: requiredValue(externalRevision, '外部版本')
  }
  return parsed.kind === 'import'
    ? { ...identity, document: parsed.document }
    : { ...identity, snapshot: parsed.document }
}

export const scheduleSubmissionErrorMessage = (error) => {
  const status = error?.response?.status
  const detail = error?.response?.data?.detail
  if (status === 422 && detail?.errors?.length) {
    const errors = detail.errors
      .slice(0, 3)
      .map((item) => `${item.path} ${item.message}`)
      .join('；')
    const suffix = detail.errors.length > 3 ? `；另有 ${detail.errors.length - 3} 项错误` : ''
    return `${detail.message || '排期数据校验失败'}：${errors}${suffix}`
  }
  if (status === 409 && detail?.code === 'SCHEDULE_SUBMISSION_IN_PROGRESS') {
    return '相同请求正在处理中，请稍后使用当前文件重试。'
  }
  if (status === 409) {
    return '当前提交标识已用于不同的排期内容或外部身份，请重新选择文件后提交。'
  }
  if (status === 500) {
    return '排期保存失败，请保留当前弹窗并原样重试。'
  }
  return detail?.message || error?.message || '排期提交失败'
}

const scheduleSummary = (document) => ({
  projectName: document.project?.name || '未命名项目',
  tasks: Array.isArray(document.tasks) ? document.tasks.length : 0,
  dependencies: Array.isArray(document.dependencies) ? document.dependencies.length : 0
})

const requiredValue = (value, label) => {
  const normalized = typeof value === 'string' ? value.trim() : ''
  if (!normalized) throw new Error(`${label}不能为空。`)
  return normalized
}
