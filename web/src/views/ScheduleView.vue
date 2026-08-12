<template>
  <main class="schedule-page">
    <header class="page-header">
      <div>
        <p class="eyebrow">SCHEDULE AUDIT</p>
        <h1>排期审查</h1>
        <p class="subtitle">查看不可变来源快照、Yuxi 确定性审查结论与可追溯证据。</p>
      </div>
      <a-button :loading="loadingSnapshots" @click="loadSnapshots(false)">刷新</a-button>
    </header>

    <a-alert
      v-if="pageError"
      type="error"
      show-icon
      :message="pageError"
      class="page-alert"
    />

    <a-spin :spinning="loadingSnapshots">
      <a-empty v-if="!loadingSnapshots && !snapshots.length" description="暂无排期快照" />
      <section v-else class="workspace-grid">
        <aside class="snapshot-panel">
          <h2>来源快照</h2>
          <button
            v-for="item in snapshots"
            :key="item.schedule_snapshot_id"
            type="button"
            class="snapshot-item"
            :class="{ active: item.schedule_snapshot_id === selectedSnapshotId }"
            @click="selectSnapshot(item.schedule_snapshot_id)"
          >
            <strong>{{ item.external_project_id }}</strong>
            <span>{{ item.external_revision }} · {{ formatDate(item.ready_at) }}</span>
            <code>{{ shortId(item.schedule_snapshot_id) }}</code>
          </button>
          <a-button
            v-if="hasMoreSnapshots"
            block
            class="load-more-button"
            :loading="loadingSnapshots"
            @click="loadSnapshots(true)"
          >
            继续加载
          </a-button>
        </aside>

        <a-spin :spinning="loadingDetail" class="detail-spin">
          <section v-if="audit" class="detail-panel">
            <div class="detail-heading">
              <div>
                <h2>{{ snapshotDetail?.snapshot?.project?.name || '排期快照' }}</h2>
                <p>
                  {{ audit.statistics.tasks }} 个任务 · {{ audit.statistics.dependencies }} 条依赖 ·
                  规则集 {{ audit.rule_set_version }}
                </p>
              </div>
              <a-tag color="green">只读来源快照</a-tag>
            </div>

            <div class="metric-grid">
              <article class="metric-card">
                <span>零 Lag 已检查</span>
                <strong>{{ audit.dependency_date_checks.checked }}</strong>
              </article>
              <article class="metric-card warning">
                <span>非零 Lag 未检查</span>
                <strong>{{ audit.dependency_date_checks.skipped }}</strong>
              </article>
              <article class="metric-card danger">
                <span>阻断级问题</span>
                <strong>{{ audit.issue_summary.blocker }}</strong>
              </article>
              <article class="metric-card">
                <span>开放起点 / 终点</span>
                <strong>{{ audit.statistics.open_start_tasks }} / {{ audit.statistics.open_finish_tasks }}</strong>
              </article>
            </div>

            <section class="content-card">
              <div class="section-title">
                <h3>能力边界</h3>
                <span>能力阻断与 Issue 严重等级分别判断</span>
              </div>
              <div class="capability-grid">
                <article v-for="(capability, name) in audit.capabilities" :key="name">
                  <div>
                    <CheckCircle2 v-if="capability.allowed" :size="18" class="allowed" />
                    <Ban v-else :size="18" class="blocked" />
                    <strong>{{ capabilityLabel(name) }}</strong>
                  </div>
                  <p>{{ capability.allowed ? '允许' : capability.reasons.join('、') }}</p>
                </article>
              </div>
            </section>

            <section class="content-card">
              <div class="section-title issue-title">
                <div>
                  <h3>审查问题</h3>
                  <span>结论均来自 YUXI_AUDIT，不复制来源 Validation</span>
                </div>
                <div class="filters">
                  <a-select
                    v-model:value="severityFilter"
                    allow-clear
                    placeholder="严重等级"
                    :options="severityOptions"
                    @change="loadIssues(false)"
                  />
                  <a-select
                    v-model:value="categoryFilter"
                    allow-clear
                    placeholder="分类"
                    :options="categoryOptions"
                    @change="loadIssues(false)"
                  />
                </div>
              </div>

              <a-table
                :columns="issueColumns"
                :data-source="issues"
                :loading="loadingIssues"
                :pagination="false"
                row-key="issue_id"
                size="middle"
              >
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === 'severity'">
                    <a-tag :color="severityColor(record.severity)">{{ record.severity }}</a-tag>
                  </template>
                  <template v-else-if="column.key === 'objects'">
                    <code>{{ record.object_refs.join(', ') }}</code>
                  </template>
                  <template v-else-if="column.key === 'actions'">
                    <a-space>
                      <a-button type="link" size="small" @click="openIssue(record.issue_id)">证据</a-button>
                      <a-button type="link" size="small" @click="explainIssue(record)">Agent 解释</a-button>
                    </a-space>
                  </template>
                </template>
              </a-table>
              <a-empty v-if="!loadingIssues && !issues.length" description="当前筛选条件下没有问题" />
              <a-button
                v-if="hasMoreIssues"
                block
                class="load-more-button"
                :loading="loadingIssues"
                @click="loadIssues(true)"
              >
                继续加载
              </a-button>
            </section>
          </section>
        </a-spin>
      </section>
    </a-spin>

    <a-drawer v-model:open="issueDrawerOpen" title="问题证据" width="620">
      <a-spin :spinning="loadingIssueDetail">
        <template v-if="issueDetail?.issue">
          <a-descriptions :column="1" bordered size="small">
            <a-descriptions-item label="Issue ID">{{ issueDetail.issue.issue_id }}</a-descriptions-item>
            <a-descriptions-item label="规则">{{ issueDetail.issue.rule_id }}</a-descriptions-item>
            <a-descriptions-item label="结论">{{ issueDetail.issue.message }}</a-descriptions-item>
            <a-descriptions-item label="建议">{{ issueDetail.issue.recommendation }}</a-descriptions-item>
          </a-descriptions>
          <h4>证据</h4>
          <pre>{{ pretty(issueDetail.issue.evidence) }}</pre>
          <h4>涉及任务</h4>
          <a-list :data-source="issueDetail.tasks" size="small" bordered>
            <template #renderItem="{ item }">
              <a-list-item><code>{{ item.task_id }}</code> · {{ item.name }}</a-list-item>
            </template>
          </a-list>
          <h4>直接上下游</h4>
          <a-list :data-source="issueDetail.direct_network" size="small" bordered>
            <template #renderItem="{ item }">
              <a-list-item>
                <code>{{ item.predecessor_task_id }} → {{ item.successor_task_id }}</code>
                （{{ item.type }}，Lag {{ item.lag_minutes }}）
              </a-list-item>
            </template>
          </a-list>
        </template>
      </a-spin>
    </a-drawer>

    <a-modal v-model:open="agentModalOpen" title="选择具备排期工具的智能体" @ok="openAgent">
      <a-radio-group v-model:value="selectedAgentId" class="agent-options">
        <a-radio v-for="agent in capableAgents" :key="agent.agent_id" :value="agent.agent_id">
          {{ agent.name }}
        </a-radio>
      </a-radio-group>
    </a-modal>
  </main>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { Ban, CheckCircle2 } from 'lucide-vue-next'
