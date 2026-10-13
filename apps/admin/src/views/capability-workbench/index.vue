<template>
  <section v-loading="loading" class="canvas-workbench" aria-label="流程画布">
    <div ref="stage" class="canvas-stage" :class="{ connecting: connectionFrom, panning: gesture?.kind === 'pan' }"
      :style="{ backgroundSize: `${24 * view.zoom}px ${24 * view.zoom}px`, backgroundPosition: `${view.x}px ${view.y}px` }"
      @pointerdown="beginPan" @wheel.prevent="wheelZoom">
      <div class="canvas-world" :style="{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})` }">
        <svg class="connections" aria-label="节点连线">
          <defs><marker id="flow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="context-stroke" /></marker></defs>
          <g v-for="edge in drawnEdges" :key="edge.id" @pointerdown.stop @click.stop="selectEdge(edge.id)">
            <path :d="edge.path" class="edge-hit" />
            <path :d="edge.path" class="edge-line" :class="{ selected: selectedEdgeId === edge.id }" marker-end="url(#flow-arrow)" />
            <text v-if="edge.label" :x="edge.labelX" :y="edge.labelY" class="edge-label" text-anchor="middle">{{ edge.label }}</text>
          </g>
          <path v-if="pendingPath" :d="pendingPath" class="edge-line pending" />
        </svg>
        <div v-for="node in definition?.nodes || []" :key="node.id" class="flow-node" :class="[node.type, { selected: selectedNodeId === node.id, invalid: nodeIssues(node.id).length }]"
          :style="{ left: `${node.position.x}px`, top: `${node.position.y}px`, height: `${nodeHeight(node)}px` }"
          role="button" tabindex="0" :aria-label="`${typeLabels[node.type]}：${node.name || '未命名节点'}`"
          @pointerdown.stop="beginNodeDrag($event, node)" @keydown.enter.stop="selectNode(node.id)">
          <button v-if="node.type !== 'start'" class="port input" :data-input-id="node.id" :disabled="locked" :aria-label="`连接到${node.name}`" @pointerdown.stop @click.stop="finishConnection(node.id)"></button>
          <div class="node-type"><span class="type-icon">{{ typeIcons[node.type] }}</span>{{ typeLabels[node.type] }}<span v-if="nodeIssues(node.id).length" class="node-error">待修改</span></div>
          <strong class="node-name">{{ node.name || '未命名节点' }}</strong>
          <div v-if="node.type === 'condition'" class="canvas-branches">
            <button v-if="!node.branches.length" class="add-branch" :disabled="locked" @pointerdown.stop @click.stop="addBranch(node)">增加分支</button>
            <div v-for="(branch, index) in node.branches" :key="branch.id" class="canvas-branch">
              <div class="branch-heading"><span>{{ branchName(index) }}</span><button v-if="index === 0" class="add-branch" :disabled="locked || node.branches.length >= 150" @pointerdown.stop @click.stop="addBranch(node)">增加分支</button><button v-if="node.branches.length > 1" class="delete-branch" :disabled="locked" :aria-label="`删除${branchName(index)}`" @pointerdown.stop @click.stop="removeBranch(node, branch.id)">×</button></div>
              <ElInput v-model="branch.condition" type="textarea" :rows="2" :disabled="locked" maxlength="4000" placeholder="填写这个分支的条件" resize="none" @pointerdown.stop="selectNode(node.id)" @click.stop @keydown.stop="keyDown" @wheel.stop />
              <button class="port output branch-port" :class="{ active: connectionFrom === node.id && connectionBranch === branch.id }" :disabled="locked" :aria-label="`从${node.name}的${branchName(index)}连线`" @pointerdown.stop="beginConnection($event, node.id, branch.id)" @click.stop></button>
            </div>
          </div>
          <template v-else-if="node.type === 'general'">
            <div v-for="field in businessFields" :key="field.key" class="node-preview"><span>{{ field.label }}</span><p>{{ node[field.key] || '待填写' }}</p></div>
          </template>
          <template v-else-if="node.type === 'scheduled_send'"><div class="schedule-summary">{{ scheduleSummary(node) }}</div><div class="node-preview"><span>发送内容</span><p>{{ contentSummary(node) }}</p></div></template>
          <p v-else-if="node.type === 'handoff'" class="handoff-preview">按默认设置转交负责人<br>自动附带上下文，暂停 AI 回复</p>
          <p v-else-if="node.type === 'start'" class="start-preview">{{ node.trigger_condition || '标签被打上、加上好友或特定时间点…' }}</p>
          <p v-else class="end-preview">结束本次流程</p>
          <button v-if="node.type !== 'end' && node.type !== 'condition'" class="port output" :class="{ active: connectionFrom === node.id }" :disabled="locked" :aria-label="`从${node.name}连线`" @pointerdown.stop="beginConnection($event, node.id)" @click.stop></button>
        </div>
      </div>
    </div>

    <div class="canvas-toolbar">
      <ElSelect :model-value="workflow?.workflow_id" placeholder="选择流程" class="workflow-picker" :disabled="busy" @change="switchWorkflow">
        <ElOption v-for="item in workflows" :key="item.workflow_id" :value="item.workflow_id" :label="item.name || '未命名流程'" />
      </ElSelect>
      <ElButton v-if="isAdmin()" :disabled="busy" @click="newWorkflow">新建流程</ElButton>
      <div v-if="definition" class="workflow-heading">
        <input v-model="definition.name" aria-label="流程名称" maxlength="80" placeholder="流程名称" :disabled="locked" />
        <span :class="{ unsaved: dirty }">{{ statusText }}</span>
      </div>
      <div class="toolbar-actions">
        <ElButton v-if="review" :disabled="busy" @click="panel = panel === 'review' ? null : 'review'">流程理解<span v-if="review.issues.length"> · {{ review.issues.length }}</span></ElButton>
        <ElButton :disabled="busy || !workflow" @click="reloadWorkflow">重新加载</ElButton>
        <ElButton v-if="isAdmin()" :disabled="busy || !workflow || !dirty" :loading="saving" @click="saveDraft">保存草稿</ElButton>
        <ElButton v-if="isAdmin()" type="primary" :disabled="busy || !workflow" :loading="publishing" @click="publish">{{ publishing ? 'Qwen 正在理解流程' : '发布' }}</ElButton>
      </div>
    </div>

    <div v-if="workflow" class="node-palette">
      <span>添加节点</span>
      <button v-for="type in nodeTypes" :key="type" :disabled="locked || (type === 'start' && definition?.nodes.some(node => node.type === 'start'))" @click="addNode(type)"><i :class="type">{{ typeIcons[type] }}</i>{{ typeLabels[type] }}<b>＋</b></button>
    </div>
    <div v-if="!workflow && !loading" class="empty-canvas">
      <div class="empty-symbol">＋</div><h2>从一条流程开始</h2><p>添加节点，用文字写清条件和行动，再连接起来。</p>
      <ElButton v-if="isAdmin()" type="primary" @click="newWorkflow">新建流程</ElButton>
      <p v-else>管理员创建的流程会显示在这里。</p>
    </div>

    <aside v-if="panel" class="configuration-panel">
      <div class="panel-heading"><strong>{{ panel === 'review' ? '整张流程的理解与检查' : selectedNode ? typeLabels[selectedNode.type] : '连线配置' }}</strong><button aria-label="关闭配置" @click="panel = null">×</button></div>
      <div class="panel-content">
        <template v-if="panel === 'review' && review">
          <div class="review-status" :class="{ failed: review.issues.length }">{{ review.issues.length ? `发布未通过 · ${review.issues.length} 项需要修改` : '流程理解已发布' }}</div>
          <p v-if="dirty || !workflow?.last_review" class="muted">{{ dirty ? '流程已修改，以下为上次检查结果，请重新发布。' : '以下为上次已发布版本的理解。' }}</p>
          <button v-for="(issue, index) in review.issues" :key="index" class="review-issue" @click="locateIssue(issue)"><span>{{ issue.node_id ? nodeName(issue.node_id) : '整张流程' }} · {{ fieldLabels[issue.field] }}</span><strong>{{ issue.message }}</strong><p>{{ issue.suggestion }}</p><small v-if="issue.node_id">定位到节点 →</small></button>
          <template v-if="review.summary"><h3>流程理解</h3><p class="review-copy">{{ review.summary }}</p></template>
          <div v-for="item in review.nodes" :key="item.node_id" class="node-understanding"><button @click="selectNode(item.node_id)">{{ nodeName(item.node_id) }} ↗</button><p>{{ item.understanding }}</p></div>
          <p v-if="!review.issues.length" class="review-note">已保存流程与 AI 理解，供后续实现实际执行逻辑使用。</p>
        </template>
        <template v-else-if="selectedNode">
          <label class="field-label">节点名称</label><ElInput v-model="selectedNode.name" :disabled="locked" maxlength="80" placeholder="给节点起一个业务名称" />
          <template v-if="selectedNode.type === 'start'">
            <label class="field-label">触发条件</label>
            <ElInput v-model="selectedNode.trigger_condition" type="textarea" :rows="7" :disabled="locked" maxlength="4000" show-word-limit placeholder="例如：客户被打上「服务中」标签时触发。&#10;或：客户加上好友时触发。&#10;或：每周四上午 10 点，对服务中的客户触发。" />
            <p class="field-help">用文字写清标签、加好友或时间触发条件。多种条件请说明满足任意一项还是全部满足。</p>
          </template>
          <template v-else-if="selectedNode.type === 'condition'">
            <button v-if="!selectedNode.branches.length" class="add-branch empty-branch" :disabled="locked" @click="addBranch(selectedNode)">增加分支</button>
            <div v-for="(branch, index) in selectedNode.branches" :key="branch.id" class="branch-editor">
              <div class="branch-heading"><span>{{ branchName(index) }}</span><button v-if="index === 0" class="add-branch" :disabled="locked || selectedNode.branches.length >= 150" @click="addBranch(selectedNode)">增加分支</button><button v-if="selectedNode.branches.length > 1" class="delete-branch" :disabled="locked" :aria-label="`删除${branchName(index)}`" @click="removeBranch(selectedNode, branch.id)">×</button></div>
              <ElInput v-model="branch.condition" type="textarea" :rows="3" :disabled="locked" maxlength="4000" placeholder="例如：客户明确提出选购需求" />
            </div>
          </template>
          <template v-else-if="selectedNode.type === 'scheduled_send'">
            <div class="schedule-controls"><div><label class="field-label">发送时间</label><ElTimePicker :model-value="selectedNode.send_time || null" :disabled="locked" :clearable="false" format="HH:mm" value-format="HH:mm" placeholder="选择时间" @update:model-value="setSendTime" /></div><div><label class="field-label">发送周期</label><ElSelect v-model="selectedNode.send_cycle" :disabled="locked" @change="changeCycle"><ElOption v-for="option in cycleOptions" :key="option.value" :value="option.value" :label="option.label" /></ElSelect></div></div>
            <p class="field-help">{{ scheduleHint(selectedNode) }}</p>
            <div class="content-heading"><label class="field-label">内容库</label><button :disabled="contentLoading" @click="loadContentSources">{{ contentLoading ? '加载中…' : '刷新' }}</button></div>
            <ElSelect :model-value="selectedNode.content_source?.library" :disabled="locked" placeholder="选择内容库" @change="changeContentLibrary"><ElOption v-for="item in libraryOptions" :key="item.id" :value="item.id" :label="item.label" /></ElSelect>
            <template v-if="selectedNode.content_source?.library === 'cloud_video'">
              <label class="field-label">内容分类</label>
              <ElSelect v-model="selectedNode.content_source.category" :disabled="locked || contentLoading || !selectedLibrary?.available" :loading="contentLoading" placeholder="选择视频内容分类" filterable>
                <ElOption v-if="selectedNode.content_source.category && !selectedCategory" :value="selectedNode.content_source.category" :label="`${selectedNode.content_source.category}（暂不可用）`" disabled />
                <ElOption v-for="item in selectedLibrary?.categories || []" :key="item.id" :value="item.id" :label="`${item.name}（${item.count} 个视频）`" />
              </ElSelect>
            </template>
            <p v-if="contentError || selectedLibrary?.message" class="content-warning">{{ contentError || selectedLibrary?.message }}</p>
            <p v-else-if="selectedNode.content_source?.library === 'cloud_video' && selectedNode.content_source.category && selectedLibrary && !selectedCategory" class="content-warning">该分类已不存在或暂无可用视频，请重新选择。</p>
            <p v-if="selectedNode.content_source" class="field-help">{{ selectedNode.content_source.library === 'care_manual' ? '从养护手册库按客户品种匹配内容，无匹配手册时跳过。' : '从所选分类中匹配视频，使用素材配套文案；无匹配视频时跳过。' }}</p>
            <label class="field-label">补充内容要求<span class="muted">（选填）</span></label>
            <ElInput v-model="selectedNode.action" type="textarea" :rows="3" :disabled="locked" maxlength="6000" placeholder="默认按所选内容库匹配；有额外要求时再填写，例如优先匹配客户的养护痛点。" />
          </template>
          <p v-else-if="selectedNode.type === 'handoff'" class="field-help">默认通知转人工设置中的负责人，附带客户需求和对话上下文。人工接管后暂停 AI 回复，由人工确认恢复。</p>
          <template v-else-if="selectedNode.type === 'general'">
            <div v-for="field in businessFields" :key="field.key" :data-field="field.key"><label class="field-label">{{ field.label }}</label><ElInput v-model="selectedNode[field.key]" type="textarea" :rows="field.key === 'action' ? 6 : 4" :disabled="locked" :maxlength="field.key === 'action' ? 6000 : 4000" show-word-limit :placeholder="field.placeholder" /></div>
            <p class="field-help">采集到明确的客户信息时，底层能力负责按现有标签打标并记录档案。</p>
          </template>
          <p v-else class="field-help">流程到达这个节点后结束。可以通过节点名称说明本次流程的结果。</p>
          <div v-for="(issue, index) in nodeIssues(selectedNode.id)" :key="index" class="inline-issue"><strong>{{ issue.message }}</strong><p>{{ issue.suggestion }}</p></div>
          <div v-if="selectedNode.type !== 'end' && selectedNode.type !== 'condition'" class="node-exits"><label class="field-label">后续节点</label><p v-if="!outgoingEdges.length" class="muted">从节点右侧圆点拖线到下一个节点。</p><button v-for="edge in outgoingEdges" :key="edge.id" @click="selectEdge(edge.id)">{{ edge.label || '继续' }} → {{ nodeName(edge.target) }}</button></div>
          <ElButton v-if="isAdmin()" type="danger" plain :disabled="busy" class="remove-button" @click="removeSelection">删除节点</ElButton>
        </template>
        <template v-else-if="selectedEdge">
          <p>{{ nodeName(selectedEdge.source) }} → {{ nodeName(selectedEdge.target) }}</p>
          <template v-if="selectedEdge.source_branch"><label class="field-label">{{ edgeLabel(selectedEdge) }}</label><p class="field-help">{{ edgeCondition(selectedEdge) }}</p></template>
          <template v-else><label class="field-label">连线说明</label><ElInput v-model="selectedEdge.label" :disabled="locked" maxlength="200" placeholder="选填，例如：客户明确需要人工处理" /></template>
          <ElButton v-if="isAdmin()" type="danger" plain :disabled="busy" class="remove-button" @click="removeSelection">删除连线</ElButton>
        </template>
      </div>
    </aside>
    <div class="canvas-controls"><button aria-label="缩小画布" @click="changeZoom(-0.1)">−</button><span>{{ Math.round(view.zoom * 100) }}%</span><button aria-label="放大画布" @click="changeZoom(0.1)">＋</button><i></i><button class="fit-button" @click="fitCanvas">适应画布</button></div>
    <div v-if="workflow" class="canvas-hint">{{ connectionFrom ? '连接到目标节点左侧圆点 · Esc 取消' : '拖动空白处平移 · 滚轮缩放 · 从圆点连接节点' }}</div>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElButton, ElInput, ElMessage, ElMessageBox, ElOption, ElSelect, ElTimePicker } from 'element-plus'
