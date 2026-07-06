<template>
  <div class="workbench">
    <ConversationList
      ref="conversationListRef"
      class="panel list"
      :active-id="selectedId"
      @select="selectConversation"
    />
    <MessagePanel
      ref="messagePanelRef"
      class="panel messages"
      :conversation-id="selectedId"
      @loaded="conversation = $event"
    />
    <SupervisionPanel
      class="panel side"
      :conversation-id="selectedId"
      :conversation="conversation"
      @changed="handleChanged"
    />
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import type { ConversationItem } from '@/api/admin/conversations'
import ConversationList from './components/ConversationList.vue'
import MessagePanel from './components/MessagePanel.vue'
import SupervisionPanel from './components/SupervisionPanel.vue'

defineOptions({ name: 'Workbench' })

const FALLBACK_SYNC_INTERVAL_MS = 30_000

const selectedId = ref('')
const conversation = ref<ConversationItem>()
const conversationListRef = ref<InstanceType<typeof ConversationList>>()
const messagePanelRef = ref<InstanceType<typeof MessagePanel>>()
let eventSource: EventSource | undefined
let fallbackTimer: number | undefined

const selectConversation = (id: string) => {
  selectedId.value = id
}

const handleChanged = async () => {
  await syncWorkbench()
}

const syncWorkbench = async (conversationId?: string) => {
  await conversationListRef.value?.load({ silent: true })
  if (!conversationId || conversationId === selectedId.value) {
    await messagePanelRef.value?.load({ silent: true })
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

const handleVisibilityChange = () => {
  if (document.visibilityState === 'visible') {
    void syncWorkbench()
    if (!eventSource || eventSource.readyState === EventSource.CLOSED) {
      connectEvents()
    }
  }
}

onMounted(() => {
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
