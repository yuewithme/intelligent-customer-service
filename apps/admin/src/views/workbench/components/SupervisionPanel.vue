<template>
  <aside class="supervision">
    <ElEmpty v-if="!conversationId || !conversation" description="请选择会话" />
    <template v-else>
      <div class="section">
        <div class="title">
          <span>监督面板</span>
          <ElTag :type="statusType(conversation.status)">
            {{ statusText(conversation.status) }}
          </ElTag>
        </div>
        <dl>
          <dt>标签</dt>
          <dd>
            <div v-if="profileLoading || catalogLoading" class="muted">更新中...</div>
            <div v-else-if="tags.length" class="tag-list">
              <ElTag v-for="tag in tags" :key="tag" size="small" effect="plain">
                {{ tagValueText(tag) }}
              </ElTag>
            </div>
            <span v-else>-</span>
          </dd>
          <dt>转人工原因</dt>
          <dd>{{ handoffReasonText(conversation.handoff_reason) }}</dd>
        </dl>
      </div>

      <ChatReviewPanel
        :key="conversationId"
        :conversation-id="conversationId"
        :messages="messages"
        :profile="profile"
        :active="reviewActive"
      />

      <YouzanOrderPanel :conversation-id="conversationId" />

    </template>
  </aside>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { ConversationMessage, ConversationItem, ConversationStatus } from '@/api/admin/conversations'
import { getTagCatalog } from '@/api/admin/tags'
import type { UserProfile } from '@/api/user-profile'
import { tagValueText } from '@/utils/tagDisplay'
import YouzanOrderPanel from './YouzanOrderPanel.vue'
import ChatReviewPanel from './ChatReviewPanel.vue'

const props = defineProps<{
  conversationId: string
  conversation?: ConversationItem
  messages: ConversationMessage[]
  profile?: UserProfile
  profileLoading?: boolean
  reviewActive: boolean
}>()

const catalogTags = ref<string[]>([])
const catalogLoading = ref(false)
const tags = computed(() => {
  const selected = new Set(props.profile?.customer_tags?.filter(Boolean) || [])
  return catalogTags.value.filter((tag) => selected.has(tag))
})

const loadCatalog = async () => {
  catalogLoading.value = true
  try {
    const catalog = await getTagCatalog()
    catalogTags.value = catalog.items
      .filter((category) => category.profile_assignable)
      .flatMap((category) => category.tags.map((tag) => tag.value))
  } finally {
    catalogLoading.value = false
  }
}

onMounted(loadCatalog)

const statusText = (value: ConversationStatus) =>
  ({
    ai_active: 'AI 自动回复',
    ai_waiting: 'AI 等待中',
    handoff_pending: '等待接管',
    human_active: '人工回复',
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

const handoffReasonText = (value?: string | null) =>
  ({
    workbench_typing: '正在输入回复',
    manual_force_handoff: '人工主动接管',
    human_required: '需要人工处理',
    unsupported_message_type: '非文本消息需人工处理',
    advanced_customer_level: '高阶客户需人工处理',
    resolved_by_operator: '人工结束会话'
  })[value || ''] ||
  value ||
  '-'

</script>

<style scoped>
.supervision {
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: 100%;
  padding: 16px;
  overflow: auto;
}

.section {
  padding-bottom: 12px;
  border-bottom: 1px solid #e5e7eb;
}

.title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  font-weight: 600;
}

dl {
  display: grid;
  grid-template-columns: 88px 1fr;
  gap: 8px;
  margin: 0;
  font-size: 14px;
}

dt {
  color: #6b7280;
}

dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
  color: #111827;
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.muted {
  color: #9ca3af;
}

</style>
