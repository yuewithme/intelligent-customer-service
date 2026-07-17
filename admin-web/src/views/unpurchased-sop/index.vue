<template>
  <ContentWrap>
    <div class="page-head">
      <div>
        <h1>未购 SOP</h1>
        <p>按加好友天数触达未购买客户；抖音已购或微信已购客户会自动退出。</p>
      </div>
      <div class="head-actions">
        <ElButton :loading="syncing" @click="syncContacts">立即同步联系人</ElButton>
        <ElButton type="primary" :loading="savingConfig" @click="saveConfig">保存设置</ElButton>
      </div>
    </div>

    <div class="metrics">
      <div><span>联系人</span><strong>{{ stats.contacts || 0 }}</strong></div>
      <div><span>有效好友</span><strong>{{ stats.active_contacts || 0 }}</strong></div>
      <div><span>执行中</span><strong>{{ stats.active_enrollments || 0 }}</strong></div>
      <div><span>已发送</span><strong>{{ stats.sent || 0 }}</strong></div>
      <div><span>发送失败</span><strong>{{ stats.failed || 0 }}</strong></div>
    </div>

    <ElAlert
      v-if="config.dry_run"
      title="当前为试运行模式：系统会识别客户和生成执行记录，但不会真正发送消息。"
      type="warning"
      :closable="false"
      show-icon
    />

    <ElTabs v-model="activeTab" class="sop-tabs" @tab-change="loadTab">
      <ElTabPane label="流程设置" name="flow">
        <section class="panel config-panel">
          <ElForm label-position="top">
            <div class="config-grid">
              <ElFormItem label="流程名称"><ElInput v-model="config.name" /></ElFormItem>
              <ElFormItem label="发送时间范围">
                <div class="time-range">
                  <ElTimeSelect v-model="config.send_window_start" start="00:00" step="00:30" end="23:30" />
                  <span>至</span>
                  <ElTimeSelect v-model="config.send_window_end" start="00:00" step="00:30" end="23:30" />
                </div>
              </ElFormItem>
              <ElFormItem label="总开关"><ElSwitch v-model="config.enabled" active-text="启用" inactive-text="停用" /></ElFormItem>
              <ElFormItem label="运行方式"><ElSwitch v-model="config.dry_run" active-text="试运行" inactive-text="真实发送" /></ElFormItem>
              <ElFormItem label="联系人轮询间隔">
                <ElSelect v-model="config.contact_poll_interval_minutes">
                  <ElOption label="每 30 分钟" :value="30" />
                  <ElOption label="每 1 小时" :value="60" />
                  <ElOption label="每 2 小时" :value="120" />
                  <ElOption label="每 4 小时" :value="240" />
                  <ElOption label="每 12 小时" :value="720" />
                  <ElOption label="每天" :value="1440" />
                </ElSelect>
                <small>系统按此频率识别新添加的好友</small>
              </ElFormItem>
              <ElFormItem label="好友移除确认次数">
                <ElInputNumber v-model="config.contact_missing_threshold" :min="1" :max="10" />
                <small>连续多次未发现后才标记为已移除，避免接口偶发漏数</small>
              </ElFormItem>
            </div>
          </ElForm>
          <div class="sync-info">
            首次基线：{{ formatTime(config.baseline_initialized_at) }} · 最近同步：{{ formatTime(config.last_contact_sync_at) }}
          </div>
        </section>

        <div class="section-head">
          <div><h2>触达节点</h2><p>一个节点可组合多条文本、图片和视频，并按设定顺序通过风控队列逐条发送。</p></div>
          <ElButton type="primary" @click="openCreate">新增节点</ElButton>
        </div>

        <section class="panel">
          <ElEmpty v-if="!steps.length" description="还没有触达节点" />
          <ElTable v-else :data="steps" row-key="id">
            <ElTableColumn label="节点" width="90">
              <template #default="{ row }"><strong>D{{ row.day_offset }}</strong></template>
            </ElTableColumn>
            <ElTableColumn label="发送时间范围" width="160"><template #default="{ row }">{{ row.send_time_start }} - {{ row.send_time_end }}</template></ElTableColumn>
            <ElTableColumn label="消息序列" width="190">
              <template #default="{ row }">
                <ElTag v-for="(message, index) in stepMessages(row)" :key="index" class="sequence-tag">{{ index + 1 }}. {{ typeText(message.message_type) }}</ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn label="内容" min-width="320">
              <template #default="{ row }">
                <div class="sequence-preview">
                  <div v-for="(message, index) in stepMessages(row)" :key="index" class="sequence-preview-item">
                    <span class="sequence-number">{{ index + 1 }}</span>
                    <span v-if="message.message_type === 'text'" class="message-text">{{ message.content }}</span>
                    <ElImage v-else-if="message.message_type === 'image'" class="media-thumb" :src="message.content" fit="cover" :preview-src-list="[message.content]" />
                    <div v-else class="video-cell"><video :src="message.content" :poster="message.preview_url || undefined" controls preload="metadata"></video></div>
                  </div>
                </div>
              </template>
            </ElTableColumn>
            <ElTableColumn label="状态" width="90">
              <template #default="{ row }"><ElTag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</ElTag></template>
            </ElTableColumn>
            <ElTableColumn label="操作" width="210" fixed="right">
              <template #default="{ row }">
                <ElButton link type="success" @click="openDirectSend(row)">发送</ElButton>
                <ElButton link type="primary" @click="openEdit(row)">修改</ElButton>
                <ElButton link type="danger" @click="removeStep(row)">删除</ElButton>
              </template>
            </ElTableColumn>
          </ElTable>
        </section>
      </ElTabPane>

      <ElTabPane label="执行客户" name="contacts">
        <section class="panel">
          <ElTable v-loading="contactsLoading" :data="contacts" row-key="id">
            <ElTableColumn label="客户" min-width="180">
              <template #default="{ row }"><strong>{{ contactRemark(row) }}</strong><small>微信号/ID：{{ row.wechat_id || row.wc_id }}</small></template>
            </ElTableColumn>
            <ElTableColumn prop="friend_added_on" label="加好友日期" width="130">
              <template #default="{ row }">{{ row.friend_added_on || '历史基线' }}</template>
            </ElTableColumn>
            <ElTableColumn label="购买标签" min-width="180">
              <template #default="{ row }"><ElTag v-for="tag in row.customer_tags" :key="tag" class="tag">{{ tag }}</ElTag><span v-if="!row.customer_tags.length">无</span></template>
            </ElTableColumn>
            <ElTableColumn label="好友状态" width="110"><template #default="{ row }">{{ row.status === 'active' ? '有效好友' : '已移除' }}</template></ElTableColumn>
            <ElTableColumn label="SOP状态" width="140">
              <template #default="{ row }">{{ enrollmentText(row.enrollment_status, row.exit_reason) }}</template>
            </ElTableColumn>
            <ElTableColumn label="操作" width="110" fixed="right">
              <template #default="{ row }"><ElButton link type="primary" :disabled="row.status !== 'active' || !steps.length" @click="openTestSend(row)">测试发送</ElButton></template>
            </ElTableColumn>
          </ElTable>
          <ElPagination v-if="contactsTotal > 50" layout="prev, pager, next, total" :total="contactsTotal" :page-size="50" @current-change="loadContacts" />
        </section>
      </ElTabPane>

      <ElTabPane label="发送记录" name="deliveries">
        <section class="panel">
          <ElTable v-loading="deliveriesLoading" :data="deliveries" row-key="id">
            <ElTableColumn label="客户" min-width="160"><template #default="{ row }">{{ row.display_name || row.wc_id || '-' }}</template></ElTableColumn>
            <ElTableColumn prop="step_id" label="节点ID" width="90" />
            <ElTableColumn label="消息" width="100"><template #default="{ row }">{{ row.messages?.length || 1 }} 条</template></ElTableColumn>
            <ElTableColumn label="内容" min-width="260"><template #default="{ row }"><span class="message-text">{{ deliveryMessageSummary(row) }}</span></template></ElTableColumn>
            <ElTableColumn label="计划时间" width="180"><template #default="{ row }">{{ formatTime(row.due_at) }}</template></ElTableColumn>
            <ElTableColumn label="状态" width="110"><template #default="{ row }"><ElTag :type="deliveryTagType(row.status)">{{ deliveryText(row.status) }}</ElTag></template></ElTableColumn>
            <ElTableColumn prop="last_error" label="失败原因" min-width="180" />
          </ElTable>
          <ElPagination v-if="deliveriesTotal > 50" layout="prev, pager, next, total" :total="deliveriesTotal" :page-size="50" @current-change="loadDeliveries" />
        </section>
      </ElTabPane>
    </ElTabs>

    <ElDialog v-model="stepDialog" :title="editingId ? '修改节点' : '新增节点'" width="760px" destroy-on-close>
      <ElForm label-position="top">
        <div class="step-grid">
          <ElFormItem label="加好友后第几天"><ElInputNumber v-model="stepForm.day_offset" :min="0" :max="3650" /></ElFormItem>
          <ElFormItem label="随机发送时间范围">
            <div class="time-range"><ElTimeSelect v-model="stepForm.send_time_start" start="00:00" step="00:30" end="23:30" /><span>至</span><ElTimeSelect v-model="stepForm.send_time_end" start="00:00" step="00:30" end="23:30" /></div>
            <small>每位客户会在该范围内生成一个随机发送时刻</small>
          </ElFormItem>
        </div>
        <div class="message-editor-head"><strong>消息序列</strong><small>拖动卡片或使用箭头调整顺序，系统将从上到下发送</small></div>
        <div class="message-editor-list">
          <div
            v-for="(message, index) in stepForm.messages"
            :key="index"
            class="message-editor-card"
            @dragover.prevent
            @drop="dropMessage(index)"
          >
            <div class="message-card-head">
              <span class="drag-handle" draggable="true" @dragstart="dragMessageIndex = index">⠿ 第 {{ index + 1 }} 条</span>
              <div>
                <ElButton link :disabled="index === 0" @click="moveMessage(index, -1)">↑</ElButton>
                <ElButton link :disabled="index === stepForm.messages.length - 1" @click="moveMessage(index, 1)">↓</ElButton>
                <ElButton link type="danger" :disabled="stepForm.messages.length === 1" @click="removeMessage(index)">删除</ElButton>
              </div>
            </div>
            <ElFormItem label="消息类型">
              <ElRadioGroup v-model="message.message_type" @change="changeMessageType(message)">
                <ElRadioButton value="text">文本</ElRadioButton><ElRadioButton value="image">图片</ElRadioButton><ElRadioButton value="video">视频</ElRadioButton>
              </ElRadioGroup>
            </ElFormItem>
            <ElFormItem v-if="message.message_type === 'text'" label="消息内容">
              <ElInput v-model="message.content" type="textarea" :rows="4" maxlength="20000" show-word-limit />
            </ElFormItem>
            <template v-else>
              <ElFormItem :label="message.message_type === 'image' ? '上传图片' : '上传视频'">
                <small class="media-limit">{{ mediaLimitText(message.message_type) }}</small>
                <input class="file-input" type="file" :accept="message.message_type === 'image' ? 'image/*' : 'video/mp4,video/quicktime'" @change="uploadPrimary($event, index)" />
                <ElProgress v-if="uploadingMessageIndex === index" :percentage="100" :indeterminate="true" />
                <ElImage v-if="message.message_type === 'image' && message.content" class="large-preview" :src="message.content" fit="contain" />
                <video v-if="message.message_type === 'video' && message.content" class="large-video" :src="message.content" :poster="message.preview_url || undefined" controls></video>
              </ElFormItem>
              <ElFormItem v-if="message.message_type === 'video'" label="视频封面（上传视频后自动生成，也可重新上传）">
                <small class="media-limit">Eyun 官方建议视频封面控制在 50KB 以内；本系统图片上传上限 5MB。</small>
                <input class="file-input" type="file" accept="image/*" @change="uploadCover($event, index)" />
                <ElImage v-if="message.preview_url" class="cover-preview" :src="message.preview_url" fit="cover" />
              </ElFormItem>
            </template>
          </div>
        </div>
        <div class="add-message-actions">
          <span>添加消息：</span>
          <ElButton :disabled="stepForm.messages.length >= 20" @click="addMessage('text')">+ 文本</ElButton>
          <ElButton :disabled="stepForm.messages.length >= 20" @click="addMessage('image')">+ 图片</ElButton>
          <ElButton :disabled="stepForm.messages.length >= 20" @click="addMessage('video')">+ 视频</ElButton>
        </div>
        <ElFormItem label="节点状态"><ElSwitch v-model="stepForm.enabled" active-text="启用" inactive-text="停用" /></ElFormItem>
      </ElForm>
      <template #footer><ElButton @click="stepDialog = false">取消</ElButton><ElButton type="primary" :loading="savingStep" @click="saveStep">保存节点</ElButton></template>
    </ElDialog>

    <ElDialog v-model="directSendDialog" title="发送节点内容" width="560px">
      <p v-if="directSendStep">D{{ directSendStep.day_offset }} · {{ directSendStep.send_time_start }}-{{ directSendStep.send_time_end }} · {{ stepMessages(directSendStep).length }} 条消息</p>
      <ElForm label-position="top">
        <ElFormItem label="选择联系人">
          <ElSelect v-model="directContactIds" multiple :multiple-limit="50" filterable remote reserve-keyword :remote-method="searchDirectContacts" :loading="directContactsLoading" placeholder="搜索备注名、微信号或微信ID" style="width:100%">
            <ElOption v-for="contact in directContactOptions" :key="contact.id" :label="contactOptionLabel(contact)" :value="contact.id" :disabled="contact.status !== 'active'" />
          </ElSelect>
          <small>最多选择 50 位联系人；确认后立即加入现有风控发送队列。</small>
        </ElFormItem>
      </ElForm>
      <template #footer><ElButton @click="directSendDialog = false">取消</ElButton><ElButton type="primary" :loading="directSending" @click="confirmDirectSend">确认发送</ElButton></template>
    </ElDialog>

    <ElDialog v-model="testDialog" title="测试发送" width="460px">
      <p>发送给：{{ testContact ? contactOptionLabel(testContact) : '' }}</p>
      <ElSelect v-model="testStepId" placeholder="选择一个节点" style="width: 100%"><ElOption v-for="step in steps" :key="step.id" :label="`D${step.day_offset} ${step.send_time_start}-${step.send_time_end} · ${stepMessages(step).length} 条消息`" :value="step.id" /></ElSelect>
      <template #footer><ElButton @click="testDialog = false">取消</ElButton><ElButton type="primary" :loading="testing" @click="confirmTestSend">加入发送队列</ElButton></template>
    </ElDialog>
  </ContentWrap>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createUnpurchasedSopStep,
  deleteUnpurchasedSopStep,
  getUnpurchasedSop,
  getUnpurchasedSopContacts,
  getUnpurchasedSopDeliveries,
  syncUnpurchasedSopContacts,
  testSendUnpurchasedSop,
  updateUnpurchasedSop,
  updateUnpurchasedSopStep,
  uploadUnpurchasedSopMedia,
  type SopMessageItem,
  type SopMessageType,
  type SopStepPayload,
  type UnpurchasedSopConfig,
  type UnpurchasedSopContact,
  type UnpurchasedSopDelivery,
  type UnpurchasedSopStep
} from '@/api/admin/unpurchasedSop'

