<template>
  <section class="product-panel">
    <div class="product-header">
      <div><h3>商品</h3><p>搜索在售商品，发送商品卡片</p></div>
      <ElButton :icon="Refresh" circle :loading="loading" aria-label="刷新商品" @click="loadProducts" />
    </div>
    <ElInput v-model="keyword" clearable placeholder="搜索商品名称或编号" :prefix-icon="Search" />
    <ElAlert v-if="!canSend" :title="sendHint" type="info" :closable="false" />
    <ElAlert v-if="loadError" :title="loadError" type="error" :closable="false" />
    <div v-loading="loading" class="product-list">
      <article v-for="product in products" :key="product.item_id" class="product-card">
        <ElImage :src="product.image_url || ''" fit="cover" lazy>
          <template #error><span class="cover-fallback">暂无图片</span></template>
        </ElImage>
        <div class="product-info">
          <strong>{{ product.title }}</strong>
          <span class="price">{{ product.price_cent == null ? '价格以商品页为准' : `¥${(product.price_cent / 100).toFixed(2)}` }}</span>
          <small v-if="product.stock != null">库存 {{ product.stock }}</small>
          <ElButton type="primary" size="small" :loading="sendingId === product.item_id"
            :disabled="!canSend || !hasLink(product) || !!sendingId" @click="sendProduct(product)">
            {{ hasLink(product) ? '发送商品卡片' : '暂无购买链接' }}
          </ElButton>
        </div>
      </article>
      <ElEmpty v-if="!loading && !loadError && !products.length" :description="keyword ? '没有匹配的商品' : '暂无在售商品'" :image-size="72" />
    </div>
    <ElPagination v-if="total > pageSize" v-model:current-page="page" :page-size="pageSize" :total="total"
      small layout="prev, pager, next" :pager-count="5" @current-change="loadProducts" />
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Refresh, Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getReplyProducts, replyConversationProduct, updateReplyDraft, type ConversationItem, type ReplyProduct } from '@/api/admin/conversations'
import { useUserStore } from '@/store/modules/user'
import { isTestGate } from '@/utils/gate'

const props = defineProps<{ conversationId: string; conversation?: ConversationItem }>()
const emit = defineEmits<{ changed: [] }>()
const userStore = useUserStore()
const products = ref<ReplyProduct[]>([])
const keyword = ref('')
const page = ref(1)
const pageSize = 20
const total = ref(0)
const loading = ref(false)
const loadError = ref('')
const sendingId = ref('')
let requestVersion = 0
let searchTimer: ReturnType<typeof setTimeout> | undefined
const canSend = computed(() => !isTestGate() && !!props.conversationId && !!props.conversation && props.conversation.status !== 'resolved')
const sendHint = computed(() => isTestGate() ? '测试身份暂不支持商品面板' : !props.conversationId ? '请先选择一个会话' : '会话已结束，不能发送卡片')
const hasLink = (product: ReplyProduct) => /^https?:\/\//i.test(product.h5_url || product.page_url || '')

const loadProducts = async () => {
  if (isTestGate()) return
  const version = ++requestVersion
  loading.value = true
  loadError.value = ''
  try {
    const result = await getReplyProducts({ page: page.value, page_size: pageSize, keyword: keyword.value.trim() || undefined })
    if (version !== requestVersion) return
    products.value = result.items
    total.value = result.total
  } catch {
    if (version === requestVersion) loadError.value = '商品加载失败，请点击刷新重试'
  } finally {
    if (version === requestVersion) loading.value = false
  }
}
watch(keyword, () => {
  ++requestVersion
  clearTimeout(searchTimer)
  page.value = 1
  searchTimer = setTimeout(loadProducts, 300)
})

const sendProduct = async (product: ReplyProduct) => {
  if (!canSend.value || sendingId.value) return
  const conversationId = props.conversationId
  const operatorId = userStore.user.nickname || 'admin'
  const editorId = crypto.randomUUID()
  sendingId.value = product.item_id
  let acquired = false
  try {
    if (props.conversation?.status !== 'human_active') {
      await updateReplyDraft(conversationId, { operator_id: operatorId, editor_id: editorId, active: true })
      acquired = true
    }
    await replyConversationProduct(conversationId, operatorId, product.item_id)
    ElMessage.success('商品卡片已进入发送队列')
  } catch {
    // The shared HTTP client displays provider and ownership errors.
  } finally {
    if (acquired) await updateReplyDraft(conversationId, { operator_id: operatorId, editor_id: editorId, active: false }).catch(() => undefined)
    sendingId.value = ''
    emit('changed')
  }
}
onMounted(loadProducts)
onBeforeUnmount(() => { ++requestVersion; clearTimeout(searchTimer) })
</script>

<style scoped>
.product-panel { box-sizing: border-box; display: flex; flex: 1; flex-direction: column; gap: 12px; min-height: 0; padding: 16px; overflow: hidden; }
.product-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.product-header h3, .product-header p { margin: 0; }
.product-header h3 { font-size: 16px; }
.product-header p { margin-top: 5px; color: var(--app-text-secondary); font-size: 13px; }
.product-list { flex: 1; min-height: 120px; overflow-y: auto; }
.product-card { display: grid; grid-template-columns: 82px minmax(0, 1fr); gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--app-border); }
.product-card :deep(.el-image) { width: 82px; height: 82px; border-radius: 8px; }
.cover-fallback { display: grid; height: 100%; place-items: center; background: #f4f6f5; font-size: 12px; color: var(--app-text-secondary); }
.product-info { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; min-width: 0; }
.product-info strong { font-size: 14px; overflow-wrap: anywhere; }
.product-info small { color: var(--app-text-secondary); }
.price { color: #ad603a; font-size: 14px; }
</style>
