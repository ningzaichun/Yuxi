export const scheduleReviewSuggestions = [
  {
    key: 'summary',
    label: '概括主要问题',
    question: '这份计划最主要的问题是什么？请区分确定性发现、管理质量提醒和待业务确认事项。'
  },
  {
    key: 'capabilities',
    label: '解释能力阻断',
    question: '当前数据为什么不能进行某些计算或优化？请先解释 Capability 阻断原因。'
  },
  {
    key: 'priority',
    label: '建议处理顺序',
    question: '我应该优先处理哪些问题？请按既定优先级口径说明，并把同级排序明确标为建议。'
  }
]

export const buildScheduleReviewRouteQuery = (agentId, snapshot) => ({
  agent_id: requiredString(agentId, 'agent_id'),
  schedule_snapshot_id: requiredString(snapshot?.schedule_snapshot_id, 'schedule_snapshot_id'),
  schedule_snapshot_sha256: requiredString(
    snapshot?.snapshot_content_sha256,
    'snapshot_content_sha256'
  ),
  schedule_external_project_id: requiredString(
    snapshot?.external_project_id,
    'external_project_id'
  ),
  schedule_external_revision: requiredString(snapshot?.external_revision, 'external_revision')
})

export const scheduleReviewContextFromQuery = (query) => {
  const snapshotId = queryString(query?.schedule_snapshot_id)
  const context = {
    scheduleSnapshotId: snapshotId,
    snapshotContentSha256: queryString(query?.schedule_snapshot_sha256),
    externalProjectId: queryString(query?.schedule_external_project_id),
    externalRevision: queryString(query?.schedule_external_revision),
    mode: queryString(query?.schedule_goal_optimization) === '1' ? 'goal_optimization' : 'review'
  }
  return hasCompleteReviewIdentity(context) ? context : null
}

export const buildScheduleReviewThreadMetadata = (context) => ({
  schedule_review: {
    schedule_snapshot_id: requiredString(context?.scheduleSnapshotId, 'schedule_snapshot_id'),
    snapshot_content_sha256: requiredString(
      context?.snapshotContentSha256,
      'snapshot_content_sha256'
    ),
    external_project_id: requiredString(context?.externalProjectId, 'external_project_id'),
    external_revision: requiredString(context?.externalRevision, 'external_revision'),
    mode: context?.mode === 'goal_optimization' ? 'goal_optimization' : 'review'
  }
})

export const scheduleReviewContextFromMetadata = (metadata) => {
  const review = metadata?.schedule_review
  const context = {
    scheduleSnapshotId: queryString(review?.schedule_snapshot_id),
    snapshotContentSha256: queryString(review?.snapshot_content_sha256),
    externalProjectId: queryString(review?.external_project_id),
    externalRevision: queryString(review?.external_revision),
    mode: review?.mode === 'goal_optimization' ? 'goal_optimization' : 'review'
  }
  return hasCompleteReviewIdentity(context) ? context : null
}

export const buildScheduleReviewDraft = (context, question = scheduleReviewSuggestions[0].question) => {
  const snapshotId = requiredString(context?.scheduleSnapshotId, 'schedule_snapshot_id')
  const expectedHash = requiredString(context?.snapshotContentSha256, 'snapshot_content_sha256')
  return `请审查整份排期快照。
schedule_snapshot_id=${snapshotId}
expected_snapshot_content_sha256=${expectedHash}

必须先调用 get_schedule_review_context，并确认工具返回的 Snapshot ID 和内容哈希与上述值一致。只能依据工具返回的 YUXI_AUDIT、Issue 和 Capability 回答；涉及具体 Issue 时再调用 get_schedule_issue_context，并使用 evidence_locator 提供证据链接。不得读取或推断完整来源 JSON、Notes、未知扩展字段或 ignored/unsupported 字段，不得自行计算日期、关键路径、成本或 Patch；没有版本化规则和证据时，不得判断工程业务合理性。

${question}`
}

export const buildScheduleGoalDraft = (context) => {
  const snapshotId = requiredString(context?.scheduleSnapshotId, 'schedule_snapshot_id')
  const expectedHash = requiredString(context?.snapshotContentSha256, 'snapshot_content_sha256')
  return `请帮我梳理工期目标优化意图。
schedule_snapshot_id=${snapshotId}
expected_snapshot_content_sha256=${expectedHash}

必须先调用 get_schedule_review_context 和 get_schedule_goal_optimization_context，并确认 Snapshot ID 与内容哈希一致。依次确认：主要目标、目标完成时间（如适用）、明确授权可压缩的自动活动任务、授权后的工期、locked tasks。不得替我推断可压缩工期或伪造授权；依赖、Lag、日历、里程碑和任务模式全部保持不变。本对话只梳理意图，不创建 Candidate；最后请让我回到排期页面完成明确授权和提交。`
}

const queryString = (value) => (typeof value === 'string' ? value.trim() : '')

const hasCompleteReviewIdentity = (context) =>
  Boolean(
    context.scheduleSnapshotId &&
      context.snapshotContentSha256 &&
      context.externalProjectId &&
      context.externalRevision
  )

const requiredString = (value, name) => {
  const normalized = queryString(value)
  if (!normalized) throw new Error(`${name}不能为空。`)
  return normalized
}