const activeTab = ref('flow')
const config = reactive<UnpurchasedSopConfig>({ id: 1, name: '未购SOP', enabled: false, dry_run: true, send_window_start: '09:00', send_window_end: '20:00', contact_poll_interval_minutes: 120, contact_missing_threshold: 3, timezone: 'Asia/Shanghai', updated_at: '' })
const steps = ref<UnpurchasedSopStep[]>([])
const stats = reactive<Record<string, number>>({})
const savingConfig = ref(false)
const syncing = ref(false)
const stepDialog = ref(false)
const editingId = ref<number>()
const savingStep = ref(false)
const uploadingMessageIndex = ref<number>()
const dragMessageIndex = ref<number>()
const IMAGE_MAX_BYTES = 5 * 1024 * 1024
const VIDEO_MAX_BYTES = 100 * 1024 * 1024
const emptyMessage = (message_type: SopMessageType = 'text'): SopMessageItem => ({ message_type, content: '', preview_url: '' })
const stepForm = reactive<SopStepPayload>({ day_offset: 0, send_time_start: '09:00', send_time_end: '10:00', messages: [emptyMessage()], position: 0, enabled: true })
const contacts = ref<UnpurchasedSopContact[]>([])
const contactsTotal = ref(0)
const contactsLoading = ref(false)
const deliveries = ref<UnpurchasedSopDelivery[]>([])
const deliveriesTotal = ref(0)
const deliveriesLoading = ref(false)
const testDialog = ref(false)
const testContact = ref<UnpurchasedSopContact>()
const testStepId = ref<number>()
const testing = ref(false)
const directSendDialog = ref(false)
const directSendStep = ref<UnpurchasedSopStep>()
const directContactIds = ref<number[]>([])
const directContactOptions = ref<UnpurchasedSopContact[]>([])
const directContactsLoading = ref(false)
const directSending = ref(false)

