<template>
  <ElDialog v-model="visible" title="转发消息" width="min(460px, 92vw)" :close-on-click-modal="!sending" :close-on-press-escape="!sending" :show-close="!sending">
    <p class="forward-summary">逐条转发 {{ messages.length }} 条消息</p>
    <ElSelect v-model="targetId" filterable remote reserve-keyword :remote-method="searchTargets" :loading="loading"
      :disabled="sending" placeholder="搜索客户或群聊" class="target-select" @visible-change="value => value && searchTargets('')">
      <ElOption v-for="item in targets" :key="item.conversation_id" :value="item.conversation_id"
        :label="`${item.user_display_name || item.user_id} · ${item.owner_display_name || item.owner_wc_id || '微信'}`" />
    </ElSelect>
    <p class="forward-hint">图片、视频和文件等原始微信消息需要转发到同一微信账号的会话。</p>
    <template #footer>
      <ElButton :disabled="sending" @click="visible = false">取消</ElButton>
      <ElButton type="primary" :disabled="!targetId || !messages.length" :loading="sending" @click="forward">确认转发</ElButton>
    </template>
  </ElDialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { forwardConversationMessage, getConversations, updateReplyDraft, type ConversationItem, type ConversationMessage } from '@/api/admin/conversations'
import { useUserStore } from '@/store/modules/user'

const visible = defineModel<boolean>({ required: true })
const props = defineProps<{ messages: ConversationMessage[] }>()
const emit = defineEmits<{ sent: [] }>()
const userStore = useUserStore()
const targetId = ref('')
const targets = ref<ConversationItem[]>([])
const loading = ref(false)
const sending = ref(false)
const completed = new Set<number>()
let searchVersion = 0

watch(visible, value => {
  if (value) { targetId.value = ''; completed.clear(); void searchTargets('') }
  else ++searchVersion
})
watch(targetId, () => completed.clear())
const searchTargets = async (keyword: string) => {
  const version = ++searchVersion
  loading.value = true
  try {
    const result = await getConversations({ page: 1, page_size: 100, channel: 'wechat', keyword: keyword.trim() || undefined })
    if (version === searchVersion) targets.value = result.items.filter(item => item.status !== 'resolved')
  } catch {
    if (version === searchVersion) targets.value = []
  } finally {
    if (version === searchVersion) loading.value = false
  }
}
const forward = async () => {
  if (!targetId.value || sending.value) return
  const conversationId = targetId.value
  const messages = [...props.messages]
  const operatorId = userStore.user.nickname || 'admin'
  const editorId = crypto.randomUUID()
  sending.value = true
  let acquired = false
  try {
    if (targets.value.find(item => item.conversation_id === conversationId)?.status !== 'human_active') {
      await updateReplyDraft(conversationId, { operator_id: operatorId, editor_id: editorId, active: true })
      acquired = true
    }
    for (const message of messages) {
      if (completed.has(message.id)) continue
      await forwardConversationMessage(conversationId, operatorId, message.id)
      completed.add(message.id)
    }
    ElMessage.success('消息已进入转发队列')
    visible.value = false
    emit('sent')
  } catch {
    if (completed.size) ElMessage.warning(`已有 ${completed.size} 条加入队列，再次确认会继续转发剩余消息`)
  } finally {
    if (acquired) await updateReplyDraft(conversationId, { operator_id: operatorId, editor_id: editorId, active: false }).catch(() => undefined)
    sending.value = false
  }
}
</script>

<style scoped>
.target-select { width: 100%; }
.forward-summary { margin-top: 0; }
.forward-hint { color: var(--app-text-secondary); font-size: 12px; line-height: 1.6; }
</style>
