<template>
  <div class="agent-view">
    <div class="agent-view-body">
      <!-- 中间内容区域 -->
      <div class="content">
        <AgentChatComponent
          ref="chatComponentRef"
          :single-mode="false"
          :thread-metadata="scheduleReviewThreadMetadata"
          @thread-change="handleThreadChange"
          @draft-consumed="clearScheduleEntryQuery"
        >
          <template #header-left>
            <div
              v-if="scheduleReviewContext"
              class="schedule-review-context"
              :title="`${scheduleReviewContext.externalProjectId} · ${scheduleReviewContext.externalRevision} · ${scheduleReviewContext.scheduleSnapshotId}`"
            >
              <MessageSquareText :size="15" />
              <strong>{{ isScheduleGoalContext ? '工期优化规划' : '排期审查' }}</strong>
              <span>{{ scheduleReviewContext.externalProjectId }}</span>
              <span>{{ scheduleReviewContext.externalRevision }}</span>
              <code>{{ shortId(scheduleReviewContext.scheduleSnapshotId) }}</code>
            </div>
          </template>
          <template #header-right>
            <a-dropdown
              v-if="scheduleReviewContext && !isScheduleGoalContext"
              :trigger="['click']"
              placement="bottomRight"
            >
              <a-button type="text" size="small" class="schedule-suggestion-trigger">
                建议问题
                <ChevronDown :size="14" />
              </a-button>
              <template #overlay>
                <a-menu @click="setScheduleReviewQuestion">
                  <a-menu-item v-for="item in scheduleReviewSuggestions" :key="item.key">
                    {{ item.label }}
                  </a-menu-item>
                </a-menu>
              </template>
            </a-dropdown>
          </template>
          <template #input-actions-left="{ hasActiveThread }">
            <a-dropdown
              v-if="selectedAgentId"
              v-model:open="agentDropdownOpen"
              :trigger="['click']"
              placement="topLeft"
              overlay-class-name="config-dropdown-overlay"
            >
              <button
                type="button"
                class="input-action-btn config-dropdown-trigger"
                :class="{ disabled: isLoadingConfig }"
                @click.stop
                @mousedown.stop
              >
                <span class="hide-text config-dropdown-text">{{ currentAgentLabel }}</span>
                <ChevronDown size="15" class="config-dropdown-chevron" />
              </button>

              <template #overlay>
                <div class="config-dropdown-panel" @click.stop>
                  <button
                    v-for="agent in agentQuickSwitchOptions"
                    :key="agent.value"
                    type="button"
                    class="config-dropdown-item"
                    :class="{
                      selected: agent.value === selectedAgentId,
                      disabled: hasActiveThread && agent.value !== selectedAgentId
                    }"
                    @click="handleAgentSwitch(agent.value, hasActiveThread)"
                  >
                    <FallbackAvatar
                      class="config-dropdown-item-icon-image"
                      :src="agent.icon"
                      :default-src="agent.defaultIcon"
                      :name="agent.label"
                      :seed="agent.value || agent.label"
                      kind="agent"
                      :size="24"
                      shape="rounded"
                      :alt="`${agent.label}图标`"
                    />
                    <span class="config-dropdown-item-label">{{ agent.label }}</span>
                    <span v-if="agent.isBuiltin" class="config-dropdown-item-badge">内置</span>
                    <Check
                      v-if="agent.value === selectedAgentId"
                      :size="14"
                      class="config-dropdown-item-check"
                    />
                  </button>

                  <div v-if="hasActiveThread" class="config-dropdown-hint">
                    当前对话已绑定智能体，新对话可切换。
                  </div>

                  <div class="config-dropdown-divider"></div>

                  <button
                    type="button"
                    class="config-dropdown-item action-item"
                    @click="openAgentManagement"
                  >
                    <Settings2 :size="15" class="config-dropdown-item-icon" />
                    <span class="config-dropdown-item-label">管理智能体</span>
                  </button>
                </div>
              </template>
            </a-dropdown>
          </template>
        </AgentChatComponent>
      </div>
    </div>
    <AgentEditModal
      ref="agentEditModalRef"
      :backend-options="agentBackendOptions"
      @saved="handleAgentSaved"
    />
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { Settings2, ChevronDown, Check, MessageSquareText } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { agentApi } from '@/apis/agent_api'
import AgentChatComponent from '@/components/AgentChatComponent.vue'
import AgentEditModal from '@/components/model-management/AgentEditModal.vue'
import { isBuiltinAgent, useAgentStore } from '@/stores/agent'
import { useChatThreadsStore } from '@/stores/chatThreads'
import { handleChatError } from '@/utils/errorHandler'
import { generatePixelAvatar } from '@/utils/pixelAvatar'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'
import {
  buildScheduleGoalDraft,
  buildScheduleReviewDraft,
  buildScheduleReviewThreadMetadata,
  scheduleReviewContextFromQuery,
  scheduleReviewContextFromMetadata,
  scheduleReviewSuggestions
} from '@/utils/scheduleAgentEntry'