const load = async () => {
  const data = await getUnpurchasedSop()
  Object.assign(config, data.sop)
  steps.value = data.steps
  Object.assign(stats, data.stats)
}

const saveConfig = async () => {
  savingConfig.value = true
  try {
    Object.assign(config, await updateUnpurchasedSop({ name: config.name, enabled: config.enabled, dry_run: config.dry_run, send_window_start: config.send_window_start, send_window_end: config.send_window_end, contact_poll_interval_minutes: config.contact_poll_interval_minutes, contact_missing_threshold: config.contact_missing_threshold }))
    ElMessage.success('SOP设置已保存')
  } finally { savingConfig.value = false }
}

const syncContacts = async () => {
  syncing.value = true
  try {
    const result = await syncUnpurchasedSopContacts()
    ElMessage.success(`同步完成，本次新增 ${result.new || 0} 位联系人`)
    await load()
    if (activeTab.value === 'contacts') await loadContacts()
  } finally { syncing.value = false }
}

const stepMessages = (step: UnpurchasedSopStep): SopMessageItem[] => step.messages?.length ? step.messages : [{ message_type: step.message_type, content: step.content, preview_url: step.preview_url }]
const deliveryMessageSummary = (delivery: UnpurchasedSopDelivery) => delivery.messages?.length ? delivery.messages.map((message) => typeText(message.message_type)).join(' → ') : delivery.content
const resetStep = () => Object.assign(stepForm, { day_offset: 0, send_time_start: config.send_window_start || '09:00', send_time_end: config.send_window_end || '20:00', messages: [emptyMessage()], position: steps.value.length, enabled: true })
const openCreate = () => { editingId.value = undefined; resetStep(); stepDialog.value = true }
const openEdit = (step: UnpurchasedSopStep) => { editingId.value = step.id; Object.assign(stepForm, { day_offset: step.day_offset, send_time_start: step.send_time_start || step.send_time, send_time_end: step.send_time_end || step.send_time, messages: stepMessages(step).map((message) => ({ ...message, preview_url: message.preview_url || '' })), position: step.position, enabled: step.enabled }); stepDialog.value = true }

