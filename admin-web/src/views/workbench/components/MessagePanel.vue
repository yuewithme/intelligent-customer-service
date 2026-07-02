<template>
  <section class="message-panel">
    <div class="header">
      <div>
        <h2>{{ detail?.conversation.user_id || '选择会话' }}</h2>
        <p v-if="detail">{{ detail.conversation.channel }} / {{ detail.conversation.session_id || 'default' }}</p>
      </div>
      <ElButton :disabled="!conversationId" :icon="Refresh" circle @click="load" />
    </div>

    <div v-loading="loading" class="timeline">
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
import { onMounted, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import {
  getConversationDetail,
  type ConversationDetail,
  type ConversationItem
} from '@/api/admin/conversations'

const props = defineProps<{ conversationId: string; refreshKey: number }>()
const emit = defineEmits<{ loaded: [conversation: ConversationItem | undefined] }>()

const loading = ref(false)
const detail = ref<ConversationDetail>()

const load = async () => {
  if (!props.conversationId) {
    detail.value = undefined
    emit('loaded', undefined)
    return
  }
  loading.value = true
  try {
    detail.value = await getConversationDetail(props.conversationId)
    emit('loaded', detail.value.conversation)
  } finally {
    loading.value = false
  }
}

const senderText = (value: string) =>
  ({ customer: '客户', ai: 'AI', human: '人工', system: '系统' })[value] || value

const formatTime = (value: string) => new Date(value).toLocaleString()

watch(() => props.conversationId, load)
watch(() => props.refreshKey, load)
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

h2,
p {
  margin: 0;
}

h2 {
  font-size: 16px;
  font-weight: 600;
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
