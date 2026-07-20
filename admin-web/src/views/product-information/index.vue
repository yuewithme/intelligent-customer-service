<template>
  <ContentWrap>
    <ElTabs v-model="activeTab">
      <ElTabPane label="已关联商品" name="products">
    <div class="page-head">
      <div>
        <h1>产品信息</h1>
        <p>只展示已有产品知识的有赞商品；每天自动同步价格、库存与规格，人工排序不会被覆盖。</p>
      </div>
      <ElButton type="primary" :loading="syncing" @click="runSync">立即同步有赞</ElButton>
    </div>

    <div class="sync-state">
      <span>商品总数 <strong>{{ total }}</strong></span>
      <span>最近同步 <strong>{{ lastSyncText }}</strong></span>
      <ElTag v-if="lastSync" :type="syncTagType">{{ syncStatusText }}</ElTag>
      <span v-if="lastSync?.detail_error_count" class="warning">
        {{ lastSync.detail_error_count }} 个商品规格未更新
      </span>
    </div>

    <div class="toolbar">
      <ElInput
        v-model="keyword"
        clearable
        placeholder="搜索商品名称或商品ID"
        @keyup.enter="applyFilters"
        @clear="applyFilters"
      />
      <ElSelect v-model="status" clearable placeholder="全部状态" @change="applyFilters">
        <ElOption label="出售中" value="on_sale" />
        <ElOption label="已下架" value="off_shelf" />
        <ElOption label="已售罄" value="sold_out" />
        <ElOption label="有赞中已不存在" value="missing" />
      </ElSelect>
      <ElSelect v-model="sortBy" @change="applyFilters">
        <ElOption label="人工排序" value="manual" />
        <ElOption label="商品名称" value="title" />
        <ElOption label="价格" value="price" />
        <ElOption label="库存" value="stock" />
        <ElOption label="有赞更新时间" value="updated_at" />
      </ElSelect>
      <ElSelect v-model="sortDirection" @change="applyFilters">
        <ElOption label="升序" value="asc" />
        <ElOption label="降序" value="desc" />
      </ElSelect>
      <ElButton @click="applyFilters">查询</ElButton>
    </div>

    <ElTable v-loading="loading" :data="items" row-key="item_id" class="product-table">
      <ElTableColumn type="expand" width="44">
        <template #default="scope">
          <div class="sku-panel">
            <strong>规格明细</strong>
            <ElTable v-if="scope.row.skus.length" :data="scope.row.skus" size="small">
              <ElTableColumn prop="spec_name" label="规格" min-width="220" />
              <ElTableColumn label="价格" width="120">
                <template #default="skuScope">{{ money(skuScope.row.price_cent) }}</template>
              </ElTableColumn>
              <ElTableColumn prop="stock" label="库存" width="100" />
              <ElTableColumn prop="sku_code" label="SKU编码" min-width="150" />
            </ElTable>
            <ElEmpty v-else description="该商品没有独立规格" :image-size="48" />
          </div>
        </template>
      </ElTableColumn>
      <ElTableColumn label="商品" min-width="300">
        <template #default="scope">
          <div class="product-cell">
            <ElImage :src="scope.row.image_url || ''" fit="cover">
              <template #error><div class="image-fallback">无图</div></template>
            </ElImage>
            <div>
              <strong>{{ scope.row.title || '未命名商品' }}</strong>
              <small>ID：{{ scope.row.item_id }}</small>
            </div>
          </div>
        </template>
      </ElTableColumn>
      <ElTableColumn label="状态" width="100">
        <template #default="scope">
          <ElTag :type="statusType(scope.row.status)">{{ statusText(scope.row.status) }}</ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn label="价格" width="120">
        <template #default="scope">{{ money(scope.row.price_cent) }}</template>
      </ElTableColumn>
      <ElTableColumn prop="stock" label="库存" width="90" />
      <ElTableColumn label="规格" width="90">
        <template #default="scope">{{ scope.row.sku_count }} 个</template>
      </ElTableColumn>
      <ElTableColumn label="排序" width="150">
        <template #default="scope">
          <ElInputNumber
            v-model="scope.row.sort_order"
            :min="-1000000"
            :max="1000000"
            controls-position="right"
            @change="saveSort(scope.row)"
          />
        </template>
      </ElTableColumn>
      <ElTableColumn label="内部备注" min-width="190">
        <template #default="scope">
          <ElInput
            v-model="scope.row.internal_note"
            maxlength="2000"
            placeholder="如：带花苞、3苗、带盆"
            @change="saveNote(scope.row)"
          />
        </template>
      </ElTableColumn>
      <ElTableColumn label="更新时间" width="170">
        <template #default="scope">{{ formatTime(scope.row.youzan_updated_at || scope.row.last_synced_at) }}</template>
      </ElTableColumn>
      <ElTableColumn label="操作" width="90" fixed="right">
        <template #default="scope">
          <ElLink v-if="scope.row.h5_url" :href="scope.row.h5_url" target="_blank" type="primary">查看商品</ElLink>
          <span v-else>-</span>
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
        @change="loadProducts"
      />
    </div>
      </ElTabPane>
      <ElTabPane label="产品知识库" name="knowledge">
        <KnowledgeTab />
      </ElTabPane>
    </ElTabs>
  </ContentWrap>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import KnowledgeTab from './KnowledgeTab.vue'