const addMessage = (messageType: SopMessageType) => { if (stepForm.messages.length < 20) stepForm.messages.push(emptyMessage(messageType)) }
const removeMessage = (index: number) => { if (stepForm.messages.length > 1) stepForm.messages.splice(index, 1) }
const moveMessage = (index: number, offset: number) => {
  const target = index + offset
  if (target < 0 || target >= stepForm.messages.length) return
  const [message] = stepForm.messages.splice(index, 1)
  stepForm.messages.splice(target, 0, message)
}
const dropMessage = (target: number) => {
  const source = dragMessageIndex.value
  dragMessageIndex.value = undefined
  if (source === undefined || source === target) return
  const [message] = stepForm.messages.splice(source, 1)
  stepForm.messages.splice(target, 0, message)
}
const changeMessageType = (message: SopMessageItem) => { message.content = ''; message.preview_url = '' }

const saveStep = async () => {
  if (stepForm.send_time_end < stepForm.send_time_start) return ElMessage.warning('发送结束时间不能早于开始时间')
  const emptyIndex = stepForm.messages.findIndex((message) => !message.content.trim())
  if (emptyIndex >= 0) return ElMessage.warning(`请完善第 ${emptyIndex + 1} 条消息内容`)
  const videoWithoutCover = stepForm.messages.findIndex((message) => message.message_type === 'video' && !message.preview_url)
  if (videoWithoutCover >= 0) return ElMessage.warning(`第 ${videoWithoutCover + 1} 条视频必须有封面图`)
  savingStep.value = true
  try {
    const payload = { ...stepForm, messages: stepForm.messages.map((message) => ({ ...message, content: message.content.trim(), preview_url: message.preview_url || undefined })) }
    if (editingId.value) await updateUnpurchasedSopStep(editingId.value, payload)
    else await createUnpurchasedSopStep(payload)
    ElMessage.success('节点已保存')
    stepDialog.value = false
    await load()
  } finally { savingStep.value = false }
}