import { scheduleApi } from '@/apis/schedule_api'

const router = useRouter()
const snapshots = ref([])
const selectedSnapshotId = ref('')
const snapshotDetail = ref(null)
const audit = ref(null)
const issues = ref([])
const issueDetail = ref(null)
const capableAgents = ref([])
const selectedAgentId = ref('')
const pendingIssueId = ref('')
const severityFilter = ref()
const categoryFilter = ref()
const loadingSnapshots = ref(false)
const loadingDetail = ref(false)
const loadingIssues = ref(false)
const loadingIssueDetail = ref(false)
const issueDrawerOpen = ref(false)
const agentModalOpen = ref(false)
const pageError = ref('')
const snapshotOffset = ref(0)
const issueOffset = ref(0)
const hasMoreSnapshots = ref(false)
const hasMoreIssues = ref(false)

const SNAPSHOT_PAGE_SIZE = 50
const ISSUE_PAGE_SIZE = 100

const severityOptions = [
  { label: 'Blocker', value: 'blocker' },
  { label: 'Warning', value: 'warning' },
  { label: 'Info', value: 'info' }
]
const issueColumns = [
  { title: '等级', key: 'severity', width: 100 },
  { title: '规则', dataIndex: 'rule_id', key: 'rule', width: 220 },
  { title: '说明', dataIndex: 'message', key: 'message' },
  { title: '对象', key: 'objects', width: 220 },
  { title: '操作', key: 'actions', width: 150 }
]
const categoryOptions = [
  { label: '契约', value: 'contract' },
  { label: '网络', value: 'network' },
  { label: '依赖', value: 'dependency' },
  { label: '项目管理', value: 'management' },
  { label: '资源', value: 'resource' },
  { label: '引擎边界', value: 'engine_contract' }
]

