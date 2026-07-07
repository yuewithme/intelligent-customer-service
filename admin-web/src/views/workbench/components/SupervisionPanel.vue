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
          <dt>客户</dt>
          <dd class="customer-cell">
            <ElAvatar :size="28" :src="conversation.user_avatar_url || undefined">
              {{ avatarText(conversation) }}
            </ElAvatar>
            <span>{{ displayName(conversation) }}</span>
          </dd>
          <dt>渠道</dt>
          <dd>{{ conversation.channel }}</dd>
          <dt>会话</dt>
          <dd>{{ sessionText(conversation) }}</dd>
          <dt>路由</dt>
          <dd>{{ routeText(conversation.last_route) }}</dd>
          <dt>意图</dt>
          <dd>{{ intentText(conversation.last_intent) }}</dd>
          <dt>接管人</dt>
          <dd>{{ conversation.owner_id || '-' }}</dd>
          <dt>转人工原因</dt>
          <dd>{{ handoffReasonText(conversation.handoff_reason) }}</dd>
          <dt>标签</dt>
          <dd>
            <div v-if="profileLoading" class="muted">更新中...</div>
            <div v-else-if="tags.length" class="tag-list">
              <ElTag v-for="tag in tags" :key="tag" size="small" effect="plain">
                {{ tag }}
              </ElTag>
            </div>
            <span v-else>-</span>
          </dd>
        </dl>
      </div>

      <div class="actions">
        <ElButton v-if="conversation.status === 'handoff_pending'" type="primary" @click="claim">
          领取接管
        </ElButton>
        <ElButton
          v-if="conversation.status === 'ai_active' || conversation.status === 'ai_waiting'"
          type="warning"
          @click="force"
        >
          强制转人工
        </ElButton>
        <ElButton v-if="conversation.status === 'human_active'" @click="release">交回 AI</ElButton>
        <ElButton v-if="conversation.status !== 'resolved'" type="danger" plain @click="resolve">
          结束会话
        </ElButton>
      </div>

      <ReplyComposer :status="conversation.status" @claim="claim" @send="reply" />

      <div class="profile-section">
        <div class="title">
          <span>用户画像</span>
          <ElTag v-if="profile?.updated_at" size="small" type="info" effect="plain"> 实时 </ElTag>
        </div>
        <ElSkeleton v-if="profileLoading" :rows="4" animated />
        <ElEmpty v-else-if="!profile" description="暂无画像" :image-size="72" />
        <dl v-else class="profile-grid">
          <dt>当前阶段</dt>
          <dd>{{ emptyText(profile.current_stage) }}</dd>
          <dt>风险等级</dt>
          <dd>{{ riskText(profile.risk_level) }}</dd>
          <dt>产品兴趣</dt>
          <dd>
            <div v-if="profile.product_interests?.length" class="tag-list">
              <ElTag
                v-for="item in profile.product_interests"
                :key="item"
                size="small"
                type="success"
                effect="plain"
              >
                {{ item }}
              </ElTag>
            </div>
            <span v-else>-</span>
          </dd>
          <dt>痛点</dt>
          <dd>
            <div v-if="profile.pain_points?.length" class="stack-list">
              <span v-for="item in profile.pain_points" :key="item">{{ item }}</span>
            </div>
            <span v-else>-</span>
          </dd>
          <dt>偏好摘要</dt>
          <dd>{{ emptyText(profile.preference_summary) }}</dd>
          <dt>AI 摘要</dt>
          <dd>{{ emptyText(profile.ai_summary) }}</dd>
          <dt>最近意图</dt>
          <dd>{{ intentText(profile.last_intent) }}</dd>
          <dt>最近路由</dt>
          <dd>{{ routeText(profile.last_route) }}</dd>
          <dt>最近模板</dt>
          <dd>{{ emptyText(profile.last_template_id) }}</dd>
          <dt>转人工状态</dt>
          <dd>{{ handoffStatusText(profile.human_handoff_status) }}</dd>
          <dt>画像更新时间</dt>
          <dd>{{ formatTime(profile.updated_at) }}</dd>
        </dl>
      </div>
    </template>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  claimConversation,
  forceHandoff,
  releaseToAi,
  replyConversation,
  resolveConversation,
  type ConversationItem,
  type ConversationStatus
} from '@/api/admin/conversations'
import type { UserProfile } from '@/api/user-profile'
import { useUserStore } from '@/store/modules/user'
import ReplyComposer from './ReplyComposer.vue'

