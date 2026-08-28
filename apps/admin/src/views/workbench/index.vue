<template>
  <div class="workbench">
    <ConversationList
      ref="conversationListRef"
      class="panel list"
      :class="{ 'mobile-active': mobileView === 'conversations' }"
      :active-key="selectedGroupKey"
      @select="selectConversation"
      @hidden="handleHidden"
      @view-change="clearSelection"
    />
    <MessagePanel
      ref="messagePanelRef"
      class="panel messages"
      :class="{ 'mobile-active': mobileView === 'messages' }"
      :conversation-id="selectedId"
      :conversation-ids="selectedIds"
      :focus-message-id="focusMessageId"
      show-mobile-back
      @mobile-back="mobileView = 'conversations'"
      @loaded="handleConversationLoaded"
    />
    <WorkbenchSidePanel
      class="panel side"
      :class="{
        'mobile-active': mobileView === 'details',
        'mobile-reply-active': mobileView === 'messages' && Boolean(selectedId)
      }"
      :conversation-id="selectedId"
      :conversation="conversation"
      :agent-relationship="agentRelationship"
      :profile="profile"
      :profile-loading="profileLoading"
      :reply-mode="isMobile && mobileView === 'messages'"
      @changed="handleChanged"
      @profile-changed="handleProfileChanged"
    />
    <nav class="mobile-workbench-nav" aria-label="工作台视图">
      <button
        type="button"
        :class="{ active: mobileView === 'conversations' }"
        @click="mobileView = 'conversations'"
      >
        <span>☰</span>会话
      </button>
      <button
        type="button"
        :disabled="!selectedId"
        :class="{ active: mobileView === 'messages' }"
        @click="mobileView = 'messages'"
      >
        <span>▣</span>聊天回复
      </button>
      <button
        type="button"
        :disabled="!selectedId"
        :class="{ active: mobileView === 'details' }"
        @click="mobileView = 'details'"
      >
        <span>◇</span>客户资料
      </button>
    </nav>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  getConversationDetail,
  markConversationRead,
  type AgentRelationshipState,
  type ConversationDetail,
  type ConversationItem
} from '@/api/admin/conversations'
import { getUserProfileBundle, type UserProfile } from '@/api/user-profile'
import type { ConversationGroupItem } from './conversationGrouping'
import ConversationList from './components/ConversationList.vue'
import MessagePanel from './components/MessagePanel.vue'
import WorkbenchSidePanel from './components/WorkbenchSidePanel.vue'
import { isTestGate } from '@/utils/gate'
import { useMessageTenantStore } from '@/store/modules/messageTenant'

defineOptions({ name: 'Workbench' })

const FALLBACK_SYNC_INTERVAL_MS = 30_000

const selectedId = ref('')
const mobileView = ref<'conversations' | 'messages' | 'details'>('conversations')
const isMobile = ref(false)
const route = useRoute()
const tenantStore = useMessageTenantStore()
const selectedIds = ref<string[]>([])
const selectedGroupKey = ref('')
const selectedUnreadCount = ref(0)
const focusMessageId = ref<number>()
const conversation = ref<ConversationItem>()
const agentRelationship = ref<AgentRelationshipState>()
const profile = ref<UserProfile>()
const profileLoading = ref(false)
const conversationListRef = ref<InstanceType<typeof ConversationList>>()
const messagePanelRef = ref<InstanceType<typeof MessagePanel>>()
let eventSource: EventSource | undefined
let fallbackTimer: number | undefined
let mobileMediaQuery: MediaQueryList | undefined
let markingReadKey = ''
let profileRequestKey = 0

const selectConversation = (item: ConversationGroupItem) => {
  focusMessageId.value = undefined
  selectedId.value = item.conversation_id
  selectedIds.value = item.conversation_ids
  selectedGroupKey.value = item.group_key
  selectedUnreadCount.value = item.unread_count
  mobileView.value = 'messages'
}

const handleChanged = async (updatedConversation?: ConversationItem) => {
  if (updatedConversation?.conversation_id === selectedId.value) {
    conversation.value = updatedConversation
  }
  await syncWorkbench()
}

