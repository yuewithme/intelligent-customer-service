<template>
  <ContentWrap>
    <div class="page-head">
      <div>
        <div class="title-row">
          <h1>能力工作台</h1>
          <ElTag type="success" effect="plain">SOP 节点可配置</ElTag>
        </div>
        <p>查看首单与服务 SOP，并统一设置每个业务节点由 AI 回复或转人工。</p>
      </div>
      <ElButton :loading="loading" @click="load">刷新数据</ElButton>
    </div>

    <ElAlert
      title="SOP 节点开关会直接影响线上 Agent 接管方式；其他流程和能力信息仍为查看模式。"
      type="info"
      :closable="false"
      show-icon
      class="notice"
    />

    <div v-loading="loading" class="workbench-body">
      <section class="metrics">
        <article>
          <span>能力模块</span>
          <strong>{{ data?.stats.capabilities_total || 0 }}</strong>
          <small>已纳入统一能力协议</small>
        </article>
        <article>
          <span>经验包</span>
          <strong>{{ data?.stats.experience_packages_total || 0 }}</strong>
          <small>{{ data?.stats.draft_packages_total || 0 }} 个草稿版本</small>
        </article>
        <article>
          <span>可拖拽动作</span>
          <strong>{{ data?.stats.draggable_total || 0 }}</strong>
          <small>发送、通知和人工协作</small>
        </article>
        <article>
          <span>系统内部能力</span>
          <strong>{{ data?.stats.hidden_total || 0 }}</strong>
          <small>默认不暴露给业务画布</small>
        </article>
      </section>

      <ElTabs v-model="activeTab" class="workspace-tabs">
        <ElTabPane label="经验包流程" name="flow">
          <div v-if="activePackage" class="flow-workspace">
            <aside class="package-panel">
              <div class="panel-title">
                <span>经验包</span>
                <small>{{ packages.length }}</small>
              </div>
              <button
                v-for="item in packages"
                :key="item.package_id"
                type="button"
                class="package-card"
                :class="{ active: item.package_id === activePackage.package_id }"
                @click="selectPackage(item.package_id)"
              >
                <span class="package-card-head">
                  <strong>{{ item.name }}</strong>
                  <em>{{ statusText(item.status) }}</em>
                </span>
                <small>{{ item.steps.length }} 个步骤 · {{ item.transitions.length }} 条连线</small>
                <p>{{ item.description }}</p>
              </button>
              <div class="package-meta">
                <span>入口事件</span>
                <ElTag v-for="event in activePackage.entry.events" :key="event" size="small" effect="plain">
                  {{ event }}
                </ElTag>
                <span>目标结果</span>
                <ElTag v-for="outcome in activePackage.outcomes" :key="outcome.outcome_id" size="small" type="success" effect="plain">
                  {{ outcome.name }}
                </ElTag>
              </div>
            </aside>

            <section class="graph-panel">
              <div class="graph-head">
                <div>
                  <strong>{{ activePackage.name }}</strong>
                  <span>v{{ activePackage.version }} · {{ statusText(activePackage.status) }}</span>
                </div>
                <div class="graph-legend">
                  <span><i class="agent" />对话阶段</span>
                  <span><i class="decision" />条件判断</span>
                  <span><i class="action" />执行动作</span>
                  <span><i class="wait" />等待</span>
                  <span><i class="outcome" />结果</span>
                </div>
              </div>
              <div class="graph-scroll">
                <div class="graph-canvas" :style="canvasStyle">
                  <svg class="graph-lines" :width="canvasSize.width" :height="canvasSize.height">
                    <defs>
                      <marker id="capability-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                        <path d="M 0 0 L 10 5 L 0 10 z" />
                      </marker>
                    </defs>
                    <g
                      v-for="edge in graphEdges"
                      :key="edge.transition.transition_id"
                      class="edge-group"
                      :class="{ active: selectedTransitionId === edge.transition.transition_id }"
                    >
                      <path class="edge-hit" :d="edge.path" @click.stop="selectTransition(edge.transition.transition_id)" />
                      <path class="edge-line" :d="edge.path" marker-end="url(#capability-arrow)" />
                      <text class="edge-label" :x="edge.labelX" :y="edge.labelY" text-anchor="middle">
                        {{ edge.transition.label }}
                      </text>
                    </g>
                  </svg>
                  <button
                    v-for="node in graphNodes"
                    :key="node.key"
                    type="button"
                    class="graph-node"
                    :class="[node.type, { active: selectedNodeKey === node.key, handoff: node.handoffEnabled }]"
                    :style="{ left: `${node.x}px`, top: `${node.y}px` }"
                    @click="selectNode(node.key)"
                  >
                    <span>{{ nodeTypeText(node.type) }}</span>
                    <em v-if="node.type !== 'outcome'" class="node-mode">
                      {{ node.handoffEnabled ? '转人工' : 'AI 回复' }}
                    </em>
                    <strong>{{ node.name }}</strong>
                    <small>{{ node.subtitle }}</small>
                  </button>
                </div>
              </div>
            </section>

            <aside class="detail-panel">
              <template v-if="selectedTransition">
                <div class="detail-kicker">流程连线</div>
                <h2>{{ selectedTransition.label }}</h2>
                <p>{{ stepName(selectedTransition.from_step) }} → {{ transitionTargetName(selectedTransition) }}</p>
                <dl>
                  <dt>优先级</dt>
                  <dd>{{ selectedTransition.priority }}</dd>
                  <dt>判断方式</dt>
                  <dd>{{ selectedTransition.condition ? conditionModeText(selectedTransition.condition.mode) : '默认出口' }}</dd>
                </dl>
                <section v-if="selectedTransition.condition">
                  <h3>进入条件</h3>
                  <ul>
                    <li v-for="(condition, index) in selectedTransition.condition.conditions" :key="index">
                      <strong>{{ condition.description }}</strong>
                      <small>{{ conditionText(condition) }}</small>
                    </li>
                  </ul>
                </section>
              </template>

              <template v-else-if="selectedStep">
                <div class="detail-kicker">{{ stepTypeText(selectedStep.type) }}</div>
                <div class="detail-title-row">
                  <h2>{{ selectedStep.name }}</h2>
                  <div v-if="selectedStep.node_id" class="step-handoff-control">
                    <span>{{ selectedStep.handoff_enabled ? '转人工' : 'AI 回复' }}</span>
                    <ElSwitch
                      :model-value="Boolean(selectedStep.handoff_enabled)"
                      :loading="savingNodeId === selectedStep.node_id"
                      @change="updateNodeHandoff"
                    />
                  </div>
                </div>
                <p>{{ selectedStep.description || '暂无补充说明' }}</p>
                <section v-if="selectedStep.goal">
                  <h3>步骤目标</h3>
                  <p>{{ selectedStep.goal }}</p>
                </section>
                <section v-if="selectedStep.directions?.length">
                  <h3>执行方向</h3>
                  <ol>
                    <li v-for="direction in selectedStep.directions" :key="direction">{{ direction }}</li>
                  </ol>
                </section>
                <section v-if="selectedStep.collect?.length">
                  <h3>收集数据</h3>
                  <ul>
                    <li v-for="fact in selectedStep.collect" :key="fact.key">
                      <strong>{{ fact.key }} <em v-if="fact.required_for_completion">必需</em></strong>
                      <small>{{ fact.description }}</small>
                    </li>
                  </ul>
                </section>
                <section v-if="selectedStep.capabilities?.length || selectedStep.capability_id">
                  <h3>使用能力</h3>
                  <button
                    v-for="binding in selectedStep.capabilities || []"
                    :key="binding.capability_id"
                    type="button"
                    class="capability-link"
                    @click="openCapability(binding.capability_id)"
                  >
                    {{ capabilityName(binding.capability_id) }}
                    <small>{{ binding.purpose }}</small>
                  </button>
                  <button
                    v-if="selectedStep.capability_id"
                    type="button"
                    class="capability-link"
                    @click="openCapability(selectedStep.capability_id)"
                  >
                    {{ capabilityName(selectedStep.capability_id) }}
                    <small>明确执行动作</small>
                  </button>
                </section>
                <section v-if="selectedStep.completion">
                  <h3>完成条件 · {{ conditionModeText(selectedStep.completion.mode) }}</h3>
                  <ul>
                    <li v-for="(condition, index) in selectedStep.completion.conditions" :key="index">
                      <strong>{{ condition.description }}</strong>
                      <small>{{ conditionText(condition) }}</small>
                    </li>
                  </ul>
                </section>
                <section v-if="selectedStep.type === 'wait'">
                  <h3>等待设置</h3>
                  <p>恢复事件：{{ selectedStep.resume_events?.join('、') }}</p>
                  <p>超时：{{ timeoutText(selectedStep) }}</p>
                </section>
              </template>

              <template v-else-if="selectedOutcome">
                <div class="detail-kicker">流程结果</div>
                <h2>{{ selectedOutcome.name }}</h2>
                <p>结果标识：{{ selectedOutcome.outcome_id }}</p>
                <section>
                  <h3>结果标签</h3>
                  <ElTag v-for="tag in selectedOutcome.result_tags" :key="tag" size="small" effect="plain">
                    {{ tag }}
                  </ElTag>
                </section>
                <section v-if="selectedOutcome.next_package_id">
                  <h3>后续经验包</h3>
                  <p>{{ selectedOutcome.next_package_id }}</p>
                </section>
              </template>
            </aside>
          </div>
          <ElEmpty v-else description="暂无经验包" />
        </ElTabPane>

        <ElTabPane label="能力模块" name="capabilities">
          <div class="capability-toolbar">
            <ElInput v-model="capabilityKeyword" clearable placeholder="搜索能力名称、标识或说明" />
            <ElSelect v-model="activeGroup" placeholder="能力分组">
              <ElOption label="全部分组" value="all" />
              <ElOption v-for="group in capabilityGroups" :key="group" :label="group" :value="group" />
            </ElSelect>
            <ElSelect v-model="visibilityFilter" placeholder="展示级别">
              <ElOption label="业务可见" value="business" />
              <ElOption label="全部能力" value="all" />
              <ElOption label="可拖拽动作" value="draggable" />
              <ElOption label="步骤内配置" value="configurable" />
              <ElOption label="系统内部" value="hidden" />
            </ElSelect>
          </div>
          <div class="capability-grid">
            <button
              v-for="item in filteredCapabilities"
              :key="item.capability_id"
              type="button"
              class="capability-card"
              @click="openCapability(item.capability_id)"
            >
              <div class="capability-card-head">
                <span class="capability-icon">{{ capabilityIcon(item) }}</span>
                <span>
                  <strong>{{ item.name }}</strong>
                  <small>{{ item.ui.group }}</small>
                </span>
              </div>
              <p>{{ item.business.action }}</p>
              <div class="card-tags">
                <ElTag size="small" :type="aiModeTagType(item.business.ai_mode)" effect="plain">
                  {{ aiModeText(item.business.ai_mode) }}
                </ElTag>
                <ElTag size="small" :type="customerContactTagType(item.business.customer_contact)" effect="plain">
                  {{ customerContactText(item.business.customer_contact) }}
                </ElTag>
                <ElTag v-if="item.business.staff_notification" size="small" type="warning" effect="plain">会通知工作人员</ElTag>
              </div>
              <small class="usage-count">被 {{ item.used_by.length }} 个步骤引用</small>
            </button>
          </div>
          <ElEmpty v-if="!filteredCapabilities.length" description="没有匹配的能力" />
        </ElTabPane>
      </ElTabs>
    </div>

    <ElDrawer
      v-model="capabilityDrawerVisible"
      :title="selectedCapability?.name || '能力详情'"
      size="min(720px, 96vw)"
    >
      <template v-if="selectedCapability">
        <div class="drawer-tags">
          <ElTag effect="plain">{{ selectedCapability.ui.group }}</ElTag>
          <ElTag :type="aiModeTagType(selectedCapability.business.ai_mode)" effect="plain">
            {{ aiModeText(selectedCapability.business.ai_mode) }}
          </ElTag>
          <ElTag :type="customerContactTagType(selectedCapability.business.customer_contact)" effect="plain">
            {{ customerContactText(selectedCapability.business.customer_contact) }}
          </ElTag>
          <ElTag v-if="selectedCapability.business.staff_notification" type="warning" effect="plain">会通知工作人员</ElTag>
        </div>
        <section class="business-action">
          <span>具体动作</span>
          <p>{{ selectedCapability.business.action }}</p>
        </section>
        <div class="business-grid">
          <article>
            <span>数据来源</span>
            <p>{{ selectedCapability.business.data_source }}</p>
          </article>
          <article>
            <span>AI 如何使用</span>
            <p>{{ selectedCapability.business.ai_mode_description }}</p>
          </article>
          <article>
            <span>是否触达客户</span>
            <p>{{ selectedCapability.business.customer_contact_description }}</p>
          </article>
          <article>
            <span>使用权限</span>
            <p>{{ selectedCapability.business.permission_description }}</p>
          </article>
          <article>
            <span>执行结果</span>
            <p>{{ selectedCapability.business.result_description }}</p>
          </article>
          <article :class="`risk-${selectedCapability.business.risk_level}`">
            <span>风险级别 · {{ riskLevelText(selectedCapability.business.risk_level) }}</span>
            <p>{{ selectedCapability.business.risk_description }}</p>
          </article>
        </div>

        <section v-if="selectedCapability.preconditions" class="drawer-section">
          <h3>什么时候可以使用</h3>
          <ul>
            <li v-for="(condition, index) in selectedCapability.preconditions.conditions" :key="index">
              <strong>{{ condition.description }}</strong>
              <small>{{ conditionText(condition) }}</small>
            </li>
          </ul>
        </section>
        <section class="drawer-section">
          <h3>使用规则</h3>
          <ul>
            <li v-for="guidance in selectedCapability.usage_guidance" :key="guidance">{{ guidance }}</li>
          </ul>
        </section>
        <section class="drawer-section">
          <h3>使用位置</h3>
          <div v-if="selectedCapability.used_by.length" class="usage-list">
            <button
              v-for="usage in selectedCapability.used_by"
              :key="`${usage.package_id}:${usage.step_id}`"
              type="button"
              @click="jumpToUsage(usage)"
            >
              <strong>{{ usage.package_name }} · {{ usage.step_name }}</strong>
              <small>{{ usageTypeText(usage.usage) }}</small>
            </button>
          </div>
          <p v-else class="muted">当前经验包尚未引用。</p>
        </section>
        <ElCollapse class="technical-collapse">
          <ElCollapseItem name="technical">
            <template #title>
              <span class="technical-title">研发信息 <small>业务人员可以忽略</small></span>
            </template>
            <ElDescriptions :column="1" border>
              <ElDescriptionsItem label="能力标识">{{ selectedCapability.capability_id }}</ElDescriptionsItem>
              <ElDescriptionsItem label="执行器">{{ selectedCapability.execution.handler_key }}</ElDescriptionsItem>
              <ElDescriptionsItem label="能力类型">{{ kindText(selectedCapability.kind) }}</ElDescriptionsItem>
              <ElDescriptionsItem label="展示级别">{{ visibilityText(selectedCapability.ui.visibility) }}</ElDescriptionsItem>
              <ElDescriptionsItem label="超时与重试">
                {{ selectedCapability.execution.timeout_seconds }} 秒 · 最多 {{ selectedCapability.execution.retry.max_attempts }} 次
              </ElDescriptionsItem>
              <ElDescriptionsItem label="幂等要求">{{ selectedCapability.execution.idempotency }}</ElDescriptionsItem>
              <ElDescriptionsItem label="技术权限">
                {{ selectedCapability.permissions.join('、') || '无额外权限' }}
              </ElDescriptionsItem>
              <ElDescriptionsItem label="技术副作用">
                {{ selectedCapability.side_effects.map(sideEffectText).join('、') || '无' }}
              </ElDescriptionsItem>
              <ElDescriptionsItem label="错误编码">
                {{ selectedCapability.error_codes.join('、') || '无' }}
              </ElDescriptionsItem>
            </ElDescriptions>
            <section class="schema-grid">
              <div>
                <h3>输入协议</h3>
                <pre>{{ formatJson(selectedCapability.input_schema) }}</pre>
              </div>
              <div>
                <h3>输出协议</h3>
                <pre>{{ formatJson(selectedCapability.output_schema) }}</pre>
              </div>
            </section>
          </ElCollapseItem>
        </ElCollapse>
      </template>
    </ElDrawer>
  </ContentWrap>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getCapabilityWorkbench,
  updateWorkbenchSopNodeHandoff,
  type CapabilityAiMode,
  type CapabilityCustomerContact,
  type CapabilityItem,
  type CapabilityKind,
  type CapabilityRiskLevel,
  type CapabilityUsage,
  type CapabilityVisibility,
  type CapabilityWorkbenchResponse,
  type ConditionClause,
  type ExperiencePackage,
  type ExperienceStep,
  type ExperienceTransition
} from '@/api/admin/capabilityWorkbench'

