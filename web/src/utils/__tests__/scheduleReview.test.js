import assert from 'node:assert/strict'

import {
  cpmActionState,
  isGoalOptimizableTask,
  scheduleSourcePresentation,
  uniformDependencyParameters
} from '../scheduleReview.js'

const activity = {
  task_type: 'activity',
  active: true,
  scheduling_mode: 'automatic',
  duration_minutes: 480,
  percent_complete: 0,
  actual_start: null,
  actual_finish: null
}

assert.equal(isGoalOptimizableTask(activity), true)
assert.equal(isGoalOptimizableTask({ ...activity, status: 'NOT_STARTED' }), true)
assert.equal(isGoalOptimizableTask({ ...activity, status: 'IN_PROGRESS' }), false)
assert.equal(isGoalOptimizableTask({ ...activity, status: 'COMPLETED' }), false)
assert.equal(isGoalOptimizableTask({ ...activity, percent_complete: 30 }), false)
assert.equal(isGoalOptimizableTask({ ...activity, actual_start: '2026-08-20T08:00:00+08:00' }), false)
assert.equal(isGoalOptimizableTask({ ...activity, active: false }), false)

const fs = { type: 'FS', lag_minutes: 0 }
assert.deepEqual(uniformDependencyParameters([fs]), fs)
assert.deepEqual(uniformDependencyParameters([fs, { ...fs }]), fs)
assert.equal(uniformDependencyParameters([fs, { type: 'SS', lag_minutes: 0 }]), null)
assert.equal(uniformDependencyParameters([fs, { type: 'FS', lag_minutes: 60 }]), null)
assert.equal(uniformDependencyParameters([]), null)

assert.deepEqual(scheduleSourcePresentation({ source: { format: 'MPP' } }), {
  kind: 'mpp',
  label: 'Microsoft Project 来源计划日期'
})
assert.deepEqual(
  scheduleSourcePresentation({ source: { extraction_method: 'YUXI_TEST_SUITE_ADAPTER' } }),
  { kind: 'suite', label: 'Suite Reference 测试参考日期' }
)
assert.deepEqual(
  scheduleSourcePresentation({
    source: { extraction_method: 'YUXI_TEST_SUITE_ENGINE_REFERENCE' }
  }),
  { kind: 'suite', label: 'Suite Reference 测试参考日期' }
)
assert.deepEqual(scheduleSourcePresentation({ source: { format: 'VENDOR' } }), {
  kind: 'source',
  label: '外部来源计划日期'
})

assert.deepEqual(cpmActionState(), { allowed: false, reasons: [] })
assert.deepEqual(
  cpmActionState({
    capabilities: { cpm_recalculation: { allowed: false, reasons: ['MILESTONE_UNSUPPORTED'] } }
  }),
  { allowed: false, reasons: ['MILESTONE_UNSUPPORTED'] }
)
assert.deepEqual(
  cpmActionState({ capabilities: { cpm_recalculation: { allowed: true, reasons: [] } } }),
  { allowed: true, reasons: [] }
)

console.log('scheduleReview: all assertions passed')
