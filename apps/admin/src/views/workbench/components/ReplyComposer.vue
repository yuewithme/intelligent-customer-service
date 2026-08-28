<template>
  <div class="composer">
    <div class="mobile-reception-status" :class="`is-${status}`">
      <div class="reception-label">
        <span class="status-dot" aria-hidden="true"></span>
        <span>当前接待：</span>
        <strong>{{ receptionStatusText }}</strong>
      </div>
      <ElDropdown
        v-if="status !== 'resolved'"
        trigger="click"
        placement="top-end"
        @command="switchReception"
      >
        <ElButton class="mobile-switch-button" size="small">切换接待</ElButton>
        <template #dropdown>
          <ElDropdownMenu>
            <ElDropdownItem command="human" :disabled="isHumanReplying">
              转人工
            </ElDropdownItem>
            <ElDropdownItem command="ai" :disabled="isAiReplying">
              转 AI
            </ElDropdownItem>
          </ElDropdownMenu>
        </template>
      </ElDropdown>
    </div>
    <ElAlert
      v-if="status === 'ai_active' || status === 'ai_waiting'"
      class="ai-monitor-alert"
      title="当前由 AI 自动回复，人工仅可监控"
      type="info"
      :closable="false"
    />
    <ElButton
      v-if="status === 'handoff_pending'"
      class="desktop-claim-action"
      type="primary"
      @click="$emit('claim')"
    >
      领取接管
    </ElButton>
    <div
      v-if="status !== 'resolved'"
      class="reply-controls"
      :class="{ 'can-reply': canReply }"
    >
      <ElInput
        v-model="content"
        type="textarea"
        :rows="4"
        :disabled="!canReply"
        :placeholder="replyPlaceholder"
      />
      <div class="composer-tools">
        <ElPopover
          placement="top-start"
          :width="'min(344px, calc(100vw - 24px))'"
          trigger="click"
        >
          <template #reference>
            <ElButton :disabled="!canReply">全部小表情</ElButton>
          </template>
          <emoji-picker class="emoji-picker" locale="zh" @emoji-click="selectUnicodeEmoji" />
        </ElPopover>
        <ElButton :disabled="!canReply" @click="openImagePicker">发送图片</ElButton>
        <input
          ref="imageInput"
          class="file-input"
          type="file"
          accept=".jpg,.jpeg,.png,.gif,.webp,image/jpeg,image/png,image/gif,image/webp"
          @change="selectImage"
        />
        <ElButton type="primary" :disabled="!canReply || !content.trim()" @click="send">
          发送
        </ElButton>
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
    </div>
    <ElAlert
      v-if="status === 'resolved'"
      title="会话已结束，仅可查看"
      type="warning"
      :closable="false"
    />
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
  switchHuman: []
  switchAi: []
  send: [content: string]
  sendImage: [file: File]
  sendEmoji: [sourceMessageId: number]
}>()
const content = ref('')
const imageInput = ref<HTMLInputElement>()
const receivedEmojis = ref<ConversationEmoji[]>([])
const isAiReplying = computed(
  () => props.status === 'ai_active' || props.status === 'ai_waiting'
)
const isHumanReplying = computed(
  () => props.status === 'handoff_pending' || props.status === 'human_active'
)
const canReply = computed(() => isHumanReplying.value)
const replyPlaceholder = computed(() =>
  canReply.value ? '输入人工回复' : '转为人工接管后可回复'
)
const receptionStatusText = computed(
  () =>
    ({
      ai_active: 'AI 回复中',
      ai_waiting: 'AI 回复中',
      handoff_pending: '人工回复中',
      human_active: '人工回复中',
      resolved: '会话已结束'
    })[props.status] || props.status
)

const switchReception = (command: string) => {
  if (command === 'human') emit('switchHuman')
  if (command === 'ai') emit('switchAi')
}

const send = () => {
  if (!canReply.value) return
  const value = content.value.trim()
  if (!value) return
  emit('send', value)
  content.value = ''
}

const selectUnicodeEmoji = (event: Event) => {
  if (!canReply.value) return
  const emoji = (event as CustomEvent<{ unicode?: string }>).detail?.unicode || ''
  content.value += emoji
}

const openImagePicker = () => {
  if (canReply.value) imageInput.value?.click()
}

const selectImage = (event: Event) => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file && canReply.value) emit('sendImage', file)
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

.reply-controls {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.reply-controls:not(.can-reply) {
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
    justify-content: space-between;
    gap: 8px;
    min-height: 34px;
    padding: 4px 9px;
    color: #56645f;
    border: 1px solid #dfe8e4;
    border-radius: 6px;
    background: #f7faf9;
    font-size: 13px;
  }
  .reception-label { display: flex; align-items: center; min-width: 0; }
  .mobile-switch-button { flex: 0 0 auto; margin-left: 0; }
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
  .ai-monitor-alert,
  .desktop-claim-action { display: none; }
  .reply-controls,
  .reply-controls:not(.can-reply) { display: flex; gap: 6px; }
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
