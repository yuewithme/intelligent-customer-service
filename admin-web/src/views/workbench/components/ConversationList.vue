<template>
  <aside class="conversation-list">
    <div class="toolbar">
      <ElSelect v-model="status" clearable placeholder="全部状态" @change="load()">
        <ElOption label="AI 自动回复" value="ai_waiting" />
        <ElOption label="等待接管" value="handoff_pending" />
        <ElOption label="人工接管中" value="human_active" />
        <ElOption label="已结束" value="resolved" />
      </ElSelect>
      <ElButton :icon="Refresh" circle @click="load()" />
    </div>
    <ElInput
      v-model="keyword"
      :prefix-icon="Search"
      clearable
      placeholder="搜索最近消息"
      @clear="load()"
      @keyup.enter="load()"
    />

    <div v-loading="loading" class="items">
      <ElEmpty v-if="!items.length && !loading" description="暂无会话" />
      <button
        v-for="item in items"
        :key="item.group_key"
        class="item"
        :class="{ active: item.group_key === activeKey }"
        type="button"
        @click="emit('select', item)"
        @contextmenu.prevent="hideItem(item)"
      >
        <ElAvatar :size="36" :src="item.user_avatar_url || undefined">
          {{ avatarText(item) }}
        </ElAvatar>
        <div class="item-main">
          <div class="item-head">
            <strong>{{ displayName(item) }}</strong>
            <ElBadge v-if="item.unread_count" :value="item.unread_count" />
          </div>
          <div class="preview">{{ item.last_message || '暂无消息' }}</div>
          <div class="meta">
            <ElTag :type="statusType(item.status)" size="small">{{ statusText(item.status) }}</ElTag>
            <span>{{ formatTime(item.updated_at) }}</span>
          </div>
        </div>
      </button>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import {
  getConversations,
  hideConversation,
  type ConversationItem,
  type ConversationStatus
} from '@/api/admin/conversations'
import { isTestGate } from '@/utils/gate'
import {
  groupConversationsByCustomer,
  type ConversationGroupItem
} from '../conversationGrouping'
import { formatChinaTime } from '../time'

defineProps<{ activeKey: string }>()
const emit = defineEmits<{
  select: [item: ConversationGroupItem]
  hidden: [item: ConversationGroupItem]
}>()

const loading = ref(false)
const status = ref('')
const keyword = ref('')
const items = ref<ConversationGroupItem[]>([])
let pendingLoad: Promise<void> | undefined

const load = async (options: { silent?: boolean } = {}) => {
  if (pendingLoad) {
    await pendingLoad
    return
  }
  if (!options.silent) {
    loading.value = true
  }
  pendingLoad = (async () => {
    const data = await getConversations({
      page: 1,
      page_size: 50,
      status: status.value || undefined,
      keyword: keyword.value || undefined,
      channel: isTestGate() ? undefined : 'wechat'
    })
    items.value = groupConversationsByCustomer(data.items, {
      collapseTestData: isTestGate()
    })
  })()
  try {
    await pendingLoad
  } finally {
    pendingLoad = undefined
    if (!options.silent) {
      loading.value = false
    }
  }
}

const hideItem = async (item: ConversationGroupItem) => {
  if (isTestGate()) return
  try {
    await ElMessageBox.confirm(
      `隐藏“${displayName(item)}”后，该对话将不再显示，但聊天记录不会删除。`,
      '隐藏对话',
      {
        confirmButtonText: '隐藏',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    await Promise.all(item.conversation_ids.map((conversationId) => hideConversation(conversationId)))
    emit('hidden', item)
    await load({ silent: true })
    ElMessage.success('对话已隐藏')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error('隐藏失败，请稍后重试')
    }
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

const displayName = (item: ConversationItem) => item.user_display_name || item.user_id

const avatarText = (item: ConversationItem) => displayName(item).slice(0, 1).toUpperCase()

const formatTime = formatChinaTime

const getItemByKey = (groupKey: string) => items.value.find((item) => item.group_key === groupKey)
const getItemByConversationId = (conversationId: string) =>
  items.value.find((item) => item.conversation_ids.includes(conversationId))

onMounted(load)

defineExpose({ load, getItemByKey, getItemByConversationId })
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
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: 10px;
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

.item-main {
  min-width: 0;
}

.item-head,
.meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.item-head strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
