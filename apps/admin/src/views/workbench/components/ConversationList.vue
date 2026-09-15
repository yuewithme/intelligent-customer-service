<template>
  <aside class="conversation-list">
    <div class="list-heading"><h2>会话列表</h2><span>{{ items.length }} 个会话</span></div>
    <div class="toolbar">
      <ElSelect v-model="status" clearable placeholder="全部状态" @change="load()">
        <ElOption label="AI 自动回复" value="ai_waiting" />
        <ElOption label="等待接管" value="handoff_pending" />
        <ElOption label="人工接管中" value="human_active" />
        <ElOption label="已结束" value="resolved" />
      </ElSelect>
      <ElButton
        v-if="isAdmin()"
        class="test-toggle"
        :type="testView ? 'primary' : 'default'"
        @click="toggleTestView"
      >
        {{ testView ? '返回正式对话' : '测试对话' }}
      </ElButton>
      <ElButton :icon="Refresh" circle aria-label="刷新会话列表" @click="load()" />
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
      <ElEmpty
        v-if="!items.length && !loading"
        :description="testView ? '暂无测试对话' : '暂无会话'"
      />
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
import { onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import {
  getConversations,
  hideConversation,
  type ConversationItem,
  type ConversationStatus
} from '@/api/admin/conversations'
import { isTestGate, isAdmin } from '@/utils/gate'
import {
  groupConversationsByCustomer,
  type ConversationGroupItem
} from '../conversationGrouping'
import { formatChinaTime } from '@/utils/time'
import { useMessageTenantStore } from '@/store/modules/messageTenant'

defineProps<{ activeKey: string }>()
const emit = defineEmits<{
  select: [item: ConversationGroupItem]
  hidden: [conversationIds: string[]]
  viewChange: []
}>()

const testGate = isTestGate()
const tenantStore = useMessageTenantStore()
const loading = ref(false)
const status = ref('')
const keyword = ref('')
const testView = ref(testGate)
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
      channel: testView.value ? undefined : 'wechat',
      test_only: testGate ? undefined : testView.value,
      tenant_id:
        !testGate && !testView.value
          ? tenantStore.selectedTenantId || undefined
          : undefined
    })
    items.value = groupConversationsByCustomer(data.items, {
      collapseTestData: testGate
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

const toggleTestView = async () => {
  testView.value = !testView.value
  items.value = []
  emit('viewChange')
  if (pendingLoad) await pendingLoad
  await load()
}

const hideItem = async (item: ConversationGroupItem) => {
  if (testGate || testView.value) return
  try {
    await ElMessageBox.confirm(
      `隐藏“${displayName(item)}”后，它将不再出现在会话列表中，但聊天记录不会删除。`,
      '隐藏对话',
      {
        confirmButtonText: '隐藏',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
  } catch {
    return
  }
  await Promise.all(item.conversation_ids.map((id) => hideConversation(id)))
  items.value = items.value.filter((candidate) => candidate.group_key !== item.group_key)
  emit('hidden', item.conversation_ids)
  ElMessage.success('对话已隐藏，收到新消息后会重新显示')
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

watch(
  () => tenantStore.selectedTenantId,
  async (tenantId, previousTenantId) => {
    if (testGate || testView.value || tenantId === previousTenantId) return
    items.value = []
    emit('viewChange')
    if (pendingLoad) await pendingLoad
    await load()
  }
)

onMounted(async () => {
  if (!testGate) await tenantStore.loadTenants()
  await load()
})

defineExpose({ load, getItemByKey, getItemByConversationId })
</script>

<style scoped>
.conversation-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  padding: 16px 12px 8px;
}

.list-heading { display: flex; align-items: center; justify-content: space-between; padding: 0 4px 4px; }
.list-heading h2 { margin: 0; font-size: 17px; font-weight: 600; }
.list-heading span { color: var(--app-text-secondary); font-size: 13px; }

.toolbar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 36px;
  gap: 8px;
}
.toolbar:has(.test-toggle) :deep(.el-select) { grid-column: 1 / -1; }
.toolbar :deep(.el-button + .el-button) { margin-left: 0; }

.items {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.item {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: 10px;
  width: 100%;
  padding: 14px 10px;
  margin-bottom: 4px;
  text-align: left;
  cursor: pointer;
  background: #fff;
  border: 1px solid transparent;
  border-radius: 8px;
  transition: background .15s ease, border-color .15s ease;
}
.item:hover { background: var(--app-surface-soft); }

.item.active {
  background: var(--app-accent-soft);
  border-color: var(--el-color-primary-light-7);
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
  color: var(--app-text);
  font-size: 15px;
  font-weight: 600;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview {
  margin: 6px 0 10px;
  overflow: hidden;
  color: var(--app-text-secondary);
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta {
  flex-wrap: wrap;
  gap: 4px 8px;
  font-size: 13px;
  color: var(--app-text-secondary);
}

@media (max-width: 820px), (hover: none) and (pointer: coarse) {
  .conversation-list { padding: 10px; }
  .toolbar { grid-template-columns: minmax(0, 1fr) 36px; }
  .toolbar :deep(.el-button + .el-button) { margin-left: 0; }
  .item { min-height: 76px; padding: 11px 10px; }
}
</style>
