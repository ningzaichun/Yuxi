import assert from 'node:assert/strict'

import {
  buildScheduleGoalDraft,
  buildScheduleReviewDraft,
  buildScheduleReviewRouteQuery,
  buildScheduleReviewThreadMetadata,
  scheduleReviewContextFromQuery,
  scheduleReviewContextFromMetadata,
  scheduleReviewSuggestions
} from '../scheduleAgentEntry.js'

const snapshot = {
  schedule_snapshot_id: 'snapshot-1',
  snapshot_content_sha256: 'sha256:canonical',
  external_project_id: 'project-1',
  external_revision: 'V3',
  snapshot: { notes: 'must-not-enter-route' },
  normalization_report: { unknown_field: 'must-not-enter-route' }
}

const query = buildScheduleReviewRouteQuery('agent-1', snapshot)
assert.deepEqual(query, {
  agent_id: 'agent-1',
  schedule_snapshot_id: 'snapshot-1',
  schedule_snapshot_sha256: 'sha256:canonical',
  schedule_external_project_id: 'project-1',
  schedule_external_revision: 'V3'
})
assert.equal(JSON.stringify(query).includes('must-not-enter-route'), false)

const context = scheduleReviewContextFromQuery(query)
assert.deepEqual(context, {
  scheduleSnapshotId: 'snapshot-1',
  snapshotContentSha256: 'sha256:canonical',
  externalProjectId: 'project-1',
  externalRevision: 'V3',
  mode: 'review'
})
assert.equal(scheduleReviewContextFromQuery({}), null)
assert.equal(scheduleReviewContextFromQuery({ schedule_snapshot_id: 'snapshot-1' }), null)

const metadata = buildScheduleReviewThreadMetadata(context)
assert.deepEqual(metadata, {
  schedule_review: {
    schedule_snapshot_id: 'snapshot-1',
    snapshot_content_sha256: 'sha256:canonical',
    external_project_id: 'project-1',
    external_revision: 'V3',
    mode: 'review'
  }
})
assert.deepEqual(scheduleReviewContextFromMetadata(metadata), context)
assert.equal(scheduleReviewContextFromMetadata({ schedule_review: { schedule_snapshot_id: 'snapshot-1' } }), null)

const goalContext = scheduleReviewContextFromQuery({ ...query, schedule_goal_optimization: '1' })
assert.equal(goalContext.mode, 'goal_optimization')
assert.equal(buildScheduleReviewThreadMetadata(goalContext).schedule_review.mode, 'goal_optimization')

const draft = buildScheduleReviewDraft(context, scheduleReviewSuggestions[2].question)
assert.match(draft, /get_schedule_review_context/)
assert.match(draft, /schedule_snapshot_id=snapshot-1/)
assert.match(draft, /expected_snapshot_content_sha256=sha256:canonical/)
assert.match(draft, /同级排序明确标为建议/)
assert.match(draft, /不得判断工程业务合理性/)

const goalDraft = buildScheduleGoalDraft(context)
assert.match(goalDraft, /get_schedule_goal_optimization_context/)
assert.match(goalDraft, /不得替我推断可压缩工期或伪造授权/)
assert.match(goalDraft, /本对话只梳理意图，不创建 Candidate/)

assert.throws(() => buildScheduleReviewRouteQuery('', snapshot), /agent_id不能为空/)
