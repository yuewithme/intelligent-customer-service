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
          <div><h2>触达节点</h2><p>支持文本、图片和视频；媒体文件上传后由 Docker 公网地址提供给 Eyun。</p></div>
          <ElButton type="primary" @click="openCreate">新增节点</ElButton>
        </div>

        <section class="panel">
          <ElEmpty v-if="!steps.length" description="还没有触达节点" />
          <ElTable v-else :data="steps" row-key="id">
            <ElTableColumn label="节点" width="90">
              <template #default="{ row }"><strong>D{{ row.day_offset }}</strong></template>
            </ElTableColumn>
            <ElTableColumn prop="send_time" label="发送时间" width="110" />
            <ElTableColumn label="类型" width="100">
              <template #default="{ row }"><ElTag>{{ typeText(row.message_type) }}</ElTag></template>
            </ElTableColumn>
            <ElTableColumn label="内容" min-width="320">
              <template #default="{ row }">
                <span v-if="row.message_type === 'text'" class="message-text">{{ row.content }}</span>
                <ElImage v-else-if="row.message_type === 'image'" class="media-thumb" :src="row.content" fit="cover" :preview-src-list="[row.content]" />
                <div v-else class="video-cell"><video :src="row.content" :poster="row.preview_url || undefined" controls preload="metadata"></video></div>
              </template>
            </ElTableColumn>
            <ElTableColumn label="状态" width="90">
              <template #default="{ row }"><ElTag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</ElTag></template>
            </ElTableColumn>
            <ElTableColumn label="操作" width="160" fixed="right">
              <template #default="{ row }">
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
              <template #default="{ row }"><strong>{{ row.display_name || row.wc_id }}</strong><small>{{ row.wc_id }}</small></template>
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
            <ElTableColumn label="类型" width="90"><template #default="{ row }">{{ typeText(row.message_type) }}</template></ElTableColumn>
            <ElTableColumn label="内容" min-width="260"><template #default="{ row }"><span class="message-text">{{ row.content }}</span></template></ElTableColumn>
            <ElTableColumn label="计划时间" width="180"><template #default="{ row }">{{ formatTime(row.due_at) }}</template></ElTableColumn>
            <ElTableColumn label="状态" width="110"><template #default="{ row }"><ElTag :type="deliveryTagType(row.status)">{{ deliveryText(row.status) }}</ElTag></template></ElTableColumn>
            <ElTableColumn prop="last_error" label="失败原因" min-width="180" />
          </ElTable>
          <ElPagination v-if="deliveriesTotal > 50" layout="prev, pager, next, total" :total="deliveriesTotal" :page-size="50" @current-change="loadDeliveries" />
        </section>
      </ElTabPane>
    </ElTabs>

    <ElDialog v-model="stepDialog" :title="editingId ? '修改节点' : '新增节点'" width="620px" destroy-on-close>
      <ElForm label-position="top">
        <div class="step-grid">
          <ElFormItem label="加好友后第几天"><ElInputNumber v-model="stepForm.day_offset" :min="0" :max="3650" /></ElFormItem>
          <ElFormItem label="发送时间"><ElTimeSelect v-model="stepForm.send_time" start="00:00" step="00:30" end="23:30" /></ElFormItem>
        </div>
        <ElFormItem label="消息类型"><ElRadioGroup v-model="stepForm.message_type"><ElRadioButton value="text">文本</ElRadioButton><ElRadioButton value="image">图片</ElRadioButton><ElRadioButton value="video">视频</ElRadioButton></ElRadioGroup></ElFormItem>
        <ElFormItem v-if="stepForm.message_type === 'text'" label="消息内容"><ElInput v-model="stepForm.content" type="textarea" :rows="6" maxlength="20000" show-word-limit /></ElFormItem>
        <template v-else>
          <ElFormItem :label="stepForm.message_type === 'image' ? '上传图片' : '上传视频'">
            <input class="file-input" type="file" :accept="stepForm.message_type === 'image' ? 'image/*' : 'video/mp4,video/quicktime'" @change="uploadPrimary" />
            <ElProgress v-if="uploadingPrimary" :percentage="100" :indeterminate="true" />
            <ElImage v-if="stepForm.message_type === 'image' && stepForm.content" class="large-preview" :src="stepForm.content" fit="contain" />
            <video v-if="stepForm.message_type === 'video' && stepForm.content" class="large-video" :src="stepForm.content" :poster="stepForm.preview_url || undefined" controls></video>
          </ElFormItem>
          <ElFormItem v-if="stepForm.message_type === 'video'" label="视频封面（上传视频后自动生成，也可重新上传）">
            <input class="file-input" type="file" accept="image/*" @change="uploadCover" />
            <ElImage v-if="stepForm.preview_url" class="cover-preview" :src="stepForm.preview_url" fit="cover" />
          </ElFormItem>
        </template>
        <ElFormItem label="节点状态"><ElSwitch v-model="stepForm.enabled" active-text="启用" inactive-text="停用" /></ElFormItem>
      </ElForm>
      <template #footer><ElButton @click="stepDialog = false">取消</ElButton><ElButton type="primary" :loading="savingStep" @click="saveStep">保存节点</ElButton></template>
    </ElDialog>

    <ElDialog v-model="testDialog" title="测试发送" width="460px">
      <p>发送给：{{ testContact?.display_name || testContact?.wc_id }}</p>
      <ElSelect v-model="testStepId" placeholder="选择一个节点" style="width: 100%"><ElOption v-for="step in steps" :key="step.id" :label="`D${step.day_offset} ${step.send_time} · ${typeText(step.message_type)}`" :value="step.id" /></ElSelect>
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
const uploadingPrimary = ref(false)
const stepForm = reactive<SopStepPayload>({ day_offset: 0, send_time: '10:00', message_type: 'text', content: '', preview_url: '', position: 0, enabled: true })
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