const NODE_WIDTH = 190
const NODE_HEIGHT = 84

interface GraphNode {
  key: string
  id: string
  name: string
  subtitle: string
  type: ExperienceStep['type'] | 'outcome'
  handoffEnabled: boolean
  x: number
  y: number
}

interface GraphEdge {
  transition: ExperienceTransition
  path: string
  labelX: number
  labelY: number
}

const loading = ref(false)
const data = ref<CapabilityWorkbenchResponse | null>(null)
const activeTab = ref('flow')
const activePackageId = ref('')
const selectedNodeKey = ref('')
const selectedTransitionId = ref('')
const capabilityKeyword = ref('')
const activeGroup = ref('all')
const visibilityFilter = ref('business')
const capabilityDrawerVisible = ref(false)
const selectedCapabilityId = ref('')
const savingNodeId = ref('')

const packages = computed(() => data.value?.experience_packages || [])
const capabilities = computed(() => data.value?.capabilities || [])
const activePackage = computed(() =>
  packages.value.find((item) => item.package_id === activePackageId.value) || packages.value[0] || null
)
const capabilityMap = computed(() => new Map(capabilities.value.map((item) => [item.capability_id, item])))

const graphNodes = computed<GraphNode[]>(() => {
  const current = activePackage.value
  if (!current) return []
  const stepNodes = current.steps.map((step, index) => {
    const position = current.layout[step.step_id] || { x: 80 + index * 260, y: 180 }
    return {
      key: `step:${step.step_id}`,
      id: step.step_id,
      name: step.name,
      subtitle: stepSubtitle(step),
      type: step.type,
      handoffEnabled: Boolean(step.handoff_enabled),
      x: position.x,
      y: position.y
    } satisfies GraphNode
  })
  const maxStepX = Math.max(...stepNodes.map((node) => node.x), 0)
  const outcomeX = maxStepX + 300
  const outcomeNodes = current.outcomes.map((outcome, index) => ({
    key: `outcome:${outcome.outcome_id}`,
    id: outcome.outcome_id,
    name: outcome.name,
    subtitle: outcome.next_package_id ? `转入 ${outcome.next_package_id}` : '流程结束',
    type: 'outcome' as const,
    handoffEnabled: false,
    x: outcomeX,
    y: 50 + index * 112
  }))
  return [...stepNodes, ...outcomeNodes]
})

