<template>
  <section class="accounts-page">
    <div class="page-head">
      <div><h1>账号与权限</h1><p>新增、删除账号，设置登录账密并分配页面和客服微信权限。</p></div>
      <ElButton type="primary" :disabled="loading" @click="openCreate">创建账号</ElButton>
    </div>
    <ElAlert title="管理员拥有全部权限；测试账号仅观看和使用演示会话；员工未选择微信时可访问全部微信，选择后仅可操作所选微信，其他页面为只读。" type="info" :closable="false" />
    <ElTable v-loading="loading" :data="accounts" class="account-table">
      <ElTableColumn prop="username" label="账号" min-width="140" />
      <ElTableColumn prop="display_name" label="姓名" min-width="110" />
      <ElTableColumn label="角色" width="100"><template #default="{ row }">{{ roleLabel(row.role) }}</template></ElTableColumn>
      <ElTableColumn label="状态" width="90"><template #default="{ row }"><ElTag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</ElTag></template></ElTableColumn>
      <ElTableColumn label="页面权限" min-width="220"><template #default="{ row }">{{ row.role === 'admin' ? '全部页面' : row.pages.map((path: string) => pageTitle(path)).join('、') || '未分配' }}</template></ElTableColumn>
      <ElTableColumn label="可操作微信" min-width="200"><template #default="{ row }">{{ row.role === 'test' ? '仅演示会话' : row.role === 'admin' || !row.wechat_ids.length ? '全部微信' : row.wechat_ids.map((id: string) => wechatTitle(id)).join('、') }}</template></ElTableColumn>
      <ElTableColumn label="操作" width="200" fixed="right"><template #default="{ row }">
        <ElButton link type="primary" @click="openEdit(row)">编辑账号</ElButton>
        <ElButton link type="danger" :disabled="row.id === currentAccount?.id" @click="removeAccount(row)">删除账号</ElButton>
      </template></ElTableColumn>
    </ElTable>

    <ElDialog v-model="editing" :title="editingId ? '编辑账号与权限' : '创建账号'" width="min(640px, 94vw)" :close-on-click-modal="false">
      <ElForm label-position="top" :disabled="saving">
        <ElFormItem label="账号" required><ElInput v-model="form.username" placeholder="3–64 位字母、数字、点、下划线或短横线" autocomplete="off" maxlength="64" /></ElFormItem>
        <ElFormItem label="姓名" required><ElInput v-model="form.display_name" maxlength="64" /></ElFormItem>
        <ElFormItem :label="editingId ? '设置新密码' : '初始密码'"><ElInput v-model="form.password" show-password type="password" autocomplete="new-password" maxlength="256" :placeholder="editingId ? '留空保留原密码，设置时至少 6 位' : '留空自动生成，手动设置至少 6 位'" /></ElFormItem>
        <ElFormItem label="角色"><ElRadioGroup v-model="form.role" :disabled="editingId === currentAccount?.id">
          <ElRadioButton value="employee">员工</ElRadioButton><ElRadioButton value="test">测试</ElRadioButton><ElRadioButton value="admin">管理员</ElRadioButton>
        </ElRadioGroup></ElFormItem>
        <ElFormItem label="账号状态"><ElSwitch v-model="form.enabled" :disabled="editingId === currentAccount?.id" active-text="启用" inactive-text="停用" /></ElFormItem>
        <ElFormItem v-if="form.role !== 'admin'" label="页面观看权限">
          <ElCheckboxGroup v-model="form.pages" class="page-options"><ElCheckbox v-for="page in options.pages" :key="page.path" :value="page.path">{{ page.title }}</ElCheckbox></ElCheckboxGroup>
        </ElFormItem>
        <ElFormItem v-if="form.role === 'employee'" label="允许操作的客服微信">
          <ElSelect v-model="form.wechat_ids" multiple filterable placeholder="不选择则默认全部微信" style="width: 100%">
            <ElOption v-for="wechat in options.wechats" :key="wechat.wc_id" :value="wechat.wc_id" :label="wechatTitle(wechat.wc_id)" />
          </ElSelect>
        </ElFormItem>
      </ElForm>
      <template #footer><ElButton @click="editing = false">取消</ElButton><ElButton type="primary" :loading="saving" @click="save">保存</ElButton></template>
    </ElDialog>

    <ElDialog v-model="showCredentials" title="账号凭据" width="min(480px, 94vw)" :close-on-click-modal="false" @closed="credentials = null">
      <ElAlert title="请现在复制并分配给使用人。关闭后无法再次查看密码。" type="warning" :closable="false" />
      <dl v-if="credentials"><dt>账号</dt><dd>{{ credentials.username }}</dd><dt>密码</dt><dd class="password">{{ credentials.password }}</dd></dl>
      <template #footer><ElButton @click="copyCredentials">复制账密</ElButton><ElButton type="primary" @click="finishCredentials">已保存，关闭</ElButton></template>
    </ElDialog>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/config/axios'
