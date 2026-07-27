<template>
  <ContentWrap>
    <div class="page-head">
      <div>
        <h1>影子决策评测</h1>
        <p>客户仍收到生产版回复；影子方案只用于盲评、积累评测集，不产生任何业务动作。</p>
      </div>
      <div class="head-actions">
        <ElButton :loading="exporting" @click="exportDataset">导出已确认 JSONL</ElButton>
        <ElButton type="primary" @click="load">刷新</ElButton>
      </div>
    </div>

    <div class="safety-note">
      <span class="pulse"></span>
      <strong>生产影子模式</strong>
      <span>普通流量按配置抽样，高风险案例优先审核；影子失败不会影响客户回复。</span>
    </div>

    <div class="metrics">
      <div><span>待审核</span><strong>{{ summary.pending }}</strong></div>
      <div><span>已审核</span><strong>{{ summary.reviewed }}</strong></div>
      <div><span>当前筛选</span><strong>{{ total }}</strong></div>
      <div><span>本页高优先级</span><strong>{{ highPriorityCount }}</strong></div>
    </div>

    <div class="filters">
      <ElInput
        v-model="filters.keyword"
        clearable
        placeholder="搜索客户消息"
        @clear="search"
        @keyup.enter="search"
      />
      <ElSelect v-model="filters.review_status" clearable placeholder="审核状态" @change="search">
        <ElOption label="待审核" value="pending" />
        <ElOption label="已审核" value="reviewed" />
        <ElOption label="生产版更好" value="primary_better" />
        <ElOption label="影子版更好" value="shadow_better" />
        <ElOption label="基本相同" value="tie" />
        <ElOption label="两个都不好" value="both_bad" />
        <ElOption label="暂不确定" value="uncertain" />
        <ElOption label="排除" value="excluded" />
      </ElSelect>
      <ElSelect v-model="filters.review_priority" clearable placeholder="审核优先级" @change="search">
        <ElOption label="高优先级" value="high" />
        <ElOption label="中优先级" value="medium" />
        <ElOption label="低优先级" value="low" />
      </ElSelect>
      <ElSelect v-model="filters.status" clearable placeholder="影子运行状态" @change="search">
        <ElOption label="运行成功" value="success" />
        <ElOption label="运行失败" value="failed" />
      </ElSelect>
    </div>

    <ElTable v-loading="loading" :data="items" row-key="id" @row-dblclick="openDetail">
      <ElTableColumn label="时间" width="160">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </ElTableColumn>
      <ElTableColumn label="客户消息" min-width="300">
        <template #default="{ row }">
          <div class="message-cell">{{ row.user_message }}</div>
          <small>{{ row.channel }} · {{ row.experiment_id }} · {{ row.candidate_version }}</small>
        </template>
      </ElTableColumn>
      <ElTableColumn label="决策差异" min-width="260">
        <template #default="{ row }">
          <div v-if="row.status === 'failed'" class="failure">
            影子运行失败 · {{ row.error_class || '未知异常' }}
          </div>
          <template v-else-if="row.auto_issues.length">
            <ElTag
              v-for="issue in row.auto_issues.slice(0, 3)"
              :key="issue"
              size="small"
              effect="plain"
              class="issue-tag"
            >
              {{ issueText(issue) }}
            </ElTag>
            <small v-if="row.auto_issues.length > 3">另有 {{ row.auto_issues.length - 3 }} 项</small>
          </template>
          <ElTag v-else type="success" effect="plain">关键决策一致</ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn label="优先级" width="110">
        <template #default="{ row }">
          <ElTag :type="priorityType(row.review_priority)">
            {{ priorityText(row.review_priority) }}
          </ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn label="审核" width="130">
        <template #default="{ row }">
          <ElTag v-if="row.latest_annotation" :type="verdictType(row.latest_annotation.verdict)">
            {{ verdictText(row.latest_annotation.verdict) }}
          </ElTag>
          <ElTag v-else type="warning">待审核</ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn label="耗时" width="90">
        <template #default="{ row }">{{ row.latency_ms }}ms</template>
      </ElTableColumn>
      <ElTableColumn label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <ElButton link type="primary" @click="openDetail(row)">
            {{ row.latest_annotation ? '查看/复审' : '盲评' }}
          </ElButton>
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
        @current-change="load"
        @size-change="search"
      />
    </div>

    <ElDrawer v-model="drawerVisible" title="影子决策盲评" size="min(1120px, 94vw)">
      <template v-if="detail">
        <section class="detail-card customer-card">
          <div class="detail-title">
            <div>
              <span class="eyebrow">客户原话</span>
              <p>{{ detail.user_message }}</p>
            </div>
            <div class="run-meta">
              <ElTag :type="priorityType(detail.review_priority)">
                {{ priorityText(detail.review_priority) }}
              </ElTag>
              <span>{{ detail.experiment_id }} / {{ detail.candidate_version }}</span>
            </div>
          </div>
          <div v-if="detail.auto_issues.length" class="issue-row">
            <ElTag v-for="issue in detail.auto_issues" :key="issue" size="small" effect="plain">
              {{ issueText(issue) }}
            </ElTag>
          </div>
        </section>

        <ElAlert
          v-if="detail.status === 'failed'"
          :title="`影子运行失败：${detail.error_class || '未知异常'}`"
          type="error"
          :closable="false"
          show-icon
        />

        <div v-else class="comparison-grid">
          <section
            v-for="candidate in blindCandidates"
            :key="candidate.key"
            class="candidate-card"
            :class="{ winner: candidateWinner(candidate.key) }"
          >
            <div class="candidate-head">
              <div>
                <span class="candidate-label">方案 {{ candidate.label }}</span>
                <small v-if="detail.latest_annotation">{{ candidateSourceText(candidate.key) }}</small>
              </div>
              <ElTag v-if="candidateWinner(candidate.key)" type="success">已选更优</ElTag>
            </div>

            <dl class="decision-grid">
              <dt>销售阶段</dt><dd>{{ salesStageText(candidate.data.sales_stage) }}</dd>
              <dt>回复路由</dt><dd>{{ routeText(candidate.data.route) }}</dd>
              <dt>销售动作</dt><dd>{{ salesActionText(candidate.data.sales_action) }}</dd>
              <dt>转人工</dt><dd>{{ candidate.data.need_human ? '是' : '否' }}</dd>
            </dl>

            <div class="reply-copy">
              <span>给客户的回复</span>
              <p>{{ candidate.data.reply || '（空回复）' }}</p>
            </div>

            <div v-if="candidate.data.follow_up?.needed" class="follow-up">
              <div class="follow-up-title">建议后续跟进</div>
              <p>{{ candidate.data.follow_up.action || '未说明具体动作' }}</p>
              <p>执行时间：{{ candidate.data.follow_up.due_in_hours }} 小时后</p>
              <div>
                <span>取消条件</span>
                <ul>
                  <li v-for="condition in candidate.data.follow_up.cancel_conditions" :key="condition">
                    {{ condition }}
                  </li>
                </ul>
              </div>
            </div>
            <div v-else class="no-follow-up">本轮不建议主动跟进</div>

            <div v-if="candidate.data.reason" class="reason">
              <span>判断理由</span>
              <p>{{ candidate.data.reason }}</p>
            </div>
          </section>
        </div>

        <section v-if="detail.status === 'success'" class="detail-card review-card">
          <h3>人工结论</h3>
          <ElAlert
            v-if="readOnly"
            title="测试身份为只读模式，不能提交审核"
            type="info"
            :closable="false"
          />
          <ElForm label-position="top">
            <ElFormItem label="哪个方案更好？">
              <ElRadioGroup v-model="form.choice">
                <ElRadioButton value="a_better">方案 A 更好</ElRadioButton>
                <ElRadioButton value="b_better">方案 B 更好</ElRadioButton>
                <ElRadioButton value="tie">基本相同</ElRadioButton>
                <ElRadioButton value="both_bad">两个都不好</ElRadioButton>
                <ElRadioButton value="uncertain">暂不确定</ElRadioButton>
                <ElRadioButton value="excluded">排除</ElRadioButton>
              </ElRadioGroup>
            </ElFormItem>
            <ElFormItem label="问题标签（可多选）">
              <ElSelect
                v-model="form.error_tags"
                multiple
                filterable
                allow-create
                default-first-option
                placeholder="选择或输入问题标签"
              >
                <ElOption v-for="tag in errorTagOptions" :key="tag" :label="tag" :value="tag" />
              </ElSelect>
            </ElFormItem>
            <div class="form-grid">
              <ElFormItem label="审核人">
                <ElInput v-model="form.annotator_id" />
              </ElFormItem>
              <ElFormItem label="判断依据">
                <ElInput v-model="form.note" placeholder="建议说明为什么更好或哪里有风险" />
              </ElFormItem>
            </div>
            <ElButton
              type="primary"
              :disabled="readOnly"
              :loading="saving"
              @click="saveAnnotation"
            >
              保存审核
            </ElButton>
          </ElForm>
        </section>

        <section v-if="detail.annotation_history.length" class="detail-card">
          <h3>审核历史</h3>
          <ElTimeline>
            <ElTimelineItem
              v-for="item in detail.annotation_history"
              :key="item.id"
              :timestamp="formatTime(item.created_at)"
            >
              {{ item.annotator_id }} · {{ verdictText(item.verdict) }}
              <span v-if="item.error_tags.length"> · {{ item.error_tags.join('、') }}</span>
              <span v-if="item.note"> · {{ item.note }}</span>
            </ElTimelineItem>
          </ElTimeline>
        </section>

        <ElCollapse class="technical-detail">
          <ElCollapseItem title="技术详情：输入快照与结构化输出" name="technical">
            <pre>{{ JSON.stringify({
              input: detail.input_snapshot,
              primary: detail.primary,
              shadow: detail.shadow
            }, null, 2) }}</pre>
          </ElCollapseItem>
        </ElCollapse>
      </template>
    </ElDrawer>
  </ContentWrap>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  annotateReplyShadow,
  downloadReplyShadowDataset,
  getReplyShadow,
  getReplyShadows,
  type ReplyShadowDetail,
  type ReplyShadowRun,
  type ReplyShadowVerdict,
  type ShadowDecision
} from '@/api/admin/replyShadows'
import { salesStageText } from '@/utils/tagDisplay'
import { isTestGate } from '@/utils/gate'

