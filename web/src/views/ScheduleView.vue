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
                  <h3>S4 正向重算</h3>
                  <span>统一项目日历、FS/SS/FF/SF 零/正 Lag、ASAP/SNET/FNET、手工任务和 locked 冲突；负 Lag 和范围外输入明确阻断</span>
                </div>
                <a-button type="primary" :loading="recalculatingForward" @click="recalculateForward">
                  生成重算 Candidate
                </a-button>
              </div>
              <a-alert
                v-if="forwardBlockedResult"
                type="warning"
                show-icon
                message="当前来源超出最小 CPM Profile，未生成近似结果。"
                class="workbench-alert"
              />
              <div v-if="forwardBlockedResult" class="blocker-list">
                <div v-for="blocker in forwardBlockedResult.support.blockers" :key="`${blocker.code}-${blocker.object_refs.join('-')}`">
                  <a-tag color="orange">{{ blocker.code }}</a-tag>
                  <span>{{ blocker.message }}</span>
                  <code v-if="blocker.object_refs.length">
                    {{ blocker.object_refs.slice(0, 5).join(', ') }}
                    <template v-if="blocker.object_refs.length > 5">等 {{ blocker.object_refs.length }} 个对象</template>
                  </code>
                </div>
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
                      <a-button
                        v-if="record.rule_id === 'SUMMARY_TASK_DEPENDENCY'"
                        type="link"
                        size="small"
                        @click="openDependencyWorkbench(record.issue_id)"
                      >
                        确认关系
                      </a-button>
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

    <a-drawer v-model:open="workbenchOpen" title="汇总依赖确认工作台" width="760">
      <a-spin :spinning="loadingWorkbench">
        <template v-if="dependencyWorkbench">
          <a-alert
            type="info"
            show-icon
            message="这里只记录业务确认，不修改来源排期，也不运行日期计算。"
            class="workbench-alert"
          />
          <a-descriptions :column="1" bordered size="small">
            <a-descriptions-item label="原关系">
              {{ dependencyWorkbench.source_dependency.predecessor_task_id }}
              → {{ dependencyWorkbench.source_dependency.successor_task_id }}
            </a-descriptions-item>
            <a-descriptions-item label="类型 / Lag">
              {{ dependencyWorkbench.source_dependency.type }} /
              {{ dependencyWorkbench.source_dependency.lag_minutes }} 分钟
            </a-descriptions-item>
          </a-descriptions>

          <div class="task-context-grid">
            <section>
              <h4>上游任务树</h4>
              <div class="task-tree">
                <div
                  v-for="task in dependencyWorkbench.predecessor.tasks"
                  :key="task.task_id"
                  class="task-row"
                  :style="{ paddingLeft: `${Math.max(task.outline_level - 1, 0) * 14 + 10}px` }"
                >
                  <span>{{ task.wbs }} {{ task.name }}</span>
                  <a-tag
                    v-if="dependencyWorkbench.predecessor.candidate_task_ids.includes(task.task_id)"
                    color="blue"
                  >
                    出口候选
                  </a-tag>
                </div>
              </div>
            </section>
            <section>
              <h4>下游任务树</h4>
              <div class="task-tree">
                <div
                  v-for="task in dependencyWorkbench.successor.tasks"
                  :key="task.task_id"
                  class="task-row"
                  :style="{ paddingLeft: `${Math.max(task.outline_level - 1, 0) * 14 + 10}px` }"
                >
                  <span>{{ task.wbs }} {{ task.name }}</span>
                  <a-tag
                    v-if="dependencyWorkbench.successor.candidate_task_ids.includes(task.task_id)"
                    color="cyan"
                  >
                    入口候选
                  </a-tag>
                </div>
              </div>
            </section>
          </div>

          <h4>局部依赖关系</h4>
          <div class="network-list">
            <div v-for="edge in dependencyWorkbench.direct_network" :key="edge.dependency_id">
              <code>{{ taskLabel(edge.predecessor_task_id) }} → {{ taskLabel(edge.successor_task_id) }}</code>
              <span>{{ edge.type }} · Lag {{ edge.lag_minutes }} 分钟</span>
            </div>
            <a-empty
              v-if="!dependencyWorkbench.direct_network.length"
              image="simple"
              description="当前局部范围没有其他依赖"
            />
          </div>

          <a-form layout="vertical" class="decision-form">
            <a-form-item label="处理方式">
              <a-radio-group v-model:value="decisionForm.resolution" :disabled="decisionConfirmed">
                <a-radio value="replace_with_leaf_tasks">选择叶子任务替代</a-radio>
                <a-radio value="add_milestone">需要新增里程碑</a-radio>
                <a-radio value="defer">暂不处理</a-radio>
              </a-radio-group>
            </a-form-item>

            <template v-if="decisionForm.resolution === 'replace_with_leaf_tasks'">
              <div class="candidate-grid">
                <a-form-item label="上游阶段完成条件">
                  <a-button
                    type="link"
                    size="small"
                    :disabled="decisionConfirmed"
                    @click="selectWholePredecessorStage"
                  >
                    选择全部出口候选（整个阶段完成）
                  </a-button>
                  <a-select
                    v-model:value="decisionForm.predecessor_task_ids"
                    mode="multiple"
                    :disabled="decisionConfirmed"
                    :options="predecessorCandidateOptions"
                    placeholder="选择一个或多个出口叶子任务"
                  />
                </a-form-item>
                <a-form-item label="下游阶段开始条件">
                  <a-select
                    v-model:value="decisionForm.successor_task_ids"
                    mode="multiple"
                    :disabled="decisionConfirmed"
                    :options="successorCandidateOptions"
                    placeholder="选择一个或多个入口叶子任务"
                  />
                </a-form-item>
              </div>
              <div class="relation-grid">
                <a-form-item label="关系类型">
                  <a-select
                    v-model:value="decisionForm.dependency_type"
                    :disabled="decisionConfirmed"
                    :options="dependencyTypeOptions"
                  />
                </a-form-item>
                <a-form-item label="Lag（分钟）">
                  <a-input-number v-model:value="decisionForm.lag_minutes" :disabled="decisionConfirmed" />
                </a-form-item>
              </div>
            </template>

            <a-form-item label="业务理由">
              <a-textarea
                v-model:value="decisionForm.reason"
                :disabled="decisionConfirmed"
                :rows="3"
                placeholder="说明为何选择这些条件，或为何新增里程碑/暂不处理"
              />
            </a-form-item>
            <a-space>
              <a-button :disabled="decisionConfirmed" :loading="savingDecision" @click="saveDecision">
                保存草稿
              </a-button>
              <a-button
                type="primary"
                :disabled="decisionConfirmed"
                :loading="confirmingDecision"
                @click="confirmDecision"
              >
                明确确认
              </a-button>
              <a-tag v-if="decisionConfirmed" color="green">已确认，不可修改</a-tag>
              <a-button
                v-if="candidateEligible"
                type="primary"
                :loading="generatingCandidate"
                @click="generateCandidate"
              >
                {{ dependencyWorkbench.candidate ? '查看依赖 Candidate' : '生成依赖 Candidate' }}
              </a-button>
            </a-space>
            <a-alert
              v-if="decisionConfirmed && !candidateEligible"
              type="warning"
              show-icon
              message="只有已确认的“选择叶子任务替代”关系可以生成 S2 Candidate。"
              class="workbench-alert"
            />
          </a-form>
        </template>
      </a-spin>
    </a-drawer>

    <a-drawer v-model:open="candidateOpen" :title="candidateDrawerTitle" width="820">
      <a-spin :spinning="loadingCandidate">
        <template v-if="candidateDetail">
          <a-alert
            type="info"
            show-icon
            :message="candidateNotice"
            class="workbench-alert"
          />
          <a-descriptions :column="2" bordered size="small">
            <a-descriptions-item label="技术状态">
              <a-tag :color="candidateDetail.candidate_status === 'valid' ? 'green' : 'red'">
                {{ candidateDetail.candidate_status }}
              </a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="基础版本">
              <a-tag :color="candidateDetail.base_snapshot_status === 'current' ? 'green' : 'orange'">
                {{ candidateDetail.base_snapshot_status }}
              </a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="用户态度">{{ candidateDetail.user_attitude }}</a-descriptions-item>
            <a-descriptions-item label="Candidate Kind">{{ candidateDetail.candidate_kind }}</a-descriptions-item>
          </a-descriptions>

          <h4>请求变更</h4>
          <div class="patch-list">
            <div v-for="operation in candidateDetail.requested_patch.operations" :key="operation.operation_id">
              <a-tag :color="operation.operation === 'remove_dependency' ? 'orange' : 'blue'">
                {{ operation.operation }}
              </a-tag>
              <code v-if="operation.operation === 'recalculate_automatic_downstream'">
                {{ operation.scope }} · Source fields modified: {{ operation.source_fields_modified }}
              </code>
              <code v-else-if="operation.dependency_id">{{ operation.dependency_id }}</code>
              <code v-else-if="operation.dependency">
                {{ operation.dependency.predecessor_task_id }} → {{ operation.dependency.successor_task_id }}
                · {{ operation.dependency.type }} · Lag {{ operation.dependency.lag_minutes }}
              </code>
            </div>
          </div>

          <h4>{{ isForwardCandidate ? '日期差异' : '审查差异' }}</h4>
          <a-descriptions v-if="isForwardCandidate" :column="1" bordered size="small">
            <a-descriptions-item label="受影响任务">{{ candidateDetail.comparison.affected_task_count }}</a-descriptions-item>
            <a-descriptions-item label="来源完成">{{ candidateDetail.comparison.finish_before }}</a-descriptions-item>
            <a-descriptions-item label="重算完成">{{ candidateDetail.comparison.finish_after }}</a-descriptions-item>
            <a-descriptions-item label="任务日期变化">
              <pre>{{ pretty(candidateDetail.effective_patch.task_date_changes) }}</pre>
            </a-descriptions-item>
          </a-descriptions>
          <a-descriptions v-else :column="1" bordered size="small">
            <a-descriptions-item label="目标问题已解决">
              {{ candidateDetail.comparison.target_issue_resolved ? '是' : '否' }}
            </a-descriptions-item>
            <a-descriptions-item label="新增重复关系">
              {{ candidateDetail.comparison.duplicate_relation_added ? '是' : '否' }}
            </a-descriptions-item>
            <a-descriptions-item label="新增 Blocker">
              {{ candidateDetail.comparison.new_blockers.length ? candidateDetail.comparison.new_blockers.join('、') : '无' }}
            </a-descriptions-item>
            <a-descriptions-item label="规则数量变化">
              <pre>{{ pretty(candidateDetail.comparison.rule_count_changes) }}</pre>
            </a-descriptions-item>
          </a-descriptions>

          <a-form layout="vertical" class="decision-form">
            <a-form-item label="审阅意见">
              <a-textarea v-model:value="candidateComment" :rows="3" placeholder="说明接受或拒绝原因" />
            </a-form-item>
            <a-space>
              <a-button :loading="reviewingCandidate" @click="reviewCandidate('rejected')">拒绝</a-button>
              <a-button
                type="primary"
                :disabled="candidateDetail.candidate_status !== 'valid' || candidateDetail.base_snapshot_status !== 'current'"
                :loading="reviewingCandidate"
                @click="reviewCandidate('accepted')"
              >
                接受
              </a-button>
              <a-button
                v-if="candidateDetail.user_attitude === 'accepted'"
                :loading="loadingDelivery"
                @click="loadDelivery"
              >
                查看 Delivery
              </a-button>
              <a-button
                v-if="candidateDetail.user_attitude === 'accepted' && !isForwardCandidate"
                :loading="loadingAcceptanceEvidence"
                @click="loadAcceptanceEvidence"
              >
                查看回流验收证据
              </a-button>
            </a-space>
          </a-form>

          <template v-if="deliveryDetail">
            <h4>Delivery Package</h4>
            <a-alert
              :type="deliveryDetail.application_allowed ? 'success' : 'warning'"
              show-icon
              :message="deliveryMessage"
            />
            <pre>{{ pretty(deliveryDetail) }}</pre>
          </template>

          <template v-if="acceptanceEvidence">
            <h4>回流验收证据</h4>
            <a-alert
              :type="acceptanceEvidenceAlert.type"
              show-icon
              :message="acceptanceEvidenceAlert.message"
              class="workbench-alert"
            />
            <div class="acceptance-checks">
              <div v-for="check in acceptanceEvidence.checks" :key="check.code">
                <a-tag :color="check.passed ? 'green' : 'default'">
                  {{ check.passed ? '通过' : '未通过' }}
                </a-tag>
                <span>{{ acceptanceCheckLabels[check.code] || check.code }}</span>
              </div>
            </div>
          </template>
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
import { computed, onMounted, reactive, ref } from 'vue'
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
const workbenchOpen = ref(false)
const loadingWorkbench = ref(false)
const savingDecision = ref(false)
const confirmingDecision = ref(false)
const generatingCandidate = ref(false)
const recalculatingForward = ref(false)
const candidateOpen = ref(false)
const loadingCandidate = ref(false)
const reviewingCandidate = ref(false)
const loadingDelivery = ref(false)
const loadingAcceptanceEvidence = ref(false)
const candidateDetail = ref(null)
const deliveryDetail = ref(null)
const acceptanceEvidence = ref(null)
const forwardBlockedResult = ref(null)
const candidateComment = ref('')
const dependencyWorkbench = ref(null)
const activeWorkbenchIssueId = ref('')
const pageError = ref('')
const snapshotOffset = ref(0)
const issueOffset = ref(0)
const hasMoreSnapshots = ref(false)
const hasMoreIssues = ref(false)
const decisionForm = reactive({
  resolution: 'replace_with_leaf_tasks',
  predecessor_task_ids: [],
  successor_task_ids: [],
  dependency_type: 'FS',
  lag_minutes: 0,
  reason: ''
})

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
const dependencyTypeOptions = ['FS', 'SS', 'FF', 'SF'].map((value) => ({ label: value, value }))
const acceptanceCheckLabels = {
  CANDIDATE_VALID: 'Candidate 技术状态有效',
  CANDIDATE_ACCEPTED: '业务人员已明确接受',
  ENGINE_NOT_USED: '本阶段未伪造排程引擎结果',
  BASE_SOURCE_HASH_UNCHANGED: '基础 Source 内容 Hash 未改变',
  BASE_OBJECT_HASH_MATCHES_RECORD: '基础 Source 对象与数据库 Hash 一致',
  NEW_SNAPSHOT_AVAILABLE: '新的 Source Snapshot 已回流',
  LATEST_OBJECT_HASH_MATCHES_RECORD: '回流 Source 对象与数据库 Hash 一致',
  SUBMISSION_REQUEST_IS_NEW: '回流使用了新的提交请求标识',
  EXTERNAL_SNAPSHOT_IS_NEW: '外部 Snapshot 标识已更新',
  EXTERNAL_REVISION_IS_NEW: '外部 Revision 已更新',
  SOURCE_SNAPSHOT_IS_NEW: 'Source 内部 Snapshot 标识已更新',
  PATCH_APPLIED_EXACTLY: 'Delivery Patch 已准确应用',
  TARGET_ISSUE_RESOLVED: '目标汇总依赖问题已消失',
  NO_STATISTICS_OR_CAPABILITY_MISMATCH: '无统计或能力声明漂移',
  NO_NEW_BLOCKER: '没有新增阻断问题',
  BASE_CANDIDATE_OUTDATED: '旧 Candidate 已自动过期'
}
const acceptanceEvidenceAlert = computed(() => {
  if (acceptanceEvidence.value?.status === 'passed') {
    return { type: 'success', message: '回流验收已通过，可保存为该真实案例的迁移证据。' }
  }
  if (acceptanceEvidence.value?.status === 'failed') {
    return { type: 'error', message: '新版本已回流，但至少一项验收检查失败。' }
  }
  return { type: 'info', message: '尚未检测到新的 Source Snapshot，当前等待业务适配器回流。' }
})
const decisionConfirmed = computed(() => dependencyWorkbench.value?.decision?.status === 'confirmed')
const candidateEligible = computed(
  () => decisionConfirmed.value && dependencyWorkbench.value?.decision?.resolution === 'replace_with_leaf_tasks'
)
const isForwardCandidate = computed(
  () => candidateDetail.value?.candidate_kind === 'automatic_forward_recalculation'
)
const candidateDrawerTitle = computed(() =>
  isForwardCandidate.value ? '正向重算 Candidate 审阅' : '依赖 Candidate 审阅'
)
const candidateNotice = computed(() =>
  isForwardCandidate.value
    ? '日期仅保存在 engine_result，来源任务及 source_calculation 均未修改。'
    : '该 Candidate 只规范化依赖，不修改 Source，也不运行 CPM。'
)
const deliveryMessage = computed(() => {
  if (!deliveryDetail.value?.application_allowed) return '当前不可交付'
  return isForwardCandidate.value
    ? '可交付 engine_result 供业务审阅；不会改写 Source'
    : '可交给业务适配器应用到新的 Source 副本'
})
const candidateOptions = (side) => {
  const taskNames = new Map((side?.tasks || []).map((task) => [task.task_id, `${task.wbs} ${task.name}`]))
  return (side?.candidate_task_ids || []).map((taskId) => ({
    value: taskId,
    label: taskNames.get(taskId) || taskId
  }))
}
const predecessorCandidateOptions = computed(() => candidateOptions(dependencyWorkbench.value?.predecessor))
const successorCandidateOptions = computed(() => candidateOptions(dependencyWorkbench.value?.successor))
const selectWholePredecessorStage = () => {
  decisionForm.predecessor_task_ids = predecessorCandidateOptions.value.map((item) => item.value)
}
const taskLabel = (taskId) => {
  const tasks = [
    ...(dependencyWorkbench.value?.predecessor?.tasks || []),
    ...(dependencyWorkbench.value?.successor?.tasks || [])
  ]
  const task = tasks.find((item) => item.task_id === taskId)
  return task ? `${task.wbs} ${task.name}` : taskId
}

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

