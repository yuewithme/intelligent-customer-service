<template>
  <div class="composer">
    <ElAlert
      v-if="status === 'ai_active' || status === 'ai_waiting'"
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
        <ElPopover placement="top-start" :width="344" trigger="click">
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
import { ref, watch } from 'vue'
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
</style>