const nodeMap = computed(() => new Map(graphNodes.value.map((node) => [node.key, node])))

const graphEdges = computed<GraphEdge[]>(() => {
  const current = activePackage.value
  if (!current) return []
  return current.transitions.flatMap((transition) => {
    const source = nodeMap.value.get(`step:${transition.from_step}`)
    const targetKey = transition.to_step ? `step:${transition.to_step}` : `outcome:${transition.outcome}`
    const target = nodeMap.value.get(targetKey)
    if (!source || !target) return []
    const siblings = current.transitions.filter((item) => item.from_step === transition.from_step)
    const siblingIndex = siblings.findIndex((item) => item.transition_id === transition.transition_id)
    const sourceOffset = (siblingIndex - (siblings.length - 1) / 2) * 12
    const startX = source.x + NODE_WIDTH
    const startY = source.y + NODE_HEIGHT / 2 + sourceOffset
    const endX = target.x
    const endY = target.y + NODE_HEIGHT / 2
    const forward = endX >= startX
    const controlDistance = forward ? Math.max((endX - startX) / 2, 48) : 110
    const firstControlX = startX + controlDistance
    const secondControlX = forward ? endX - controlDistance : endX - 110
    return [{
      transition,
      path: `M ${startX} ${startY} C ${firstControlX} ${startY}, ${secondControlX} ${endY}, ${endX} ${endY}`,
      labelX: (startX + endX) / 2,
      labelY: (startY + endY) / 2 - 8
    }]
  })
})

