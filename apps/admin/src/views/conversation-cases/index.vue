<template>
  <ContentWrap>
    <div class="page-head">
      <div>
        <h1>整案案例库</h1>
        <p>一位客户的一整段聊天作为一个案例；检查点只用于顺序回放，不会把案例拆散。</p>
      </div>
      <div class="head-actions">
        <ElButton :loading="exporting" @click="exportLibrary">
          导出{{ libraryText(activeLibrary) }} JSONL
        </ElButton>
        <ElButton type="primary" @click="load">刷新</ElButton>
      </div>
    </div>

    <ElAlert
      title="历史客服回复只是对照参考，不是标准答案。影子方案看不到参考回复，并维护自己的历史；固定客户后续消息仍属于历史回放，不等同于真实客户模拟。"
      type="warning"
      :closable="false"
      show-icon
      class="notice"
    />

    <div class="metrics">
      <button
        type="button"
        :class="{ active: activeLibrary === 'complete' }"
        @click="selectLibrary('complete')"
      >
        <span>完整案例库</span>
        <strong>{{ libraryCounts.complete || 0 }}</strong>
        <small>保留完整客户对话，用于归档与人工核对</small>
      </button>
      <button
        type="button"
        :class="{ active: activeLibrary === 'cleaned' }"
        @click="selectLibrary('cleaned')"
      >
        <span>清洗后案例库</span>
        <strong>{{ libraryCounts.cleaned || 0 }}</strong>
        <small>独立清洗版本，用于意图标注与影子回放</small>
      </button>
    </div>

    <div class="filters">
      <ElInput
        v-model="filters.keyword"
        clearable
        placeholder="搜索案例编号或客户原话"
        @clear="load"
        @keyup.enter="load"
      />
    </div>

    <ElTable v-loading="loading" :data="items" row-key="case_id" @row-dblclick="openCase">
      <ElTableColumn label="案例" width="110">
        <template #default="{ row }">
          <strong>{{ row.case_id }}</strong>
        </template>
      </ElTableColumn>
      <ElTableColumn label="客户开场" min-width="360">
        <template #default="{ row }">
          <div class="preview">{{ row.preview }}</div>
          <small>{{ qualityText(row.content_quality) }}</small>
        </template>
      </ElTableColumn>
      <ElTableColumn label="完整会话" width="130">
        <template #default="{ row }">{{ row.turn_count }} 轮 / {{ row.message_count }} 条</template>
      </ElTableColumn>
      <ElTableColumn label="回放检查点" width="120" prop="checkpoint_count" />
      <ElTableColumn label="操作" width="110" fixed="right">
        <template #default="{ row }">
          <ElButton link type="primary" @click="openCase(row)">查看整案</ElButton>
        </template>
      </ElTableColumn>
    </ElTable>

    <ElDrawer
      v-model="drawerVisible"
      :title="detail ? `${detail.case_id} · ${libraryText(detail.library_type)}` : '案例详情'"
      size="min(1180px, 96vw)"
      @closed="stopPolling"
    >
      <template v-if="detail">
        <div class="drawer-head">
          <div>
            <ElTag effect="plain">{{ libraryText(detail.library_type) }}</ElTag>
            <ElTag effect="plain" type="info">{{ qualityText(detail.content_quality) }}</ElTag>
            <span>{{ detail.turn_count }} 轮 · {{ detail.checkpoint_count }} 个检查点</span>
          </div>
          <ElButton
            v-if="detail.library_type === 'cleaned'"
            type="primary"
            :disabled="readOnly || runInProgress"
            :loading="starting"
            @click="startRun"
          >
            {{ runInProgress ? '整案回放中' : '启动整案影子回放' }}
          </ElButton>
        </div>

        <ElAlert
          v-if="detail.library_type === 'complete'"
          title="完整案例仅用于归档与人工核对；意图标注和影子回放固定使用同编号的清洗后案例。"
          type="info"
          :closable="false"
          class="notice"
        />

        <ElAlert
          v-else-if="readOnly"
          title="测试身份为只读模式，不能启动会产生模型调用的整案回放。"
          type="info"
          :closable="false"
          class="notice"
        />

        <ElTabs v-model="activeTab">
          <ElTabPane :label="libraryText(detail.library_type)" name="transcript">
            <div class="transcript">
              <article
                v-for="turn in detail.turns"
                :key="turn.turn_id"
                class="turn"
                :class="turn.role"
              >
                <div class="turn-label">
                  {{ turn.role === 'customer' ? '客户' : '历史客服（仅参考）' }}
                </div>
                <p v-for="(message, index) in turn.messages" :key="index">{{ message }}</p>
              </article>
            </div>
          </ElTabPane>

          <ElTabPane
            v-if="detail.library_type === 'cleaned'"
            :label="`影子回放记录（${runs.length}）`"
            name="runs"
          >
            <div v-if="selectedRun" class="run-summary">
              <div>
                <strong>{{ statusText(selectedRun.status) }}</strong>
                <span>
                  {{ selectedRun.completed_checkpoints }}/{{ selectedRun.total_checkpoints }} 个检查点
                  <template v-if="selectedRun.failed_checkpoints">
                    · {{ selectedRun.failed_checkpoints }} 个失败
                  </template>
                </span>
              </div>
              <ElProgress
                :percentage="runProgress(selectedRun)"
                :status="selectedRun.status === 'failed' ? 'exception' : undefined"
              />
              <div v-if="selectedRun.result?.summary" class="quality-summary">
                <ElTag type="success" effect="plain">
                  通过硬约束 {{ selectedRun.result.summary.clean_checkpoints }}
                </ElTag>
                <ElTag v-if="selectedRun.result.summary.repair_attempts" type="warning" effect="plain">
                  自动修正 {{ selectedRun.result.summary.repair_attempts }}
                </ElTag>
                <ElTag
                  v-for="(count, issue) in selectedRun.result.summary.issue_counts"
                  :key="issue"
                  type="danger"
                  effect="plain"
                >
                  {{ issueText(issue) }} {{ count }}
                </ElTag>
              </div>
            </div>

            <ElCollapse v-if="selectedRun?.result?.turn_results?.length">
              <ElCollapseItem
                v-for="(result, index) in selectedRun.result.turn_results"
                :key="result.checkpoint_id"
                :title="`检查点 ${index + 1} · ${result.customer_message.slice(0, 36)}`"
                :name="result.checkpoint_id"
              >
                <div class="customer-input">
                  <span>客户原话</span>
                  <p>{{ result.customer_message }}</p>
                </div>
                <div v-if="result.status === 'success'" class="comparison">
                  <section>
                    <span>历史客服参考</span>
                    <p>{{ result.reference_reply || '（当时没有客服回复）' }}</p>
                  </section>
                  <section>
                    <span>独立影子方案</span>
                    <div v-if="result.repair_attempted || result.auto_issues?.length" class="issue-row">
                      <ElTag v-if="result.repair_attempted" size="small" type="warning">
                        Harness 已自动修正
                      </ElTag>
                      <ElTag
                        v-for="issue in result.auto_issues"
                        :key="issue"
                        size="small"
                        type="danger"
                      >
                        {{ issueText(issue) }}
                      </ElTag>
                    </div>
                    <p>{{ result.shadow?.reply || '（空回复）' }}</p>
                    <small>
                      {{ result.shadow?.sales_stage }} · {{ result.shadow?.sales_action }}
                    </small>
                    <small v-if="result.shadow?.reason">{{ result.shadow.reason }}</small>
                  </section>
                </div>
                <ElAlert
                  v-else
                  :title="`本检查点失败：${result.error_class || '未知错误'}`"
                  type="error"
                  :closable="false"
                />
              </ElCollapseItem>
            </ElCollapse>

            <ElEmpty v-else description="尚未运行这个完整案例" />

            <div v-if="runs.length" class="run-history">
              <h3>运行历史</h3>
              <ElTable :data="runs" size="small" row-key="run_id" @row-click="selectRun">
                <ElTableColumn label="开始时间" width="170">
                  <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
                </ElTableColumn>
                <ElTableColumn label="状态" width="150">
                  <template #default="{ row }">{{ statusText(row.status) }}</template>
                </ElTableColumn>
                <ElTableColumn label="进度">
                  <template #default="{ row }">
                    {{ row.completed_checkpoints }}/{{ row.total_checkpoints }}
                  </template>
                </ElTableColumn>
              </ElTable>
            </div>
          </ElTabPane>
        </ElTabs>
      </template>
    </ElDrawer>
  </ContentWrap>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  downloadConversationCaseLibrary,
  getConversationCase,
  getConversationCaseRun,
  getConversationCaseRuns,
  getConversationCases,
  startConversationCaseRun,
  type ConversationCaseDetail,
  type ConversationCaseLibrary,
  type ConversationCaseRun,
  type ConversationCaseSummary
} from '@/api/admin/conversationCases'
import { isTestGate } from '@/utils/gate'

