<template>
  <div class="knowledge-page">
    <div class="page-head">
      <div>
        <h2>产品知识库</h2>
        <p>AI 商品问答直接读取这里；关联有赞商品后，该商品才会出现在“已关联商品”中。</p>
      </div>
      <ElButton type="primary" @click="openCreate">新增产品知识</ElButton>
    </div>

    <div class="summary">
      <span>知识总数 <strong>{{ total }}</strong></span>
      <span>已关联 <strong>{{ linkedCount }}</strong></span>
      <span>待关联 <strong>{{ Math.max(0, total - linkedCount) }}</strong></span>
    </div>

    <div class="toolbar">
      <ElInput
        v-model="keyword"
        clearable
        placeholder="搜索产品名称、类别或特征"
        @keyup.enter="applyFilters"
        @clear="applyFilters"
      />
      <ElSelect v-model="linkedFilter" clearable placeholder="全部关联状态" @change="applyFilters">
        <ElOption label="已关联" value="true" />
        <ElOption label="待关联" value="false" />
      </ElSelect>
      <ElButton @click="applyFilters">查询</ElButton>
    </div>

    <ElTable v-loading="loading" :data="items" row-key="id">
      <ElTableColumn prop="product_name" label="产品名称" min-width="150" fixed="left" />
      <ElTableColumn label="关联有赞商品" min-width="240">
        <template #default="scope">
          <div v-if="scope.row.linked_product" class="linked-product">
            <ElTag type="success" size="small">已关联</ElTag>
            <span>{{ scope.row.linked_product.title }}</span>
          </div>
          <ElTag v-else type="warning" size="small">待关联</ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="category" label="类别" width="110" />
      <ElTableColumn prop="flower_color" label="花色" width="130" show-overflow-tooltip />
      <ElTableColumn prop="fragrance" label="香味" width="130" show-overflow-tooltip />
      <ElTableColumn prop="flowering_status" label="是否带花" width="110" />
      <ElTableColumn prop="bloom_period" label="花期" width="120" show-overflow-tooltip />
      <ElTableColumn prop="audience_tag" label="适合人群" width="140" show-overflow-tooltip />
      <ElTableColumn prop="highlighted_features" label="突出特征" min-width="220" show-overflow-tooltip />
      <ElTableColumn label="操作" width="120" fixed="right">
        <template #default="scope">
          <ElButton link type="primary" @click="openEdit(scope.row)">编辑</ElButton>
          <ElPopconfirm title="确定删除这条产品知识吗？" @confirm="remove(scope.row)">
            <template #reference><ElButton link type="danger">删除</ElButton></template>
          </ElPopconfirm>
        </template>
      </ElTableColumn>
    </ElTable>

    <div class="pagination">
      <ElPagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        @change="loadKnowledge"
      />
    </div>

    <ElDialog v-model="dialogVisible" :title="editingId ? '编辑产品知识' : '新增产品知识'" width="760px">
      <ElForm label-position="top" :model="form">
        <div class="form-grid">
          <ElFormItem label="产品名称" required>
            <ElInput v-model="form.product_name" maxlength="256" />
          </ElFormItem>
          <ElFormItem label="关联有赞商品">
            <ElSelect v-model="form.item_id" clearable filterable placeholder="不关联则不会在商品页显示">
              <ElOption
                v-for="option in productOptions"
                :key="option.item_id"
                :label="option.title"
                :value="option.item_id"
                :disabled="option.linked && option.item_id !== originalItemId"
              />
            </ElSelect>
          </ElFormItem>
          <ElFormItem v-for="field in shortFields" :key="field.key" :label="field.label">
            <ElInput v-model="form[field.key]" />
          </ElFormItem>
        </div>
        <ElFormItem v-for="field in longFields" :key="field.key" :label="field.label">
          <ElInput v-model="form[field.key]" type="textarea" :rows="3" />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="dialogVisible = false">取消</ElButton>
        <ElButton type="primary" :loading="saving" @click="save">保存</ElButton>
      </template>
    </ElDialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  createProductKnowledge,
  deleteProductKnowledge,
  getProductKnowledge,
  getProductOptions,
  updateProductKnowledge,
  type ProductKnowledgeItem,
  type ProductKnowledgePayload,
  type ProductOption
} from '@/api/admin/products'

