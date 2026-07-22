<template>
  <ContentWrap>
    <div class="page-head">
      <div>
        <h1>目前活动</h1>
        <p v-if="sendMode">
          正在为 <strong>{{ customerName }}</strong> 选择活动，发送前会再次校验人工接管状态。
        </p>
        <p v-else>管理从会话中保存的活动消息包。</p>
      </div>
      <div class="head-actions">
        <ElButton v-if="sendMode" @click="backToConversation">返回原会话</ElButton>
        <ElButton :icon="Refresh" circle @click="load" />
      </div>
    </div>

    <div class="filters">
      <ElInput
        v-model="keyword"
        clearable
        placeholder="搜索活动名称或说明"
        @clear="load"
        @keyup.enter="load"
      />
      <ElSelect v-if="!sendMode" v-model="status" clearable placeholder="全部状态" @change="load">
        <ElOption label="已启用" value="published" />
        <ElOption label="已归档" value="archived" />
      </ElSelect>
      <ElAlert
        v-if="!sendMode"
        :title="readOnly ? '测试身份为只读模式' : '新活动请先在销售工作台中右键选择消息，再点击“存为活动”'"
        type="info"
        :closable="false"
      />
    </div>

    <div v-loading="loading" class="activity-grid">
      <ElEmpty v-if="!activities.length && !loading" description="暂无可用活动" />
      <article
        v-for="activity in activities"
        :key="activity.id"
        class="activity-card"
        :class="{ selected: selectedActivityId === activity.id }"
        @click="sendMode && (selectedActivityId = activity.id)"
      >
        <div class="card-head">
          <div>
            <h2>{{ activity.title }}</h2>
            <p>{{ activity.summary || '暂无活动说明' }}</p>
          </div>
          <ElTag :type="statusType(activity.effective_status)">
            {{ statusText(activity.effective_status) }}
          </ElTag>
        </div>

        <div class="message-preview">
          <div v-for="item in activity.items.slice(0, 3)" :key="item.position" class="preview-item">
            <span class="position">{{ item.position }}</span>
            <span v-if="item.type === 'text'">{{ item.content }}</span>
            <ElImage
              v-else-if="item.type === 'received_image' && item.preview_url"
              class="thumb"
              :src="item.preview_url"
              fit="cover"
            />
            <span v-else>{{ item.type === 'received_video' ? '视频消息' : '媒体消息' }}</span>
          </div>
          <span v-if="activity.item_count > 3" class="more">还有 {{ activity.item_count - 3 }} 条</span>
        </div>

        <dl>
          <dt>有效时间</dt>
          <dd>{{ validityText(activity) }}</dd>
          <dt>消息数量</dt>
          <dd>{{ activity.item_count }} 条</dd>
          <dt>更新时间</dt>
          <dd>{{ formatTime(activity.updated_at) }}</dd>
        </dl>

        <div v-if="!sendMode && !readOnly" class="switches" @click.stop>
          <label>
            总开关
            <ElSwitch
              v-model="activity.enabled"
              :disabled="activity.status === 'archived'"
              @change="toggleEnabled(activity)"
            />
          </label>
          <label>
            允许 AI
            <ElSwitch
              v-model="activity.ai_enabled"
              :disabled="activity.status === 'archived'"
              @change="toggleAi(activity)"
            />
          </label>
        </div>

        <div class="card-actions" @click.stop>
          <ElButton size="small" @click="openPreview(activity)">预览</ElButton>
          <template v-if="!sendMode">
            <ElButton
              v-if="!readOnly"
              size="small"
              :disabled="activity.status === 'archived' || activity.enabled"
              @click="openEdit(activity)"
            >
              编辑
            </ElButton>
            <ElButton
              v-if="!readOnly && activity.status === 'archived'"
              size="small"
              type="primary"
              @click="restart(activity)"
            >
              重新启动
            </ElButton>
            <ElButton size="small" @click="openLogs(activity)">发送记录</ElButton>
            <ElButton
              v-if="!readOnly && activity.status !== 'archived'"
              size="small"
              type="danger"
              plain
              @click="archive(activity)"
            >
              归档
            </ElButton>
          </template>
          <ElButton
            v-else
            size="small"
            type="primary"
            :disabled="readOnly || selectedActivityId !== activity.id"
            @click="confirmSend(activity)"
          >
            预览并发送
          </ElButton>
        </div>
      </article>
    </div>

    <ElDialog v-model="previewVisible" :title="previewActivity?.title || '活动预览'" width="620px">
      <div class="full-preview">
        <div v-for="item in previewActivity?.items || []" :key="item.position" class="full-item">
          <strong>{{ item.position }}.</strong>
          <span v-if="item.type === 'text'">{{ item.content }}</span>
          <ElImage
            v-else-if="item.type === 'received_image' && item.preview_url"
            class="full-image"
            :src="item.preview_url"
            :preview-src-list="[item.preview_url]"
            fit="contain"
          />
          <video
            v-else-if="item.type === 'received_video' && item.preview_url"
            class="full-video"
            :src="item.preview_url"
            controls
          ></video>
          <span v-else>{{ item.type === 'received_video' ? '视频消息' : '图片消息' }}</span>
        </div>
      </div>
    </ElDialog>

    <ElDialog v-model="editVisible" title="编辑活动" width="520px">
      <ElForm label-position="top">
        <ElFormItem label="活动名称"><ElInput v-model="editForm.title" /></ElFormItem>
        <ElFormItem label="活动说明">
          <ElInput v-model="editForm.summary" type="textarea" :rows="3" />
        </ElFormItem>
        <ElFormItem label="开始时间（可选）">
          <ElDatePicker
            v-model="editForm.valid_from"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ssZ"
          />
        </ElFormItem>
        <ElFormItem label="结束时间（留空为长期）">
          <ElDatePicker
            v-model="editForm.valid_until"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ssZ"
          />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="editVisible = false">取消</ElButton>
        <ElButton type="primary" :loading="saving" @click="saveEdit">保存</ElButton>
      </template>
    </ElDialog>

    <ElDrawer v-model="logsVisible" title="发送记录" size="520px">
      <ElEmpty v-if="!sendLogs.length" description="暂无发送记录" />
      <ElTimeline v-else>
        <ElTimelineItem v-for="log in sendLogs" :key="log.id" :timestamp="formatTime(log.created_at)">
          <div>{{ log.trigger_mode === 'manual' ? '人工发送' : 'AI 发送' }} · {{ log.status }}</div>
          <div v-if="log.last_error" class="error-text">{{ log.last_error }}</div>
        </ElTimelineItem>
      </ElTimeline>
    </ElDrawer>
  </ContentWrap>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  archiveActivity,
  getActivities,
  getActivitySendLogs,
  publishActivity,
  sendActivity,
  updateActivity,
  updateActivitySwitches,
  type ActivityEffectiveStatus,
  type ActivityItem,
  type ActivitySendLog
} from '@/api/admin/activities'
import { getConversationDetail } from '@/api/admin/conversations'
import { useUserStore } from '@/store/modules/user'
import { formatChinaTime } from '../workbench/time'
import { isTestGate } from '@/utils/gate'

