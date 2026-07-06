<template>
  <section class="message-panel">
    <div class="header">
      <div class="customer-title">
        <ElAvatar v-if="detail" :size="36" :src="detail.conversation.user_avatar_url || undefined">
          {{ avatarText(detail.conversation) }}
        </ElAvatar>
        <div>
          <h2>{{ detail ? displayName(detail.conversation) : '选择会话' }}</h2>
          <p v-if="detail">{{ detail.conversation.channel }} / {{ detail.conversation.session_id || 'default' }}</p>
        </div>
      </div>
      <ElButton :disabled="!conversationId" :icon="Refresh" circle @click="load()" />
    </div>

    <div ref="timelineRef" v-loading="loading" class="timeline">
      <ElEmpty v-if="!conversationId" description="请选择左侧会话" />
      <ElEmpty v-else-if="!detail?.messages.length && !loading" description="暂无消息" />
      <div
        v-for="message in detail?.messages || []"
        :key="message.id"
        class="message-row"
        :class="message.sender_type"
      >
        <div class="bubble">
          <div class="sender">{{ senderText(message.sender_type) }}</div>
          <div class="content">{{ message.content }}</div>
          <div class="time">{{ formatTime(message.created_at) }}</div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import {
  getConversationDetail,
  type ConversationDetail,
  type ConversationItem
} from '@/api/admin/conversations'

const props = defineProps<{ conversationId: string }>()
const emit = defineEmits<{ loaded: [conversation: ConversationItem | undefined] }>()

const loading = ref(false)
const detail = ref<ConversationDetail>()
const timelineRef = ref<HTMLElement>()
let reloadPending = false
let requesting = false

const load = async (options: { silent?: boolean } = {}) => {
  if (requesting) {
    reloadPending = true
    return
  }
  if (!props.conversationId) {
    detail.value = undefined
    emit('loaded', undefined)
    return
  }
  requesting = true
  const timeline = timelineRef.value
  const wasNearBottom = timeline
    ? timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 80
    : true
  if (!options.silent) {
    loading.value = true
  }
  try {
    detail.value = await getConversationDetail(props.conversationId)
    emit('loaded', detail.value.conversation)
    await nextTick()
    if (wasNearBottom && timelineRef.value) {
      timelineRef.value.scrollTop = timelineRef.value.scrollHeight
    }
  } finally {
    requesting = false
    if (!options.silent) {
      loading.value = false
    }
    if (reloadPending) {
      reloadPending = false
      void load({ silent: true })
    }
  }
}

const senderText = (value: string) =>
  ({ customer: '客户', ai: 'AI', human: '人工', system: '系统' })[value] || value

const displayName = (conversation: ConversationItem) =>
  conversation.user_display_name || conversation.user_id

const avatarText = (conversation: ConversationItem) =>
  displayName(conversation).slice(0, 1).toUpperCase()

const formatTime = (value: string) => new Date(value).toLocaleString()

watch(
  () => props.conversationId,
  () => load()
)
onMounted(load)

defineExpose({ load })
</script>

<style scoped>
.message-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid #e5e7eb;
}

.customer-title {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 10px;
}

h2,
p {
  margin: 0;
}

h2 {
  overflow: hidden;
  font-size: 16px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

p {
  margin-top: 4px;
  font-size: 12px;
  color: #6b7280;
}

.timeline {
  flex: 1;
  min-height: 0;
  padding: 18px;
  overflow: auto;
  background: #f9fafb;
}

.message-row {
  display: flex;
  margin-bottom: 14px;
}

.message-row.ai,
.message-row.human {
  justify-content: flex-end;
}

.bubble {
  max-width: min(70%, 680px);
  padding: 10px 12px;
  white-space: pre-wrap;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
}

.ai .bubble {
  background: #eef2ff;
  border-color: #c7d2fe;
}

.human .bubble {
  color: #fff;
  background: #2563eb;
  border-color: #2563eb;
}

.sender,
.time {
  font-size: 12px;
  opacity: 0.75;
}

.content {
  margin: 4px 0;
  line-height: 1.6;
}
</style>
