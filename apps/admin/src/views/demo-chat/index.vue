<template>
  <main class="demo-page">
    <section class="chat-shell">
      <header class="chat-header">
        <div>
          <p class="eyebrow">在线体验</p>
          <h1>销售 Agent 测试台</h1>
          <p>像真实客户一样提问，体验知识问答、需求理解和销售推进。</p>
        </div>
        <a
          class="admin-link"
          href="/gate?redirect=/workbench"
        >进入管理后台</a>
      </header>

      <div class="customer-bar">
        <ElInput v-model="customerName" placeholder="客户昵称" maxlength="128" />
        <ElInput v-model="customerId" placeholder="客户编号" maxlength="128" />
        <ElButton :loading="loading" @click="switchConversation()">切换到该会话</ElButton>
        <ElButton :loading="loading" type="primary" plain @click="newConversation()">
          新建会话
        </ElButton>
        <p class="shared-session-hint">
          当前会话在所有设备间共享；只有点击“切换”或“新建”才会更换。
        </p>
      </div>

      <div ref="messageListRef" class="message-list">
        <div v-if="!messages.length" class="welcome-card">
          <h2>开始一段真实的销售对话</h2>
          <p>例如：“我是新手，预算 300 元，想买一盆好养的兰花。”</p>
        </div>
        <div
          v-for="item in messages"
          :key="item.id"
          class="message-row"
          :class="item.role"
        >
          <div class="bubble">
            <span class="sender">{{ item.role === 'customer' ? customerName || '客户' : '销售 Agent' }}</span>
            <p v-if="item.content">{{ item.content }}</p>
            <ElImage
              v-if="item.imageUrl"
              class="opening-image"
              :src="item.imageUrl"
              :preview-src-list="[item.imageUrl]"
              fit="cover"
              preview-teleported
            />
            <video
              v-else-if="item.videoUrl"
              class="message-video"
              :src="item.videoUrl"
              controls
            ></video>
            <audio
              v-else-if="item.audioUrl"
              class="message-audio"
              :src="item.audioUrl"
              controls
            ></audio>
            <a
              v-if="item.card?.url"
              class="message-card"
              :href="item.card.url"
              target="_blank"
              rel="noreferrer"
            >
              <img v-if="item.card.thumbUrl" :src="item.card.thumbUrl" alt="" />
              <span>
                <strong>{{ item.card.title }}</strong>
                <small>{{ item.card.description }}</small>
              </span>
            </a>
            <div v-else-if="item.card" class="message-card">
              <img v-if="item.card.thumbUrl" :src="item.card.thumbUrl" alt="" />
              <span>
                <strong>{{ item.card.title }}</strong>
                <small>{{ item.card.description }}</small>
              </span>
            </div>
            <a
              v-else-if="item.fileUrl"
              class="message-file"
              :href="item.fileUrl"
              target="_blank"
              rel="noreferrer"
            >打开附件</a>
          </div>
        </div>
      </div>

      <aside v-if="latestResult" class="agent-state">
        <span>运行方式：小兰自主 Agent</span>
        <span>下一步：{{ latestResult.next_action || '继续了解客户需求' }}</span>
        <span v-if="latestResult.need_human" class="warning">建议转人工</span>
      </aside>

      <form class="composer" @submit.prevent="sendMessage">
        <ElInput
          v-model="draft"
          type="textarea"
          :rows="3"
          resize="none"
          maxlength="4000"
          show-word-limit
          placeholder="输入客户想说的话，Ctrl / Cmd + Enter 发送"
          @keydown.ctrl.enter.prevent="sendMessage"
          @keydown.meta.enter.prevent="sendMessage"
        />
        <ElButton type="primary" native-type="submit" :loading="loading">发送消息</ElButton>
      </form>
    </section>
  </main>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  chatWithDemoSalesAgent,
  getActiveDemoConversation,
  openDemoSalesConversation,
  type DemoHistoryMessage,
  type DemoOutboundMessage,
  type DemoChatResponse
} from '@/api/demo'

interface MessageCard {
  title: string
  description: string
  thumbUrl?: string
  url?: string
}

interface ChatMessage {
  id: number
  role: 'customer' | 'agent'
  content: string
  imageUrl?: string
  videoUrl?: string
  audioUrl?: string
  fileUrl?: string
  card?: MessageCard
}

const customerName = ref('测试客户')
const customerId = ref(`visitor_${Date.now().toString(36)}`)
const conversationId = ref('')
const draft = ref('')
const loading = ref(false)
const messages = ref<ChatMessage[]>([])
const latestResult = ref<DemoChatResponse>()
const messageListRef = ref<HTMLElement>()
let messageId = 0
let historyPoll: ReturnType<typeof setInterval> | undefined

const scrollToBottom = async () => {
  await nextTick()
  if (messageListRef.value) {
    messageListRef.value.scrollTop = messageListRef.value.scrollHeight
  }
}

const objectValue = (value: unknown): Record<string, unknown> | undefined => {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  if (typeof value !== 'string') return undefined
  try {
    const parsed = JSON.parse(value) as unknown
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : undefined
  } catch {
    return undefined
  }
}

