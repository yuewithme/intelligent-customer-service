<template>
  <ElDialog v-model="visible" title="转发消息" width="min(540px, 92vw)" :close-on-click-modal="!sending" :close-on-press-escape="!sending" :show-close="!sending">
    <p class="forward-summary">逐条转发 {{ messages.length }} 条消息</p>
    <ElInput v-model="keyword" :prefix-icon="Search" clearable :disabled="sending"
      placeholder="搜索昵称、备注、微信号、聊天记录" aria-label="搜索转发用户" @keyup.enter="searchTargets()" />
    <p class="target-heading">{{ keyword.trim() ? '搜索结果' : '最近会话' }}</p>
    <div v-loading="loading" class="target-list" aria-label="转发用户列表" :aria-busy="loading">
      <ElEmpty v-if="!targets.length && !loading" :description="loadFailed ? '加载失败，请重试' : keyword.trim() ? '未找到匹配会话' : '暂无可转发会话'">
        <ElButton v-if="loadFailed" size="small" @click="searchTargets()">重新加载</ElButton>
      </ElEmpty>
      <button v-for="item in targets" :key="item.group_key" type="button" class="target-item"
        :class="{ active: item.conversation_id === targetId }" :disabled="sending || loading"
        :aria-pressed="item.conversation_id === targetId" @click="targetId = item.conversation_id">
        <ElAvatar :size="36" :src="item.user_avatar_url || undefined">{{ (item.user_display_name || item.user_id).slice(0, 1) }}</ElAvatar>
        <span class="target-main">
          <span class="target-name">{{ item.user_display_name || item.user_id }}</span>
          <span class="target-account">{{ item.owner_display_name || item.owner_wc_id || '微信' }} · {{ item.user_id }}</span>
          <span class="target-preview">{{ item.last_message || '暂无消息' }}</span>
          <span v-if="item.search_matches?.length" class="target-match">
            {{ item.search_matches.map(match => match.label).join('、') }}：{{ item.search_matches.find(match => match.field === 'message')?.text || item.search_matches[0].text }}
          </span>
          <span class="target-time">{{ formatChinaTime(item.updated_at) }}</span>
        </span>
        <span class="target-check" aria-hidden="true">{{ item.conversation_id === targetId ? '✓' : '' }}</span>
      </button>
      <ElButton v-if="page * pageSize < total" class="load-more" :disabled="sending || loading" @click="searchTargets(page + 1)">加载更多</ElButton>
    </div>
    <p v-if="selectedTarget" class="selected-target">已选择：{{ selectedTarget.user_display_name || selectedTarget.user_id }}</p>
    <p class="forward-hint">图片、视频和文件等原始微信消息需要转发到同一微信账号的会话。</p>
    <p v-if="restrictedOwner !== undefined && !targets.length && !loading" class="forward-hint">没有可用的同账号会话，请检查所选消息是否属于同一微信账号。</p>
    <template #footer>
      <ElButton :disabled="sending" @click="visible = false">取消</ElButton>
      <ElButton type="primary" :disabled="!selectedTarget || !messages.length || loading" :loading="sending" @click="forward">确认转发</ElButton>
    </template>
  </ElDialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { forwardConversationMessage, getConversations, updateReplyDraft, type ConversationItem, type ConversationMessage } from '@/api/admin/conversations'
import { useUserStore } from '@/store/modules/user'
import { groupConversationsByCustomer } from '../conversationGrouping'
import { formatChinaTime } from '@/utils/time'

const visible = defineModel<boolean>({ required: true })
const props = defineProps<{ messages: ConversationMessage[]; sourceOwners: Record<string, string> }>()
const emit = defineEmits<{ sent: [] }>()
const userStore = useUserStore()
const targetId = ref('')
const keyword = ref('')
const targetConversations = ref<ConversationItem[]>([])
const targets = computed(() => {
  const grouped = groupConversationsByCustomer(targetConversations.value)
  return keyword.value.trim() ? grouped : grouped.sort((left, right) =>
    new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime())
})
const selectedTarget = computed(() => targets.value.find(item => item.conversation_id === targetId.value))
const pageSize = 50
const page = ref(1)
const total = ref(0)
const loading = ref(false)
const loadFailed = ref(false)
const sending = ref(false)
const completed = new Set<number>()
let searchVersion = 0
let searchTimer: ReturnType<typeof setTimeout> | undefined
const restrictedOwner = computed(() => {
  const owners = props.messages.filter(message => message.actions?.forward_same_account).map(message => props.sourceOwners[message.conversation_id] || '')
  if (!owners.length) return undefined
  return owners[0] && owners.every(owner => owner === owners[0]) ? owners[0] : ''
})

