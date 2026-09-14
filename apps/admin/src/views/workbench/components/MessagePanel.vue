<template>
  <section class="message-panel">
    <div class="header">
      <button
        v-if="showMobileBack"
        class="mobile-back"
        type="button"
        aria-label="返回会话列表"
        @click="emit('mobile-back')"
      >
        ‹
      </button>
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
      <ElButton :disabled="!conversationIds.length" :icon="Refresh" circle aria-label="刷新聊天记录" @click="load()" />
    </div>

    <div ref="timelineRef" v-loading="loading" class="timeline" :class="{ 'is-empty': !detail?.messages.length }">
      <div v-if="selectionMode && isAdmin()" class="selection-toolbar">
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
        :data-message-id="message.id"
        class="message-row"
        :class="[
          message.sender_type,
          { selectable: canSelectMessage(message), selected: selectedMessageIds.has(message.id), focused: focusedMessageId === message.id }
        ]"
        @click="toggleSelectedMessage(message)"
        @contextmenu.prevent="isAdmin() && startSelection(message)"
      >
        <div class="bubble">
          <span v-if="selectedMessageIds.has(message.id)" class="selected-mark">✓</span>
          <div class="sender">
            {{ senderText(message.sender_type) }}
            <ElTag v-if="message.metadata.is_evaluation" size="small" type="warning">
              测试 · {{ message.metadata.evaluation_id }}
            </ElTag>
            <ElTag
              v-if="hasDeliveryFailure(message)"
              size="small"
              type="danger"
            >
              发送失败
            </ElTag>
          </div>
          <div class="content">
            <small v-if="isPrivateFile(message)" class="file-network-hint">
              文件名已识别，原文件请在微信中查看
            </small>
            <a
              v-if="linkCard(message)"
              class="commerce-card"
              :href="linkCard(message)?.url"
              target="_blank"
              rel="noreferrer"
            >
              <img
                v-if="linkCard(message)?.thumb_url"
                :src="linkCard(message)?.thumb_url"
                alt=""
              />
              <span>
                <strong>{{ linkCard(message)?.title || '查看详情' }}</strong>
                <small v-if="linkCard(message)?.description">
                  {{ linkCard(message)?.description }}
                </small>
              </span>
            </a>
            <div v-else-if="miniProgramCard(message)" class="commerce-card">
              <img
                v-if="miniProgramCard(message)?.thumb_url"
                :src="miniProgramCard(message)?.thumb_url"
                alt=""
              />
              <span>
                <strong>{{ miniProgramCard(message)?.title || '微信小程序' }}</strong>
                <small>商品小程序卡片</small>
              </span>
            </div>
            <ElImage
              v-else-if="isImageMessage(message) && mediaSource(message)"
              class="message-image"
              :src="mediaSource(message)"
              :preview-src-list="[mediaSource(message)]"
              fit="cover"
              preview-teleported
            >
              <template #error>
                <div class="image-load-error">图片加载失败</div>
              </template>
            </ElImage>
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
              @error="markMediaFailed(message)"
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
            <div v-else-if="canResolveMedia(message)" class="media-placeholder">
              <span>{{ mediaStatusText(message) }}</span>
              <small v-if="messageMedia(message)?.resolve_error">
                {{ messageMedia(message)?.resolve_error }}
              </small>
              <ElButton
                v-if="canRetryMedia(message)"
                type="primary"
                link
                :loading="resolvingMediaIds.has(message.id)"
                @click="retryMedia(message)"
              >
                重新解析
              </ElButton>
            </div>
            <span v-else>{{ displayContent(message) }}</span>
            <a
              v-if="showOriginalLink(message)"
              class="original-link"
              :href="mediaSource(message)"
              target="_blank"
              rel="noreferrer"
            >
              打开媒体文件
            </a>
          </div>
          <div class="message-footer">
            <ElButton
              v-if="!readOnly && canRetryDelivery(message)"
              type="primary"
              link
              size="small"
              :loading="retryingDeliveryIds.has(message.id)"
              @click.stop="retryDelivery(message)"
            >
              重新发送
            </ElButton>
            <span class="time">{{ formatTime(message.created_at) }}</span>
          </div>
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
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  getConversationDetail,
  retryConversationMessageDelivery,
  resolveConversationMessageMedia,
  type ConversationDetail,
  type ConversationItem,
  type ConversationMessage
} from '@/api/admin/conversations'
import { formatChinaTime } from '@/utils/time'
import SaveActivityDialog from './SaveActivityDialog.vue'
import { isTestGate, isAdmin } from '@/utils/gate'
import { createWechatMaterialFromMessage } from '@/api/admin/wechatMaterials'