type CandidateKey = 'primary' | 'shadow'
type ReviewChoice = 'a_better' | 'b_better' | 'tie' | 'both_bad' | 'uncertain' | 'excluded'

const loading = ref(false)
const saving = ref(false)
const exporting = ref(false)
const drawerVisible = ref(false)
const items = ref<ReplyShadowRun[]>([])
const detail = ref<ReplyShadowDetail | null>(null)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const summary = reactive({ pending: 0, reviewed: 0 })
const readOnly = isTestGate()
const filters = reactive({
  keyword: '',
  review_status: 'pending',
  review_priority: '',
  status: ''
})
const form = reactive({
  choice: '' as ReviewChoice | '',
  error_tags: [] as string[],
  note: '',
  annotator_id: localStorage.getItem('reply-shadow-annotator-id') || 'admin'
})
const errorTagOptions = [
  '事实错误',
  '答非所问',
  '销售阶段错误',
  '销售动作错误',
  '错误转人工',
  '过度销售',
  '重复追问',
  '跟进不合理',
  '缺少取消条件',
  '表达问题',
  '内部字段泄漏'
]

const highPriorityCount = computed(
  () => items.value.filter((item) => item.review_priority === 'high').length
)

const isShadowFirst = computed(() => {
  const traceId = detail.value?.trace_id || ''
  return [...traceId].reduce((sum, char) => sum + char.charCodeAt(0), 0) % 2 === 0
})

