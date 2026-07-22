<template>
  <ContentWrap>
    <div class="page-head">
      <div>
        <h1>意图识别日志</h1>
        <p>默认接受高置信度预测，仅需处理低置信度记录；可定位到销售工作台核对上下文。</p>
      </div>
      <div class="head-actions">
        <ElButton :loading="exporting" @click="exportDataset">导出可训练 JSONL</ElButton>
        <ElButton type="primary" @click="load">刷新</ElButton>
      </div>
    </div>

    <div class="metrics">
      <div><span>待修正（低置信度）</span><strong>{{ summary.pending }}</strong></div>
      <div><span>预测正确</span><strong>{{ summary.accepted }}</strong></div>
      <div><span>人工已修正</span><strong>{{ summary.corrected }}</strong></div>
      <div><span>当前筛选</span><strong>{{ total }}</strong></div>
    </div>

    <div class="filters">
      <ElInput
        v-model="filters.keyword"
        clearable
        placeholder="搜索客户消息或用户 ID"
        @clear="search"
        @keyup.enter="search"
      />
      <ElSelect v-model="filters.annotation_status" clearable placeholder="审核状态" @change="search">
        <ElOption label="待修正" value="pending" />
        <ElOption label="预测正确" value="confirmed" />
        <ElOption label="已修正" value="corrected" />
        <ElOption label="不确定" value="uncertain" />
        <ElOption label="排除训练" value="excluded" />
      </ElSelect>
      <ElSelect v-model="filters.primary_domain" clearable filterable placeholder="Domain" @change="search">
        <ElOption v-for="item in domainCards" :key="item.id" :label="`${item.name} · ${item.id}`" :value="item.id" />
      </ElSelect>
      <ElSelect v-model="filters.primary_goal" clearable filterable placeholder="Goal" @change="search">
        <ElOption v-for="item in goalCards" :key="item.id" :label="`${item.name} · ${item.id}`" :value="item.id" />
      </ElSelect>
      <ElSelect v-model="filters.classifier_source" clearable placeholder="识别来源" @change="search">
        <ElOption label="大模型" value="llm" />
        <ElOption label="安全规则" value="rule_guard" />
        <ElOption label="高精度规则" value="hard_rule" />
        <ElOption label="上下文规则" value="context_rule" />
        <ElOption label="兜底规则" value="fallback_rule" />
        <ElOption label="大模型低置信度后规则兜底" value="llm_fallback_rule" />
        <ElOption label="历史漏采待补标" value="capture_gap" />
      </ElSelect>
      <ElSelect v-model="filters.max_confidence" clearable placeholder="低置信度" @change="search">
        <ElOption label="低于 0.60" :value="0.6" />
        <ElOption label="低于 0.75" :value="0.75" />
        <ElOption label="低于 0.90" :value="0.9" />
      </ElSelect>
    </div>

    <ElTable v-loading="loading" :data="items" row-key="trace_id" @row-dblclick="openDetail">
      <ElTableColumn label="时间" width="150">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </ElTableColumn>
      <ElTableColumn label="客户消息" min-width="280">
        <template #default="{ row }">
          <div class="message-cell">{{ row.user_message }}</div>
          <small>{{ row.user_id }} · {{ row.channel }}</small>
          <small v-if="row.conversation_message_ids.length > 1"> · 本轮合并 {{ row.conversation_message_ids.length }} 条消息</small>
        </template>
      </ElTableColumn>
      <ElTableColumn label="Domain / Goal" min-width="260">
        <template #default="{ row }">
          <div class="label-line"><b>D</b>{{ cardText(row.primary_domain) }}</div>
          <div class="label-line"><b>G</b>{{ cardText(row.primary_goal) }}</div>
          <div v-if="row.issues.length" class="issue-list">
            <ElTag v-for="issue in row.issues" :key="issue" size="small" effect="plain">{{ cardText(issue) }}</ElTag>
          </div>
        </template>
      </ElTableColumn>
      <ElTableColumn label="识别" width="130">
        <template #default="{ row }">
          <div>{{ sourceText(row.classifier_source) }}</div>
          <small>{{ confidenceText(row.confidence) }}</small>
        </template>
      </ElTableColumn>
      <ElTableColumn label="审核" width="110">
        <template #default="{ row }">
          <ElTag :type="annotationType(row.annotation_status)">{{ annotationText(row.annotation_status) }}</ElTag>
          <small v-if="row.annotation_origin === 'automatic' && !row.needs_review" class="status-note">系统默认</small>
        </template>
      </ElTableColumn>
      <ElTableColumn label="定位" width="80">
        <template #default="{ row }"><ElButton link type="primary" @click.stop="locateConversation(row)">定位</ElButton></template>
      </ElTableColumn>
      <ElTableColumn label="操作" width="100" fixed="right">
        <template #default="{ row }"><ElButton link type="primary" @click="openDetail(row)">{{ row.needs_review ? '修正' : '查看/修改' }}</ElButton></template>
      </ElTableColumn>
    </ElTable>

    <div class="pagination">
      <ElPagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        @current-change="load"
        @size-change="search"
      />
    </div>

    <ElDrawer v-model="drawerVisible" title="意图审核" size="760px">
      <template v-if="detail">
        <section class="detail-card">
          <div class="detail-title"><h3>本轮用户消息</h3><ElButton link type="primary" @click="locateConversation(detail)">定位到会话</ElButton></div>
          <p class="customer-message">{{ detail.user_message }}</p>
          <div v-if="detail.context.length" class="context-list">
            <h4>分类时使用的最近上下文</h4>
            <p v-for="(turn, index) in detail.context" :key="index">
              <b>{{ turn.role === 'user' ? '客户' : '客服' }}</b>{{ turn.content }}
            </p>
          </div>
        </section>

        <section class="detail-card predicted">
          <h3>系统预测</h3>
          <dl>
            <dt>Domain</dt><dd>{{ cardText(detail.primary_domain) }}</dd>
            <dt>Goal</dt><dd>{{ cardText(detail.primary_goal) }}</dd>
            <dt>Issue</dt><dd>{{ detail.issues.map(cardText).join('、') || '无' }}</dd>
            <dt>范围</dt><dd>{{ detail.scope }}</dd>
            <dt>置信度</dt><dd>{{ confidenceText(detail.confidence) }}</dd>
            <dt>来源</dt><dd>{{ sourceText(detail.classifier_source) }} · {{ detail.classifier_model || '无模型' }}</dd>
            <dt>原因</dt><dd>{{ detail.intent_reason || '—' }}</dd>
          </dl>
        </section>

        <section class="detail-card">
          <h3>人工结论</h3>
          <ElAlert v-if="readOnly" title="测试身份为只读模式，不能提交标注" type="info" :closable="false" />
          <ElForm label-position="top">
            <ElFormItem label="审核结果">
              <ElRadioGroup v-model="form.status">
                <ElRadioButton value="confirmed">预测正确</ElRadioButton>
                <ElRadioButton value="corrected">需要修正</ElRadioButton>
                <ElRadioButton value="uncertain">暂不确定</ElRadioButton>
                <ElRadioButton value="excluded">排除训练</ElRadioButton>
              </ElRadioGroup>
            </ElFormItem>
            <template v-if="form.status === 'corrected'">
              <div class="form-grid">
                <ElFormItem label="正确 Domain">
                  <ElSelect v-model="form.primary_domain" filterable>
                    <ElOption v-for="item in domainCards" :key="item.id" :label="`${item.name} · ${item.id}`" :value="item.id" />
                  </ElSelect>
                </ElFormItem>
                <ElFormItem label="正确 Goal">
                  <ElSelect v-model="form.primary_goal" filterable>
                    <ElOption v-for="item in goalCards" :key="item.id" :label="`${item.name} · ${item.id}`" :value="item.id" />
                  </ElSelect>
                </ElFormItem>
              </div>
              <div class="form-grid">
                <ElFormItem label="次要 Domain（可选）">
                  <ElSelect v-model="form.secondary_domains" multiple filterable collapse-tags>
                    <ElOption v-for="item in domainCards" :key="item.id" :label="`${item.name} · ${item.id}`" :value="item.id" />
                  </ElSelect>
                </ElFormItem>
                <ElFormItem label="次要 Goal（可选）">
                  <ElSelect v-model="form.secondary_goals" multiple filterable collapse-tags>
                    <ElOption v-for="item in goalCards" :key="item.id" :label="`${item.name} · ${item.id}`" :value="item.id" />
                  </ElSelect>
                </ElFormItem>
              </div>
              <ElFormItem label="正确 Issue（可多选）">
                <ElSelect v-model="form.issues" multiple filterable collapse-tags>
                  <ElOption v-for="item in issueCards" :key="item.id" :label="`${item.name} · ${item.id}`" :value="item.id" />
                </ElSelect>
              </ElFormItem>
              <ElFormItem label="范围">
                <ElSelect v-model="form.scope">
                  <ElOption label="业务范围内" value="in_scope" />
                  <ElOption label="信息不明确" value="ambiguous" />
                  <ElOption label="业务范围外" value="out_of_scope" />
                </ElSelect>
              </ElFormItem>
            </template>
            <div class="form-grid">
              <ElFormItem label="标注人"><ElInput v-model="form.annotator_id" /></ElFormItem>
              <ElFormItem label="备注"><ElInput v-model="form.note" placeholder="可选：说明判断依据" /></ElFormItem>
            </div>
            <ElButton type="primary" :disabled="readOnly" :loading="saving" @click="saveAnnotation">保存审核</ElButton>
          </ElForm>
        </section>

        <section v-if="detail.annotation_history.length" class="detail-card">
          <h3>审核历史</h3>
          <ElTimeline>
            <ElTimelineItem v-for="item in detail.annotation_history" :key="item.id" :timestamp="formatTime(item.created_at)">
              {{ item.annotator_id }} · {{ annotationText(item.status) }}
              <span v-if="item.note"> · {{ item.note }}</span>
            </ElTimelineItem>
          </ElTimeline>
        </section>

        <ElCollapse class="technical-detail">
          <ElCollapseItem title="技术详情：候选、证据与模型原始输出" name="technical">
            <pre>{{ JSON.stringify({ evidence: detail.evidence, candidates: detail.candidate_labels, raw: detail.raw_prediction }, null, 2) }}</pre>
          </ElCollapseItem>
        </ElCollapse>
      </template>
    </ElDrawer>
  </ContentWrap>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import {
  annotateIntentObservation,
  downloadIntentTrainingData,
  getIntentObservation,
  getIntentObservations,
  getIntentTaxonomy,
  type AnnotationStatus,
  type IntentObservation,
  type IntentObservationDetail,
  type TaxonomyCard
} from '@/api/admin/intentObservations'
import { isTestGate } from '@/utils/gate'