const applyDecision = (decision) => {
  decisionForm.resolution = decision?.resolution || 'replace_with_leaf_tasks'
  decisionForm.predecessor_task_ids = [...(decision?.predecessor_task_ids || [])]
  decisionForm.successor_task_ids = [...(decision?.successor_task_ids || [])]
  decisionForm.dependency_type = decision?.dependency_type || dependencyWorkbench.value?.source_dependency.type || 'FS'
  decisionForm.lag_minutes = decision?.lag_minutes ?? dependencyWorkbench.value?.source_dependency.lag_minutes ?? 0
  decisionForm.reason = decision?.reason || ''
}

const openDependencyWorkbench = async (issueId) => {
  workbenchOpen.value = true
  loadingWorkbench.value = true
  activeWorkbenchIssueId.value = issueId
  dependencyWorkbench.value = null
  try {
    dependencyWorkbench.value = await scheduleApi.getDependencyWorkbench(issueId)
    applyDecision(dependencyWorkbench.value.decision)
  } catch (error) {
    message.error(error.message || '汇总依赖工作台加载失败')
  } finally {
    loadingWorkbench.value = false
  }
}

const decisionPayload = () => ({
  resolution: decisionForm.resolution,
  predecessor_task_ids:
    decisionForm.resolution === 'replace_with_leaf_tasks' ? decisionForm.predecessor_task_ids : [],
  successor_task_ids:
    decisionForm.resolution === 'replace_with_leaf_tasks' ? decisionForm.successor_task_ids : [],
  dependency_type:
    decisionForm.resolution === 'replace_with_leaf_tasks' ? decisionForm.dependency_type : null,
  lag_minutes: decisionForm.resolution === 'replace_with_leaf_tasks' ? decisionForm.lag_minutes : null,
  reason: decisionForm.reason
})