const blindCandidates = computed(() => {
  if (!detail.value) return []
  const ordered: Array<{ key: CandidateKey; data: ShadowDecision }> = isShadowFirst.value
    ? [
        { key: 'shadow', data: detail.value.shadow },
        { key: 'primary', data: detail.value.primary }
      ]
    : [
        { key: 'primary', data: detail.value.primary },
        { key: 'shadow', data: detail.value.shadow }
      ]
  return ordered.map((candidate, index) => ({
    ...candidate,
    label: index === 0 ? 'A' : 'B'
  }))
})

const load = async () => {
  loading.value = true
  try {
    const result = await getReplyShadows({
      page: page.value,
      page_size: pageSize.value,
      review_status: filters.review_status || undefined,
      review_priority: filters.review_priority || undefined,
      status: filters.status || undefined,
      keyword: filters.keyword || undefined
    })
    items.value = result.items
    total.value = result.total
    summary.pending = result.pending_count
    summary.reviewed = result.reviewed_count
  } finally {
    loading.value = false
  }
}

const search = () => {
  page.value = 1
  void load()
}

const openDetail = async (row: ReplyShadowRun) => {
  detail.value = await getReplyShadow(row.trace_id)
  form.choice = verdictToChoice(detail.value.latest_annotation?.verdict)
  form.error_tags = [...(detail.value.latest_annotation?.error_tags || [])]
  form.note = ''
  drawerVisible.value = true
}