import {
  getProducts,
  syncProducts,
  updateProductNote,
  updateProductSort,
  type ProductItem,
  type ProductSyncRun
} from '@/api/admin/products'

const items = ref<ProductItem[]>([])
const activeTab = ref('products')
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const keyword = ref('')
const status = ref('')
const sortBy = ref('manual')
const sortDirection = ref('asc')
const loading = ref(false)
const syncing = ref(false)
const lastSync = ref<ProductSyncRun | null>(null)

const syncStatusText = computed(() => lastSync.value
  ? ({ running: '同步中', success: '同步成功', failed: '同步失败' }[lastSync.value.status])
  : '未同步')
const syncTagType = computed(() => lastSync.value
  ? ({ running: 'warning', success: 'success', failed: 'danger' }[lastSync.value.status] as 'warning' | 'success' | 'danger')
  : undefined)
const lastSyncText = computed(() => lastSync.value ? formatTime(lastSync.value.finished_at || lastSync.value.started_at) : '尚未同步')

const loadProducts = async () => {
  loading.value = true
  try {
    const data = await getProducts({
      page: page.value,
      page_size: pageSize.value,
      keyword: keyword.value.trim() || undefined,
      status: status.value || undefined,
      sort_by: sortBy.value,
      sort_direction: sortDirection.value
    })
    items.value = data.items
    total.value = data.total
    lastSync.value = data.last_sync || null
  } finally {
    loading.value = false
  }
}

const applyFilters = () => {
  page.value = 1
  void loadProducts()
}

const runSync = async () => {
  syncing.value = true
  try {
    const result = await syncProducts()
    ElMessage.success(`同步完成：${result.product_count} 个商品，${result.sku_count} 个规格`)
    await loadProducts()
  } finally {
    syncing.value = false
  }
}

const saveSort = async (product: ProductItem) => {
  await updateProductSort(product.item_id, product.sort_order)
  ElMessage.success('排序已保存')
  if (sortBy.value === 'manual') await loadProducts()
}

const saveNote = async (product: ProductItem) => {
  await updateProductNote(product.item_id, product.internal_note || '')
  ElMessage.success('备注已保存')
}

const money = (cent?: number | null) => cent == null ? '-' : `¥${(cent / 100).toFixed(2)}`
const formatTime = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
const statusText = (value: ProductItem['status']) => ({ on_sale: '出售中', off_shelf: '已下架', sold_out: '已售罄', missing: '已不存在' }[value])
const statusType = (value: ProductItem['status']) => ({ on_sale: 'success', off_shelf: 'info', sold_out: 'warning', missing: 'danger' }[value] as 'success' | 'info' | 'warning' | 'danger')

onMounted(loadProducts)
</script>

<style scoped>
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 18px; }
.page-head h1 { margin: 0; color: #163d32; font-size: 25px; }
.page-head p { margin: 7px 0 0; color: #71827c; }
.sync-state { display: flex; align-items: center; gap: 20px; padding: 14px 18px; margin-bottom: 16px; background: #f4f8f6; border: 1px solid #dfe9e5; border-radius: 10px; }
.sync-state span { color: #64756f; }
.sync-state strong { margin-left: 5px; color: #173d32; }
.sync-state .warning { color: #b26a00; }
.toolbar { display: grid; grid-template-columns: minmax(240px, 1fr) 150px 150px 110px auto; gap: 10px; margin-bottom: 16px; }
.product-table { width: 100%; border-radius: 10px; }
.product-cell { display: flex; align-items: center; gap: 12px; }
.product-cell .el-image { flex: 0 0 auto; width: 58px; height: 58px; background: #eef3f1; border-radius: 8px; }
.product-cell strong, .product-cell small { display: block; }
.product-cell small { margin-top: 6px; color: #8b9994; }
.image-fallback { display: grid; width: 100%; height: 100%; place-items: center; color: #9aaba5; font-size: 12px; }
.sku-panel { padding: 12px 24px 20px 70px; }
.sku-panel > strong { display: block; margin-bottom: 10px; color: #31584c; }
.pagination { display: flex; justify-content: flex-end; padding-top: 18px; }
:deep(.el-input-number) { width: 120px; }
@media (max-width: 980px) { .toolbar { grid-template-columns: 1fr 1fr; } .sync-state { align-items: flex-start; flex-direction: column; gap: 8px; } }
</style>
