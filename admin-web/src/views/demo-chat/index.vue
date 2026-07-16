<template>
  <main class="demo-page">
    <section class="chat-shell">
      <header class="chat-header">
        <div>
          <p class="eyebrow">在线体验</p>
          <h1>销售 Agent 测试台</h1>
          <p>像真实客户一样提问，体验知识问答、需求理解和销售推进。</p>
        </div>
        <a class="admin-link" href="/demo-admin">查看测试后台</a>
      </header>

      <div class="customer-bar">
        <ElInput v-model="customerName" placeholder="客户昵称" maxlength="128" />
        <ElInput v-model="customerId" placeholder="客户编号" maxlength="128" />
        <ElButton @click="newConversation">新建会话</ElButton>
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
            <p>{{ item.content }}</p>
          </div>
        </div>
        <div v-if="loading" class="message-row agent">
          <div class="bubble thinking">销售 Agent 正在思考…</div>
        </div>
      </div>

      <aside v-if="latestResult" class="agent-state">
        <span>销售阶段：{{ latestResult.sales_stage || '识别中' }}</span>
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
import { nextTick, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  chatWithDemoSalesAgent,
  type DemoChatResponse
} from '@/api/demo'

interface ChatMessage {
  id: number
  role: 'customer' | 'agent'
  content: string
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

const scrollToBottom = async () => {
  await nextTick()
  if (messageListRef.value) {
    messageListRef.value.scrollTop = messageListRef.value.scrollHeight
  }
}

const newConversation = () => {
  conversationId.value = ''
  messages.value = []
  latestResult.value = undefined
  customerId.value = `visitor_${Date.now().toString(36)}`
  ElMessage.success('已创建新的测试客户')
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
    messages.value.push({ id: ++messageId, role: 'agent', content: result.reply })
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}
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
  grid-template-columns: 1fr 1fr auto;
  gap: 12px;
  padding: 16px 24px;
  border-bottom: 1px solid #e8ecea;
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

.sender {
  font-size: 12px;
  font-weight: 700;
  opacity: 0.72;
}

.thinking {
  color: #60786e;
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
