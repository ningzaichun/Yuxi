import assert from 'node:assert/strict'

import { ganttBar, ganttDayWidth, ganttDays, ganttWindow } from '../scheduleGantt.js'

const tasks = [
  { planned_start: '2026-10-12T08:00:00+08:00', planned_finish: '2026-10-12T17:00:00+08:00' },
  { planned_start: '2026-10-13T08:00:00+08:00', planned_finish: '2026-10-14T17:00:00+08:00' },
  { planned_start: '2026-10-21T17:00:00+08:00', planned_finish: '2026-10-21T17:00:00+08:00' }
]

const window = ganttWindow('2026-10-12T08:00:00+08:00', '2026-11-13T17:00:00+08:00', tasks)

assert.equal(window.days, 33) // 2026-10-12 .. 2026-11-13 inclusive
assert.equal(window.start.toISOString(), '2026-10-12T00:00:00.000Z')
assert.equal(window.finish.toISOString(), '2026-11-13T00:00:00.000Z')

const firstBar = ganttBar('2026-10-12T08:00:00+08:00', '2026-10-12T17:00:00+08:00', window)
assert.deepEqual(firstBar, { startIndex: 0, spanDays: 1 })

const middleBar = ganttBar('2026-10-13T08:00:00+08:00', '2026-10-14T17:00:00+08:00', window)
assert.deepEqual(middleBar, { startIndex: 1, spanDays: 2 })

const sameDayBar = ganttBar('2026-10-21T17:00:00+08:00', '2026-10-21T17:00:00+08:00', window)
assert.deepEqual(sameDayBar, { startIndex: 9, spanDays: 1 })

// Window falls back to task dates when the project period is missing.
const fallback = ganttWindow(null, null, tasks)
assert.equal(fallback.days, 10) // 2026-10-12 .. 2026-10-21 inclusive
const clipped = ganttBar('2026-10-21T17:00:00+08:00', '2026-10-21T17:00:00+08:00', fallback)
assert.deepEqual(clipped, { startIndex: 9, spanDays: 1 })

assert.equal(ganttWindow(null, null, []), null)

assert.equal(ganttDayWidth(33), 32)
assert.equal(ganttDayWidth(90), 16)
assert.equal(ganttDayWidth(300), 12)

// 2026-10-12 is a Monday; 2026-10-17/18 are Saturday/Sunday.
const days = ganttDays({ start: window.start, finish: window.finish, days: 7 })
assert.deepEqual(
  days.map((day) => ({ label: day.label, weekend: day.weekend })),
  [
    { label: '10-12', weekend: false },
    { label: '10-13', weekend: false },
    { label: '10-14', weekend: false },
    { label: '10-15', weekend: false },
    { label: '10-16', weekend: false },
    { label: '10-17', weekend: true },
    { label: '10-18', weekend: true }
  ]
)

console.log('scheduleGantt: all assertions passed')
