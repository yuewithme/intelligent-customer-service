<template>
  <section class="opportunity-panel">
    <div class="title"><span>首单销售机会</span><ElTag v-if="opportunity?.status" :type="opportunity.status === 'active' ? 'success' : 'info'">{{ statusText(opportunity.status) }}</ElTag></div>
    <ElSkeleton v-if="loading" :rows="4" animated />
    <ElEmpty v-else-if="!opportunity?.status" description="暂无进行中的机会" :image-size="54" />
    <template v-else>
      <ElAlert v-if="opportunity.interruption" :title="`流程已中断：${interruptionText}`" type="warning" :closable="false" show-icon />
      <dl>
        <dt>当前目标</dt><dd>{{ opportunity.stage_objective || '-' }}</dd>
        <dt>推进依据</dt>
        <dd><div v-if="evidenceItems.length" class="fact-list"><span v-for="item in evidenceItems" :key="item">{{ item }}</span></div><span v-else>-</span></dd>
        <dt>已知信息</dt>
        <dd><div v-if="knownSlotItems.length" class="fact-list"><span v-for="item in knownSlotItems" :key="item">{{ item }}</span></div><span v-else>-</span></dd>
        <dt>待补信息</dt><dd>{{ missingSlotText }}</dd>
        <dt>成交阻碍</dt><dd>{{ blockerText }}</dd>
        <dt>推荐商品 ID</dt><dd>{{ opportunity.recommended_product_ids.join('、') || '-' }}</dd>
        <dt>下一步</dt><dd>{{ opportunity.reply_goal || opportunity.next_action || '-' }}</dd>
      </dl>
      <div v-if="opportunity.status === 'active'" class="actions">
        <ElSelect v-model="targetStage" placeholder="调整阶段" :disabled="Boolean(opportunity.interruption)">
          <ElOption v-for="item in stages" :key="item.stage" :label="item.display_name" :value="item.stage" />
        </ElSelect>
        <ElButton type="primary" :disabled="!targetStage || Boolean(opportunity.interruption)" @click="adjust">确认调整</ElButton>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { adjustSalesStage, getSalesOpportunity, getSalesStages, type SalesOpportunity, type SalesStageDefinition } from '@/api/admin/sales-flow'

const props = defineProps<{ userId?: string; operatorId: string }>()
const emit = defineEmits<{ changed: [] }>()
const opportunity = ref<SalesOpportunity>()
const stages = ref<SalesStageDefinition[]>([])
const targetStage = ref('')
const loading = ref(false)
const stageName = (stage?: string | null) => stages.value.find((item) => item.stage === stage)?.display_name || stage || '-'

const SLOT_TEXT: Record<string, string> = {
  material_type: '咨询主题',
  resource_type: '资料类型',
  need_track: '需求类型',
  desired_outcome: '期望结果',
  pain_point: '核心痛点',
  failed_history: '过往经历',
  region: '所在地区',
  placement: '摆放环境',
  light: '光照条件',
  ventilation: '通风条件',
  budget: '预算范围',
  color_preference: '花色偏好',
  fragrance_preference: '香味偏好',
  difficulty_preference: '养护难度偏好',
  collection_preference: '收藏偏好',
  selected_product_id: '已选商品 ID',
  selected_product_name: '已选商品',
  selected_sku_id: '已选规格',
  quantity: '购买数量',
  decision_blocker: '成交阻碍'
}

const VALUE_TEXT: Record<string, string> = {
  service: '养护服务',
  product: '商品购买',
  combined: '养护与购买',
  orchid_care: '兰花养护',
  orchid_material: '兰花植料',
  price: '价格顾虑',
  trust: '信任顾虑',
  care_risk: '养护风险',
  product_fit: '商品适配',
  choice: '选择困难',
  timing: '购买时机',
  other: '其他原因',
  true: '是',
  false: '否'
}

const SIGNAL_TEXT: Record<string, string> = {
  responded: '客户已回复',
  service_need: '客户需要养护服务',
  product_need: '客户有商品需求',
  combined_need: '客户同时有养护和商品需求',
  pain_revealed: '客户已表达痛点',
  preference_revealed: '客户已表达偏好',
  recommendation_engaged: '客户正在了解推荐方案',
  value_acknowledged: '客户认可方案价值',
  price_interest: '客户关注价格',
  ready_to_buy: '客户准备购买',
  objection: '客户存在异议',
  purchase_rejected: '客户拒绝购买',
  payment_claimed: '客户表示已付款',
  purchased: '订单已确认成交'
}

