<template>
  <section class="opportunity-panel">
    <div class="title"><span>首单销售机会</span><ElTag v-if="opportunity?.status" :type="opportunity.status === 'active' ? 'success' : 'info'">{{ opportunity.status }}</ElTag></div>
    <ElSkeleton v-if="loading" :rows="4" animated />
    <ElEmpty v-else-if="!opportunity?.status" description="暂无进行中的机会" :image-size="54" />
    <template v-else>
      <ElAlert v-if="opportunity.interruption" :title="`流程已中断：${interruptionText}`" type="warning" :closable="false" show-icon />
      <dl>
        <dt>当前阶段</dt><dd>{{ opportunity.stage_display_name }}</dd>
        <dt>上一步</dt><dd>{{ stageName(opportunity.previous_stage) }}</dd>
        <dt>阶段目标</dt><dd>{{ opportunity.stage_objective || '-' }}</dd>
        <dt>推进依据</dt><dd>{{ evidenceText }}</dd>
        <dt>已知信息</dt><dd>{{ recordText(opportunity.known_slots) }}</dd>
        <dt>缺失信息</dt><dd>{{ opportunity.missing_slots.join('、') || '-' }}</dd>
        <dt>成交阻碍</dt><dd>{{ recordText(opportunity.decision_blocker) }}</dd>
        <dt>推荐商品</dt><dd>{{ opportunity.recommended_product_ids.join('、') || '-' }}</dd>
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
const recordText = (value?: Record<string, unknown> | string | null) => {
  if (!value) return '-'
  if (typeof value === 'string') return value
  return Object.entries(value).map(([key, item]) => `${key}: ${typeof item === 'object' ? JSON.stringify(item) : String(item)}`).join('；') || '-'
}
const evidenceText = computed(() => opportunity.value?.stage_evidence.map((item) => typeof item === 'string' ? item : String(item.code || item.value || '')).filter(Boolean).join('、') || '-')
const interruptionText = computed(() => {
  const item = opportunity.value?.interruption
  if (!item) return ''
  return `${item.type}${item.reason ? `（${item.reason}）` : ''}`
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
.actions { display: flex; gap: 8px; } .actions .el-select { flex: 1; }
</style>