const firstText = (value: Record<string, unknown>, keys: string[]) => {
  for (const key of keys) {
    const item = value[key]
    if (typeof item === 'string' && item.trim()) return item.trim()
  }
  return ''
}

const parseCard = (message: DemoOutboundMessage): MessageCard => {
  const value = objectValue(message.card) || objectValue(message.content) || {}
  const miniProgram = message.type === 'mini_program'
  return {
    title: firstText(value, ['title', 'name']) || (miniProgram ? '微信小程序' : '查看详情'),
    description:
      firstText(value, ['description', 'desc', 'summary']) ||
      (miniProgram ? '商品小程序卡片' : ''),
    thumbUrl:
      firstText(value, ['thumb_url', 'thumbUrl', 'image_url', 'imageUrl', 'pic_url']) ||
      undefined,
    url: firstText(value, ['url', 'jump_url', 'link']) || undefined
  }
}

const mediaSource = (message: DemoOutboundMessage) => {
  const media = objectValue(message.media)
  const source = media ? firstText(media, ['url', 'preview_url', 'original_url']) : ''
  if (source) return source
  const contentValue = objectValue(message.content)
  const contentSource = contentValue
    ? firstText(contentValue, ['url', 'preview_url', 'original_url'])
    : message.content.trim()
  return /^(https?:|data:|\/)/i.test(contentSource) ? contentSource : ''
}

const appendAgentMessages = (result: DemoChatResponse) => {
  const outbound = result.outbound_messages || []
  if (!outbound.length) {
    if (result.reply) {
      messages.value.push({ id: ++messageId, role: 'agent', content: result.reply })
    }
    return
  }
  for (const item of outbound) {
    messages.value.push(toChatMessage(item, ++messageId, 'agent'))
  }
}

const toChatMessage = (
  item: DemoOutboundMessage,
  id: number,
  role: ChatMessage['role']
): ChatMessage => {
  const type = item.type.toLowerCase()
  if (['image', 'received_image', 'emoji'].includes(type)) {
    const source = mediaSource(item)
    return source
      ? { id, role, content: '', imageUrl: source }
      : { id, role, content: item.content || '[图片]' }
  }
  if (['video', 'received_video'].includes(type)) {
    const source = mediaSource(item)
    return source
      ? { id, role, content: '', videoUrl: source }
      : { id, role, content: item.content || '[视频]' }
  }
  if (type === 'audio') {
    const source = mediaSource(item)
    return source
      ? { id, role, content: '', audioUrl: source }
      : { id, role, content: item.content || '[语音]' }
  }
  if (type === 'file' || type === 'material') {
    const source = mediaSource(item)
    if (/^(https?:|data:)/i.test(source)) {
      return { id, role, content: '', fileUrl: source }
    }
  }
  if (type === 'link_card' || type === 'mini_program') {
    return { id, role, content: '', card: parseCard(item) }
  }
  return { id, role, content: item.content }
}

const restoreActiveConversation = async () => {
  const result = await getActiveDemoConversation()
  customerId.value = result.customer_id
  customerName.value = result.customer_name
  conversationId.value = result.conversation_id
  latestResult.value = undefined
  messages.value = result.messages.map((item: DemoHistoryMessage) =>
    toChatMessage(item, item.id, item.role)
  )
  messageId = Math.max(0, ...messages.value.map((item) => item.id))
  await scrollToBottom()
}

const refreshActiveConversation = async () => {
  if (loading.value) return
  const result = await getActiveDemoConversation()
  const refreshed = result.messages.map((item: DemoHistoryMessage) =>
    toChatMessage(item, item.id, item.role)
  )
  if (refreshed.length === messages.value.length) return
  messages.value = refreshed
  messageId = Math.max(0, ...messages.value.map((item) => item.id))
  await scrollToBottom()
}

const switchConversation = async (showSuccess = true) => {
  if (loading.value) return
  const nextCustomerId = customerId.value.trim()
  if (!nextCustomerId) return ElMessage.warning('请输入客户编号')
  loading.value = true
  try {
    await openDemoSalesConversation({
      customer_id: nextCustomerId,
      customer_name: customerName.value.trim() || undefined
    })
    await restoreActiveConversation()
    if (showSuccess) ElMessage.success('已切换到该测试会话')
  } finally {
    loading.value = false
  }
}

const newConversation = async () => {
  customerId.value = `visitor_${Date.now().toString(36)}`
  await switchConversation(false)
  ElMessage.success('已创建并切换到新会话')
}