import { isAdmin } from '@/utils/gate'
import { createCanvasWorkflow, getCanvasWorkflow, listCanvasWorkflows, listCanvasContentSources, publishCanvasWorkflow, saveCanvasWorkflow, syncExistingSops,
  type CanvasContentLibrary, type CanvasContentLibraryOption, type CanvasEdge, type CanvasIssue, type CanvasNode, type CanvasNodeType, type CanvasSummary, type CanvasWorkflow } from '@/api/admin/canvasWorkflow'

const workflows = ref<CanvasSummary[]>([])
const workflow = ref<CanvasWorkflow | null>(null)
const savedFingerprint = ref('')
const loading = ref(false), saving = ref(false), publishing = ref(false)
const busy = computed(() => loading.value || saving.value || publishing.value)
const locked = computed(() => busy.value || !isAdmin())
const definition = computed(() => workflow.value?.definition)
const dirty = computed(() => Boolean(definition.value && JSON.stringify(definition.value) !== savedFingerprint.value))
const review = computed(() => workflow.value?.last_review || workflow.value?.published?.review)
const selectedNodeId = ref<string | null>(null), selectedEdgeId = ref<string | null>(null)
const selectedNode = computed(() => definition.value?.nodes.find(node => node.id === selectedNodeId.value))
const selectedEdge = computed(() => definition.value?.edges.find(edge => edge.id === selectedEdgeId.value))
const outgoingEdges = computed(() => definition.value?.edges.filter(edge => edge.source === selectedNodeId.value) || [])
const panel = ref<'node' | 'review' | null>(null)
const stage = ref<HTMLElement>()
const view = reactive({ x: 0, y: 0, zoom: 1 })
const connectionFrom = ref<string | null>(null)
const connectionBranch = ref<string | null>(null)
const connectionPoint = reactive({ x: 0, y: 0 })
const gesture = ref<{ kind: 'pan' | 'node'; nodeId?: string; clientX: number; clientY: number; x: number; y: number } | null>(null)
const editableNodeTypes: CanvasNodeType[] = ['general', 'scheduled_send', 'condition', 'handoff']
const nodeTypes: CanvasNodeType[] = ['start', ...editableNodeTypes, 'end']
const typeLabels = { start: '开始', general: '一般节点', scheduled_send: '定时发送', condition: '条件分支', handoff: '转人工', end: '结束' }
const typeIcons = { start: '▷', general: '◇', scheduled_send: '◷', condition: '⑂', handoff: '↗', end: '□' }
const contentLibraries = ref<CanvasContentLibraryOption[]>([])
const contentLoading = ref(false), contentError = ref('')
const libraryNames: Record<CanvasContentLibrary, string> = { care_manual: '养护手册', cloud_video: '云端视频' }
const libraryOptions = computed(() => (Object.keys(libraryNames) as CanvasContentLibrary[]).map(id => {
  const source = contentLibraries.value.find(item => item.id === id)
  return { id, label: source?.available ? `${source.name}（${source.count} ${id === 'care_manual' ? '篇' : '个视频'}）` : libraryNames[id] }
}))
const selectedLibrary = computed(() => contentLibraries.value.find(item => item.id === selectedNode.value?.content_source?.library))
const selectedCategory = computed(() => selectedLibrary.value?.categories.find(item => item.id === selectedNode.value?.content_source?.category))
const contentSummary = (node: CanvasNode) => node.content_source ? `${libraryNames[node.content_source.library]}${node.content_source.library === 'cloud_video' ? ` · ${node.content_source.category || '待选择分类'}` : ''}` : '请选择内容库'
async function loadContentSources() {
  if (contentLoading.value) return
  contentLoading.value = true
  contentError.value = ''
  try { contentLibraries.value = await listCanvasContentSources() }
  catch { contentLibraries.value = []; contentError.value = '内容库暂时无法加载，请点击刷新重试。' }
  finally { contentLoading.value = false }
}
function changeContentLibrary(library: CanvasContentLibrary) {
  if (!selectedNode.value || locked.value || selectedNode.value.content_source?.library === library) return
  selectedNode.value.content_source = { library, category: '' }
}
const cycleOptions = [{ value: 'daily', label: '每天' }, { value: 'every_three_days', label: '每三天' }, { value: 'weekly', label: '每周' }]
const branchName = (index: number) => {
  const number = index + 1, digits = '零一二三四五六七八九'
  const suffix = number < 10 ? digits[number] : number < 100 ? `${number >= 20 ? digits[Math.floor(number / 10)] : ''}十${number % 10 ? digits[number % 10] : ''}` : String(number)
  return `分支${suffix}`
}
const scheduleSummary = (node: CanvasNode) => `${node.send_cycle === 'weekly' && node.send_weekday !== null ? `每周${'一二三四五六日'[node.send_weekday]}` : cycleOptions.find(option => option.value === node.send_cycle)?.label || '每天'} ${node.send_time || '待选择时间'}`
const scheduleHint = (node: CanvasNode) => node.send_cycle === 'weekly' && node.send_weekday !== null
  ? `北京时间，每周${'一二三四五六日'[node.send_weekday]}发送。`
  : node.send_cycle === 'daily' ? '北京时间，每天在所选时间发送。' : '北京时间，从流程生效后的首次到期时间开始按周期重复。'