const loading = ref(false)
const exporting = ref(false)
const starting = ref(false)
const drawerVisible = ref(false)
const activeTab = ref('transcript')
const items = ref<ConversationCaseSummary[]>([])
const detail = ref<ConversationCaseDetail | null>(null)
const runs = ref<ConversationCaseRun[]>([])
const selectedRun = ref<ConversationCaseRun | null>(null)
const libraryCounts = ref<Record<ConversationCaseLibrary, number>>({
  complete: 0,
  cleaned: 0
})
const activeLibrary = ref<ConversationCaseLibrary>('complete')
const readOnly = isTestGate()
const filters = reactive({ keyword: '' })
let pollTimer: number | undefined

const runInProgress = computed(() =>
  selectedRun.value
    ? ['pending', 'running'].includes(selectedRun.value.status)
    : false
)

const load = async () => {
  loading.value = true
  try {
    const result = await getConversationCases({
      keyword: filters.keyword || undefined,
      library_type: activeLibrary.value
    })
    items.value = result.items
    libraryCounts.value = result.library_counts
  } finally {
    loading.value = false
  }
}

const openCase = async (row: ConversationCaseSummary) => {
  stopPolling()
  detail.value = await getConversationCase(row.case_id, activeLibrary.value)
  runs.value =
    activeLibrary.value === 'cleaned'
      ? await getConversationCaseRuns(row.case_id)
      : []
  selectedRun.value = runs.value[0] || null
  if (selectedRun.value) await refreshSelectedRun()
  activeTab.value = 'transcript'
  drawerVisible.value = true
  if (runInProgress.value) startPolling()
}

