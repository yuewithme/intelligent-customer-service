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
      <div class="header-actions">
        <ElButton v-if="detail && !readOnly" :type="onlyFavorites ? 'primary' : 'default'" size="small" @click="onlyFavorites = !onlyFavorites">
          {{ onlyFavorites ? '查看全部' : '查看收藏' }}
        </ElButton>
        <ElButton :disabled="!conversationIds.length" :icon="Refresh" circle aria-label="刷新聊天记录" @click="load()" />
      </div>
    </div>

    <div ref="timelineRef" v-loading="loading" class="timeline" :class="{ 'is-empty': !detail?.messages.length }" @scroll="closeMenu">
      <div v-if="selectionMode" class="selection-toolbar">
        <span>已选 {{ selectedMessageIds.size }} 条</span>
        <div>
          <ElButton size="small" @click="clearSelection">取消</ElButton>
          <ElButton size="small" :disabled="!selectedIds.length" @click="copyMessages(selectedMessages)">复制</ElButton>
          <ElButton v-if="!readOnly" size="small" :disabled="!canForwardSelection" @click="openForward(selectedMessages)">转发</ElButton>
          <ElButton v-if="!readOnly" size="small" :disabled="!selectedIds.length || preferenceBusy" @click="favoriteMessages(selectedMessages)">收藏</ElButton>
          <ElButton v-if="!readOnly" size="small" type="danger" :disabled="!selectedIds.length || preferenceBusy" @click="deleteMessages(selectedMessages)">删除</ElButton>
          <ElButton
            v-if="isAdmin()"
            size="small"
            type="primary"
            :disabled="!canSaveActivitySelection"
            @click="saveDialogVisible = true"
          >
            存为活动
          </ElButton>
          <ElButton
            v-if="isAdmin()"
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
      <ElEmpty v-else-if="!displayedMessages.length && !loading" :description="onlyFavorites ? '当前会话暂无收藏消息' : '暂无消息'" />
      <div
        v-for="message in displayedMessages"
        :key="message.id"
        :data-message-id="message.id"
        class="message-row"
        :class="[
          message.sender_type,
          { selectable: selectionMode && canSelectMessage(message), selected: selectedMessageIds.has(message.id), focused: focusedMessageId === message.id }
        ]"
        @click.capture="handleMessageClick($event, message)"
        @contextmenu.prevent.stop="openMenu($event, message)"
      >
        <div class="bubble">
          <span v-if="selectedMessageIds.has(message.id)" class="selected-mark">✓</span>
          <div class="sender">
            {{ senderText(message.sender_type) }}
            <small v-if="message.is_favorite" class="favorite-mark" title="已收藏到工作台">★ 已收藏</small>
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
          <div v-if="message.metadata.revoked_at" class="revoked-message">该消息已撤回</div>
          <div v-else class="content">
            <blockquote v-if="quotedContent(message)" class="quoted-message">{{ quotedContent(message) }}</blockquote>
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
            <div v-else-if="miniProgramCard(message)" class="mini-program-card">
              <div class="mini-program-name">
                <img v-if="miniProgramCard(message)?.icon_url" :src="miniProgramCard(message)?.icon_url" alt="" referrerpolicy="no-referrer" />
                {{ miniProgramCard(message)?.display_name || '微信小程序' }}
              </div>
              <strong>{{ miniProgramCard(message)?.title || '微信小程序' }}</strong>
              <img
                v-if="miniProgramCard(message)?.thumb_url && !failedMediaIds.has(message.id)"
                class="mini-program-cover"
                :src="miniProgramCard(message)?.thumb_url"
                alt="小程序封面"
                referrerpolicy="no-referrer"
                loading="lazy"
                @error="markMediaFailed(message)"
              />
              <small v-else class="mini-program-cover-hint">封面暂不可用，可在微信查看</small>
              <small class="mini-program-footer">小程序</small>
            </div>
            <ElImage
              v-else-if="isImageMessage(message) && mediaSource(message)"
              :ref="element => setImageRef(message.id, element)"
              class="message-image"
              :src="mediaSource(message)"
              :preview-src-list="selectionMode ? [] : [mediaSource(message)]"
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
              playsinline
              preload="metadata"
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
              <small v-if="mediaFailureHint(message)">
                {{ mediaFailureHint(message) }}
              </small>
              <ElButton
                v-if="canRetryMedia(message)"
                type="primary"
                link
                :loading="resolvingMediaIds.has(message.id)"
                @click="retryMedia(message)"
              >
                重新加载
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
            <ElButton
              v-if="canResolveMedia(message) && mediaSource(message)"
              type="primary"
              link
              :loading="resolvingMediaIds.has(message.id)"
              @click.stop="retryMedia(message)"
            >
              重新加载
            </ElButton>
            <div v-if="mediaType(message) === 'audio' && !message.metadata.revoked_at" class="voice-transcription" aria-live="polite">
              <template v-if="voiceTranscript(message)">
                <div class="transcription-header">
                  <small>语音转文字</small>
                  <ElButton type="primary" link size="small" @click.stop="copyMessages([message], voiceTranscript(message))">复制文字</ElButton>
                </div>
                <p>{{ voiceTranscript(message) }}</p>
              </template>
              <template v-else-if="canTranscribe(message)">
                <ElButton type="primary" link :loading="transcriptionBusy(message)" @click.stop="transcribeVoice(message)">
                  {{ transcriptionBusy(message) ? '转文字中…' : transcriptionFailed(message) ? '重新转文字' : '转文字' }}
                </ElButton>
                <small v-if="transcriptionFailed(message) && !transcriptionBusy(message)">转文字失败，请重试；也可以播放语音。</small>
              </template>
            </div>
          </div>
          <div class="message-footer">
            <ElButton v-if="!message.metadata.revoked_at && message.sender_type !== 'system'" text size="small" aria-label="消息操作" @click.stop="openMenu($event, message)">···</ElButton>
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
    <ForwardMessagesDialog v-model="forwardDialogVisible" :messages="forwardMessages" :source-owners="conversationOwners" @sent="clearSelection(); load({ silent: true })" />
    <ElDialog v-model="textDialogVisible" :title="textAction === 'extract' ? '提取图片文字' : '翻译'" width="min(560px, 92vw)">
      <div v-loading="textBusy" class="message-text-result" aria-live="polite">{{ textResult || (textBusy ? '处理中…' : textError || '未识别到文字') }}</div>
      <p v-if="!textBusy && !textError && textResult" class="text-result-hint">AI 处理结果，请核对原文。</p>
      <template #footer>
        <ElButton @click="textDialogVisible = false">关闭</ElButton>
        <ElButton type="primary" :disabled="textBusy || !textResult" @click="copyMessages([], textResult)">复制文字</ElButton>
      </template>
    </ElDialog>
    <Teleport to="body">
      <div v-if="menu" ref="menuRef" class="message-context-menu" role="menu" aria-label="消息操作" :style="{ left: `${menu.x}px`, top: `${menu.y}px` }" @pointerdown.stop @contextmenu.prevent @keydown="handleMenuNavigation">
        <button type="button" role="menuitem" @click="copyFromMenu">{{ menu.selection ? '复制选中文字' : isImageMessage(menu.message) && mediaSource(menu.message) ? '复制图片' : '复制' }}</button>
        <button v-if="isImageMessage(menu.message) && mediaSource(menu.message)" type="button" role="menuitem" @click="previewImage(menu.message)">查看图片</button>
        <button v-if="mediaSource(menu.message) && !mediaSource(menu.message).startsWith('data:')" type="button" role="menuitem" @click="copyMessages([], mediaSource(menu.message))">复制{{ isImageMessage(menu.message) ? '图片' : '文件' }}链接</button>
        <button v-if="downloadableMedia(menu.message)" type="button" role="menuitem" :disabled="downloadingIds.has(menu.message.id)" @click="saveMedia(menu.message)">{{ downloadingIds.has(menu.message.id) ? '保存中…' : `${mediaLabel(menu.message)}另存为…` }}</button>
        <button v-if="!readOnly && isImageMessage(menu.message) && mediaSource(menu.message)" type="button" role="menuitem" @click="showMessageText(menu.message, 'extract')">提取文字</button>
        <button v-if="!readOnly && canTranslate(menu.message)" type="button" role="menuitem" @click="showMessageText(menu.message, 'translate')">翻译</button>
        <button v-if="canTranscribe(menu.message) && !voiceTranscript(menu.message)" type="button" role="menuitem" :disabled="transcriptionBusy(menu.message)" @click="transcribeVoice(menu.message)">{{ transcriptionBusy(menu.message) ? '转文字中…' : '转文字' }}</button>
        <button v-if="!readOnly && menu.message.actions?.quote && detail?.conversation.status !== 'resolved'" type="button" role="menuitem" @click="quoteFromMenu">引用</button>
        <button v-if="!readOnly && menu.message.actions?.forward" type="button" role="menuitem" @click="openForward([menu.message])">转发</button>
        <button v-if="!readOnly" type="button" role="menuitem" :disabled="preferenceBusy" @click="favoriteMessages([menu.message])">{{ menu.message.is_favorite ? '取消收藏' : '收藏' }}（工作台）</button>
        <button v-if="!readOnly && menu.message.actions?.revoke" type="button" role="menuitem" :disabled="revokingIds.has(menu.message.id)" @click="revokeMessage(menu.message)">撤回</button>
        <button type="button" role="menuitem" @click="startSelection(menu.message); closeMenu()">多选</button>
        <button v-if="!readOnly" type="button" role="menuitem" class="danger-menu-item" :disabled="preferenceBusy" @click="deleteMessages([menu.message])">删除</button>
        <button v-if="isAdmin() && canSaveActivityMessage(menu.message)" type="button" role="menuitem" @click="saveActivityFromMenu">存为活动</button>
        <button v-if="isAdmin() && canSaveActivityMessage(menu.message) && ['image', 'video'].includes(mediaType(menu.message))" type="button" role="menuitem" @click="saveMaterialFromMenu">存为微信素材</button>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  getConversationDetail,
  retryConversationMessageDelivery,
  resolveConversationMessageMedia,
  transcribeConversationMessage,
  revokeConversationMessage,
  updateMessagePreference,
  getMessageText,
  getMessageMedia,
  type ConversationDetail,
  type ConversationItem,
  type ConversationMessage
} from '@/api/admin/conversations'
import { formatChinaTime } from '@/utils/time'
import SaveActivityDialog from './SaveActivityDialog.vue'
import ForwardMessagesDialog from './ForwardMessagesDialog.vue'
import { useUserStore } from '@/store/modules/user'
import { isTestGate, isAdmin } from '@/utils/gate'
import { createWechatMaterialFromMessage } from '@/api/admin/wechatMaterials'