type KnowledgeTextKey = Exclude<keyof ProductKnowledgePayload, 'item_id' | 'product_name'>
const emptyForm = (): ProductKnowledgePayload => ({
  item_id: null,
  product_name: '',
  category: '',
  flower_color: '',
  fragrance: '',
  flowering_status: '',
  price_budget: '',
  care_scenes: '',
  bloom_period: '',
  audience_tag: '',
  market_price: '',
  highlighted_features: '',
  sales_copy: ''
})

const shortFields: Array<{ key: KnowledgeTextKey; label: string }> = [
  { key: 'category', label: '所属类别' },
  { key: 'flower_color', label: '花色' },
  { key: 'fragrance', label: '香味' },
  { key: 'flowering_status', label: '是否带花' },
  { key: 'bloom_period', label: '花期' },
  { key: 'audience_tag', label: '适合人群标签' }
]
const longFields: Array<{ key: KnowledgeTextKey; label: string }> = [
  { key: 'price_budget', label: '价格预算' },
  { key: 'care_scenes', label: '适合养护场景' },
  { key: 'market_price', label: '市场价' },
  { key: 'highlighted_features', label: '需要突出的特征' },
  { key: 'sales_copy', label: '塑品话术' }
]

const items = ref<ProductKnowledgeItem[]>([])
const productOptions = ref<ProductOption[]>([])
const total = ref(0)
const linkedCount = ref(0)
const page = ref(1)
const pageSize = ref(50)
const keyword = ref('')
const linkedFilter = ref('')
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const originalItemId = ref<string | null>(null)
const form = reactive<ProductKnowledgePayload>(emptyForm())

const loadKnowledge = async () => {
  loading.value = true
  try {
    const data = await getProductKnowledge({
      page: page.value,
      page_size: pageSize.value,
      keyword: keyword.value.trim() || undefined,
      linked: linkedFilter.value ? linkedFilter.value === 'true' : undefined
    })
    items.value = data.items
    total.value = data.total
    linkedCount.value = data.linked_count
  } finally {
    loading.value = false
  }
}

const loadOptions = async () => { productOptions.value = await getProductOptions() }
const applyFilters = () => { page.value = 1; void loadKnowledge() }
const resetForm = (value: Partial<ProductKnowledgePayload> = {}) => Object.assign(form, emptyForm(), value)

const openCreate = async () => {
  editingId.value = null
  originalItemId.value = null
  resetForm()
  await loadOptions()
  dialogVisible.value = true
}

const openEdit = async (item: ProductKnowledgeItem) => {
  editingId.value = item.id
  originalItemId.value = item.item_id || null
  resetForm(item)
  await loadOptions()
  dialogVisible.value = true
}

const save = async () => {
  if (!form.product_name.trim()) {
    ElMessage.warning('请填写产品名称')
    return
  }
  saving.value = true
  try {
    const payload = { ...form, product_name: form.product_name.trim() }
    if (editingId.value) await updateProductKnowledge(editingId.value, payload)
    else await createProductKnowledge(payload)
    ElMessage.success('产品知识已保存')
    dialogVisible.value = false
    await loadKnowledge()
  } finally {
    saving.value = false
  }
}

const remove = async (item: ProductKnowledgeItem) => {
  await deleteProductKnowledge(item.id)
  ElMessage.success('产品知识已删除')
  await loadKnowledge()
}

onMounted(loadKnowledge)
</script>

<style scoped>
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin: 8px 0 18px; }
.page-head h2 { margin: 0; color: #163d32; font-size: 22px; }
.page-head p { margin: 7px 0 0; color: #71827c; }
.summary { display: flex; gap: 24px; padding: 14px 18px; margin-bottom: 16px; background: #f4f8f6; border: 1px solid #dfe9e5; border-radius: 10px; color: #64756f; }
.summary strong { margin-left: 5px; color: #173d32; }
.toolbar { display: grid; grid-template-columns: minmax(260px, 1fr) 170px auto; gap: 10px; margin-bottom: 16px; }
.linked-product { display: flex; align-items: center; gap: 8px; }
.pagination { display: flex; justify-content: flex-end; padding-top: 18px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 18px; }
.form-grid :deep(.el-select) { width: 100%; }
@media (max-width: 760px) { .toolbar, .form-grid { grid-template-columns: 1fr; } .summary { flex-direction: column; gap: 8px; } }
</style>