const saveDecision = async () => {
  savingDecision.value = true
  try {
    const decision = await scheduleApi.saveDependencyDecision(activeWorkbenchIssueId.value, decisionPayload())
    dependencyWorkbench.value.decision = decision
    message.success('决策草稿已保存，来源排期未修改')
  } catch (error) {
    message.error(error.message || '决策草稿保存失败')
  } finally {
    savingDecision.value = false
  }
}

const confirmDecision = async () => {
  confirmingDecision.value = true
  try {
    const saved = await scheduleApi.saveDependencyDecision(activeWorkbenchIssueId.value, decisionPayload())
    dependencyWorkbench.value.decision = saved
    const confirmed = await scheduleApi.confirmDependencyDecision(activeWorkbenchIssueId.value)
    dependencyWorkbench.value.decision = confirmed
    message.success('依赖处理意见已明确确认，可供下一阶段生成 Candidate')
  } catch (error) {
    message.error(error.message || '依赖处理意见确认失败')
  } finally {
    confirmingDecision.value = false
  }
}

const requestId = (prefix) => `${prefix}-${crypto.randomUUID()}`

const generateCandidate = async () => {
  generatingCandidate.value = true
  deliveryDetail.value = null
  acceptanceEvidence.value = null
  try {
    candidateDetail.value = dependencyWorkbench.value.candidate
      ? await scheduleApi.getCandidate(dependencyWorkbench.value.candidate.candidate_snapshot_id)
      : await scheduleApi.createDependencyCandidate(
          dependencyWorkbench.value.issue.schedule_snapshot_id,
          {
            request_id: requestId('dependency-optimization'),
            dependency_decision_id: dependencyWorkbench.value.decision.decision_id,
            base_snapshot_content_sha256: snapshotDetail.value.snapshot_content_sha256
          }
        )
    dependencyWorkbench.value.candidate = {
      candidate_snapshot_id: candidateDetail.value.candidate_snapshot_id,
      candidate_status: candidateDetail.value.candidate_status,
      candidate_kind: candidateDetail.value.candidate_kind
    }
    candidateOpen.value = true
    message.success('依赖 Candidate 已打开，Source 未修改')
  } catch (error) {
    message.error(error.message || '依赖 Candidate 生成失败')
  } finally {
    generatingCandidate.value = false
  }
}