const removeStep = async (step: UnpurchasedSopStep) => {
  await ElMessageBox.confirm(`确认删除 D${step.day_offset} 的组合节点（${stepMessages(step).length} 条消息）？历史发送记录仍会保留。`, '删除节点', { type: 'warning' })
  await deleteUnpurchasedSopStep(step.id)
  ElMessage.success('节点已删除')
  await load()
}

const uploadPrimary = async (event: Event, index: number) => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const message = stepForm.messages[index]
  if (!message) return
  const maxBytes = message.message_type === 'image' ? IMAGE_MAX_BYTES : VIDEO_MAX_BYTES
  if (file.size > maxBytes) { input.value = ''; return ElMessage.warning(`${message.message_type === 'image' ? '图片' : '视频'}不能超过 ${maxBytes / 1024 / 1024}MB`) }
  uploadingMessageIndex.value = index
  try {
    const media = await uploadUnpurchasedSopMedia(file)
    message.content = media.url
    if (message.message_type === 'video') {
      try {
        const cover = await createVideoCover(file)
        message.preview_url = (await uploadUnpurchasedSopMedia(cover)).url
      } catch { ElMessage.warning('视频已上传，请补充上传封面图') }
    }
    ElMessage.success('媒体上传成功')
  } finally { uploadingMessageIndex.value = undefined; input.value = '' }
}