const props = defineProps<{
  conversationId: string
  conversation?: ConversationItem
  profile?: UserProfile
  profileLoading?: boolean
}>()
const emit = defineEmits<{ changed: [] }>()

const userStore = useUserStore()
const operatorId = computed(() => userStore.user.nickname || 'admin')
const tags = computed(() => props.profile?.customer_tags?.filter(Boolean) || [])

const claim = async () => {
  await claimConversation(props.conversationId, operatorId.value)
  ElMessage.success('已领取接管')
  emit('changed')
}

const reply = async (content: string) => {
  await replyConversation(props.conversationId, operatorId.value, content)
  ElMessage.success('已发送人工回复')
  emit('changed')
}

const force = async () => {
  await forceHandoff(props.conversationId, operatorId.value, 'manual_force_handoff')
  ElMessage.success('已转为等待人工接管')
  emit('changed')
}

const release = async () => {
  await releaseToAi(props.conversationId, operatorId.value)
  ElMessage.success('已交回 AI')
  emit('changed')
}

const resolve = async () => {
  await ElMessageBox.confirm('结束后会话将只读查看，确认结束？', '结束会话', {
    type: 'warning'
  })
  await resolveConversation(props.conversationId, operatorId.value, 'resolved_by_operator')
  ElMessage.success('会话已结束')
  emit('changed')
}

const statusText = (value: ConversationStatus) =>
  ({
    ai_active: 'AI 自动回复',
    ai_waiting: 'AI 等待中',
    handoff_pending: '等待接管',
    human_active: '人工接管',
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

const sessionText = (conversation: ConversationItem) =>
  conversation.channel === 'wechat' && conversation.session_id === 'default'
    ? '私聊'
    : conversation.session_id || '-'

const routeText = (value?: string | null) =>
  ({
    unsupported: '未匹配',
    inbound_text: '私聊消息',
    non_text: '非文本消息',
    rag_answer: '知识库回答',
    template_reply: '话术回答',
    template_then_rag: '话术后知识库',
    clarify: '追问澄清',
    human: '人工处理'
  })[value || ''] ||
  value ||
  '-'

const intentText = (value?: string | null) =>
  ({
    unsupported: '未匹配',
    unknown: '待识别',
    message: '普通消息',
    care_question: '养护问题'
  })[value || ''] ||
  value ||
  '-'

const handoffReasonText = (value?: string | null) =>
  ({
    manual_force_handoff: '人工主动接管',
    human_required: '需要人工处理',
    unsupported_message_type: '非文本消息需人工处理',
    advanced_customer_level: '高阶客户需人工处理',
    resolved_by_operator: '人工结束会话'
  })[value || ''] ||
  value ||
  '-'

const handoffStatusText = (value?: string | null) =>
  ({
    pending: '等待接管',
    active: '人工接管中',
    resolved: '已结束'
  })[value || ''] ||
  value ||
  '-'

const riskText = (value?: string | null) =>
  ({
    normal: '正常',
    medium: '中风险',
    high: '高风险'
  })[value || ''] ||
  value ||
  '-'

const displayName = (conversation: ConversationItem) =>
  conversation.user_display_name || conversation.user_id

const avatarText = (conversation: ConversationItem) =>
  displayName(conversation).slice(0, 1).toUpperCase()

const emptyText = (value?: string | null) => value || '-'

const formatTime = (value?: string | null) => {
  if (!value) {
    return '-'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString('zh-CN', { hour12: false })
}
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
  font-size: 13px;
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

.customer-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.customer-cell span {
  min-width: 0;
  overflow-wrap: anywhere;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.profile-section {
  padding-top: 4px;
}

.profile-grid {
  grid-template-columns: 88px minmax(0, 1fr);
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.stack-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.muted {
  color: #9ca3af;
}
</style>
