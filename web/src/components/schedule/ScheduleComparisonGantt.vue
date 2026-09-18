<template>
  <div v-if="layout" class="comparison-gantt">
    <div class="gantt-legend">
      <span class="legend-item"><span class="legend-swatch legend-source" />{{ sourceLabel }}</span>
      <span class="legend-item"><span class="legend-swatch legend-optimized" />{{ optimizedLabel }}</span>
      <span class="legend-item legend-note">深色描边 = 日期有变化</span>
      <span v-if="layout.markers.length" class="legend-item legend-note">竖线 = 完成时刻</span>
    </div>
    <div class="gantt-scroll">
      <div class="gantt-canvas" :style="{ width: `${layout.canvasWidth}px` }">
        <div class="gantt-header">
          <span class="gantt-label gantt-header-label">任务</span>
          <div class="gantt-track">
            <div
              v-for="day in layout.days"
              :key="day.key"
              class="gantt-day"
              :class="{ 'gantt-day-weekend': day.weekend }"
              :style="{ width: `${layout.dayWidth}px` }"
            >
              {{ day.label }}
            </div>
          </div>
        </div>
        <div class="gantt-body">
          <div
            v-for="cell in layout.weekendCells"
            :key="cell.key"
            class="gantt-weekend-cell"
            :style="{ left: `${cell.left}px`, width: `${cell.width}px` }"
          />
          <div
            v-for="row in layout.rows"
            :key="row.task_id"
            class="gantt-row"
            :class="{
              'gantt-row-summary': row.summary,
              'gantt-row-milestone': row.milestone,
              'gantt-row-inactive': row.active === false
            }"
          >
            <span class="gantt-label" :title="`${row.wbs} ${row.name}`">
              {{ row.wbs }} {{ row.name }}
              <span v-if="row.milestone" class="gantt-task-kind">里程碑</span>
              <span v-if="row.active === false" class="gantt-task-kind">inactive</span>
            </span>
            <div class="gantt-track">
              <div
                class="gantt-bar gantt-bar-source"
                :class="{ 'gantt-bar-milestone': row.milestone }"
                :style="row.sourceStyle"
                :title="`${row.name} ${sourceLabel}：${formatDate(row.before.start)} → ${formatDate(row.before.finish)}`"
              />
              <div
                class="gantt-bar gantt-bar-optimized"
                :class="{
                  'gantt-bar-changed': row.changed,
                  'gantt-bar-milestone': row.milestone
                }"
                :style="row.optimizedStyle"
                :title="`${row.name} ${optimizedLabel}：${formatDate(row.optimized.start)} → ${formatDate(row.optimized.finish)}`"
              />
            </div>
          </div>
        </div>
        <div
          v-for="marker in layout.markers"
          :key="marker.key"
          class="gantt-marker"
          :class="`gantt-marker-${marker.kind}`"
          :style="{ left: `${marker.left}px` }"
          :title="marker.label"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

import { ganttBar, ganttDayWidth, ganttDays, ganttWindow } from '@/utils/scheduleGantt'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  finishBefore: { type: String, default: null },
  finishAfter: { type: String, default: null },
  sourceLabel: { type: String, default: '来源计划' },
  optimizedLabel: { type: String, default: '优化后' },
  beforeLabel: { type: String, default: '基线完成' },
  afterLabel: { type: String, default: '优化完成' },
  labelWidth: { type: Number, default: 220 }
})

const formatDate = (value) => (value ? new Date(value).toLocaleString() : '—')

const barStyle = (bar, dayWidth, milestone = false) => {
  const left = bar.startIndex * dayWidth
  if (milestone) return { left: `${left + dayWidth / 2 - 4}px`, width: '8px' }
  return { left: `${left + 1}px`, width: `${Math.max(bar.spanDays * dayWidth - 2, 6)}px` }
}