const loading = ref(false)
const saving = ref(false)
const exporting = ref(false)
const drawerVisible = ref(false)
const items = ref<IntentObservation[]>([])
const detail = ref<IntentObservationDetail | null>(null)
const cards = ref<TaxonomyCard[]>([])
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const summary = reactive({ pending: 0, accepted: 0, corrected: 0 })
const router = useRouter()
const readOnly = isTestGate()
const filters = reactive({
  keyword: '', annotation_status: 'pending', primary_domain: '', primary_goal: '',
  classifier_source: '', max_confidence: undefined as number | undefined
})
const form = reactive({
  status: 'confirmed' as Exclude<AnnotationStatus, 'pending'>,
  primary_domain: '', secondary_domains: [] as string[],
  primary_goal: '', secondary_goals: [] as string[], issues: [] as string[], scope: 'in_scope',
  annotator_id: localStorage.getItem('intent-annotator-id') || 'admin', note: ''
})

const domainCards = computed(() => cards.value.filter((item) => item.kind === 'domain'))
const goalCards = computed(() => cards.value.filter((item) => item.kind === 'goal'))
const issueCards = computed(() => cards.value.filter((item) => item.kind === 'issue'))

const load = async () => {
  loading.value = true
  try {
    const result = await getIntentObservations({
      page: page.value, page_size: pageSize.value,
      annotation_status: filters.annotation_status || undefined,
      primary_domain: filters.primary_domain || undefined,
      primary_goal: filters.primary_goal || undefined,
      classifier_source: filters.classifier_source || undefined,
      max_confidence: filters.max_confidence,
      keyword: filters.keyword || undefined
    })
    items.value = result.items
    total.value = result.total
    summary.pending = result.pending_count
    summary.accepted = result.accepted_count
    summary.corrected = result.corrected_count
  } finally { loading.value = false }
}