interface MediaMetadata {
  type?: string
  url?: string
  resolve_status?: 'pending' | 'processing' | 'succeeded' | 'failed'
  resolve_error?: string
  resolve_updated_at?: string
  job_key?: string
  thumb_base64?: string
  file_name?: string
  filename?: string
  name?: string
  recognition?: {
    status?: 'pending' | 'processing' | 'succeeded' | 'failed'
    text?: string
  }
}

interface CommerceCardMetadata {
  display_name?: string
  icon_url?: string
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
  quote: [message: ConversationMessage]
}>()

const loading = ref(false)
const detail = ref<ConversationDetail>()
const timelineRef = ref<HTMLElement>()
const resolvingMediaIds = ref(new Set<number>())
const transcribingIds = ref(new Set<number>())
const failedMediaIds = ref(new Set<number>())
const retryingDeliveryIds = ref(new Set<number>())
const selectedMessageIds = ref(new Set<number>())
const selectionMode = ref(false)
const saveDialogVisible = ref(false)
const menu = ref<{ message: ConversationMessage; x: number; y: number; selection: string }>()
const menuRef = ref<HTMLElement>()
const onlyFavorites = ref(false)
const preferenceBusy = ref(false)
const downloadingIds = ref(new Set<number>())
const conversationOwners = ref<Record<string, string>>({})
const imageRefs = new Map<number, { showPreview: () => void }>()
const textDialogVisible = ref(false)
const textBusy = ref(false)
const textAction = ref<'extract' | 'translate'>('extract')
const textResult = ref('')
const textError = ref('')
let textRequestVersion = 0
let menuTrigger: HTMLElement | undefined
const forwardDialogVisible = ref(false)
const forwardMessages = ref<ConversationMessage[]>([])
const revokingIds = ref(new Set<number>())
const userStore = useUserStore()
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
    conversationOwners.value = Object.fromEntries(details.map(item => [item.conversation.conversation_id, item.conversation.owner_wc_id || '']))

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
    selectedMessageIds.value = new Set(detail.value.messages.filter(message => canSelectMessage(message) && selectedMessageIds.value.has(message.id)).map(message => message.id))
    if (!selectedMessageIds.value.size) selectionMode.value = false
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

