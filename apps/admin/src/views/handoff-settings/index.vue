<template>
  <ContentWrap>
    <div class="page-head">
      <div>
        <h1>转人工设置</h1>
        <p>配置转人工时的微信通知人和通知内容。</p>
      </div>
      <div class="head-actions">
        <ElButton :loading="syncing" @click="syncContacts">同步联系人</ElButton>
        <ElButton type="primary" :loading="saving" @click="saveSettings">保存设置</ElButton>
      </div>
    </div>

    <div v-loading="loading" class="settings-grid">
      <section class="setting-card">
        <div class="card-title">
          <span class="step">1</span>
          <div>
            <h2>转人工通知</h2>
            <p>选择接收转人工提醒的微信联系人，最多可选 20 人。</p>
          </div>
        </div>
        <ElSelect
          v-model="form.recipient_contact_ids"
          multiple
          filterable
          remote
          reserve-keyword
          :multiple-limit="20"
          :remote-method="searchContacts"
          :loading="contactsLoading"
          placeholder="搜索备注名、昵称、微信号或微信 ID"
          style="width: 100%"
        >
          <ElOption
            v-for="contact in contactOptions"
            :key="contact.id"
            :label="contactLabel(contact)"
            :value="contact.id"
          />
        </ElSelect>
        <div v-if="selectedContacts.length" class="selected-list">
          <div v-for="contact in selectedContacts" :key="contact.id" class="selected-contact">
            <ElAvatar :size="34" :src="contact.avatar_url || undefined">
              {{ contactName(contact).slice(0, 1) }}
            </ElAvatar>
            <span><strong>{{ contactName(contact) }}</strong><small>{{ contact.wechat_id || contact.wc_id }}</small></span>
          </div>
        </div>
        <ElEmpty v-else description="还没有选择通知联系人" :image-size="72" />
      </section>

      <section class="setting-card">
        <div class="card-title">
          <span class="step">2</span>
          <div>
            <h2>通知信息编辑</h2>
            <p>填写通知正文，发送时系统会自动附上客户信息、转人工原因和触发消息。</p>
          </div>
        </div>
        <ElInput
          v-model="form.message_text"
          type="textarea"
          :rows="8"
          maxlength="2000"
          show-word-limit
          placeholder="例如：有客户需要转人工处理，请及时跟进。"
        />
        <div class="auto-fields">
          <span>自动附加</span>
          <code>转人工用户昵称：客户昵称</code>
          <code>转人工用户微信号：客户微信号</code>
          <code>转人工原因：系统判断原因或人工填写原因</code>
          <code>触发客户消息：导致转人工的原始消息</code>
        </div>
      </section>
    </div>
  </ContentWrap>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getHandoffNotificationContacts,
  getHandoffNotificationSettings,
  syncHandoffNotificationContacts,
  updateHandoffNotificationSettings,
  type HandoffNotificationContact
} from '@/api/admin/handoffNotification'

const loading = ref(false)
const saving = ref(false)
const syncing = ref(false)
const contactsLoading = ref(false)
const contactOptions = ref<HandoffNotificationContact[]>([])
const form = reactive({ recipient_contact_ids: [] as number[], message_text: '' })

const selectedContacts = computed(() => {
  const byId = new Map(contactOptions.value.map((contact) => [contact.id, contact]))
  return form.recipient_contact_ids
    .map((contactId) => byId.get(contactId))
    .filter((contact): contact is HandoffNotificationContact => Boolean(contact))
})

const contactName = (contact: HandoffNotificationContact) =>
  contact.remark_name || contact.display_name || contact.wechat_id || contact.wc_id

const contactLabel = (contact: HandoffNotificationContact) => {
  const account = contact.wechat_id || contact.wc_id
  return `${contactName(contact)} · ${account}`
}

const mergeContacts = (contacts: HandoffNotificationContact[]) => {
  const merged = new Map(contactOptions.value.map((contact) => [contact.id, contact]))
  contacts.forEach((contact) => merged.set(contact.id, contact))
  contactOptions.value = Array.from(merged.values())
}

const searchContacts = async (keyword: string) => {
  contactsLoading.value = true
  try {
    const data = await getHandoffNotificationContacts(keyword)
    mergeContacts(data.items)
  } finally {
    contactsLoading.value = false
  }
}

const loadSettings = async () => {
  loading.value = true
  try {
    const [settings, contacts] = await Promise.all([
      getHandoffNotificationSettings(),
      getHandoffNotificationContacts()
    ])
    form.recipient_contact_ids = [...settings.recipient_contact_ids]
    form.message_text = settings.message_text
    mergeContacts([...settings.recipients, ...contacts.items])
  } finally {
    loading.value = false
  }
}

const syncContacts = async () => {
  syncing.value = true
  try {
    await syncHandoffNotificationContacts()
    await searchContacts('')
    ElMessage.success('微信联系人已同步')
  } finally {
    syncing.value = false
  }
}

const saveSettings = async () => {
  if (!form.recipient_contact_ids.length) {
    ElMessage.warning('请至少选择一位通知联系人')
    return
  }
  if (!form.message_text.trim()) {
    ElMessage.warning('请填写通知信息')
    return
  }
  saving.value = true
  try {
    const settings = await updateHandoffNotificationSettings({
      recipient_contact_ids: form.recipient_contact_ids,
      message_text: form.message_text.trim()
    })
    form.message_text = settings.message_text
    mergeContacts(settings.recipients)
    ElMessage.success('转人工设置已保存')
  } finally {
    saving.value = false
  }
}

onMounted(() => { void loadSettings() })
</script>

<style scoped>
.page-head { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 20px; }
.page-head h1 { margin: 0; color: #193c32; font-size: 26px; }
.page-head p { margin: 7px 0 0; color: #718079; }
.head-actions { display: flex; gap: 10px; }
.settings-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 20px; }
.setting-card { min-height: 360px; padding: 24px; background: #fff; border: 1px solid #e2e9e6; border-radius: 14px; box-shadow: 0 8px 24px rgb(18 63 51 / 6%); }
.card-title { display: flex; gap: 13px; margin-bottom: 24px; }
.card-title .step { display: grid; flex: 0 0 34px; height: 34px; place-items: center; color: #fff; font-weight: 700; background: #258460; border-radius: 10px; }
.card-title h2 { margin: 1px 0 5px; color: #213e35; font-size: 18px; }
.card-title p { margin: 0; color: #7b8984; font-size: 13px; }
.selected-list { display: grid; gap: 10px; margin-top: 18px; }
.selected-contact { display: flex; align-items: center; gap: 11px; padding: 10px 12px; background: #f5f9f7; border-radius: 10px; }
.selected-contact strong, .selected-contact small { display: block; }
.selected-contact strong { color: #2c443c; font-size: 14px; }
.selected-contact small { margin-top: 3px; color: #829089; }
.auto-fields { display: grid; gap: 8px; margin-top: 18px; padding: 14px; background: #f5f9f7; border-radius: 10px; }
.auto-fields span { color: #678078; font-size: 12px; }
.auto-fields code { color: #2d5b4c; font-family: inherit; font-size: 13px; }
@media (max-width: 960px) { .settings-grid { grid-template-columns: 1fr; } }
</style>
