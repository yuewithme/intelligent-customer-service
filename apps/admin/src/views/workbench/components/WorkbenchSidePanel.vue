<template>
  <aside class="workbench-side-panel">
    <div class="side-switch" role="tablist" aria-label="右侧面板">
      <button
        v-for="item in tabs"
        :key="item.value"
        type="button"
        role="tab"
        :aria-selected="activeTab === item.value"
        :class="{ active: activeTab === item.value }"
        @click="activeTab = item.value"
      >
        {{ item.label }}
      </button>
    </div>

    <SupervisionPanel
      v-if="activeTab === 'supervision'"
      :conversation-id="conversationId"
      :conversation="conversation"
      :agent-relationship="agentRelationship"
      :profile="profile"
      :profile-loading="profileLoading"
      @changed="$emit('changed')"
    />
    <CareManualPanel
      v-else-if="activeTab === 'care-manuals'"
      :conversation-id="conversationId"
      :conversation="conversation"
      @changed="$emit('changed')"
    />
    <UserTagPanel
      v-else
      :user-id="conversation?.user_id || ''"
      :profile="profile"
      :profile-loading="profileLoading"
      @profile-changed="$emit('profile-changed', $event)"
    />
  </aside>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type {
  AgentRelationshipState,
  ConversationItem
} from '@/api/admin/conversations'
import type { UserProfile } from '@/api/user-profile'
import CareManualPanel from './CareManualPanel.vue'
import SupervisionPanel from './SupervisionPanel.vue'
import UserTagPanel from './UserTagPanel.vue'

defineProps<{
  conversationId: string
  conversation?: ConversationItem
  agentRelationship?: AgentRelationshipState
  profile?: UserProfile
  profileLoading?: boolean
}>()
defineEmits<{ changed: []; 'profile-changed': [profile: UserProfile] }>()

const tabs = [
  { label: '监督面板', value: 'supervision' },
  { label: '养护手册', value: 'care-manuals' },
  { label: '客户标签', value: 'customer-tags' }
] as const
const activeTab = ref<(typeof tabs)[number]['value']>('supervision')
</script>

<style scoped>
.workbench-side-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.side-switch {
  display: grid;
  flex: 0 0 auto;
  grid-template-columns: repeat(3, 1fr);
  padding: 8px 12px 0;
  border-bottom: 1px solid #e5e7eb;
}

.side-switch button {
  padding: 9px 8px 10px;
  color: #6b7280;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  font-size: 14px;
  cursor: pointer;
}

.side-switch button.active {
  color: #256d59;
  border-bottom-color: #256d59;
  font-weight: 600;
}

.workbench-side-panel > :deep(.supervision),
.workbench-side-panel > :deep(.care-manual-panel),
.workbench-side-panel > :deep(.user-tag-panel) {
  min-height: 0;
}
</style>