const uploadCover = async (event: Event, index: number) => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  if (file.size > IMAGE_MAX_BYTES) { input.value = ''; return ElMessage.warning('封面图片不能超过 5MB') }
  const message = stepForm.messages[index]
  if (!message) return
  const media = await uploadUnpurchasedSopMedia(file)
  message.preview_url = media.url
  if (media.size > 50 * 1024) ElMessage.warning('Eyun建议视频封面控制在50KB以内，当前封面可能影响发送速度')
  ElMessage.success('封面上传成功')
  input.value = ''
}

const createVideoCover = (file: File): Promise<File> => new Promise((resolve, reject) => {
  const url = URL.createObjectURL(file)
  const video = document.createElement('video')
  video.preload = 'metadata'
  video.muted = true
  video.src = url
  video.onloadeddata = () => { video.currentTime = Math.min(0.2, video.duration || 0) }
  video.onseeked = () => {
    const canvas = document.createElement('canvas')
    const scale = Math.min(1, 360 / Math.max(video.videoWidth, 1))
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale))
    canvas.height = Math.max(1, Math.round(video.videoHeight * scale))
    canvas.getContext('2d')?.drawImage(video, 0, 0, canvas.width, canvas.height)
    canvas.toBlob((blob) => { URL.revokeObjectURL(url); blob ? resolve(new File([blob], 'video-cover.jpg', { type: 'image/jpeg' })) : reject(new Error('封面生成失败')) }, 'image/jpeg', 0.65)
  }
  video.onerror = () => { URL.revokeObjectURL(url); reject(new Error('无法读取视频')) }
})