defineOptions({ name: 'CurrentActivities' })

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const readOnly = isTestGate()
const conversationId = computed(() =>
  typeof route.query.conversation_id === 'string' ? route.query.conversation_id : ''
)
const sendMode = computed(() => Boolean(conversationId.value))
const operatorId = computed(() => userStore.user.nickname || 'admin')
const loading = ref(false)
const saving = ref(false)
const keyword = ref('')
const status = ref('')
const activities = ref<ActivityItem[]>([])
const selectedActivityId = ref<number>()
const customerName = ref('当前客户')
const previewVisible = ref(false)
const previewActivity = ref<ActivityItem>()
const editVisible = ref(false)
const editingActivity = ref<ActivityItem>()
const editForm = reactive({ title: '', summary: '', valid_from: '', valid_until: '' })
const logsVisible = ref(false)
const sendLogs = ref<ActivitySendLog[]>([])

const load = async () => {
  loading.value = true
  try {
    const data = await getActivities({
      page: 1,
      page_size: 100,
      status: status.value || undefined,
      keyword: keyword.value.trim() || undefined,
      conversation_id: conversationId.value || undefined
    })
    activities.value = data.items
    if (selectedActivityId.value && !data.items.some((item) => item.id === selectedActivityId.value)) {
      selectedActivityId.value = undefined
    }
  } finally {
    loading.value = false
  }
}

const loadCustomer = async () => {
  if (!conversationId.value) return
  const detail = await getConversationDetail(conversationId.value)
  customerName.value = detail.conversation.user_display_name || detail.conversation.user_id
}

const toggleEnabled = async (activity: ActivityItem) => {
  try {
    await updateActivitySwitches(activity.id, {
      operator_id: operatorId.value,
      enabled: activity.enabled
    })
    ElMessage.success(activity.enabled ? '活动已打开' : '活动已关闭')
  } catch {
    await load()
  }
}

const toggleAi = async (activity: ActivityItem) => {
  try {
    await updateActivitySwitches(activity.id, {
      operator_id: operatorId.value,
      ai_enabled: activity.ai_enabled
    })
    ElMessage.success(activity.ai_enabled ? '已允许 AI 使用' : '已关闭 AI 使用')
  } catch {
    await load()
  }
}

const restart = async (activity: ActivityItem) => {
  await publishActivity(activity.id, operatorId.value)
  ElMessage.success('活动已重新启动')
  await load()
}

const archive = async (activity: ActivityItem) => {
  await ElMessageBox.confirm('归档后活动不可继续发送，确认归档？', '归档活动', {
    type: 'warning'
  })
  await archiveActivity(activity.id, operatorId.value)
  ElMessage.success('活动已归档')
  await load()
}