interface MediaMetadata {
  type?: string
  url?: string
  resolve_status?: 'pending' | 'processing' | 'succeeded' | 'failed'
  resolve_error?: string
  job_key?: string
  thumb_base64?: string
  file_name?: string
  filename?: string
  name?: string
}

interface CommerceCardMetadata {
  title?: string
  description?: string
  url?: string
  thumb_url?: string
  page_path?: string
}

const props = defineProps<{
  conversationId: string
  conversationIds: string[]
  focusMessageId?: number
  showMobileBack?: boolean
}>()
const readOnly = isTestGate()
const emit = defineEmits<{
  loaded: [detail: ConversationDetail | undefined]
  'mobile-back': []
}>()

const loading = ref(false)
const detail = ref<ConversationDetail>()
const timelineRef = ref<HTMLElement>()
const resolvingMediaIds = ref(new Set<number>())
const failedMediaIds = ref(new Set<number>())
const retryingDeliveryIds = ref(new Set<number>())
const selectedMessageIds = ref(new Set<number>())
const selectionMode = ref(false)
const saveDialogVisible = ref(false)
const focusedMessageId = ref<number>()
let focusTimer: number | undefined
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
        ),
      agent_relationship: currentDetail.agent_relationship,
    }
    emit('loaded', detail.value)
    await nextTick()
    if (props.focusMessageId && timelineRef.value) {
      const target = timelineRef.value.querySelector<HTMLElement>(
        `[data-message-id="${props.focusMessageId}"]`
      )
      if (target) {
        target.scrollIntoView({ block: 'center', behavior: 'smooth' })
        focusedMessageId.value = props.focusMessageId
        if (focusTimer) window.clearTimeout(focusTimer)
        focusTimer = window.setTimeout(() => { focusedMessageId.value = undefined }, 3000)
      }
    } else if (wasNearBottom && timelineRef.value) {
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

const deliveryFailureStatuses = new Set(['failed', 'waiting_material'])

const hasDeliveryFailure = (message: ConversationMessage) =>
  deliveryFailureStatuses.has(message.delivery_status || '')

const canRetryDelivery = hasDeliveryFailure

const retryDelivery = async (message: ConversationMessage) => {
  retryingDeliveryIds.value = new Set(retryingDeliveryIds.value).add(message.id)
  try {
    await retryConversationMessageDelivery(message.id)
    ElMessage.success('已安排重新发送')
    await load({ silent: true })
  } catch {
    ElMessage.error('重新发送失败，请稍后重试')
  } finally {
    const next = new Set(retryingDeliveryIds.value)
    next.delete(message.id)
    retryingDeliveryIds.value = next
  }
}

const messageMedia = (message: ConversationMessage): MediaMetadata | undefined => {
  const media = message.metadata.media
  return media && typeof media === 'object' ? (media as MediaMetadata) : undefined
}

const mediaType = (message: ConversationMessage) => messageMedia(message)?.type || ''

const canResolveMedia = (message: ConversationMessage) =>
  message.metadata.provider === 'eyun' &&
  ['60003', '60004'].includes(String(message.metadata.message_type || '')) &&
  ['video', 'audio'].includes(mediaType(message))

const canRetryMedia = (message: ConversationMessage) => {
  const media = messageMedia(message)
  const status = media?.resolve_status
  return (
    canResolveMedia(message) &&
    (
      status === 'failed' ||
      failedMediaIds.value.has(message.id) ||
      !status ||
      (status === 'pending' && !media?.job_key)
    )
  )
}

const mediaStatusText = (message: ConversationMessage) => {
  const label = mediaType(message) === 'audio' ? '语音' : '视频'
  const status = messageMedia(message)?.resolve_status
  if (status === 'failed' || failedMediaIds.value.has(message.id)) {
    return `${label}解析失败`
  }
  return `${label}处理中…`
}

const isImageMessage = (message: ConversationMessage) =>
  ['image', 'emoji'].includes(mediaType(message))

const mediaSource = (message: ConversationMessage) => {
  const media = messageMedia(message)
  if (!media || failedMediaIds.value.has(message.id)) {
    return ''
  }
  if (media.url) {
    if (media.type === 'file' && isPrivateNetworkUrl(media.url)) {
      return ''
    }
    if (
      media.url.startsWith('http://') &&
      message.metadata.direction === 'outbound' &&
      message.metadata.source_type === 'service_material_touch'
    ) {
      return `/api/v1/admin/conversations/message-media/${message.id}`
    }
    return media.url
  }
  if (media.type === 'image' && media.thumb_base64) {
    return `data:image/jpeg;base64,${media.thumb_base64}`
  }
  return ''
}

const isPrivateNetworkUrl = (url: string) =>
  /^http:\/\/(?:localhost|127\.|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)/i.test(url)

const isPrivateFile = (message: ConversationMessage) => {
  const media = messageMedia(message)
  return media?.type === 'file' && Boolean(media.url && isPrivateNetworkUrl(media.url))
}

const mediaFileName = (message: ConversationMessage) => {
  const media = messageMedia(message)
  return media?.file_name || media?.filename || media?.name || ''
}

const showOriginalLink = (message: ConversationMessage) =>
  ['video', 'audio'].includes(mediaType(message)) && Boolean(mediaSource(message))

const markMediaFailed = (message: ConversationMessage) => {
  failedMediaIds.value = new Set(failedMediaIds.value).add(message.id)
}

const displayContent = (message: ConversationMessage) => {
  if (mediaType(message) === 'file') {
    return mediaFileName(message) || message.content
  }
  return message.content
}

const markVideoFailed = markMediaFailed

const cardMetadata = (
  message: ConversationMessage,
  key: 'link_card' | 'mini_program'
): CommerceCardMetadata | undefined => {
  const card = message.metadata[key]
  return card && typeof card === 'object' ? (card as CommerceCardMetadata) : undefined
}

const linkCard = (message: ConversationMessage) => cardMetadata(message, 'link_card')
const miniProgramCard = (message: ConversationMessage) =>
  cardMetadata(message, 'mini_program')

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

const retryMedia = async (message: ConversationMessage) => {
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
    ElMessage.success('已重新进入媒体处理队列')
  } catch {
    ElMessage.warning('重新解析请求失败，请稍后再试')
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
  () => [props.conversationId, props.conversationIds.join('|'), props.focusMessageId],
  () => {
    clearSelection()
    void load()
  }
)
onMounted(load)
onBeforeUnmount(() => {
  if (focusTimer) window.clearTimeout(focusTimer)
})

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
  gap: 12px;
  min-height: 78px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--app-border);
}
.header > .el-button { flex-shrink: 0; }
.customer-title > div { min-width: 0; }

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
  padding: 24px;
  overflow: auto;
  background: #f7f9f8;
}
.timeline.is-empty { display: grid; align-content: center; }

