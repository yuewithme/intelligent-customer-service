<template>
  <aside class="conversation-list">
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
        {{ testView ? '正式对话' : '测试对话' }}
      </ElButton>
      <ElButton :icon="Refresh" circle aria-label="刷新会话列表" @click="load()" />
    </div>
    <ElInput
      v-model="keyword"
      :prefix-icon="Search"
      clearable
      placeholder="搜索昵称、备注、微信号、聊天记录"
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
        :class="{ active: item.group_key === activeKey, pinned: item.is_pinned }"
        type="button"
        @click="emit('select', item)"
        @contextmenu.prevent="openContextMenu($event, item)"
      >
        <ElAvatar :size="36" :src="item.user_avatar_url || undefined">
          {{ avatarText(item) }}
        </ElAvatar>
        <div class="item-main">
          <div class="item-head">
            <strong>{{ displayName(item) }}</strong>
            <span class="item-indicators">
              <ElIcon v-if="item.is_pinned" title="已置顶" aria-label="已置顶"><Top /></ElIcon>
              <ElIcon v-if="item.is_muted" title="消息免打扰" aria-label="消息免打扰"><MuteNotification /></ElIcon>
              <span v-if="item.is_muted && item.unread_count" class="muted-unread" aria-label="有未读消息" />
              <ElBadge v-else-if="item.unread_count" :value="item.unread_count" />
            </span>
          </div>
          <div v-if="item.search_matches?.length" class="search-match">
            <span class="search-source">{{ item.search_matches.map(match => match.label).join('、') }}</span>
            <span>{{ item.search_matches.find(match => match.field === 'message')?.text || item.search_matches[0].text }}</span>
          </div>
        </div>
      </button>
    </div>
    <ElPagination
      v-if="total > 50"
      small
      layout="prev, pager, next"
      :pager-count="3"
      :current-page="page"
      :page-size="50"
      :total="total"
      :disabled="loading"
      @current-change="load({ page: $event })"
    />
    <Teleport to="body">
      <div
        v-if="contextMenu"
        ref="contextMenuRef"
        class="conversation-context-menu"
        role="menu"
        aria-label="会话操作"
        :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }"
        @contextmenu.prevent
        @keydown="handleMenuKeydown"
      >
        <button type="button" role="menuitem" @click="runAction('hide')">
          <ElIcon><Hide /></ElIcon>隐藏对话
        </button>
        <button type="button" role="menuitem" @click="runAction(contextMenu.item.is_pinned ? 'unpin' : 'pin')">
          <ElIcon><Top /></ElIcon>{{ contextMenu.item.is_pinned ? '取消置顶' : '置顶对话' }}
        </button>
        <button type="button" role="menuitem" @click="runAction('unread')">
          <ElIcon><ChatDotRound /></ElIcon>标为未读
        </button>
        <button type="button" role="menuitem" @click="runAction(contextMenu.item.is_muted ? 'unmute' : 'mute')">
          <ElIcon><MuteNotification /></ElIcon>{{ contextMenu.item.is_muted ? '取消免打扰' : '消息免打扰' }}
        </button>
      </div>
    </Teleport>
  </aside>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ChatDotRound, Hide, MuteNotification, Refresh, Search, Top } from '@element-plus/icons-vue'
import {
  getConversations,
  applyConversationListAction,
  type ConversationListAction,
  type ConversationItem
} from '@/api/admin/conversations'
import { isTestGate, isAdmin } from '@/utils/gate'
import {
  groupConversationsByCustomer,
  type ConversationGroupItem
} from '../conversationGrouping'
import { useMessageTenantStore } from '@/store/modules/messageTenant'

defineProps<{ activeKey: string }>()
const emit = defineEmits<{
  select: [item: ConversationGroupItem]
  hidden: [conversationIds: string[]]
  markedUnread: [conversationIds: string[]]
  viewChange: []
}>()