const startRun = async () => {
  if (!detail.value) return
  await ElMessageBox.confirm(
    `将按顺序调用模型 ${detail.value.checkpoint_count} 次。影子回复不会发送给客户，也不会写入客户状态。是否继续？`,
    '启动整案影子回放',
    { type: 'warning', confirmButtonText: '开始回放', cancelButtonText: '取消' }
  )
  starting.value = true
  try {
    selectedRun.value = await startConversationCaseRun(detail.value.case_id)
    activeTab.value = 'runs'
    await loadRuns()
    startPolling()
    ElMessage.success('整案影子回放已启动')
  } finally {
    starting.value = false
  }
}

const loadRuns = async () => {
  if (!detail.value) return
  runs.value = await getConversationCaseRuns(detail.value.case_id)
}

const selectRun = async (run: ConversationCaseRun) => {
  selectedRun.value = await getConversationCaseRun(run.run_id)
  if (runInProgress.value) startPolling()
}

const refreshSelectedRun = async () => {
  if (!selectedRun.value) return
  selectedRun.value = await getConversationCaseRun(selectedRun.value.run_id)
}

const startPolling = () => {
  stopPolling()
  pollTimer = window.setInterval(async () => {
    await refreshSelectedRun()
    if (!runInProgress.value) {
      stopPolling()
      await loadRuns()
    }
  }, 2000)
}

const stopPolling = () => {
  if (pollTimer !== undefined) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
}