.mobile-back { display: none; }

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
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: 8px;
  box-shadow: 0 4px 12px rgb(15 23 42 / 8%);
}

.message-row {
  display: flex;
  margin-bottom: 20px;
}

.message-row.ai,
.message-row.human {
  justify-content: flex-end;
}

.message-row.selectable {
  cursor: pointer;
}

.message-row.selected .bubble {
  border-color: var(--app-accent);
  box-shadow: 0 0 0 2px var(--el-color-primary-light-8);
}

.message-row.focused .bubble {
  border-color: #f59e0b;
  box-shadow: 0 0 0 4px rgb(245 158 11 / 22%);
}

.bubble {
  position: relative;
  max-width: min(88%, 640px);
  padding: 12px 14px;
  white-space: pre-wrap;
  background: #fff;
  border: 1px solid var(--app-border);
  border-radius: 12px;
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
  background: var(--app-accent);
  border-radius: 50%;
  place-items: center;
}

.ai .bubble {
  background: var(--app-accent-soft);
  border-color: #d5e6dc;
}

.human .bubble {
  color: #fff;
  background: var(--app-accent);
  border-color: var(--app-accent);
}

.sender,
.time {
  font-size: 12px;
  color: var(--app-text-secondary);
}
.human .sender, .human .time { color: #e0eee8; }

.sender {
  display: flex;
  align-items: center;
  gap: 6px;
}

.message-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 18px;
}

