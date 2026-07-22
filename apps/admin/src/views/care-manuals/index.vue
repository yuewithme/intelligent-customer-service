<template>
  <ContentWrap>
    <div class="page-head">
      <div>
        <h1>养护手册</h1>
        <p>同步有赞已发布的“养护注意事项”笔记，只管理卡片元数据，不读取或写入笔记正文。</p>
      </div>
      <ElButton type="primary" :loading="syncing" @click="runSync">立即同步</ElButton>
    </div>

    <div class="stats-grid">
      <article><span>有效手册</span><strong>{{ stats.active }}</strong><small>已启用且有赞已发布</small></article>
      <article><span>停用手册</span><strong>{{ stats.disabled }}</strong><small>人工停用或来源不可用</small></article>
      <article :class="{ attention: stats.unbound > 0 }"><span>未绑定品种</span><strong>{{ stats.unbound }}</strong><small>需要补充标准兰花名称</small></article>
      <article><span>最近同步</span><strong class="sync-label">{{ syncStatusText }}</strong><small>{{ lastSyncText }}</small></article>
    </div>

    <ElAlert
      v-if="lastSync?.status === 'failed'"
      class="sync-alert"
      type="warning"
      :closable="false"
      show-icon
      :title="lastSync.error_message || '最近一次同步失败，旧数据未被覆盖'"
    />
    <div v-else-if="lastSync?.status === 'success'" class="sync-summary">
      最近一次共扫描 {{ lastSync.scanned_count }} 条，符合 {{ lastSync.qualified_count }} 条；
      新增 {{ lastSync.created_count }}、更新 {{ lastSync.updated_count }}、停用 {{ lastSync.disabled_count }}。
    </div>

    <div class="toolbar">
      <ElInput
        v-model="keyword"
        clearable
        placeholder="搜索手册名、兰花品种、别名、关键词或商品"
        @keyup.enter="applyFilters"
        @clear="applyFilters"
      />
      <ElSelect v-model="enabledFilter" clearable placeholder="全部启用状态" @change="applyFilters">
        <ElOption label="已启用" value="true" />
        <ElOption label="已停用" value="false" />
      </ElSelect>
      <ElSelect v-model="matchStatus" clearable placeholder="全部匹配状态" @change="applyFilters">
        <ElOption label="已绑定品种" value="bound" />
        <ElOption label="未绑定品种" value="unbound" />
      </ElSelect>
      <ElButton @click="applyFilters">查询</ElButton>
      <ElButton @click="openMatchDialog()">测试匹配</ElButton>
    </div>

    <ElTable v-loading="loading" :data="items" row-key="id" class="manual-table">
      <ElTableColumn label="手册" min-width="330">
        <template #default="scope">
          <div class="manual-cell">
            <ElImage :src="scope.row.cover_url || ''" fit="cover" preview-teleported>
              <template #error><div class="image-fallback">无封面</div></template>
            </ElImage>
            <div>
              <strong>{{ scope.row.title }}</strong>
              <small>笔记 ID：{{ scope.row.youzan_note_id }}</small>
              <ElLink :href="scope.row.note_url" target="_blank" type="primary">预览真实链接</ElLink>
            </div>
          </div>
        </template>
      </ElTableColumn>
      <ElTableColumn label="标准品种 / 别名" min-width="220">
        <template #default="scope">
          <strong v-if="scope.row.orchid_name" class="orchid-name">{{ scope.row.orchid_name }}</strong>
          <ElTag v-else type="warning" effect="plain">待绑定品种</ElTag>
          <div v-if="scope.row.aliases.length" class="tag-list">
            <ElTag v-for="alias in scope.row.aliases" :key="alias" size="small" effect="plain">{{ alias }}</ElTag>
          </div>
        </template>
      </ElTableColumn>
      <ElTableColumn label="关联商品" min-width="220">
        <template #default="scope">
          <div v-if="scope.row.product_links.length" class="product-links">
            <ElTag v-for="link in scope.row.product_links" :key="link.youzan_item_id" size="small" type="success" effect="plain">
              {{ link.product_name }}
            </ElTag>
          </div>
          <span v-else class="muted">未关联</span>
        </template>
      </ElTableColumn>
      <ElTableColumn label="状态" width="120">
        <template #default="scope">
          <ElTag :type="scope.row.available ? 'success' : 'info'">{{ scope.row.available ? '有效' : '停用' }}</ElTag>
          <small class="source-status">有赞：{{ scope.row.youzan_status }}</small>
        </template>
      </ElTableColumn>
      <ElTableColumn label="排序" prop="sort_order" width="80" />
      <ElTableColumn label="发布时间" width="170">
        <template #default="scope">{{ formatTime(scope.row.published_at) }}</template>
      </ElTableColumn>
      <ElTableColumn label="操作" width="150" fixed="right">
        <template #default="scope">
          <ElButton link type="primary" @click="openEdit(scope.row)">编辑</ElButton>
          <ElButton link type="primary" @click="openMatchDialog(scope.row)">测试匹配</ElButton>
        </template>
      </ElTableColumn>
      <template #empty><ElEmpty description="暂无养护手册；可先执行安全全量同步" /></template>
    </ElTable>

    <div class="pagination">
      <ElPagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        @change="loadManuals"
      />
    </div>

    <ElDialog v-model="editVisible" title="编辑养护手册" width="680px" destroy-on-close>
      <div v-if="editing" class="edit-title">
        <ElImage :src="editing.cover_url || ''" fit="cover">
          <template #error><div class="image-fallback">无封面</div></template>
        </ElImage>
        <div><strong>{{ editing.title }}</strong><small>同步只更新标题、封面、链接和有赞状态，不覆盖以下人工配置。</small></div>
      </div>
      <ElForm label-position="top">
        <ElFormItem label="标准兰花品种">
          <ElInput v-model="form.orchid_name" maxlength="256" placeholder="例如：建兰玉白丹红" />
        </ElFormItem>
        <ElFormItem label="别名 / 商品标题关键词">
          <ElSelect v-model="form.aliases" multiple filterable allow-create default-first-option placeholder="输入后回车添加">
            <ElOption v-for="value in form.aliases" :key="value" :label="value" :value="value" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="关联有赞商品">
          <ElSelect v-model="form.youzan_item_ids" multiple filterable collapse-tags collapse-tags-tooltip placeholder="请选择已同步商品">
            <ElOption v-for="product in productOptions" :key="product.item_id" :label="product.title" :value="product.item_id">
              <span>{{ product.title }}</span><small class="option-id">ID {{ product.item_id }}</small>
            </ElOption>
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="人工匹配关键词">
          <ElSelect v-model="form.match_keywords" multiple filterable allow-create default-first-option placeholder="仅用于候选匹配，输入后回车添加">
            <ElOption v-for="value in form.match_keywords" :key="value" :label="value" :value="value" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="卡片描述（不取笔记正文）">
          <ElInput v-model="form.card_description" type="textarea" :rows="3" maxlength="2000" show-word-limit />
        </ElFormItem>
        <div class="form-row">
          <ElFormItem label="排序优先级"><ElInputNumber v-model="form.sort_order" :min="-1000000" :max="1000000" /></ElFormItem>
          <ElFormItem label="允许匹配与发送"><ElSwitch v-model="form.enabled" active-text="启用" inactive-text="停用" /></ElFormItem>
        </div>
      </ElForm>
      <template #footer>
        <ElButton @click="editVisible = false">取消</ElButton>
        <ElButton type="primary" :loading="saving" @click="saveEdit">保存</ElButton>
      </template>
    </ElDialog>

    <ElDialog v-model="matchVisible" title="测试匹配（不会发送微信消息）" width="720px" destroy-on-close>
      <ElAlert title="这里只调用确定性匹配服务；不会进入消息队列，也不会联系真实客户。" type="info" :closable="false" show-icon />
      <div class="match-form">
        <ElInput v-model="matchForm.query" placeholder="用户说法，例如：玉白丹红" />
        <ElInput v-model="matchForm.product_name" placeholder="商品名称（可选）" />
        <ElSelect v-model="matchForm.youzan_item_id" clearable filterable placeholder="精确关联商品（可选）">
          <ElOption v-for="product in productOptions" :key="product.item_id" :label="product.title" :value="product.item_id" />
        </ElSelect>
        <ElButton type="primary" :loading="matching" @click="runMatch">开始匹配</ElButton>
      </div>
      <div v-if="matchResult" class="match-result">
        <ElTag :type="decisionType">{{ decisionText }}</ElTag>
        <span>{{ matchResult.auto_send_eligible ? '结果唯一，可供受控流程选择' : '不会自动选择或发送' }}</span>
        <ElTable :data="matchResult.matches" size="small">
          <ElTableColumn label="匹配手册" min-width="260">
            <template #default="scope"><strong>{{ scope.row.title }}</strong><small>{{ scope.row.orchid_name || '未绑定品种' }}</small></template>
          </ElTableColumn>
          <ElTableColumn label="匹配方式" width="140">
            <template #default="scope">{{ matchTypeText(scope.row.match_type) }}</template>
          </ElTableColumn>
          <ElTableColumn label="结果" width="100">
            <template #default="scope"><ElTag v-if="scope.row.selected" type="success">首选</ElTag><span v-else>候选</span></template>
          </ElTableColumn>
          <ElTableColumn label="链接" width="80"><template #default="scope"><ElLink :href="scope.row.note_url" target="_blank">预览</ElLink></template></ElTableColumn>
        </ElTable>
      </div>
    </ElDialog>
  </ContentWrap>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getProductOptions, type ProductOption } from '@/api/admin/products'