const recalculateForward = async () => {
  if (!selectedSnapshotId.value || !snapshotDetail.value) return
  recalculatingForward.value = true
  forwardBlockedResult.value = null
  deliveryDetail.value = null
  acceptanceEvidence.value = null
  try {
    const result = await scheduleApi.createForwardRecalculation(selectedSnapshotId.value, {
      request_id: requestId('forward-recalculation'),
      base_snapshot_content_sha256: snapshotDetail.value.snapshot_content_sha256
    })
    if (result.candidate_status === 'blocked') {
      forwardBlockedResult.value = result.engine_result
      return
    }
    candidateDetail.value = result
    candidateOpen.value = true
    message.success('正向重算 Candidate 已生成，Source 未修改')
  } catch (error) {
    message.error(error.message || '正向重算失败')
  } finally {
    recalculatingForward.value = false
  }
}

const reviewCandidate = async (attitude) => {
  reviewingCandidate.value = true
  try {
    await scheduleApi.recordCandidateDecision(candidateDetail.value.candidate_snapshot_id, {
      request_id: requestId('candidate-review'),
      attitude,
      comment: candidateComment.value
    })
    candidateDetail.value = await scheduleApi.getCandidate(candidateDetail.value.candidate_snapshot_id)
    deliveryDetail.value = null
    acceptanceEvidence.value = null
    message.success(attitude === 'accepted' ? 'Candidate 已接受' : 'Candidate 已拒绝')
  } catch (error) {
    message.error(error.message || 'Candidate 审阅失败')
  } finally {
    reviewingCandidate.value = false
  }
}