const canvasSize = computed(() => ({
  width: Math.max(...graphNodes.value.map((node) => node.x + NODE_WIDTH + 80), 900),
  height: Math.max(...graphNodes.value.map((node) => node.y + NODE_HEIGHT + 80), 560)
}))
const canvasStyle = computed(() => ({ width: `${canvasSize.value.width}px`, height: `${canvasSize.value.height}px` }))

const selectedStep = computed(() => {
  if (!selectedNodeKey.value.startsWith('step:')) return null
  const id = selectedNodeKey.value.slice(5)
  return activePackage.value?.steps.find((step) => step.step_id === id) || null
})
const selectedOutcome = computed(() => {
  if (!selectedNodeKey.value.startsWith('outcome:')) return null
  const id = selectedNodeKey.value.slice(8)
  return activePackage.value?.outcomes.find((outcome) => outcome.outcome_id === id) || null
})
const selectedTransition = computed(() =>
  activePackage.value?.transitions.find((item) => item.transition_id === selectedTransitionId.value) || null
)
const selectedCapability = computed(() => capabilityMap.value.get(selectedCapabilityId.value) || null)

const capabilityGroups = computed(() =>
  [...new Set(capabilities.value.map((item) => item.ui.group))].sort((left, right) => left.localeCompare(right, 'zh-CN'))
)
const filteredCapabilities = computed(() => {
  const keyword = capabilityKeyword.value.trim().toLowerCase()
  return capabilities.value.filter((item) => {
    if (activeGroup.value !== 'all' && item.ui.group !== activeGroup.value) return false
    if (visibilityFilter.value === 'business' && item.ui.visibility === 'hidden') return false
    if (visibilityFilter.value !== 'all' && visibilityFilter.value !== 'business' && item.ui.visibility !== visibilityFilter.value) return false
    if (!keyword) return true
    return [
      item.name,
      item.capability_id,
      item.description,
      item.ui.summary,
      item.business.action,
      item.business.data_source
    ]
      .some((value) => value.toLowerCase().includes(keyword))
  })
})