import { storeToRefs } from 'pinia'

// 组件引用
const chatComponentRef = ref(null)
const agentEditModalRef = ref(null)

// Stores
const agentStore = useAgentStore()
const chatThreadsStore = useChatThreadsStore()
const route = useRoute()
const router = useRouter()

// 从 agentStore 中获取响应式状态
const { agents, selectedAgentId, isLoadingConfig } = storeToRefs(agentStore)
const { currentThread } = storeToRefs(chatThreadsStore)

const syncingRouteThread = ref(false)
const consumingScheduleEntry = ref(false)
const preparedScheduleSnapshotId = ref('')

const getRouteThreadId = () => {
  const value = route.params.thread_id
  return typeof value === 'string' ? value : ''
}

const getRouteAgentId = () => {
  const value = route.query.agent_id
  return typeof value === 'string' ? value : ''
}

const getRouteScheduleIssueId = () => {
  const value = route.query.schedule_issue_id
  return typeof value === 'string' ? value : ''
}

const scheduleReviewContext = computed(
  () =>
    scheduleReviewContextFromQuery(route.query) ||
    scheduleReviewContextFromMetadata(currentThread.value?.metadata)
)
const scheduleReviewThreadMetadata = computed(() =>
  scheduleReviewContext.value
    ? buildScheduleReviewThreadMetadata(scheduleReviewContext.value)
    : {}
)
const isScheduleGoalContext = computed(
  () => scheduleReviewContext.value?.mode === 'goal_optimization'
)
const scheduleReviewThreadId = ref(
  scheduleReviewContext.value ? getRouteThreadId() : ''
)

const syncSelectedThreadFromRoute = async () => {
  const chatComponent = chatComponentRef.value
  if (!chatComponent?.selectThreadFromRoute) return

  const threadId = getRouteThreadId()
  syncingRouteThread.value = true
  try {
    if (!threadId && !agentStore.isInitialized) {
      await agentStore.initialize()
    }

    const ok = await chatComponent.selectThreadFromRoute(threadId)
    if (threadId && !ok) {
      await router.replace({ name: 'AgentComp' })
    }
  } catch (error) {
    handleChatError(error, 'load')
  } finally {
    syncingRouteThread.value = false
  }
}

const consumeRouteAgentSelection = async () => {
  const targetAgentId = getRouteAgentId()
  const scheduleIssueId = getRouteScheduleIssueId()
  const reviewContext = scheduleReviewContext.value
  const chatComponent = chatComponentRef.value
  if (
    (!targetAgentId && !scheduleIssueId && !reviewContext) ||
    getRouteThreadId() ||
    !chatComponent ||
    consumingScheduleEntry.value
  )
    return

  consumingScheduleEntry.value = true
  try {
    if (!agentStore.isInitialized) {
      await agentStore.initialize()
    }

    await nextTick()
    await chatComponent.selectThreadFromRoute?.('')
    if (targetAgentId) await agentStore.selectAgent(targetAgentId)
    if (scheduleIssueId) {
      chatComponent.setDraftMessage?.(
        `请解释排期审查问题 issue_id=${scheduleIssueId}，并说明证据、影响和需要工程人员确认的事项。只能依据 Schedule 工具返回的 YUXI_AUDIT 事实；不得把来源 Validation，或 normalization report 中 ignored/unsupported 的字段描述成已参与审查或计算。`
      )
    } else if (
      reviewContext &&
      preparedScheduleSnapshotId.value !== reviewContext.scheduleSnapshotId
    ) {
      chatComponent.setDraftMessage?.(
        isScheduleGoalContext.value
          ? buildScheduleGoalDraft(reviewContext)
          : buildScheduleReviewDraft(reviewContext)
      )
      preparedScheduleSnapshotId.value = reviewContext.scheduleSnapshotId
    }
  } catch (error) {
    handleChatError(error, 'load')
  } finally {
    consumingScheduleEntry.value = false
  }
  if (!scheduleIssueId && !reviewContext) {
    const nextQuery = { ...route.query }
    delete nextQuery.agent_id
    await router.replace({ name: 'AgentComp', query: nextQuery })
  }
}