const resetStep = () => Object.assign(stepForm, { day_offset: 0, send_time: config.send_window_start || '10:00', message_type: 'text' as SopMessageType, content: '', preview_url: '', position: steps.value.length, enabled: true })
const openCreate = () => { editingId.value = undefined; resetStep(); stepDialog.value = true }
const openEdit = (step: UnpurchasedSopStep) => { editingId.value = step.id; Object.assign(stepForm, { day_offset: step.day_offset, send_time: step.send_time, message_type: step.message_type, content: step.content, preview_url: step.preview_url || '', position: step.position, enabled: step.enabled }); stepDialog.value = true }

const saveStep = async () => {
  if (!stepForm.content.trim()) return ElMessage.warning(stepForm.message_type === 'text' ? '请输入消息内容' : '请先上传媒体文件')
  if (stepForm.message_type === 'video' && !stepForm.preview_url) return ElMessage.warning('视频必须有封面图')
  savingStep.value = true
  try {
    const payload = { ...stepForm, content: stepForm.content.trim(), preview_url: stepForm.preview_url || undefined }
    if (editingId.value) await updateUnpurchasedSopStep(editingId.value, payload)
    else await createUnpurchasedSopStep(payload)
    ElMessage.success('节点已保存')
    stepDialog.value = false
    await load()
  } finally { savingStep.value = false }
}

const removeStep = async (step: UnpurchasedSopStep) => {
  await ElMessageBox.confirm(`确认删除 D${step.day_offset} 的${typeText(step.message_type)}节点？历史发送记录仍会保留。`, '删除节点', { type: 'warning' })
  await deleteUnpurchasedSopStep(step.id)
  ElMessage.success('节点已删除')
  await load()
}

const uploadPrimary = async (event: Event) => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploadingPrimary.value = true
  try {
    const media = await uploadUnpurchasedSopMedia(file)
    stepForm.content = media.url
    if (stepForm.message_type === 'video') {
      try {
        const cover = await createVideoCover(file)
        stepForm.preview_url = (await uploadUnpurchasedSopMedia(cover)).url
      } catch { ElMessage.warning('视频已上传，请补充上传封面图') }
    }
    ElMessage.success('媒体上传成功')
  } finally { uploadingPrimary.value = false; input.value = '' }
}

const uploadCover = async (event: Event) => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const media = await uploadUnpurchasedSopMedia(file)
  stepForm.preview_url = media.url
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
const confirmTestSend = async () => { if (!testContact.value || !testStepId.value) return; testing.value = true; try { await testSendUnpurchasedSop(testStepId.value, testContact.value.id); ElMessage.success('测试消息已加入Eyun发送队列'); testDialog.value = false } finally { testing.value = false } }

const typeText = (type: SopMessageType) => ({ text: '文本', image: '图片', video: '视频' }[type])
const formatTime = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '尚未执行'
const enrollmentText = (status?: string | null, reason?: string | null) => !status ? '未加入' : status === 'active' ? '执行中' : reason === 'purchase_tag_added' ? '已购退出' : reason === 'contact_removed' ? '好友移除' : '已退出'
const deliveryText = (status: string) => ({ dry_run: '试运行', creating: '创建中', queued: '待发送', sending: '发送中', sent: '已发送', failed: '失败', cancelled: '已取消', skipped_reply: '客户回复跳过' }[status] || status)
const deliveryTagType = (status: string): 'success' | 'danger' | 'info' | 'warning' | undefined => status === 'sent' ? 'success' : status === 'failed' ? 'danger' : ['cancelled', 'skipped_reply'].includes(status) ? 'info' : status === 'dry_run' ? 'warning' : undefined

onMounted(load)
</script>

<style scoped>
.page-head,.section-head{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}.page-head h1,.section-head h2{margin:0;color:#18352d}.page-head p,.section-head p{margin:7px 0 0;color:#708079}.head-actions{display:flex;gap:10px}.metrics{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:12px;margin:20px 0}.metrics div,.panel{background:#fff;border:1px solid #e2e9e6;border-radius:12px}.metrics div{padding:16px}.metrics span{display:block;color:#75857f;font-size:13px}.metrics strong{display:block;margin-top:8px;color:#18352d;font-size:25px}.sop-tabs{margin-top:18px}.panel{padding:18px}.config-panel{margin-bottom:20px}.config-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.time-range{display:flex;align-items:center;gap:8px}.sync-info{color:#84918c;font-size:12px}.section-head{margin:24px 0 12px}.message-text{display:-webkit-box;overflow:hidden;-webkit-line-clamp:3;-webkit-box-orient:vertical;white-space:pre-wrap}.media-thumb{width:90px;height:64px;border-radius:8px}.video-cell video{width:150px;max-height:100px;border-radius:8px;background:#111}.step-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.file-input{display:block;width:100%;padding:10px;border:1px dashed #aebdb7;border-radius:8px}.large-preview,.large-video{display:block;width:100%;max-height:300px;margin-top:12px;border-radius:8px;background:#f3f6f5}.cover-preview{width:160px;height:90px;margin-top:10px;border-radius:8px}.tag{margin-right:5px}small{display:block;margin-top:4px;color:#8b9994}.el-pagination{justify-content:flex-end;margin-top:16px}@media(max-width:1000px){.metrics{grid-template-columns:repeat(2,1fr)}.config-grid{grid-template-columns:1fr 1fr}}@media(max-width:640px){.page-head,.section-head{display:block}.head-actions{margin-top:14px}.metrics,.config-grid,.step-grid{grid-template-columns:1fr}}
</style>