const businessFields = [
  { key: 'trigger_condition' as const, label: '触发条件', placeholder: '什么情况下进入这一步？例如：客户已收到教程，并提出养护问题。' },
  { key: 'action' as const, label: '具体行动', placeholder: '进入后具体做什么？例如：结合养护手册回答客户的问题，了解种植环境，并记录客户明确提供的信息。' },
  { key: 'exit_condition' as const, label: '退出条件', placeholder: '什么情况下进入下一步或结束？例如：客户确认问题已解决；需要人工时进入人工处理分支。' }
]
const fieldLabels = { name: '名称', trigger_condition: '触发条件', action: '具体行动', exit_condition: '退出条件', schedule_rule: '发送时间与周期', send_content_type: '发送内容', branch_mode: '分支匹配方式', handoff_target: '转交对象', branches: '分支条件', send_time: '发送时间', send_cycle: '发送周期', content_source: '内容库与分类', graph: '连线与结构' }
const statusText = computed(() => {
  if (publishing.value) return '正在理解整张流程…'
  if (dirty.value) return '有未保存的修改'
  if (workflow.value?.published_revision === workflow.value?.revision) return '流程理解已发布'
  return workflow.value?.published_revision ? '草稿已保存 · 已有发布版本' : '草稿已保存'
})
const nodeHeight = (node: CanvasNode) => node.type === 'condition' ? Math.max(184, 88 + node.branches.length * 108) : node.type === 'general' ? 252 : node.type === 'scheduled_send' ? 190 : node.type === 'end' ? 98 : 144
const nodeName = (nodeId: string) => definition.value?.nodes.find(node => node.id === nodeId)?.name || nodeId
const nodeIssues = (nodeId: string) => workflow.value?.last_review?.issues.filter(issue => issue.node_id === nodeId) || []
const portPosition = (node: CanvasNode, output = false, branchId: string | null = null) => ({ x: node.position.x + (output ? 260 : 0), y: node.position.y + (output && node.type === 'condition' && branchId ? 136 + node.branches.findIndex(branch => branch.id === branchId) * 108 : nodeHeight(node) / 2) })
const edgeLabel = (edge: CanvasEdge) => {
  const source = definition.value?.nodes.find(node => node.id === edge.source)
  const index = source?.branches.findIndex(branch => branch.id === edge.source_branch) ?? -1
  return source?.type === 'condition' && index >= 0 ? branchName(index) : edge.label
}
const edgeCondition = (edge: CanvasEdge) => definition.value?.nodes.find(node => node.id === edge.source)?.branches.find(branch => branch.id === edge.source_branch)?.condition || '待填写分支条件'
const curve = (from: { x: number; y: number }, to: { x: number; y: number }) => {
  const bend = Math.max(70, Math.abs(to.x - from.x) * 0.5)
  return `M ${from.x} ${from.y} C ${from.x + bend} ${from.y}, ${to.x - bend} ${to.y}, ${to.x} ${to.y}`
}
const drawnEdges = computed(() => (definition.value?.edges || []).flatMap(edge => {
  const source = definition.value?.nodes.find(node => node.id === edge.source), target = definition.value?.nodes.find(node => node.id === edge.target)
  if (!source || !target) return []
  const from = portPosition(source, true, edge.source_branch), to = portPosition(target)
  return [{ ...edge, label: edgeLabel(edge), path: curve(from, to), labelX: (from.x + to.x) / 2, labelY: (from.y + to.y) / 2 - 12 }]
}))
const pendingPath = computed(() => {
  const source = definition.value?.nodes.find(node => node.id === connectionFrom.value)
  return source ? curve(portPosition(source, true, connectionBranch.value), connectionPoint) : ''
})
const worldPoint = (clientX: number, clientY: number) => {
  const rect = stage.value?.getBoundingClientRect()
  return { x: (clientX - (rect?.left || 0) - view.x) / view.zoom, y: (clientY - (rect?.top || 0) - view.y) / view.zoom }
}
const newId = (prefix: string) => `${prefix}_${crypto.randomUUID().replaceAll('-', '')}`

