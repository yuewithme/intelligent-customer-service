<template>
  <section class="order-panel">
    <div class="title">
      <span>有赞订单</span>
      <ElButton link type="primary" :loading="loading" @click="load">刷新</ElButton>
    </div>
    <ElSkeleton v-if="loading && !result" :rows="3" animated />
    <ElAlert
      v-else-if="result?.status === 'unbound'"
      title="暂未识别客户订单身份"
      description="客户提供下单手机号后，系统会自动关联已同步订单。"
      type="info"
      :closable="false"
      show-icon
    />
    <ElEmpty
      v-else-if="!result?.items.length"
      description="暂未查到有赞订单"
      :image-size="58"
    />
    <div v-else class="orders">
      <article v-for="order in result.items" :key="order.order_no" class="order-card">
        <div class="order-head">
          <ElTag size="small" :type="statusType(order.status)">
            {{ order.status_text }}
          </ElTag>
          <time>{{ formatTime(order.created_at) }}</time>
        </div>
        <div v-for="(item, index) in order.items.slice(0, 3)" :key="index" class="order-item">
          <ElImage
            v-if="item.image_url"
            :src="item.image_url"
            fit="cover"
            class="item-image"
          />
          <div>
            <strong>{{ item.title }}</strong>
            <span>× {{ item.quantity }}</span>
          </div>
        </div>
        <p v-if="!order.items.length">{{ order.item_summary || '商品信息待同步' }}</p>
        <dl>
          <dt>订单号</dt><dd>{{ order.order_no }}</dd>
          <template v-if="order.payment_amount">
            <dt>实付</dt><dd>¥{{ order.payment_amount }}</dd>
          </template>
          <template v-if="order.express_company || order.tracking_no_masked">
            <dt>物流</dt>
            <dd>{{ [order.express_company, order.tracking_no_masked].filter(Boolean).join(' · ') }}</dd>
          </template>
        </dl>
      </article>
    </div>
    <p v-if="result?.last_synced_at" class="sync-time">
      最近同步：{{ formatTime(result.last_synced_at) }}
      <span v-if="result.mobile_masked"> · {{ result.mobile_masked }}</span>
    </p>
  </section>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  getConversationOrders,
  type ConversationOrders,
  type YouzanOrder
} from '@/api/admin/conversations'
import { formatChinaTime } from '../time'

const props = defineProps<{ conversationId: string }>()
const loading = ref(false)
const result = ref<ConversationOrders>()
let requestKey = 0

const load = async () => {
  const key = ++requestKey
  if (!props.conversationId) {
    result.value = undefined
    return
  }
  loading.value = true
  try {
    const data = await getConversationOrders(props.conversationId)
    if (key === requestKey) result.value = data
  } finally {
    if (key === requestKey) loading.value = false
  }
}

const statusType = (status: YouzanOrder['status']) => {
  if (status === 'TRADE_SUCCESS' || status === 'TRADE_BUYER_SIGNED') return 'success'
  if (status === 'WAIT_SELLER_SEND_GOODS') return 'warning'
  if (status === 'TRADE_CLOSED') return 'info'
  return 'primary'
}

const formatTime = (value?: string | null) => value ? formatChinaTime(value) : '-'

watch(() => props.conversationId, () => void load(), { immediate: true })
</script>

<style scoped>
.order-panel { padding-top: 4px; border-top: 1px solid #e5e7eb; }
.title { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; font-weight: 600; }
.orders { display: grid; gap: 9px; }
.order-card { padding: 10px; border: 1px solid #e5e7eb; border-radius: 8px; background: #fafafa; }
.order-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.order-head time,.sync-time { color: #9ca3af; font-size: 12px; }
.order-item { display: flex; align-items: center; gap: 8px; margin: 6px 0; }
.order-item div { display: flex; min-width: 0; flex: 1; align-items: center; justify-content: space-between; gap: 8px; }
.order-item strong { overflow: hidden; font-size: 13px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.order-item span { flex: 0 0 auto; color: #6b7280; font-size: 12px; }
.item-image { width: 34px; height: 34px; border-radius: 6px; background: #eef2f1; }
.order-card p { margin: 4px 0 8px; font-size: 13px; }
.order-card dl { grid-template-columns: 48px 1fr; gap: 4px 8px; font-size: 12px; }
.sync-time { margin: 9px 0 0; }
</style>