const searchTargets = async (requestedPage = 1) => {
  clearTimeout(searchTimer)
  const version = ++searchVersion
  loading.value = true
  loadFailed.value = false
  if (requestedPage === 1) {
    targetConversations.value = []
    targetId.value = ''
    total.value = 0
    page.value = 1
  }
  try {
    if (restrictedOwner.value === '') return
    const result = await getConversations({
      page: requestedPage, page_size: pageSize, channel: 'wechat',
      keyword: keyword.value.trim() || undefined,
      for_forwarding: true, owner_wc_id: restrictedOwner.value,
    })
    if (version !== searchVersion || !visible.value) return
    targetConversations.value = requestedPage === 1 ? result.items : [...targetConversations.value, ...result.items]
    page.value = result.page
    total.value = result.total
  } catch {
    if (version === searchVersion) loadFailed.value = true
  } finally {
    if (version === searchVersion) loading.value = false
  }
}
watch(visible, value => {
  ++searchVersion
  clearTimeout(searchTimer)
  targetId.value = ''
  completed.clear()
  if (value) void searchTargets()
  else {
    keyword.value = ''
    targetConversations.value = []
    loading.value = false
  }
})
watch(keyword, (value, previous) => {
  if (!visible.value || value.trim() === previous.trim()) return
  ++searchVersion
  clearTimeout(searchTimer)
  targetId.value = ''
  targetConversations.value = []
  total.value = 0
  loading.value = true
  searchTimer = setTimeout(() => { void searchTargets() }, 250)
})
watch(restrictedOwner, () => { if (visible.value) void searchTargets() })
watch(targetId, () => completed.clear())
onBeforeUnmount(() => { ++searchVersion; clearTimeout(searchTimer) })
const forward = async () => {
  if (!selectedTarget.value || loading.value || sending.value) return
  const conversationId = targetId.value
  const messages = [...props.messages]
  const operatorId = userStore.user.nickname || 'admin'
  const editorId = crypto.randomUUID()
  sending.value = true
  let acquired = false
  try {
    if (selectedTarget.value.status !== 'human_active') {
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
.forward-summary { margin-top: 0; }
.target-heading { margin: 14px 0 8px; color: var(--app-text-secondary); font-size: 13px; }
.target-list { min-height: 220px; max-height: min(430px, 48dvh); overflow-y: auto; border: 1px solid var(--app-border); border-radius: 8px; }
.target-item { display: flex; align-items: flex-start; gap: 10px; width: 100%; padding: 12px; border: 0; border-bottom: 1px solid var(--app-border); background: transparent; color: var(--app-text); text-align: left; cursor: pointer; }
.target-item:hover, .target-item.active { background: var(--app-accent-soft); }
.target-item:disabled { cursor: default; }
.target-main { display: flex; flex: 1; flex-direction: column; gap: 3px; min-width: 0; }
.target-name { font-size: 14px; font-weight: 600; }
.target-name, .target-account, .target-preview, .target-match { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.target-account, .target-time { color: var(--app-text-secondary); font-size: 12px; }
.target-preview { font-size: 13px; }
.target-match { color: var(--app-accent); font-size: 12px; }
.target-check { flex-shrink: 0; width: 18px; color: var(--app-accent); font-weight: 600; }
.load-more { display: block; margin: 10px auto; }
.selected-target { margin-bottom: 0; color: var(--app-accent); font-size: 13px; }
.forward-hint { color: var(--app-text-secondary); font-size: 12px; line-height: 1.6; }
</style>