const openPreview = (activity: ActivityItem) => {
  previewActivity.value = activity
  previewVisible.value = true
}

const openEdit = (activity: ActivityItem) => {
  editingActivity.value = activity
  editForm.title = activity.title
  editForm.summary = activity.summary || ''
  editForm.valid_from = activity.valid_from || ''
  editForm.valid_until = activity.valid_until || ''
  editVisible.value = true
}

const saveEdit = async () => {
  if (!editingActivity.value || !editForm.title.trim()) return
  saving.value = true
  try {
    await updateActivity(editingActivity.value.id, {
      title: editForm.title.trim(),
      summary: editForm.summary.trim() || undefined,
      operator_id: operatorId.value,
      valid_from: editForm.valid_from || undefined,
      valid_until: editForm.valid_until || undefined,
      ai_rules: editingActivity.value.ai_rules
    })
    ElMessage.success('活动已更新')
    editVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

const openLogs = async (activity: ActivityItem) => {
  const data = await getActivitySendLogs(activity.id)
  sendLogs.value = data.items
  logsVisible.value = true
}

const confirmSend = async (activity: ActivityItem) => {
  await ElMessageBox.confirm(
    `将按顺序向 ${customerName.value} 发送 ${activity.item_count} 条消息，确认发送？`,
    '发送活动',
    { type: 'warning' }
  )
  await sendActivity(activity.id, conversationId.value, operatorId.value)
  ElMessage.success('活动已进入发送队列')
}

const backToConversation = () => {
  void router.push({ name: 'Workbench', query: { conversation_id: conversationId.value } })
}

const validityText = (activity: ActivityItem) => {
  if (!activity.valid_until) return '长期有效'
  return `${activity.valid_from ? formatTime(activity.valid_from) : '发布后'} 至 ${formatTime(activity.valid_until)}`
}

const statusText = (value: ActivityEffectiveStatus) =>
  ({
    published: '已启用',
    archived: '已归档',
    active: '进行中',
    disabled: '已关闭',
    scheduled: '未开始',
    expired: '已过期'
  })[value] || value

const statusType = (value: ActivityEffectiveStatus) =>
  ({
    published: 'success',
    archived: 'info',
    active: 'success',
    disabled: 'warning',
    scheduled: 'warning',
    expired: 'danger'
  })[value] as 'success' | 'info' | 'warning' | 'danger'

const formatTime = formatChinaTime

onMounted(() => {
  void Promise.all([load(), loadCustomer()])
})
</script>

<style scoped>
.page-head,
.card-head,
.head-actions,
.switches,
.card-actions {
  display: flex;
  align-items: center;
}

.page-head,
.card-head {
  justify-content: space-between;
  gap: 16px;
}

h1,
h2,
p {
  margin: 0;
}

h1 {
  font-size: 22px;
}

h2 {
  font-size: 17px;
}

.page-head p,
.card-head p {
  margin-top: 6px;
  color: #6b7280;
}

.head-actions,
.switches,
.card-actions {
  gap: 10px;
}

.filters {
  display: grid;
  grid-template-columns: minmax(240px, 360px) 180px 1fr;
  gap: 12px;
  margin: 20px 0;
}

.activity-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
  min-height: 220px;
}

.activity-card {
  padding: 18px;
  cursor: default;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
}

.activity-card.selected {
  border-color: #2563eb;
  box-shadow: 0 0 0 2px rgb(37 99 235 / 12%);
}

.message-preview {
  min-height: 86px;
  padding: 10px;
  margin: 14px 0;
  background: #f8fafc;
  border-radius: 6px;
}

.preview-item {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.position {
  display: inline-grid;
  flex: 0 0 20px;
  height: 20px;
  color: #2563eb;
  background: #dbeafe;
  border-radius: 50%;
  place-items: center;
}

.thumb {
  width: 56px;
  height: 42px;
  border-radius: 4px;
}

.more {
  color: #6b7280;
  font-size: 12px;
}

dl {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: 8px;
  margin: 0 0 14px;
  font-size: 13px;
}

dt {
  color: #6b7280;
}

dd {
  margin: 0;
}

.switches {
  padding-top: 12px;
  border-top: 1px solid #e5e7eb;
}

.switches label {
  display: flex;
  align-items: center;
  gap: 6px;
}

.card-actions {
  flex-wrap: wrap;
  margin-top: 14px;
}

.full-item {
  display: grid;
  grid-template-columns: 24px 1fr;
  gap: 8px;
  padding: 12px 0;
  border-bottom: 1px solid #e5e7eb;
  white-space: pre-wrap;
}

.full-image,
.full-video {
  width: min(360px, 100%);
  max-height: 320px;
}

.error-text {
  margin-top: 4px;
  color: #dc2626;
  font-size: 12px;
}

@media (max-width: 900px) {
  .filters {
    grid-template-columns: 1fr;
  }
}
</style>