import { currentAccount, setAccount, type Account, type GateRole } from '@/utils/gate'

const base = '/api/v1/admin/accounts'
const accounts = ref<Account[]>([])
const options = ref<{ pages: { path: string; title: string }[]; wechats: { wc_id: string; display_name?: string }[] }>({ pages: [], wechats: [] })
const loading = ref(false)
const saving = ref(false)
const editing = ref(false)
const editingId = ref<number | null>(null)
const showCredentials = ref(false)
const credentials = ref<{ username: string; password: string } | null>(null)
const form = reactive({ username: '', display_name: '', password: '', role: 'employee' as GateRole, enabled: true, pages: ['/workbench'], wechat_ids: [] as string[] })
const roleLabel = (role: GateRole) => ({ admin: '管理员', test: '测试', employee: '员工' })[role]
const pageTitle = (path: string) => options.value.pages.find(page => page.path === path)?.title || path
const wechatTitle = (id: string) => {
  const name = options.value.wechats.find(wechat => wechat.wc_id === id)?.display_name
  return name ? `${name}（${id}）` : id
}
const load = async () => {
  loading.value = true
  try {
    const [data, choices] = await Promise.all([request.get<{ items: Account[] }>({ url: base }), request.get<typeof options.value>({ url: `${base}/options` })])
    accounts.value = data.items
    options.value = { ...choices, wechats: choices.wechats.filter((w, i, all) => all.findIndex(item => item.wc_id === w.wc_id) === i) }
  } finally { loading.value = false }
}
const openCreate = () => {
  editingId.value = null
  Object.assign(form, { username: '', display_name: '', password: '', role: 'employee', enabled: true, pages: ['/workbench'], wechat_ids: [] })
  editing.value = true
}
const openEdit = (account: Account) => {
  editingId.value = account.id
  Object.assign(form, { ...account, pages: [...account.pages], wechat_ids: [...account.wechat_ids], password: '' })
  editing.value = true
}
const save = async () => {
  if (!form.display_name.trim() || !/^[a-zA-Z0-9_.-]{3,64}$/.test(form.username)) return ElMessage.warning('请填写姓名与有效账号')
  if (form.password && form.password.length < 6) return ElMessage.warning('密码至少 6 位')
  saving.value = true
  try {
    const data = { username: form.username, password: form.password || null, display_name: form.display_name.trim(), role: form.role, enabled: form.enabled, pages: form.pages, wechat_ids: form.role === 'employee' ? form.wechat_ids : [] }
    if (editingId.value) {
      const ownCredentialsChanged = editingId.value === currentAccount.value?.id && (form.username.toLowerCase() !== currentAccount.value.username || Boolean(form.password))
      const account = await request.put<Account>({ url: `${base}/${editingId.value}`, data })
      if (ownCredentialsChanged) {
        ElMessage.success('账密已保存，请使用新账密重新登录')
        window.location.assign('/gate')
        return
      }
      if (account.id === currentAccount.value?.id) setAccount(account)
    } else {
      const result = await request.post<{ account: Account; password: string }>({ url: base, data })
      credentials.value = { username: result.account.username, password: result.password }
      showCredentials.value = true
    }
    form.password = ''
    editing.value = false
    ElMessage.success('账号和权限已保存')
    await load()
  } finally { saving.value = false }
}
const removeAccount = async (account: Account) => {
  try { await ElMessageBox.confirm(`确定删除账号 ${account.username}？删除后该账号立即退出并无法登录，历史业务记录保留。`, '删除账号', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) } catch { return }
  await request.delete({ url: `${base}/${account.id}` })
  ElMessage.success('账号已删除')
  await load()
}
const copyCredentials = async () => {
  if (!credentials.value) return
  try {
    await navigator.clipboard.writeText(`账号：${credentials.value.username}\n密码：${credentials.value.password}`)
    ElMessage.success('已复制')
  } catch { ElMessage.info('请选中账号和密码手动复制') }
}
const finishCredentials = () => {
  showCredentials.value = false
}
onMounted(load)
</script>

<style scoped>
.accounts-page { padding: 28px; }
.page-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 22px; }
h1 { margin: 0 0 8px; font-size: 24px; }
p, dt { color: #687d74; }
p { margin: 0; }
.account-table { margin-top: 20px; }
.page-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
dt { margin-top: 20px; } dd { margin: 8px 0; overflow-wrap: anywhere; }
.password { font-family: monospace; font-size: 18px; user-select: all; }
@media (max-width: 640px) { .accounts-page { padding: 16px; } .page-head { align-items: flex-start; } }
</style>