const testGate = isTestGate()
const tenantStore = useMessageTenantStore()
const loading = ref(false)
const status = ref('')
const keyword = ref('')
const testView = ref(testGate)
const items = ref<ConversationGroupItem[]>([])
const page = ref(1)
const total = ref(0)
const contextMenu = ref<{ item: ConversationGroupItem; x: number; y: number }>()
const contextMenuRef = ref<HTMLElement>()
const actionPending = ref(false)
let contextMenuTrigger: HTMLElement | undefined
let loadSequence = 0
let appliedQuery = ''

const load = async (options: { silent?: boolean; page?: number } = {}) => {
  const sequence = ++loadSequence
  const params = {
    status: status.value || undefined,
    keyword: keyword.value.trim() || undefined,
    channel: testView.value ? undefined : 'wechat',
    test_only: testGate ? undefined : testView.value,
    tenant_id: !testGate && !testView.value ? tenantStore.selectedTenantId || undefined : undefined
  }
  const query = JSON.stringify(params)
  const requestedPage = query !== appliedQuery ? 1 : options.page ?? (options.silent ? page.value : 1)
  if (query !== appliedQuery) {
    items.value = []
    total.value = 0
  }
  loading.value = !options.silent || loading.value
  try {
    const data = await getConversations({
      ...params,
      page: requestedPage,
      page_size: 50
    })
    if (sequence !== loadSequence) return
    const lastPage = Math.max(1, Math.ceil(data.total / 50))
    if (requestedPage > lastPage) {
      await load({ ...options, page: lastPage })
      return
    }
    appliedQuery = query
    page.value = data.page
    total.value = data.total
    items.value = groupConversationsByCustomer(data.items, {
      collapseTestData: testGate
    })
  } finally {
    if (sequence === loadSequence) loading.value = false
  }
}

const toggleTestView = async () => {
  closeContextMenu()
  testView.value = !testView.value
  items.value = []
  emit('viewChange')
  await load()
}

const closeContextMenu = () => {
  contextMenu.value = undefined
}

const openContextMenu = async (event: MouseEvent, item: ConversationGroupItem) => {
  if (testGate || testView.value || actionPending.value) return
  contextMenuTrigger = event.currentTarget as HTMLElement
  const menu = { item, x: event.clientX + 4, y: event.clientY + 4 }
  contextMenu.value = menu
  await nextTick()
  const element = contextMenuRef.value
  if (!element || contextMenu.value?.item !== item) return
  contextMenu.value.x = Math.max(8, Math.min(menu.x, window.innerWidth - element.offsetWidth - 8))
  contextMenu.value.y = Math.max(8, Math.min(menu.y, window.innerHeight - element.offsetHeight - 8))
  element.querySelector<HTMLButtonElement>('button')?.focus({ preventScroll: true })
}

const handleOutsidePointer = (event: PointerEvent) => {
  if (!contextMenuRef.value?.contains(event.target as Node)) closeContextMenu()
}

const handleEscape = (event: KeyboardEvent) => {
  if (event.key === 'Escape' && contextMenu.value) {
    closeContextMenu()
    contextMenuTrigger?.focus({ preventScroll: true })
  }
}

const handleMenuKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Tab') {
    closeContextMenu()
    return
  }
  if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
  event.preventDefault()
  const buttons = Array.from(contextMenuRef.value?.querySelectorAll<HTMLButtonElement>('button') || [])
  const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
  buttons[(index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus()
}

const runAction = async (action: ConversationListAction) => {
  const item = contextMenu.value?.item
  if (!item || actionPending.value) return
  closeContextMenu()
  actionPending.value = true
  try {
    const data = await applyConversationListAction(item.conversation_id, action)
    const ids = data.items.map((conversation) => conversation.conversation_id)
    if (action === 'hide') {
      items.value = items.value.filter((candidate) => candidate.group_key !== item.group_key)
      emit('hidden', ids)
    } else if (action === 'unread') {
      emit('markedUnread', ids)
    }
    await load({ silent: true })
    ElMessage.success({
      hide: '对话已隐藏，收到新消息后会重新显示',
      pin: '对话已置顶',
      unpin: '已取消置顶',
      unread: '已标为未读',
      mute: '已开启消息免打扰',
      unmute: '已取消消息免打扰'
    }[action])
  } finally {
    actionPending.value = false
  }
}

const displayName = (item: ConversationItem) => item.user_display_name || item.user_id

const avatarText = (item: ConversationItem) => displayName(item).slice(0, 1).toUpperCase()

const getItemByKey = (groupKey: string) => items.value.find((item) => item.group_key === groupKey)
const getItemByConversationId = (conversationId: string) =>
  items.value.find((item) => item.conversation_ids.includes(conversationId))

watch(
  () => tenantStore.selectedTenantId,
  async (tenantId, previousTenantId) => {
    if (testGate || testView.value || tenantId === previousTenantId) return
    closeContextMenu()
    items.value = []
    emit('viewChange')
    await load()
  }
)

onMounted(async () => {
  document.addEventListener('pointerdown', handleOutsidePointer)
  document.addEventListener('keydown', handleEscape)
  window.addEventListener('resize', closeContextMenu)
  window.addEventListener('scroll', closeContextMenu, true)
  if (!testGate) await tenantStore.loadTenants()
  await load()
})

onBeforeUnmount(() => {
  ++loadSequence
  document.removeEventListener('pointerdown', handleOutsidePointer)
  document.removeEventListener('keydown', handleEscape)
  window.removeEventListener('resize', closeContextMenu)
  window.removeEventListener('scroll', closeContextMenu, true)
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

.toolbar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 36px;
  gap: 8px;
}
.toolbar:has(.test-toggle) { grid-template-columns: minmax(0, 1fr) auto 32px; }
.toolbar :deep(.el-select) { min-width: 0; }
.toolbar .test-toggle { padding: 8px 10px; }
.toolbar :deep(.el-button + .el-button) { margin-left: 0; }

.items {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.item {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  margin-bottom: 4px;
  text-align: left;
  cursor: pointer;
  background: #fff;
  border: 1px solid transparent;
  border-radius: 8px;
  transition: background .15s ease, border-color .15s ease;
}
.item:hover { background: var(--app-surface-soft); }
.item.pinned { background: var(--app-surface-soft); }

.item-indicators { display: flex; align-items: center; gap: 5px; flex-shrink: 0; color: var(--app-text-secondary); }
.muted-unread { width: 7px; height: 7px; border-radius: 50%; background: var(--app-text-secondary); }

.conversation-context-menu {
  position: fixed;
  z-index: 3000;
  width: 166px;
  padding: 5px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 6px 24px rgb(0 0 0 / 14%);
}
.conversation-context-menu button {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--app-text);
  font: inherit;
  font-size: 14px;
  text-align: left;
  cursor: pointer;
}
.conversation-context-menu button:hover,
.conversation-context-menu button:focus-visible { background: var(--app-accent-soft); outline: none; }

.item.active {
  background: var(--app-accent-soft);
  border-color: var(--el-color-primary-light-7);
}

.item-main {
  min-width: 0;
}

.item-head {
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

.search-match {
  display: grid;
  gap: 3px;
  margin: 4px 0 0;
  color: var(--app-text-secondary);
  font-size: 12px;
}
.search-match span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.search-source { color: var(--el-color-primary); }

@media (max-width: 820px), (hover: none) and (pointer: coarse) {
  .conversation-list { padding: 10px; }
  .toolbar { grid-template-columns: minmax(0, 1fr) 36px; }
  .toolbar :deep(.el-button + .el-button) { margin-left: 0; }
  .item { min-height: 56px; padding: 8px 10px; }
}
</style>
