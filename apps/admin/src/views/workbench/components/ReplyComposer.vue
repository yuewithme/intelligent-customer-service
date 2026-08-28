<template>
  <div class="composer">
    <div class="mobile-reception-status" :class="`is-${status}`">
      <span class="status-dot" aria-hidden="true"></span>
      <span>当前接待：</span>
      <strong>{{ receptionStatusText }}</strong>
    </div>
    <ElAlert
      v-if="status === 'ai_active' || status === 'ai_waiting'"
      class="ai-monitor-alert"
      title="当前由 AI 自动回复，人工仅可监控"
      type="info"
      :closable="false"
    />
    <ElButton v-else-if="status === 'handoff_pending'" type="primary" @click="$emit('claim')">
      领取接管
    </ElButton>
    <template v-else-if="status === 'human_active'">
      <ElInput v-model="content" type="textarea" :rows="4" placeholder="输入人工回复" />
      <div class="composer-tools">
        <ElPopover
          placement="top-start"
          :width="'min(344px, calc(100vw - 24px))'"
          trigger="click"
        >
          <template #reference>
            <ElButton>全部小表情</ElButton>
          </template>
          <emoji-picker class="emoji-picker" locale="zh" @emoji-click="selectUnicodeEmoji" />
        </ElPopover>
        <ElButton @click="openImagePicker">发送图片</ElButton>
        <input
          ref="imageInput"
          class="file-input"
          type="file"
          accept=".jpg,.jpeg,.png,.gif,.webp,image/jpeg,image/png,image/gif,image/webp"
          @change="selectImage"
        />
        <ElButton type="primary" :disabled="!content.trim()" @click="send">发送</ElButton>
      </div>
      <div v-if="receivedEmojis.length" class="received-emojis">
        <span>客户发过的表情</span>
        <button
          v-for="item in receivedEmojis"
          :key="item.message_id"
          type="button"
          title="发送同款表情"
          @click="$emit('sendEmoji', item.message_id)"
        >
          <img v-if="item.url" :src="item.url" alt="客户表情" />
          <span v-else>表情</span>
        </button>
      </div>
    </template>
    <ElAlert v-else title="会话已结束，仅可查看" type="warning" :closable="false" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import 'emoji-picker-element'
import {
  getConversationEmojis,
  type ConversationEmoji
} from '@/api/admin/conversations'

const props = defineProps<{ status: string; conversationId: string }>()
const emit = defineEmits<{
  claim: []
  send: [content: string]
  sendImage: [file: File]
  sendEmoji: [sourceMessageId: number]
}>()
const content = ref('')
const imageInput = ref<HTMLInputElement>()
const receivedEmojis = ref<ConversationEmoji[]>([])
const receptionStatusText = computed(
  () =>
    ({
      ai_active: 'AI 接待',
      ai_waiting: 'AI 接待',
      handoff_pending: '等待人工接管',
      human_active: '人工接管',
      resolved: '会话已结束'
    })[props.status] || props.status
)

const send = () => {
  const value = content.value.trim()
  if (!value) return
  emit('send', value)
  content.value = ''
}

const selectUnicodeEmoji = (event: Event) => {
  const emoji = (event as CustomEvent<{ unicode?: string }>).detail?.unicode || ''
  content.value += emoji
}

const openImagePicker = () => imageInput.value?.click()

const selectImage = (event: Event) => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) emit('sendImage', file)
  input.value = ''
}

const loadReceivedEmojis = async () => {
  if (props.status !== 'human_active' || !props.conversationId) {
    receivedEmojis.value = []
    return
  }
  const result = await getConversationEmojis(props.conversationId)
  receivedEmojis.value = result.items
}

watch(
  () => [props.conversationId, props.status],
  () => void loadReceivedEmojis(),
  { immediate: true }
)
</script>

<style scoped>
.composer {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.mobile-reception-status {
  display: none;
}

.composer-tools {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.composer-tools .el-button:last-child {
  margin-left: auto;
}

.file-input {
  display: none;
}

.emoji-picker {
  width: 320px;
  max-width: 100%;
  height: 360px;
}

.received-emojis {
  display: flex;
  align-items: center;
  gap: 6px;
  overflow-x: auto;
}

.received-emojis > span {
  flex: 0 0 auto;
  color: #6b7280;
  font-size: 12px;
}

.received-emojis button {
  display: grid;
  flex: 0 0 38px;
  width: 38px;
  height: 38px;
  padding: 2px;
  place-items: center;
  overflow: hidden;
  border: 1px solid #e5e7eb;
  border-radius: 7px;
  background: #fff;
  cursor: pointer;
}

.received-emojis img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.received-emojis button span {
  font-size: 11px;
}

@media (max-width: 820px) {
  .composer { gap: 8px; }
  .mobile-reception-status {
    display: flex;
    align-items: center;
    min-height: 28px;
    padding: 4px 9px;
    color: #56645f;
    border: 1px solid #dfe8e4;
    border-radius: 6px;
    background: #f7faf9;
    font-size: 13px;
  }
  .mobile-reception-status strong { color: #167452; }
  .mobile-reception-status .status-dot {
    width: 7px;
    height: 7px;
    margin-right: 7px;
    border-radius: 50%;
    background: #20a06b;
  }
  .mobile-reception-status.is-ai_active strong,
  .mobile-reception-status.is-ai_waiting strong { color: #2563a5; }
  .mobile-reception-status.is-ai_active .status-dot,
  .mobile-reception-status.is-ai_waiting .status-dot { background: #409eff; }
  .mobile-reception-status.is-handoff_pending strong { color: #b66a13; }
  .mobile-reception-status.is-handoff_pending .status-dot { background: #e6a23c; }
  .mobile-reception-status.is-resolved strong { color: #7b8581; }
  .mobile-reception-status.is-resolved .status-dot { background: #909399; }
  .ai-monitor-alert { display: none; }
  .composer :deep(.el-textarea__inner) {
    height: 56px !important;
    min-height: 56px !important;
    max-height: 96px;
  }
  .composer-tools { display: grid; grid-template-columns: 1fr 1fr auto; gap: 6px; }
  .composer-tools :deep(.el-button) { width: 100%; margin-left: 0; }
  .composer-tools .el-button:last-child { min-width: 68px; margin-left: 0; }
  .emoji-picker { height: min(360px, 56dvh); }
}
</style>