const loadContacts = async (page = 1) => { contactsLoading.value = true; try { const data = await getUnpurchasedSopContacts(page, 50); contacts.value = data.items; contactsTotal.value = data.total } finally { contactsLoading.value = false } }
const loadDeliveries = async (page = 1) => { deliveriesLoading.value = true; try { const data = await getUnpurchasedSopDeliveries(page, 50); deliveries.value = data.items; deliveriesTotal.value = data.total } finally { deliveriesLoading.value = false } }
const loadTab = async (name: string | number) => { if (name === 'contacts') await loadContacts(); if (name === 'deliveries') await loadDeliveries() }
const openTestSend = (contact: UnpurchasedSopContact) => { testContact.value = contact; testStepId.value = steps.value[0]?.id; testDialog.value = true }
const confirmTestSend = async () => { if (!testContact.value || !testStepId.value) return; testing.value = true; try { await testSendUnpurchasedSop(testStepId.value, [testContact.value.id]); ElMessage.success('测试消息已加入Eyun发送队列'); testDialog.value = false } finally { testing.value = false } }

const contactRemark = (contact: UnpurchasedSopContact) => contact.remark_name || contact.display_name || '未设置备注'
const contactOptionLabel = (contact: UnpurchasedSopContact) => `${contactRemark(contact)} · ${contact.wechat_id || contact.wc_id}`
const mediaLimitText = (type: SopMessageType) => type === 'image'
  ? '单张图片不能超过 5MB；超过后无法上传或发送。支持 JPG、PNG、GIF、WEBP，图片地址需公网可访问。'
  : 'Eyun 官方未规定视频硬性大小上限；本系统单个上限 100MB，支持 MP4、MOV、M4V，视频地址需公网可访问。'
const searchDirectContacts = async (keyword = '') => {
  directContactsLoading.value = true
  try {
    const items = (await getUnpurchasedSopContacts(1, 100, keyword)).items
    const selected = directContactOptions.value.filter((contact) => directContactIds.value.includes(contact.id))
    directContactOptions.value = [...selected, ...items.filter((contact) => !selected.some((current) => current.id === contact.id))]
  }
  finally { directContactsLoading.value = false }
}
const openDirectSend = async (step: UnpurchasedSopStep) => {
  directSendStep.value = step
  directContactIds.value = []
  directSendDialog.value = true
  await searchDirectContacts()
}
const confirmDirectSend = async () => {
  if (!directSendStep.value || !directContactIds.value.length) return ElMessage.warning('请至少选择一位联系人')
  directSending.value = true
  try {
    const result = await testSendUnpurchasedSop(directSendStep.value.id, directContactIds.value)
    ElMessage.success(`已将 ${result.contact_count} 位联系人的消息加入风控发送队列`)
    directSendDialog.value = false
  } finally { directSending.value = false }
}