const search = () => { page.value = 1; load() }

const openDetail = async (row: IntentObservation) => {
  detail.value = await getIntentObservation(row.trace_id)
  const latest = detail.value.latest_annotation
  form.status = latest?.status || (detail.value.needs_review ? 'corrected' : 'confirmed')
  const useCorrection = latest?.status === 'corrected'
  form.primary_domain = (useCorrection ? latest.primary_domain : detail.value.primary_domain) || ''
  form.secondary_domains = [...(useCorrection ? latest.secondary_domains : detail.value.secondary_domains)]
  form.primary_goal = (useCorrection ? latest.primary_goal : detail.value.primary_goal) || ''
  form.secondary_goals = [...(useCorrection ? latest.secondary_goals : detail.value.secondary_goals)]
  form.issues = [...(useCorrection ? latest.issues : detail.value.issues)]
  form.scope = (useCorrection ? latest.scope : detail.value.scope) || 'in_scope'
  form.note = ''
  drawerVisible.value = true
}

const saveAnnotation = async () => {
  if (!detail.value || !form.annotator_id.trim()) return ElMessage.warning('请填写标注人')
  if (form.status === 'corrected' && (!form.primary_domain || !form.primary_goal || !form.scope)) {
    return ElMessage.warning('修正时必须填写 Domain、Goal 和范围')
  }
  saving.value = true
  try {
    localStorage.setItem('intent-annotator-id', form.annotator_id.trim())
    await annotateIntentObservation(detail.value.trace_id, {
      status: form.status,
      primary_domain: form.status === 'corrected' ? form.primary_domain : undefined,
      secondary_domains: form.status === 'corrected' ? form.secondary_domains : undefined,
      primary_goal: form.status === 'corrected' ? form.primary_goal : undefined,
      secondary_goals: form.status === 'corrected' ? form.secondary_goals : undefined,
      issues: form.status === 'corrected' ? form.issues : undefined,
      scope: form.status === 'corrected' ? form.scope : undefined,
      annotator_id: form.annotator_id.trim(), note: form.note || undefined
    })
    ElMessage.success('审核已保存')
    detail.value = await getIntentObservation(detail.value.trace_id)
    await load()
  } finally { saving.value = false }
}