const INTERNAL_SLOTS = new Set(['original_route', 'sales_stage_reason'])
const slotText = (key: string) => SLOT_TEXT[key] || key
const valueText = (value: unknown): string => {
  if (value === null || value === undefined || value === '') return '-'
  if (Array.isArray(value)) return value.map(valueText).join('、')
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${slotText(key)}：${valueText(item)}`)
      .join('；')
  }
  const raw = String(value)
  return VALUE_TEXT[raw] || raw
}

const evidenceItemText = (item: Record<string, unknown> | string) => {
  const code = typeof item === 'string' ? item : String(item.code || '')
  if (code.startsWith('slot:')) {
    const key = code.slice(5)
    if (INTERNAL_SLOTS.has(key)) return ''
    const value = typeof item === 'string'
      ? opportunity.value?.known_slots?.[key]
      : item.value
    return value === undefined ? `已确认${slotText(key)}` : `${slotText(key)}：${valueText(value)}`
  }
  if (code.startsWith('signal:')) {
    const signal = code.slice(7)
    return SIGNAL_TEXT[signal] || signal
  }
  return SIGNAL_TEXT[code] || code
}

const evidenceItems = computed(() => Array.from(new Set(
  (opportunity.value?.stage_evidence || []).map(evidenceItemText).filter(Boolean)
)))
const knownSlotItems = computed(() => Object.entries(opportunity.value?.known_slots || {})
  .filter(([key]) => !INTERNAL_SLOTS.has(key))
  .map(([key, value]) => `${slotText(key)}：${valueText(value)}`))
const missingSlotText = computed(() => opportunity.value?.missing_slots
  .filter((key) => !INTERNAL_SLOTS.has(key))
  .map(slotText)
  .join('、') || '-')
const blockerText = computed(() => valueText(opportunity.value?.decision_blocker))
const statusText = (status: string) => ({ active: '进行中', won: '已成交', lost: '已流失', paused: '已暂停', expired: '已过期' })[status] || status
const interruptionText = computed(() => {
  const item = opportunity.value?.interruption
  if (!item) return ''
  const type = ({ after_sale: '转入售后', human_pending: '等待人工处理' })[item.type] || item.type
  return `${type}${item.reason ? `（${item.reason}）` : ''}`
})
const load = async () => {
  if (!props.userId) { opportunity.value = undefined; return }
  loading.value = true
  try {
    if (!stages.value.length) stages.value = (await getSalesStages()).items
    opportunity.value = await getSalesOpportunity(props.userId)
    targetStage.value = opportunity.value.current_stage || ''
  } finally { loading.value = false }
}
const adjust = async () => {
  if (!props.userId || !targetStage.value) return
  const { value: reason } = await ElMessageBox.prompt('请输入调整原因，操作将写入审计记录。', '人工调整阶段', { inputValidator: (value) => Boolean(value.trim()) || '原因不能为空' })
  await ElMessageBox.confirm(`确认将阶段调整为“${stageName(targetStage.value)}”？`, '二次确认', { type: 'warning' })
  opportunity.value = await adjustSalesStage(props.userId, { stage: targetStage.value, reason, operator_id: props.operatorId })
  ElMessage.success('销售阶段已调整')
  emit('changed')
}
watch(() => props.userId, () => { void load() }, { immediate: true })
</script>

<style scoped>
.opportunity-panel { padding-top: 4px; border-top: 1px solid #e5e7eb; } .title { display: flex; justify-content: space-between; margin-bottom: 12px; font-weight: 600; }
dl { display: grid; grid-template-columns: 76px 1fr; gap: 8px; margin: 12px 0; font-size: 13px; } dt { color: #6b7280; } dd { margin: 0; overflow-wrap: anywhere; }
.fact-list { display: flex; flex-direction: column; gap: 4px; }
.actions { display: flex; gap: 8px; } .actions .el-select { flex: 1; }
</style>