const clearScheduleEntryQuery = async () => {
  const nextQuery = { ...route.query }
  delete nextQuery.agent_id
  delete nextQuery.schedule_issue_id
  const threadId = getRouteThreadId()
  await router.replace(
    threadId
      ? { name: 'AgentCompWithThreadId', params: { thread_id: threadId }, query: nextQuery }
      : { name: 'AgentComp', query: nextQuery }
  )
}

watch(
  () => route.params.thread_id,
  () => {
    syncSelectedThreadFromRoute()
  },
  { immediate: true }
)

watch(
  () => [
    route.query.agent_id,
    route.query.schedule_issue_id,
    route.query.schedule_snapshot_id,
    route.query.schedule_snapshot_sha256,
    route.query.schedule_external_project_id,
    route.query.schedule_external_revision,
    route.query.schedule_goal_optimization
  ],
  (current, previous) => {
    if (current[2] !== previous?.[2]) {
      preparedScheduleSnapshotId.value = ''
      scheduleReviewThreadId.value = getRouteThreadId()
    }
    consumeRouteAgentSelection()
  },
  { immediate: true }
)

watch(chatComponentRef, async (instance) => {
  if (!instance) return
  // Route thread selection and Schedule draft projection both reset the
  // empty-chat state, so keep their order deterministic on first mount.
  await syncSelectedThreadFromRoute()
  await consumeRouteAgentSelection()
})

const handleThreadChange = (threadId) => {
  if (syncingRouteThread.value) return
  const currentRouteThreadId = getRouteThreadId()
  const nextThreadId = threadId || ''
  if (currentRouteThreadId === nextThreadId) return

  if (nextThreadId) {
    let query = {}
    if (scheduleReviewContext.value) {
      if (!scheduleReviewThreadId.value) scheduleReviewThreadId.value = nextThreadId
      if (scheduleReviewThreadId.value === nextThreadId) {
        query = {
          schedule_snapshot_id: scheduleReviewContext.value.scheduleSnapshotId,
          schedule_snapshot_sha256: scheduleReviewContext.value.snapshotContentSha256,
          schedule_external_project_id: scheduleReviewContext.value.externalProjectId,
          schedule_external_revision: scheduleReviewContext.value.externalRevision,
          ...(scheduleReviewContext.value.mode === 'goal_optimization'
            ? { schedule_goal_optimization: '1' }
            : {})
        }
      } else {
        preparedScheduleSnapshotId.value = ''
        scheduleReviewThreadId.value = ''
      }
    }
    router.replace({
      name: 'AgentCompWithThreadId',
      params: { thread_id: nextThreadId },
      query
    })
  } else {
    preparedScheduleSnapshotId.value = ''
    scheduleReviewThreadId.value = ''
    router.replace({ name: 'AgentComp' })
  }
}

const setScheduleReviewQuestion = ({ key }) => {
  const suggestion = scheduleReviewSuggestions.find((item) => item.key === key)
  if (!suggestion || !scheduleReviewContext.value) return
  chatComponentRef.value?.setDraftMessage?.(
    buildScheduleReviewDraft(scheduleReviewContext.value, suggestion.question)
  )
}

const shortId = (value) => (value ? `${value.slice(0, 8)}…${value.slice(-6)}` : '-')

const agentQuickSwitchOptions = computed(() =>
  (agents.value || [])
    .filter((agent) => !agent.is_subagent)
    .map((agent) => ({
      label: agent.name || agent.id,
      value: agent.id,
      icon: agent.icon || '',
      defaultIcon: agent.id ? generatePixelAvatar(agent.id) : '',
      isBuiltin: isBuiltinAgent(agent)
    }))
)

const currentAgentOption = computed(() =>
  agentQuickSwitchOptions.value.find((agent) => agent.value === selectedAgentId.value)
)

const currentAgentLabel = computed(() => {
  if (isLoadingConfig.value) return '加载中...'
  return currentAgentOption.value?.label || '智能体'
})

const agentDropdownOpen = ref(false)
const agentBackendOptions = ref([])
const agentBackendsLoaded = ref(false)

const loadAgentBackends = async () => {
  if (agentBackendsLoaded.value) return
  const response = await agentApi.getAgentBackends()
  agentBackendOptions.value = (response.backends || []).map((backend) => ({
    label: backend.name || backend.backend_id,
    value: backend.backend_id
  }))
  agentBackendsLoaded.value = true
}