const layout = computed(() => {
  const rows = props.rows.filter(
    (row) => row?.before?.start && row?.before?.finish && row?.optimized?.start && row?.optimized?.finish
  )
  if (!rows.length) return null
  const ranges = []
  rows.forEach((row) => {
    ranges.push({ planned_start: row.before.start, planned_finish: row.before.finish })
    ranges.push({ planned_start: row.optimized.start, planned_finish: row.optimized.finish })
  })
  const window = ganttWindow(null, null, ranges)
  if (!window) return null
  const dayWidth = ganttDayWidth(window.days)
  const days = ganttDays(window)
  const markers = []
  if (props.finishBefore) {
    const startIndex = ganttBar(props.finishBefore, props.finishBefore, window).startIndex
    markers.push({
      key: 'before',
      kind: 'before',
      left: props.labelWidth + (startIndex + 1) * dayWidth,
      label: `${props.beforeLabel} ${formatDate(props.finishBefore)}`
    })
  }
  if (props.finishAfter) {
    const startIndex = ganttBar(props.finishAfter, props.finishAfter, window).startIndex
    markers.push({
      key: 'after',
      kind: 'after',
      left: props.labelWidth + (startIndex + 1) * dayWidth,
      label: `${props.afterLabel} ${formatDate(props.finishAfter)}`
    })
  }
  return {
    dayWidth,
    canvasWidth: props.labelWidth + window.days * dayWidth,
    days,
    weekendCells: days
      .filter((day) => day.weekend)
      .map((day) => ({
        key: day.key,
        left: props.labelWidth + day.index * dayWidth,
        width: dayWidth
      })),
    markers,
    rows: rows.map((row) => {
      const milestone = row.task_type === 'milestone' || row.duration_minutes === 0
      return {
        task_id: row.task_id,
        wbs: row.wbs,
        name: row.name,
        summary: row.summary,
        milestone,
        changed: row.changed,
        active: row.active,
        before: row.before,
        optimized: row.optimized,
        sourceStyle: barStyle(
          ganttBar(row.before.start, row.before.finish, window),
          dayWidth,
          milestone
        ),
        optimizedStyle: barStyle(
          ganttBar(row.optimized.start, row.optimized.finish, window),
          dayWidth,
          milestone
        )
      }
    })
  }
})
</script>

<style lang="less" scoped>
.gantt-legend {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.legend-swatch {
  width: 14px;
  height: 8px;
  border-radius: 2px;
}

.legend-source {
  background: var(--gray-400);
}

.legend-optimized {
  background: var(--main-500);
}

.legend-note {
  color: var(--color-text-tertiary);
}

.gantt-scroll {
  overflow-x: auto;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--color-bg-container);
}

.gantt-canvas {
  position: relative;
  min-width: 100%;
}

.gantt-header {
  display: flex;
  align-items: stretch;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-10);
}

.gantt-label {
  flex: none;
  width: 220px;
  padding: 5px 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--color-text-secondary);
  border-right: 1px solid var(--gray-150);
  box-sizing: border-box;
}

.gantt-header-label {
  font-weight: 600;
  color: var(--color-text);
}

.gantt-track {
  position: relative;
  display: flex;
  flex: 1;
}

.gantt-day {
  flex: none;
  padding: 5px 0;
  text-align: center;
  font-size: 11px;
  color: var(--color-text-tertiary);
  border-right: 1px solid var(--gray-100);
  box-sizing: border-box;
}

.gantt-day-weekend {
  background: var(--gray-25);
}

.gantt-body {
  position: relative;
}

.gantt-weekend-cell {
  position: absolute;
  top: 0;
  bottom: 0;
  background: var(--gray-25);
  pointer-events: none;
}

.gantt-row {
  display: flex;
  align-items: center;
  height: 28px;
  border-bottom: 1px solid var(--gray-100);
}

.gantt-row:last-child {
  border-bottom: 0;
}

.gantt-row .gantt-label {
  color: var(--color-text);
}

.gantt-row-summary .gantt-label {
  font-weight: 600;
}

.gantt-row-inactive {
  opacity: 0.58;
}

.gantt-row-inactive .gantt-bar {
  background: var(--gray-400);
  border: 1px dashed var(--gray-600);
}

.gantt-task-kind {
  margin-left: 4px;
  color: var(--main-700);
}

.gantt-bar {
  position: absolute;
  top: 6px;
  height: 14px;
  border-radius: 3px;
  box-sizing: border-box;
}

.gantt-bar-source {
  top: 3px;
  height: 8px;
  background: var(--gray-400);
}

.gantt-bar-optimized {
  top: 13px;
  height: 10px;
  background: var(--main-500);
}

.gantt-bar-changed {
  border: 1px solid var(--main-700);
}

.gantt-bar-milestone {
  width: 8px;
  height: 8px;
  border-radius: 1px;
  transform: rotate(45deg);
}

.gantt-bar-source.gantt-bar-milestone {
  top: 3px;
}

.gantt-bar-optimized.gantt-bar-milestone {
  top: 16px;
}

.gantt-marker {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  pointer-events: none;
}

.gantt-marker-before {
  background: var(--gray-500);
}

.gantt-marker-after {
  background: var(--main-color);
}
</style>