const load = async () => {
  loading.value = true
  try {
    data.value = await getCapabilityWorkbench()
    if (!activePackageId.value || !packages.value.some((item) => item.package_id === activePackageId.value)) {
      activePackageId.value = packages.value[0]?.package_id || ''
    }
    ensureSelectedNode()
  } finally {
    loading.value = false
  }
}

const selectPackage = (packageId: string) => {
  activePackageId.value = packageId
  selectedTransitionId.value = ''
  const item = packages.value.find((current) => current.package_id === packageId)
  selectedNodeKey.value = item ? `step:${item.entry.start_step_id}` : ''
}

const ensureSelectedNode = () => {
  if (!activePackage.value) {
    selectedNodeKey.value = ''
    return
  }
  if (!graphNodes.value.some((node) => node.key === selectedNodeKey.value)) {
    selectedNodeKey.value = `step:${activePackage.value.entry.start_step_id}`
  }
}

const selectNode = (key: string) => {
  selectedNodeKey.value = key
  selectedTransitionId.value = ''
}

const selectTransition = (transitionId: string) => {
  selectedTransitionId.value = transitionId
  selectedNodeKey.value = ''
}

const updateNodeHandoff = async (value: string | number | boolean) => {
  const step = selectedStep.value
  if (!step?.node_id) return
  const previous = Boolean(step.handoff_enabled)
  const enabled = Boolean(value)
  step.handoff_enabled = enabled
  savingNodeId.value = step.node_id
  try {
    const result = await updateWorkbenchSopNodeHandoff({
      node_id: step.node_id,
      handoff_enabled: enabled
    })
    step.handoff_enabled = result.handoff_enabled
    ElMessage.success(`${step.name}已切换为${result.handoff_enabled ? '转人工' : 'AI 回复'}`)
  } catch {
    step.handoff_enabled = previous
  } finally {
    savingNodeId.value = ''
  }
}

const openCapability = (capabilityId: string) => {
  selectedCapabilityId.value = capabilityId
  capabilityDrawerVisible.value = true
}

const jumpToUsage = (usage: CapabilityUsage) => {
  activeTab.value = 'flow'
  selectPackage(usage.package_id)
  const targetPackage = packages.value.find((item) => item.package_id === usage.package_id)
  selectedNodeKey.value = targetPackage?.steps.some((step) => step.step_id === usage.step_id)
    ? `step:${usage.step_id}`
    : `step:${targetPackage?.entry.start_step_id || ''}`
  capabilityDrawerVisible.value = false
}