const saveAnnotation = async () => {
  if (!detail.value || !form.annotator_id.trim()) {
    return ElMessage.warning('请填写审核人')
  }
  if (!form.choice) {
    return ElMessage.warning('请选择审核结论')
  }
  saving.value = true
  try {
    localStorage.setItem('reply-shadow-annotator-id', form.annotator_id.trim())
    await annotateReplyShadow(detail.value.trace_id, {
      verdict: choiceToVerdict(form.choice),
      error_tags: form.error_tags,
      note: form.note || undefined,
      annotator_id: form.annotator_id.trim()
    })
    ElMessage.success('审核已保存')
    detail.value = await getReplyShadow(detail.value.trace_id)
    await load()
  } finally {
    saving.value = false
  }
}

const exportDataset = async () => {
  exporting.value = true
  try {
    const blob = await downloadReplyShadowDataset()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `reply-shadow-dataset-${new Date().toISOString().slice(0, 10)}.jsonl`
    link.click()
    URL.revokeObjectURL(url)
  } finally {
    exporting.value = false
  }
}

const choiceToVerdict = (choice: ReviewChoice): ReplyShadowVerdict => {
  if (choice === 'a_better') {
    return blindCandidates.value[0]?.key === 'shadow' ? 'shadow_better' : 'primary_better'
  }
  if (choice === 'b_better') {
    return blindCandidates.value[1]?.key === 'shadow' ? 'shadow_better' : 'primary_better'
  }
  return choice
}

const verdictToChoice = (verdict?: ReplyShadowVerdict): ReviewChoice | '' => {
  if (!verdict) return ''
  if (verdict === 'primary_better' || verdict === 'shadow_better') {
    const winnerIndex = blindCandidates.value.findIndex((item) => item.key === (
      verdict === 'shadow_better' ? 'shadow' : 'primary'
    ))
    return winnerIndex === 1 ? 'b_better' : 'a_better'
  }
  return verdict
}

const candidateWinner = (key: CandidateKey) => {
  const verdict = detail.value?.latest_annotation?.verdict
  return (verdict === 'primary_better' && key === 'primary')
    || (verdict === 'shadow_better' && key === 'shadow')
}

const candidateSourceText = (key: CandidateKey) => key === 'primary' ? '生产版' : '影子版'
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false })
const priorityText = (value: string) => ({ high: '高', medium: '中', low: '低' }[value] || value)
const priorityType = (value: string) => ({ high: 'danger', medium: 'warning', low: 'info' }[value] as any)
const verdictText = (value: string) => ({
  primary_better: '生产版更好',
  shadow_better: '影子版更好',
  tie: '基本相同',
  both_bad: '两个都不好',
  uncertain: '暂不确定',
  excluded: '已排除'
}[value] || value)
const verdictType = (value: string) => ({
  primary_better: 'warning',
  shadow_better: 'success',
  tie: 'info',
  both_bad: 'danger',
  uncertain: 'info',
  excluded: 'danger'
}[value] as any)
const routeText = (value?: string | null) => ({
  template_reply: '模板/业务回复',
  rag_answer: '知识检索回复',
  human: '转人工',
  chitchat: '闲聊',
  unsupported: '业务外',
  clarify: '澄清'
}[value || ''] || value || '—')
const salesActionText = (value?: string | null) => ({
  build_rapport: '建立关系',
  discover_need_track: '识别需求方向',
  discover_pain: '发现核心问题',
  recommend_solution: '推荐方案',
  build_value: '建立价值',
  trial_close: '试探成交',
  resolve_blocker: '解决成交阻碍',
  close_order: '推动下单',
  provide_service: '提供服务',
  handoff_to_human: '转人工'
}[value || ''] || value || '—')
const issueText = (value: string) => ({
  shadow_failed: '影子运行失败',
  route_disagreement: '回复路由不同',
  sales_action_disagreement: '销售动作不同',
  stage_disagreement: '销售阶段不同',
  handoff_disagreement: '转人工判断不同',
  follow_up_proposed: '存在跟进建议差异',
  follow_up_incomplete: '跟进信息不完整',
  missing_cancel_conditions: '缺少取消条件',
  internal_field_leak: '疑似内部字段泄漏'
}[value] || value)

