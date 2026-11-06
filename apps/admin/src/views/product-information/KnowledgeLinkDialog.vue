<template>
  <ElDialog v-model="visible" title="关联信息" width="640px" :close-on-click-modal="!saving" :close-on-press-escape="!saving" :show-close="!saving" @open="reset">
    <p class="target-name">{{ targetName }}</p>
    <div class="search-bar">
      <ElInput v-model="keyword" clearable placeholder="搜索产品知识名称" :disabled="saving" @keyup.enter="search" @clear="search" />
      <ElButton :disabled="saving" @click="search">搜索</ElButton>
    </div>
    <ElTable v-loading="loading" :data="items" row-key="id" max-height="360" @row-click="select">
      <ElTableColumn label="选择" width="65">
        <template #default="scope">
          <ElRadio :model-value="selected?.id" :value="scope.row.id" :disabled="saving" :aria-label="`选择 ${scope.row.product_name}`" @change="select(scope.row)"><span /></ElRadio>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="product_name" label="产品知识" min-width="220" />
      <ElTableColumn prop="category" label="类别" width="100" />
      <template #empty><ElEmpty description="未找到产品知识" :image-size="60" /></template>
    </ElTable>
    <div class="dialog-pagination">
      <ElPagination v-model:current-page="page" :page-size="20" :total="total" :disabled="saving" layout="total, prev, pager, next" @current-change="load" />
    </div>
    <p v-if="selected" class="selection">已选择：{{ selected.product_name }}</p>
    <template #footer>
      <ElButton :disabled="saving" @click="visible = false">取消</ElButton>
      <ElButton type="primary" :loading="saving" :disabled="!selected || loading" @click="confirm">确认关联</ElButton>
    </template>
  </ElDialog>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { getProductKnowledge, type ProductKnowledgeItem } from '@/api/admin/products'

const visible = defineModel<boolean>({ required: true })
const props = defineProps<{ targetName: string; saving: boolean }>()
const emit = defineEmits<{ confirm: [knowledge: ProductKnowledgeItem] }>()
const keyword = ref('')
const query = ref('')
const items = ref<ProductKnowledgeItem[]>([])
const selected = ref<ProductKnowledgeItem | null>(null)
const page = ref(1)
const total = ref(0)
const loading = ref(false)
let sequence = 0

const load = async () => {
  const current = ++sequence
  loading.value = true
  try {
    const data = await getProductKnowledge({ page: page.value, page_size: 20, keyword: query.value || undefined, name_only: true })
    if (current !== sequence) return
    items.value = data.items
    total.value = data.total
  } finally { if (current === sequence) loading.value = false }
}
const reset = () => {
  keyword.value = ''
  query.value = ''
  page.value = 1
  selected.value = null
  items.value = []
  total.value = 0
  void load()
}
const search = () => {
  query.value = keyword.value.trim()
  page.value = 1
  void load()
}
const select = (item: ProductKnowledgeItem) => { if (!props.saving) selected.value = item }
const confirm = () => { if (selected.value && !props.saving) emit('confirm', selected.value) }
onBeforeUnmount(() => { sequence++ })
</script>

<style scoped>
.target-name { margin: 0 0 16px; color: var(--app-text-secondary); }
.search-bar { display: flex; gap: 10px; margin-bottom: 12px; }
.dialog-pagination { display: flex; justify-content: flex-end; margin-top: 12px; }
.selection { margin-bottom: 0; color: var(--el-color-primary); }
</style>