const exportDataset = async () => {
  exporting.value = true
  try {
    const blob = await downloadIntentTrainingData()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `intent-training-data-${new Date().toISOString().slice(0, 10)}.jsonl`
    link.click()
    URL.revokeObjectURL(url)
  } finally { exporting.value = false }
}

const locateConversation = (row: IntentObservation) => {
  if (!row.conversation_id) return ElMessage.warning('该记录暂未关联到销售工作台会话')
  const messageId = row.conversation_message_ids.at(-1)
  void router.push({
    path: '/workbench',
    query: {
      conversation_id: row.conversation_id,
      ...(messageId ? { message_id: String(messageId) } : {})
    }
  })
}

const cardText = (id?: string | null) => {
  if (!id) return '未识别'
  const card = cards.value.find((item) => item.id === id)
  return card ? `${card.name} · ${id}` : id
}
const confidenceText = (value?: number | null) => value == null ? '—' : `${(value * 100).toFixed(0)}%`
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false })
const sourceText = (value: string) => ({ llm: '大模型', rule_guard: '安全规则', hard_rule: '高精度规则', context_rule: '上下文规则', fallback_rule: '兜底规则', llm_fallback_rule: '大模型低置信度后规则兜底', state_guard: '会话状态', bypass_route: '固定旁路', pipeline_error: '管线异常', capture_gap: '历史漏采待补标' }[value] || value)
const annotationText = (value: AnnotationStatus | string) => ({ pending: '待修正', confirmed: '预测正确', corrected: '已修正', uncertain: '不确定', excluded: '排除训练' }[value] || value)
const annotationType = (value: AnnotationStatus) => ({ pending: 'warning', confirmed: 'success', corrected: 'primary', uncertain: 'info', excluded: 'danger' }[value] as any)

onMounted(async () => {
  const taxonomy = await getIntentTaxonomy()
  cards.value = taxonomy.labels
  await load()
})
</script>

<style scoped>
.page-head,.head-actions,.filters,.metrics,.form-grid{display:flex;align-items:center;gap:12px}.page-head{justify-content:space-between;margin-bottom:18px}.page-head h1{margin:0 0 6px;font-size:24px}.page-head p{margin:0;color:#64748b}.metrics{margin-bottom:16px}.metrics>div{min-width:150px;padding:14px 18px;border:1px solid #e5e7eb;border-radius:12px;background:#fff}.metrics span{display:block;color:#64748b;font-size:13px}.metrics strong{font-size:24px}.filters{flex-wrap:wrap;margin-bottom:16px}.filters .el-input{width:260px}.filters .el-select{width:180px}.message-cell{line-height:1.6;white-space:normal}.message-cell+small,.el-table small{color:#94a3b8}.label-line{margin:3px 0}.label-line b{display:inline-grid;place-items:center;width:20px;height:20px;margin-right:7px;border-radius:5px;background:#eff6ff;color:#2563eb}.issue-list{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}.pagination{display:flex;justify-content:flex-end;margin-top:18px}.detail-card{margin-bottom:16px;padding:16px;border:1px solid #e5e7eb;border-radius:12px}.detail-card h3{margin:0 0 12px}.detail-title{display:flex;align-items:flex-start;justify-content:space-between}.detail-card h4{margin:12px 0 8px}.customer-message{padding:14px;border-radius:8px;background:#f8fafc;line-height:1.7}.context-list p{display:flex;gap:10px;margin:7px 0;color:#475569}.context-list b{flex:0 0 36px}.predicted dl{display:grid;grid-template-columns:90px 1fr;gap:8px 12px;margin:0}.predicted dt{color:#64748b}.predicted dd{margin:0}.form-grid>*{flex:1}.technical-detail pre{overflow:auto;max-height:420px;padding:12px;background:#0f172a;color:#e2e8f0;border-radius:8px;font-size:12px}.status-note{display:block;margin-top:4px}.el-select{width:100%}
</style>
