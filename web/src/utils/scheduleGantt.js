/**
 * Lightweight Gantt geometry for the schedule review page.
 *
 * Dates are positioned at whole-day granularity using the calendar date
 * portion of ISO strings, so the layout is independent of browser timezone.
 * Bars reflect the calendar span of planned dates, not working-calendar time.
 */

const DAY_MS = 24 * 60 * 60 * 1000

const dayParts = (value) => {
  if (value instanceof Date) {
    return { year: value.getUTCFullYear(), month: value.getUTCMonth(), date: value.getUTCDate() }
  }
  const [year, month, date] = String(value).slice(0, 10).split('-').map(Number)
  return { year, month: month - 1, date }
}

const calendarDay = (value) => {
  const { year, month, date } = dayParts(value)
  return new Date(Date.UTC(year, month, date))
}

const wholeDaysBetween = (start, finish) =>
  Math.round((calendarDay(finish).getTime() - calendarDay(start).getTime()) / DAY_MS)

const isWeekend = (date) => {
  const day = date.getUTCDay()
  return day === 0 || day === 6
}

/**
 * Time window covering every planned task date, expanded to the project
 * planned period when available.
 * @returns {{ start: Date, finish: Date, days: number } | null}
 */
export const ganttWindow = (projectStart, projectFinish, tasks) => {
  const dated = (tasks || []).filter((task) => task.planned_start && task.planned_finish)
  if (!dated.length) return null
  const starts = dated.map((task) => calendarDay(task.planned_start).getTime())
  const finishes = dated.map((task) => calendarDay(task.planned_finish).getTime())
  const startMs = Math.min(
    projectStart ? calendarDay(projectStart).getTime() : Number.POSITIVE_INFINITY,
    ...starts
  )
  const finishMs = Math.max(
    projectFinish ? calendarDay(projectFinish).getTime() : Number.NEGATIVE_INFINITY,
    ...finishes
  )
  const start = new Date(startMs)
  const finish = new Date(finishMs)
  return { start, finish, days: wholeDaysBetween(start, finish) + 1 }
}

/**
 * Bar geometry for one task inside a gantt window.
 * @returns {{ startIndex: number, spanDays: number }}
 */
export const ganttBar = (plannedStart, plannedFinish, window) => {
  const startIndex = wholeDaysBetween(window.start, plannedStart)
  const finishIndex = wholeDaysBetween(window.start, plannedFinish)
  return { startIndex, spanDays: Math.max(finishIndex - startIndex + 1, 1) }
}

/** Adaptive day width so long schedules stay scrollable and short ones readable. */
export const ganttDayWidth = (days) => (days <= 45 ? 32 : Math.max(12, Math.floor(1440 / days)))

/**
 * Header day cells for the whole window.
 * @returns {Array<{ key: string, label: string, weekend: boolean, index: number }>}
 */
export const ganttDays = (window) =>
  Array.from({ length: window.days }, (_, index) => {
    const date = new Date(window.start.getTime() + index * DAY_MS)
    return {
      key: date.toISOString().slice(0, 10),
      label: `${String(date.getUTCMonth() + 1).padStart(2, '0')}-${String(date.getUTCDate()).padStart(2, '0')}`,
      weekend: isWeekend(date),
      index
    }
  })
