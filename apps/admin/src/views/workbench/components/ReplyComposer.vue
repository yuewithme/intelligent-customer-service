<template>
  <div class="composer">
    <template v-if="status !== 'resolved'">
      <div v-if="quotedMessage" class="quote-draft">
        <div><strong>引用 · {{ quotedMessage.sender_type === 'customer' ? '客户' : '客服' }}</strong><span>{{ quotedMessage.content }}</span><small>引用内容将随回复文字发送</small></div>
        <ElButton text :disabled="sending" aria-label="取消引用" @click="emit('clearQuote')">×</ElButton>
      </div>
      <div class="composer-main">
        <ElInput
          ref="replyInput"
          v-model="content"
          class="reply-input"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 6 }"
          :disabled="sending"
          resize="none"
          placeholder="输入或粘贴文字、图片，Enter 发送，Shift+Enter 换行"
          aria-label="回复客户"
          @keydown="handleKeydown"
          @paste="handlePaste"
          @contextmenu.stop
          @compositionstart="composing = true"
          @compositionend="composing = false"
        />
        <div class="send-area">
          <span class="reply-status" :class="{ human: isHumanReplying }" :aria-busy="syncing">
            <i aria-hidden="true"></i>{{ isHumanReplying ? '人工回复' : 'AI回复' }}
          </span>
          <ElButton type="primary" :loading="sending" :disabled="!hasDraft" @click="sendReply">发送</ElButton>
        </div>
      </div>
      <div v-if="pendingImages.length" class="image-drafts" aria-label="待发送图片">
        <div v-for="item in pendingImages" :key="item.url" class="image-draft">
          <img :src="item.url" alt="待发送图片预览" />
          <button type="button" :disabled="sending" aria-label="移除图片" @click="removeImage(item.url)">×</button>
        </div>
      </div>
      <div class="composer-tools">
        <ElPopover placement="top-start" :width="'min(344px, calc(100vw - 24px))'" trigger="click">
          <template #reference><ElButton size="small" text :disabled="sending">表情</ElButton></template>
          <emoji-picker class="emoji-picker" locale="zh" @emoji-click="selectUnicodeEmoji" />
        </ElPopover>
        <ElButton v-if="!isTestGate()" size="small" text :disabled="sending" @click="imageInput?.click()">发送图片</ElButton>
        <ElButton v-if="!isTestGate()" size="small" text @click="openCurrentActivities">目前活动</ElButton>
        <span class="composer-hint">{{ manuallyHeld ? '已手动接管，点击右侧“转AI”交还 AI' : '输入时人工回复，清空或停笔 1 分钟后 AI 回复' }}</span>
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
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import 'emoji-picker-element'
import {
  getConversationEmojis, replyConversation, replyConversationEmoji,
  replyConversationImage, updateReplyDraft,
  type ConversationEmoji, type ConversationItem, type ConversationMessage
} from '@/api/admin/conversations'
import { useUserStore } from '@/store/modules/user'
import { isTestGate } from '@/utils/gate'

const props = defineProps<{ status: string; conversationId: string; handoffReason?: string | null; quotedMessage?: ConversationMessage }>()
const emit = defineEmits<{
  changed: []
  receptionChanged: [conversation: ConversationItem]
  clearQuote: []
}>()
const userStore = useUserStore()
const router = useRouter()
const operatorId = userStore.user.nickname || 'admin'
const editorId = crypto.randomUUID()
const conversationId = props.conversationId
const content = ref('')
const replyInput = ref<{ focus: () => void }>()
watch(() => props.quotedMessage, async message => { if (message) { await nextTick(); replyInput.value?.focus() } })
const pendingImages = ref<{ file: File; url: string }[]>([])
const hasDraft = computed(() => Boolean(content.value.trim()) || pendingImages.value.length > 0)
const sending = ref(false)
const syncing = ref(false)
const syncError = ref(false)
const composing = ref(false)
const confirmedStatus = ref(props.status)
const imageInput = ref<HTMLInputElement>()
const receivedEmojis = ref<ConversationEmoji[]>([])
const isHumanReplying = computed(() => ['human_active', 'handoff_pending'].includes(confirmedStatus.value))
const manuallyHeld = computed(() => confirmedStatus.value === 'human_active' && props.handoffReason !== 'workbench_typing')
let lastEditedAt = 0
let editTimer: ReturnType<typeof setTimeout> | undefined
let expiryTimer: ReturnType<typeof setTimeout> | undefined
let syncQueue: Promise<void> = Promise.resolve()
let syncVersion = 0
let disposed = false

watch(() => [props.status, props.handoffReason] as const, ([value, reason], [previous]) => {
  confirmedStatus.value = value
  if ((previous === 'human_active' && ['ai_active', 'ai_waiting'].includes(value)) ||
      (value === 'human_active' && reason !== 'workbench_typing')) {
    ++syncVersion
    lastEditedAt = 0
    clearTimeout(editTimer)
    clearTimeout(expiryTimer)
  }
})

