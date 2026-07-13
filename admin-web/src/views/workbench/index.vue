<template>
  <div class="workbench">
    <ConversationList
      ref="conversationListRef"
      class="panel list"
      :active-key="selectedGroupKey"
      @select="selectConversation"
    />
    <MessagePanel
      ref="messagePanelRef"
      class="panel messages"
      :conversation-id="selectedId"
      :conversation-ids="selectedIds"
      @loaded="handleConversationLoaded"
    />
    <SupervisionPanel
      class="panel side"
      :conversation-id="selectedId"
      :conversation="conversation"
      :profile="profile"
      :profile-loading="profileLoading"
      @changed="handleChanged"
    />
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { markConversationRead, type ConversationItem } from '@/api/admin/conversations'
import { getUserProfileBundle, type UserProfile } from '@/api/user-profile'
import type { ConversationGroupItem } from './conversationGrouping'
import ConversationList from './components/ConversationList.vue'
import MessagePanel from './components/MessagePanel.vue'
import SupervisionPanel from './components/SupervisionPanel.vue'

defineOptions({ name: 'Workbench' })

const FALLBACK_SYNC_INTERVAL_MS = 30_000

const selectedId = ref('')
const route = useRoute()
const selectedIds = ref<string[]>([])
const selectedGroupKey = ref('')
const selectedUnreadCount = ref(0)
const conversation = ref<ConversationItem>()
const profile = ref<UserProfile>()
const profileLoading = ref(false)
const conversationListRef = ref<InstanceType<typeof ConversationList>>()
const messagePanelRef = ref<InstanceType<typeof MessagePanel>>()
let eventSource: EventSource | undefined
let fallbackTimer: number | undefined
let markingReadKey = ''
let profileRequestKey = 0

const selectConversation = (item: ConversationGroupItem) => {
  selectedId.value = item.conversation_id
  selectedIds.value = item.conversation_ids
  selectedGroupKey.value = item.group_key
  selectedUnreadCount.value = item.unread_count
}

const handleChanged = async () => {
  await syncWorkbench()
}

const handleConversationLoaded = (loadedConversation: ConversationItem | undefined) => {
  conversation.value = loadedConversation
  void loadProfile(loadedConversation?.user_id)
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
  await conversationListRef.value?.load({ silent: true })
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
  eventSource = new EventSource('/api/v1/admin/conversations/events', {
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
  await conversationListRef.value?.load({ silent: true })
  const item = conversationListRef.value?.getItemByConversationId(conversationId)
  if (item) selectConversation(item)
}

const handleVisibilityChange = () => {
  if (document.visibilityState === 'visible') {
    void syncWorkbench()
    if (!eventSource || eventSource.readyState === EventSource.CLOSED) {
      connectEvents()
    }
  }
}

onMounted(() => {
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

@media (max-width: 1100px) {
  .workbench {
    grid-template-columns: 280px minmax(0, 1fr);
  }

  .side {
    grid-column: 1 / -1;
    min-height: 360px;
  }
}
</style>