const typeText = (type: SopMessageType) => ({ text: '文本', image: '图片', video: '视频' }[type])
const formatTime = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '尚未执行'
const enrollmentText = (status?: string | null, reason?: string | null) => !status ? '未加入' : status === 'active' ? '执行中' : reason === 'purchase_tag_added' ? '已购退出' : reason === 'contact_removed' ? '好友移除' : '已退出'
const deliveryText = (status: string) => ({ dry_run: '试运行', creating: '创建中', queued: '待发送', sending: '发送中', sent: '已发送', failed: '失败', cancelled: '已取消', skipped_reply: '客户回复跳过' }[status] || status)
const deliveryTagType = (status: string): 'success' | 'danger' | 'info' | 'warning' | undefined => status === 'sent' ? 'success' : status === 'failed' ? 'danger' : ['cancelled', 'skipped_reply'].includes(status) ? 'info' : status === 'dry_run' ? 'warning' : undefined

onMounted(load)
</script>

<style scoped>
.page-head,.section-head{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}.page-head h1,.section-head h2{margin:0;color:#18352d}.page-head p,.section-head p{margin:7px 0 0;color:#708079}.head-actions{display:flex;gap:10px}.metrics{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:12px;margin:20px 0}.metrics div,.panel{background:#fff;border:1px solid #e2e9e6;border-radius:12px}.metrics div{padding:16px}.metrics span{display:block;color:#75857f;font-size:13px}.metrics strong{display:block;margin-top:8px;color:#18352d;font-size:25px}.sop-tabs{margin-top:18px}.panel{padding:18px}.config-panel{margin-bottom:20px}.config-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.time-range{display:flex;align-items:center;gap:8px}.sync-info{color:#84918c;font-size:12px}.section-head{margin:24px 0 12px}.message-text{display:-webkit-box;overflow:hidden;-webkit-line-clamp:3;-webkit-box-orient:vertical;white-space:pre-wrap}.media-thumb{width:90px;height:64px;border-radius:8px}.video-cell video{width:150px;max-height:100px;border-radius:8px;background:#111}.step-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.file-input{display:block;width:100%;padding:10px;border:1px dashed #aebdb7;border-radius:8px}.large-preview,.large-video{display:block;width:100%;max-height:300px;margin-top:12px;border-radius:8px;background:#f3f6f5}.cover-preview{width:160px;height:90px;margin-top:10px;border-radius:8px}.tag{margin-right:5px}small{display:block;margin-top:4px;color:#8b9994}.el-pagination{justify-content:flex-end;margin-top:16px}@media(max-width:1000px){.metrics{grid-template-columns:repeat(2,1fr)}.config-grid{grid-template-columns:1fr 1fr}}@media(max-width:640px){.page-head,.section-head{display:block}.head-actions{margin-top:14px}.metrics,.config-grid,.step-grid{grid-template-columns:1fr}}
.sequence-tag{margin:2px 4px 2px 0}.sequence-preview{display:flex;flex-direction:column;gap:8px}.sequence-preview-item{display:flex;align-items:flex-start;gap:8px}.sequence-number{display:flex;flex:0 0 22px;align-items:center;justify-content:center;height:22px;border-radius:50%;background:#eef4f1;color:#567068;font-size:12px}.message-editor-head{display:flex;align-items:center;justify-content:space-between;margin:4px 0 10px}.message-editor-head small{margin:0}.message-editor-list{display:flex;flex-direction:column;gap:12px;max-height:55vh;overflow:auto;padding-right:4px}.message-editor-card{padding:14px 14px 2px;border:1px solid #dce6e2;border-radius:10px;background:#fbfdfc}.message-editor-card:hover{border-color:#9ebdb2}.message-card-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}.drag-handle{color:#4f675f;cursor:grab;font-weight:600}.media-limit{margin:0 0 8px;color:#768781;line-height:1.5}.add-message-actions{display:flex;align-items:center;gap:8px;margin:14px 0 20px;padding:12px;border:1px dashed #b8c8c2;border-radius:10px}.add-message-actions span{color:#64756f;font-size:13px}@media(max-width:640px){.message-editor-head,.add-message-actions{align-items:flex-start;flex-wrap:wrap}.message-editor-list{max-height:none}}
</style>
