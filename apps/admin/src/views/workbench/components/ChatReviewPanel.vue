<template>
  <section class="chat-review" aria-live="polite" :aria-busy="loading">
    <div class="review-heading">
      <strong>当前回复复盘</strong>
      <span class="review-hint">最近 {{ review?.round_count || '最多5' }} 轮</span>
    </div>
    <p v-if="loading" class="review-hint">正在分析当前回复并生成建议…</p>
    <template v-else-if="review?.status === 'ready'">
      <details v-if="review.reviewed_reply" class="review-source">
        <summary>正在复盘的回复</summary>
        <p>{{ review.reviewed_reply }}</p>
      </details>
      <p v-else class="review-hint">尚无已发送的客服回复，先根据客户诉求提供建议。</p>
      <p class="review-analysis">{{ review.analysis }}</p>
      <div class="suggestion-heading">建议回复 <span class="review-hint">三选一，按需使用</span></div>
      <ol class="reply-suggestions">
        <li v-for="(suggestion, index) in review.suggestions" :key="index">{{ suggestion }}</li>
      </ol>
      <p class="review-hint update-hint">随新消息自动更新</p>
    </template>
    <p v-else-if="review?.status === 'empty'" class="review-hint">{{ review.analysis }}</p>
    <div v-else class="review-error">
      <span>复盘暂时未生成</span>
      <ElButton size="small" text type="primary" @click="scheduleReview">重试</ElButton>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { getChatReview, type ChatReview, type ConversationMessage } from '@/api/admin/conversations'
import type { UserProfile } from '@/api/user-profile'

const props = defineProps<{
  conversationId: string
  messages: ConversationMessage[]
  profile?: UserProfile
}>()
const review = ref<ChatReview>()
const loading = ref(false)
let requestVersion = 0
let debounceTimer: ReturnType<typeof setTimeout> | undefined

const snapshot = computed(() => JSON.stringify([
  props.conversationId,
  props.messages.filter(message => message.conversation_id === props.conversationId)
    .map(message => [message.id, message.sender_type, message.content, message.delivery_status,
      (message.metadata.media as { recognition?: unknown } | undefined)?.recognition]),
  props.profile
]))

const scheduleReview = () => {
  const version = ++requestVersion
  const conversationId = props.conversationId
  clearTimeout(debounceTimer)
  review.value = undefined
  loading.value = Boolean(conversationId)
  if (!conversationId) return
  debounceTimer = setTimeout(async () => {
    try {
      const result = await getChatReview(conversationId)
      if (version === requestVersion) review.value = result
    } catch {
      // The inline retry state stays tied to this conversation and snapshot.
    } finally {
      if (version === requestVersion) loading.value = false
    }
  }, 400)
}

watch(snapshot, scheduleReview, { immediate: true })
onBeforeUnmount(() => {
  ++requestVersion
  clearTimeout(debounceTimer)
})
</script>

<style scoped>
.chat-review { min-width: 0; font-size: 14px; }
.review-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.review-hint { color: var(--app-text-secondary, #6b7280); font-size: 12px; font-weight: 400; }
.review-source { margin-top: 12px; padding: 10px; border-radius: 8px; background: #f4f7f5; }
.review-source summary { cursor: pointer; color: var(--app-text-secondary, #6b7280); font-size: 12px; }
.review-source p { margin-bottom: 0; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.6; }
.review-analysis { margin: 12px 0 16px; line-height: 1.7; overflow-wrap: anywhere; }
.suggestion-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-weight: 600; }
.reply-suggestions { display: grid; gap: 8px; margin: 10px 0 0; padding-left: 24px; }
.reply-suggestions li { padding: 10px; border: 1px solid var(--app-border, #dbe5df); border-radius: 8px; line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere; }
.reply-suggestions li::marker { color: var(--app-accent, #21765d); font-weight: 600; }
.update-hint { margin: 10px 0 0; }
.review-error { display: flex; align-items: center; justify-content: space-between; margin-top: 12px; color: var(--app-text-secondary, #6b7280); }
</style>
