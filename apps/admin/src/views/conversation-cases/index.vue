<template>
  <ContentWrap>
    <div class="page-head">
      <div>
        <h1>销售案例库</h1>
        <p>沉淀真实销售经验，供小兰理解沟通原则和优秀表达；历史回复不是固定答案。</p>
      </div>
      <div class="head-actions">
        <ElButton :loading="exporting" @click="exportLibrary">
          导出{{ libraryText(activeLibrary) }} JSONL
        </ElButton>
        <ElButton type="primary" @click="load">刷新</ElButton>
      </div>
    </div>

    <ElAlert
      title="案例用于学习判断思路和表达方式，不会作为线上固定流程、节点或话术直接执行。"
      type="info"
      :closable="false"
      show-icon
      class="notice"
    />

    <div class="metrics">
      <button
        v-for="library in libraries"
        :key="library.value"
        type="button"
        :class="{ active: activeLibrary === library.value }"
        @click="selectLibrary(library.value)"
      >
        <span>{{ library.label }}</span>
        <strong>{{ libraryCounts[library.value] || 0 }}</strong>
        <small>{{ library.description }}</small>
      </button>
    </div>

    <ElInput
      v-model="filters.keyword"
      class="search"
      clearable
      placeholder="搜索案例编号或客户原话"
      @clear="load"
      @keyup.enter="load"
    />

    <ElTable v-loading="loading" :data="items" row-key="case_id" @row-dblclick="openCase">
      <ElTableColumn label="案例" width="120" prop="case_id" />
      <ElTableColumn label="客户开场" min-width="360">
        <template #default="{ row }">
          <div class="preview">{{ row.preview }}</div>
          <small>{{ qualityText(row.content_quality) }}</small>
        </template>
      </ElTableColumn>
      <ElTableColumn label="完整会话" width="130">
        <template #default="{ row }">{{ row.turn_count }} 轮 · {{ row.message_count }} 条</template>
      </ElTableColumn>
      <ElTableColumn label="客户轮次" width="100" prop="customer_turn_count" />
      <ElTableColumn label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <ElButton link type="primary" @click="openCase(row)">查看</ElButton>
        </template>
      </ElTableColumn>
    </ElTable>

    <ElDrawer
      v-model="drawerVisible"
      :title="detail ? `${detail.case_id} · ${libraryText(detail.library_type)}` : '案例详情'"
      size="min(920px, 96vw)"
    >
      <template v-if="detail">
        <div class="drawer-meta">
          <ElTag effect="plain">{{ libraryText(detail.library_type) }}</ElTag>
          <ElTag effect="plain" type="info">{{ qualityText(detail.content_quality) }}</ElTag>
          <span>{{ detail.turn_count }} 轮 · {{ detail.message_count }} 条</span>
        </div>
        <div class="transcript">
          <article
            v-for="turn in detail.turns"
            :key="turn.turn_id"
            class="turn"
            :class="turn.role"
          >
            <div class="turn-label">
              {{ turn.role === 'customer' ? '客户' : '历史销售（经验参考）' }}
            </div>
            <p v-for="(message, index) in turn.messages" :key="index">{{ message }}</p>
          </article>
        </div>
      </template>
    </ElDrawer>
  </ContentWrap>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import {
  downloadConversationCaseLibrary,
  getConversationCase,
  getConversationCases,
  type ConversationCaseDetail,
  type ConversationCaseLibrary,
  type ConversationCaseSummary
} from '@/api/admin/conversationCases'

const libraries: Array<{
  value: ConversationCaseLibrary
  label: string
  description: string
}> = [
  { value: 'complete', label: '完整案例', description: '保留上下文，适合人工复盘销售判断' },
  { value: 'cleaned', label: '清洗案例', description: '脱敏整理，适合提供给 Agent 渐进学习' }
]

const loading = ref(false)
const exporting = ref(false)
const drawerVisible = ref(false)
const items = ref<ConversationCaseSummary[]>([])
const detail = ref<ConversationCaseDetail | null>(null)
const libraryCounts = ref<Record<ConversationCaseLibrary, number>>({ complete: 0, cleaned: 0 })
const activeLibrary = ref<ConversationCaseLibrary>('complete')
const filters = reactive({ keyword: '' })

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
  detail.value = await getConversationCase(row.case_id, activeLibrary.value)
  drawerVisible.value = true
}

const selectLibrary = (value: ConversationCaseLibrary) => {
  if (activeLibrary.value === value) return
  activeLibrary.value = value
  void load()
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

const libraryText = (value: ConversationCaseLibrary) =>
  ({ complete: '完整案例', cleaned: '清洗案例' })[value]

const qualityText = (value: string) => ({
  cleaned_transcript: '清洗后的完整会话',
  cleaned_verbatim_case_transcript: '清洗后的逐字会话',
  cleaned_verbatim_chat_export: '清洗后的聊天导出',
  complete_privacy_safe_transcript: '完整会话（隐私字段已保护）',
  reconstructed_from_summary: '由摘要重建'
})[value] || value

onMounted(load)
</script>

<style scoped>
.page-head, .head-actions, .drawer-meta { display: flex; align-items: center; gap: 12px; }
.page-head { justify-content: space-between; }
.page-head h1 { margin: 0 0 6px; font-size: 24px; }
.page-head p, .drawer-meta span, small { color: var(--el-text-color-secondary); }
.notice { margin: 18px 0; }
.metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }
.metrics button { padding: 16px; border: 1px solid var(--el-border-color-light); border-radius: 10px; background: var(--el-bg-color); color: inherit; text-align: left; cursor: pointer; }
.metrics button.active { border-color: var(--el-color-primary); box-shadow: 0 0 0 1px var(--el-color-primary-light-7); }
.metrics span, .metrics strong, .metrics small { display: block; }
.metrics strong { margin-top: 8px; font-size: 26px; }
.metrics small { margin-top: 6px; }
.search { width: min(420px, 100%); margin-bottom: 14px; }
.preview { margin-bottom: 5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.drawer-meta { margin-bottom: 18px; }
.transcript { max-width: 820px; margin: 0 auto; }
.turn { width: min(78%, 680px); margin: 12px 0; padding: 12px 16px; border-radius: 12px; background: var(--el-fill-color-light); }
.turn.customer { margin-left: auto; background: var(--el-color-primary-light-9); }
.turn-label { color: var(--el-text-color-secondary); font-size: 12px; font-weight: 700; }
.turn p { margin: 7px 0 0; line-height: 1.65; white-space: pre-wrap; }
@media (max-width: 760px) { .page-head { align-items: flex-start; flex-direction: column; } .metrics { grid-template-columns: 1fr; } .turn { width: 90%; } }
</style>