import {
  getCareManuals,
  syncCareManuals,
  testCareManualMatch,
  updateCareManual,
  type CareManualItem,
  type CareManualMatchResult,
  type CareManualPayload,
  type CareManualSyncRun
} from '@/api/admin/careManuals'

const items = ref<CareManualItem[]>([])
const productOptions = ref<ProductOption[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const keyword = ref('')
const enabledFilter = ref('')
const matchStatus = ref('')
const stats = reactive({ active: 0, disabled: 0, unbound: 0 })
const lastSync = ref<CareManualSyncRun | null>(null)
const loading = ref(false)
const syncing = ref(false)
const saving = ref(false)
const matching = ref(false)
const editVisible = ref(false)
const matchVisible = ref(false)
const editing = ref<CareManualItem | null>(null)
const matchResult = ref<CareManualMatchResult | null>(null)
const form = reactive<CareManualPayload>({ orchid_name: '', aliases: [], youzan_item_ids: [], card_description: '', sort_order: 0, enabled: true, match_keywords: [] })
const matchForm = reactive({ query: '', product_name: '', youzan_item_id: '', limit: 5 })

const syncStatusText = computed(() => lastSync.value
  ? ({ running: '同步中', success: '同步成功', failed: '同步失败' }[lastSync.value.status])
  : '尚未同步')
const lastSyncText = computed(() => lastSync.value ? formatTime(lastSync.value.finished_at || lastSync.value.started_at) : '暂无同步记录')
const decisionText = computed(() => ({ unique: '唯一匹配', ambiguous: '多个候选', not_found: '未匹配' }[matchResult.value?.decision || 'not_found']))
const decisionType = computed(() => ({ unique: 'success', ambiguous: 'warning', not_found: 'info' }[matchResult.value?.decision || 'not_found'] as 'success' | 'warning' | 'info'))

const loadManuals = async () => {
  loading.value = true
  try {
    const data = await getCareManuals({
      page: page.value,
      page_size: pageSize.value,
      keyword: keyword.value.trim() || undefined,
      enabled: enabledFilter.value ? enabledFilter.value === 'true' : undefined,
      match_status: matchStatus.value || undefined
    })
    items.value = data.items
    total.value = data.total
    Object.assign(stats, data.stats)
    lastSync.value = data.last_sync || null
  } finally { loading.value = false }
}

const loadProductOptions = async () => { productOptions.value = await getProductOptions() }
const applyFilters = () => { page.value = 1; void loadManuals() }

const runSync = async () => {
  syncing.value = true
  try {
    const result = await syncCareManuals()
    ElMessage.success(`同步完成：扫描 ${result.scanned_count} 条，收录 ${result.qualified_count} 条`)
  } finally {
    syncing.value = false
    await loadManuals()
  }
}

const openEdit = (item: CareManualItem) => {
  editing.value = item
  Object.assign(form, {
    orchid_name: item.orchid_name || '', aliases: [...item.aliases],
    youzan_item_ids: item.product_links.map(link => link.youzan_item_id),
    card_description: item.card_description || '', sort_order: item.sort_order,
    enabled: item.enabled, match_keywords: [...item.match_keywords]
  })
  editVisible.value = true
}

const saveEdit = async () => {
  if (!editing.value) return
  saving.value = true
  try {
    await updateCareManual(editing.value.id, { ...form })
    ElMessage.success('养护手册配置已保存')
    editVisible.value = false
    await loadManuals()
  } finally { saving.value = false }
}

const openMatchDialog = (item?: CareManualItem) => {
  matchResult.value = null
  Object.assign(matchForm, { query: item?.orchid_name || '', product_name: '', youzan_item_id: item?.product_links[0]?.youzan_item_id || '', limit: 5 })
  matchVisible.value = true
}

const runMatch = async () => {
  if (!matchForm.query.trim() && !matchForm.product_name.trim() && !matchForm.youzan_item_id) {
    ElMessage.warning('请至少输入一种匹配条件'); return
  }
  matching.value = true
  try { matchResult.value = await testCareManualMatch({ ...matchForm }) } finally { matching.value = false }
}

const formatTime = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
const matchTypeText = (value: string) => ({ exact_product: '商品精确关联', exact_orchid: '标准品种精确', exact_alias: '人工别名精确', keyword: '人工关键词', candidate: '模糊候选' }[value] || value)

onMounted(async () => { await Promise.all([loadManuals(), loadProductOptions()]) })
</script>

<style scoped>
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 18px; }
.page-head h1 { margin: 0; color: #163d32; font-size: 25px; }
.page-head p { margin: 7px 0 0; color: #71827c; }
.stats-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.stats-grid article { padding: 17px 18px; background: #fff; border: 1px solid #dfe8e4; border-radius: 11px; box-shadow: 0 2px 10px rgb(28 66 53 / 4%); }
.stats-grid article.attention { background: #fffaf0; border-color: #efd8a8; }
.stats-grid span, .stats-grid small { display: block; color: #71827c; }
.stats-grid strong { display: block; margin: 7px 0 5px; color: #173d32; font-size: 28px; }
.stats-grid .sync-label { font-size: 20px; }
.sync-alert, .sync-summary { margin-bottom: 16px; }
.sync-summary { padding: 12px 16px; color: #45655a; background: #f2f8f5; border-radius: 9px; }
.toolbar { display: grid; grid-template-columns: minmax(260px, 1fr) 160px 160px auto auto; gap: 10px; margin-bottom: 16px; }
.manual-table { width: 100%; border-radius: 10px; }
.manual-cell { display: flex; align-items: center; gap: 12px; }
.manual-cell .el-image, .edit-title .el-image { flex: 0 0 auto; width: 66px; height: 66px; background: #eef3f1; border-radius: 9px; }
.manual-cell strong, .manual-cell small, .source-status, .match-result small, .edit-title small { display: block; }
.manual-cell small { margin: 5px 0 3px; color: #8b9994; }
.image-fallback { display: grid; width: 100%; height: 100%; place-items: center; color: #9aaba5; font-size: 12px; }
.orchid-name { color: #254f42; }
.tag-list, .product-links { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 7px; }
.muted, .source-status { color: #899792; }
.source-status { margin-top: 7px; font-size: 11px; }
.pagination { display: flex; justify-content: flex-end; padding-top: 18px; }
.edit-title { display: flex; align-items: center; gap: 14px; padding: 12px; margin-bottom: 16px; background: #f4f8f6; border-radius: 10px; }
.edit-title small { margin-top: 5px; color: #74857f; }
.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.edit-title + .el-form :deep(.el-select) { width: 100%; }
.option-id { float: right; margin-left: 24px; color: #9aa7a2; }
.match-form { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 16px 0; }
.match-result { display: grid; grid-template-columns: auto 1fr; align-items: center; gap: 10px; }
.match-result .el-table { grid-column: 1 / -1; }
.match-result small { margin-top: 4px; color: #80908a; }
@media (max-width: 1050px) { .stats-grid { grid-template-columns: 1fr 1fr; } .toolbar { grid-template-columns: 1fr 1fr; } }
</style>