onMounted(load)
</script>

<style scoped>
.page-head,.head-actions,.filters,.metrics,.detail-title,.candidate-head,.form-grid{display:flex;align-items:center;gap:12px}.page-head{justify-content:space-between;margin-bottom:14px}.page-head h1{margin:0 0 6px;font-size:24px}.page-head p{margin:0;color:#64748b}.safety-note{display:flex;align-items:center;gap:9px;padding:11px 14px;margin-bottom:16px;color:#285b49;background:#eefaf5;border:1px solid #ccecdf;border-radius:10px}.safety-note span:last-child{color:#557268}.pulse{width:8px;height:8px;background:#22a06b;border-radius:50%;box-shadow:0 0 0 4px #caeedf}.metrics{margin-bottom:16px}.metrics>div{min-width:145px;padding:14px 18px;border:1px solid #e5e7eb;border-radius:12px;background:#fff}.metrics span{display:block;color:#64748b;font-size:13px}.metrics strong{font-size:24px}.filters{flex-wrap:wrap;margin-bottom:16px}.filters .el-input{width:280px}.filters .el-select{width:180px}.message-cell{line-height:1.6;white-space:normal}.message-cell+small,.el-table small{color:#94a3b8}.issue-tag{margin:2px 4px 2px 0}.failure{color:#c2413b}.pagination{display:flex;justify-content:flex-end;margin-top:18px}.detail-card{margin-bottom:16px;padding:18px;border:1px solid #e5e7eb;border-radius:13px;background:#fff}.detail-card h3{margin:0 0 14px}.customer-card{background:#f8fbfa}.detail-title{align-items:flex-start;justify-content:space-between}.eyebrow,.reply-copy>span,.reason>span,.follow-up span{color:#64748b;font-size:12px}.customer-card p{margin:7px 0 0;font-size:17px;line-height:1.7}.run-meta{display:flex;align-items:flex-end;gap:8px;flex-direction:column;color:#64748b;font-size:12px}.issue-row{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px}.comparison-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin:16px 0}.candidate-card{padding:20px;border:1px solid #dfe7e3;border-radius:15px;background:#fff;box-shadow:0 3px 12px rgba(34,68,56,.04)}.candidate-card.winner{border-color:#59b28f;box-shadow:0 0 0 2px #dff4eb}.candidate-head{justify-content:space-between;padding-bottom:14px;border-bottom:1px solid #edf1ef}.candidate-label{display:block;color:#173f32;font-size:18px;font-weight:800}.candidate-head small{display:block;margin-top:4px;color:#73837d}.decision-grid{display:grid;grid-template-columns:78px 1fr;gap:8px 12px;margin:16px 0}.decision-grid dt{color:#718078}.decision-grid dd{margin:0;font-weight:600}.reply-copy p{min-height:84px;padding:13px;margin:7px 0 0;background:#f7faf9;border-radius:9px;line-height:1.75;white-space:pre-wrap}.follow-up{padding:13px;margin-top:12px;color:#644d1c;background:#fff9e8;border:1px solid #f3e2a8;border-radius:10px}.follow-up-title{font-weight:700}.follow-up p{margin:6px 0}.follow-up ul{padding-left:20px;margin:6px 0 0}.no-follow-up{padding:11px;margin-top:12px;color:#718078;background:#f8faf9;border-radius:9px}.reason{margin-top:14px}.reason p{margin:5px 0;color:#475569;line-height:1.6}.review-card{border-color:#d9e7e1}.form-grid>*{flex:1}.technical-detail pre{overflow:auto;max-height:460px;padding:14px;background:#0f172a;color:#e2e8f0;border-radius:9px;font-size:12px}.el-select{width:100%}@media(max-width:900px){.comparison-grid{grid-template-columns:1fr}.metrics{flex-wrap:wrap}.page-head{align-items:flex-start;flex-direction:column}}
</style>
