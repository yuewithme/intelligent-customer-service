<template>
  <section class="message-panel">
    <div class="header">
      <div class="customer-title">
        <ElAvatar v-if="detail" :size="36" :src="detail.conversation.user_avatar_url || undefined">
          {{ avatarText(detail.conversation) }}
        </ElAvatar>
        <div>
          <h2>{{ detail ? displayName(detail.conversation) : '选择会话' }}</h2>
          <p v-if="detail"
            >{{ detail.conversation.channel }} / {{ sessionText(detail.conversation) }}</p
          >
        </div>
      </div>
      <ElButton :disabled="!conversationIds.length" :icon="Refresh" circle @click="load()" />
    </div>

    <div ref="timelineRef" v-loading="loading" class="timeline">
      <div v-if="selectionMode && !readOnly" class="selection-toolbar">
        <span>已选 {{ selectedMessageIds.size }} 条</span>
        <div>
          <ElButton size="small" @click="clearSelection">取消</ElButton>
          <ElButton
            size="small"
            type="primary"
            :disabled="!selectedMessageIds.size"
            @click="saveDialogVisible = true"
          >
            存为活动
          </ElButton>
          <ElButton
            size="small"
            type="success"
            :disabled="!selectedMaterialMessage"
            @click="saveAsWechatMaterial"
          >
            存为微信素材
          </ElButton>
        </div>
      </div>
      <ElEmpty v-if="!conversationIds.length" description="请选择左侧会话" />
      <ElEmpty v-else-if="!detail?.messages.length && !loading" description="暂无消息" />
      <div
        v-for="message in detail?.messages || []"
        :key="message.id"
        class="message-row"
        :class="[
          message.sender_type,
          { selectable: canSelectMessage(message), selected: selectedMessageIds.has(message.id) }
        ]"
        @click="toggleSelectedMessage(message)"
        @contextmenu.prevent="!readOnly && startSelection(message)"
      >
        <div class="bubble">
          <span v-if="selectedMessageIds.has(message.id)" class="selected-mark">✓</span>
          <div class="sender">{{ senderText(message.sender_type) }}</div>
          <div class="content">
            <ElImage
              v-if="isImageMessage(message) && mediaSource(message)"
              class="message-image"
              :src="mediaSource(message)"
              :preview-src-list="[mediaSource(message)]"
              fit="cover"
              preview-teleported
            />
            <video
              v-else-if="mediaType(message) === 'video' && mediaSource(message)"
              :key="mediaSource(message)"
              class="message-video"
              :src="mediaSource(message)"
              controls
              @error="markVideoFailed(message)"
            ></video>
            <audio
              v-else-if="mediaType(message) === 'audio' && mediaSource(message)"
              class="message-audio"
              :src="mediaSource(message)"
              controls
            ></audio>
            <a
              v-else-if="mediaSource(message)"
              class="message-link"
              :href="mediaSource(message)"
              target="_blank"
              rel="noreferrer"
            >
              {{ mediaFileName(message) || message.content }}
            </a>
            <ElButton
              v-else-if="mediaType(message) === 'video'"
              type="primary"
              link
              :loading="resolvingMediaIds.has(message.id)"
              @click="resolveVideo(message)"
            >
              解析视频
            </ElButton>
            <span v-else>{{ message.content }}</span>
            <a
              v-if="showOriginalLink(message)"
              class="original-link"
              :href="mediaSource(message)"
              target="_blank"
              rel="noreferrer"
            >
              {{ resolvingMediaIds.has(message.id) ? '正在解析视频...' : '打开原链接' }}
            </a>
          </div>
          <div class="time">{{ formatTime(message.created_at) }}</div>
        </div>
      </div>
    </div>
    <SaveActivityDialog
      v-model="saveDialogVisible"
      :conversation-id="conversationId"
      :message-ids="selectedIds"
      @saved="clearSelection"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  getConversationDetail,
  resolveConversationMessageMedia,
  type ConversationDetail,
  type ConversationItem,
  type ConversationMessage
} from '@/api/admin/conversations'
import { formatChinaTime } from '../time'
import SaveActivityDialog from './SaveActivityDialog.vue'
import { isTestGate } from '@/utils/gate'
import { createWechatMaterialFromMessage } from '@/api/admin/wechatMaterials'