.time {
  margin-left: auto;
}

.content {
  margin: 6px 0;
  font-size: 14px;
  line-height: 1.75;
  overflow-wrap: anywhere;
}

.message-image, .message-video, .message-audio, .commerce-card { max-width: 100%; }
.human .commerce-card { color: var(--app-text); }

.message-image {
  display: block;
  width: min(240px, 52vw);
  max-height: 320px;
  overflow: hidden;
  border-radius: 6px;
}

.file-network-hint {
  display: block;
  margin-bottom: 4px;
  color: var(--el-text-color-secondary);
}

.image-load-error {
  display: grid;
  min-height: 96px;
  color: var(--el-color-danger);
  background: var(--el-fill-color-light);
  place-items: center;
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

.media-placeholder {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}

.media-placeholder small {
  max-width: 360px;
  color: var(--el-text-color-secondary);
  overflow-wrap: anywhere;
}

.commerce-card {
  display: flex;
  width: min(360px, 62vw);
  min-height: 72px;
  overflow: hidden;
  color: inherit;
  text-decoration: none;
  background: rgb(255 255 255 / 72%);
  border: 1px solid rgb(148 163 184 / 35%);
  border-radius: 8px;
}

.commerce-card img {
  width: 88px;
  object-fit: cover;
  background: #f3f4f6;
}

.commerce-card span {
  display: flex;
  flex: 1;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
  padding: 10px 12px;
}

.commerce-card strong {
  overflow: hidden;
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.commerce-card small {
  display: -webkit-box;
  margin-top: 5px;
  overflow: hidden;
  color: #6b7280;
  font-size: 12px;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.original-link {
  display: block;
  margin-top: 6px;
  color: inherit;
  font-size: 12px;
  text-decoration: underline;
  text-underline-offset: 3px;
}

@media (max-width: 820px), (hover: none) and (pointer: coarse) {
  .header { min-height: 56px; padding: 10px 12px; }
  .mobile-back {
    display: grid;
    flex: 0 0 34px;
    width: 34px;
    height: 34px;
    padding: 0;
    margin-right: 6px;
    place-items: center;
    color: #36534a;
    font-size: 30px;
    line-height: 1;
    background: #f2f6f4;
    border: 0;
    border-radius: 8px;
  }
  .customer-title { flex: 1; }
  .timeline { padding: 12px 10px; }
  .bubble { max-width: 88%; padding: 9px 10px; }
  .message-image,
  .message-video,
  .message-audio,
  .commerce-card { width: min(100%, 78vw); }
  .selection-toolbar { align-items: flex-start; flex-direction: column; gap: 8px; }
  .selection-toolbar > div { display: flex; flex-wrap: wrap; gap: 6px; }
  .selection-toolbar :deep(.el-button + .el-button) { margin-left: 0; }
}
</style>
