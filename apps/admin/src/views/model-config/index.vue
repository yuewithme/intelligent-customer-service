<template>
  <ContentWrap>
    <div class="page-head">
      <div>
        <h1>模型配置</h1>
        <p>管理智能客服使用的服务配置。</p>
      </div>
    </div>

    <ElAlert v-if="readonly" title="仅管理员可查看和修改微信接入配置" type="info" :closable="false" />
    <section v-else v-loading="loading" class="setting-card">
      <h2>微信接入配置</h2>
      <p class="description">填写当前客服微信的 WID 和 WCID，保存后立即生效，服务重启后仍保留。</p>
      <ElAlert v-if="loadFailed" title="配置加载失败，请重新加载后再编辑" type="error" :closable="false" />
      <ElForm label-position="top" :disabled="loading || saving || !loaded" @submit.prevent="saveSettings">
        <ElFormItem label="WID（登录实例 ID）" required>
          <ElInput v-model="form.w_id" placeholder="请输入当前登录实例的 wId" maxlength="256" autocomplete="off" />
          <div class="field-help">微信登录后获得的实例标识，重新登录后可能变化。</div>
        </ElFormItem>
        <ElFormItem label="WCID（客服微信 ID）" required>
          <ElInput v-model="form.wc_id" placeholder="请输入客服账号的 wcId，例如 wxid_…" maxlength="256" autocomplete="off" />
          <div class="field-help">填写当前客服账号的微信原始 ID，不是客户 ID 或昵称。</div>
        </ElFormItem>
        <p class="monitor-help">登录监控获取到该账号的新 WID 时会自动同步，可重新加载查看最新值。</p>
        <ElButton type="primary" native-type="submit" :loading="saving">保存配置</ElButton>
      </ElForm>
      <ElButton class="reload-button" :disabled="loading || saving" @click="loadSettings">重新加载</ElButton>
    </section>
  </ContentWrap>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getEyunAccountSettings, updateEyunAccountSettings } from '@/api/admin/modelConfig'
import { isAdmin } from '@/utils/gate'

const readonly = !isAdmin()
const loading = ref(false)
const saving = ref(false)
const loaded = ref(false)
const loadFailed = ref(false)
const form = reactive({ w_id: '', wc_id: '' })

const loadSettings = async () => {
  loading.value = true
  loaded.value = false
  loadFailed.value = false
  try {
    Object.assign(form, await getEyunAccountSettings())
    loaded.value = true
  } catch {
    loadFailed.value = true
  } finally {
    loading.value = false
  }
}

const saveSettings = async () => {
  if (!loaded.value || loading.value || saving.value) return
  const data = { w_id: form.w_id.trim(), wc_id: form.wc_id.trim() }
  if (!data.w_id || !data.wc_id || /\s/.test(data.w_id) || /\s/.test(data.wc_id)) {
    ElMessage.warning('请填写完整的 WID 和 WCID，标识中不能包含空白字符')
    return
  }
  saving.value = true
  try {
    Object.assign(form, await updateEyunAccountSettings(data))
    ElMessage.success('配置已保存，后端已同步更新')
  } catch {
    // The shared request handler displays the save error; retain the user's input.
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  if (!readonly) void loadSettings()
})
</script>

<style scoped>
.description, .field-help, .monitor-help { color: var(--el-text-color-secondary); }
.setting-card { max-width: 760px; padding: 28px; border: 1px solid var(--el-border-color-light); border-radius: 12px; background: var(--el-bg-color); }
.setting-card h2 { margin: 0 0 12px; font-size: 18px; }
.description { margin-bottom: 28px; line-height: 1.7; }
.field-help { margin-top: 6px; font-size: 13px; line-height: 1.6; }
.monitor-help { font-size: 14px; line-height: 1.7; }
.reload-button { margin-top: 12px; }
@media (max-width: 640px) { .setting-card { padding: 20px; } }
</style>