const loadSnapshots = async (append = false) => {
  loadingSnapshots.value = true
  pageError.value = ''
  try {
    const offset = append ? snapshotOffset.value : 0
    const response = await scheduleApi.listSnapshots({ limit: SNAPSHOT_PAGE_SIZE, offset })
    const items = response.items || []
    snapshots.value = append ? [...snapshots.value, ...items] : items
    snapshotOffset.value = offset + items.length
    hasMoreSnapshots.value = items.length === SNAPSHOT_PAGE_SIZE
    if (!selectedSnapshotId.value && snapshots.value.length) {
      await selectSnapshot(snapshots.value[0].schedule_snapshot_id)
    }
  } catch (error) {
    pageError.value = error.message || '排期快照加载失败或当前用户无权访问'
  } finally {
    loadingSnapshots.value = false
  }
}

const selectSnapshot = async (snapshotId) => {
  selectedSnapshotId.value = snapshotId
  loadingDetail.value = true
  pageError.value = ''
  try {
    ;[snapshotDetail.value, audit.value] = await Promise.all([
      scheduleApi.getSnapshot(snapshotId),
      scheduleApi.getAudit(snapshotId)
    ])
    await loadIssues(false)
  } catch (error) {
    pageError.value = error.message || '快照不存在或当前用户无权访问'
    audit.value = null
  } finally {
    loadingDetail.value = false
  }
}

const loadIssues = async (append = false) => {
  if (!selectedSnapshotId.value) return
  loadingIssues.value = true
  try {
    const offset = append ? issueOffset.value : 0
    const response = await scheduleApi.listIssues(selectedSnapshotId.value, {
      severity: severityFilter.value,
      category: categoryFilter.value,
      limit: ISSUE_PAGE_SIZE,
      offset
    })
    const items = response.items || []
    issues.value = append ? [...issues.value, ...items] : items
    issueOffset.value = offset + items.length
    hasMoreIssues.value = items.length === ISSUE_PAGE_SIZE
  } catch (error) {
    message.error(error.message || '问题列表加载失败')
  } finally {
    loadingIssues.value = false
  }
}

const openIssue = async (issueId) => {
  issueDrawerOpen.value = true
  loadingIssueDetail.value = true
  issueDetail.value = null
  try {
    issueDetail.value = await scheduleApi.getIssue(issueId)
  } catch (error) {
    message.error(error.message || '问题证据加载失败')
  } finally {
    loadingIssueDetail.value = false
  }
}

const explainIssue = async (issue) => {
  pendingIssueId.value = issue.issue_id
  try {
    const response = await scheduleApi.listCapableAgents()
    capableAgents.value = response.items || []
    if (!capableAgents.value.length) {
      message.warning('没有可用智能体，请先为智能体启用两个 Schedule 工具')
      return
    }
    if (capableAgents.value.length === 1) {
      selectedAgentId.value = capableAgents.value[0].agent_id
      await openAgent()
      return
    }
    selectedAgentId.value = capableAgents.value[0].agent_id
    agentModalOpen.value = true
  } catch (error) {
    message.error(error.message || '智能体列表加载失败')
  }
}