const canRetryDelivery = (message: ConversationMessage) => !message.metadata.revoked_at && hasDeliveryFailure(message)

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

const voiceTranscript = (message: ConversationMessage) => {
  const recognition = messageMedia(message)?.recognition
  return mediaType(message) === 'audio' && recognition?.status === 'succeeded' ? recognition.text?.trim() || '' : ''
}
const canTranscribe = (message: ConversationMessage) => !readOnly && !message.metadata.revoked_at &&
  mediaType(message) === 'audio' && message.metadata.provider === 'eyun' &&
  ['60004', '80004'].includes(String(message.metadata.message_type || ''))
const transcriptionOverdue = (message: ConversationMessage) =>
  Date.now() - new Date(messageMedia(message)?.resolve_updated_at || message.created_at).getTime() > 5 * 60 * 1000
const transcriptionBusy = (message: ConversationMessage) => {
  const media = messageMedia(message)
  return transcribingIds.value.has(message.id) || (!transcriptionOverdue(message) &&
    (['pending', 'processing'].includes(media?.recognition?.status || '') ||
      (Boolean(media?.job_key) && ['pending', 'processing'].includes(media?.resolve_status || ''))))
}
const transcriptionFailed = (message: ConversationMessage) =>
  messageMedia(message)?.recognition?.status === 'failed' ||
  messageMedia(message)?.resolve_status === 'failed' ||
  (['pending', 'processing'].includes(messageMedia(message)?.recognition?.status || '') && transcriptionOverdue(message))