function applyWorkflow(value: CanvasWorkflow) {
  for (const node of value.definition.nodes) {
    node.schedule_rule ??= ''; node.send_content_type ??= 'text'; node.branch_mode ??= 'first_match'; node.handoff_target ??= ''
    node.branches ??= []; node.send_time ??= ''; node.send_cycle ??= 'daily'; node.send_weekday ??= null
    node.content_source ??= null
  }
  workflow.value = value
  savedFingerprint.value = JSON.stringify(value.definition)
  const existing = workflows.value.find(item => item.workflow_id === value.workflow_id)
  if (existing) existing.name = value.definition.name
  else workflows.value.unshift({ workflow_id: value.workflow_id, name: value.definition.name })
}
function clearSelection() { selectedNodeId.value = null; selectedEdgeId.value = null; panel.value = null; connectionFrom.value = null; connectionBranch.value = null }
function selectNode(nodeId: string) {
  if (!definition.value?.nodes.some(node => node.id === nodeId)) return
  selectedNodeId.value = nodeId; selectedEdgeId.value = null; panel.value = 'node'
}
function selectEdge(edgeId: string) { selectedEdgeId.value = edgeId; selectedNodeId.value = null; panel.value = 'node' }
async function loadWorkflow(workflowId: string) {
  loading.value = true
  try { applyWorkflow(await getCanvasWorkflow(workflowId)); clearSelection(); await nextTick(); fitCanvas() }
  catch { /* The shared request client displays the error. */ }
  finally { loading.value = false }
}
async function saveDraft(): Promise<boolean> {
  if (!workflow.value || !definition.value || saving.value || !isAdmin()) return false
  if (!dirty.value) return true
  saving.value = true
  try { applyWorkflow(await saveCanvasWorkflow(workflow.value.workflow_id, workflow.value.revision, JSON.parse(JSON.stringify(definition.value)))); return true }
  catch { return false }
  finally { saving.value = false }
}
async function switchWorkflow(workflowId: string) {
  if (busy.value || workflowId === workflow.value?.workflow_id) return
  if (dirty.value && !await saveDraft()) return
  await loadWorkflow(workflowId)
}
async function newWorkflow() {
  if (busy.value || !isAdmin()) return
  if (dirty.value && !await saveDraft()) return
  loading.value = true
  try { applyWorkflow(await createCanvasWorkflow()); clearSelection(); selectNode('start'); await nextTick(); fitCanvas() }
  catch { /* The shared request client displays the error. */ }
  finally { loading.value = false }
}
async function reloadWorkflow() {
  if (!workflow.value || busy.value) return
  if (dirty.value) {
    try { await ElMessageBox.confirm('重新加载会放弃尚未保存的修改，是否继续？', '重新加载流程', { type: 'warning', confirmButtonText: '重新加载', cancelButtonText: '取消' }) }
    catch { return }
  }
  await loadWorkflow(workflow.value.workflow_id)
}
async function publish() {
  if (busy.value || !workflow.value || !isAdmin()) return
  if (!await saveDraft()) return
  publishing.value = true; connectionFrom.value = null; gesture.value = null
  try {
    const result = await publishCanvasWorkflow(workflow.value.workflow_id, workflow.value.revision)
    applyWorkflow(result.workflow); panel.value = 'review'
    if (result.published) ElMessage.success('流程理解已发布')
    else ElMessage.warning(`发布未通过，请修改 ${result.review.issues.length} 项问题`)
  } catch { /* A failed review must leave the current draft and published result available. */ }
  finally { publishing.value = false }
}
function addNode(type: CanvasNodeType) {
  if (!definition.value || locked.value || definition.value.nodes.length >= 60) return
  if (type === 'start' && definition.value.nodes.some(node => node.type === 'start')) return
  const rect = stage.value?.getBoundingClientRect()
  const position = worldPoint((rect?.left || 0) + Math.max(320, ((rect?.width || 1000) - 360) / 2), (rect?.top || 0) + (rect?.height || 700) / 2)
  const node: CanvasNode = { id: newId('node'), type, name: typeLabels[type], position: { x: Math.max(-10000, Math.min(10000, position.x - 130)), y: Math.max(-10000, Math.min(10000, position.y - 90)) }, trigger_condition: '', action: '', exit_condition: '', schedule_rule: '', send_content_type: 'text', branch_mode: 'first_match', handoff_target: '', branches: type === 'condition' ? [{ id: newId('branch'), condition: '' }, { id: newId('branch'), condition: '' }] : [], send_time: type === 'scheduled_send' ? '09:00' : '', send_cycle: 'daily', send_weekday: null, content_source: null }
  definition.value.nodes.push(node); selectNode(node.id)
}
function addBranch(node: CanvasNode) {
  if (locked.value || node.type !== 'condition' || node.branches.length >= 150) return
  node.branches.push({ id: newId('branch'), condition: '' })
}
function removeBranch(node: CanvasNode, branchId: string) {
  if (locked.value || !definition.value || node.branches.length <= 1) return
  node.branches = node.branches.filter(branch => branch.id !== branchId)
  definition.value.edges = definition.value.edges.filter(edge => edge.source !== node.id || edge.source_branch !== branchId)
  for (const edge of definition.value.edges) if (edge.source === node.id) edge.label = edgeLabel(edge)
  if (connectionFrom.value === node.id && connectionBranch.value === branchId) { connectionFrom.value = null; connectionBranch.value = null }
}
function setSendTime(value: unknown) {
  if (!locked.value && selectedNode.value && typeof value === 'string') selectedNode.value.send_time = value
}
function changeCycle() {
  if (locked.value || !selectedNode.value) return
  selectedNode.value.send_weekday = null
}
function removeSelection() {
  if (!definition.value || locked.value) return
  if (selectedNodeId.value) {
    definition.value.nodes = definition.value.nodes.filter(node => node.id !== selectedNodeId.value)
    definition.value.edges = definition.value.edges.filter(edge => edge.source !== selectedNodeId.value && edge.target !== selectedNodeId.value)
  } else if (selectedEdgeId.value) definition.value.edges = definition.value.edges.filter(edge => edge.id !== selectedEdgeId.value)
  clearSelection()
}
function beginPan(event: PointerEvent) {
  if (event.button !== 0) return
  connectionFrom.value = null
  gesture.value = { kind: 'pan', clientX: event.clientX, clientY: event.clientY, x: view.x, y: view.y }
}
function beginNodeDrag(event: PointerEvent, node: CanvasNode) {
  if (event.button !== 0) return
  selectNode(node.id)
  if (locked.value) return
  event.preventDefault()
  gesture.value = { kind: 'node', nodeId: node.id, clientX: event.clientX, clientY: event.clientY, x: node.position.x, y: node.position.y }
}
function beginConnection(event: PointerEvent, nodeId: string, branchId: string | null = null) {
  if (locked.value || event.button !== 0) return
  event.preventDefault(); gesture.value = null; connectionFrom.value = nodeId; connectionBranch.value = branchId
  Object.assign(connectionPoint, worldPoint(event.clientX, event.clientY))
}
function finishConnection(target: string) {
  if (!definition.value || locked.value || !connectionFrom.value) return
  const source = connectionFrom.value, sourceBranch = connectionBranch.value
  connectionFrom.value = null; connectionBranch.value = null
  if (source === target || definition.value.edges.some(edge => edge.source === source && edge.target === target && (edge.source_branch || null) === sourceBranch)) return
  if (definition.value.nodes.find(node => node.id === target)?.type === 'start' || definition.value.edges.length >= 150) return
  const sourceNode = definition.value.nodes.find(node => node.id === source)
  if (sourceNode?.type === 'condition' && !sourceNode.branches.some(branch => branch.id === sourceBranch)) return
  if (sourceBranch) definition.value.edges = definition.value.edges.filter(edge => edge.source !== source || edge.source_branch !== sourceBranch)
  const edge: CanvasEdge = { id: newId('edge'), source, target, label: '', source_branch: sourceBranch }
  edge.label = edgeLabel(edge)
  definition.value.edges.push(edge); selectEdge(edge.id)
}
function pointerMove(event: PointerEvent) {
  if (connectionFrom.value) Object.assign(connectionPoint, worldPoint(event.clientX, event.clientY))
  const active = gesture.value
  if (!active) return
  const dx = event.clientX - active.clientX, dy = event.clientY - active.clientY
  if (active.kind === 'pan') { view.x = active.x + dx; view.y = active.y + dy }
  else if (!locked.value) {
    const node = definition.value?.nodes.find(item => item.id === active.nodeId)
    if (node) { node.position.x = Math.max(-10000, Math.min(10000, active.x + dx / view.zoom)); node.position.y = Math.max(-10000, Math.min(10000, active.y + dy / view.zoom)) }
  }
}
function pointerUp(event: PointerEvent) {
  gesture.value = null
  if (!connectionFrom.value) return
  const target = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>('[data-input-id]')?.dataset.inputId
  if (target) finishConnection(target)
}
function zoomAt(zoom: number, x: number, y: number) {
  const next = Math.max(0.25, Math.min(1.75, zoom)), ratio = next / view.zoom
  view.x = x - (x - view.x) * ratio; view.y = y - (y - view.y) * ratio; view.zoom = next
}
function wheelZoom(event: WheelEvent) {
  const rect = stage.value?.getBoundingClientRect()
  zoomAt(view.zoom * Math.exp(-event.deltaY * 0.001), event.clientX - (rect?.left || 0), event.clientY - (rect?.top || 0))
}
function changeZoom(delta: number) { zoomAt(view.zoom + delta, (stage.value?.clientWidth || 1000) / 2, (stage.value?.clientHeight || 700) / 2) }
function fitCanvas() {
  const nodes = definition.value?.nodes
  if (!nodes?.length || !stage.value) { view.x = 0; view.y = 0; view.zoom = 1; return }
  const left = Math.min(...nodes.map(node => node.position.x)), top = Math.min(...nodes.map(node => node.position.y))
  const right = Math.max(...nodes.map(node => node.position.x + 260)), bottom = Math.max(...nodes.map(node => node.position.y + nodeHeight(node)))
  const availableWidth = Math.max(200, stage.value.clientWidth - (panel.value ? 390 : 30) - 190), availableHeight = Math.max(200, stage.value.clientHeight - 220)
  view.zoom = Math.min(1, Math.max(0.25, Math.min(availableWidth / (right - left), availableHeight / (bottom - top))))
  view.x = 170 + (availableWidth - (right - left) * view.zoom) / 2 - left * view.zoom
  view.y = 120 + (availableHeight - (bottom - top) * view.zoom) / 2 - top * view.zoom
}
function locateIssue(issue: CanvasIssue) {
  if (!issue.node_id) return
  selectNode(issue.node_id)
  const node = selectedNode.value
  if (!node || !stage.value) return
  view.x = Math.max(170, (stage.value.clientWidth - 360) / 2 - 130) - node.position.x * view.zoom
  view.y = Math.max(150, stage.value.clientHeight / 2 - nodeHeight(node) / 2) - node.position.y * view.zoom
}
function keyDown(event: Event) {
  if (!(event instanceof KeyboardEvent)) return
  if (event.key === 'Escape') { connectionFrom.value = null; gesture.value = null; return }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); if (!busy.value) void saveDraft(); return }
  if ((event.target as HTMLElement)?.closest('input, textarea, [contenteditable="true"]')) return
  if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); removeSelection() }
}
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value || publishing.value) { event.preventDefault(); event.returnValue = '' } }
onBeforeRouteLeave(async () => {
  if (publishing.value) { ElMessage.info('正在理解流程，请等待发布结果'); return false }
  return dirty.value ? await saveDraft() : true
})
onMounted(async () => {
  window.addEventListener('pointermove', pointerMove); window.addEventListener('pointerup', pointerUp)
  window.addEventListener('keydown', keyDown); window.addEventListener('beforeunload', beforeUnload)
  void loadContentSources()
  loading.value = true
  try {
    if (isAdmin()) {
      try { await syncExistingSops() }
      catch { /* Existing canvases remain accessible if importing a legacy SOP fails. */ }
    }
    workflows.value = await listCanvasWorkflows()
    if (workflows.value[0]) await loadWorkflow(workflows.value[0].workflow_id)
  }
  catch { /* The shared request client displays the error. */ }
  finally { loading.value = false }
})
onBeforeUnmount(() => {
  window.removeEventListener('pointermove', pointerMove); window.removeEventListener('pointerup', pointerUp)
  window.removeEventListener('keydown', keyDown); window.removeEventListener('beforeunload', beforeUnload)
})
</script>