const openAgent = async () => {
  if (!selectedAgentId.value || !pendingIssueId.value) return
  agentModalOpen.value = false
  await router.push({
    path: '/agent',
    query: { agent_id: selectedAgentId.value, schedule_issue_id: pendingIssueId.value }
  })
}

const capabilityLabel = (name) =>
  ({
    gantt_display: '甘特展示',
    source_schedule_review: '来源排期审查',
    cpm_recalculation: 'CPM 重算',
    resource_leveling: '资源均衡',
    resource_cost_optimization: '资源成本优化'
  })[name] || name
const severityColor = (severity) => ({ blocker: 'red', warning: 'orange', info: 'blue' })[severity]
const shortId = (value) => `${value.slice(0, 8)}…`
const formatDate = (value) => (value ? new Date(value).toLocaleString() : '处理中')
const pretty = (value) => JSON.stringify(value, null, 2)

onMounted(loadSnapshots)
</script>

<style lang="less" scoped>
.schedule-page {
  min-height: 100vh;
  padding: var(--page-padding);
  color: var(--color-text);
  background: var(--main-20);
}

.page-header,
.detail-heading,
.section-title,
.issue-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.page-header h1,
.detail-heading h2,
.snapshot-panel h2,
.section-title h3 {
  margin: 0;
}

.eyebrow {
  margin: 0 0 4px;
  color: var(--main-700);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.14em;
}

.subtitle,
.detail-heading p,
.section-title span {
  margin: 6px 0 0;
  color: var(--color-text-secondary);
}

.page-alert {
  margin: 18px 0;
}

.workspace-grid {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: 18px;
  margin-top: 20px;
}

.snapshot-panel,
.content-card {
  padding: 18px;
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  background: var(--color-bg-container);
}

.snapshot-panel {
  align-self: start;
}

.snapshot-item {
  display: flex;
  width: 100%;
  margin-top: 12px;
  padding: 12px;
  flex-direction: column;
  gap: 5px;
  text-align: left;
  color: var(--color-text);
  border: 1px solid var(--gray-150);
  border-radius: 9px;
  background: var(--main-0);
  cursor: pointer;
}

.snapshot-item span,
.snapshot-item code {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.snapshot-item.active {
  border-color: var(--main-500);
  background: var(--main-30);
}

.load-more-button {
  margin-top: 12px;
}

.detail-spin,
.detail-panel {
  width: 100%;
}

.metric-grid,
.capability-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 16px 0;
}

.metric-card,
.capability-grid article {
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--main-0);
}

.metric-card span,
.capability-grid p {
  color: var(--color-text-secondary);
}

.metric-card strong {
  display: block;
  margin-top: 8px;
  font-size: 26px;
}

.metric-card.warning strong,
.blocked {
  color: var(--color-warning-700);
}

.metric-card.danger strong {
  color: var(--color-error-700);
}

.content-card {
  margin-top: 16px;
}

.capability-grid article > div {
  display: flex;
  align-items: center;
  gap: 8px;
}

.capability-grid p {
  margin: 8px 0 0;
  font-size: 12px;
  word-break: break-word;
}

.allowed {
  color: var(--color-success-700);
}

.filters {
  display: flex;
  gap: 8px;
}

.filters :deep(.ant-select) {
  min-width: 130px;
}

pre {
  overflow: auto;
  padding: 12px;
  border-radius: 8px;
  background: var(--gray-50);
  white-space: pre-wrap;
}

h4 {
  margin: 22px 0 8px;
}

.agent-options {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

@media (max-width: 1000px) {
  .workspace-grid {
    grid-template-columns: 1fr;
  }

  .metric-grid,
  .capability-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
