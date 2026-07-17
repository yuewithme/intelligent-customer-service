<template>
  <ContentWrap>
    <div class="page-head">
      <div>
        <h1>微信素材库</h1>
        <p>批量发送的图片、视频必须先在素材群中生成 XML，再通过转发接口使用。</p>
      </div>
      <ElButton @click="load">刷新</ElButton>
    </div>

    <ElAlert
      title="将图片或视频发送到配置的微信素材群，系统会从 Eyun 回调中自动保存原始 XML。"
      type="info"
      show-icon
      :closable="false"
    />

    <div class="filters">
      <ElInput v-model="keyword" clearable placeholder="搜索素材名称" @keyup.enter="load" />
      <ElSelect v-model="status" clearable placeholder="全部状态">
        <ElOption label="可用" value="ready" />
        <ElOption label="已失效" value="expired" />
        <ElOption label="已停用" value="disabled" />
      </ElSelect>
      <ElButton type="primary" @click="load">查询</ElButton>
    </div>

    <ElTable :data="items" v-loading="loading">
      <ElTableColumn label="预览" width="120">
        <template #default="{ row }">
          <ElImage v-if="row.media_type === 'image' && row.preview_url" class="thumb" :src="row.preview_url" fit="cover" />
          <video v-else-if="row.preview_url" class="thumb" :src="row.preview_url" muted></video>
          <span v-else>{{ row.media_type === 'image' ? '图片' : '视频' }}</span>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="name" label="素材名称" min-width="220" />
      <ElTableColumn label="类型" width="90"><template #default="{ row }">{{ row.media_type === 'image' ? '图片' : '视频' }}</template></ElTableColumn>
      <ElTableColumn label="状态" width="100"><template #default="{ row }"><ElTag :type="statusType(row.status)">{{ statusText(row.status) }}</ElTag></template></ElTableColumn>
      <ElTableColumn prop="source_message_id" label="来源消息" min-width="150" />
      <ElTableColumn prop="last_error" label="最近错误" min-width="220" show-overflow-tooltip />
      <ElTableColumn label="操作" width="150">
        <template #default="{ row }"><ElButton link type="primary" @click="edit(row)">编辑</ElButton></template>
      </ElTableColumn>
    </ElTable>

    <ElPagination v-model:current-page="page" :page-size="pageSize" :total="total" layout="total, prev, pager, next" @current-change="load" />

    <h2>批量发送任务</h2>
    <ElTable :data="jobs" v-loading="jobsLoading">
      <ElTableColumn prop="id" label="ID" width="80" />
      <ElTableColumn prop="source_type" label="来源" />
      <ElTableColumn prop="status" label="状态" />
      <ElTableColumn prop="total_count" label="总消息" />
      <ElTableColumn prop="queued_count" label="排队中" />
      <ElTableColumn prop="sent_count" label="已发送" />
      <ElTableColumn prop="failed_count" label="失败" />
    </ElTable>

    <ElDialog v-model="dialog" title="编辑微信素材" width="460px">
      <ElForm label-position="top">
        <ElFormItem label="名称"><ElInput v-model="form.name" /></ElFormItem>
        <ElFormItem label="状态"><ElSelect v-model="form.status"><ElOption label="可用" value="ready" /><ElOption label="已失效" value="expired" disabled /><ElOption label="停用" value="disabled" /></ElSelect></ElFormItem>
      </ElForm>
      <template #footer><ElButton @click="dialog = false">取消</ElButton><ElButton type="primary" :loading="saving" @click="save">保存</ElButton></template>
    </ElDialog>
  </ContentWrap>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getWechatBulkJobs, getWechatMaterials, updateWechatMaterial, type WechatMaterial } from '@/api/admin/wechatMaterials'

const items = ref<WechatMaterial[]>([])
const jobs = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 50
const keyword = ref('')
const status = ref('')
const loading = ref(false)
const jobsLoading = ref(false)
const dialog = ref(false)
const saving = ref(false)
const editingId = ref<number>()
const form = reactive<{ name: string; status: 'ready' | 'expired' | 'disabled' }>({ name: '', status: 'ready' })

const load = async () => {
  loading.value = true
  jobsLoading.value = true
  try {
    const [materials, bulkJobs] = await Promise.all([
      getWechatMaterials(page.value, pageSize, keyword.value, status.value),
      getWechatBulkJobs(1, 50)
    ])
    items.value = materials.items
    total.value = materials.total
    jobs.value = bulkJobs.items
  } finally { loading.value = false; jobsLoading.value = false }
}
const edit = (row: WechatMaterial) => { editingId.value = row.id; form.name = row.name; form.status = row.status; dialog.value = true }
const save = async () => { if (!editingId.value || !form.name.trim()) return; saving.value = true; try { await updateWechatMaterial(editingId.value, { name: form.name.trim(), status: form.status }); ElMessage.success('素材已更新'); dialog.value = false; await load() } finally { saving.value = false } }
const statusText = (value: string) => ({ ready: '可用', expired: '已失效', disabled: '已停用' }[value] || value)
const statusType = (value: string) => value === 'ready' ? 'success' : value === 'expired' ? 'danger' : 'info'
onMounted(load)
</script>

<style scoped>
.page-head{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:18px}.page-head h1{margin:0;color:#18352d}.page-head p{margin:7px 0 0;color:#708079}.filters{display:flex;gap:12px;margin:18px 0}.filters .el-input{max-width:320px}.filters .el-select{width:160px}.thumb{width:88px;height:58px;border-radius:7px;background:#eef3f1}.el-pagination{justify-content:flex-end;margin-top:16px}h2{margin-top:32px;color:#18352d}
</style>
