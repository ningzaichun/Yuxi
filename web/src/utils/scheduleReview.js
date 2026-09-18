/** Return whether a source task can be offered for authorized duration optimization. */
export const isGoalOptimizableTask = (task) => {
  if (
    task?.task_type !== 'activity' ||
    task.active === false ||
    task.scheduling_mode !== 'automatic' ||
    task.duration_minutes <= 1
  ) {
    return false
  }

  if (task.status) return task.status === 'NOT_STARTED'
  return task.percent_complete === 0 && !task.actual_start && !task.actual_finish
}

/** Return shared source parameters only when every dependency agrees. */
export const uniformDependencyParameters = (dependencies) => {
  if (!dependencies?.length) return null
  const first = dependencies[0]
  return dependencies.every(
    (dependency) =>
      dependency.type === first.type && dependency.lag_minutes === first.lag_minutes
  )
    ? { type: first.type, lag_minutes: first.lag_minutes }
    : null
}

/** Label source dates without presenting test references or Yuxi candidates as MPP facts. */
export const scheduleSourcePresentation = (snapshot) => {
  const source = snapshot?.source || {}
  if (source.format === 'MPP') {
    return { kind: 'mpp', label: 'Microsoft Project 来源计划日期' }
  }
  if (source.extraction_method?.startsWith('YUXI_TEST_SUITE_')) {
    return { kind: 'suite', label: 'Suite Reference 测试参考日期' }
  }
  return { kind: 'source', label: '外部来源计划日期' }
}

/** Project the CPM capability into one stable UI action state. */
export const cpmActionState = (audit) => {
  const capability = audit?.capabilities?.cpm_recalculation
  return {
    allowed: capability?.allowed === true,
    reasons: capability?.reasons || []
  }
}
