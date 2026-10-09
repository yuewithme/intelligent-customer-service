<template>
  <div class="composer">
    <template v-if="status !== 'resolved'">
      <div class="composer-main">
        <ElInput
          v-model="content"
          class="reply-input"
          type="textarea"
          :rows="4"
          :disabled="sending"
          resize="none"
          placeholder="输入回复，Enter 发送，Shift+Enter 换行"
          aria-label="回复客户"
          @keydown="handleKeydown"
          @compositionstart="composing = true"
          @compositionend="composing = false"
        />
        <div class="send-area">
          <span class="reply-status" :class="{ human: isHumanReplying }" :aria-busy="syncing">
            <i aria-hidden="true"></i>{{ isHumanReplying ? '人工回复' : 'AI回复' }}
          </span>
          <ElButton type="primary" :loading="sending" :disabled="!content.trim()" @click="sendText">发送</ElButton>
        </div>
      </div>
      <div class="composer-tools">
        <ElPopover placement="top-start" :width="'min(344px, calc(100vw - 24px))'" trigger="click">
          <template #reference><ElButton size="small" text :disabled="sending">表情</ElButton></template>
          <emoji-picker class="emoji-picker" locale="zh" @emoji-click="selectUnicodeEmoji" />
        </ElPopover>
        <ElButton v-if="!isTestGate()" size="small" text :disabled="sending" @click="imageInput?.click()">发送图片</ElButton>
        <ElButton v-if="!isTestGate()" size="small" text @click="openCurrentActivities">目前活动</ElButton>
        <span class="composer-hint">输入时人工回复，清空或停笔 1 分钟后 AI 回复</span>
        <ElButton v-if="syncError" size="small" text type="danger" @click="syncReception().catch(() => {})">状态同步失败，重试</ElButton>
        <input ref="imageInput" class="file-input" type="file" accept=".jpg,.jpeg,.png,.gif,.webp" @change="selectImage" />
      </div>
      <div v-if="receivedEmojis.length" class="received-emojis">
        <span>客户表情</span>
        <button v-for="item in receivedEmojis" :key="item.message_id" type="button" :disabled="sending" title="发送同款表情" @click="sendEmoji(item.message_id)">
          <img v-if="item.url" :src="item.url" alt="客户表情" /><span v-else>表情</span>
        </button>
      </div>
    </template>
    <p v-else class="composer-hint">会话已结束，仅可查看</p>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import 'emoji-picker-element'
import {
  getConversationEmojis, replyConversation, replyConversationEmoji,
  replyConversationImage, updateReplyDraft,
  type ConversationEmoji, type ConversationItem
} from '@/api/admin/conversations'
import { useUserStore } from '@/store/modules/user'
import { isTestGate } from '@/utils/gate'

const props = defineProps<{ status: string; conversationId: string }>()
const emit = defineEmits<{
  changed: []
  receptionChanged: [conversation: ConversationItem]
}>()
const userStore = useUserStore()
const router = useRouter()
const operatorId = userStore.user.nickname || 'admin'
const editorId = crypto.randomUUID()
const conversationId = props.conversationId
const content = ref('')
const sending = ref(false)
const syncing = ref(false)
const syncError = ref(false)
const composing = ref(false)
const confirmedStatus = ref(props.status)
const imageInput = ref<HTMLInputElement>()
const receivedEmojis = ref<ConversationEmoji[]>([])
const isHumanReplying = computed(() => ['human_active', 'handoff_pending'].includes(confirmedStatus.value))
let lastEditedAt = 0
let editTimer: ReturnType<typeof setTimeout> | undefined
let expiryTimer: ReturnType<typeof setTimeout> | undefined
let syncQueue: Promise<void> = Promise.resolve()
let syncVersion = 0
let disposed = false

watch(() => props.status, value => { confirmedStatus.value = value })

const syncReception = () => {
  const version = ++syncVersion
  const operation = syncQueue.catch(() => {}).then(async () => {
    if (disposed || version !== syncVersion || props.status === 'resolved') return
    const remaining = 60 - (Date.now() - lastEditedAt) / 1000
    const active = sending.value || (Boolean(content.value.trim()) && remaining > 0)
    syncing.value = true
    try {
      const result = await updateReplyDraft(conversationId, {
        operator_id: operatorId, editor_id: editorId, active,
        remaining_seconds: sending.value ? 60 : Math.max(0.01, Math.min(60, remaining))
      })
      if (!disposed) {
        confirmedStatus.value = result.status
        syncError.value = false
        emit('receptionChanged', result)
      }
    } catch (error) {
      if (!disposed) syncError.value = true
      throw error
    } finally {
      if (!disposed) syncing.value = false
    }
  })
  syncQueue = operation
  return operation
}

