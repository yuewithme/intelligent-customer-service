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
          <dd class="handoff-actions">
            <span>{{ handoffReasonText(conversation.handoff_reason) }}</span>
            <ElButton
              v-if="!isTestGate() && conversation.status !== 'resolved'"
              type="primary"
              size="small"
              :loading="switching"
              :disabled="otherOperator"
              :title="otherOperator ? '其他客服正在接管' : undefined"
              @click="toggleReception"
            >{{ manuallyHeld ? '转AI' : '转人工' }}</ElButton>
          </dd>
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
import { ElMessage } from 'element-plus'
import { claimConversation, releaseToAi, type ConversationMessage, type ConversationItem, type ConversationStatus } from '@/api/admin/conversations'
import { getTagCatalog } from '@/api/admin/tags'
import type { UserProfile } from '@/api/user-profile'
import { tagValueText } from '@/utils/tagDisplay'
import { isTestGate } from '@/utils/gate'
import { useUserStore } from '@/store/modules/user'
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
const emit = defineEmits<{ changed: [conversation?: ConversationItem] }>()
const userStore = useUserStore()
const operatorId = computed(() => userStore.user.username || userStore.user.nickname || 'admin')
const switching = ref(false)
const manuallyHeld = computed(() => props.conversation?.status === 'human_active' && props.conversation.handoff_reason !== 'workbench_typing')
const otherOperator = computed(() => props.conversation?.status === 'human_active' && props.conversation.owner_id !== operatorId.value)

const toggleReception = async () => {
  if (switching.value || otherOperator.value || isTestGate() || !props.conversation || props.conversation.status === 'resolved') return
  const conversationId = props.conversationId
  const release = manuallyHeld.value
  switching.value = true
  try {
    const updated = await (release ? releaseToAi : claimConversation)(conversationId, operatorId.value)
    emit('changed', updated)
    ElMessage.success(release ? '已交还 AI 自动回复' : '已转人工，点击“转AI”可交还 AI')
  } catch {
    emit('changed')
  } finally {
    switching.value = false
  }
}

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
    customer_requested_human: '客户主动要求转人工',
    service_handoff_invitation_accepted: '客户同意由业务主管接手',
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

.handoff-actions { display: flex; align-items: center; gap: 12px; }
.handoff-actions > span { flex: 1; min-width: 0; }
.handoff-actions > .el-button { flex-shrink: 0; }

.muted {
  color: #9ca3af;
}

</style>
