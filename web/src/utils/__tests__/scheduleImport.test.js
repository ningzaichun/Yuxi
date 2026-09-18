import assert from 'node:assert/strict'

import {
  buildScheduleSubmission,
  parseScheduleDocument,
  parseScheduleJsonText,
  scheduleSubmissionErrorMessage
} from '../scheduleImport.js'

const sourceDocument = {
  schema_version: 'microsoft_project_interchange_mock_v1.1',
  artifact_version: 'v1.1',
  source: { mpp_sha256: 'a'.repeat(64) },
  project: { project_id: 'project:pump', name: '水泵站' },
  tasks: [{ task_id: 'task:1' }],
  dependencies: []
}

const canonicalDocument = {
  schema_version: 'canonical_schedule_v2.2',
  snapshot_id: 'snapshot:canonical-1',
  generated_at: '2026-08-14T10:00:00+08:00',
  project: { project_id: 'project:canonical', name: 'Canonical 案例' },
  tasks: [],
  dependencies: []
}

const source = parseScheduleDocument(sourceDocument, 'pump.json')
assert.equal(source.kind, 'import')
assert.equal(source.externalProjectId, 'project:pump')
assert.equal(source.externalSnapshotId, `mpp:${'a'.repeat(64)}`)
assert.equal(source.externalRevision, 'v1.1')
assert.equal(source.summary.tasks, 1)
assert.equal(
  parseScheduleJsonText(`\uFEFF${JSON.stringify(sourceDocument)}`, 'pump-bom.json').kind,
  'import'
)

const formalSource = parseScheduleDocument(
  { ...sourceDocument, schema_version: 'microsoft_project_interchange_v1.1' },
  'formal-source.json'
)
assert.equal(formalSource.kind, 'import')

for (const version of [
  'canonical_schedule_v2.2',
  'canonical_schedule_v2.3',
  'canonical_schedule_v2.4',
  'canonical_schedule_v2.5',
  'canonical_schedule_v2.6',
  'canonical_schedule_v2.7',
  'canonical_schedule_v2.8'
]) {
  assert.equal(parseScheduleDocument({ ...canonicalDocument, schema_version: version }).kind, 'snapshot')
}

const sourceSubmission = buildScheduleSubmission(source, {
  requestId: 'schedule-import-request-1',
  externalProjectId: source.externalProjectId,
  externalSnapshotId: source.externalSnapshotId,
  externalRevision: source.externalRevision
})
assert.deepEqual(sourceSubmission, {
  request_id: 'schedule-import-request-1',
  external_project_id: 'project:pump',
  external_snapshot_id: `mpp:${'a'.repeat(64)}`,
  external_revision: 'v1.1',
  document: sourceDocument
})

const canonical = parseScheduleDocument(canonicalDocument, 'canonical.json')
assert.equal(canonical.kind, 'snapshot')
assert.equal(canonical.externalSnapshotId, 'snapshot:canonical-1')
assert.equal(canonical.externalRevision, '2026-08-14T10:00:00+08:00')
assert.ok('snapshot' in buildScheduleSubmission(canonical, {
  requestId: 'schedule-snapshot-request-1',
  externalProjectId: canonical.externalProjectId,
  externalSnapshotId: canonical.externalSnapshotId,
  externalRevision: canonical.externalRevision
}))

assert.throws(
  () => parseScheduleDocument({ schema_version: 'unknown_v1' }, 'unknown.json'),
  /不支持的排期 JSON 格式/
)
assert.equal(
  scheduleSubmissionErrorMessage({
    response: {
      status: 422,
      data: {
        detail: {
          message: '排期来源数据不符合声明的导入格式',
          errors: [{ path: '/document/project/name', message: 'Field required' }]
        }
      }
    }
  }),
  '排期来源数据不符合声明的导入格式：/document/project/name Field required'
)
assert.equal(
  scheduleSubmissionErrorMessage({
    response: {
      status: 422,
      data: {
        detail: {
          message: '排期数据未通过结构预检',
          errors: [
            {
              code: 'DEPENDENCY_CYCLE',
              object_ref: null,
              object_refs: ['task:a', 'task:b'],
              message: '任务依赖网络存在循环。'
            }
          ]
        }
      }
    }
  }),
  '排期数据未通过结构预检：task:a、task:b 任务依赖网络存在循环。'
)
assert.equal(
  scheduleSubmissionErrorMessage({ response: { status: 409, data: { detail: { code: 'SCHEDULE_SUBMISSION_IN_PROGRESS' } } } }),
  '相同请求正在处理中，请稍后使用当前文件重试。'
)

console.log('scheduleImport: all assertions passed')
