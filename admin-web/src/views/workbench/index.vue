<template>
  <div class="workbench">
    <ConversationList
      class="panel list"
      :active-id="selectedId"
      :refresh-key="refreshKey"
      @select="selectConversation"
    />
    <MessagePanel
      class="panel messages"
      :conversation-id="selectedId"
      :refresh-key="refreshKey"
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
import { ref } from 'vue'
import type { ConversationItem } from '@/api/admin/conversations'
import ConversationList from './components/ConversationList.vue'
import MessagePanel from './components/MessagePanel.vue'
import SupervisionPanel from './components/SupervisionPanel.vue'

defineOptions({ name: 'Workbench' })

const selectedId = ref('')
const refreshKey = ref(0)
const conversation = ref<ConversationItem>()
const selectConversation = (id: string) => {
  selectedId.value = id
}

const handleChanged = async () => {
  refreshKey.value += 1
}
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
