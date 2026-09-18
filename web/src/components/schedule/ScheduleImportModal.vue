<template>
  <a-modal
    v-model:open="visible"
    title="导入排期"
    width="720px"
    :mask-closable="!submitting"
    :closable="!submitting"
    :bodyStyle="{ maxHeight: 'calc(100vh - 220px)', overflowY: 'auto' }"
    @cancel="closeModal"
  >
    <a-upload-dragger
      accept=".json,application/json"
      :file-list="fileList"
      :max-count="1"
      :before-upload="readFile"
      @remove="clearFile"
    >
      <UploadCloud :size="32" class="upload-icon" />
      <p class="ant-upload-text">点击或拖拽处理完成的排期 JSON</p>
      <p class="ant-upload-hint">支持版本化来源 JSON 和 canonical_schedule_v2.2–v2.8，最大 10 MiB；MPP 请先通过 Bridge 转换</p>
    </a-upload-dragger>

    <a-alert
      v-if="fileError"
      type="error"
      show-icon
      :message="fileError"
      class="import-alert"
    />

    <template v-if="parsed">
      <section class="schedule-summary" aria-label="待导入排期摘要">
        <div class="summary-heading">
          <div>
            <strong>{{ parsed.summary.projectName }}</strong>
            <span>{{ parsed.fileName }}</span>
          </div>
          <a-tag :color="parsed.kind === 'import' ? 'blue' : 'orange'">
            {{ parsed.kind === 'import' ? '来源 JSON' : 'Canonical 快照' }}
          </a-tag>
        </div>
        <a-descriptions size="small" :column="1">
          <a-descriptions-item label="格式版本">{{ parsed.schemaVersion }}</a-descriptions-item>
          <a-descriptions-item label="规模">
            {{ parsed.summary.tasks }} 个任务 · {{ parsed.summary.dependencies }} 条依赖
          </a-descriptions-item>
        </a-descriptions>
      </section>

      <a-alert
        v-if="parsed.kind === 'snapshot'"
        type="warning"
        show-icon
        message="这是严格 Canonical 兼容入口；普通外部来源应优先提交版本化来源 JSON。"
        class="import-alert"
      />

      <a-form ref="formRef" :model="form" :rules="rules" layout="vertical" class="identity-form">
        <a-form-item label="外部项目 ID" name="externalProjectId">
          <a-input v-model:value="form.externalProjectId" :maxlength="256" />
        </a-form-item>
        <a-form-item label="外部快照 ID" name="externalSnapshotId">
          <a-input v-model:value="form.externalSnapshotId" :maxlength="256" />
        </a-form-item>
        <a-form-item label="外部版本" name="externalRevision">
          <a-input v-model:value="form.externalRevision" :maxlength="256" />
        </a-form-item>
      </a-form>

      <a-alert
        v-if="submissionError"
        type="error"
        show-icon
        :message="submissionError"
        class="import-alert"
        aria-live="polite"
      />
    </template>

    <template #footer>
      <a-space>
        <a-button :disabled="submitting" @click="closeModal">取消</a-button>
        <a-button type="primary" :disabled="!parsed" :loading="submitting" @click="submit">
          校验并导入
        </a-button>
      </a-space>
    </template>
  </a-modal>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { UploadCloud } from 'lucide-vue-next'
import { scheduleApi } from '@/apis/schedule_api'
import {
  MAX_SCHEDULE_JSON_BYTES,
  buildScheduleSubmission,
  parseScheduleJsonText,
  scheduleSubmissionErrorMessage
} from '@/utils/scheduleImport'

const props = defineProps({ open: { type: Boolean, default: false } })
const emit = defineEmits(['update:open', 'success'])

const visible = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value)
})
const formRef = ref()
const fileList = ref([])
const parsed = ref(null)
const requestId = ref('')
const fileError = ref('')
const submissionError = ref('')
const submitting = ref(false)
const form = reactive({ externalProjectId: '', externalSnapshotId: '', externalRevision: '' })
const rules = {
  externalProjectId: [{ required: true, whitespace: true, message: '请输入外部项目 ID' }],
  externalSnapshotId: [{ required: true, whitespace: true, message: '请输入外部快照 ID' }],
  externalRevision: [{ required: true, whitespace: true, message: '请输入外部版本' }]
}

const readFile = async (file) => {
  clearFile()
  if (!file.name.toLowerCase().endsWith('.json')) {
    fileError.value = '仅支持 .json 文件。'
    return false
  }
  if (!file.size || file.size > MAX_SCHEDULE_JSON_BYTES) {
    fileError.value = file.size ? '排期 JSON 不能超过 10 MiB。' : '排期 JSON 不能为空。'
    return false
  }
  try {
    const result = parseScheduleJsonText(await file.text(), file.name)
    parsed.value = result
    fileList.value = [file]
    requestId.value = `schedule-${result.kind}-${
      typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
    }`
    form.externalProjectId = result.externalProjectId
    form.externalSnapshotId = result.externalSnapshotId
    form.externalRevision = result.externalRevision
  } catch (error) {
    fileError.value = error instanceof SyntaxError ? '文件不是有效 JSON。' : error.message
  }
  return false
}

const clearFile = () => {
  fileList.value = []
  parsed.value = null
  requestId.value = ''
  fileError.value = ''
  submissionError.value = ''
  form.externalProjectId = ''
  form.externalSnapshotId = ''
  form.externalRevision = ''
  formRef.value?.clearValidate()
  return true
}

const submit = async () => {
  submissionError.value = ''
  try {
    await formRef.value.validate()
    const payload = buildScheduleSubmission(parsed.value, {
      requestId: requestId.value,
      ...form
    })
    submitting.value = true
    const result =
      parsed.value.kind === 'import'
        ? await scheduleApi.submitImport(payload)
        : await scheduleApi.submitSnapshot(payload)
    message.success(result.idempotent_replay ? '排期已存在，已打开原快照。' : '排期导入成功。')
    emit('success', result)
    visible.value = false
    clearFile()
  } catch (error) {
    if (!error?.errorFields) submissionError.value = scheduleSubmissionErrorMessage(error)
  } finally {
    submitting.value = false
  }
}

const closeModal = () => {
  if (submitting.value) return
  visible.value = false
  clearFile()
}
</script>

<style lang="less" scoped>
.upload-icon {
  margin-bottom: 8px;
  color: var(--main-color);
}

.import-alert,
.schedule-summary,
.identity-form {
  margin-top: 16px;
}

.schedule-summary {
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.summary-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.summary-heading div {
  display: grid;
  gap: 4px;
}

.summary-heading span {
  color: var(--gray-500);
  word-break: break-all;
}

:deep(.ant-descriptions-item-content) {
  word-break: break-word;
}
</style>