const capabilityName = (id: string) => capabilityMap.value.get(id)?.name || id
const stepName = (id: string) => activePackage.value?.steps.find((step) => step.step_id === id)?.name || id
const outcomeName = (id: string) => activePackage.value?.outcomes.find((outcome) => outcome.outcome_id === id)?.name || id
const transitionTargetName = (transition: ExperienceTransition) =>
  transition.to_step ? stepName(transition.to_step) : outcomeName(transition.outcome || '')

const stepSubtitle = (step: ExperienceStep) => {
  if (step.node_id) return step.description || ''
  if (step.type === 'action') return capabilityName(step.capability_id || '')
  if (step.type === 'wait') return `等待 ${step.resume_events?.length || 0} 类事件`
  if (step.type === 'decision') return step.strategy === 'hybrid' ? '规则 + AI 判断' : `${step.strategy} 判断`
  return `${step.capabilities?.length || 0} 项可用能力`
}

const nodeTypeText = (type: GraphNode['type']) => type === 'outcome' ? '结果' : stepTypeText(type)
const stepTypeText = (type: ExperienceStep['type']) => ({
  agent_stage: '对话阶段',
  decision: '条件判断',
  action: '执行动作',
  wait: '等待事件'
})[type]
const statusText = (status: ExperiencePackage['status']) => ({ draft: '草稿', published: '已发布', deprecated: '已停用' })[status]
const conditionModeText = (mode: 'all' | 'any') => mode === 'all' ? '全部满足' : '任一满足'
const conditionText = (condition: ConditionClause) => {
  if (condition.kind === 'semantic') return 'AI 语义判断'
  const value = condition.value === undefined ? '' : ` ${JSON.stringify(condition.value)}`
  return `${condition.path || ''} ${condition.operator || ''}${value}`.trim()
}
const timeoutText = (step: ExperienceStep) => {
  if (!step.timeout) return '未配置'
  const base = step.timeout.parameter ? `参数 ${step.timeout.parameter}` : `${step.timeout.value}`
  return `${base} ${step.timeout.unit}`
}
const visibilityText = (value: CapabilityVisibility) => ({ hidden: '系统内部', configurable: '步骤内配置', draggable: '可拖拽动作' })[value]
const aiModeText = (value: CapabilityAiMode) => ({
  automatic: 'AI 可主动使用',
  conditional: '满足条件可使用',
  workflow_only: '仅按经验包执行',
  human_confirm: '需要人工确认'
})[value]
const aiModeTagType = (value: CapabilityAiMode) => ({
  automatic: 'success',
  conditional: 'warning',
  workflow_only: 'primary',
  human_confirm: 'danger'
} as const)[value]
const customerContactText = (value: CapabilityCustomerContact) => ({
  none: '不会联系客户',
  reply_support: '用于组织回复',
  direct_message: '会发送消息',
  direct_card: '会发送商品卡',
  conversation_handoff: '会转人工'
})[value]
const customerContactTagType = (value: CapabilityCustomerContact) => ({
  none: 'info',
  reply_support: 'success',
  direct_message: 'warning',
  direct_card: 'warning',
  conversation_handoff: 'danger'
} as const)[value]
const riskLevelText = (value: CapabilityRiskLevel) => ({ low: '低', medium: '中', high: '高' })[value]
const usageTypeText = (value: CapabilityUsage['usage']) => ({ allowed: '可使用', required: '必须使用', action: '执行动作' })[value]
const kindText = (value: CapabilityKind) => ({ query: '查询', action: '动作', human: '人工协作', internal: '系统内部' })[value]
const sideEffectText = (value: string) => ({
  customer_state: '修改客户状态',
  prepared_customer_message: '准备客户消息',
  external_write: '写入外部系统',
  human_notification: '通知工作人员',
  conversation_handoff: '改变会话接管状态'
})[value] || value
const capabilityIcon = (item: CapabilityItem) => ({ query: '查', action: '动', human: '人', internal: '内' })[item.kind]
const formatJson = (value: unknown) => JSON.stringify(value, null, 2)

onMounted(load)
</script>