const handleProfileChanged = (updatedProfile: UserProfile) => {
  profile.value = updatedProfile
}

const handleHidden = (conversationIds: string[]) => {
  if (!selectedIds.value.some((id) => conversationIds.includes(id))) return
  clearSelection()
}

const clearSelection = () => {
  selectedId.value = ''
  selectedIds.value = []
  selectedGroupKey.value = ''
  selectedUnreadCount.value = 0
  conversation.value = undefined
  agentRelationship.value = undefined
  profile.value = undefined
  mobileView.value = 'conversations'
}

const handleConversationLoaded = (detail: ConversationDetail | undefined) => {
  conversation.value = detail?.conversation
  agentRelationship.value = detail?.agent_relationship
  void loadProfile(detail?.conversation.user_id)
  if (selectedUnreadCount.value > 0) {
    void markSelectedRead()
  }
}

const markSelectedRead = async () => {
  if (!selectedIds.value.length || selectedUnreadCount.value <= 0) {
    return
  }
  const key = selectedIds.value.join('|')
  if (markingReadKey === key) {
    return
  }
  markingReadKey = key
  try {
    await Promise.all(
      selectedIds.value.map((conversationId) => markConversationRead(conversationId))
    )
    selectedUnreadCount.value = 0
    await conversationListRef.value?.load({ silent: true })
  } finally {
    markingReadKey = ''
  }
}

const syncWorkbench = async (conversationId?: string) => {
  await Promise.all([
    conversationListRef.value?.load({ silent: true }),
    isTestGate() ? Promise.resolve() : tenantStore.loadTenants({ silent: true })
  ])
  if (selectedGroupKey.value) {
    const selectedGroup = conversationListRef.value?.getItemByKey(selectedGroupKey.value)
    if (selectedGroup) {
      selectedId.value = selectedGroup.conversation_id
      selectedIds.value = selectedGroup.conversation_ids
      selectedUnreadCount.value = selectedGroup.unread_count
    }
  }
  if (!conversationId || selectedIds.value.includes(conversationId)) {
    await messagePanelRef.value?.load({ silent: true })
  }
}

const loadProfile = async (userId?: string | null) => {
  const requestKey = ++profileRequestKey
  if (!userId) {
    profile.value = undefined
    return
  }
  profileLoading.value = true
  try {
    const bundle = await getUserProfileBundle(userId)
    if (requestKey === profileRequestKey) {
      profile.value = bundle.profile
    }
  } catch {
    if (requestKey === profileRequestKey) {
      profile.value = undefined
    }
  } finally {
    if (requestKey === profileRequestKey) {
      profileLoading.value = false
    }
  }
}

const connectEvents = () => {
  eventSource?.close()
  const eventPath = isTestGate()
    ? '/api/v1/demo-admin/conversations/events'
    : '/api/v1/admin/conversations/events'
  eventSource = new EventSource(eventPath, {
    withCredentials: true
  })
  eventSource.onmessage = (message) => {
    try {
      const event = JSON.parse(message.data)
      if (event.type === 'conversation.changed') {
        void syncWorkbench(event.conversation_id)
      }
    } catch {
      // EventSource reconnects automatically; the fallback sync covers malformed events.
    }
  }
}

const restoreRouteConversation = async () => {
  const conversationId =
    typeof route.query.conversation_id === 'string' ? route.query.conversation_id : ''
  if (!conversationId) return
  const messageId = Number(route.query.message_id)
  await conversationListRef.value?.load({ silent: true })
  const item = conversationListRef.value?.getItemByConversationId(conversationId)
  if (item) {
    selectConversation(item)
  } else {
    const detail = await getConversationDetail(conversationId)
    if (!isTestGate() && detail.conversation.tenant_id !== tenantStore.selectedTenantId) {
      tenantStore.selectTenant(detail.conversation.tenant_id)
      await conversationListRef.value?.load({ silent: true })
      const scopedItem = conversationListRef.value?.getItemByConversationId(conversationId)
      if (scopedItem) {
        selectConversation(scopedItem)
        focusMessageId.value =
          Number.isInteger(messageId) && messageId > 0 ? messageId : undefined
        return
      }
    }
    selectedId.value = detail.conversation.conversation_id
    selectedIds.value = [detail.conversation.conversation_id]
    selectedGroupKey.value = ''
    selectedUnreadCount.value = detail.conversation.unread_count
  }
  focusMessageId.value = Number.isInteger(messageId) && messageId > 0 ? messageId : undefined
  mobileView.value = 'messages'
}