const handleAgentSwitch = async (agentId, hasActiveThread) => {
  if (!agentId || agentId === selectedAgentId.value) return
  if (hasActiveThread) {
    message.info('当前对话已绑定智能体，请新建对话后切换')
    return
  }
  try {
    await agentStore.selectAgent(agentId)
    agentDropdownOpen.value = false
  } catch (error) {
    console.error('切换智能体出错:', error)
    message.error('切换智能体失败')
  }
}

const handleAgentSaved = async () => {
  await agentStore.fetchAgents()
  if (selectedAgentId.value) {
    await agentStore.fetchAgentDetail(selectedAgentId.value, true)
  }
}

const openAgentManagement = async () => {
  agentDropdownOpen.value = false
  if (!selectedAgentId.value) {
    message.warning('请先选择智能体')
    return
  }
  try {
    await loadAgentBackends()
    await agentEditModalRef.value?.openEdit(selectedAgentId.value)
  } catch (error) {
    message.error(error.message || '打开智能体配置失败')
  }
}
</script>

<style lang="less" scoped>
.agent-view {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100vh;
  overflow: hidden;
}

.agent-view-body {
  --gap-radius: 6px;
  display: flex;
  flex-direction: row;
  width: 100%;
  flex: 1;
  height: 100%;
  overflow: hidden;
  position: relative;

  .content {
    flex: 1;
    display: flex;
    flex-direction: column;
  }
}

.content {
  flex: 1;
  overflow: hidden;
}

.schedule-review-context {
  display: flex;
  align-items: center;
  min-width: 0;
  max-width: min(620px, calc(100vw - 440px));
  gap: 6px;
  color: var(--color-text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.schedule-review-context strong {
  color: var(--color-text);
}

.schedule-review-context span {
  overflow: hidden;
  max-width: 160px;
  text-overflow: ellipsis;
}

.schedule-review-context code {
  color: var(--main-700);
}

.schedule-suggestion-trigger {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.config-dropdown-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
  max-width: min(240px, calc(100vw - 160px));
  gap: 4px;
}

.config-dropdown-trigger :deep(svg) {
  color: currentColor;
}

.config-dropdown-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: currentColor;
}

.config-dropdown-chevron {
  flex-shrink: 0;
  color: currentColor;
}

// 响应式优化
@media (max-width: 520px) {
  .schedule-review-context span {
    display: none;
  }

  .schedule-review-context {
    max-width: calc(100vw - 250px);
  }

  .config-dropdown-trigger {
    max-width: calc(100vw - 112px);
  }
}
</style>

<style lang="less">
.config-dropdown-overlay .config-dropdown-panel {
  min-width: 188px;
  max-width: min(260px, calc(100vw - 24px));
  padding: 4px;
  background: var(--gray-0);
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  box-shadow:
    0 8px 24px rgba(0, 0, 0, 0.08),
    0 2px 8px rgba(0, 0, 0, 0.04);
}

.config-dropdown-overlay .config-dropdown-item {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  width: 100%;
  padding: 6px 8px;
  border: none;
  border-radius: 6px;
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.config-dropdown-overlay .config-dropdown-item:hover {
  background: var(--gray-50);
}

.config-dropdown-overlay .config-dropdown-item.disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.config-dropdown-overlay .config-dropdown-item.selected {
  background: var(--gray-50);
}

.config-dropdown-overlay .config-dropdown-item.action-item {
  color: var(--gray-800);
}

.config-dropdown-overlay .config-dropdown-item-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  line-height: 1.35;
  color: var(--gray-800);
}

.config-dropdown-overlay .config-dropdown-item-icon,
.config-dropdown-overlay .config-dropdown-item-icon-image,
.config-dropdown-overlay .config-dropdown-item-icon-empty {
  flex-shrink: 0;
}

.config-dropdown-overlay .config-dropdown-item-icon {
  color: var(--gray-500);
}

.config-dropdown-overlay .config-dropdown-item-icon-image,
.config-dropdown-overlay .config-dropdown-item-icon-empty {
  width: 24px;
  height: 24px;
  border-radius: 4px;
}

.config-dropdown-overlay .config-dropdown-item-icon-image {
  object-fit: cover;
}

.config-dropdown-overlay .config-dropdown-item-badge {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 11px;
  line-height: 1.4;
}

.config-dropdown-overlay .config-dropdown-item-check {
  flex-shrink: 0;
  color: var(--main-600);
}

.config-dropdown-overlay .config-dropdown-hint {
  padding: 6px 8px;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.4;
}

.config-dropdown-overlay .config-dropdown-divider {
  height: 1px;
  margin: 4px 4px;
  background: var(--gray-100);
}
</style>
