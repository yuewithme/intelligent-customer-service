<template>
  <section class="care-manual-panel">
    <div class="manual-header">
      <div>
        <h3>养护手册</h3>
        <p>选择手册卡片发送到当前聊天</p>
      </div>
      <ElButton :icon="Refresh" circle :loading="loading" @click="loadManuals" />
    </div>

    <ElInput
      v-model="keyword"
      clearable
      placeholder="搜索手册名称或品种"
      :prefix-icon="Search"
    />

    <ElAlert
      v-if="sendHint"
      :title="sendHint"
      :type="conversation?.status === 'resolved' ? 'warning' : 'info'"
      :closable="false"
    />
    <ElAlert v-if="loadError" :title="loadError" type="error" :closable="false" />

    <div v-loading="loading" class="manual-list">
      <article v-for="manual in filteredManuals" :key="manual.id" class="manual-card">
        <ElImage :src="manual.cover_url || ''" fit="cover" lazy>
          <template #error><div class="cover-fallback">暂无封面</div></template>
        </ElImage>
        <div class="manual-info">
          <strong>{{ manual.title }}</strong>
          <small v-if="manual.orchid_name">{{ manual.orchid_name }}</small>
          <ElButton
            type="primary"
            size="small"
            :loading="sendingId === manual.id"
            :disabled="!canSend || (sendingId !== null && sendingId !== manual.id)"
            @click="sendManual(manual)"
          >
            一键发送
          </ElButton>
        </div>
      </article>
      <ElEmpty
        v-if="!loading && !loadError && !filteredManuals.length"
        :description="keyword ? '没有匹配的养护手册' : '暂无可发送的养护手册'"
        :image-size="72"
      />
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Refresh, Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getCareManuals, type CareManualItem } from '@/api/admin/careManuals'
import {
  updateReplyDraft,
  replyConversationCareManual,
  type ConversationItem
} from '@/api/admin/conversations'
import { useUserStore } from '@/store/modules/user'

const props = defineProps<{
  conversationId: string
  conversation?: ConversationItem
}>()
const emit = defineEmits<{ changed: [] }>()

const userStore = useUserStore()
const manuals = ref<CareManualItem[]>([])
const keyword = ref('')
const loading = ref(false)
const loadError = ref('')
const sendingId = ref<number | null>(null)
let refreshTimer: ReturnType<typeof setInterval> | undefined
const operatorId = computed(() => userStore.user.nickname || 'admin')
const canSend = computed(
  () =>
    Boolean(props.conversationId) &&
    Boolean(props.conversation) &&
    props.conversation?.status !== 'resolved'
)
const sendHint = computed(() => {
  if (!props.conversationId) return '请先选择一个会话'
  if (props.conversation?.status === 'resolved') return '会话已结束，不能发送卡片'
  return ''
})
const filteredManuals = computed(() => {
  const query = keyword.value.trim().toLocaleLowerCase()
  if (!query) return manuals.value
  return manuals.value.filter((manual) =>
    [
      manual.title,
      manual.orchid_name,
      ...manual.aliases,
      ...manual.match_keywords,
      ...manual.product_links.map((link) => link.product_name)
    ]
      .filter(Boolean)
      .some((value) => String(value).toLocaleLowerCase().includes(query))
  )
})

const loadManuals = async () => {
  if (loading.value) return
  loading.value = true
  loadError.value = ''
  try {
    const items: CareManualItem[] = []
    for (let page = 1; ; page += 1) {
      const result = await getCareManuals({ page, page_size: 200, enabled: true })
      items.push(...result.items)
      if (items.length >= result.total) break
      if (!result.items.length) throw new Error('养护手册分页数据不完整')
    }
    manuals.value = items.filter((item) => item.available)
  } catch {
    loadError.value = '养护手册刷新失败，请点击刷新重试'
  } finally {
    loading.value = false
  }
}

const sendManual = async (manual: CareManualItem) => {
  if (!canSend.value || sendingId.value !== null) return
  sendingId.value = manual.id
  const conversationId = props.conversationId
  const senderId = operatorId.value
  const editorId = crypto.randomUUID()
  let acquiredDraft = false
  try {
    if (props.conversation?.status !== 'human_active') {
      await updateReplyDraft(conversationId, {
        operator_id: senderId, editor_id: editorId, active: true
      })
      acquiredDraft = true
    }
    await replyConversationCareManual(conversationId, senderId, manual.id)
    ElMessage.success('养护手册卡片已进入发送队列')
  } finally {
    if (acquiredDraft) {
      await updateReplyDraft(conversationId, {
        operator_id: senderId, editor_id: editorId, active: false
      }).catch(() => undefined)
    }
    emit('changed')
    sendingId.value = null
  }
}

const refreshVisibleManuals = () => {
  if (!document.hidden) void loadManuals()
}

onMounted(() => {
  void loadManuals()
  refreshTimer = setInterval(refreshVisibleManuals, 60_000)
  document.addEventListener('visibilitychange', refreshVisibleManuals)
})

onUnmounted(() => {
  clearInterval(refreshTimer)
  document.removeEventListener('visibilitychange', refreshVisibleManuals)
})
</script>

<style scoped>
.care-manual-panel {
  box-sizing: border-box;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  padding: 16px;
  overflow: hidden;
}

.manual-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.manual-header h3,
.manual-header p {
  margin: 0;
}

.manual-header h3 {
  color: #111827;
  font-size: 16px;
}

.manual-header p {
  margin-top: 5px;
  color: #8a949f;
  font-size: 13px;
}

.manual-list {
  min-height: 120px;
  overflow-y: auto;
}

.manual-card {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid #edf0f3;
}

.manual-card :deep(.el-image) {
  width: 92px;
  height: 82px;
  overflow: hidden;
  background: #f3f6f5;
  border-radius: 7px;
}

.cover-fallback {
  display: grid;
  width: 100%;
  height: 100%;
  place-items: center;
  color: #9ca3af;
  font-size: 13px;
}

.manual-info {
  display: flex;
  min-width: 0;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}

.manual-info strong {
  display: -webkit-box;
  overflow: hidden;
  color: #1f2937;
  font-size: 15px;
  line-height: 1.45;
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.manual-info small {
  color: #72817b;
}

.manual-info .el-button {
  margin-top: auto;
}
</style>