const transcribeVoice = async (message: ConversationMessage) => {
  closeMenu()
  if (transcriptionBusy(message)) return
  transcribingIds.value.add(message.id)
  try {
    const updated = await transcribeConversationMessage(message.id)
    message.metadata = updated.metadata
    await load({ silent: true })
  } catch {
    ElMessage.warning('转文字请求失败，请稍后重试')
  } finally {
    transcribingIds.value.delete(message.id)
  }
}

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
      (status === 'pending' && !media?.job_key) ||
      mediaLoadingOverdue(message)
    )
  )
}

const mediaLoadingOverdue = (message: ConversationMessage) => {
  const media = messageMedia(message)
  return (
    ['pending', 'processing'].includes(media?.resolve_status || '') &&
    Date.now() - new Date(media?.resolve_updated_at || message.created_at).getTime() > 5 * 60 * 1000
  )
}

const mediaStatusText = (message: ConversationMessage) => {
  const label = mediaType(message) === 'audio' ? '语音' : '视频'
  const status = messageMedia(message)?.resolve_status
  if (status === 'failed' || failedMediaIds.value.has(message.id)) {
    return `${label}加载失败`
  }
  if (mediaLoadingOverdue(message)) return `${label}加载时间较长`
  return `${label}加载中…`
}

const mediaFailureHint = (message: ConversationMessage) => {
  const media = messageMedia(message)
  if (failedMediaIds.value.has(message.id)) return '文件暂时无法播放，请重新加载。'
  if (media?.resolve_status === 'failed') {
    return /403|SignatureDoesNotMatch/i.test(media.resolve_error || '')
      ? '媒体文件暂时无法下载，请重新加载。'
      : '暂时无法获取媒体文件，请重试；也可先在微信中查看。'
  }
  if (mediaLoadingOverdue(message)) return '可以重新加载，或先在微信中查看。'
  return ''
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

const canSelectMessage = (message: ConversationMessage) => message.sender_type !== 'system' && !message.metadata.revoked_at

const canSaveActivityMessage = (message: ConversationMessage) => {
  if (!canSelectMessage(message)) return false
  if (message.conversation_id !== props.conversationId || message.sender_type !== 'customer') {
    return false
  }
  const type = mediaType(message)
  if (!type) return Boolean(message.content.trim())
  return ['image', 'video'].includes(type) && Boolean(message.metadata.raw_content)
}

const startSelection = (message: ConversationMessage) => {
  if (!canSelectMessage(message)) {
    ElMessage.warning('该消息不能选择')
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

const handleMessageClick = (event: MouseEvent, message: ConversationMessage) => {
  if (!selectionMode.value) return
  event.preventDefault()
  event.stopPropagation()
  toggleSelectedMessage(message)
}

const displayedMessages = computed(() => (detail.value?.messages || []).filter(message => !onlyFavorites.value || message.is_favorite))

const selectedIds = computed(() =>
  (detail.value?.messages || [])
    .filter((message) => selectedMessageIds.value.has(message.id))
    .map((message) => message.id)
)
const selectedMessages = computed(() => (detail.value?.messages || []).filter(message => selectedMessageIds.value.has(message.id)))
const canForwardSelection = computed(() => selectedMessages.value.length > 0 && selectedMessages.value.every(message => message.actions?.forward))
const canSaveActivitySelection = computed(() => selectedMessages.value.length > 0 && selectedMessages.value.every(canSaveActivityMessage))

const selectedMaterialMessage = computed(() => {
  if (selectedMessageIds.value.size !== 1) return undefined
  const message = (detail.value?.messages || []).find((item) =>
    selectedMessageIds.value.has(item.id)
  )
  return message && canSaveActivityMessage(message) && ['image', 'video'].includes(mediaType(message)) ? message : undefined
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

const closeMenu = () => { menu.value = undefined }
const handleMenuKey = (event: KeyboardEvent) => {
  if (event.key === 'Escape' && menu.value) {
    closeMenu()
    menuTrigger?.focus({ preventScroll: true })
  }
}
const handleMenuNavigation = (event: KeyboardEvent) => {
  if (event.key === 'Tab') { closeMenu(); return }
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const buttons = Array.from(menuRef.value?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || [])
  const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
  buttons[next]?.focus()
}
const openMenu = async (event: MouseEvent, message: ConversationMessage) => {
  if (!canSelectMessage(message)) return
  const selection = window.getSelection()
  const row = (event.currentTarget as HTMLElement)?.closest('.message-row')
  menuTrigger = (event.currentTarget as HTMLElement)?.querySelector<HTMLButtonElement>('button[aria-label="消息操作"]') || event.currentTarget as HTMLElement
  const bounds = menuTrigger.getBoundingClientRect()
  const state = {
    message, x: event.detail === 0 && event.type === 'click' ? bounds.left : event.clientX,
    y: event.detail === 0 && event.type === 'click' ? bounds.bottom : event.clientY,
    selection: selection?.anchorNode && selection.focusNode && row?.contains(selection.anchorNode) && row.contains(selection.focusNode) ? selection.toString() : ''
  }
  menu.value = state
  const activeMenu = menu.value
  await nextTick()
  if (menu.value !== activeMenu || !menuRef.value) return
  menu.value.x = Math.max(8, Math.min(state.x, window.innerWidth - menuRef.value.offsetWidth - 8))
  menu.value.y = Math.max(8, Math.min(state.y, window.innerHeight - menuRef.value.offsetHeight - 8))
  menuRef.value.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus({ preventScroll: true })
}
const quoteFromMenu = () => {
  if (menu.value) {
    const message = menu.value.message
    emit('quote', { ...message, content: voiceTranscript(message) || message.content })
  }
  closeMenu()
}
const quotedContent = (message: ConversationMessage) => {
  const quote = message.metadata.quoted_message as { sender: string; content: string } | undefined
  return quote ? `${quote.sender}：${quote.content}` : ''
}
const messageCopyText = (message: ConversationMessage) => {
  const card = linkCard(message)
  const body = card ? [card.title, card.description, card.url].filter(Boolean).join('\n') : voiceTranscript(message) || mediaSource(message) || displayContent(message)
  const quote = quotedContent(message)
  return quote ? `「${quote}」\n${body}` : body
}
const copyMessages = async (messages: ConversationMessage[], selectedText = '') => {
  const text = selectedText || messages.map(messageCopyText).join('\n\n')
  closeMenu()
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text)
    else {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.style.cssText = 'position:fixed;left:-9999px;top:0'
      document.body.append(textarea)
      textarea.select()
      try { if (!document.execCommand('copy')) throw new Error('copy failed') }
      finally { textarea.remove() }
    }
    ElMessage.success('已复制，可粘贴到回复框')
  } catch { ElMessage.warning('复制失败，请选中文字后按 Ctrl+C 或使用浏览器复制') }
}
const copyFromMenu = () => {
  const state = menu.value
  if (!state) return
  if (!state.selection && isImageMessage(state.message) && mediaSource(state.message)) void copyImage(state.message)
  else void copyMessages([state.message], state.selection)
}
const fetchMedia = (message: ConversationMessage) => {
  const source = mediaSource(message)
  if (source.startsWith('data:')) return fetch(source).then(response => response.blob())
  return getMessageMedia(message.id, source.startsWith('/static/') ? source : undefined)
}
const copyImage = async (message: ConversationMessage) => {
  closeMenu()
  if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') {
    ElMessage.warning('当前浏览器不支持复制图片，请打开图片后复制')
    return
  }
  try {
    const blob = (async () => {
      const bitmap = await createImageBitmap(await fetchMedia(message))
      const canvas = document.createElement('canvas')
      canvas.width = bitmap.width
      canvas.height = bitmap.height
      const context = canvas.getContext('2d')
      if (!context) { bitmap.close(); throw new Error('image conversion unavailable') }
      context.drawImage(bitmap, 0, 0)
      bitmap.close()
      return await new Promise<Blob>((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('image conversion failed')), 'image/png'))
    })()
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
    ElMessage.success('图片已复制，可粘贴到回复框')
  } catch { ElMessage.warning('图片复制失败，请打开原图后复制') }
}
const setImageRef = (id: number, element: unknown) => {
  if (element) imageRefs.set(id, element as { showPreview: () => void })
  else imageRefs.delete(id)
}
const previewImage = async (message: ConversationMessage) => {
  closeMenu()
  if (selectionMode.value) clearSelection()
  await nextTick()
  imageRefs.get(message.id)?.showPreview()
}
const downloadableMedia = (message: ConversationMessage) => ['image', 'emoji', 'video', 'audio', 'file'].includes(mediaType(message)) && Boolean(mediaSource(message))
const mediaLabel = (message: ConversationMessage) => ({ image: '图片', emoji: '图片', video: '视频', audio: '语音', file: '文件' })[mediaType(message)] || '文件'
const saveMedia = async (message: ConversationMessage) => {
  closeMenu()
  if (downloadingIds.value.has(message.id)) return
  downloadingIds.value.add(message.id)
  try {
    const blob = await fetchMedia(message)
    const sourceName = mediaSource(message).startsWith('data:') ? '' : new URL(mediaSource(message), window.location.origin).pathname.split('/').pop() || ''
    const extension = ({ 'image/jpeg': 'jpg', 'image/png': 'png', 'image/gif': 'gif', 'image/webp': 'webp', 'video/mp4': 'mp4', 'audio/mpeg': 'mp3', 'audio/wav': 'wav', 'audio/ogg': 'ogg', 'application/pdf': 'pdf' } as Record<string, string>)[blob.type.split(';')[0]]
    const filename = mediaFileName(message) || (sourceName.includes('.') ? sourceName : `${mediaLabel(message)}-${message.id}${extension ? `.${extension}` : ''}`)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_')
    document.body.append(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
    ElMessage.success('已发起保存，请在浏览器下载记录中查看')
  } catch { ElMessage.warning('保存失败，媒体可能已过期，请重新加载后再试') }
  finally { downloadingIds.value.delete(message.id) }
}
const canTranslate = (message: ConversationMessage) => Boolean((!mediaType(message) && !linkCard(message) && !miniProgramCard(message) && message.content.trim()) || voiceTranscript(message))
const showMessageText = async (message: ConversationMessage, action: 'extract' | 'translate') => {
  closeMenu()
  const version = ++textRequestVersion
  textAction.value = action
  textResult.value = ''
  textError.value = ''
  textBusy.value = true
  textDialogVisible.value = true
  try {
    const result = await getMessageText(message.id, action)
    if (version === textRequestVersion) textResult.value = result.text
  } catch {
    if (version === textRequestVersion) textError.value = '处理失败，请稍后重试。'
  } finally { if (version === textRequestVersion) textBusy.value = false }
}
const favoriteMessages = async (messages: ConversationMessage[]) => {
  closeMenu()
  if (!messages.length || preferenceBusy.value) return
  const action = messages.length === 1 && messages[0].is_favorite ? 'unfavorite' : 'favorite'
  preferenceBusy.value = true
  try {
    for (const message of messages) {
      const updated = await updateMessagePreference(message.id, action, userStore.user.username || 'admin')
      message.is_favorite = updated.is_favorite
    }
    ElMessage.success(action === 'favorite' ? '已收藏到工作台，可通过“查看收藏”查找；不会同步到微信收藏' : '已取消收藏')
  } finally {
    preferenceBusy.value = false
    await load({ silent: true })
  }
}
const deleteMessages = async (messages: ConversationMessage[]) => {
  closeMenu()
  if (!messages.length || preferenceBusy.value) return
  try {
    await ElMessageBox.confirm(`删除这 ${messages.length} 条消息？仅从当前账号的工作台隐藏，不删除微信消息，原始记录仍保留用于服务复盘。`, '删除消息', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  preferenceBusy.value = true
  let completed = 0
  try {
    for (const message of messages) {
      await updateMessagePreference(message.id, 'hide', userStore.user.username || 'admin')
      completed++
    }
    ElMessage.success(`已删除 ${completed} 条消息`)
    clearSelection()
  } catch {
    if (completed) ElMessage.warning(`已删除 ${completed} 条，剩余消息未删除，请重试`)
  } finally {
    preferenceBusy.value = false
    await load({ silent: true })
  }
}
const openForward = (messages: ConversationMessage[]) => {
  if (readOnly || !messages.length || messages.some(message => !message.actions?.forward)) return
  forwardMessages.value = [...messages]
  forwardDialogVisible.value = true
  closeMenu()
}
const revokeMessage = async (message: ConversationMessage) => {
  if (revokingIds.value.has(message.id)) return
  closeMenu()
  revokingIds.value.add(message.id)
  try {
    await revokeConversationMessage(message.id, userStore.user.nickname || 'admin')
    ElMessage.success('消息已撤回')
    await load({ silent: true })
  } finally { revokingIds.value.delete(message.id) }
}
const saveActivityFromMenu = () => {
  if (!menu.value) return
  clearSelection()
  startSelection(menu.value.message)
  saveDialogVisible.value = true
  closeMenu()
}
const saveMaterialFromMenu = () => {
  if (!menu.value) return
  clearSelection()
  startSelection(menu.value.message)
  closeMenu()
  void saveAsWechatMaterial()
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
    ElMessage.success('已请求重新加载媒体')
  } catch {
    ElMessage.warning('重新加载失败，请稍后再试')
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
    closeMenu()
    onlyFavorites.value = false
    textDialogVisible.value = false
    ++textRequestVersion
    forwardDialogVisible.value = false
    clearSelection()
    void load()
  }
)
onMounted(() => {
  void load()
  document.addEventListener('pointerdown', closeMenu)
  document.addEventListener('keydown', handleMenuKey)
  window.addEventListener('resize', closeMenu)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', closeMenu)
  document.removeEventListener('keydown', handleMenuKey)
  window.removeEventListener('resize', closeMenu)
  ++textRequestVersion
  if (focusTimer) window.clearTimeout(focusTimer)
})

defineExpose({ load })
</script>

<style scoped>
.message-context-menu { position: fixed; z-index: 4000; display: flex; flex-direction: column; min-width: 160px; max-width: calc(100vw - 16px); max-height: calc(100dvh - 16px); overflow-y: auto; padding: 5px; border: 1px solid var(--app-border); border-radius: 8px; background: #fff; box-shadow: 0 6px 24px #0002; }
.message-context-menu button { padding: 8px 14px; border: 0; border-radius: 4px; background: transparent; color: var(--app-text); text-align: left; font-size: 13px; cursor: pointer; }
.message-context-menu button:hover, .message-context-menu button:focus-visible { background: var(--app-accent-soft); }
.message-context-menu button:disabled { opacity: .5; cursor: not-allowed; }
.message-context-menu .danger-menu-item { color: var(--el-color-danger); }
.header-actions { display: flex; align-items: center; gap: 8px; }
.header-actions :deep(.el-button + .el-button) { margin-left: 0; }
.favorite-mark { color: #b7791f; font-weight: 500; }
.human .favorite-mark { color: #ffe39d; }
.message-text-result { min-height: 100px; max-height: 55vh; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; user-select: text; }
.text-result-hint { margin-top: 12px; color: var(--el-text-color-secondary); font-size: 12px; }
.content { user-select: text; -webkit-user-select: text; }
.quoted-message { margin: 0 0 8px; padding: 7px 10px; border-left: 3px solid currentColor; background: #8881; font-size: 12px; opacity: .8; overflow-wrap: anywhere; }
.revoked-message { font-style: italic; opacity: .7; }
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
  font-size: 18px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

p {
  margin-top: 4px;
  font-size: 13px;
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
  flex-wrap: wrap;
  gap: 8px;
  justify-content: space-between;
  padding: 10px 12px;
  margin: -8px -8px 14px;
  background: #fff;
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: 8px;
  box-shadow: 0 4px 12px rgb(15 23 42 / 8%);
}
.selection-toolbar > div { display: flex; flex-wrap: wrap; gap: 6px; }
.selection-toolbar :deep(.el-button + .el-button) { margin-left: 0; }

.message-row {
  display: flex;
  margin-bottom: 20px;
}

.message-row:not(.customer) {
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
  font-size: 13px;
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
  font-size: 13px;
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
  font-size: 16px;
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

.mini-program-card {
  width: min(280px, 62vw);
  overflow: hidden;
  padding: 12px;
  background: #fff;
  border: 1px solid rgb(148 163 184 / 35%);
  border-radius: 8px;
  color: var(--app-text);
}

.mini-program-name {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
  color: #6b7280;
  font-size: 12px;
}

.mini-program-name img { width: 20px; height: 20px; border-radius: 50%; }
.mini-program-card strong { display: block; margin-bottom: 10px; overflow-wrap: anywhere; }
.mini-program-cover { display: block; width: 100%; height: auto; }
.mini-program-cover-hint { display: block; padding: 24px 0; color: #6b7280; }
.mini-program-footer { display: block; margin-top: 10px; padding-top: 8px; border-top: 1px solid #eee; color: #6b7280; }

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
  font-size: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.commerce-card small {
  display: -webkit-box;
  margin-top: 5px;
  overflow: hidden;
  color: #6b7280;
  font-size: 13px;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.original-link {
  display: block;
  margin-top: 6px;
  color: inherit;
  font-size: 13px;
  text-decoration: underline;
  text-underline-offset: 3px;
}

.voice-transcription {
  max-width: 360px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--app-border);
}
.transcription-header { display: flex; align-items: center; gap: 12px; }
.voice-transcription small { color: var(--el-text-color-secondary); }
.voice-transcription > small { display: block; margin-top: 4px; }
.voice-transcription p { margin: 4px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; user-select: text; }

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