const exportLibrary = async () => {
  exporting.value = true
  try {
    const blob = await downloadConversationCaseLibrary(activeLibrary.value)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${activeLibrary.value}-conversation-case-library.jsonl`
    anchor.click()
    URL.revokeObjectURL(url)
  } finally {
    exporting.value = false
  }
}

const selectLibrary = (value: ConversationCaseLibrary) => {
  if (activeLibrary.value === value) return
  activeLibrary.value = value
  load()
}

const libraryText = (value: ConversationCaseLibrary) =>
  ({ complete: '完整案例库', cleaned: '清洗后案例库' })[value]

const qualityText = (value: string) =>
  ({
    cleaned_transcript: '清洗后的完整会话',
    cleaned_verbatim_case_transcript: '清洗后的逐字会话',
    cleaned_verbatim_chat_export: '清洗后的聊天导出',
    complete_privacy_safe_transcript: '完整会话（隐私字段已保护）',
    reconstructed_from_summary: '由摘要重建'
  })[value] || value

const statusText = (value: ConversationCaseRun['status']) =>
  ({
    pending: '等待运行',
    running: '运行中',
    completed: '已完成',
    completed_with_errors: '完成，部分检查点失败',
    failed: '运行失败'
  })[value]

const runProgress = (run: ConversationCaseRun) =>
  run.total_checkpoints
    ? Math.round((run.completed_checkpoints / run.total_checkpoints) * 100)
    : 0

const issueText = (value: string) =>
  ({
    rag_without_evidence: '无证据使用 RAG',
    unverified_fact_usage: '引用未验证事实',
    overlong_reply: '回复过长',
    multiple_questions: '一次追问过多'
  })[value] || value

const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN')

onMounted(load)
onUnmounted(stopPolling)
</script>

<style scoped>
.page-head,
.drawer-head,
.run-summary > div:first-child {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.page-head h1 {
  margin: 0 0 6px;
  font-size: 24px;
}

.page-head p,
.drawer-head span,
.run-summary span,
small {
  color: var(--el-text-color-secondary);
}

.head-actions,
.drawer-head > div {
  display: flex;
  align-items: center;
  gap: 10px;
}

.notice {
  margin: 18px 0;
}

.metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}

.metrics > button {
  padding: 16px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 10px;
  background: var(--el-bg-color);
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.metrics > button.active {
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 1px var(--el-color-primary-light-7);
}

.metrics span,
.metrics strong {
  display: block;
}

.metrics small {
  margin-top: 6px;
}

.metrics strong {
  margin-top: 8px;
  font-size: 26px;
}

.filters {
  margin-bottom: 14px;
}

.preview {
  margin-bottom: 5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.block {
  display: block;
  margin-top: 4px;
}

.drawer-head {
  margin-bottom: 16px;
}

.transcript {
  max-width: 900px;
  margin: 0 auto;
}

.turn {
  width: min(78%, 720px);
  margin: 12px 0;
  padding: 12px 16px;
  border-radius: 12px;
  background: var(--el-fill-color-light);
}

.turn.customer {
  margin-left: auto;
  background: var(--el-color-primary-light-9);
}

.turn-label,
.customer-input span,
.comparison span {
  font-size: 12px;
  font-weight: 700;
  color: var(--el-text-color-secondary);
}

.turn p,
.customer-input p,
.comparison p {
  margin: 7px 0 0;
  line-height: 1.65;
  white-space: pre-wrap;
}

.run-summary {
  margin-bottom: 16px;
  padding: 14px 16px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 10px;
}

.run-summary .el-progress {
  margin-top: 12px;
}

.quality-summary,
.issue-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.customer-input {
  padding: 12px;
  border-radius: 8px;
  background: var(--el-fill-color-lighter);
}

.comparison {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 12px;
}

.comparison section {
  padding: 14px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 9px;
}

.comparison small {
  display: block;
  margin-top: 10px;
  line-height: 1.5;
}

.run-history {
  margin-top: 24px;
}

@media (max-width: 900px) {
  .metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .comparison {
    grid-template-columns: 1fr;
  }
}
</style>