const loadDelivery = async () => {
  loadingDelivery.value = true
  try {
    deliveryDetail.value = await scheduleApi.getCandidateDelivery(candidateDetail.value.candidate_snapshot_id)
  } catch (error) {
    message.error(error.message || 'Delivery 读取失败')
  } finally {
    loadingDelivery.value = false
  }
}

const loadAcceptanceEvidence = async () => {
  loadingAcceptanceEvidence.value = true
  try {
    acceptanceEvidence.value = await scheduleApi.getCandidateAcceptanceEvidence(
      candidateDetail.value.candidate_snapshot_id
    )
  } catch (error) {
    message.error(error.message || '回流验收证据读取失败')
  } finally {
    loadingAcceptanceEvidence.value = false
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

.workbench-alert,
.decision-form {
  margin-top: 16px;
}

.candidate-grid,
.relation-grid,
.task-context-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.blocker-list {
  display: flex;
  margin-top: 12px;
  flex-direction: column;
  gap: 8px;
}

.blocker-list > div {
  display: flex;
  align-items: center;
  gap: 8px;
}

.task-tree,
.network-list {
  max-height: 260px;
  overflow: auto;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--main-0);
}

.task-row,
.network-list > div {
  display: flex;
  min-height: 38px;
  padding: 8px 10px;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  border-bottom: 1px solid var(--gray-100);
}

.task-row:last-child,
.network-list > div:last-child {
  border-bottom: 0;
}

.network-list > div {
  align-items: flex-start;
  flex-direction: column;
}

.network-list span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.patch-list {
  display: grid;
  gap: 8px;
  margin-bottom: 16px;
}

.patch-list > div {
  display: flex;
  align-items: center;
  gap: 8px;
}

.acceptance-checks {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.acceptance-checks > div {
  display: flex;
  align-items: center;
  gap: 8px;
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
