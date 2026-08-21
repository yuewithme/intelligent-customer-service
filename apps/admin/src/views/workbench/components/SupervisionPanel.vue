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
          <dt>运行方式</dt>
          <dd>{{ routeText(conversation.last_route) }}</dd>
          <dt>客户信号</dt>
          <dd>{{ customerSignalText(agentRelationship?.customer_signal) }}</dd>
          <dt>接管人</dt>
          <dd>{{ conversation.owner_id || '-' }}</dd>
          <dt>转人工原因</dt>
          <dd>{{ handoffReasonText(conversation.handoff_reason) }}</dd>
          <dt>标签</dt>
          <dd>
            <div v-if="profileLoading" class="muted">更新中...</div>
            <div v-else-if="tags.length" class="tag-list">
              <ElTag v-for="tag in tags" :key="tag" size="small" effect="plain">
                {{ tagValueText(tag) }}
              </ElTag>
            </div>
            <span v-else>-</span>
          </dd>
        </dl>
      </div>

      <div v-if="agentRelationship?.commercial_judgment || agentRelationship?.relationship_purpose" class="agent-section">
        <div class="title"><span>小兰本轮判断</span></div>
        <dl class="profile-detail">
          <dt>商业判断</dt>
          <dd class="profile-long-text">{{ agentRelationship.commercial_judgment || '-' }}</dd>
          <dt>关系目的</dt>
          <dd class="profile-long-text">{{ agentRelationship.relationship_purpose || '-' }}</dd>
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
        <ElButton
          v-if="conversation.status === 'human_active'"
          type="primary"
          plain
          @click="openCurrentActivities"
        >
          目前活动
        </ElButton>
        <ElButton v-if="conversation.status !== 'resolved'" type="danger" plain @click="resolve">
          结束会话
        </ElButton>
      </div>

      <ReplyComposer
        :status="conversation.status"
        :conversation-id="conversationId"
        @claim="claim"
        @send="reply"
        @send-image="replyImage"
        @send-emoji="replyEmoji"
      />

      <YouzanOrderPanel :conversation-id="conversationId" />

      <div class="profile-section">
        <div class="title">
          <span>用户画像</span>
          <ElTag v-if="profile?.updated_at" size="small" type="info" effect="plain"> 实时 </ElTag>
        </div>
        <ElSkeleton v-if="profileLoading" :rows="4" animated />
        <ElEmpty v-else-if="!hasProfileDetail" description="暂无画像" :image-size="72" />
        <dl v-else class="profile-detail">
          <dt>风险等级</dt>
          <dd>{{ riskLevelText(profile?.risk_level) }}</dd>
          <dt>产品兴趣</dt>
          <dd>{{ productInterestText }}</dd>
          <dt>痛点</dt>
          <dd class="profile-long-text">{{ painPointText }}</dd>
          <dt>AI 摘要</dt>
          <dd class="profile-long-text">{{ profileMemory || '-' }}</dd>
          <dt>画像更新时间</dt>
          <dd>{{ updatedAtText }}</dd>
        </dl>
      </div>
    </template>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  claimConversation,
  forceHandoff,
  releaseToAi,
  replyConversation,
  replyConversationEmoji,
  replyConversationImage,
  resolveConversation,
  type AgentRelationshipState,
  type ConversationItem,
  type ConversationStatus
} from '@/api/admin/conversations'
import type { UserProfile } from '@/api/user-profile'
import { useUserStore } from '@/store/modules/user'
import { riskLevelText, tagValueText } from '@/utils/tagDisplay'
import { formatChinaTime } from '../time'
import ReplyComposer from './ReplyComposer.vue'
import YouzanOrderPanel from './YouzanOrderPanel.vue'

const props = defineProps<{
  conversationId: string
  conversation?: ConversationItem
  agentRelationship?: AgentRelationshipState
  profile?: UserProfile
  profileLoading?: boolean
}>()
const emit = defineEmits<{ changed: [] }>()

const userStore = useUserStore()
const router = useRouter()
const operatorId = computed(() => userStore.user.nickname || 'admin')
const tags = computed(() => props.profile?.customer_tags?.filter(Boolean) || [])
const profileMemory = computed(() => props.profile?.ai_summary?.trim() || '')
const productInterestText = computed(() => joinProfileList(props.profile?.product_interests))
const painPointText = computed(() => joinProfileList(props.profile?.pain_points))
const updatedAtText = computed(() =>
  props.profile?.updated_at ? formatChinaTime(props.profile.updated_at) : '-'
)
const hasProfileDetail = computed(
  () =>
    Boolean(props.profile) &&
    Boolean(
      props.profile?.risk_level ||
        productInterestText.value !== '-' ||
        painPointText.value !== '-' ||
        profileMemory.value ||
        props.profile?.updated_at
    )
)

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

const replyImage = async (file: File) => {
  await replyConversationImage(props.conversationId, operatorId.value, file)
  ElMessage.success('图片已进入发送队列')
  emit('changed')
}

const replyEmoji = async (sourceMessageId: number) => {
  await replyConversationEmoji(
    props.conversationId,
    operatorId.value,
    sourceMessageId
  )
  ElMessage.success('表情已进入发送队列')
  emit('changed')
}

const openCurrentActivities = () => {
  void router.push({
    name: 'CurrentActivities',
    query: { conversation_id: props.conversationId }
  })
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
    agent: '小兰自主 Agent',
    agent_first_contact: '小兰主动开场',
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

const customerSignalText = (value?: AgentRelationshipState['customer_signal']) =>
  ({
    none: '正常沟通',
    soft_refusal: '软拒绝／需要降压',
    explicit_refusal: '明确拒绝／降低频率'
  })[value || 'none']

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

const displayName = (conversation: ConversationItem) =>
  conversation.user_display_name || conversation.user_id

const avatarText = (conversation: ConversationItem) =>
  displayName(conversation).slice(0, 1).toUpperCase()

const joinProfileList = (values?: string[] | null) => values?.filter(Boolean).join('、') || '-'
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

.profile-section,
.agent-section {
  padding-top: 4px;
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.profile-memory {
  font-size: 13px;
  line-height: 1.7;
  color: #111827;
  white-space: pre-wrap;
}

.profile-detail {
  grid-template-columns: 88px 1fr;
}

.profile-long-text {
  line-height: 1.7;
  white-space: pre-wrap;
}

.muted {
  color: #9ca3af;
}
</style>