interface MediaMetadata {
  type?: string
  url?: string
  resolve_status?: 'pending' | 'processing' | 'succeeded' | 'failed'
  resolve_error?: string
  thumb_base64?: string
  file_name?: string
  filename?: string
  name?: string
}

const props = defineProps<{ conversationId: string; conversationIds: string[] }>()
const readOnly = isTestGate()
const emit = defineEmits<{ loaded: [conversation: ConversationItem | undefined] }>()

const loading = ref(false)
const detail = ref<ConversationDetail>()
const timelineRef = ref<HTMLElement>()
const resolvingMediaIds = ref(new Set<number>())
const failedMediaIds = ref(new Set<number>())
const selectedMessageIds = ref(new Set<number>())
const selectionMode = ref(false)
const saveDialogVisible = ref(false)
let reloadPending = false
let requesting = false

const load = async (options: { silent?: boolean } = {}) => {
  if (requesting) {
    reloadPending = true
    return
  }
  if (!props.conversationIds.length) {
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
    const details = await Promise.all(
      props.conversationIds.map((conversationId) => getConversationDetail(conversationId))
    )
    const currentDetail =
      details.find((item) => item.conversation.conversation_id === props.conversationId) ||
      details[0]

    detail.value = {
      conversation: currentDetail.conversation,
      messages: details
        .flatMap((item) => item.messages)
        .sort(
          (left, right) =>
            new Date(left.created_at).getTime() - new Date(right.created_at).getTime() ||
            left.id - right.id
        )
    }
    emit('loaded', detail.value.conversation)
    await nextTick()
    if (wasNearBottom && timelineRef.value) {
      timelineRef.value.scrollTop = timelineRef.value.scrollHeight
    }
    autoResolvePendingVideos(detail.value.messages)
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

const messageMedia = (message: ConversationMessage): MediaMetadata | undefined => {
  const media = message.metadata.media
  return media && typeof media === 'object' ? (media as MediaMetadata) : undefined
}

const mediaType = (message: ConversationMessage) => messageMedia(message)?.type || ''

const isImageMessage = (message: ConversationMessage) => mediaType(message) === 'image'

const mediaSource = (message: ConversationMessage) => {
  const media = messageMedia(message)
  if (!media || failedMediaIds.value.has(message.id)) {
    return ''
  }
  if (media.url) {
    return media.url
  }
  if (media.type === 'image' && media.thumb_base64) {
    return `data:image/jpeg;base64,${media.thumb_base64}`
  }
  return ''
}

const mediaFileName = (message: ConversationMessage) => {
  const media = messageMedia(message)
  return media?.file_name || media?.filename || media?.name || ''
}

const showOriginalLink = (message: ConversationMessage) =>
  ['video', 'audio'].includes(mediaType(message)) && Boolean(mediaSource(message))

const markVideoFailed = (message: ConversationMessage) => {
  failedMediaIds.value = new Set(failedMediaIds.value).add(message.id)
}

const canSelectMessage = (message: ConversationMessage) => {
  if (message.conversation_id !== props.conversationId || message.sender_type !== 'customer') {
    return false
  }
  const type = mediaType(message)
  if (!type) return Boolean(message.content.trim())
  return ['image', 'video'].includes(type) && Boolean(message.metadata.raw_content)
}

const startSelection = (message: ConversationMessage) => {
  if (!canSelectMessage(message)) {
    ElMessage.warning('该消息不支持保存为活动素材')
    return
  }
  selectionMode.value = true
  selectedMessageIds.value = new Set(selectedMessageIds.value).add(message.id)
}

const toggleSelectedMessage = (message: ConversationMessage) => {
  if (!selectionMode.value || !canSelectMessage(message)) return
  const selected = new Set(selectedMessageIds.value)
  if (selected.has(message.id)) {
    selected.delete(message.id)
  } else {
    selected.add(message.id)
  }
  selectedMessageIds.value = selected
  if (!selected.size) selectionMode.value = false
}

const selectedIds = computed(() =>
  (detail.value?.messages || [])
    .filter((message) => selectedMessageIds.value.has(message.id))
    .map((message) => message.id)
)

const selectedMaterialMessage = computed(() => {
  if (selectedMessageIds.value.size !== 1) return undefined
  const message = (detail.value?.messages || []).find((item) =>
    selectedMessageIds.value.has(item.id)
  )
  return message && ['image', 'video'].includes(mediaType(message)) ? message : undefined
})

const saveAsWechatMaterial = async () => {
  const message = selectedMaterialMessage.value
  if (!message) return
  await createWechatMaterialFromMessage(message.id)
  ElMessage.success('已存入微信素材库，批量发送时将复用 XML')
  clearSelection()
}

const clearSelection = () => {
  selectedMessageIds.value = new Set()
  selectionMode.value = false
  saveDialogVisible.value = false
}

const autoResolvePendingVideos = (messages: ConversationMessage[]) => {
  for (const message of messages) {
    const media = messageMedia(message)
    if (media?.type === 'video' && media.resolve_status === 'pending') {
      void resolveVideo(message, { silent: true })
    }
  }
}

const resolveVideo = async (
  message: ConversationMessage,
  options: { silent?: boolean } = {}
) => {
  if (resolvingMediaIds.value.has(message.id)) {
    return
  }
  resolvingMediaIds.value = new Set(resolvingMediaIds.value).add(message.id)
  try {
    const resolved = await resolveConversationMessageMedia(message.id)
    message.metadata = resolved.metadata
    const failed = new Set(failedMediaIds.value)
    failed.delete(message.id)
    failedMediaIds.value = failed
    await nextTick()
  } catch {
    const media = messageMedia(message)
    if (media) {
      media.resolve_status = 'failed'
      media.resolve_error = 'provider_download_failed'
    }
    if (!options.silent) {
      ElMessage.warning('视频解析失败，已保留原链接')
    }
  } finally {
    const pending = new Set(resolvingMediaIds.value)
    pending.delete(message.id)
    resolvingMediaIds.value = pending
  }
}

const displayName = (conversation: ConversationItem) =>
  conversation.user_display_name || conversation.user_id

const sessionText = (conversation: ConversationItem) =>
  conversation.channel === 'wechat' && conversation.session_id === 'default'
    ? '私聊'
    : conversation.session_id || '-'

const avatarText = (conversation: ConversationItem) =>
  displayName(conversation).slice(0, 1).toUpperCase()

const formatTime = formatChinaTime

watch(
  () => [props.conversationId, props.conversationIds.join('|')],
  () => {
    clearSelection()
    void load()
  }
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

.selection-toolbar {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  margin: -8px -8px 14px;
  background: #fff;
  border: 1px solid #bfdbfe;
  border-radius: 6px;
  box-shadow: 0 4px 12px rgb(15 23 42 / 8%);
}

.message-row {
  display: flex;
  margin-bottom: 14px;
}

.message-row.ai,
.message-row.human {
  justify-content: flex-end;
}

.message-row.selectable {
  cursor: pointer;
}

.message-row.selected .bubble {
  border-color: #2563eb;
  box-shadow: 0 0 0 2px rgb(37 99 235 / 16%);
}

.bubble {
  position: relative;
  max-width: min(70%, 680px);
  padding: 10px 12px;
  white-space: pre-wrap;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
}

.selected-mark {
  position: absolute;
  top: -8px;
  right: -8px;
  display: grid;
  width: 20px;
  height: 20px;
  color: #fff;
  font-size: 12px;
  background: #2563eb;
  border-radius: 50%;
  place-items: center;
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

.message-image {
  display: block;
  width: min(240px, 52vw);
  max-height: 320px;
  overflow: hidden;
  border-radius: 6px;
}

.message-video {
  display: block;
  width: min(360px, 60vw);
  max-height: 320px;
  border-radius: 6px;
}

.message-audio {
  display: block;
  width: min(320px, 58vw);
}

.message-link {
  color: inherit;
  text-decoration: underline;
  text-underline-offset: 3px;
}

.original-link {
  display: block;
  margin-top: 6px;
  color: inherit;
  font-size: 12px;
  text-decoration: underline;
  text-underline-offset: 3px;
}
</style>