const handleVisibilityChange = () => {
  if (document.visibilityState === 'visible') {
    void syncWorkbench()
    if (!eventSource || eventSource.readyState === EventSource.CLOSED) {
      connectEvents()
    }
  }
}

const syncMobileViewport = () => {
  isMobile.value = mobileMediaQuery?.matches || false
}

onMounted(() => {
  mobileMediaQuery = window.matchMedia('(max-width: 820px)')
  syncMobileViewport()
  mobileMediaQuery.addEventListener('change', syncMobileViewport)
  void restoreRouteConversation()
  connectEvents()
  fallbackTimer = window.setInterval(() => {
    if (document.visibilityState === 'visible') {
      void syncWorkbench()
    }
  }, FALLBACK_SYNC_INTERVAL_MS)
  document.addEventListener('visibilitychange', handleVisibilityChange)
})

onBeforeUnmount(() => {
  mobileMediaQuery?.removeEventListener('change', syncMobileViewport)
  eventSource?.close()
  if (fallbackTimer) {
    window.clearInterval(fallbackTimer)
  }
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})
</script>

<style scoped>
.workbench {
  display: grid;
  grid-template-columns: 320px minmax(420px, 1fr) 360px;
  gap: 12px;
  height: calc(100vh - 96px);
  padding: 12px;
  overflow: hidden;
  background: #f5f7fb;
}

.panel {
  min-height: 0;
  overflow: hidden;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
}

.panel.side {
  box-sizing: border-box;
  overflow: hidden;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}

@media (max-width: 1100px) {
  .workbench {
    grid-template-columns: 280px minmax(0, 1fr);
  }

  .side {
    grid-column: 1 / -1;
    min-height: 360px;
  }
}

@media (max-width: 820px) {
  .workbench {
    display: flex;
    flex-direction: column;
    gap: 8px;
    height: calc(100dvh - 56px);
    padding: 8px 8px max(8px, env(safe-area-inset-bottom));
  }

  .panel {
    display: none;
    flex: 1;
    width: 100%;
    min-height: 0;
  }

  .panel.mobile-active,
  .side.mobile-reply-active {
    display: flex;
  }

  .side.mobile-reply-active {
    flex: 0 0 auto;
    height: auto;
    max-height: 44dvh;
    overflow: auto;
    border: 0;
  }

  .side.mobile-reply-active :deep(.side-switch),
  .side.mobile-reply-active :deep(.supervision > :not(.composer)) {
    display: none;
  }

  .side.mobile-reply-active :deep(.supervision) {
    height: auto;
    padding: 10px;
    overflow: visible;
    background: #fff;
    border: 1px solid #e5e7eb;
    border-radius: 6px;
  }

  .mobile-workbench-nav {
    display: grid;
    flex: 0 0 auto;
    grid-template-columns: repeat(3, 1fr);
    min-height: 56px;
    padding-bottom: env(safe-area-inset-bottom);
    background: #fff;
    border: 1px solid #dfe6e3;
    border-radius: 10px;
    box-shadow: 0 -4px 18px rgb(15 23 42 / 7%);
  }

  .mobile-workbench-nav button {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
    min-height: 54px;
    padding: 5px 4px;
    color: #6b7d76;
    font-size: 11px;
    background: transparent;
    border: 0;
  }

  .mobile-workbench-nav button span { font-size: 17px; line-height: 1; }
  .mobile-workbench-nav button.active { color: #1f7559; font-weight: 700; }
  .mobile-workbench-nav button:disabled { color: #b9c3bf; }
}

@media (min-width: 821px) {
  .mobile-workbench-nav { display: none; }
}
</style>