const sendMessage = async () => {
  const content = draft.value.trim()
  if (!content || loading.value) return
  messages.value.push({ id: ++messageId, role: 'customer', content })
  draft.value = ''
  loading.value = true
  await scrollToBottom()
  try {
    const result = await chatWithDemoSalesAgent({
      customer_id: customerId.value.trim(),
      customer_name: customerName.value.trim() || undefined,
      conversation_id: conversationId.value || undefined,
      message: content
    })
    conversationId.value = result.conversation_id
    latestResult.value = result
    appendAgentMessages(result)
  } catch {
    await restoreActiveConversation()
    ElMessage.warning('当前会话已同步，请重新发送刚才的消息')
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}

onMounted(async () => {
  loading.value = true
  try {
    await restoreActiveConversation()
  } finally {
    loading.value = false
  }
  historyPoll = setInterval(() => {
    void refreshActiveConversation()
  }, 10_000)
})

onBeforeUnmount(() => {
  if (historyPoll) clearInterval(historyPoll)
})
</script>

<style scoped>
.demo-page {
  min-height: 100vh;
  padding: 24px;
  background:
    radial-gradient(circle at 10% 0%, rgb(43 120 94 / 16%), transparent 34%),
    linear-gradient(145deg, #edf7f3, #f7f5ed 52%, #edf2f7);
}

.chat-shell {
  display: grid;
  grid-template-rows: auto auto minmax(360px, 1fr) auto auto;
  width: min(980px, 100%);
  min-height: calc(100vh - 48px);
  margin: 0 auto;
  overflow: hidden;
  background: rgb(255 255 255 / 94%);
  border: 1px solid rgb(31 77 62 / 12%);
  border-radius: 24px;
  box-shadow: 0 24px 80px rgb(35 69 58 / 14%);
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 28px 32px 22px;
  color: #f8fffc;
  background: linear-gradient(120deg, #164f3c, #28785c);
}

.chat-header h1,
.chat-header p {
  margin: 0;
}

.chat-header h1 {
  margin: 4px 0 8px;
  font-size: 28px;
}

.chat-header .eyebrow {
  color: #bfe8d7;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.16em;
}

.admin-link {
  flex: none;
  padding: 10px 16px;
  color: #164f3c;
  text-decoration: none;
  background: #fff;
  border-radius: 999px;
}

.customer-bar {
  display: grid;
  grid-template-columns: 1fr 1fr auto auto;
  gap: 12px;
  padding: 16px 24px;
  border-bottom: 1px solid #e8ecea;
}

.shared-session-hint {
  grid-column: 1 / -1;
  margin: 0;
  color: #6d7d77;
  font-size: 12px;
}

.message-list {
  padding: 24px;
  overflow: auto;
  background: #f7faf8;
}

.welcome-card {
  max-width: 560px;
  padding: 24px;
  margin: 60px auto;
  text-align: center;
  color: #365247;
  background: #fff;
  border: 1px dashed #9dbdaf;
  border-radius: 18px;
}

.welcome-card h2 {
  margin: 0 0 10px;
}

.message-row {
  display: flex;
  margin-bottom: 18px;
}

.message-row.customer {
  justify-content: flex-end;
}

.bubble {
  max-width: min(72%, 660px);
  padding: 12px 16px;
  color: #263c34;
  background: #fff;
  border: 1px solid #e0e8e4;
  border-radius: 6px 18px 18px;
  box-shadow: 0 8px 24px rgb(24 62 49 / 7%);
}

.customer .bubble {
  color: #fff;
  background: #28785c;
  border-color: #28785c;
  border-radius: 18px 6px 18px 18px;
}

.bubble p {
  margin: 5px 0 0;
  line-height: 1.7;
  white-space: pre-wrap;
}

.opening-image {
  display: block;
  width: min(100%, 360px);
  margin-top: 12px;
  border-radius: 12px;
}

.message-video {
  display: block;
  width: min(100%, 420px);
  max-height: 360px;
  margin-top: 12px;
  border-radius: 12px;
}

.message-audio {
  display: block;
  width: min(100%, 360px);
  margin-top: 12px;
}

.message-file {
  display: inline-block;
  margin-top: 8px;
  color: inherit;
  text-decoration: underline;
}

.message-card {
  display: flex;
  width: min(360px, 64vw);
  min-height: 76px;
  overflow: hidden;
  color: inherit;
  text-decoration: none;
  background: #f8faf9;
  border: 1px solid #dce7e2;
  border-radius: 10px;
}

.message-card img {
  width: 92px;
  object-fit: cover;
  background: #edf2ef;
}

.message-card span {
  display: flex;
  flex: 1;
  flex-direction: column;
  justify-content: center;
  padding: 10px 12px;
}

.message-card strong { font-size: 14px; }
.message-card small { margin-top: 5px; color: #6d7d77; line-height: 1.45; }

.sender {
  font-size: 12px;
  font-weight: 700;
  opacity: 0.72;
}

.agent-state {
  display: flex;
  gap: 18px;
  padding: 10px 24px;
  overflow: auto;
  color: #4b6259;
  font-size: 13px;
  border-top: 1px solid #e8ecea;
}

.warning {
  color: #b54708;
}

.composer {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 12px;
  align-items: end;
  padding: 18px 24px 24px;
}

@media (max-width: 720px) {
  .demo-page {
    padding: 0;
  }

  .chat-shell {
    min-height: 100vh;
    border-radius: 0;
  }

  .chat-header {
    align-items: flex-start;
    padding: 22px 18px;
  }

  .customer-bar {
    grid-template-columns: 1fr;
  }

  .bubble {
    max-width: 88%;
  }

  .composer {
    grid-template-columns: 1fr;
  }
}
</style>
