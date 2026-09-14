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
      v-if="replyMode || activeTab === 'supervision'"
      :conversation-id="conversationId"
      :conversation="conversation"
      :agent-relationship="agentRelationship"
      :profile="profile"
      :profile-loading="profileLoading"
      :reply-mode="replyMode"
      @changed="forwardChanged"
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
  replyMode?: boolean
}>()
const emit = defineEmits<{
  changed: [conversation?: ConversationItem]
  'profile-changed': [profile: UserProfile]
}>()

const forwardChanged = (conversation?: ConversationItem) => emit('changed', conversation)

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
  padding: 12px 12px 0;
  border-bottom: 1px solid var(--app-border);
}

.side-switch button {
  padding: 9px 8px 10px;
  min-width: 0;
  white-space: nowrap;
  color: var(--app-text-secondary);
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  font-size: 13px;
  cursor: pointer;
}

.side-switch button.active {
  color: var(--app-accent);
  border-bottom-color: var(--app-accent);
  font-weight: 600;
}

.workbench-side-panel > :deep(.supervision),
.workbench-side-panel > :deep(.care-manual-panel),
.workbench-side-panel > :deep(.user-tag-panel) {
  min-height: 0;
}

@media (max-width: 820px), (hover: none) and (pointer: coarse) {
  .side-switch { position: sticky; top: 0; z-index: 2; padding: 6px 6px 0; background: #fff; }
  .side-switch button { min-width: 0; padding: 10px 4px; font-size: 13px; }
}
</style>