watch(content, (value, previous) => {
  lastEditedAt = Date.now()
  clearTimeout(editTimer)
  clearTimeout(expiryTimer)
  const sync = () => { void syncReception().catch(() => {}) }
  if (!previous.trim() || !value.trim()) sync()
  else editTimer = setTimeout(sync, 300)
  if (value.trim()) expiryTimer = setTimeout(sync, 60_000)
}, { flush: 'sync' })

const send = async (action: () => Promise<unknown>, clearText = false) => {
  if (sending.value || props.status === 'resolved') return
  sending.value = true
  clearTimeout(editTimer)
  try {
    await syncReception()
    await action()
    if (clearText) content.value = ''
    if (!disposed) {
      ElMessage.success('已发送')
      emit('changed')
    }
  } catch {
    // Preserve the draft when reception sync or sending fails.
  } finally {
    sending.value = false
    if (!disposed) void syncReception().catch(() => {})
  }
}

const sendText = () => {
  const text = content.value.trim()
  if (text) void send(() => replyConversation(conversationId, operatorId, text), true)
}

const handleKeydown = (rawEvent: Event | KeyboardEvent) => {
  const event = rawEvent as KeyboardEvent
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || composing.value || event.keyCode === 229) return
  event.preventDefault()
  if (!event.repeat) sendText()
}

const selectUnicodeEmoji = (event: Event) => {
  if (!sending.value) content.value += (event as CustomEvent<{ unicode?: string }>).detail?.unicode || ''
}
const selectImage = (event: Event) => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) void send(() => replyConversationImage(conversationId, operatorId, file))
  input.value = ''
}
const sendEmoji = (messageId: number) => void send(() => replyConversationEmoji(conversationId, operatorId, messageId))
const openCurrentActivities = () => void router.push({ name: 'CurrentActivities', query: { conversation_id: conversationId } })

onMounted(() => {
  void syncReception().catch(() => {})
  if (!isTestGate()) void getConversationEmojis(conversationId).then(result => {
    if (!disposed) receivedEmojis.value = result.items
  }).catch(() => {})
})

onBeforeUnmount(() => {
  disposed = true
  ++syncVersion
  clearTimeout(editTimer)
  clearTimeout(expiryTimer)
  void syncQueue.catch(() => {}).then(() => updateReplyDraft(conversationId, {
    operator_id: operatorId, editor_id: editorId, active: false
  })).catch(() => {})
})
</script>

<style scoped>
.composer { flex: 0 0 auto; padding: 12px 14px 8px; border-top: 1px solid var(--app-border); background: #fff; }
.composer-main { display: flex; align-items: stretch; gap: 12px; }
.reply-input { flex: 1; min-width: 0; }
.reply-input :deep(.el-textarea__inner) { min-height: 112px; padding: 10px 12px; line-height: 1.6; font-size: 14px; }
.send-area { display: flex; flex: 0 0 92px; flex-direction: column; align-items: stretch; justify-content: flex-end; gap: 12px; }
.reply-status { display: flex; align-items: center; justify-content: center; gap: 6px; padding: 7px 4px; border-radius: 6px; background: #edf5f1; color: var(--app-accent); font-size: 13px; white-space: nowrap; }
.reply-status.human { background: #fff4e4; color: #a56616; }
.reply-status i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.send-area .el-button { margin: 0; min-height: 36px; }
.composer-tools { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
.composer-tools :deep(.el-button + .el-button) { margin-left: 0; }
.composer-hint { color: var(--app-text-secondary); font-size: 12px; }
.composer-tools .composer-hint { margin-left: auto; }
.file-input { display: none; }
.emoji-picker { width: 320px; max-width: 100%; height: 320px; }
.received-emojis { display: flex; align-items: center; gap: 6px; overflow-x: auto; margin-top: 4px; font-size: 12px; color: var(--app-text-secondary); }
.received-emojis > span { flex: 0 0 auto; }
.received-emojis button { flex: 0 0 30px; width: 30px; height: 30px; padding: 2px; border: 1px solid var(--app-border); border-radius: 5px; background: #fff; cursor: pointer; }
.received-emojis img { width: 100%; height: 100%; object-fit: contain; }
@media (max-width: 820px), (hover: none) and (pointer: coarse) {
  .composer { padding: 8px; }
  .composer-main { gap: 8px; }
  .send-area { flex-basis: 76px; gap: 8px; }
  .reply-input :deep(.el-textarea__inner) { min-height: 88px; }
  .composer-tools .composer-hint { flex-basis: 100%; margin-left: 0; font-size: 11px; }
  .emoji-picker { height: min(320px, 50dvh); }
}
</style>
