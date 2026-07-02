<template>
  <aside class="supervision">
    <ElEmpty v-if="!conversationId || !conversation" description="请选择会话" />
    <template v-else>
      <div class="section">
        <div class="title">
          <span>监督面板</span>
          <ElTag :type="statusType(conversation.status)">{{ statusText(conversation.status) }}</ElTag>
        </div>
        <dl>
          <dt>客户</dt>
          <dd>{{ conversation.user_id }}</dd>
          <dt>渠道</dt>
          <dd>{{ conversation.channel }}</dd>
          <dt>会话</dt>
          <dd>{{ conversation.session_id || 'default' }}</dd>
          <dt>路由</dt>
          <dd>{{ conversation.last_route || '-' }}</dd>
          <dt>意图</dt>
          <dd>{{ conversation.last_intent || '-' }}</dd>
          <dt>接管人</dt>
          <dd>{{ conversation.owner_id || '-' }}</dd>
          <dt>转人工原因</dt>
          <dd>{{ conversation.handoff_reason || '-' }}</dd>
        </dl>
      </div>

      <div class="actions">
        <ElButton
          v-if="conversation.status === 'handoff_pending'"
          type="primary"
          @click="claim"
        >
          领取接管
        </ElButton>
        <ElButton
          v-if="conversation.status === 'ai_active' || conversation.status === 'ai_waiting'"
          type="warning"
          @click="force"
        >
          强制转人工
        </ElButton>
        <ElButton
          v-if="conversation.status === 'human_active'"
          @click="release"
        >
          交回 AI
        </ElButton>
        <ElButton
          v-if="conversation.status !== 'resolved'"
          type="danger"
          plain
          @click="resolve"
        >
          结束会话
        </ElButton>
      </div>

      <ReplyComposer
        :status="conversation.status"
        @claim="claim"
        @send="reply"
      />
    </template>
  </aside>
</template>

<script setup lang="ts">
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
import { useUserStore } from '@/store/modules/user'
import ReplyComposer from './ReplyComposer.vue'

const props = defineProps<{ conversationId: string; conversation?: ConversationItem }>()
const emit = defineEmits<{ changed: [] }>()

const userStore = useUserStore()
const operatorId = computed(() => userStore.user.nickname || 'admin')

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
  border-bottom: 1px solid #e5e7eb;
  padding-bottom: 12px;
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

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
</style>
