<template>
  <aside class="conversation-list">
    <div class="toolbar">
      <ElSelect v-model="status" clearable placeholder="全部状态" @change="load">
        <ElOption label="AI 自动回复" value="ai_waiting" />
        <ElOption label="等待接管" value="handoff_pending" />
        <ElOption label="人工接管中" value="human_active" />
        <ElOption label="已结束" value="resolved" />
      </ElSelect>
      <ElButton :icon="Refresh" circle @click="load" />
    </div>
    <ElInput
      v-model="keyword"
      :prefix-icon="Search"
      clearable
      placeholder="搜索最近消息"
      @clear="load"
      @keyup.enter="load"
    />

    <div v-loading="loading" class="items">
      <ElEmpty v-if="!items.length && !loading" description="暂无会话" />
      <button
        v-for="item in items"
        :key="item.conversation_id"
        class="item"
        :class="{ active: item.conversation_id === activeId }"
        type="button"
        @click="$emit('select', item.conversation_id)"
      >
        <div class="item-head">
          <strong>{{ item.user_id }}</strong>
          <ElBadge v-if="item.unread_count" :value="item.unread_count" />
        </div>
        <div class="preview">{{ item.last_message || '暂无消息' }}</div>
        <div class="meta">
          <ElTag :type="statusType(item.status)" size="small">{{ statusText(item.status) }}</ElTag>
          <span>{{ formatTime(item.updated_at) }}</span>
        </div>
      </button>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { Refresh, Search } from '@element-plus/icons-vue'
import { getConversations, type ConversationItem, type ConversationStatus } from '@/api/admin/conversations'

const props = defineProps<{ activeId: string; refreshKey: number }>()
defineEmits<{ select: [id: string] }>()

const loading = ref(false)
const status = ref('')
const keyword = ref('')
const items = ref<ConversationItem[]>([])

const load = async () => {
  loading.value = true
  try {
    const data = await getConversations({
      page: 1,
      page_size: 50,
      status: status.value || undefined,
      keyword: keyword.value || undefined
    })
    items.value = data.items
  } finally {
    loading.value = false
  }
}

const statusText = (value: ConversationStatus) =>
  ({
    ai_active: 'AI 自动回复',
    ai_waiting: 'AI 等待中',
    handoff_pending: '等待接管',
    human_active: '人工接管',
    resolved: '已结束'
  })[value] || value

const statusType = (value: ConversationStatus) =>
  ({
    ai_active: 'info',
    ai_waiting: 'info',
    handoff_pending: 'warning',
    human_active: 'success',
    resolved: 'danger'
  })[value] as 'info' | 'warning' | 'success' | 'danger'

const formatTime = (value: string) => new Date(value).toLocaleString()

watch(
  () => props.refreshKey,
  () => load()
)
onMounted(load)

defineExpose({ load })
</script>

<style scoped>
.conversation-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  padding: 12px;
}

.toolbar {
  display: grid;
  grid-template-columns: 1fr 32px;
  gap: 8px;
}

.items {
  min-height: 0;
  overflow: auto;
}

.item {
  width: 100%;
  padding: 12px;
  margin-bottom: 8px;
  text-align: left;
  cursor: pointer;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
}

.item.active {
  border-color: #2563eb;
  box-shadow: 0 0 0 1px #2563eb inset;
}

.item-head,
.meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.preview {
  margin: 8px 0;
  overflow: hidden;
  color: #4b5563;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta {
  font-size: 12px;
  color: #6b7280;
}
</style>
