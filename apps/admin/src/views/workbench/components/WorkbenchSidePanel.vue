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
      :messages="messages"
      :profile="profile"
      :profile-loading="profileLoading"
      :review-active="reviewActive"
    />
    <CareManualPanel
      v-else-if="activeTab === 'care-manuals'"
      :conversation-id="conversationId"
      :conversation="conversation"
      @changed="$emit('changed')"
    />
    <UserTagPanel
      v-show="activeTab === 'customer-tags'"
      :key="conversation?.user_id || ''"
      :user-id="conversation?.user_id || ''"
      :profile="profile"
      :profile-loading="profileLoading"
      @profile-changed="$emit('profile-changed', $event)"
    />
    <ProductPanel
      v-if="activeTab === 'products'"
      :conversation-id="conversationId"
      :conversation="conversation"
      @changed="$emit('changed')"
    />
  </aside>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type {
  ConversationMessage,
  ConversationItem
} from '@/api/admin/conversations'
import type { UserProfile } from '@/api/user-profile'
import CareManualPanel from './CareManualPanel.vue'
import ProductPanel from './ProductPanel.vue'
import SupervisionPanel from './SupervisionPanel.vue'
import UserTagPanel from './UserTagPanel.vue'

defineProps<{
  conversationId: string
  conversation?: ConversationItem
  messages: ConversationMessage[]
  profile?: UserProfile
  profileLoading?: boolean
  reviewActive: boolean
}>()
const emit = defineEmits<{
  changed: [conversation?: ConversationItem]
  'profile-changed': [profile: UserProfile]
}>()

const tabs = [
  { label: '监督面板', value: 'supervision' },
  { label: '养护手册', value: 'care-manuals' },
  { label: '商品', value: 'products' },
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
  grid-template-columns: repeat(4, 1fr);
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
  font-size: 14px;
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
  .side-switch button { min-width: 0; padding: 10px 4px; font-size: 14px; }
}
</style>