<style scoped>
.canvas-workbench { position: relative; height: calc(100dvh - var(--app-header-height, 76px)); min-height: 480px; overflow: hidden; color: #223f35; background: #f5f8f6; }
.canvas-stage { position: absolute; inset: 0; overflow: hidden; touch-action: none; background-image: radial-gradient(#cbd8d0 1px, transparent 1px); cursor: grab; }
.canvas-stage.panning { cursor: grabbing; }.canvas-stage.connecting { cursor: crosshair; }
.canvas-world { position: absolute; top: 0; left: 0; transform-origin: 0 0; }
.connections { position: absolute; width: 1px; height: 1px; overflow: visible; pointer-events: none; }
.edge-hit { fill: none; stroke: transparent; stroke-width: 22px; pointer-events: stroke; cursor: pointer; }
.edge-line { fill: none; stroke: #96ada1; stroke-width: 2px; }.edge-line.selected { stroke: #218063; stroke-width: 3px; }.edge-line.pending { stroke: #218063; stroke-dasharray: 6 5; }
.edge-label { font-size: 12px; fill: #60796c; paint-order: stroke; stroke: #f5f8f6; stroke-width: 5px; stroke-linejoin: round; pointer-events: all; cursor: pointer; }
.canvas-toolbar { position: absolute; z-index: 4; top: 16px; left: 16px; right: 16px; min-height: 64px; display: flex; align-items: center; gap: 10px; padding: 10px 14px; border: 1px solid #dce5df; border-radius: 12px; background: #fff; box-shadow: 0 3px 14px #1d493609; }
.workflow-picker { width: 150px; flex-shrink: 0; }.workflow-heading { min-width: 100px; flex: 1; padding-left: 10px; border-left: 1px solid #e3eae5; }
.workflow-heading input { display: block; width: 100%; max-width: 260px; padding: 0; border: 0; outline-color: #338569; background: transparent; font: inherit; font-size: 15px; font-weight: 600; color: inherit; }
.workflow-heading span { display: block; font-size: 11px; color: #778b80; margin-top: 3px; }.workflow-heading .unsaved { color: #ae7938; }
.toolbar-actions { display: flex; gap: 8px; margin-left: auto; flex-shrink: 0; }.toolbar-actions :deep(.el-button + .el-button) { margin-left: 0; }
.node-palette { position: absolute; z-index: 2; left: 18px; top: 104px; width: 132px; padding: 12px 8px 8px; border: 1px solid #dce5df; border-radius: 12px; background: #fff; box-shadow: 0 3px 14px #1d493609; }
.node-palette > span { display: block; padding: 0 6px 8px; font-size: 11px; color: #7a8b81; }
.node-palette button { display: flex; align-items: center; gap: 7px; width: 100%; padding: 9px 6px; font: inherit; font-size: 12px; border: 0; background: transparent; border-radius: 6px; color: #385247; cursor: pointer; }
.node-palette button:hover { background: #f1f6f3; }.node-palette button:disabled { opacity: .4; cursor: default; }
.node-palette i { width: 22px; height: 22px; display: grid; place-items: center; border-radius: 6px; font-style: normal; background: #e5f2eb; color: #367553; }.node-palette i.general { color: #5874ad; background: #edf1fa; }.node-palette i.end { color: #837184; background: #f1edf2; }.node-palette b { margin-left: auto; font-weight: 400; }
.flow-node { position: absolute; box-sizing: border-box; width: 260px; padding: 14px 18px; border: 1px solid #dce5df; border-radius: 12px; background: #fff; box-shadow: 0 4px 16px #2948360a; cursor: grab; user-select: none; touch-action: none; }
.flow-node.selected { border-color: #2e8867; box-shadow: 0 0 0 2px #2e886722; }.flow-node.invalid { border-color: #ce7a64; }
.node-type { display: flex; align-items: center; gap: 7px; font-size: 11px; color: #759083; }.type-icon { display: grid; place-items: center; width: 22px; height: 22px; color: #327958; background: #eaf5ee; border-radius: 6px; font-size: 15px; }.general .type-icon { color: #5874ad; background: #edf1fa; }.end .type-icon { color: #837184; background: #f1edf2; }.node-error { color: #b25b48; font-size: 10px; margin-left: auto; }
.node-name { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 15px; margin: 9px 0; }
.scheduled_send .type-icon, .node-palette i.scheduled_send { color: #ae7832; background: #fff3df; }
.condition .type-icon, .node-palette i.condition { color: #7961a5; background: #f2edfa; }
.handoff .type-icon, .node-palette i.handoff { color: #ae6652; background: #fbeee8; }
.panel-content :deep(.el-select) { width: 100%; }
.branch-editor { margin-top: 20px; }.branch-heading { display: flex; align-items: center; gap: 6px; height: 24px; margin-bottom: 6px; font-size: 12px; color: #597061; }.branch-heading span { flex: 1; font-weight: 600; }
.add-branch, .delete-branch { padding: 2px 5px; border: 0; border-radius: 4px; background: #f2edfa; color: #7961a5; font: inherit; font-size: 11px; cursor: pointer; }.delete-branch { color: #8e807f; background: transparent; font-size: 17px; }.add-branch:disabled, .delete-branch:disabled { opacity: .35; cursor: default; }.empty-branch { margin-top: 20px; }
.canvas-branches { position: absolute; top: 76px; left: 18px; right: 18px; }.canvas-branch { position: relative; height: 108px; }.canvas-branch :deep(.el-textarea__inner) { height: 60px; min-height: 60px !important; padding: 5px 8px; font-size: 12px; line-height: 18px; user-select: text; touch-action: auto; }.canvas-branch .branch-port { top: 60px; right: -26px; border-color: #9e87bf; }
.schedule-controls { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; }.schedule-controls :deep(.el-date-editor.el-input) { width: 100%; }.content-heading { display: flex; align-items: baseline; justify-content: space-between; }.content-heading button { border: 0; background: transparent; color: #21745b; cursor: pointer; font-size: 12px; }.content-warning { color: #ad6229; font-size: 12px; line-height: 1.6; margin-top: 10px; }.schedule-summary { margin-top: 14px; color: #a37235; font-size: 13px; }.handoff-preview { font-size: 12px; color: #788b7e; line-height: 19px; margin-top: 12px; }
.node-preview { display: flex; gap: 8px; border-top: 1px solid #f0f3f1; padding-top: 8px; margin-top: 8px; font-size: 11px; }.node-preview > span { color: #87978e; flex: 0 0 44px; }.node-preview p { margin: 0; color: #577163; line-height: 17px; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.start-preview { color: #738b7c; font-size: 12px; line-height: 19px; margin: 0; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }.end-preview { font-size: 11px; color: #89988e; margin: 0; }
.port { position: absolute; top: 50%; width: 14px; height: 14px; border: 2px solid #78a68c; border-radius: 50%; background: #fff; transform: translateY(-50%); cursor: crosshair; padding: 0; }.port::after { content: ''; position: absolute; inset: -7px; border-radius: 50%; }.port.input { left: -8px; }.port.output { right: -8px; }.port:hover, .port.active { background: #2e8867; border-color: #2e8867; }.port:disabled { cursor: default; }
.configuration-panel { position: absolute; z-index: 3; top: 98px; right: 16px; bottom: 64px; width: 350px; display: flex; flex-direction: column; background: #fff; border: 1px solid #dce5df; border-radius: 12px; box-shadow: 0 5px 25px #21452f12; }
.panel-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 17px 20px; border-bottom: 1px solid #edf1ee; font-size: 14px; }.panel-heading button { border: 0; background: transparent; color: #8a9b8f; font-size: 24px; line-height: 20px; cursor: pointer; }
.panel-content { padding: 0 20px 20px; overflow-y: auto; min-height: 0; }.field-label { display: block; font-size: 12px; font-weight: 600; margin: 20px 0 9px; }.panel-content :deep(.el-textarea__inner) { font-size: 12px; line-height: 1.8; padding: 10px 12px 22px; }.panel-content :deep(.el-input__count) { font-size: 10px; }
.field-help, .muted { font-size: 12px; color: #88978e; line-height: 1.8; }.node-exits button { display: block; width: 100%; padding: 9px 0; text-align: left; border: 0; background: transparent; color: #548169; font: inherit; font-size: 12px; cursor: pointer; }.remove-button { margin-top: 26px; }
.review-status { padding: 12px; margin-top: 20px; border-radius: 8px; color: #286b50; background: #edf7f0; font-size: 13px; }.review-status.failed { color: #a25b43; background: #fff3ed; }
.review-issue { display: block; width: 100%; text-align: left; padding: 12px; margin-top: 14px; border: 1px solid #f0d9ce; border-radius: 8px; background: #fffcfa; cursor: pointer; font: inherit; }.review-issue span, .review-issue small { color: #a17a68; font-size: 11px; }.review-issue strong { display: block; color: #915641; font-size: 13px; margin-top: 8px; }.review-issue p, .inline-issue p { color: #987864; font-size: 12px; line-height: 1.7; margin: 7px 0; }.inline-issue { padding: 10px; background: #fff5ef; border-radius: 6px; margin-top: 12px; font-size: 12px; color: #ad674b; }
.panel-content h3 { font-size: 13px; margin: 22px 0 10px; }.review-copy, .node-understanding p { white-space: pre-wrap; font-size: 12px; line-height: 1.9; color: #597061; }.node-understanding { padding-top: 14px; margin-top: 14px; border-top: 1px solid #edf1ee; }.node-understanding button { padding: 0; border: 0; background: transparent; font-size: 12px; font-weight: 600; color: #3e795c; cursor: pointer; }.review-note { font-size: 11px; line-height: 1.8; color: #859388; background: #f4f7f4; border-radius: 8px; padding: 12px; margin-top: 22px; }
.canvas-controls { position: absolute; z-index: 2; left: 18px; bottom: 18px; display: flex; align-items: center; height: 38px; padding: 0 7px; border: 1px solid #dce5df; border-radius: 8px; background: #fff; box-shadow: 0 2px 8px #21452f08; }.canvas-controls button { width: 30px; height: 30px; border: 0; border-radius: 5px; color: #54745f; background: transparent; cursor: pointer; font-size: 17px; }.canvas-controls button:hover { background: #edf4ef; }.canvas-controls span { width: 42px; text-align: center; font-size: 11px; color: #738579; }.canvas-controls i { height: 16px; width: 1px; background: #e4eae5; margin: 0 7px; }.canvas-controls .fit-button { width: auto; padding: 0 10px; font-size: 12px; }
.canvas-hint { position: absolute; bottom: 28px; left: 50%; transform: translateX(-50%); white-space: nowrap; font-size: 11px; color: #8b9e91; pointer-events: none; }
.empty-canvas { position: absolute; top: 44%; left: 50%; transform: translate(-50%, -50%); text-align: center; }.empty-symbol { margin: 0 auto 18px; width: 52px; height: 52px; display: grid; place-items: center; border-radius: 16px; color: #79a68a; background: #e8f1eb; font-size: 28px; }.empty-canvas h2 { font-size: 19px; }.empty-canvas p { color: #8b9b90; font-size: 13px; margin: 14px 0 24px; }
@media (max-width: 1200px) { .canvas-toolbar { flex-wrap: wrap; }.workflow-heading { min-width: 140px; }.toolbar-actions { flex-wrap: wrap; }.node-palette, .configuration-panel { top: 146px; }.canvas-hint { display: none; } }
@media (max-width: 700px) { .canvas-toolbar { top: 8px; left: 8px; right: 8px; padding: 8px; gap: 6px; }.workflow-picker { width: 130px; }.workflow-heading { display: none; }.toolbar-actions { width: 100%; justify-content: flex-end; }.toolbar-actions :deep(.el-button) { padding: 8px; }.node-palette { top: 115px; left: 8px; width: 116px; }.configuration-panel { top: 115px; right: 8px; width: min(350px, calc(100% - 144px)); bottom: 62px; }.panel-content { padding: 0 12px 16px; }.panel-heading { padding: 14px 12px; } }
</style>