<style scoped>
.page-head, .title-row, .graph-head, .graph-legend, .capability-toolbar, .drawer-tags { display: flex; align-items: center; gap: 12px; }
.page-head { justify-content: space-between; }
.page-head h1 { margin: 0; font-size: 25px; }
.page-head p { margin: 7px 0 0; color: var(--el-text-color-secondary); }
.notice { margin: 18px 0; }
.workbench-body { min-height: 560px; }
.metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }
.metrics article { padding: 16px 18px; border: 1px solid var(--el-border-color-light); border-radius: 12px; background: #fff; }
.metrics span, .metrics strong, .metrics small { display: block; }
.metrics span { color: var(--el-text-color-secondary); font-size: 13px; }
.metrics strong { margin: 7px 0 4px; color: #173f34; font-size: 28px; }
.metrics small { color: var(--el-text-color-secondary); }
.workspace-tabs { padding: 0 18px 18px; border: 1px solid var(--el-border-color-light); border-radius: 12px; background: #fff; }
.flow-workspace { display: grid; grid-template-columns: 230px minmax(560px, 1fr) 320px; min-height: 660px; border: 1px solid var(--el-border-color-lighter); border-radius: 10px; overflow: hidden; }
.package-panel, .detail-panel { padding: 16px; background: #fbfcfc; }
.package-panel { border-right: 1px solid var(--el-border-color-lighter); }
.detail-panel { overflow: auto; border-left: 1px solid var(--el-border-color-lighter); }
.panel-title { display: flex; justify-content: space-between; margin-bottom: 12px; font-weight: 700; }
.panel-title small { display: grid; width: 24px; height: 24px; place-items: center; border-radius: 999px; background: var(--el-fill-color); }
.package-card { width: 100%; padding: 13px; border: 1px solid var(--el-border-color-light); border-radius: 10px; color: inherit; background: #fff; text-align: left; cursor: pointer; }
.package-card.active { border-color: var(--el-color-primary); box-shadow: 0 0 0 1px var(--el-color-primary-light-7); }
.package-card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.package-card-head em { padding: 2px 6px; color: #9a5a00; font-size: 11px; font-style: normal; background: #fff1d6; border-radius: 999px; }
.package-card > small, .package-card p { color: var(--el-text-color-secondary); }
.package-card > small { display: block; margin-top: 6px; }
.package-card p { margin: 8px 0 0; font-size: 12px; line-height: 1.55; }
.package-meta { display: flex; align-items: flex-start; flex-wrap: wrap; gap: 7px; margin-top: 20px; }
.package-meta > span { width: 100%; margin-top: 8px; color: var(--el-text-color-secondary); font-size: 12px; font-weight: 700; }
.graph-panel { min-width: 0; background: #f7faf9; }
.graph-head { min-height: 62px; padding: 10px 16px; justify-content: space-between; border-bottom: 1px solid var(--el-border-color-lighter); background: #fff; }
.graph-head strong, .graph-head span { display: block; }
.graph-head > div > span { margin-top: 4px; color: var(--el-text-color-secondary); font-size: 12px; }
.graph-legend { flex-wrap: wrap; justify-content: flex-end; font-size: 11px; color: var(--el-text-color-secondary); }
.graph-legend span { display: flex; align-items: center; gap: 4px; }
.graph-legend i { width: 9px; height: 9px; border-radius: 3px; }
.graph-legend i.agent { background: #67b79a; }
.graph-legend i.decision { background: #7c91cf; }
.graph-legend i.action { background: #e5a74f; }
.graph-legend i.wait { background: #aa84c4; }
.graph-legend i.outcome { background: #5d6b66; }
.graph-scroll { height: 598px; overflow: auto; }
.graph-canvas { position: relative; background-image: radial-gradient(#cfdad6 1px, transparent 1px); background-size: 22px 22px; }
.graph-lines { position: absolute; inset: 0; overflow: visible; pointer-events: none; }
.edge-group { color: #9eb2aa; }
.edge-group.active { color: var(--el-color-primary); }
.edge-line { fill: none; stroke: currentColor; stroke-width: 2; pointer-events: none; }
.edge-hit { fill: none; stroke: transparent; stroke-width: 16; pointer-events: stroke; cursor: pointer; }
.edge-label { fill: #70817b; font-size: 10px; paint-order: stroke; stroke: #f7faf9; stroke-width: 5px; stroke-linejoin: round; pointer-events: none; }
#capability-arrow path { fill: currentColor; }
.graph-node { position: absolute; width: 190px; height: 84px; padding: 10px 12px; border: 2px solid transparent; border-radius: 11px; color: #263c35; background: #fff; box-shadow: 0 5px 16px rgb(33 76 63 / 10%); text-align: left; cursor: pointer; z-index: 2; }
.graph-node:hover, .graph-node.active { transform: translateY(-1px); box-shadow: 0 8px 22px rgb(33 76 63 / 18%); }
.graph-node.active { border-color: var(--el-color-primary); }
.graph-node.handoff { background: #fff8f5; }
.graph-node > span { color: var(--el-text-color-secondary); font-size: 10px; font-weight: 700; letter-spacing: .08em; }
.graph-node .node-mode { position: absolute; top: 9px; right: 10px; padding: 2px 6px; color: #4d7568; background: #edf7f3; border-radius: 999px; font-size: 10px; font-style: normal; }
.graph-node.handoff .node-mode { color: #b64f2e; background: #ffebe3; }
.graph-node strong, .graph-node small { display: block; }
.graph-node strong { margin-top: 5px; font-size: 15px; }
.graph-node small { margin-top: 6px; color: var(--el-text-color-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.graph-node.agent_stage { border-left: 5px solid #67b79a; }
.graph-node.decision { border-left: 5px solid #7c91cf; }
.graph-node.action { border-left: 5px solid #e5a74f; }
.graph-node.wait { border-left: 5px solid #aa84c4; }
.graph-node.outcome { border-left: 5px solid #5d6b66; background: #f0f4f2; }
.detail-kicker { color: var(--el-color-primary); font-size: 11px; font-weight: 800; letter-spacing: .1em; }
.detail-title-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.step-handoff-control { display: flex; flex: 0 0 auto; align-items: center; gap: 8px; padding: 8px 10px; border: 1px solid var(--el-border-color-light); border-radius: 9px; background: #fff; }
.step-handoff-control span { color: var(--el-text-color-secondary); font-size: 12px; }
.detail-panel h2 { margin: 7px 0; font-size: 21px; }
.detail-panel > p, .detail-panel section > p { color: var(--el-text-color-secondary); line-height: 1.65; }
.detail-panel section { padding-top: 4px; margin-top: 18px; border-top: 1px solid var(--el-border-color-lighter); }
.detail-panel h3, .drawer-section h3 { margin: 13px 0 9px; font-size: 14px; }
.detail-panel ol, .detail-panel ul, .drawer-section ul { padding-left: 20px; }
.detail-panel li, .drawer-section li { margin: 7px 0; line-height: 1.55; }
.detail-panel li strong, .detail-panel li small, .drawer-section li strong, .drawer-section li small { display: block; }
.detail-panel li small, .drawer-section li small { margin-top: 3px; color: var(--el-text-color-secondary); }
.detail-panel li em { margin-left: 4px; color: #a26000; font-size: 10px; font-style: normal; }
.detail-panel dl { display: grid; grid-template-columns: 80px 1fr; gap: 8px; font-size: 13px; }
.detail-panel dt { color: var(--el-text-color-secondary); }
.detail-panel dd { margin: 0; }
.capability-link { display: block; width: 100%; margin: 7px 0; padding: 9px 10px; border: 1px solid var(--el-border-color-light); border-radius: 8px; color: inherit; background: #fff; text-align: left; cursor: pointer; }
.capability-link small { display: block; margin-top: 3px; color: var(--el-text-color-secondary); }
.capability-toolbar { margin: 4px 0 16px; }
.capability-toolbar :deep(.el-input) { width: min(380px, 100%); }
.capability-toolbar :deep(.el-select) { width: 170px; }
.capability-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 13px; }
.capability-card { min-height: 210px; padding: 15px; border: 1px solid var(--el-border-color-light); border-radius: 11px; color: inherit; background: #fff; text-align: left; cursor: pointer; }
.capability-card:hover { border-color: var(--el-color-primary-light-5); box-shadow: 0 7px 18px rgb(33 76 63 / 10%); transform: translateY(-1px); }
.capability-card-head { display: flex; align-items: center; gap: 11px; }
.capability-card-head strong, .capability-card-head small { display: block; }
.capability-card-head small { margin-top: 4px; color: var(--el-text-color-secondary); font-size: 11px; }
.capability-icon { display: grid; flex: 0 0 40px; width: 40px; height: 40px; place-items: center; color: #fff; font-weight: 800; background: #397b65; border-radius: 10px; }
.capability-card p { min-height: 82px; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.card-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.usage-count { display: block; margin-top: 12px; color: var(--el-text-color-secondary); }
.drawer-tags { flex-wrap: wrap; }
.business-action { padding: 16px 18px; margin-top: 18px; border: 1px solid #b9dfd1; border-radius: 11px; background: #f0faf6; }
.business-action span, .business-grid span { color: #397b65; font-size: 12px; font-weight: 800; }
.business-action p { margin: 7px 0 0; color: #203e34; font-size: 16px; font-weight: 600; line-height: 1.65; }
.business-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }
.business-grid article { padding: 13px 14px; border: 1px solid var(--el-border-color-light); border-radius: 9px; background: #fff; }
.business-grid article p { margin: 7px 0 0; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.business-grid article.risk-medium { border-color: #eed5a8; background: #fffaf0; }
.business-grid article.risk-high { border-color: #efb4b4; background: #fff5f5; }
.drawer-section { margin-top: 24px; }
.usage-list { display: grid; gap: 8px; }
.usage-list button { padding: 10px 12px; border: 1px solid var(--el-border-color-light); border-radius: 8px; color: inherit; background: #fff; text-align: left; cursor: pointer; }
.usage-list strong, .usage-list small { display: block; }
.usage-list small { margin-top: 4px; color: var(--el-text-color-secondary); }
.technical-collapse { margin-top: 24px; border-top: 1px solid var(--el-border-color-light); }
.technical-title { display: flex; align-items: center; gap: 8px; font-weight: 700; }
.technical-title small { color: var(--el-text-color-secondary); font-weight: 400; }
.schema-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 16px; }
pre { max-height: 360px; padding: 12px; overflow: auto; color: #d8eee6; background: #173f34; border-radius: 8px; font-size: 11px; line-height: 1.55; }
.muted { color: var(--el-text-color-secondary); }
@media (max-width: 1480px) { .flow-workspace { grid-template-columns: 210px minmax(520px, 1fr) 290px; } .capability-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 1120px) { .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } .flow-workspace { grid-template-columns: 210px minmax(560px, 1fr); overflow: auto; } .detail-panel { display: none; } .capability-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 760px) { .page-head, .capability-toolbar { align-items: flex-start; flex-direction: column; } .metrics, .capability-grid, .business-grid, .schema-grid { grid-template-columns: 1fr; } .workspace-tabs { padding: 0 10px 12px; } .capability-toolbar :deep(.el-select) { width: 100%; } .flow-workspace { grid-template-columns: 1fr; } .package-panel { border-right: 0; border-bottom: 1px solid var(--el-border-color-lighter); } .graph-head { align-items: flex-start; flex-direction: column; gap: 8px; } .graph-legend { justify-content: flex-start; } .graph-panel { min-height: 620px; } }
</style>