const syncReception = () => {
  const version = ++syncVersion
  const operation = syncQueue.catch(() => {}).then(async () => {
    if (disposed || version !== syncVersion || props.status === 'resolved') return
    const remaining = 60 - (Date.now() - lastEditedAt) / 1000
    const active = sending.value || (hasDraft.value && remaining > 0)
    syncing.value = true
    try {
      const result = await updateReplyDraft(conversationId, {
        operator_id: operatorId, editor_id: editorId, active,
        remaining_seconds: sending.value ? 60 : Math.max(0.01, Math.min(60, remaining))
      })
      if (!disposed && version === syncVersion) {
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

watch([content, () => pendingImages.value.length], ([value, count], [previous, previousCount]) => {
  lastEditedAt = Date.now()
  clearTimeout(editTimer)
  clearTimeout(expiryTimer)
  const sync = () => { void syncReception().catch(() => {}) }
  if ((!previous.trim() && !previousCount) || (!value.trim() && !count)) sync()
  else editTimer = setTimeout(sync, 300)
  if (value.trim() || count) expiryTimer = setTimeout(sync, 60_000)
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

const sendReply = () => {
  if (!hasDraft.value) return
  const text = content.value.trim()
  if (props.quotedMessage && !text) {
    ElMessage.warning('请填写引用回复的文字')
    return
  }
  const sourceMessageId = props.quotedMessage?.id
  const images = [...pendingImages.value]
  void send(async () => {
    for (const item of images) {
      await replyConversationImage(conversationId, operatorId, item.file)
      removeImage(item.url)
      if (!disposed) emit('changed')
    }
    if (text) await replyConversation(conversationId, operatorId, text, sourceMessageId)
    emit('clearQuote')
  }, true)
}

const handleKeydown = (rawEvent: Event | KeyboardEvent) => {
  const event = rawEvent as KeyboardEvent
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || composing.value || event.keyCode === 229) return
  event.preventDefault()
  if (!event.repeat) sendReply()
}

const handlePaste = (event: ClipboardEvent) => {
  if (event.clipboardData?.getData('text/plain')) return
  const files = Array.from(event.clipboardData?.items || [])
    .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
    .map(item => item.getAsFile())
    .filter((file): file is File => Boolean(file))
  if (!files.length) return
  event.preventDefault()
  if (sending.value || props.status === 'resolved') return
  if (isTestGate()) {
    ElMessage.warning('测试身份暂不支持发送图片')
    return
  }
  for (const file of files) {
    const extension = ({ 'image/jpeg': 'jpg', 'image/png': 'png', 'image/gif': 'gif', 'image/webp': 'webp' } as Record<string, string>)[file.type]
    if (!extension) {
      ElMessage.warning('仅支持 JPG、PNG、GIF、WebP 图片')
      continue
    }
    const image = new File([file], `pasted-image.${extension}`, { type: file.type })
    pendingImages.value.push({ file: image, url: URL.createObjectURL(image) })
  }
}

const removeImage = (url: string) => {
  pendingImages.value = pendingImages.value.filter(item => item.url !== url)
  URL.revokeObjectURL(url)
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
  pendingImages.value.forEach(item => URL.revokeObjectURL(item.url))
  void syncQueue.catch(() => {}).then(() => updateReplyDraft(conversationId, {
    operator_id: operatorId, editor_id: editorId, active: false
  })).catch(() => {})
})
</script>

<style scoped>
.composer { flex: 0 0 auto; padding: 8px 14px 6px; border-top: 1px solid var(--app-border); background: #fff; }
.composer-main { display: flex; align-items: stretch; gap: 12px; }
.reply-input { flex: 1; min-width: 0; }
.reply-input :deep(.el-textarea__inner) { min-height: 64px; padding: 8px 12px; line-height: 1.6; font-size: 14px; user-select: text; -webkit-user-select: text; }
.quote-draft { display: flex; align-items: flex-start; gap: 8px; padding: 8px 12px; margin-bottom: 8px; border-left: 3px solid var(--app-accent); background: #f3f6f4; border-radius: 4px; font-size: 12px; }
.quote-draft > div { display: flex; flex: 1; flex-direction: column; gap: 4px; min-width: 0; }
.quote-draft span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.quote-draft small { color: var(--app-text-secondary); }
.send-area { display: flex; flex: 0 0 92px; flex-direction: column; align-items: stretch; justify-content: flex-end; gap: 8px; }
.reply-status { display: flex; align-items: center; justify-content: center; gap: 6px; padding: 5px 4px; border-radius: 6px; background: #edf5f1; color: var(--app-accent); font-size: 13px; white-space: nowrap; }
.reply-status.human { background: #fff4e4; color: #a56616; }
.reply-status i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.send-area .el-button { margin: 0; min-height: 32px; }
.composer-tools { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
.composer-tools :deep(.el-button + .el-button) { margin-left: 0; }
.composer-hint { color: var(--app-text-secondary); font-size: 12px; }
.composer-tools .composer-hint { margin-left: auto; }
.file-input { display: none; }
.image-drafts { display: flex; gap: 8px; margin-top: 8px; overflow-x: auto; }
.image-draft { position: relative; flex: 0 0 72px; height: 72px; }
.image-draft img { width: 100%; height: 100%; object-fit: contain; border: 1px solid var(--app-border); border-radius: 6px; }
.image-draft button { position: absolute; top: 0; right: 0; width: 22px; height: 22px; padding: 0; border: 0; border-radius: 50%; background: #fff; color: var(--app-text-secondary); cursor: pointer; }
.emoji-picker { width: 320px; max-width: 100%; height: 320px; }
.received-emojis { display: flex; align-items: center; gap: 6px; overflow-x: auto; margin-top: 4px; font-size: 12px; color: var(--app-text-secondary); }
.received-emojis > span { flex: 0 0 auto; }
.received-emojis button { flex: 0 0 30px; width: 30px; height: 30px; padding: 2px; border: 1px solid var(--app-border); border-radius: 5px; background: #fff; cursor: pointer; }
.received-emojis img { width: 100%; height: 100%; object-fit: contain; }
@media (max-width: 820px), (hover: none) and (pointer: coarse) {
  .composer { padding: 8px; }
  .composer-main { gap: 8px; }
  .send-area { flex-basis: 76px; gap: 8px; }
  .reply-input :deep(.el-textarea__inner) { min-height: 64px; }
  .composer-tools .composer-hint { flex-basis: 100%; margin-left: 0; font-size: 11px; }
  .emoji-picker { height: min(320px, 50dvh); }
}
</style>
